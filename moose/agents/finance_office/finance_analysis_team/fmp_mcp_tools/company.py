import os
from typing import Any, Dict, List, Optional, Union

from .basic import FMPMCPTools, mcp_envelope_err, mcp_envelope_ok, mcp_tool


class CompanyMCPTools(FMPMCPTools):
    """
    Company MCP Tools - Company/People/M&A endpoints exposed through MCP.
    """

    def __init__(self, api_key: Optional[str] = None, logger=None):
        super().__init__(api_key, logger)

    def _normalize_symbol(self, symbol: str) -> str:
        return str(symbol).strip().upper()

    def _normalize_symbols(self, symbols: Union[str, List[str]]) -> List[str]:
        if isinstance(symbols, str):
            parts = [p.strip() for p in symbols.split(",")]
            return [p.upper() for p in parts if p]
        out: List[str] = []
        for s in symbols:
            ss = str(s).strip()
            if ss:
                out.append(ss.upper())
        return out

    def _validate_iso_date(self, value: str, field: str) -> Optional[dict]:
        v = str(value).strip()
        # Basic check: YYYY-MM-DD (length 10) and digits/dashes in right places.
        if len(v) != 10 or v[4] != "-" or v[7] != "-":
            return mcp_envelope_err(f"{field} must be in YYYY-MM-DD format", meta={"tool": "_validate_iso_date", "field": field})
        y, m, d = v.split("-")
        if not (y.isdigit() and m.isdigit() and d.isdigit()):
            return mcp_envelope_err(f"{field} must be in YYYY-MM-DD format", meta={"tool": "_validate_iso_date", "field": field})
        return None

    @mcp_tool()
    def get_company_employee_count(self, symbol: str, limit: int = 100) -> dict:
        """
        Retrieve employee count filings/records for a company (FMP Stable).

        General description
        - Returns workforce size information (employee count) reported by a company, typically keyed by reporting period
          and filing date. This is useful for analyzing headcount growth/downsizing and operating leverage trends.

        Use case
        - You want to get a quick “company snapshot” that includes the latest employee count for a company alongside
          market cap and float metrics.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - limit: Maximum number of records to return (positive integer).

        Return value
        - Dict with:
          - symbol: normalized ticker
          - limit: requested limit
          - latest: first record (best-effort) if the API returns a list, otherwise the dict response
          - raw: full FMP response payload

        Concrete example (code)

        ```python
        get_company_employee_count(symbol="AAPL", limit=5)
        ```

        Data source: FMP Stable Company Employee Count API
        - GET `https://financialmodelingprep.com/stable/employee-count?symbol=...`
        """
        meta = {"tool": "get_company_employee_count", "symbol": symbol, "limit": limit}
        if not symbol:
            return mcp_envelope_err("symbol is required", meta=meta)
        if not isinstance(limit, int) or limit < 1:
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        sym = self._normalize_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym, "limit": limit}
        raw = self._request_json("employee-count", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        latest = self._coerce_first_record(raw)
        tf = f"{sym}: employee count records"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        if isinstance(latest, dict):
            # best-effort common fields
            for k in ("periodOfReport", "date", "employeeCount", "fullTimeEmployees"):
                if k in latest and latest.get(k) is not None:
                    tf += f"; {k}={latest.get(k)}"
                    break

        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_company_market_cap(self, symbol: str) -> dict:
        """
        Retrieve a company’s market capitalization (FMP Stable).

        General description
        - Returns market cap data for a company.
          Market cap is typically computed as price × shares outstanding and is a common measure of company “size”.

        Use case
        - You want to compare a company’s market cap across dates around an event window (such as earnings or M&A news).

        Parameters
        - symbol: Stock ticker (e.g., `"MSFT"`).

        Return value
        - Dict with:
          - symbol: normalized ticker
          - date: date used (or None)
          - latest: first record (best-effort) if list response, otherwise dict response
          - raw: full FMP response payload

        Concrete example (code)

        ```python
        get_company_market_cap(symbol="MSFT")
        ```

        Data source: FMP Stable Company Market Cap API
        - GET `https://financialmodelingprep.com/stable/market-capitalization?symbol=...`
        """
        meta = {"tool": "get_company_market_cap", "symbol": symbol}
        if not symbol:
            return mcp_envelope_err("symbol is required", meta=meta)

        sym = self._normalize_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym}

        raw = self._request_json("market-capitalization", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        latest = self._coerce_first_record(raw)
        tf = f"{sym}: market cap"
        if isinstance(latest, dict):
            mc = latest.get("marketCap") or latest.get("marketCapitalization") or latest.get("mktCap")
            dt = latest.get("date")
            if dt:
                tf += f" as of {dt}"
            if mc is not None:
                tf += f" = {mc}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_batch_market_cap(self, symbols: Union[str, List[str]]) -> dict:
        """
        Retrieve market capitalization data for multiple companies in one request (FMP Stable).

        General description
        - Batch endpoint for market caps, useful for watchlists and peer comparisons.

        Use case
        - You want market caps for a watchlist or peer group in a single API call.

        Parameters
        - symbols: Either:
          - a comma-separated string (e.g., `"AAPL,MSFT,GOOGL"`), or
          - a list of tickers (e.g., `["AAPL", "MSFT", "GOOGL"]`)

        Return value
        - Dict with:
          - symbols: normalized list of tickers requested
          - by_symbol: map of `{SYMBOL: record}` when the API returns a list of dicts (best-effort)
          - raw: full FMP response payload

        Concrete example (code)

        ```python
        get_batch_market_cap(symbols=["AAPL", "MSFT", "GOOGL"])
        ```

        Data source: FMP Stable Batch Market Cap API
        - GET `https://financialmodelingprep.com/stable/market-capitalization-batch?symbols=AAPL,MSFT,...`
        """
        meta = {"tool": "get_batch_market_cap", "symbols": symbols}
        if symbols is None:
            return mcp_envelope_err("symbols is required", meta=meta)

        syms = self._normalize_symbols(symbols)
        if not syms:
            return mcp_envelope_err("symbols must contain at least one ticker", meta=meta)

        params: Dict[str, Any] = {"symbols": ",".join(syms)}
        raw = self._request_json("market-capitalization-batch", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        by_symbol: Dict[str, Any] = {}
        if isinstance(raw, list):
            for rec in raw:
                if isinstance(rec, dict):
                    s = rec.get("symbol")
                    if isinstance(s, str) and s:
                        by_symbol[s.upper()] = rec

        tf = f"Batch market cap for {len(syms)} symbols"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        return mcp_envelope_ok(data=raw, meta={**meta, "symbols": syms}, text_fallback=tf)

    @mcp_tool()
    def get_historical_market_cap(
        self,
        symbol: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """
        Retrieve historical market capitalization series for a company (FMP Stable).

        General description
        - Returns a time series of market cap values across dates. This is useful for long-horizon analysis, event
          studies, and charting market cap trend lines.

        Use case
        - You want a market-cap time series across a date range to analyze growth and company size over time.

        Parameters
        - symbol: Stock ticker (e.g., `"NVDA"`).
        - from_date: Optional ISO date `"YYYY-MM-DD"` (maps to API query param `from`).
        - to_date: Optional ISO date `"YYYY-MM-DD"` (maps to API query param `to`).
        - limit: Optional maximum number of records to return (positive integer).

        Return value
        - Dict with:
          - symbol, from_date, to_date, limit: normalized inputs
          - latest: first record (best-effort) if list response, otherwise dict response
          - raw: full FMP response payload (usually a list of `{date, marketCap, ...}` dicts)

        Concrete example (code)

        ```python
        get_historical_market_cap(symbol="NVDA", from_date="2023-01-01", to_date="2023-12-31", limit=200)
        ```

        Data source: FMP Stable Historical Market Cap API
        - GET `https://financialmodelingprep.com/stable/historical-market-capitalization?symbol=...&from=YYYY-MM-DD&to=YYYY-MM-DD&limit=...`
        """
        meta = {"tool": "get_historical_market_cap", "symbol": symbol, "from_date": from_date, "to_date": to_date, "limit": limit}
        if not symbol:
            return mcp_envelope_err("symbol is required", meta=meta)
        if from_date:
            err = self._validate_iso_date(from_date, "from_date")
            if err:
                return err
        if to_date:
            err = self._validate_iso_date(to_date, "to_date")
            if err:
                return err
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            return mcp_envelope_err("limit must be a positive integer when provided", meta=meta)

        sym = self._normalize_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym}
        if from_date:
            params["from"] = str(from_date).strip()
        if to_date:
            params["to"] = str(to_date).strip()
        if limit is not None:
            params["limit"] = limit

        raw = self._request_json("historical-market-capitalization", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        latest = self._coerce_first_record(raw)
        tf = f"{sym}: historical market cap"
        if from_date or to_date:
            tf += f" ({(str(from_date).strip() if from_date else '')}:{(str(to_date).strip() if to_date else '')})"
        if isinstance(raw, list):
            tf += f"; records={len(raw)}"
        return mcp_envelope_ok(
            data=raw,
            meta=meta,
            text_fallback=tf,
        )

    @mcp_tool()
    def get_company_share_float_liquidity(self, symbol: str) -> dict:
        """
        Retrieve a company’s share float and related liquidity/float fields (FMP Stable).

        General description
        - Share float is the number of shares available for public trading (excluding closely held/locked shares).
          This helps explain liquidity, volatility, and the “tradable” supply of shares.

        Use case
        - You want to assess whether a company’s tradable float may be contributing to liquidity or volatility.

        Parameters
        - symbol: Stock ticker (e.g., `"TSLA"`).

        Return value
        - Dict with:
          - symbol: normalized ticker
          - latest: first record (best-effort) if list response, otherwise dict response
          - raw: full FMP response payload

        Concrete example (code)

        ```python
        get_company_share_float_liquidity(symbol="TSLA")
        ```

        Data source: FMP Stable Company Share Float & Liquidity API
        - GET `https://financialmodelingprep.com/stable/shares-float?symbol=...`
        """
        meta = {"tool": "get_company_share_float_liquidity", "symbol": symbol}
        if not symbol:
            return mcp_envelope_err("symbol is required", meta=meta)

        sym = self._normalize_symbol(symbol)
        raw = self._request_json("shares-float", params={"symbol": sym})
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        latest = self._coerce_first_record(raw)
        tf = f"{sym}: share float"
        if isinstance(latest, dict):
            for k in ("floatShares", "sharesFloat", "freeFloat"):
                if k in latest and latest.get(k) is not None:
                    tf += f"; {k}={latest.get(k)}"
                    break
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_company_executives(self, symbol: str) -> dict:
        """
        Retrieve executives for a company (FMP Stable).

        General description
        - Returns key executives (names, titles, and related metadata when available). Useful for leadership research
          and governance analysis.

        Use case
        - You want to identify key executives and titles for leadership and governance research.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).

        Return value
        - Dict with:
          - symbol: normalized ticker
          - raw: full FMP response payload (typically a list of executive records)

        Concrete example (code)

        ```python
        get_company_executives(symbol="AAPL")
        ```

        Data source: FMP Stable Company Executives API
        - GET `https://financialmodelingprep.com/stable/key-executives?symbol=...`
        """
        meta = {"tool": "get_company_executives", "symbol": symbol}
        if not symbol:
            return mcp_envelope_err("symbol is required", meta=meta)

        sym = self._normalize_symbol(symbol)
        raw = self._request_json("key-executives", params={"symbol": sym})
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        tf = f"{sym}: executives"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_executive_compensation(self, symbol: str) -> dict:
        """
        Retrieve executive compensation details for a company (FMP Stable).

        General description
        - Returns compensation information for company executives (when available), such as salary, stock awards,
          total compensation, and reporting period metadata.

        Use case
        - You want executive compensation details to support governance review and pay-for-performance context.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).

        Return value
        - Dict with:
          - symbol: normalized ticker
          - raw: full FMP response payload

        Concrete example (code)

        ```python
        get_executive_compensation(symbol="AAPL")
        ```

        Data source: FMP Stable Executive Compensation API
        - GET `https://financialmodelingprep.com/stable/governance-executive-compensation?symbol=...`
        """
        meta = {"tool": "get_executive_compensation", "symbol": symbol}
        if not symbol:
            return mcp_envelope_err("symbol is required", meta=meta)

        sym = self._normalize_symbol(symbol)
        raw = self._request_json("governance-executive-compensation", params={"symbol": sym})
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        tf = f"{sym}: executive compensation"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)


