from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedSymbol:
    input_text: str
    symbol: str           # canonical symbol used for FMP calls (AAPL or BTCUSD)
    asset_type: str       # 'stock' or 'crypto'


def normalize_user_symbol(text: str) -> str:
    return (text or "").strip().upper().replace(" ", "")


def is_crypto_symbol(symbol: str) -> bool:
    # Our canonical rule: crypto stored as XXXUSD (e.g., BTCUSD)
    return symbol.endswith("USD") and len(symbol) > 3


def crypto_display(symbol: str) -> str:
    # Render BTCUSD as BTC
    return symbol[:-3] if is_crypto_symbol(symbol) else symbol


def asset_type_from_symbol(symbol: str) -> str:
    return "crypto" if is_crypto_symbol(symbol) else "stock"


def candidates_for_input(user_text: str) -> list[str]:
    s = normalize_user_symbol(user_text)
    if not s:
        return []
    if s.endswith("USD"):
        return [s]
    # ambiguous candidate: stock "BTC" and crypto "BTCUSD"
    return [s, f"{s}USD"]


