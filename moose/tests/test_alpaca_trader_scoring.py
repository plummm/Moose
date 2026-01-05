from __future__ import annotations

from moose.agents.alpaca_trader.services.scoring import extract_symbols, event_dedupe_key


def test_extract_symbols_basic() -> None:
    text = "Breaking: AAPL earnings beat. BTCUSD also up. $MSFT mentioned."
    syms = extract_symbols(text)
    assert "AAPL" in syms
    assert "BTCUSD" in syms
    assert "MSFT" in syms


def test_event_dedupe_key_stable() -> None:
    k1 = event_dedupe_key("Hello  world", ["AAPL"])
    k2 = event_dedupe_key("Hello world", ["AAPL"])
    assert k1 == k2


