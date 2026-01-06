from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .utils import get_db_path, load_current_memories, shrink_for_state


class TickerMemoryLoaderNode:
    """
    Node name: `load_ticker_memory`

    Reads:
    - state.routing.tickers (list[str])
    - state.routing.update_memory (bool)

    Writes:
    - state.ticker_memory (dict[ticker -> memory dict]) loaded from:
      sqlite `memory_current` (via `load_current_memories`)
    """

    def __init__(self, *, analyzer: Any, logger: Any):
        self.analyzer = analyzer
        self.logger = logger

    async def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        per_ticker_merge_mode = bool(state.get("per_ticker_merge_mode", False))
        base_news_dir = os.getenv("NEWS_RESULT_DIR", "/data/news") or "/data/news"
        now = datetime.now(timezone.utc)
        year = now.strftime("%Y")
        month = now.strftime("%m")
        db_path = get_db_path(base_news_dir=base_news_dir)
        
        mem_out: Dict[str, Any] = dict(state.get("ticker_memory") or {}) if isinstance(state.get("ticker_memory"), dict) else {}
        
        if per_ticker_merge_mode:
            # New mode: Load memory for all tickers in ticker_list
            ticker_list = state.get("ticker_list", []) if isinstance(state.get("ticker_list"), list) else []
            # DB-only bulk load. Fail-closed on sqlite errors; do not fall back to memory.json.
            db_mem = load_current_memories(
                db_path=db_path, tickers=ticker_list, logger=self.logger, fail_closed=True
            )
            for ticker in ticker_list:
                ticker = str(ticker).upper().strip()
                if not ticker:
                    continue

                if isinstance(db_mem, dict) and isinstance(db_mem.get(ticker), dict):
                    mem_out[ticker] = shrink_for_state(db_mem.get(ticker))
        else:
            # Old mode: Load memory for single current_ticker
            current_ticker = str(state.get("current_ticker") or "").upper().strip()
            if not current_ticker:
                return state

            # DB-only load. Fail-closed on sqlite errors; do not fall back to memory.json.
            db_mem = load_current_memories(
                db_path=db_path, tickers=[current_ticker], logger=self.logger, fail_closed=True
            )
            if isinstance(db_mem, dict) and isinstance(db_mem.get(current_ticker), dict):
                mem_out[current_ticker] = shrink_for_state(db_mem.get(current_ticker))
                return {**state, "ticker_memory": mem_out}
        
        return {**state, "ticker_memory": mem_out}


