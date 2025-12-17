import os
from typing import Any, Dict, Optional

from .basic import FMPMCPTools, mcp_envelope_err, mcp_envelope_ok, mcp_tool


class QuoteMCPTools(FMPMCPTools):
    """
    Quote and price-change snapshots from FMP Stable.

    Use this category for **current pricing context**: latest quote payloads and multi-horizon price change data.
    """

    def __init__(self, api_key: Optional[str] = None, logger=None):
        super().__init__(api_key, logger)

    def _normalize_symbol(self, symbol: str) -> str:
        return str(symbol).strip().upper()

    @mcp_tool()
    def get_stock_quote(self, symbol: str) -> dict:
        """
        Retrieves the latest quote payload for a single symbol.

        Use case
        - You want the current market quote snapshot for a stock for downstream pricing/monitoring logic.

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
        get_stock_quote(symbol="AAPL")
        ```

        Data source: FMP Stable Stock Quote API
        - GET `https://financialmodelingprep.com/stable/quote?symbol=...`
        """
        meta = {"tool": "get_stock_quote", "symbol": symbol}
        if not symbol:
            return mcp_envelope_err("symbol is required", meta=meta)

        sym = self._normalize_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym}
        raw = self._request_json("quote", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: quote"
        if isinstance(rec, dict):
            price = rec.get("price") or rec.get("lastPrice")
            ts = rec.get("timestamp") or rec.get("date")
            if ts is not None:
                tf += f" @ {ts}"
            if price is not None:
                tf += f"; price={price}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_stock_price_change(self, symbol: str) -> dict:
        """
        Retrieves price-change information (multiple horizons) for a single symbol.

        Use case
        - You want percent/absolute price changes over common time windows for momentum and performance context.

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
        get_stock_price_change(symbol="AAPL")
        ```

        Data source: FMP Stable Stock Price Change API
        - GET `https://financialmodelingprep.com/stable/stock-price-change?symbol=...`
        """
        meta = {"tool": "get_stock_price_change", "symbol": symbol}
        if not symbol:
            return mcp_envelope_err("symbol is required", meta=meta)

        sym = self._normalize_symbol(symbol)
        params: Dict[str, Any] = {"symbol": sym}
        raw = self._request_json("stock-price-change", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: price change"
        if isinstance(rec, dict):
            # best-effort common fields seen in this endpoint
            d1 = rec.get("1D") or rec.get("day") or rec.get("1d")
            d5 = rec.get("5D") or rec.get("5d")
            m1 = rec.get("1M") or rec.get("1m")
            if d1 is not None:
                tf += f"; 1D={d1}"
            if d5 is not None:
                tf += f"; 5D={d5}"
            if m1 is not None:
                tf += f"; 1M={m1}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)


if __name__ == "__main__":
    tools = QuoteMCPTools(api_key=os.getenv("FMP_API_KEY"))
    print(tools.get_stock_quote(symbol="AAPL"))
    print(tools.get_stock_price_change(symbol="AAPL"))


