import os
from typing import Any, Dict, Optional

from .basic import FMPMCPTools, mcp_envelope_err, mcp_envelope_ok, mcp_tool


class IndicatorMCPTools(FMPMCPTools):
    """
    Technical indicators (momentum/trend/volatility) from FMP Stable.

    Use this category for **technical analysis** workflows: moving averages (SMA/EMA/WMA/DEMA/TEMA),
    oscillators (RSI, Williams %R), volatility proxies (standard deviation), and trend strength (ADX).
    """

    def __init__(self, api_key: Optional[str] = None, logger=None):
        super().__init__(api_key, logger)

    def _normalize_symbol(self, symbol: str) -> str:
        return str(symbol).strip().upper()

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

    def _technical_indicator(
        self,
        *,
        indicator_path: str,
        tool_name: str,
        period_length: int,
        timeframe: str,
        text_label: str,
        symbol: Optional[str] = None,
        ticker: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> dict:
        sym = self._normalize_symbol(str(symbol or ticker or ""))
        meta = {
            "tool": tool_name,
            "symbol": sym,
            "period_length": period_length,
            "timeframe": timeframe,
            "from_date": from_date,
            "to_date": to_date,
        }
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)
        if not isinstance(period_length, int) or period_length < 1:
            return mcp_envelope_err("period_length must be a positive integer", meta=meta)
        if not timeframe:
            return mcp_envelope_err("timeframe is required", meta=meta)
        if from_date:
            derr = self._validate_iso_date(from_date, "from_date")
            if derr:
                return mcp_envelope_err(derr, meta=meta)
        if to_date:
            derr = self._validate_iso_date(to_date, "to_date")
            if derr:
                return mcp_envelope_err(derr, meta=meta)

        params: Dict[str, Any] = {
            "symbol": sym,
            "periodLength": period_length,
            "timeframe": str(timeframe).strip(),
        }
        if from_date:
            params["from"] = str(from_date).strip()
        if to_date:
            params["to"] = str(to_date).strip()
        raw = self._request_json(indicator_path, params=params)
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        tf = f"{sym}: {text_label} ({params['timeframe']}, periodLength={period_length})"
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
    def get_sma(
        self,
        period_length: int,
        timeframe: str,
        symbol: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        *,
        ticker: Optional[str] = None,
    ) -> dict:
        """
        Retrieve Simple Moving Average (SMA) values for a symbol (FMP Stable).

        General description
        - Computes SMA time series over the specified timeframe and period length within a date range.

        Use case
        - You want a baseline trend indicator to smooth price movements and support technical analysis workflows.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period_length: Window length used by the indicator, mapped to query parameter `periodLength`.
        - timeframe: Data timeframe (e.g., `1day`), mapped to query parameter `timeframe`.
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
        get_sma(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01")
        ```

        Data source: FMP Stable Simple Moving Average API
        - GET `https://financialmodelingprep.com/stable/technical-indicators/sma?symbol=...&periodLength=...&timeframe=...&from=...&to=...` (from/to optional)
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        return self._technical_indicator(
            indicator_path="technical-indicators/sma",
            tool_name="get_sma",
            period_length=period_length,
            timeframe=timeframe,
            text_label="SMA",
            symbol=symbol,
            ticker=ticker,
            from_date=from_date,
            to_date=to_date,
        )

    @mcp_tool()
    def get_ema(
        self,
        period_length: int,
        timeframe: str,
        symbol: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        *,
        ticker: Optional[str] = None,
    ) -> dict:
        """
        Retrieve Exponential Moving Average (EMA) values for a symbol (FMP Stable).

        General description
        - Computes EMA time series over the specified timeframe and period length within a date range.

        Use case
        - You want a trend indicator that reacts faster to recent prices than SMA for momentum-style analysis.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period_length: Window length used by the indicator, mapped to query parameter `periodLength`.
        - timeframe: Data timeframe (e.g., `1day`), mapped to query parameter `timeframe`.
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
        get_ema(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01")
        ```

        Data source: FMP Stable Exponential Moving Average API
        - GET `https://financialmodelingprep.com/stable/technical-indicators/ema?symbol=...&periodLength=...&timeframe=...&from=...&to=...` (from/to optional)
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        return self._technical_indicator(
            indicator_path="technical-indicators/ema",
            tool_name="get_ema",
            period_length=period_length,
            timeframe=timeframe,
            text_label="EMA",
            symbol=symbol,
            ticker=ticker,
            from_date=from_date,
            to_date=to_date,
        )

    @mcp_tool()
    def get_wma(
        self,
        period_length: int,
        timeframe: str,
        symbol: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        *,
        ticker: Optional[str] = None,
    ) -> dict:
        """
        Retrieve Weighted Moving Average (WMA) values for a symbol (FMP Stable).

        General description
        - Computes WMA time series over the specified timeframe and period length within a date range.

        Use case
        - You want a moving average that emphasizes recent prices while still smoothing noise.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period_length: Window length used by the indicator, mapped to query parameter `periodLength`.
        - timeframe: Data timeframe (e.g., `1day`), mapped to query parameter `timeframe`.
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
        get_wma(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01")
        ```

        Data source: FMP Stable Weighted Moving Average API
        - GET `https://financialmodelingprep.com/stable/technical-indicators/wma?symbol=...&periodLength=...&timeframe=...&from=...&to=...` (from/to optional)
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        return self._technical_indicator(
            indicator_path="technical-indicators/wma",
            tool_name="get_wma",
            period_length=period_length,
            timeframe=timeframe,
            text_label="WMA",
            symbol=symbol,
            ticker=ticker,
            from_date=from_date,
            to_date=to_date,
        )

    @mcp_tool()
    def get_dema(
        self,
        period_length: int,
        timeframe: str,
        symbol: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        *,
        ticker: Optional[str] = None,
    ) -> dict:
        """
        Retrieve Double Exponential Moving Average (DEMA) values for a symbol (FMP Stable).

        General description
        - Computes DEMA time series over the specified timeframe and period length within a date range.

        Use case
        - You want a faster-moving trend indicator that can reduce lag relative to standard EMAs.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period_length: Window length used by the indicator, mapped to query parameter `periodLength`.
        - timeframe: Data timeframe (e.g., `1day`), mapped to query parameter `timeframe`.
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
        get_dema(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01")
        ```

        Data source: FMP Stable Double Exponential Moving Average API
        - GET `https://financialmodelingprep.com/stable/technical-indicators/dema?symbol=...&periodLength=...&timeframe=...&from=...&to=...` (from/to optional)
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        return self._technical_indicator(
            indicator_path="technical-indicators/dema",
            tool_name="get_dema",
            period_length=period_length,
            timeframe=timeframe,
            text_label="DEMA",
            symbol=symbol,
            ticker=ticker,
            from_date=from_date,
            to_date=to_date,
        )

    @mcp_tool()
    def get_tema(
        self,
        period_length: int,
        timeframe: str,
        symbol: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        *,
        ticker: Optional[str] = None,
    ) -> dict:
        """
        Retrieve Triple Exponential Moving Average (TEMA) values for a symbol (FMP Stable).

        General description
        - Computes TEMA time series over the specified timeframe and period length within a date range.

        Use case
        - You want a smoother but responsive moving average for trend detection with reduced lag.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period_length: Window length used by the indicator, mapped to query parameter `periodLength`.
        - timeframe: Data timeframe (e.g., `1day`), mapped to query parameter `timeframe`.
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
        get_tema(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01")
        ```

        Data source: FMP Stable Triple Exponential Moving Average API
        - GET `https://financialmodelingprep.com/stable/technical-indicators/tema?symbol=...&periodLength=...&timeframe=...&from=...&to=...` (from/to optional)
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        return self._technical_indicator(
            indicator_path="technical-indicators/tema",
            tool_name="get_tema",
            period_length=period_length,
            timeframe=timeframe,
            text_label="TEMA",
            symbol=symbol,
            ticker=ticker,
            from_date=from_date,
            to_date=to_date,
        )

    @mcp_tool()
    def get_rsi(
        self,
        period_length: int,
        timeframe: str,
        symbol: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        *,
        ticker: Optional[str] = None,
    ) -> dict:
        """
        Retrieve Relative Strength Index (RSI) values for a symbol (FMP Stable).

        General description
        - Computes RSI time series over the specified timeframe and period length within a date range.

        Use case
        - You want a momentum oscillator to identify potential overbought/oversold conditions.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period_length: Window length used by the indicator, mapped to query parameter `periodLength`.
        - timeframe: Data timeframe (e.g., `1day`), mapped to query parameter `timeframe`.
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
        get_rsi(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01")
        ```

        Data source: FMP Stable Relative Strength Index API
        - GET `https://financialmodelingprep.com/stable/technical-indicators/rsi?symbol=...&periodLength=...&timeframe=...&from=...&to=...` (from/to optional)
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        return self._technical_indicator(
            indicator_path="technical-indicators/rsi",
            tool_name="get_rsi",
            period_length=period_length,
            timeframe=timeframe,
            text_label="RSI",
            symbol=symbol,
            ticker=ticker,
            from_date=from_date,
            to_date=to_date,
        )

    @mcp_tool()
    def get_standard_deviation(
        self,
        period_length: int,
        timeframe: str,
        symbol: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        *,
        ticker: Optional[str] = None,
    ) -> dict:
        """
        Retrieve Standard Deviation values for a symbol (FMP Stable).

        General description
        - Computes rolling standard deviation time series over the specified timeframe and period length within a date range.

        Use case
        - You want a volatility proxy to quantify variability of price movements over time.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period_length: Window length used by the indicator, mapped to query parameter `periodLength`.
        - timeframe: Data timeframe (e.g., `1day`), mapped to query parameter `timeframe`.
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
        get_standard_deviation(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01")
        ```

        Data source: FMP Stable Standard Deviation API
        - GET `https://financialmodelingprep.com/stable/technical-indicators/standarddeviation?symbol=...&periodLength=...&timeframe=...&from=...&to=...` (from/to optional)
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        return self._technical_indicator(
            indicator_path="technical-indicators/standarddeviation",
            tool_name="get_standard_deviation",
            period_length=period_length,
            timeframe=timeframe,
            text_label="standard deviation",
            symbol=symbol,
            ticker=ticker,
            from_date=from_date,
            to_date=to_date,
        )

    @mcp_tool()
    def get_williams(
        self,
        period_length: int,
        timeframe: str,
        symbol: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        *,
        ticker: Optional[str] = None,
    ) -> dict:
        """
        Retrieve Williams %R values for a symbol (FMP Stable).

        General description
        - Computes Williams %R time series over the specified timeframe and period length within a date range.

        Use case
        - You want a momentum oscillator that measures overbought/oversold levels relative to recent highs and lows.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period_length: Window length used by the indicator, mapped to query parameter `periodLength`.
        - timeframe: Data timeframe (e.g., `1day`), mapped to query parameter `timeframe`.
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
        get_williams(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01")
        ```

        Data source: FMP Stable Williams API
        - GET `https://financialmodelingprep.com/stable/technical-indicators/williams?symbol=...&periodLength=...&timeframe=...&from=...&to=...` (from/to optional)
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        return self._technical_indicator(
            indicator_path="technical-indicators/williams",
            tool_name="get_williams",
            period_length=period_length,
            timeframe=timeframe,
            text_label="Williams %R",
            symbol=symbol,
            ticker=ticker,
            from_date=from_date,
            to_date=to_date,
        )

    @mcp_tool()
    def get_adx(
        self,
        period_length: int,
        timeframe: str,
        symbol: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        *,
        ticker: Optional[str] = None,
    ) -> dict:
        """
        Retrieve Average Directional Index (ADX) values for a symbol (FMP Stable).

        General description
        - Computes ADX time series over the specified timeframe and period length within a date range.

        Use case
        - You want a trend-strength indicator to help distinguish trending vs range-bound market conditions.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period_length: Window length used by the indicator, mapped to query parameter `periodLength`.
        - timeframe: Data timeframe (e.g., `1day`), mapped to query parameter `timeframe`.
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
        get_adx(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01")
        ```

        Data source: FMP Stable Average Directional Index API
        - GET `https://financialmodelingprep.com/stable/technical-indicators/adx?symbol=...&periodLength=...&timeframe=...&from=...&to=...` (from/to optional)
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        return self._technical_indicator(
            indicator_path="technical-indicators/adx",
            tool_name="get_adx",
            period_length=period_length,
            timeframe=timeframe,
            text_label="ADX",
            symbol=symbol,
            ticker=ticker,
            from_date=from_date,
            to_date=to_date,
        )


if __name__ == "__main__":
    tools = IndicatorMCPTools(api_key=os.getenv("FMP_API_KEY"))
    print(tools.get_sma(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01"))
    print(tools.get_ema(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01"))
    print(tools.get_wma(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01"))
    print(tools.get_dema(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01"))
    print(tools.get_tema(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01"))
    print(tools.get_rsi(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01"))
    print(tools.get_standard_deviation(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01"))
    print(tools.get_williams(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01"))
    print(tools.get_adx(symbol="AAPL", period_length=10, timeframe="1day", from_date="2024-01-01", to_date="2024-03-01"))


