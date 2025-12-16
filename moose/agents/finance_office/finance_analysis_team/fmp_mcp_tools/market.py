import os
from typing import Any, Dict, Optional

from .basic import FMPMCPTools, mcp_envelope_err, mcp_envelope_ok, mcp_tool


class MarketMCPTools(FMPMCPTools):
    """
    Market-wide sector/industry performance and valuation snapshots/series (FMP Stable).

    Use this category for **market regime and rotation** context: sector/industry performance snapshots,
    historical performance series, and sector/industry P/E snapshots and histories.
    """

    def __init__(self, api_key: Optional[str] = None, logger=None):
        super().__init__(api_key, logger)

    def _validate_iso_date(self, value: str, field: str) -> Optional[str]:
        v = str(value).strip()
        if len(v) != 10 or v[4] != "-" or v[7] != "-":
            return f"{field} must be in YYYY-MM-DD format"
        y, m, d = v.split("-")
        if not (y.isdigit() and m.isdigit() and d.isdigit()):
            return f"{field} must be in YYYY-MM-DD format"
        return None

    def _handle_fmp_error(self, raw: Any, meta: Dict[str, Any]) -> Optional[dict]:
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )
        return None

    @mcp_tool()
    def get_sector_performance_snapshot(
        self,
        date: str,
        exchange: Optional[str] = None,
        sector: Optional[str] = None,
    ) -> dict:
        """
        Retrieve a market sector performance snapshot for a given date (FMP Stable).

        General description
        - Returns sector-level performance metrics for a specific exchange and sector on a given date.
          This is typically used for market regime / rotation analysis.

        Use case
        - You want a point-in-time snapshot of how a specific sector performed on a particular date.

        Parameters
        - date: Snapshot date (`YYYY-MM-DD`).
        - exchange: Optional exchange identifier as supported by the endpoint.
        - sector: Optional sector name as supported by the endpoint (e.g., `Energy`).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_sector_performance_snapshot(date="2025-10-30", exchange="NYSE", sector="Energy")
        ```

        Data source: FMP Stable Market Sector Performance Snapshot API
        - GET `https://financialmodelingprep.com/stable/sector-performance-snapshot?date=...&exchange=...&sector=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        meta = {"tool": "get_sector_performance_snapshot", "date": date, "exchange": exchange, "sector": sector}
        if not date:
            return mcp_envelope_err("date is required", meta=meta)
        derr = self._validate_iso_date(date, "date")
        if derr:
            return mcp_envelope_err(derr, meta=meta)

        params: Dict[str, Any] = {"date": str(date).strip()}
        if exchange:
            params["exchange"] = str(exchange).strip()
        if sector:
            params["sector"] = str(sector).strip()
        raw = self._request_json("sector-performance-snapshot", params=params)
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        rec = self._coerce_first_record(raw)
        tf = f"Sector performance snapshot on {date}"
        if sector:
            tf += f" (sector={sector})"
        if exchange:
            tf += f" (exchange={exchange})"
        if isinstance(rec, dict):
            perf = rec.get("performance") or rec.get("changePercent") or rec.get("changesPercentage")
            if perf is not None:
                tf += f"; performance={perf}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_industry_performance_snapshot(
        self,
        date: str,
        exchange: Optional[str] = None,
        industry: Optional[str] = None,
    ) -> dict:
        """
        Retrieve an industry performance snapshot for a given date (FMP Stable).

        General description
        - Returns industry-level performance metrics for a specific exchange and industry on a given date.
          Useful for drilling down within sectors to identify leading/lagging industries.

        Use case
        - You want a point-in-time snapshot of how a specific industry performed on a particular date.

        Parameters
        - date: Snapshot date (`YYYY-MM-DD`).
        - exchange: Optional exchange identifier as supported by the endpoint.
        - industry: Optional industry name as supported by the endpoint (e.g., `Biotechnology`).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_industry_performance_snapshot(date="2025-10-30", exchange="NYSE", industry="Biotechnology")
        ```

        Data source: FMP Stable Industry Performance Snapshot API
        - GET `https://financialmodelingprep.com/stable/industry-performance-snapshot?date=...&exchange=...&industry=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        meta = {"tool": "get_industry_performance_snapshot", "date": date, "exchange": exchange, "industry": industry}
        if not date:
            return mcp_envelope_err("date is required", meta=meta)
        derr = self._validate_iso_date(date, "date")
        if derr:
            return mcp_envelope_err(derr, meta=meta)

        params: Dict[str, Any] = {"date": str(date).strip()}
        if exchange:
            params["exchange"] = str(exchange).strip()
        if industry:
            params["industry"] = str(industry).strip()
        raw = self._request_json("industry-performance-snapshot", params=params)
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        rec = self._coerce_first_record(raw)
        tf = f"Industry performance snapshot on {date}"
        if industry:
            tf += f" (industry={industry})"
        if exchange:
            tf += f" (exchange={exchange})"
        if isinstance(rec, dict):
            perf = rec.get("performance") or rec.get("changePercent") or rec.get("changesPercentage")
            if perf is not None:
                tf += f"; performance={perf}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_historical_sector_performance(
        self,
        sector: str,
        exchange: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> dict:
        """
        Retrieve historical market sector performance for a date range (FMP Stable).

        General description
        - Returns a time series of sector performance values for a given sector and exchange between two dates.

        Use case
        - You want to analyze sector rotation trends over time for a specific exchange.

        Parameters
        - sector: Sector name (e.g., `Energy`).
        - exchange: Optional exchange identifier as supported by the endpoint.
        - from_date: Optional start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: Optional end date (`YYYY-MM-DD`) mapped to query parameter `to`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary

        Concrete example (code)

        ```python
        get_historical_sector_performance(sector="Energy", exchange="NYSE", from_date="2024-01-01", to_date="2024-03-01")
        ```

        Data source: FMP Stable Historical Market Sector Performance API
        - GET `https://financialmodelingprep.com/stable/historical-sector-performance?sector=...&exchange=...&from=...&to=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        meta = {
            "tool": "get_historical_sector_performance",
            "sector": sector,
            "exchange": exchange,
            "from_date": from_date,
            "to_date": to_date,
        }
        if not sector:
            return mcp_envelope_err("sector is required", meta=meta)
        if from_date:
            derr = self._validate_iso_date(from_date, "from_date")
            if derr:
                return mcp_envelope_err(derr, meta=meta)
        if to_date:
            derr = self._validate_iso_date(to_date, "to_date")
            if derr:
                return mcp_envelope_err(derr, meta=meta)

        params: Dict[str, Any] = {"sector": str(sector).strip()}
        if exchange:
            params["exchange"] = str(exchange).strip()
        if from_date:
            params["from"] = str(from_date).strip()
        if to_date:
            params["to"] = str(to_date).strip()
        raw = self._request_json("historical-sector-performance", params=params)
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        tf = f"Historical sector performance: {sector}"
        if exchange:
            tf += f" ({exchange})"
        if from_date and to_date:
            tf += f" from {from_date} to {to_date}"
        elif from_date:
            tf += f" from {from_date}"
        elif to_date:
            tf += f" to {to_date}"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_historical_industry_performance(
        self,
        industry: str,
        exchange: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> dict:
        """
        Retrieve historical industry performance for a date range (FMP Stable).

        General description
        - Returns a time series of industry performance values for a given industry and exchange between two dates.

        Use case
        - You want to analyze industry leadership trends over time for a specific exchange.

        Parameters
        - industry: Industry name (e.g., `Biotechnology`).
        - exchange: Optional exchange identifier as supported by the endpoint.
        - from_date: Optional start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: Optional end date (`YYYY-MM-DD`) mapped to query parameter `to`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary

        Concrete example (code)

        ```python
        get_historical_industry_performance(industry="Biotechnology", exchange="NYSE", from_date="2024-01-01", to_date="2024-03-01")
        ```

        Data source: FMP Stable Historical Industry Performance API
        - GET `https://financialmodelingprep.com/stable/historical-industry-performance?industry=...&exchange=...&from=...&to=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        meta = {
            "tool": "get_historical_industry_performance",
            "industry": industry,
            "exchange": exchange,
            "from_date": from_date,
            "to_date": to_date,
        }
        if not industry:
            return mcp_envelope_err("industry is required", meta=meta)
        if from_date:
            derr = self._validate_iso_date(from_date, "from_date")
            if derr:
                return mcp_envelope_err(derr, meta=meta)
        if to_date:
            derr = self._validate_iso_date(to_date, "to_date")
            if derr:
                return mcp_envelope_err(derr, meta=meta)

        params: Dict[str, Any] = {"industry": str(industry).strip()}
        if exchange:
            params["exchange"] = str(exchange).strip()
        if from_date:
            params["from"] = str(from_date).strip()
        if to_date:
            params["to"] = str(to_date).strip()
        raw = self._request_json("historical-industry-performance", params=params)
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        tf = f"Historical industry performance: {industry}"
        if exchange:
            tf += f" ({exchange})"
        if from_date and to_date:
            tf += f" from {from_date} to {to_date}"
        elif from_date:
            tf += f" from {from_date}"
        elif to_date:
            tf += f" to {to_date}"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_sector_pe_snapshot(
        self,
        date: str,
        exchange: Optional[str] = None,
        sector: Optional[str] = None,
    ) -> dict:
        """
        Retrieve a sector P/E snapshot for a given date (FMP Stable).

        General description
        - Returns point-in-time P/E snapshot values for a sector on an exchange for a specific date.

        Use case
        - You want a valuation snapshot of a sector on a particular date for market comparison.

        Parameters
        - date: Snapshot date (`YYYY-MM-DD`).
        - exchange: Optional exchange identifier as supported by the endpoint.
        - sector: Optional sector name as supported by the endpoint (e.g., `Energy`).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_sector_pe_snapshot(date="2025-10-30", exchange="NYSE", sector="Energy")
        ```

        Data source: FMP Stable Sector PE Snapshot API
        - GET `https://financialmodelingprep.com/stable/sector-pe-snapshot?date=...&exchange=...&sector=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        meta = {"tool": "get_sector_pe_snapshot", "date": date, "exchange": exchange, "sector": sector}
        if not date:
            return mcp_envelope_err("date is required", meta=meta)
        derr = self._validate_iso_date(date, "date")
        if derr:
            return mcp_envelope_err(derr, meta=meta)

        params: Dict[str, Any] = {"date": str(date).strip()}
        if exchange:
            params["exchange"] = str(exchange).strip()
        if sector:
            params["sector"] = str(sector).strip()
        raw = self._request_json("sector-pe-snapshot", params=params)
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        rec = self._coerce_first_record(raw)
        tf = f"Sector PE snapshot on {date}"
        if sector:
            tf += f" (sector={sector})"
        if exchange:
            tf += f" (exchange={exchange})"
        if isinstance(rec, dict):
            pe = rec.get("pe") or rec.get("peRatio") or rec.get("peRatioTTM")
            if pe is not None:
                tf += f"; pe={pe}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_industry_pe_snapshot(
        self,
        date: str,
        exchange: Optional[str] = None,
        industry: Optional[str] = None,
    ) -> dict:
        """
        Retrieve an industry P/E snapshot for a given date (FMP Stable).

        General description
        - Returns point-in-time P/E snapshot values for an industry on an exchange for a specific date.

        Use case
        - You want a valuation snapshot of an industry on a particular date for market comparison.

        Parameters
        - date: Snapshot date (`YYYY-MM-DD`).
        - exchange: Optional exchange identifier as supported by the endpoint.
        - industry: Optional industry name as supported by the endpoint (e.g., `Biotechnology`).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_industry_pe_snapshot(date="2025-10-30", exchange="NYSE", industry="Biotechnology")
        ```

        Data source: FMP Stable Industry PE Snapshot API
        - GET `https://financialmodelingprep.com/stable/industry-pe-snapshot?date=...&exchange=...&industry=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        meta = {"tool": "get_industry_pe_snapshot", "date": date, "exchange": exchange, "industry": industry}
        if not date:
            return mcp_envelope_err("date is required", meta=meta)
        derr = self._validate_iso_date(date, "date")
        if derr:
            return mcp_envelope_err(derr, meta=meta)

        params: Dict[str, Any] = {"date": str(date).strip()}
        if exchange:
            params["exchange"] = str(exchange).strip()
        if industry:
            params["industry"] = str(industry).strip()
        raw = self._request_json("industry-pe-snapshot", params=params)
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        rec = self._coerce_first_record(raw)
        tf = f"Industry PE snapshot on {date}"
        if industry:
            tf += f" (industry={industry})"
        if exchange:
            tf += f" (exchange={exchange})"
        if isinstance(rec, dict):
            pe = rec.get("pe") or rec.get("peRatio") or rec.get("peRatioTTM")
            if pe is not None:
                tf += f"; pe={pe}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_historical_sector_pe(
        self,
        sector: str,
        exchange: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> dict:
        """
        Retrieve historical sector P/E values for a date range (FMP Stable).

        General description
        - Returns a time series of sector P/E values for a given sector and exchange between two dates.

        Use case
        - You want to analyze how sector valuation changed over time for a specific exchange.

        Parameters
        - sector: Sector name (e.g., `Energy`).
        - exchange: Optional exchange identifier as supported by the endpoint.
        - from_date: Optional start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: Optional end date (`YYYY-MM-DD`) mapped to query parameter `to`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary

        Concrete example (code)

        ```python
        get_historical_sector_pe(sector="Energy", exchange="NYSE", from_date="2024-01-01", to_date="2024-03-01")
        ```

        Data source: FMP Stable Historical Sector PE API
        - GET `https://financialmodelingprep.com/stable/historical-sector-pe?sector=...&exchange=...&from=...&to=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        meta = {"tool": "get_historical_sector_pe", "sector": sector, "exchange": exchange, "from_date": from_date, "to_date": to_date}
        if not sector:
            return mcp_envelope_err("sector is required", meta=meta)
        if from_date:
            derr = self._validate_iso_date(from_date, "from_date")
            if derr:
                return mcp_envelope_err(derr, meta=meta)
        if to_date:
            derr = self._validate_iso_date(to_date, "to_date")
            if derr:
                return mcp_envelope_err(derr, meta=meta)

        params: Dict[str, Any] = {"sector": str(sector).strip()}
        if exchange:
            params["exchange"] = str(exchange).strip()
        if from_date:
            params["from"] = str(from_date).strip()
        if to_date:
            params["to"] = str(to_date).strip()
        raw = self._request_json("historical-sector-pe", params=params)
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        tf = f"Historical sector PE: {sector}"
        if exchange:
            tf += f" ({exchange})"
        if from_date and to_date:
            tf += f" from {from_date} to {to_date}"
        elif from_date:
            tf += f" from {from_date}"
        elif to_date:
            tf += f" to {to_date}"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_historical_industry_pe(
        self,
        industry: str,
        exchange: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> dict:
        """
        Retrieve historical industry P/E values for a date range (FMP Stable).

        General description
        - Returns a time series of industry P/E values for a given industry and exchange between two dates.

        Use case
        - You want to analyze how industry valuation changed over time for a specific exchange.

        Parameters
        - industry: Industry name (e.g., `Biotechnology`).
        - exchange: Optional exchange identifier as supported by the endpoint.
        - from_date: Optional start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: Optional end date (`YYYY-MM-DD`) mapped to query parameter `to`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary

        Concrete example (code)

        ```python
        get_historical_industry_pe(industry="Biotechnology", exchange="NYSE", from_date="2024-01-01", to_date="2024-03-01")
        ```

        Data source: FMP Stable Historical Industry PE API
        - GET `https://financialmodelingprep.com/stable/historical-industry-pe?industry=...&exchange=...&from=...&to=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        meta = {"tool": "get_historical_industry_pe", "industry": industry, "exchange": exchange, "from_date": from_date, "to_date": to_date}
        if not industry:
            return mcp_envelope_err("industry is required", meta=meta)
        if from_date:
            derr = self._validate_iso_date(from_date, "from_date")
            if derr:
                return mcp_envelope_err(derr, meta=meta)
        if to_date:
            derr = self._validate_iso_date(to_date, "to_date")
            if derr:
                return mcp_envelope_err(derr, meta=meta)

        params: Dict[str, Any] = {"industry": str(industry).strip()}
        if exchange:
            params["exchange"] = str(exchange).strip()
        if from_date:
            params["from"] = str(from_date).strip()
        if to_date:
            params["to"] = str(to_date).strip()
        raw = self._request_json("historical-industry-pe", params=params)
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        tf = f"Historical industry PE: {industry}"
        if exchange:
            tf += f" ({exchange})"
        if from_date and to_date:
            tf += f" from {from_date} to {to_date}"
        elif from_date:
            tf += f" from {from_date}"
        elif to_date:
            tf += f" to {to_date}"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)


if __name__ == "__main__":
    tools = MarketMCPTools(api_key=os.getenv("FMP_API_KEY"))
    print(tools.get_sector_performance_snapshot(date="2025-10-30", exchange="NYSE", sector="Energy"))
    print(tools.get_industry_performance_snapshot(date="2025-10-30", exchange="NYSE", industry="Biotechnology"))
    print(tools.get_historical_sector_performance(sector="Energy", exchange="NYSE", from_date="2024-01-01", to_date="2024-03-01"))
    print(tools.get_historical_industry_performance(industry="Biotechnology", exchange="NYSE", from_date="2024-01-01", to_date="2024-03-01"))
    print(tools.get_sector_pe_snapshot(date="2025-10-30", exchange="NYSE", sector="Energy"))
    print(tools.get_industry_pe_snapshot(date="2025-10-30", exchange="NYSE", industry="Biotechnology"))
    print(tools.get_historical_sector_pe(sector="Energy", exchange="NYSE", from_date="2024-01-01", to_date="2024-03-01"))
    print(tools.get_historical_industry_pe(industry="Biotechnology", exchange="NYSE", from_date="2024-01-01", to_date="2024-03-01"))


