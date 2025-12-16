import os
from typing import Any, Dict, Optional

from .basic import FMPMCPTools, mcp_envelope_err, mcp_envelope_ok, mcp_tool


class ChartMCPTools(FMPMCPTools):
    """
    Historical price/volume and intraday chart time series (FMP Stable).

    Use this category for **time-series price data**: end-of-day OHLCV history and intraday bars
    (5m/15m/30m/1h/4h) for event studies, backtesting, and charting.
    """

    def __init__(self, api_key: Optional[str] = None, logger=None):
        super().__init__(api_key, logger)

    def _normalize_symbol(self, symbol: str) -> str:
        return str(symbol).strip().upper()

    @mcp_tool()
    def get_stock_price_volume_data(self, symbol: str, from_date: Optional[str] = None, to_date: Optional[str] = None) -> dict:
        """
        Retrieves end-of-day historical price and volume data for a symbol.

        Use case
        - You want daily OHLCV history for backtesting, charting, or volatility/volume analysis.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - from_date: Optional start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: Optional end date (`YYYY-MM-DD`) mapped to query parameter `to`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_stock_price_volume_data(symbol="AAPL")
        ```

        Data source: FMP Stable Stock Price and Volume Data API
        - GET `https://financialmodelingprep.com/stable/historical-price-eod/full?symbol=...&from=YYYY-MM-DD&to=YYYY-MM-DD`
        Reference: [FMP stable historical-price-eod/full endpoint](https://financialmodelingprep.com/stable/historical-price-eod/full?symbol=AAPL&apikey=ixF8ja8u0jxXUkWEGL58R9OGXH9MySq3)
        """
        meta = {"tool": "get_stock_price_volume_data", "symbol": symbol, "from_date": from_date, "to_date": to_date}
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

        sym = self._normalize_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym}
        if from_date:
            params["from"] = str(from_date).strip()
        if to_date:
            params["to"] = str(to_date).strip()
        raw = self._request_json("historical-price-eod/full", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: EOD history"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        if isinstance(rec, dict) and rec.get("date"):
            tf += f"; latest_date={rec.get('date')}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_historical_chart_5min(self, symbol: str, from_date: Optional[str] = None, to_date: Optional[str] = None) -> dict:
        """
        Retrieves 5-minute interval historical chart data for a symbol.

        Use case
        - You want intraday 5-minute bars for short-horizon analysis and charting.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - from_date: Optional start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: Optional end date (`YYYY-MM-DD`) mapped to query parameter `to`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_historical_chart_5min(symbol="AAPL")
        ```

        Data source: FMP Stable 5 Min Interval Stock Chart API
        - GET `https://financialmodelingprep.com/stable/historical-chart/5min?symbol=...&from=YYYY-MM-DD&to=YYYY-MM-DD`
        """
        return self._get_historical_chart_interval(symbol=symbol, interval="5min", from_date=from_date, to_date=to_date)

    @mcp_tool()
    def get_historical_chart_15min(self, symbol: str, from_date: Optional[str] = None, to_date: Optional[str] = None) -> dict:
        """
        Retrieves 15-minute interval historical chart data for a symbol.

        Use case
        - You want intraday 15-minute bars for short-horizon analysis and charting.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - from_date: Optional start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: Optional end date (`YYYY-MM-DD`) mapped to query parameter `to`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_historical_chart_15min(symbol="AAPL")
        ```

        Data source: FMP Stable 15 Min Interval Stock Chart API
        - GET `https://financialmodelingprep.com/stable/historical-chart/15min?symbol=...&from=YYYY-MM-DD&to=YYYY-MM-DD`
        """
        return self._get_historical_chart_interval(symbol=symbol, interval="15min", from_date=from_date, to_date=to_date)

    @mcp_tool()
    def get_historical_chart_30min(self, symbol: str, from_date: Optional[str] = None, to_date: Optional[str] = None) -> dict:
        """
        Retrieves 30-minute interval historical chart data for a symbol.

        Use case
        - You want intraday 30-minute bars for short-horizon analysis and charting.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - from_date: Optional start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: Optional end date (`YYYY-MM-DD`) mapped to query parameter `to`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_historical_chart_30min(symbol="AAPL")
        ```

        Data source: FMP Stable 30 Min Interval Stock Chart API
        - GET `https://financialmodelingprep.com/stable/historical-chart/30min?symbol=...&from=YYYY-MM-DD&to=YYYY-MM-DD`
        """
        return self._get_historical_chart_interval(symbol=symbol, interval="30min", from_date=from_date, to_date=to_date)

    @mcp_tool()
    def get_historical_chart_1hour(self, symbol: str, from_date: Optional[str] = None, to_date: Optional[str] = None) -> dict:
        """
        Retrieves 1-hour interval historical chart data for a symbol.

        Use case
        - You want intraday 1-hour bars for short-horizon analysis and charting.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - from_date: Optional start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: Optional end date (`YYYY-MM-DD`) mapped to query parameter `to`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_historical_chart_1hour(symbol="AAPL")
        ```

        Data source: FMP Stable 1 Hour Interval Stock Chart API
        - GET `https://financialmodelingprep.com/stable/historical-chart/1hour?symbol=...&from=YYYY-MM-DD&to=YYYY-MM-DD`
        """
        return self._get_historical_chart_interval(symbol=symbol, interval="1hour", from_date=from_date, to_date=to_date)

    @mcp_tool()
    def get_historical_chart_4hour(self, symbol: str, from_date: Optional[str] = None, to_date: Optional[str] = None) -> dict:
        """
        Retrieves 4-hour interval historical chart data for a symbol.

        Use case
        - You want intraday 4-hour bars for multi-day trend analysis and charting.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - from_date: Optional start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: Optional end date (`YYYY-MM-DD`) mapped to query parameter `to`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_historical_chart_4hour(symbol="AAPL")
        ```

        Data source: FMP Stable 4 Hour Interval Stock Chart API
        - GET `https://financialmodelingprep.com/stable/historical-chart/4hour?symbol=...&from=YYYY-MM-DD&to=YYYY-MM-DD`
        """
        return self._get_historical_chart_interval(symbol=symbol, interval="4hour", from_date=from_date, to_date=to_date)

    def _validate_iso_date(self, value: str, field: str) -> Optional[dict]:
        v = str(value).strip()
        if len(v) != 10 or v[4] != "-" or v[7] != "-":
            return mcp_envelope_err(f"{field} must be in YYYY-MM-DD format", meta={"tool": "_validate_iso_date", "field": field})
        y, m, d = v.split("-")
        if not (y.isdigit() and m.isdigit() and d.isdigit()):
            return mcp_envelope_err(f"{field} must be in YYYY-MM-DD format", meta={"tool": "_validate_iso_date", "field": field})
        return None

    def _get_historical_chart_interval(self, symbol: str, interval: str, from_date: Optional[str] = None, to_date: Optional[str] = None) -> dict:
        meta = {"tool": f"get_historical_chart_{interval}", "symbol": symbol, "interval": interval, "from_date": from_date, "to_date": to_date}
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

        sym = self._normalize_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym}
        if from_date:
            params["from"] = str(from_date).strip()
        if to_date:
            params["to"] = str(to_date).strip()
        raw = self._request_json(f"historical-chart/{interval}", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: historical chart {interval}"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        # FMP intraday bars often use 'date' or 'time'
        if isinstance(rec, dict):
            dt = rec.get("date") or rec.get("time")
            if dt:
                tf += f"; latest={dt}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)


if __name__ == "__main__":
    tools = ChartMCPTools(api_key=os.getenv("FMP_API_KEY"))
    print(tools.get_stock_price_volume_data(symbol="AAPL", from_date="2025-12-01", to_date="2025-12-15"))
    print(tools.get_historical_chart_5min(symbol="AAPL"))
    print(tools.get_historical_chart_15min(symbol="AAPL"))
    print(tools.get_historical_chart_30min(symbol="AAPL"))
    print(tools.get_historical_chart_1hour(symbol="AAPL"))
    print(tools.get_historical_chart_4hour(symbol="AAPL"))


