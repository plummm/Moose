import os
from typing import Any, Dict, Optional

from .basic import FMPMCPTools, mcp_envelope_err, mcp_envelope_ok, mcp_tool


class AnalystMCPTools(FMPMCPTools):
    """
    Analyst estimates, ratings, price targets, and grades (FMP Stable).

    Use this category for **forward-looking expectations** and sentiment signals: analyst estimates,
    ratings snapshots/history, price target summaries/consensus, and grades/grade history.
    """

    def __init__(self, api_key: Optional[str] = None, logger=None):
        super().__init__(api_key, logger)

    def _normalize_symbol(self, symbol: str) -> str:
        return str(symbol).strip().upper()

    def _validate_estimates_period(self, period: str) -> Optional[str]:
        # Per docs, analyst-estimates supports annual/quarter.
        p = str(period).strip()
        if p not in {"annual", "quarter"}:
            return "period must be 'annual' or 'quarter'"
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
    def get_financial_estimates(
        self,
        symbol: Optional[str] = None,
        period: str = "annual",
        page: int = 0,
        limit: int = 10,
        *,
        ticker: Optional[str] = None,
    ) -> dict:
        """
        Retrieves analyst financial estimates for a company (FMP Stable).

        General description
        - Returns analyst estimates (e.g., revenue/EPS forecasts) for a symbol. Results are pageable and can be
          requested for annual or quarterly periods.

        Use case
        - You want forward-looking estimates to compare against reported results or to drive valuation models.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - page: Page index (non-negative integer).
        - limit: Maximum number of records per page (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_financial_estimates(symbol="AAPL", period="annual", page=0, limit=10)
        ```

        Data source: FMP Stable Financial Estimates API
        - GET `https://financialmodelingprep.com/stable/analyst-estimates?symbol=...&page=...&limit=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        sym = self._normalize_symbol(str(symbol or ticker or ""))
        meta = {"tool": "get_financial_estimates", "symbol": sym, "period": period, "page": page, "limit": limit}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)
        if not isinstance(page, int) or page < 0:
            return mcp_envelope_err("page must be a non-negative integer", meta=meta)
        if not isinstance(limit, int) or limit < 1:
            return mcp_envelope_err("limit must be a positive integer", meta=meta)
        perr = self._validate_estimates_period(period)
        if perr:
            return mcp_envelope_err(perr, meta=meta)

        params: Dict[str, Any] = {"symbol": sym, "period": str(period).strip(), "page": page, "limit": limit}
        raw = self._request_json("analyst-estimates", params=params)
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: analyst estimates ({str(period).strip()}, page={page}, limit={limit})"
        if isinstance(raw, list):
            tf += f"; records={len(raw)}"
        if isinstance(rec, dict):
            dt = rec.get("date") or rec.get("period")
            if dt:
                tf += f"; first_period={dt}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_ratings_snapshot(self, symbol: Optional[str] = None, *, ticker: Optional[str] = None) -> dict:
        """
        Retrieves the current ratings snapshot for a company (FMP Stable).

        General description
        - Returns a point-in-time ratings snapshot for a symbol. This is useful for quickly summarizing the market’s
          rating posture without scanning historical changes.

        Use case
        - You want a single payload capturing the latest rating information for a company for screening or dashboards.

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
        get_ratings_snapshot(symbol="AAPL")
        ```

        Data source: FMP Stable Ratings Snapshot API
        - GET `https://financialmodelingprep.com/stable/ratings-snapshot?symbol=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        sym = self._normalize_symbol(str(symbol or ticker or ""))
        meta = {"tool": "get_ratings_snapshot", "symbol": sym}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)

        raw = self._request_json("ratings-snapshot", params={"symbol": sym})
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: ratings snapshot"
        if isinstance(rec, dict):
            rating = rec.get("rating") or rec.get("ratingRecommendation")
            if rating:
                tf += f"; rating={rating}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_historical_ratings(self, symbol: Optional[str] = None, *, ticker: Optional[str] = None) -> dict:
        """
        Retrieves historical ratings for a company (FMP Stable).

        General description
        - Returns rating history for a symbol, allowing you to track changes over time.

        Use case
        - You want to build a timeline of rating changes and relate them to price performance or catalysts.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary

        Concrete example (code)

        ```python
        get_historical_ratings(symbol="AAPL")
        ```

        Data source: FMP Stable Historical Ratings API
        - GET `https://financialmodelingprep.com/stable/ratings-historical?symbol=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        sym = self._normalize_symbol(str(symbol or ticker or ""))
        meta = {"tool": "get_historical_ratings", "symbol": sym}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)

        raw = self._request_json("ratings-historical", params={"symbol": sym})
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        tf = f"{sym}: historical ratings"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_price_target_summary(self, symbol: Optional[str] = None, *, ticker: Optional[str] = None) -> dict:
        """
        Retrieves the price target summary for a company (FMP Stable).

        General description
        - Returns a summary view of analyst price targets for a symbol (e.g., low/high/average targets when provided).

        Use case
        - You want a compact view of the current price target range for a company for comparison to the current price.

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
        get_price_target_summary(symbol="AAPL")
        ```

        Data source: FMP Stable Price Target Summary API
        - GET `https://financialmodelingprep.com/stable/price-target-summary?symbol=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        sym = self._normalize_symbol(str(symbol or ticker or ""))
        meta = {"tool": "get_price_target_summary", "symbol": sym}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)

        raw = self._request_json("price-target-summary", params={"symbol": sym})
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: price target summary"
        if isinstance(rec, dict):
            avg = rec.get("priceTargetAverage") or rec.get("average") or rec.get("avg")
            high = rec.get("priceTargetHigh") or rec.get("high")
            low = rec.get("priceTargetLow") or rec.get("low")
            if avg is not None:
                tf += f"; avg={avg}"
            if low is not None:
                tf += f"; low={low}"
            if high is not None:
                tf += f"; high={high}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_price_target_consensus(self, symbol: Optional[str] = None, *, ticker: Optional[str] = None) -> dict:
        """
        Retrieves the price target consensus for a company (FMP Stable).

        General description
        - Returns consensus price target information for a symbol (the endpoint may include multiple measures
          depending on coverage).

        Use case
        - You want consensus price target data to compare market expectations across companies or over time.

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
        get_price_target_consensus(symbol="AAPL")
        ```

        Data source: FMP Stable Price Target Consensus API
        - GET `https://financialmodelingprep.com/stable/price-target-consensus?symbol=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        sym = self._normalize_symbol(str(symbol or ticker or ""))
        meta = {"tool": "get_price_target_consensus", "symbol": sym}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)

        raw = self._request_json("price-target-consensus", params={"symbol": sym})
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: price target consensus"
        if isinstance(rec, dict):
            consensus = rec.get("consensus") or rec.get("priceTargetConsensus")
            if consensus is not None:
                tf += f"; consensus={consensus}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_stock_grades(self, symbol: Optional[str] = None, *, ticker: Optional[str] = None) -> dict:
        """
        Retrieves stock grades for a company (FMP Stable).

        General description
        - Returns grading/ratings actions for a symbol, typically including firm, grade, and timing fields.

        Use case
        - You want to pull the latest grading actions for a company to summarize analyst sentiment signals.

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
        get_stock_grades(symbol="AAPL")
        ```

        Data source: FMP Stable Stock Grades API
        - GET `https://financialmodelingprep.com/stable/grades?symbol=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        sym = self._normalize_symbol(str(symbol or ticker or ""))
        meta = {"tool": "get_stock_grades", "symbol": sym}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)

        raw = self._request_json("grades", params={"symbol": sym})
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: stock grades"
        if isinstance(raw, list):
            tf += f"; records={len(raw)}"
        if isinstance(rec, dict):
            g = rec.get("grade")
            if g:
                tf += f"; first_grade={g}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_historical_stock_grades(self, symbol: Optional[str] = None, *, ticker: Optional[str] = None) -> dict:
        """
        Retrieves historical stock grades for a company (FMP Stable).

        General description
        - Returns grade history for a symbol, allowing you to track changes in grading over time.

        Use case
        - You want to build a timeline of grade changes and relate them to price performance or other events.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary

        Concrete example (code)

        ```python
        get_historical_stock_grades(symbol="AAPL")
        ```

        Data source: FMP Stable Historical Stock Grades API
        - GET `https://financialmodelingprep.com/stable/grades-historical?symbol=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        sym = self._normalize_symbol(str(symbol or ticker or ""))
        meta = {"tool": "get_historical_stock_grades", "symbol": sym}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)

        raw = self._request_json("grades-historical", params={"symbol": sym})
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        tf = f"{sym}: historical stock grades"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_stock_grades_summary(self, symbol: Optional[str] = None, *, ticker: Optional[str] = None) -> dict:
        """
        Retrieves stock grades consensus/summary for a company (FMP Stable).

        General description
        - Returns an aggregated/consensus view of grades for a symbol when provided by the endpoint.

        Use case
        - You want a compact summary of overall grading sentiment rather than individual grading actions.

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
        get_stock_grades_summary(symbol="AAPL")
        ```

        Data source: FMP Stable Stock Grades Summary API
        - GET `https://financialmodelingprep.com/stable/grades-consensus?symbol=...`
        Reference: https://site.financialmodelingprep.com/developer/docs
        """
        sym = self._normalize_symbol(str(symbol or ticker or ""))
        meta = {"tool": "get_stock_grades_summary", "symbol": sym}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)

        raw = self._request_json("grades-consensus", params={"symbol": sym})
        err = self._handle_fmp_error(raw, meta)
        if err:
            return err

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: grades summary"
        if isinstance(rec, dict):
            consensus = rec.get("consensus") or rec.get("gradeConsensus")
            if consensus is not None:
                tf += f"; consensus={consensus}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)


if __name__ == "__main__":
    tools = AnalystMCPTools(api_key=os.getenv("FMP_API_KEY"))
    print(tools.get_financial_estimates(symbol="AAPL", period="annual", page=0, limit=10))
    print(tools.get_ratings_snapshot(symbol="AAPL"))
    print(tools.get_historical_ratings(symbol="AAPL"))
    print(tools.get_price_target_summary(symbol="AAPL"))
    print(tools.get_price_target_consensus(symbol="AAPL"))
    print(tools.get_stock_grades(symbol="AAPL"))
    print(tools.get_historical_stock_grades(symbol="AAPL"))
    print(tools.get_stock_grades_summary(symbol="AAPL"))


