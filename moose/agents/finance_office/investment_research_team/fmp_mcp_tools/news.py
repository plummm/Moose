import os
from typing import Any, Dict, Optional

from .basic import FMPMCPTools, mcp_envelope_err, mcp_envelope_ok, mcp_tool


class NewsMCPTools(FMPMCPTools):
    """
    News search tools (stock + crypto) from FMP Stable.

    Use this category to pull **news flow** for one or more symbols across a date window and page through results,
    helping build catalyst timelines and correlate news with price moves.
    """

    def __init__(self, api_key: Optional[str] = None, logger=None):
        super().__init__(api_key, logger)

    def _normalize_symbols(self, symbols: str) -> str:
        parts = [p.strip().upper() for p in str(symbols).split(",") if p.strip()]
        return ",".join(parts)

    def _validate_iso_date(self, value: str, field: str) -> Optional[str]:
        v = str(value).strip()
        if len(v) != 10 or v[4] != "-" or v[7] != "-":
            return f"{field} must be in YYYY-MM-DD format"
        y, m, d = v.split("-")
        if not (y.isdigit() and m.isdigit() and d.isdigit()):
            return f"{field} must be in YYYY-MM-DD format"
        return None

    @mcp_tool()
    def search_stock_news(
        self,
        symbols: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        page: int = 0,
        limit: int = 100,
    ) -> dict:
        """
        Search for stock news articles by symbols and date range (FMP Stable).

        General description
        - Returns news items filtered by one or more stock symbols within a date range. Useful for correlating
          news flow with price moves, tracking catalysts, and building event timelines.

        Use case
        - You want to monitor or analyze symbol-specific news within a defined window and page through results.

        Parameters
        - symbols: Comma-separated symbols (e.g., `"AAPL"` or `"AAPL,MSFT"`).
        - from_date: Optional start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: Optional end date (`YYYY-MM-DD`) mapped to query parameter `to`.
        - page: Page index (non-negative integer).
        - limit: Page size / maximum records per page (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        search_stock_news(symbols="AAPL", from_date="2025-01-01", to_date="2025-01-31", page=0, limit=50)
        ```

        Data source: FMP Stable Search Stock News API
        - GET `https://financialmodelingprep.com/stable/news/stock?symbols=...&from=...&to=...&page=...&limit=...` (from/to optional)
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        meta = {
            "tool": "search_stock_news",
            "symbols": symbols,
            "from_date": from_date,
            "to_date": to_date,
            "page": page,
            "limit": limit,
        }
        if not symbols:
            return mcp_envelope_err("symbols is required", meta=meta)
        if not isinstance(page, int) or page < 0:
            return mcp_envelope_err("page must be a non-negative integer", meta=meta)
        if not isinstance(limit, int) or limit < 1:
            return mcp_envelope_err("limit must be a positive integer", meta=meta)
        if from_date:
            err = self._validate_iso_date(from_date, "from_date")
            if err:
                return mcp_envelope_err(err, meta=meta)
        if to_date:
            err = self._validate_iso_date(to_date, "to_date")
            if err:
                return mcp_envelope_err(err, meta=meta)

        syms = self._normalize_symbols(symbols)
        if not syms:
            return mcp_envelope_err("symbols must contain at least one non-empty symbol", meta=meta)
        params: Dict[str, Any] = {"symbols": syms, "page": page, "limit": limit}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        raw = self._request_json("news/stock", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"Stock news: {syms}"
        if from_date and to_date:
            tf += f" from {from_date} to {to_date}"
        elif from_date:
            tf += f" from {from_date}"
        elif to_date:
            tf += f" to {to_date}"
        tf += f" (page={page}, limit={limit})"
        if isinstance(raw, list):
            tf += f"; records={len(raw)}"
        if isinstance(rec, dict):
            title = rec.get("title")
            if title:
                tf += f"; first_title={title}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def search_crypto_news(
        self,
        symbols: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        page: int = 0,
        limit: int = 100,
    ) -> dict:
        """
        Search for crypto news articles by symbols and date range (FMP Stable).

        General description
        - Returns news items filtered by one or more crypto symbols (e.g., BTCUSD) within a date range. Useful for
          tracking macro/sector narratives and correlating news with crypto market moves.

        Use case
        - You want to monitor crypto symbol-specific news within a defined window and page through results.

        Parameters
        - symbols: Comma-separated symbols (e.g., `"BTCUSD"` or `"BTCUSD,ETHUSD"`).
        - from_date: Optional start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: Optional end date (`YYYY-MM-DD`) mapped to query parameter `to`.
        - page: Page index (non-negative integer).
        - limit: Page size / maximum records per page (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        search_crypto_news(symbols="BTCUSD", from_date="2025-01-01", to_date="2025-01-31", page=0, limit=50)
        ```

        Data source: FMP Stable Search Crypto News API
        - GET `https://financialmodelingprep.com/stable/news/crypto?symbols=...&from=...&to=...&page=...&limit=...` (from/to optional)
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        meta = {
            "tool": "search_crypto_news",
            "symbols": symbols,
            "from_date": from_date,
            "to_date": to_date,
            "page": page,
            "limit": limit,
        }
        if not symbols:
            return mcp_envelope_err("symbols is required", meta=meta)
        if not isinstance(page, int) or page < 0:
            return mcp_envelope_err("page must be a non-negative integer", meta=meta)
        if not isinstance(limit, int) or limit < 1:
            return mcp_envelope_err("limit must be a positive integer", meta=meta)
        if from_date:
            err = self._validate_iso_date(from_date, "from_date")
            if err:
                return mcp_envelope_err(err, meta=meta)
        if to_date:
            err = self._validate_iso_date(to_date, "to_date")
            if err:
                return mcp_envelope_err(err, meta=meta)

        syms = self._normalize_symbols(symbols)
        if not syms:
            return mcp_envelope_err("symbols must contain at least one non-empty symbol", meta=meta)
        params: Dict[str, Any] = {"symbols": syms, "page": page, "limit": limit}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        raw = self._request_json("news/crypto", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"Crypto news: {syms}"
        if from_date and to_date:
            tf += f" from {from_date} to {to_date}"
        elif from_date:
            tf += f" from {from_date}"
        elif to_date:
            tf += f" to {to_date}"
        tf += f" (page={page}, limit={limit})"
        if isinstance(raw, list):
            tf += f"; records={len(raw)}"
        if isinstance(rec, dict):
            title = rec.get("title")
            if title:
                tf += f"; first_title={title}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)


if __name__ == "__main__":
    tools = NewsMCPTools(api_key=os.getenv("FMP_API_KEY"))
    print(tools.search_stock_news(symbols="AAPL", from_date="2025-01-01", to_date="2025-01-31", page=0, limit=10))
    print(tools.search_crypto_news(symbols="BTCUSD", from_date="2025-01-01", to_date="2025-01-31", page=0, limit=10))


