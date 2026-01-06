from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


_WS_RE = re.compile(r"\s+")
_TICKER_RE = re.compile(r"(?i)(?:\$)?\b([A-Z]{1,5})\b")
_CRYPTO_RE = re.compile(r"\b([A-Z]{2,10}USD)\b")


def normalize_text(text: str) -> str:
    t = str(text or "").strip()
    t = _WS_RE.sub(" ", t)
    return t


def extract_symbols(text: str) -> List[str]:
    """
    Best-effort symbol extraction from free-form text.
    - Stocks: AAPL, MSFT, TSLA, etc. (1..5 letters)
    - Crypto: BTCUSD, ETHUSD, etc.

    We keep this conservative to avoid accidental captures; downstream allowlists
    are the final gate.
    """
    t = normalize_text(text).upper()
    out: List[str] = []

    for m in _CRYPTO_RE.finditer(t):
        out.append(m.group(1))

    for m in _TICKER_RE.finditer(t):
        sym = m.group(1).upper()
        # Ignore single-letter tokens (too noisy) and common stop tokens.
        if len(sym) <= 1:
            continue
        if sym in {"AND", "THE", "FOR", "WITH", "FROM", "THIS", "THAT", "WILL", "ARE", "YOU", "USD"}:
            continue
        out.append(sym)

    # De-dup in stable order.
    seen: set[str] = set()
    deduped: List[str] = []
    for s in out:
        if s in seen:
            continue
        seen.add(s)
        deduped.append(s)
    return deduped


def event_dedupe_key(text: str, symbols: Sequence[str]) -> str:
    base = normalize_text(text).lower()
    sym = ",".join([str(s).upper() for s in (symbols or [])])
    h = hashlib.sha256()
    h.update(base.encode("utf-8", errors="ignore"))
    h.update(b"|")
    h.update(sym.encode("utf-8", errors="ignore"))
    return h.hexdigest()


def regime_delta_from_text(text: str) -> float:
    """
    Very lightweight heuristic.
    """
    t = normalize_text(text).lower()
    neg = 0.0
    pos = 0.0

    # Panic-ish
    for w, d in [
        ("crash", 15),
        ("panic", 12),
        ("bank run", 15),
        ("default", 10),
        ("downgrade", 8),
        ("lawsuit", 6),
        ("fraud", 12),
        ("bankrupt", 15),
        ("recession", 12),
        ("war", 10),
        ("miss", 6),
        ("guidance cut", 10),
        ("halts", 8),
    ]:
        if w in t:
            neg += float(d)

    # Greed-ish
    for w, d in [
        ("rally", 10),
        ("surge", 10),
        ("beats", 8),
        ("record high", 12),
        ("breakout", 8),
        ("upgrade", 6),
        ("partnership", 5),
        ("approval", 7),
        ("acquisition", 6),
        ("bullish", 6),
    ]:
        if w in t:
            pos += float(d)

    return max(-25.0, min(25.0, pos - neg))


def watch_bump_from_text(text: str) -> float:
    """
    Heuristic bump to per-symbol watch_score for a mention.
    """
    t = normalize_text(text).lower()
    bump = 8.0  # base mention
    if any(w in t for w in ["breaking", "urgent", "halt", "sec", "sued", "fraud", "bankrupt"]):
        bump += 10.0
    if any(w in t for w in ["earnings", "guidance", "cpi", "fed", "rates"]):
        bump += 6.0
    return float(max(2.0, min(25.0, bump)))


def clamp(v: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, v)))


@dataclass(frozen=True)
class ScoreUpdate:
    regime_score: float
    watch_updates: Dict[str, float]  # symbol -> new watch_score



