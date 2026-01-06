from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

try:  # Moose mode
    from moose.agents.alpaca_trader.models import OrderSpec, TradePlan
except Exception:  # Standalone mode
    from models import OrderSpec, TradePlan


@dataclass(frozen=True)
class RiskCaps:
    max_notional_per_trade_usd: float
    max_notional_per_day_usd: float
    max_symbol_exposure_pct: float


@dataclass(frozen=True)
class GlobalCaps:
    max_open_positions: int
    max_open_orders: int


@dataclass(frozen=True)
class RiskConfig:
    mode: str  # paper|live
    trading_enabled: bool
    stocks: RiskCaps
    crypto: RiskCaps
    global_caps: GlobalCaps


@dataclass(frozen=True)
class RiskVerdict:
    ok: bool
    reason: str
    adjusted_plan: Optional[TradePlan] = None
    meta: Optional[Dict[str, Any]] = None


class RiskGate:
    """
    Hard guardrails that cannot be overridden by LLM.

    Notes:
    - Enforces allowlists + market-hours gating for stocks.
    - Enforces notional caps and portfolio-aware constraints (buying power, daily notional, symbol exposure).
    """

    def __init__(
        self,
        *,
        cfg: RiskConfig,
        allow_all: bool,
        allow_stocks: set[str],
        allow_crypto: set[str],
        market_hours: Any,  # MarketHoursService | None
        logger: Any,
    ) -> None:
        self.cfg = cfg
        self.allow_all = bool(allow_all)
        self.allow_stocks = allow_stocks
        self.allow_crypto = allow_crypto
        self.market_hours = market_hours
        self.logger = logger

    def _replace_plan(self, plan: TradePlan, **updates: Any) -> TradePlan:
        """
        dataclasses.replace without importing replace (and while keeping OrderSpec typed).
        """
        return TradePlan(
            symbol=str(updates.get("symbol", plan.symbol)),
            asset_class=updates.get("asset_class", plan.asset_class),
            side=updates.get("side", plan.side),
            account_id=str(updates.get("account_id", plan.account_id)),
            strategy_id=str(updates.get("strategy_id", plan.strategy_id)),
            confidence=float(updates.get("confidence", plan.confidence)),
            rationale=str(updates.get("rationale", plan.rationale)),
            holding_period_hint=str(updates.get("holding_period_hint", plan.holding_period_hint)),
            sizing_guidance=str(updates.get("sizing_guidance", plan.sizing_guidance)),
            notional_usd_hint=updates.get("notional_usd_hint", plan.notional_usd_hint),
            order=updates.get("order", plan.order),
            created_from_event=updates.get("created_from_event", plan.created_from_event),
        )

    def _extract_buying_power_usd(self, snap: Dict[str, Any]) -> Optional[float]:
        """
        Best-effort parse from account snapshot payload.
        """
        if not isinstance(snap, dict):
            return None
        acct = snap.get("account")
        if isinstance(acct, dict):
            for k in ("buying_power", "cash", "cash_withdrawable", "available_cash"):
                v = acct.get(k)
                try:
                    f = float(v)
                    if f >= 0:
                        return f
                except Exception:
                    continue
        return None

    def _extract_equity_usd(self, snap: Dict[str, Any]) -> Optional[float]:
        if not isinstance(snap, dict):
            return None
        acct = snap.get("account")
        if isinstance(acct, dict):
            for k in ("equity", "portfolio_value", "account_value"):
                v = acct.get(k)
                try:
                    f = float(v)
                    if f > 0:
                        return f
                except Exception:
                    continue
        return None

    def _extract_spread_pct(self, mkt: Dict[str, Any]) -> Optional[float]:
        if not isinstance(mkt, dict):
            return None
        v = mkt.get("spread_pct")
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    def _extract_symbol_positions_count(self, snap: Dict[str, Any]) -> Optional[int]:
        if not isinstance(snap, dict):
            return None
        positions = snap.get("positions")
        if isinstance(positions, list):
            # Count distinct symbols best-effort
            syms = set()
            for p in positions:
                if not isinstance(p, dict):
                    continue
                s = str(p.get("symbol") or p.get("asset_id") or "").strip().upper()
                if s:
                    syms.add(s)
            return len(syms)
        return None

    def _extract_open_orders_count(self, snap: Dict[str, Any]) -> Optional[int]:
        if not isinstance(snap, dict):
            return None
        oo = snap.get("open_orders")
        if isinstance(oo, list):
            return len(oo)
        return None

    def _position_notional_for_symbol(self, snap: Dict[str, Any], *, symbol: str) -> Optional[float]:
        """
        Best-effort current exposure for a symbol from account snapshot positions list.
        Tries: market_value, then qty*current_price, then qty*avg_entry_price.
        """
        if not isinstance(snap, dict):
            return None
        positions = snap.get("positions")
        if not isinstance(positions, list):
            return None
        sym = str(symbol or "").strip().upper()
        if not sym:
            return None
        best: Optional[float] = None
        for p in positions:
            if not isinstance(p, dict):
                continue
            ps = str(p.get("symbol") or "").strip().upper()
            if ps != sym:
                continue
            for k in ("market_value", "marketValue", "market_value_usd"):
                try:
                    v = p.get(k)
                    if v is not None:
                        best = float(v)
                        break
                except Exception:
                    continue
            if best is not None:
                break
            try:
                qty = float(p.get("qty") or p.get("quantity") or 0.0)
            except Exception:
                qty = 0.0
            px = None
            for k in ("current_price", "currentPrice", "price"):
                try:
                    if p.get(k) is not None:
                        px = float(p.get(k))
                        break
                except Exception:
                    continue
            if px is None:
                for k in ("avg_entry_price", "avgEntryPrice", "avg_price"):
                    try:
                        if p.get(k) is not None:
                            px = float(p.get(k))
                            break
                    except Exception:
                        continue
            if qty and px:
                best = abs(qty * px)
                break
        return abs(float(best)) if best is not None else None

    def _pending_orders_notional_for_symbol(self, snap: Dict[str, Any], *, symbol: str) -> float:
        """
        Best-effort pending notional for a symbol from open_orders list.
        """
        if not isinstance(snap, dict):
            return 0.0
        oo = snap.get("open_orders")
        if not isinstance(oo, list):
            return 0.0
        sym = str(symbol or "").strip().upper()
        total = 0.0
        for o in oo:
            if not isinstance(o, dict):
                continue
            osym = str(o.get("symbol") or "").strip().upper()
            if osym != sym:
                continue
            # Try direct notional first
            for k in ("notional", "notional_usd", "notional_usd_hint"):
                try:
                    v = o.get(k)
                    if v is not None:
                        total += abs(float(v))
                        raise StopIteration
                except StopIteration:
                    break
                except Exception:
                    continue
            # Fallback: qty * (limit_price or filled_avg_price or current_price)
            try:
                qty = float(o.get("qty") or o.get("quantity") or 0.0)
            except Exception:
                qty = 0.0
            px = None
            for k in ("limit_price", "limitPrice", "price", "avg_fill_price"):
                try:
                    if o.get(k) is not None:
                        px = float(o.get(k))
                        break
                except Exception:
                    continue
            if qty and px:
                total += abs(qty * px)
        return float(total)

    def _extract_quote_levels(self, mkt: Dict[str, Any]) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Return (bid, ask, last) best-effort.
        """
        if not isinstance(mkt, dict):
            return (None, None, None)
        q = mkt.get("quote")
        bid = ask = last = None
        if isinstance(q, dict):
            for k in ("bid", "bp", "bid_price"):
                try:
                    bid = float(q.get(k)) if q.get(k) is not None else bid
                except Exception:
                    pass
            for k in ("ask", "ap", "ask_price"):
                try:
                    ask = float(q.get(k)) if q.get(k) is not None else ask
                except Exception:
                    pass
            for k in ("last", "price", "last_price", "lp"):
                try:
                    last = float(q.get(k)) if q.get(k) is not None else last
                except Exception:
                    pass
        return (bid, ask, last)

    async def evaluate(
        self,
        plan: TradePlan,
        *,
        account_snapshot: Optional[Dict[str, Any]] = None,
        market_context: Optional[Dict[str, Any]] = None,
        daily_notional_used_usd: Optional[float] = None,
    ) -> RiskVerdict:
        # Global mode guardrail
        mode = str(self.cfg.mode or "paper").strip().lower()
        if mode != "paper":
            return RiskVerdict(ok=False, reason=f"mode_not_paper:{mode}")
        if not bool(self.cfg.trading_enabled):
            return RiskVerdict(ok=False, reason="trading_disabled")

        sym = str(plan.symbol or "").strip().upper()
        if not sym:
            return RiskVerdict(ok=False, reason="missing_symbol")

        # Allowlist
        if plan.asset_class == "crypto":
            if (not self.allow_all) and (sym not in self.allow_crypto):
                return RiskVerdict(ok=False, reason="crypto_not_allowlisted")
            caps = self.cfg.crypto
        else:
            if (not self.allow_all) and (sym not in self.allow_stocks):
                return RiskVerdict(ok=False, reason="stock_not_allowlisted")
            caps = self.cfg.stocks

        # Global caps: open positions/orders (best-effort from account snapshot)
        try:
            pos_n = self._extract_symbol_positions_count(account_snapshot or {})
            if pos_n is not None and int(pos_n) > int(self.cfg.global_caps.max_open_positions):
                return RiskVerdict(ok=False, reason="max_open_positions_exceeded", meta={"open_positions": int(pos_n)})
            ord_n = self._extract_open_orders_count(account_snapshot or {})
            if ord_n is not None and int(ord_n) > int(self.cfg.global_caps.max_open_orders):
                return RiskVerdict(ok=False, reason="max_open_orders_exceeded", meta={"open_orders": int(ord_n)})
        except Exception:
            pass

        # Market hours for stocks
        if plan.asset_class == "stock" and plan.side in {"buy", "sell"}:
            if self.market_hours is None:
                return RiskVerdict(ok=False, reason="market_hours_unavailable")
            try:
                open_now = await self.market_hours.is_market_open(datetime.now(tz=timezone.utc))
            except Exception:
                open_now = False
            if not open_now:
                return RiskVerdict(ok=False, reason="market_closed")

        # Notional hint capping
        nh = plan.notional_usd_hint
        order: Optional[OrderSpec] = plan.order
        if order is not None and isinstance(order.notional_usd, (int, float)):
            nh = float(order.notional_usd)

        adjusted = plan
        if plan.side in {"buy", "sell"}:
            if nh is None or nh <= 0:
                # Default to a conservative fraction of cap.
                nh2 = float(caps.max_notional_per_trade_usd) * 0.5
                if order is not None:
                    order = OrderSpec(**{**order.to_dict(), "notional_usd": nh2})  # type: ignore[arg-type]
                adjusted = self._replace_plan(plan, notional_usd_hint=nh2, order=order)
                return RiskVerdict(ok=True, reason="ok_sized_default", adjusted_plan=adjusted, meta={"notional": nh2})
            if float(nh) > float(caps.max_notional_per_trade_usd):
                nh2 = float(caps.max_notional_per_trade_usd)
                if order is not None:
                    order = OrderSpec(**{**order.to_dict(), "notional_usd": nh2})  # type: ignore[arg-type]
                adjusted = self._replace_plan(plan, notional_usd_hint=nh2, order=order)
                return RiskVerdict(ok=True, reason="ok_capped_notional", adjusted_plan=adjusted, meta={"notional": nh2})

            # Buying power check (best-effort)
            bp = self._extract_buying_power_usd(account_snapshot or {})
            if bp is not None and float(nh) > float(bp) * 0.98:
                return RiskVerdict(ok=False, reason="insufficient_buying_power", meta={"buying_power": bp, "notional": float(nh)})

            # Daily notional cap (best-effort)
            if daily_notional_used_usd is not None:
                used = float(daily_notional_used_usd)
                cap_day = float(caps.max_notional_per_day_usd)
                if used + float(nh) > cap_day:
                    return RiskVerdict(
                        ok=False,
                        reason="daily_notional_cap_exceeded",
                        meta={"used": used, "attempt": float(nh), "cap": cap_day},
                    )

            # Symbol exposure cap (best-effort)
            try:
                eq = self._extract_equity_usd(account_snapshot or {})
                if eq is not None and eq > 0 and float(caps.max_symbol_exposure_pct) > 0:
                    existing = self._position_notional_for_symbol(account_snapshot or {}, symbol=sym)
                    pending = self._pending_orders_notional_for_symbol(account_snapshot or {}, symbol=sym)
                    attempt = float(nh)
                    total_exposure = float(existing or 0.0) + float(pending or 0.0) + attempt
                    limit = float(eq) * float(caps.max_symbol_exposure_pct)
                    if total_exposure > limit:
                        return RiskVerdict(
                            ok=False,
                            reason="max_symbol_exposure_exceeded",
                            meta={
                                "equity": float(eq),
                                "limit": float(limit),
                                "existing": float(existing or 0.0),
                                "pending": float(pending or 0.0),
                                "attempt": attempt,
                                "total": float(total_exposure),
                            },
                        )
            except Exception:
                pass

            # Limit price sanity (best-effort, V2)
            if order is not None and order.order_type == "limit" and order.limit_price is not None:
                bid, ask, last = self._extract_quote_levels(market_context or {})
                lp = float(order.limit_price)
                # Reject obviously bad prices.
                if lp <= 0:
                    return RiskVerdict(ok=False, reason="invalid_limit_price")
                # If we have a quote, enforce no-cross + bounded slippage.
                if bid is not None and ask is not None and ask > 0 and bid > 0:
                    mid = (bid + ask) / 2.0
                    spread_pct = ((ask - bid) / mid) * 100.0 if mid > 0 else None
                    max_spread = float(order.max_spread_pct) if isinstance(order.max_spread_pct, (int, float)) else None
                    if max_spread is not None and spread_pct is not None and spread_pct > max_spread:
                        return RiskVerdict(ok=False, reason="spread_too_wide", meta={"spread_pct": spread_pct, "max_spread_pct": max_spread})
                    if plan.side == "buy" and lp > ask:
                        return RiskVerdict(ok=False, reason="limit_price_crosses_ask", meta={"ask": ask, "limit_price": lp})
                    if plan.side == "sell" and lp < bid:
                        return RiskVerdict(ok=False, reason="limit_price_crosses_bid", meta={"bid": bid, "limit_price": lp})
                    # Slippage band: default 50 bps unless specified
                    bps = float(order.max_slippage_bps) if isinstance(order.max_slippage_bps, (int, float)) else 50.0
                    band = max(0.0, bps) / 10000.0
                    ref = ask if plan.side == "buy" else bid
                    if ref is not None and ref > 0:
                        if plan.side == "buy" and lp > ref * (1.0 + band):
                            return RiskVerdict(ok=False, reason="limit_price_too_high", meta={"ref": ref, "limit_price": lp, "max_slippage_bps": bps})
                        if plan.side == "sell" and lp < ref * (1.0 - band):
                            return RiskVerdict(ok=False, reason="limit_price_too_low", meta={"ref": ref, "limit_price": lp, "max_slippage_bps": bps})
                else:
                    # No quote: still reject extreme spread_pct if present
                    sp = self._extract_spread_pct(market_context or {})
                    max_spread = float(order.max_spread_pct) if isinstance(order.max_spread_pct, (int, float)) else None
                    if max_spread is not None and sp is not None and sp > max_spread:
                        return RiskVerdict(ok=False, reason="spread_too_wide", meta={"spread_pct": sp, "max_spread_pct": max_spread})

        return RiskVerdict(ok=True, reason="ok", adjusted_plan=adjusted)


