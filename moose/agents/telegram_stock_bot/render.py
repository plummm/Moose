from __future__ import annotations

from typing import Any, Optional

# Import local agent modules (agent code is mounted into /app; do not import from the installed `moose` package)
from symbols import asset_type_from_symbol, crypto_display


def fmt_symbol_html(symbol: str) -> str:
    asset = asset_type_from_symbol(symbol)
    if asset == "crypto":
        return f"<i>{crypto_display(symbol)}</i>"
    return f"<b>{symbol}</b>"


def fmt_money(x: Any) -> str:
    try:
        v = float(x)
        if v >= 1000:
            return f"{v:,.2f}"
        return f"{v:.4f}".rstrip("0").rstrip(".")
    except Exception:
        return str(x)


def fmt_pct(x: Any) -> str:
    try:
        v = float(x)
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.2f}%"
    except Exception:
        return str(x)


def fmt_change(change: Optional[float]) -> str:
    if change is None:
        return "n/a"
    sign = "+" if change > 0 else ""
    return f"{sign}{fmt_money(change)}"


def quote_card_html(symbol: str, quote: dict[str, Any]) -> str:
    price = quote.get("price")
    change = quote.get("change")
    change_pct = quote.get("changesPercentage") or quote.get("changePercent")

    # If percent isn't available, compute from price & change when possible
    if change_pct is None:
        try:
            p = float(price)
            c = float(change)
            prev = p - c
            if prev != 0:
                change_pct = (c / prev) * 100.0
        except Exception:
            change_pct = None

    vol = quote.get("volume")
    parts = [
        f"{fmt_symbol_html(symbol)}",
        f"<b>Price</b>: {fmt_money(price)}",
        f"<b>Change</b>: {fmt_change(change)} ({fmt_pct(change_pct) if change_pct is not None else 'n/a'})",
    ]
    if vol is not None:
        parts.append(f"<b>Volume</b>: {vol}")
    return "\n".join(parts)


