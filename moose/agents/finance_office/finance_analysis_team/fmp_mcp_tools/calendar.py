import os
from typing import Any, Dict, Optional

from .basic import FMPMCPTools, mcp_envelope_err, mcp_envelope_ok, mcp_tool


class CalendarMCPTools(FMPMCPTools):
    """
    Event calendars (earnings, IPOs, splits) from FMP Stable.

    Use this category to build **event timelines** and windows around corporate actions and scheduled events
    (earnings reporting, IPO listings, stock splits).
    """

    def __init__(self, api_key: Optional[str] = None, logger=None):
        super().__init__(api_key, logger)

    def _normalize_symbol(self, symbol: str) -> str:
        return str(symbol).strip().upper()

    def _validate_iso_date(self, value: str, field: str) -> Optional[dict]:
        v = str(value).strip()
        if len(v) != 10 or v[4] != "-" or v[7] != "-":
            return mcp_envelope_err(f"{field} must be in YYYY-MM-DD format", meta={"tool": "_validate_iso_date", "field": field})
        y, m, d = v.split("-")
        if not (y.isdigit() and m.isdigit() and d.isdigit()):
            return mcp_envelope_err(f"{field} must be in YYYY-MM-DD format", meta={"tool": "_validate_iso_date", "field": field})
        return None

    @mcp_tool()
    def get_earnings_report(self, symbol: str, limit: int = 10) -> dict:
        """
        Retrieves earnings reports for a company.

        Use case
        - You want recent reported earnings (dates/periods/EPS) for a company for event studies or screening.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - limit: Maximum number of records to return (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_earnings_report(symbol="AAPL", limit=10)
        ```

        Data source: FMP Stable Earnings Report API
        - GET `https://financialmodelingprep.com/stable/earnings?symbol=...&limit=...`
        """
        meta = {"tool": "get_earnings_report", "symbol": symbol, "limit": limit}
        if not symbol:
            return mcp_envelope_err("symbol is required", meta=meta)
        if not isinstance(limit, int) or limit < 1:
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        sym = self._normalize_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym, "limit": limit}
        raw = self._request_json("earnings", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: earnings (limit={limit})"
        if isinstance(raw, list):
            tf += f"; records={len(raw)}"
        if isinstance(rec, dict):
            dt = rec.get("date")
            if dt:
                tf += f"; latest_date={dt}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_earnings_calendar(self, from_date: str, to_date: str) -> dict:
        """
        Retrieves the earnings calendar for a date range.

        Use case
        - You want upcoming or historical earnings events within a date window for planning or analysis.

        Parameters
        - from_date: Start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: End date (`YYYY-MM-DD`) mapped to query parameter `to`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary

        Concrete example (code)

        ```python
        get_earnings_calendar(from_date="2025-01-01", to_date="2025-01-31")
        ```

        Data source: FMP Stable Earnings Calendar API
        - GET `https://financialmodelingprep.com/stable/earnings-calendar?from=YYYY-MM-DD&to=YYYY-MM-DD`
        """
        meta = {"tool": "get_earnings_calendar", "from_date": from_date, "to_date": to_date}
        if not from_date:
            return mcp_envelope_err("from_date is required", meta=meta)
        if not to_date:
            return mcp_envelope_err("to_date is required", meta=meta)
        err = self._validate_iso_date(from_date, "from_date")
        if err:
            return err
        err = self._validate_iso_date(to_date, "to_date")
        if err:
            return err

        params: Dict[str, Any] = {"from": str(from_date).strip(), "to": str(to_date).strip()}
        raw = self._request_json("earnings-calendar", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        tf = f"Earnings calendar from {str(from_date).strip()} to {str(to_date).strip()}"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_ipos_calendar(self, from_date: str, to_date: str) -> dict:
        """
        Retrieves the IPOs calendar for a date range.

        Use case
        - You want IPO listings within a date window for pipeline tracking or market activity monitoring.

        Parameters
        - from_date: Start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: End date (`YYYY-MM-DD`) mapped to query parameter `to`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary

        Concrete example (code)

        ```python
        get_ipos_calendar(from_date="2025-01-01", to_date="2025-01-31")
        ```

        Data source: FMP Stable IPOs Calendar API
        - GET `https://financialmodelingprep.com/stable/ipos-calendar?from=YYYY-MM-DD&to=YYYY-MM-DD`
        """
        meta = {"tool": "get_ipos_calendar", "from_date": from_date, "to_date": to_date}
        if not from_date:
            return mcp_envelope_err("from_date is required", meta=meta)
        if not to_date:
            return mcp_envelope_err("to_date is required", meta=meta)
        err = self._validate_iso_date(from_date, "from_date")
        if err:
            return err
        err = self._validate_iso_date(to_date, "to_date")
        if err:
            return err

        params: Dict[str, Any] = {"from": str(from_date).strip(), "to": str(to_date).strip()}
        raw = self._request_json("ipos-calendar", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        tf = f"IPOs calendar from {str(from_date).strip()} to {str(to_date).strip()}"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_stock_split_details(self, symbol: str, limit: int = 100) -> dict:
        """
        Retrieves stock split details for a company.

        Use case
        - You want historical split events for a company for price normalization and corporate actions analysis.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - limit: Maximum number of records to return (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_stock_split_details(symbol="AAPL", limit=100)
        ```

        Data source: FMP Stable Stock Split Details API
        - GET `https://financialmodelingprep.com/stable/splits?symbol=...&limit=...`
        """
        meta = {"tool": "get_stock_split_details", "symbol": symbol, "limit": limit}
        if not symbol:
            return mcp_envelope_err("symbol is required", meta=meta)
        if not isinstance(limit, int) or limit < 1:
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        sym = self._normalize_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym, "limit": limit}
        raw = self._request_json("splits", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: splits (limit={limit})"
        if isinstance(raw, list):
            tf += f"; records={len(raw)}"
        if isinstance(rec, dict) and rec.get("date"):
            tf += f"; latest_date={rec.get('date')}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)


if __name__ == "__main__":
    tools = CalendarMCPTools(api_key=os.getenv("FMP_API_KEY"))
    print(tools.get_earnings_report(symbol="AAPL", limit=5))
    print(tools.get_earnings_calendar(from_date="2025-01-01", to_date="2025-01-31"))
    print(tools.get_ipos_calendar(from_date="2025-01-01", to_date="2025-01-31"))
    print(tools.get_stock_split_details(symbol="AAPL", limit=10))


