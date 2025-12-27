import os
from typing import Any, Dict, Optional

from .basic import FMPMCPTools, mcp_envelope_err, mcp_envelope_ok, mcp_tool


class IndexMCPTools(FMPMCPTools):
    """
    Stock index quote + historical chart endpoints (FMP Stable).

    Use this category for **index-level market context**: real-time index quotes, short quotes, and end-of-day index
    historical pricing series.

    Supported US index symbols (FMP)
    - `^NYA` (NYSE Composite)
    - `^XAX` (NYSE American Composite Index)
    - `^NYITR` (NYSE International 100 Index)
    - `^DJU` (Dow Jones Utility Average)
    - `^DJUSSC` (Dow Jones U.S. Semiconductors Index)
    - `^DJITR` (Dow Jones Industrial Average Total Return)
    - `^XCMP` (NASDAQ Composite Total Return)
    - `^SP500NR` (S&P 500 (Net Total Return))
    - `^IXIC` (NASDAQ Composite)
    - `^W5000` (Wilshire 5000 Total Market Index)
    - `^TRCCRBTR` (Thomson Reuters/CoreCommodity CRB Total Return Index)
    - `^DWCF` (Dow Jones U.S. Total Stock Market Index)
    - `^DJT` (Dow Jones Transportation Average)
    - `^DJI` (Dow Jones Industrial Average)
    - `^FVX` (Treasury Yield 5 Years Index)
    - `^TYX` (Treasury Yield 30 Years Index)
    - `^TNX` (Treasury Yield 10 Years Index)
    - `DX-Y.NYB` (US Dollar Index)
    - `^MID` (S&P Mid-Cap 400 Index)
    - `^SPX` (S&P 500 Index)
    - `^SPSIBI` (S&P Biotechnology Select Industry Index)
    - `^NQDEMC` (NASDAQ Germany Mid Cap Index)
    - `^SPGSCI` (S&P GSCI Index)
    - `^SPGNRUP` (S&P Global Natural Resources Index)
    - `^SPXEW` (S&P Equal Weight Index)
    - `^SP500-55` (S&P 500 Utilities (Sector))
    - `^SP500TR` (S&P 500 Total Return)
    - `^SP500-60` (S&P 500 Real Estate (Sector))
    - `^SP500-15` (S&P 500 Materials (Sector))
    - `^SP500-45` (S&P 500 Information Technology (Sector))
    - `^SP500-20` (S&P 500 Industrials (Sector))
    - `^SP500-35` (S&P 500 Health Care (Sector))
    - `^GSPE` (S&P 500 Energy (Sector))
    - `^SP500-40` (S&P 500 Financials (Sector))
    - `^SPXESUP` (S&P 500 ESG Index (USD))
    - `^SP500-30` (S&P 500 Consumer Staples (Sector))
    - `^SP500-25` (S&P 500 Consumer Discretionary (Sector))
    - `^SP500-50` (S&P 500 Communication Services)
    - `^GSPC` (S&P 500)
    - `^OEX` (S&P 100 Index)
    - `^OOI` (S&P 100 Global Index)
    - `^RMCCTR` (Russell Midcap Total Return)
    - `^RUATR` (Russell 3000 Total Return)
    - `^RUA` (Russell 3000)
    - `^RUTTR` (Russell 2000 Total Return)
    - `^RUT` (Russell 2000)
    - `^RUITR` (Russell 1000 Total Return)
    - `^RUI` (Russell 1000)
    - `^ICEBIO` (NYSE Biotechnology Index)
    - `MSCIWORLD` (MSCI World Index)
    - `^RMZ` (MSCI US REIT Index)
    - `^105834-USD-STRD` (MSCI EAFE Value)
    - `^105833-USD-STRD` (MSCI EAFE Growth)
    - `^MOVE` (ICE BofAML MOVE Index)
    - `^TRAN` (NASDAQ Transportation)
    - `^NBI` (NASDAQ Biotechnology)
    - `^XNDX` (NASDAQ 100 Total Return Index)
    - `^NDX` (NASDAQ 100)
    - `^VIX` (CBOE Volatility Index)
    - `^VVIX` (CBOE VIX Volatility Index)
    - `^RVX` (CBOE Russell 2000 Volatility Index)
    - `^VIN` (CBOE Near-term VIX Index)
    - `^VXN` (CBOE NASDAQ 100 Volatility)
    - `^GVZ` (CBOE Gold Volatility Index)
    - `^VIF` (CBOE Far Term VIX Index)
    - `^OVX` (CBOE Crude Oil Volatility Index)
    - `^AMZ` (Alerian MLP Index)
    - `^VXSLV` (CBOE Silver ETF Volatility Index)
    - `^VWA` (CBOE Market Volatility SPX Offer Price Index)
    - `^EMCLOUD` (BVP Nasdaq Emerging Cloud Index)
    - `^IRX` (13 Week Treasury Bill Index)
    - `^VIX6M` (CBOE S&P 500 6 Month Volatility)
    - `^VIX3M` (CBOE S&P 500 3 Month Volatility)
    - `^VWB` (CBOE Market Volatility SPX Bid)
    - `^COR3M` (CBOE Implied Correlation Index)
    - `^VIX1D` (CBOE 1-Day Volatility Index)
    - `^DJX` (1/100 Dow Jones Industrial Average)
    - `^VXTLT` (CBOE 20+ Year Treasury Bond ETF Volatility Index)
    """

    def __init__(self, api_key: Optional[str] = None, logger=None):
        super().__init__(api_key, logger)

    def _normalize_symbol(self, symbol: str) -> str:
        return str(symbol).strip()

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
    def get_index_quote(self, symbol: str) -> dict:
        """
        Fetches a real-time quote snapshot for a stock index symbol.

        General description
        - Returns the latest quote payload for an index, including price, daily change, day high/low, year high/low,
          volume, and moving averages when available.
        - FMP index symbols often look like `^GSPC`, `^IXIC`, `^DJI`, etc.

        Use case
        - You want up-to-date index-level market context (e.g., S&P 500, NASDAQ Composite, VIX) for a report, dashboard,
          or to gate downstream screening logic on “risk-on/risk-off” conditions.

        Parameters
        - symbol: Index symbol (e.g., `"^GSPC"`, `"^IXIC"`, `"^VIX"`).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_index_quote(symbol="^GSPC")
        ```

        Data source: FMP Stable Quote API (Index symbols supported)
        - GET `https://financialmodelingprep.com/stable/quote?symbol=...`
        """
        meta = {"tool": "get_index_quote", "symbol": symbol}
        sym = self._normalize_symbol(symbol)
        if not sym:
            return mcp_envelope_err("symbol is required", meta=meta)

        params = {"symbol": sym}
        raw = self._request_json("quote", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: index quote"
        if isinstance(rec, dict):
            px = rec.get("price")
            ch = rec.get("change")
            cp = rec.get("changePercentage")
            if px is not None:
                tf += f"; price={px}"
            if ch is not None:
                tf += f"; change={ch}"
            if cp is not None:
                tf += f"; changePct={cp}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_index_short_quote(self, symbol: str) -> dict:
        """
        Fetches a concise quote snapshot for a stock index symbol.

        General description
        - Returns a smaller quote payload with only key fields (typically price, change, volume) for an index symbol.
        - This is useful when you need a fast “headline quote” and don’t need the extended fields.

        Use case
        - You want a lightweight index quote for quick UI render, alerting, or embedding in a broader market summary.

        Parameters
        - symbol: Index symbol (e.g., `"^GSPC"`).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_index_short_quote(symbol="^GSPC")
        ```

        Data source: FMP Stable Quote Short API (Index symbols supported)
        - GET `https://financialmodelingprep.com/stable/quote-short?symbol=...`
        """
        meta = {"tool": "get_index_short_quote", "symbol": symbol}
        sym = self._normalize_symbol(symbol)
        if not sym:
            return mcp_envelope_err("symbol is required", meta=meta)

        params = {"symbol": sym}
        raw = self._request_json("quote-short", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: index short quote"
        if isinstance(rec, dict):
            px = rec.get("price")
            ch = rec.get("change")
            vol = rec.get("volume")
            if px is not None:
                tf += f"; price={px}"
            if ch is not None:
                tf += f"; change={ch}"
            if vol is not None:
                tf += f"; volume={vol}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_historical_index_light_chart(self, symbol: str, from_date: str, to_date: str) -> dict:
        """
        Fetches end-of-day historical index prices (light payload).

        General description
        - Returns EOD historical records with essential fields (date, price, volume) for an index symbol between a date
          range.
        - This “light” variant is ideal for fast charting and back-of-the-envelope analyses.

        Use case
        - You want a time series for index trend visualization or simple return calculations without needing OHLCV.

        Parameters
        - symbol: Index symbol (e.g., `"^GSPC"`).
        - from_date: Start date (inclusive), `YYYY-MM-DD`.
        - to_date: End date (inclusive), `YYYY-MM-DD`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_historical_index_light_chart(symbol="^GSPC", from_date="2025-09-09", to_date="2025-12-09")
        ```

        Data source: FMP Stable Historical Price EOD Light API (Index symbols supported)
        - GET `https://financialmodelingprep.com/stable/historical-price-eod/light?symbol=...&from=...&to=...`
        """
        meta = {"tool": "get_historical_index_light_chart", "symbol": symbol, "from": from_date, "to": to_date}
        sym = self._normalize_symbol(symbol)
        if not sym:
            return mcp_envelope_err("symbol is required", meta=meta)
        ferr = self._validate_iso_date(from_date, "from_date")
        if ferr:
            return ferr
        terr = self._validate_iso_date(to_date, "to_date")
        if terr:
            return terr

        params = {"symbol": sym, "from": str(from_date).strip(), "to": str(to_date).strip()}
        raw = self._request_json("historical-price-eod/light", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: historical index prices (light) {str(from_date).strip()}..{str(to_date).strip()}"
        if isinstance(raw, list):
            tf += f"; records={len(raw)}"
        if isinstance(rec, dict) and rec.get("date"):
            tf += f"; first_date={rec.get('date')}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_historical_index_full_chart(self, symbol: str, from_date: str, to_date: str) -> dict:
        """
        Fetches end-of-day historical index prices (full OHLCV payload).

        General description
        - Returns EOD historical records with open/high/low/close/volume and additional metrics (e.g., vwap) for an index
          symbol between a date range.
        - This “full” variant is suited for technical analysis and backtesting.

        Use case
        - You want a complete OHLCV series for index-level trend/volatility analysis, indicator computation, or
          backtesting strategies on index data.

        Parameters
        - symbol: Index symbol (e.g., `"^GSPC"`).
        - from_date: Start date (inclusive), `YYYY-MM-DD`.
        - to_date: End date (inclusive), `YYYY-MM-DD`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_historical_index_full_chart(symbol="^GSPC", from_date="2025-09-09", to_date="2025-12-09")
        ```

        Data source: FMP Stable Historical Price EOD Full API (Index symbols supported)
        - GET `https://financialmodelingprep.com/stable/historical-price-eod/full?symbol=...&from=...&to=...`
        """
        meta = {"tool": "get_historical_index_full_chart", "symbol": symbol, "from": from_date, "to": to_date}
        sym = self._normalize_symbol(symbol)
        if not sym:
            return mcp_envelope_err("symbol is required", meta=meta)
        ferr = self._validate_iso_date(from_date, "from_date")
        if ferr:
            return ferr
        terr = self._validate_iso_date(to_date, "to_date")
        if terr:
            return terr

        params = {"symbol": sym, "from": str(from_date).strip(), "to": str(to_date).strip()}
        raw = self._request_json("historical-price-eod/full", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: historical index prices (full) {str(from_date).strip()}..{str(to_date).strip()}"
        if isinstance(raw, list):
            tf += f"; records={len(raw)}"
        if isinstance(rec, dict) and rec.get("date"):
            tf += f"; first_date={rec.get('date')}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)


if __name__ == "__main__":
    tools = IndexMCPTools(api_key=os.getenv("FMP_API_KEY"))
    print(tools.get_index_quote(symbol="^GSPC"))
    print(tools.get_index_short_quote(symbol="^GSPC"))
    print(tools.get_historical_index_light_chart(symbol="^GSPC", from_date="2025-09-09", to_date="2025-12-09"))
    print(tools.get_historical_index_full_chart(symbol="^GSPC", from_date="2025-09-09", to_date="2025-12-09"))


