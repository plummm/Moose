from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from symbols import asset_type_from_symbol, candidates_for_input
from ticker_heuristics import looks_like_ticker


def create_tools(*, db, fmp, chat_id: int, chat_timezone: str, exchange: str):
    """
    Build the ONLY-allowed toolset for a single router invocation.

    Tools are bound to the current chat context (chat_id, timezone), and can safely
    read/write watchlist state.
    """
    chat_tz = ZoneInfo(chat_timezone)
    ny_tz = ZoneInfo("America/New_York")

    def _ok(result: Any) -> dict[str, Any]:
        return {"ok": True, "result": result}

    def _err(message: str, **extra) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": False, "error": message}
        payload.update(extra)
        return payload

    @tool
    async def get_price(symbol: str) -> dict[str, Any]:
        """
        General description:
          Get the latest price and basic quote info for a ticker symbol.
          The input should be a ticker symbol (e.g., \"MSFT\") or a crypto pair in this bot's format (e.g., \"BTCUSD\").
          If the input looks like a company name (e.g., \"microsoft\"), the tool will internally use FMP search to
          resolve it to one symbol. But we recommend to use ticker symbol instead of company name. If multiple candidates exist, it returns an ambiguity error so the router can
          ask a clarification question.

        Arguments:
          symbol: str
            The ticker symbol or company name.\n
            Examples:\n
            - \"AAPL\"\n
            - \"BTCUSD\"\n
            - \"MSFT\"\n

        Return format:
          dict\n
            On success:\n
              {\n
                \"ok\": true,\n
                \"result\": {\n
                  \"symbol\": \"MSFT\",\n
                  \"price\": 123.45,\n
                  \"change\": -0.12,\n
                  \"changePercent\": -0.10,\n
                  \"volume\": 12345678\n
                }\n
              }\n
            On ambiguity:\n
              {\n
                \"ok\": false,\n
                \"error\": \"ambiguous_symbol\",\n
                \"candidates\": [\"MSFT\", \"MSFT34\"],\n
                \"note\": \"...\"\n
              }\n
            On failure:\n
              {\"ok\": false, \"error\": \"...\"}\n
        """
        raw = (symbol or "").strip()
        if not raw:
            return _err("missing_symbol")

        # If it looks like a ticker, handle stock vs crypto candidate probing.
        if looks_like_ticker(raw):
            opts = candidates_for_input(raw)
            ok_syms: list[str] = []
            for s in opts:
                q = await fmp.quote_short(s)
                if q:
                    ok_syms.append(s)
            if not ok_syms:
                return _err("not_found")
            if len(ok_syms) > 1:
                return _err(
                    "ambiguous_symbol",
                    candidates=ok_syms,
                    note="Symbol matches both stock and crypto. Ask the user to choose.",
                )
            sym = ok_syms[0]
            q = await fmp.quote(sym) or await fmp.quote_short(sym)
            if not q:
                return _err("not_found")
            return _ok(
                {
                    "symbol": sym,
                    "price": q.get("price"),
                    "change": q.get("change"),
                    "changePercent": q.get("changesPercentage") or q.get("changePercent"),
                    "volume": q.get("volume"),
                }
            )

        # Otherwise treat as company name: use FMP search internally.
        results = await fmp.search_name(raw)
        if not results:
            return _err("not_found")
        # Prefer US stocks; fall back to the first.
        symbols = []
        for r in results[:10]:
            s = str(r.get("symbol") or "").strip().upper()
            if s:
                symbols.append(s)
        symbols = list(dict.fromkeys(symbols))
        if not symbols:
            return _err("not_found")
        if len(symbols) > 1:
            return _err("ambiguous_symbol", candidates=symbols[:5], note="Multiple symbols match this name.")
        sym = symbols[0]
        q = await fmp.quote(sym) or await fmp.quote_short(sym)
        if not q:
            return _err("not_found")
        return _ok(
            {
                "symbol": sym,
                "price": q.get("price"),
                "change": q.get("change"),
                "changePercent": q.get("changesPercentage") or q.get("changePercent"),
                "volume": q.get("volume"),
            }
        )

    @tool
    async def add_to_watchlist(symbol: str) -> dict[str, Any]:
        """
        General description:
          Add a canonical ticker symbol to the current chat's watchlist.
          Canonical formats:\n
          - Stock: \"AAPL\"\n
          - Crypto: \"BTCUSD\" (always suffixed with USD)\n

        Arguments:
          symbol: str\n
            Canonical ticker symbol to add.\n

        Return format:
          dict\n
            On success:\n
              {\"ok\": true, \"result\": {\"symbol\": \"AAPL\", \"added\": true}}\n
            If already present:\n
              {\"ok\": true, \"result\": {\"symbol\": \"AAPL\", \"added\": false, \"reason\": \"already_in_watchlist\"}}\n
            On failure:\n
              {\"ok\": false, \"error\": \"...\"}\n
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return _err("missing_symbol")
        inserted = db.add_to_watchlist(chat_id, sym, asset_type_from_symbol(sym))
        if inserted:
            return _ok({"symbol": sym, "added": True})
        return _ok({"symbol": sym, "added": False, "reason": "already_in_watchlist"})

    @tool
    async def remove_from_watchlist(symbol: str) -> dict[str, Any]:
        """
        General description:
          Remove a canonical ticker symbol from the current chat's watchlist.

        Arguments:
          symbol: str\n
            Canonical ticker symbol to remove.\n

        Return format:
          dict\n
            On success:\n
              {\"ok\": true, \"result\": {\"symbol\": \"AAPL\", \"removed\": true}}\n
            If not present:\n
              {\"ok\": true, \"result\": {\"symbol\": \"AAPL\", \"removed\": false, \"reason\": \"not_in_watchlist\"}}\n
            On failure:\n
              {\"ok\": false, \"error\": \"...\"}\n
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return _err("missing_symbol")
        removed = db.remove_from_watchlist(chat_id, sym)
        if removed:
            return _ok({"symbol": sym, "removed": True})
        return _ok({"symbol": sym, "removed": False, "reason": "not_in_watchlist"})

    @tool
    async def show_watchlist() -> dict[str, Any]:
        """
        General description:
          Return the current chat's watchlist as a list of canonical symbols.

        Arguments:
          (none)\n

        Return format:
          dict\n
            On success:\n
              {\"ok\": true, \"result\": {\"symbols\": [\"AAPL\", \"BTCUSD\"], \"count\": 2}}\n
            On failure:\n
              {\"ok\": false, \"error\": \"...\"}\n
        """
        items = db.list_watchlist(chat_id)
        syms = [s for (s, _t) in items]
        return _ok({"symbols": syms, "count": len(syms)})

    @tool
    async def next_market_open_time() -> dict[str, Any]:
        """
        General description:
          Compute the next US market open time (09:30 America/New_York), skipping weekends and the exchange
          holidays returned by FMP holidays-by-exchange. Returns both New York time and this chat's timezone time.

        Arguments:
          (none)\n

        Return format:
          dict\n
            On success:\n
              {\n
                \"ok\": true,\n
                \"result\": {\n
                  \"next_open_ny\": \"YYYY-MM-DD HH:MM\",\n
                  \"next_open_chat\": \"YYYY-MM-DD HH:MM\",\n
                  \"chat_timezone\": \"America/Los_Angeles\",\n
                  \"exchange\": \"NASDAQ\"\n
                }\n
              }\n
            On failure:\n
              {\"ok\": false, \"error\": \"...\"}\n
        """
        now_ny = datetime.now(tz=ny_tz)
        today = now_ny.date()
        start = today.isoformat()
        end = (today + timedelta(days=30)).isoformat()
        holidays = await fmp.holidays_by_exchange(exchange, start, end)
        closed = {h.get("date") for h in holidays if h.get("isClosed") is True and h.get("date")}

        def _is_weekend(d: date) -> bool:
            return d.weekday() >= 5

        def is_open_day(d: date) -> bool:
            if _is_weekend(d):
                return False
            return d.isoformat() not in closed

        open_dt_today = datetime.combine(today, time(9, 30), tzinfo=ny_tz)
        if is_open_day(today) and now_ny < open_dt_today:
            next_open_day = today
        else:
            d = today + timedelta(days=1)
            while not is_open_day(d) and d <= today + timedelta(days=30):
                d += timedelta(days=1)
            next_open_day = d

        next_open_ny = datetime.combine(next_open_day, time(9, 30), tzinfo=ny_tz)
        next_open_chat = next_open_ny.astimezone(chat_tz)
        return _ok(
            {
                "next_open_ny": next_open_ny.strftime("%Y-%m-%d %H:%M"),
                "next_open_chat": next_open_chat.strftime("%Y-%m-%d %H:%M"),
                "chat_timezone": chat_timezone,
                "exchange": exchange,
            }
        )

    return [get_price, add_to_watchlist, remove_from_watchlist, show_watchlist, next_market_open_time]


