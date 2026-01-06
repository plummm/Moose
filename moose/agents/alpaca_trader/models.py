from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Literal, Optional


AssetClass = Literal["stock", "crypto"]
TradeSide = Literal["buy", "sell", "hold"]
OrderType = Literal["market", "limit"]
TimeInForce = Literal["day", "gtc", "ioc", "fok"]


@dataclass(frozen=True)
class OrderSpec:
    """
    Concrete order instructions produced by V2 planning.

    Notes:
    - Use notional_usd when supported by broker; qty is optional.
    - For limit orders, limit_price must be provided.
    - max_spread_pct/max_slippage_bps are optional safety fields used by RiskGate.
    """

    order_type: OrderType
    time_in_force: TimeInForce = "day"
    limit_price: Optional[float] = None
    notional_usd: Optional[float] = None
    qty: Optional[float] = None
    max_spread_pct: Optional[float] = None
    max_slippage_bps: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    asset_class: AssetClass
    side: TradeSide
    account_id: str
    strategy_id: str
    confidence: float  # 0..1
    rationale: str
    holding_period_hint: str
    sizing_guidance: str
    notional_usd_hint: Optional[float] = None
    order: Optional[OrderSpec] = None
    created_from_event: Optional[str] = None  # event hash or id as string

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


