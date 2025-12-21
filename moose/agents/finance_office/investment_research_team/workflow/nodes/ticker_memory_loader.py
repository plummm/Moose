from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .utils import shrink_for_state


class TickerMemoryLoaderNode:
    """
    Node name: `load_ticker_memory`

    Reads:
    - state.routing.tickers (list[str])
    - state.routing.neutral_analysis (bool)

    Writes:
    - state.ticker_memory (dict[ticker -> memory dict]) loaded from:
      `/data/news/<ticker>/<year>/<month>/memory.json` (UTC bucket)
    """

    def __init__(self, *, analyzer: Any, logger: Any):
        self.analyzer = analyzer
        self.logger = logger

    async def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        current_ticker = str(state.get("current_ticker") or "").upper().strip()
        if not current_ticker:
            return state

        base_news_dir = os.getenv("NEWS_RESULT_DIR", "/data/news") or "/data/news"
        now = datetime.now(timezone.utc)
        year = now.strftime("%Y")
        month = now.strftime("%m")

        mem_out: Dict[str, Any] = dict(state.get("ticker_memory") or {}) if isinstance(state.get("ticker_memory"), dict) else {}
        fp = Path(base_news_dir) / current_ticker / year / month / "memory.json"
        if fp.exists() and fp.is_file():
            try:
                raw = json.loads(fp.read_text(encoding="utf-8"))
                mem_out[current_ticker] = shrink_for_state(raw)
            except Exception:
                pass
        return {**state, "ticker_memory": mem_out}


