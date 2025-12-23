from __future__ import annotations

import re


_STOCK_RE = re.compile(r"^[A-Z]{1,6}$")
_CRYPTO_RE = re.compile(r"^[A-Z]{2,10}USD$")


def normalize_text(s: str) -> str:
    return (s or "").strip().upper()


def looks_like_ticker(text: str) -> bool:
    """
    General description:
      Fast heuristic to decide if a string is likely a ticker symbol (stock) or a crypto pair (e.g., BTCUSD).
      Used to decide whether to treat a replied message as a ticker vs route to LLM.

    Arguments:
      text: str
        Raw user text or replied-to message text.

    Return format:
      bool:
        - True: text looks like a ticker (stock or crypto pair).
        - False: text is likely a sentence / not a ticker.
    """
    s = normalize_text(text)
    if not s:
        return False
    # reject multi-token
    if " " in s or "\n" in s or "\t" in s:
        return False
    # strip common punctuation around it
    s = s.strip(".,!?;:\"'()[]{}<>")
    if _STOCK_RE.match(s):
        return True
    if _CRYPTO_RE.match(s):
        return True
    return False



