import os
from typing import Any, Dict, Optional

from .basic import FMPMCPTools, mcp_envelope_err, mcp_envelope_ok, mcp_tool


class InsiderMCPTools(FMPMCPTools):
    """
    Insider trading and beneficial ownership endpoints (FMP Stable).

    Use this category for **insider activity** monitoring and simple screening:
    latest insider trades, search utilities, insider trade statistics, and acquisitions of beneficial ownership.
    """

    def __init__(self, api_key: Optional[str] = None, logger=None):
        super().__init__(api_key, logger)

    def _validate_iso_date(self, value: str, field: str) -> Optional[dict]:
        v = str(value).strip()
        # Basic check: YYYY-MM-DD (length 10) and digits/dashes in right places.
        if len(v) != 10 or v[4] != "-" or v[7] != "-":
            return mcp_envelope_err(
                f"{field} must be in YYYY-MM-DD format",
                meta={"tool": "_validate_iso_date", "field": field, "value": value},
            )
        y, m, d = v.split("-")
        if not (y.isdigit() and m.isdigit() and d.isdigit()):
            return mcp_envelope_err(
                f"{field} must be in YYYY-MM-DD format",
                meta={"tool": "_validate_iso_date", "field": field, "value": value},
            )
        return None

    @mcp_tool()
    def get_latest_insider_trading(self, page: int = 0, limit: int = 100, date: Optional[str] = None) -> dict:
        """
        Fetches the latest insider trading activity across the market.

        General description
        - Returns the most recently reported insider transactions (Form 4 style records), including the filer, company,
          transaction type, shares/price, and a link to the SEC filing.

        Use case
        - You want to monitor fresh insider buying/selling activity on a given day, or build a daily “insider tape”
          for screening unusual transactions.

        Parameters
        - page: Page index (0-based, non-negative integer).
        - limit: Page size (positive integer).
        - date: Optional date filter in `YYYY-MM-DD` format (e.g., `"2025-09-09"`).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_latest_insider_trading(page=0, limit=100, date="2025-09-09")
        ```

        Data source: FMP Stable Latest Insider Trading API
        - GET `https://financialmodelingprep.com/stable/insider-trading/latest?page=...&limit=...&date=...`
        """
        meta = {"tool": "get_latest_insider_trading", "page": page, "limit": limit, "date": date}
        if not isinstance(page, int) or page < 0:
            return mcp_envelope_err("page must be a non-negative integer", meta=meta)
        if not isinstance(limit, int) or limit < 1:
            return mcp_envelope_err("limit must be a positive integer", meta=meta)
        if date is not None:
            derr = self._validate_iso_date(date, "date")
            if derr:
                return derr

        params: Dict[str, Any] = {"page": page, "limit": limit}
        if date is not None:
            params["date"] = str(date).strip()

        raw = self._request_json("insider-trading/latest", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = "Latest insider trading"
        if date:
            tf += f" for {str(date).strip()}"
        if isinstance(raw, list):
            tf += f"; records={len(raw)}"
        if isinstance(rec, dict):
            sym = rec.get("symbol")
            fdt = rec.get("filingDate")
            tdt = rec.get("transactionDate")
            ttype = rec.get("transactionType")
            if sym:
                tf += f"; first={sym}"
            if ttype:
                tf += f" {ttype}"
            if tdt:
                tf += f" on {tdt}"
            if fdt:
                tf += f" (filed {fdt})"

        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def search_insider_trades_by_reporting_name(self, name: str) -> dict:
        """
        Searches reporting-name directory results for insider trading (reporting person lookup).

        General description
        - Returns matching “reportingName” records and associated reporting CIKs for a name query. This is typically used
          as a helper step before filtering insider trade searches by `reportingCik`.

        Use case
        - You want to resolve a person’s reporting CIK(s) from a name fragment (e.g., “Zuckerberg”) before pulling the
          person’s trade history.

        Parameters
        - name: Reporting name query (e.g., `"Zuckerberg"`).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        search_insider_trades_by_reporting_name(name="Zuckerberg")
        ```

        Data source: FMP Stable Search Insider Trades by Reporting Name API
        - GET `https://financialmodelingprep.com/stable/insider-trading/reporting-name?name=...`
        """
        meta = {"tool": "search_insider_trades_by_reporting_name", "name": name}
        if not isinstance(name, str) or not name.strip():
            return mcp_envelope_err("name is required", meta=meta)

        params = {"name": str(name).strip()}
        raw = self._request_json("insider-trading/reporting-name", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"Reporting-name search: {str(name).strip()}"
        if isinstance(raw, list):
            tf += f"; matches={len(raw)}"
        if isinstance(rec, dict):
            rn = rec.get("reportingName")
            cik = rec.get("reportingCik")
            if rn:
                tf += f"; first={rn}"
            if cik:
                tf += f" (cik={cik})"

        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_insider_trade_statistics(self, symbol: str) -> dict:
        """
        Fetches insider trading statistics for a symbol (by year/quarter).

        General description
        - Returns aggregate counts and share totals for acquired vs disposed transactions, plus ratios and purchase/sale
          counts. This is useful for quickly summarizing insider activity intensity over time.

        Use case
        - You want a compact “insider sentiment” snapshot for a company (e.g., purchases vs sales in the last quarter).

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_insider_trade_statistics(symbol="AAPL")
        ```

        Data source: FMP Stable Insider Trade Statistics API
        - GET `https://financialmodelingprep.com/stable/insider-trading/statistics?symbol=...`
        """
        meta = {"tool": "get_insider_trade_statistics", "symbol": symbol}
        if not symbol:
            return mcp_envelope_err("symbol is required", meta=meta)

        params = {"symbol": str(symbol).upper()}
        raw = self._request_json("insider-trading/statistics", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{str(symbol).upper()}: insider trade statistics"
        if isinstance(rec, dict):
            y = rec.get("year")
            q = rec.get("quarter")
            buys = rec.get("totalPurchases")
            sells = rec.get("totalSales")
            if y is not None:
                tf += f" {y}"
            if q is not None:
                tf += f" Q{q}"
            if buys is not None:
                tf += f"; purchases={buys}"
            if sells is not None:
                tf += f"; sales={sells}"

        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_acquisition_of_beneficial_ownership(self, symbol: str, limit: Optional[int] = None) -> dict:
        """
        Fetches “acquisition of beneficial ownership” filings for a symbol.

        General description
        - Returns beneficial ownership acquisition records (e.g., Schedule 13 filings) including reporting person, voting/
          dispositive power fields, percent of class, and a link to the underlying SEC filing.

        Use case
        - You want to monitor large holder ownership changes for a company (activists, institutions, acquirers).

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - limit: Optional maximum number of records to return (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_acquisition_of_beneficial_ownership(symbol="AAPL", limit=10)
        ```

        Data source: FMP Stable Acquisition Ownership API
        - GET `https://financialmodelingprep.com/stable/acquisition-of-beneficial-ownership?symbol=...&limit=...`
        """
        meta = {"tool": "get_acquisition_of_beneficial_ownership", "symbol": symbol, "limit": limit}
        if not symbol:
            return mcp_envelope_err("symbol is required", meta=meta)
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        params: Dict[str, Any] = {"symbol": str(symbol).upper()}
        if limit is not None:
            params["limit"] = limit

        raw = self._request_json("acquisition-of-beneficial-ownership", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{str(symbol).upper()}: acquisition of beneficial ownership"
        if isinstance(raw, list):
            tf += f"; records={len(raw)}"
        if isinstance(rec, dict):
            fdt = rec.get("filingDate")
            nm = rec.get("nameOfReportingPerson")
            pct = rec.get("percentOfClass")
            if fdt:
                tf += f" filed {fdt}"
            if nm:
                tf += f"; first={nm}"
            if pct:
                tf += f" ({pct}%)"

        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def search_insider_trades(
        self,
        page: int = 0,
        limit: int = 100,
        symbol: Optional[str] = None,
        reportingCik: Optional[str] = None,
        companyCik: Optional[str] = None,
        transactionType: Optional[str] = None,
    ) -> dict:
        """
        Searches insider trading activity by company/symbol and optional filters.

        General description
        - Returns insider transaction records (Form 4 style) with filters like symbol, reporting CIK, company CIK,
          transaction type, and pagination.

        Use case
        - You want to retrieve recent insider sales for a company, or pull a specific insider’s trading history by
          `reportingCik`.

        Parameters
        - page: Page index (0-based, non-negative integer).
        - limit: Page size (positive integer).
        - symbol: Optional stock ticker (e.g., `"AAPL"`).
        - reportingCik: Optional reporting CIK (string, typically zero-padded).
        - companyCik: Optional company CIK (string, typically zero-padded).
        - transactionType: Optional transaction type filter (e.g., `"S-Sale"`).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        search_insider_trades(
            page=0,
            limit=100,
            symbol="AAPL",
            transactionType="S-Sale",
        )
        ```

        Data source: FMP Stable Search Insider Trades API
        - GET `https://financialmodelingprep.com/stable/insider-trading/search?page=...&limit=...&symbol=...&reportingCik=...&companyCik=...&transactionType=...`
        """
        meta = {
            "tool": "search_insider_trades",
            "page": page,
            "limit": limit,
            "symbol": symbol,
            "reportingCik": reportingCik,
            "companyCik": companyCik,
            "transactionType": transactionType,
        }
        if not isinstance(page, int) or page < 0:
            return mcp_envelope_err("page must be a non-negative integer", meta=meta)
        if not isinstance(limit, int) or limit < 1:
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        params: Dict[str, Any] = {"page": page, "limit": limit}
        if symbol is not None and str(symbol).strip():
            params["symbol"] = str(symbol).strip().upper()
        if reportingCik is not None and str(reportingCik).strip():
            params["reportingCik"] = str(reportingCik).strip()
        if companyCik is not None and str(companyCik).strip():
            params["companyCik"] = str(companyCik).strip()
        if transactionType is not None and str(transactionType).strip():
            params["transactionType"] = str(transactionType).strip()

        raw = self._request_json("insider-trading/search", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = "Insider trades search"
        if symbol:
            tf += f" {str(symbol).strip().upper()}"
        if transactionType:
            tf += f" ({str(transactionType).strip()})"
        if isinstance(raw, list):
            tf += f"; records={len(raw)}"
        if isinstance(rec, dict):
            fdt = rec.get("filingDate")
            rn = rec.get("reportingName")
            if rn:
                tf += f"; first={rn}"
            if fdt:
                tf += f" (filed {fdt})"

        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)


if __name__ == "__main__":
    tools = InsiderMCPTools(api_key=os.getenv("FMP_API_KEY"))
    print(tools.get_latest_insider_trading(page=0, limit=5, date="2025-09-09"))
    print(tools.search_insider_trades_by_reporting_name(name="Zuckerberg"))
    print(tools.get_insider_trade_statistics(symbol="AAPL"))
    print(tools.get_acquisition_of_beneficial_ownership(symbol="AAPL", limit=5))
    print(tools.search_insider_trades(page=0, limit=5, symbol="AAPL", transactionType="S-Sale"))


