import os

from .basic import *

class FinanceMCPTools(FMPMCPTools):
    """
    Company financial metrics, ratios, and fundamental growth tools (FMP Stable).

    Use this category for **fundamentals screening** and modeling inputs: key metrics (annual/quarter/TTM),
    ratio buckets (profitability/liquidity/leverage/valuation), financial “health” scores, enterprise values,
    and statement growth series.
    """
    def __init__(self, api_key: Optional[str] = None, logger=None):
        """
        Initialize Company Basic MCP Tools.
        """
        super().__init__(api_key, logger)

    def _validate_growth_period(self, period: Optional[str]) -> Optional[dict]:
        if period is None:
            return None
        p = str(period).strip()
        allowed = {"Q1", "Q2", "Q3", "Q4", "FY", "annual", "quarter"}
        if p not in allowed:
            return mcp_envelope_err(
                "period must be one of: Q1, Q2, Q3, Q4, FY, annual, quarter",
                meta={"tool": "_validate_growth_period", "period": period},
            )
        return None
    
    @mcp_tool()
    def get_company_metrics(
        self,
        symbol: str,
        period: Literal["annual", "quarter"] = "annual",
        limit: int = 1,
    ) -> dict:
        """
        Fetches “key metrics” for a company (e.g., revenue, net income, EBITDA, valuation ratios) and computes a
          best-effort `summary` from the first returned record.

        Use case
        - You want a compact set of headline financial metrics for quick screening or inclusion in a company snapshot.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period: `"annual"` or `"quarter"`.
        - limit: Maximum number of records to fetch (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record
          - meta.dependencies / meta.outputs / meta.recommended_next_tools: optional pipeline hints

        Concrete example (code)

        ```python
        get_company_metrics(symbol="AAPL", period="annual", limit=1)
        ```

        Data source: FMP Stable Key Metrics API
        - GET `https://financialmodelingprep.com/stable/key-metrics?symbol=...&period=...&limit=...`
        """
        meta = {"tool": "get_company_metrics", "symbol": symbol, "period": period, "limit": limit}
        if not symbol:
            return mcp_envelope_err("symbol is required", meta=meta)
        if period not in ("annual", "quarter"):
            return mcp_envelope_err("period must be 'annual' or 'quarter'", meta=meta)
        if not isinstance(limit, int) or limit < 1:
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        params = {"symbol": str(symbol).upper(), "period": period, "limit": limit}
        raw = self._request_json("key-metrics", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        summary: dict = {"symbol": str(symbol).upper(), "period": period}
        if isinstance(rec, dict) and "error" not in rec:
            # Pull common headline fields when present
            for k in (
                "date",
                "calendarYear",
                "period",
                "revenue",
                "netIncome",
                "ebitda",
                "peRatio",
                "pbRatio",
                "pocfratio",
                "pfcfRatio",
                "evToSales",
                "evToEbitda",
                "marketCap",
                "enterpriseValue",
                "eps",
                "freeCashFlowPerShare",
                "operatingCashFlowPerShare",
                "debtToEquity",
                "currentRatio",
                "roe",
                "roa",
                "roic",
                "grossProfitMargin",
                "operatingProfitMargin",
                "netProfitMargin",
            ):
                if k in rec:
                    summary[k] = rec.get(k)

        # Human-readable fallback
        tf = f"{summary.get('symbol')} {period} key metrics"
        if "date" in summary:
            tf += f" as of {summary.get('date')}"
        if "marketCap" in summary:
            tf += f"; marketCap={summary.get('marketCap')}"
        if "revenue" in summary:
            tf += f"; revenue={summary.get('revenue')}"
        if "netIncome" in summary:
            tf += f"; netIncome={summary.get('netIncome')}"

        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_company_metrics_ttm(self, symbol: Optional[str] = None, *, ticker: Optional[str] = None) -> dict:
        """
        Fetches TTM key metrics and computes a best-effort `summary` from the first returned record.

        Use case
        - You want TTM-normalized metrics (less seasonal noise) for quick valuation and profitability checks.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record
          - meta.dependencies / meta.outputs / meta.recommended_next_tools: optional pipeline hints

        Concrete example (code)

        ```python
        get_company_metrics_ttm(symbol="AAPL")
        ```

        Data source: FMP Stable Key Metrics TTM API
        - GET `https://financialmodelingprep.com/stable/key-metrics-ttm?symbol=...`
        """
        sym = str(symbol or ticker or "").strip().upper()
        meta = {"tool": "get_company_metrics_ttm", "symbol": sym}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)

        params = {"symbol": sym}
        raw = self._request_json("key-metrics-ttm", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        summary: dict = {"symbol": sym, "ttm": True}
        if isinstance(rec, dict) and "error" not in rec:
            for k in (
                "date",
                "revenue",
                "netIncome",
                "ebitda",
                "peRatio",
                "pbRatio",
                "pocfratio",
                "pfcfRatio",
                "evToSales",
                "evToEbitda",
                "marketCap",
                "enterpriseValue",
                "eps",
                "freeCashFlowPerShare",
                "operatingCashFlowPerShare",
                "debtToEquity",
                "currentRatio",
                "roe",
                "roa",
                "roic",
                "grossProfitMargin",
                "operatingProfitMargin",
                "netProfitMargin",
            ):
                if k in rec:
                    summary[k] = rec.get(k)

        tf = f"{summary.get('symbol')} TTM key metrics"
        if "date" in summary:
            tf += f" as of {summary.get('date')}"
        if "marketCap" in summary:
            tf += f"; marketCap={summary.get('marketCap')}"
        if "revenue" in summary:
            tf += f"; revenue={summary.get('revenue')}"
        if "netIncome" in summary:
            tf += f"; netIncome={summary.get('netIncome')}"

        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_company_finance(
        self,
        symbol: str,
        period: Literal["annual", "quarter"] = "annual",
        limit: int = 1,
    ) -> dict:
        """
        Fetches financial ratios and groups common fields into buckets (profitability, liquidity, efficiency, leverage,
          valuation, coverage) as a best-effort `summary` built from the first returned record.

        Use case
        - You want ratio-based health checks (liquidity, leverage, profitability, valuation) for screening and analysis.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period: `"annual"` or `"quarter"`.
        - limit: Maximum number of records to fetch (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record
          - meta.dependencies / meta.outputs / meta.recommended_next_tools: optional pipeline hints

        Concrete example (code)

        ```python
        get_company_finance(symbol="AAPL", period="annual", limit=1)
        ```

        Data source: FMP Stable Financial Ratios API
        - GET `https://financialmodelingprep.com/stable/ratios?symbol=...&period=...&limit=...`
        """
        meta = {"tool": "get_company_finance", "symbol": symbol, "period": period, "limit": limit}
        if not symbol:
            return mcp_envelope_err("symbol is required", meta=meta)
        if period not in ("annual", "quarter"):
            return mcp_envelope_err("period must be 'annual' or 'quarter'", meta=meta)
        if not isinstance(limit, int) or limit < 1:
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        params = {"symbol": str(symbol).upper(), "period": period, "limit": limit}
        raw = self._request_json("ratios", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        # Grouped summary: pull best-effort keys if present (FMP may vary by plan/version).
        summary: dict = {
            "symbol": str(symbol).upper(),
            "period": period,
            "profitability": {},
            "liquidity": {},
            "efficiency": {},
            "leverage": {},
            "valuation": {},
            "coverage": {},
        }

        if isinstance(rec, dict) and "error" not in rec:
            # metadata
            for k in ("date", "calendarYear", "period"):
                if k in rec:
                    summary[k] = rec.get(k)

            profitability_keys = (
                "grossProfitMargin",
                "operatingProfitMargin",
                "netProfitMargin",
                "returnOnAssets",
                "returnOnEquity",
                "returnOnCapitalEmployed",
                "ebitdaMargin",
                "pretaxProfitMargin",
                "effectiveTaxRate",
            )
            liquidity_keys = (
                "currentRatio",
                "quickRatio",
                "cashRatio",
                "operatingCashFlowRatio",
            )
            efficiency_keys = (
                "assetTurnover",
                "inventoryTurnover",
                "receivablesTurnover",
                "payablesTurnover",
                "fixedAssetTurnover",
                "daysOfSalesOutstanding",
                "daysOfInventoryOutstanding",
                "daysOfPayablesOutstanding",
                "operatingCycle",
                "cashConversionCycle",
            )
            leverage_keys = (
                "debtRatio",
                "debtEquityRatio",
                "longTermDebtToCapitalization",
                "totalDebtToCapitalization",
                "cashFlowToDebtRatio",
                "financialLeverage",
            )
            valuation_keys = (
                "priceEarningsRatio",
                "priceToBookRatio",
                "priceToSalesRatio",
                "priceToFreeCashFlowsRatio",
                "priceToOperatingCashFlowsRatio",
                "enterpriseValueMultiple",
                "priceFairValue",
                "priceEarningsToGrowthRatio",
            )
            coverage_keys = (
                "interestCoverage",
                "cashFlowCoverageRatios",
                "shortTermCoverageRatios",
                "capitalExpenditureCoverageRatio",
                "dividendPaidAndCapexCoverageRatio",
            )

            for k in profitability_keys:
                if k in rec:
                    summary["profitability"][k] = rec.get(k)
            for k in liquidity_keys:
                if k in rec:
                    summary["liquidity"][k] = rec.get(k)
            for k in efficiency_keys:
                if k in rec:
                    summary["efficiency"][k] = rec.get(k)
            for k in leverage_keys:
                if k in rec:
                    summary["leverage"][k] = rec.get(k)
            for k in valuation_keys:
                if k in rec:
                    summary["valuation"][k] = rec.get(k)
            for k in coverage_keys:
                if k in rec:
                    summary["coverage"][k] = rec.get(k)

        tf = f"{summary.get('symbol')} {period} financial ratios"
        if "date" in summary:
            tf += f" as of {summary.get('date')}"
        # include a couple common highlights when present
        try:
            cr = (summary.get("liquidity") or {}).get("currentRatio")
            de = (summary.get("leverage") or {}).get("debtEquityRatio")
            if cr is not None:
                tf += f"; currentRatio={cr}"
            if de is not None:
                tf += f"; debtEquityRatio={de}"
        except Exception:
            pass

        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_company_finance_ttm(self, symbol: Optional[str] = None, *, ticker: Optional[str] = None) -> dict:
        """
        Fetches TTM financial ratios and groups common fields into buckets as a best-effort `summary` built from the
          first returned record.

        Use case
        - You want TTM-normalized ratios for quick health checks without quarter-to-quarter seasonality effects.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record
          - meta.dependencies / meta.outputs / meta.recommended_next_tools: optional pipeline hints

        Concrete example (code)

        ```python
        get_company_finance_ttm(symbol="AAPL")
        ```

        Data source: FMP Stable Financial Ratios TTM API
        - GET `https://financialmodelingprep.com/stable/ratios-ttm?symbol=...`
        """
        sym = str(symbol or ticker or "").strip().upper()
        meta = {"tool": "get_company_finance_ttm", "symbol": sym}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)

        params = {"symbol": sym}
        raw = self._request_json("ratios-ttm", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        summary: dict = {
            "symbol": sym,
            "ttm": True,
            "profitability": {},
            "liquidity": {},
            "efficiency": {},
            "leverage": {},
            "valuation": {},
            "coverage": {},
        }

        if isinstance(rec, dict) and "error" not in rec:
            for k in ("date",):
                if k in rec:
                    summary[k] = rec.get(k)

            # Use the same buckets as non-TTM.
            profitability_keys = (
                "grossProfitMargin",
                "operatingProfitMargin",
                "netProfitMargin",
                "returnOnAssets",
                "returnOnEquity",
                "returnOnCapitalEmployed",
                "ebitdaMargin",
                "pretaxProfitMargin",
                "effectiveTaxRate",
            )
            liquidity_keys = (
                "currentRatio",
                "quickRatio",
                "cashRatio",
                "operatingCashFlowRatio",
            )
            efficiency_keys = (
                "assetTurnover",
                "inventoryTurnover",
                "receivablesTurnover",
                "payablesTurnover",
                "fixedAssetTurnover",
                "daysOfSalesOutstanding",
                "daysOfInventoryOutstanding",
                "daysOfPayablesOutstanding",
                "operatingCycle",
                "cashConversionCycle",
            )
            leverage_keys = (
                "debtRatio",
                "debtEquityRatio",
                "longTermDebtToCapitalization",
                "totalDebtToCapitalization",
                "cashFlowToDebtRatio",
                "financialLeverage",
            )
            valuation_keys = (
                "priceEarningsRatio",
                "priceToBookRatio",
                "priceToSalesRatio",
                "priceToFreeCashFlowsRatio",
                "priceToOperatingCashFlowsRatio",
                "enterpriseValueMultiple",
                "priceFairValue",
                "priceEarningsToGrowthRatio",
            )
            coverage_keys = (
                "interestCoverage",
                "cashFlowCoverageRatios",
                "shortTermCoverageRatios",
                "capitalExpenditureCoverageRatio",
                "dividendPaidAndCapexCoverageRatio",
            )

            for k in profitability_keys:
                if k in rec:
                    summary["profitability"][k] = rec.get(k)
            for k in liquidity_keys:
                if k in rec:
                    summary["liquidity"][k] = rec.get(k)
            for k in efficiency_keys:
                if k in rec:
                    summary["efficiency"][k] = rec.get(k)
            for k in leverage_keys:
                if k in rec:
                    summary["leverage"][k] = rec.get(k)
            for k in valuation_keys:
                if k in rec:
                    summary["valuation"][k] = rec.get(k)
            for k in coverage_keys:
                if k in rec:
                    summary["coverage"][k] = rec.get(k)

        tf = f"{summary.get('symbol')} TTM financial ratios"
        if "date" in summary:
            tf += f" as of {summary.get('date')}"
        try:
            cr = (summary.get("liquidity") or {}).get("currentRatio")
            de = (summary.get("leverage") or {}).get("debtEquityRatio")
            if cr is not None:
                tf += f"; currentRatio={cr}"
            if de is not None:
                tf += f"; debtEquityRatio={de}"
        except Exception:
            pass

        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_financial_scores(self, symbol: Optional[str] = None, *, ticker: Optional[str] = None) -> dict:
        """
        Fetches financial “health” scores (e.g., Altman Z-Score, Piotroski Score) for a company.

        Use case
        - You want a quick quantitative health/screening view of a company’s financial strength.

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
        get_financial_scores(symbol="AAPL")
        ```

        Data source: FMP Stable Financial Scores API
        - GET `https://financialmodelingprep.com/stable/financial-scores?symbol=...`
        Reference: [FMP stable financial-scores endpoint](https://financialmodelingprep.com/stable/financial-scores?symbol=AAPL)
        """
        sym = str(symbol or ticker or "").strip().upper()
        meta = {"tool": "get_financial_scores", "symbol": sym}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)

        params = {"symbol": sym}
        raw = self._request_json("financial-scores", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: financial scores"
        if isinstance(rec, dict):
            az = rec.get("altmanZScore")
            ps = rec.get("piotroskiScore")
            if az is not None:
                tf += f"; altmanZScore={az}"
            if ps is not None:
                tf += f"; piotroskiScore={ps}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_owner_earnings(self, symbol: Optional[str] = None, limit: Optional[int] = None, *, ticker: Optional[str] = None) -> dict:
        """
        Fetches “owner earnings” metrics (including owner earnings per share) across periods.

        Use case
        - You want a cash-owner-earnings style view of performance for valuation work.

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
        get_owner_earnings(symbol="AAPL", limit=5)
        ```

        Data source: FMP Stable Owner Earnings API
        - GET `https://financialmodelingprep.com/stable/owner-earnings?symbol=...`
        Reference: [FMP stable owner-earnings endpoint](https://financialmodelingprep.com/stable/owner-earnings?symbol=AAPL)
        """
        sym = str(symbol or ticker or "").strip().upper()
        meta = {"tool": "get_owner_earnings", "symbol": sym, "limit": limit}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        params = {"symbol": sym}
        if limit is not None:
            params["limit"] = limit
        raw = self._request_json("owner-earnings", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: owner earnings"
        if isinstance(rec, dict):
            dt = rec.get("date")
            oe = rec.get("ownersEarnings")
            oeps = rec.get("ownersEarningsPerShare")
            if dt:
                tf += f" as of {dt}"
            if oeps is not None:
                tf += f"; ownersEarningsPerShare={oeps}"
            elif oe is not None:
                tf += f"; ownersEarnings={oe}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_enterprise_values(
        self, symbol: Optional[str] = None, period: Optional[str] = None, limit: Optional[int] = None, *, ticker: Optional[str] = None
    ) -> dict:
        """
        Fetches enterprise value and its components (market cap, cash, total debt) across dates.

        Use case
        - You want enterprise value for valuation multiples (EV/EBITDA, EV/Sales) and capital structure context.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period: Optional period filter: `Q1`, `Q2`, `Q3`, `Q4`, `FY`, `annual`, `quarter`.
        - limit: Optional maximum number of records to return (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_enterprise_values(symbol="AAPL", period="annual", limit=5)
        ```

        Data source: FMP Stable Enterprise Values API
        - GET `https://financialmodelingprep.com/stable/enterprise-values?symbol=...`
        Reference: [FMP stable enterprise-values endpoint](https://financialmodelingprep.com/stable/enterprise-values?symbol=AAPL)
        """
        sym = str(symbol or ticker or "").strip().upper()
        meta = {"tool": "get_enterprise_values", "symbol": sym, "period": period, "limit": limit}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)
        perr = self._validate_growth_period(period)
        if perr:
            return perr
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        params = {"symbol": sym}
        if period is not None:
            params["period"] = str(period).strip()
        if limit is not None:
            params["limit"] = limit
        raw = self._request_json("enterprise-values", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: enterprise values"
        if isinstance(rec, dict):
            dt = rec.get("date")
            ev = rec.get("enterpriseValue")
            mc = rec.get("marketCapitalization") or rec.get("marketCap")
            if dt:
                tf += f" as of {dt}"
            if ev is not None:
                tf += f"; enterpriseValue={ev}"
            if mc is not None:
                tf += f"; marketCap={mc}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_income_statement(
        self,
        symbol: Optional[str] = None,
        period: Optional[str] = None,
        limit: Optional[int] = None,
        *,
        ticker: Optional[str] = None,
    ) -> dict:
        """
        Fetches income statement line items across periods (reported financials).

        Use case
        - You want the raw income statement (revenue, gross profit, operating income, net income, EPS, etc.) for
          modeling, trend analysis, or building a company snapshot.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period: Optional period filter: `Q1`, `Q2`, `Q3`, `Q4`, `FY`, `annual`, `quarter`.
        - limit: Optional maximum number of records to return (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_income_statement(symbol="AAPL", period="annual", limit=5)
        ```

        Data source: FMP Stable Income Statement API
        - GET `https://financialmodelingprep.com/stable/income-statement?symbol=...&limit=...&period=...`
        """
        # Be tolerant to LLM tool calls that use `ticker` instead of `symbol`.
        sym = str(symbol or ticker or "").strip().upper()
        meta = {"tool": "get_income_statement", "symbol": sym, "period": period, "limit": limit}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)
        perr = self._validate_growth_period(period)
        if perr:
            return perr
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        params = {"symbol": sym}
        if period is not None:
            params["period"] = str(period).strip()
        if limit is not None:
            params["limit"] = limit

        raw = self._request_json("income-statement", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        summary: dict = {"symbol": sym, "period": period}
        if isinstance(rec, dict) and "error" not in rec:
            for k in (
                "date",
                "filingDate",
                "acceptedDate",
                "reportedCurrency",
                "cik",
                "fiscalYear",
                "period",
                "revenue",
                "costOfRevenue",
                "grossProfit",
                "operatingExpenses",
                "operatingIncome",
                "incomeBeforeTax",
                "incomeTaxExpense",
                "netIncome",
                "ebitda",
                "ebit",
                "eps",
                "epsDiluted",
                "weightedAverageShsOut",
                "weightedAverageShsOutDil",
            ):
                if k in rec:
                    summary[k] = rec.get(k)

        tf = f"{summary.get('symbol')}: income statement"
        if period:
            tf += f" (period={period})"
        if "date" in summary:
            tf += f" as of {summary.get('date')}"
        if "revenue" in summary:
            tf += f"; revenue={summary.get('revenue')}"
        if "netIncome" in summary:
            tf += f"; netIncome={summary.get('netIncome')}"
        if "eps" in summary:
            tf += f"; eps={summary.get('eps')}"

        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_balance_sheet_statement(
        self, symbol: Optional[str] = None, period: Optional[str] = None, limit: Optional[int] = None, *, ticker: Optional[str] = None
    ) -> dict:
        """
        Fetches balance sheet statement line items across periods (assets, liabilities, and equity).

        Use case
        - You want detailed balance sheet items (cash, debt, working capital components, total assets/liabilities/equity)
          for financial health analysis and modeling.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period: Optional period filter: `Q1`, `Q2`, `Q3`, `Q4`, `FY`, `annual`, `quarter`.
        - limit: Optional maximum number of records to return (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_balance_sheet_statement(symbol="AAPL", period="annual", limit=5)
        ```

        Data source: FMP Stable Balance Sheet Statement API
        - GET `https://financialmodelingprep.com/stable/balance-sheet-statement?symbol=...&limit=...&period=...`
        """
        sym = str(symbol or ticker or "").strip().upper()
        meta = {"tool": "get_balance_sheet_statement", "symbol": sym, "period": period, "limit": limit}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)
        perr = self._validate_growth_period(period)
        if perr:
            return perr
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        params = {"symbol": sym}
        if period is not None:
            params["period"] = str(period).strip()
        if limit is not None:
            params["limit"] = limit

        raw = self._request_json("balance-sheet-statement", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        summary: dict = {"symbol": sym, "period": period}
        if isinstance(rec, dict) and "error" not in rec:
            for k in (
                "date",
                "filingDate",
                "acceptedDate",
                "reportedCurrency",
                "cik",
                "fiscalYear",
                "period",
                # headline balance sheet items
                "cashAndCashEquivalents",
                "cashAndShortTermInvestments",
                "totalCurrentAssets",
                "totalNonCurrentAssets",
                "totalAssets",
                "totalCurrentLiabilities",
                "totalNonCurrentLiabilities",
                "totalLiabilities",
                "totalStockholdersEquity",
                "totalEquity",
                "totalLiabilitiesAndTotalEquity",
                # debt highlights
                "shortTermDebt",
                "longTermDebt",
                "totalDebt",
                "netDebt",
            ):
                if k in rec:
                    summary[k] = rec.get(k)

        tf = f"{summary.get('symbol')}: balance sheet statement"
        if period:
            tf += f" (period={period})"
        if "date" in summary:
            tf += f" as of {summary.get('date')}"
        if "totalAssets" in summary:
            tf += f"; totalAssets={summary.get('totalAssets')}"
        if "totalLiabilities" in summary:
            tf += f"; totalLiabilities={summary.get('totalLiabilities')}"
        if "totalStockholdersEquity" in summary:
            tf += f"; totalStockholdersEquity={summary.get('totalStockholdersEquity')}"

        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_cash_flow_statement(
        self, symbol: Optional[str] = None, period: Optional[str] = None, limit: Optional[int] = None, *, ticker: Optional[str] = None
    ) -> dict:
        """
        Fetches cash flow statement line items across periods (operating, investing, and financing cash flows).

        Use case
        - You want detailed cash flow items (CFO, capex, free cash flow, financing flows) for sustainability and
          valuation analysis.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period: Optional period filter: `Q1`, `Q2`, `Q3`, `Q4`, `FY`, `annual`, `quarter`.
        - limit: Optional maximum number of records to return (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_cash_flow_statement(symbol="AAPL", period="annual", limit=5)
        ```

        Data source: FMP Stable Cash Flow Statement API
        - GET `https://financialmodelingprep.com/stable/cash-flow-statement?symbol=...&limit=...&period=...`
        """
        sym = str(symbol or ticker or "").strip().upper()
        meta = {"tool": "get_cash_flow_statement", "symbol": sym, "period": period, "limit": limit}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)
        perr = self._validate_growth_period(period)
        if perr:
            return perr
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        params = {"symbol": sym}
        if period is not None:
            params["period"] = str(period).strip()
        if limit is not None:
            params["limit"] = limit

        raw = self._request_json("cash-flow-statement", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        summary: dict = {"symbol": sym, "period": period}
        if isinstance(rec, dict) and "error" not in rec:
            for k in (
                "date",
                "filingDate",
                "acceptedDate",
                "reportedCurrency",
                "cik",
                "fiscalYear",
                "period",
                # headline cash flow items
                "netIncome",
                "netCashProvidedByOperatingActivities",
                "netCashProvidedByInvestingActivities",
                "netCashProvidedByFinancingActivities",
                "netChangeInCash",
                "cashAtBeginningOfPeriod",
                "cashAtEndOfPeriod",
                "operatingCashFlow",
                "capitalExpenditure",
                "freeCashFlow",
                "incomeTaxesPaid",
                "interestPaid",
            ):
                if k in rec:
                    summary[k] = rec.get(k)

        tf = f"{summary.get('symbol')}: cash flow statement"
        if period:
            tf += f" (period={period})"
        if "date" in summary:
            tf += f" as of {summary.get('date')}"
        if "operatingCashFlow" in summary:
            tf += f"; operatingCashFlow={summary.get('operatingCashFlow')}"
        if "capitalExpenditure" in summary:
            tf += f"; capex={summary.get('capitalExpenditure')}"
        if "freeCashFlow" in summary:
            tf += f"; freeCashFlow={summary.get('freeCashFlow')}"

        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_financial_reports_json(
        self, year: int, period: str = "FY", symbol: Optional[str] = None, *, ticker: Optional[str] = None
    ) -> dict:
        """
        Fetches comprehensive annual report financials in JSON form (Form 10-K style tables/sections).

        Use case
        - You want a structured JSON representation of annual report tables/sections (useful for extracting detailed
          disclosures and statement tables).

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - year: Fiscal year (e.g., `2022`).
        - period: Reporting period (typically `FY`). Allowed: `Q1`, `Q2`, `Q3`, `Q4`, `FY`, `annual`, `quarter`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary (symbol/year/period and a section count when possible)

        Concrete example (code)

        ```python
        get_financial_reports_json(symbol="AAPL", year=2022, period="FY")
        ```

        Data source: FMP Stable Financial Reports JSON API
        - GET `https://financialmodelingprep.com/stable/financial-reports-json?symbol=...&year=...&period=...`
        """
        sym = str(symbol or ticker or "").strip().upper()
        meta = {"tool": "get_financial_reports_json", "symbol": sym, "year": year, "period": period}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)
        if not isinstance(year, int) or year < 1900:
            return mcp_envelope_err("year must be a valid integer (e.g., 2022)", meta=meta)
        perr = self._validate_growth_period(period)
        if perr:
            return perr

        params = {"symbol": sym, "year": year, "period": str(period).strip()}
        raw = self._request_json("financial-reports-json", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        # The record is typically a dict with top-level section keys (e.g., "Cover Page", "CONSOLIDATED ...")
        section_count = None
        if isinstance(rec, dict):
            try:
                section_count = len([k for k in rec.keys() if k not in ("symbol", "period", "year")])
            except Exception:
                section_count = None

        tf = f"{sym}: financial reports json {year} (period={str(period).strip()})"
        if section_count is not None:
            tf += f"; sections={section_count}"

        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_income_statement_growth(
        self, symbol: Optional[str] = None, period: Optional[str] = None, limit: Optional[int] = None, *, ticker: Optional[str] = None
    ) -> dict:
        """
        Fetches growth rates for income statement line items (e.g., revenue growth, net income growth) across periods.

        Use case
        - You want growth metrics for trend analysis and to quantify acceleration/slowdown in fundamentals.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period: Optional period filter: `Q1`, `Q2`, `Q3`, `Q4`, `FY`, `annual`, `quarter`.
        - limit: Optional maximum number of records to return (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_income_statement_growth(symbol="AAPL", period="annual", limit=5)
        ```

        Data source: FMP Stable Income Statement Growth API
        - GET `https://financialmodelingprep.com/stable/income-statement-growth?symbol=...`
        Reference: [FMP stable income-statement-growth endpoint](https://financialmodelingprep.com/stable/income-statement-growth?symbol=AAPL)
        """
        sym = str(symbol or ticker or "").strip().upper()
        meta = {"tool": "get_income_statement_growth", "symbol": sym, "period": period, "limit": limit}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)
        perr = self._validate_growth_period(period)
        if perr:
            return perr
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        params = {"symbol": sym}
        if period is not None:
            params["period"] = str(period).strip()
        if limit is not None:
            params["limit"] = limit
        raw = self._request_json("income-statement-growth", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: income statement growth"
        if isinstance(rec, dict):
            dt = rec.get("date")
            gr = rec.get("growthRevenue")
            gni = rec.get("growthNetIncome")
            if dt:
                tf += f" as of {dt}"
            if gr is not None:
                tf += f"; growthRevenue={gr}"
            if gni is not None:
                tf += f"; growthNetIncome={gni}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_balance_sheet_statement_growth(
        self, symbol: Optional[str] = None, period: Optional[str] = None, limit: Optional[int] = None, *, ticker: Optional[str] = None
    ) -> dict:
        """
        Fetches growth rates for balance sheet line items across periods.

        Use case
        - You want growth metrics for assets, liabilities, and equity to track balance sheet expansion and leverage changes.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period: Optional period filter: `Q1`, `Q2`, `Q3`, `Q4`, `FY`, `annual`, `quarter`.
        - limit: Optional maximum number of records to return (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_balance_sheet_statement_growth(symbol="AAPL", period="annual", limit=5)
        ```

        Data source: FMP Stable Balance Sheet Statement Growth API
        - GET `https://financialmodelingprep.com/stable/balance-sheet-statement-growth?symbol=...`
        """
        sym = str(symbol or ticker or "").strip().upper()
        meta = {"tool": "get_balance_sheet_statement_growth", "symbol": sym, "period": period, "limit": limit}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)
        perr = self._validate_growth_period(period)
        if perr:
            return perr
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        params = {"symbol": sym}
        if period is not None:
            params["period"] = str(period).strip()
        if limit is not None:
            params["limit"] = limit
        raw = self._request_json("balance-sheet-statement-growth", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: balance sheet growth"
        if isinstance(rec, dict) and rec.get("date"):
            tf += f" as of {rec.get('date')}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_cash_flow_statement_growth(
        self, symbol: Optional[str] = None, period: Optional[str] = None, limit: Optional[int] = None, *, ticker: Optional[str] = None
    ) -> dict:
        """
        Fetches growth rates for cash flow statement line items across periods.

        Use case
        - You want to track growth in operating cash flow, free cash flow, and other cash-flow drivers over time.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period: Optional period filter: `Q1`, `Q2`, `Q3`, `Q4`, `FY`, `annual`, `quarter`.
        - limit: Optional maximum number of records to return (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_cash_flow_statement_growth(symbol="AAPL", period="annual", limit=5)
        ```

        Data source: FMP Stable Cashflow Statement Growth API
        - GET `https://financialmodelingprep.com/stable/cash-flow-statement-growth?symbol=...`
        """
        sym = str(symbol or ticker or "").strip().upper()
        meta = {"tool": "get_cash_flow_statement_growth", "symbol": sym, "period": period, "limit": limit}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)
        perr = self._validate_growth_period(period)
        if perr:
            return perr
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        params = {"symbol": sym}
        if period is not None:
            params["period"] = str(period).strip()
        if limit is not None:
            params["limit"] = limit
        raw = self._request_json("cash-flow-statement-growth", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: cash flow growth"
        if isinstance(rec, dict) and rec.get("date"):
            tf += f" as of {rec.get('date')}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_financial_growth(
        self, symbol: Optional[str] = None, period: Optional[str] = None, limit: Optional[int] = None, *, ticker: Optional[str] = None
    ) -> dict:
        """
        Fetches growth rates across financial statements (aggregated growth metrics) across periods.

        Use case
        - You want a consolidated view of growth across multiple statement categories for trend analysis.

        Parameters
        - symbol: Stock ticker (e.g., `"AAPL"`).
        - period: Optional period filter: `Q1`, `Q2`, `Q3`, `Q4`, `FY`, `annual`, `quarter`.
        - limit: Optional maximum number of records to return (positive integer).

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary derived from the first record

        Concrete example (code)

        ```python
        get_financial_growth(symbol="AAPL", period="annual", limit=5)
        ```

        Data source: FMP Stable Financial Statement Growth API
        - GET `https://financialmodelingprep.com/stable/financial-growth?symbol=...`
        """
        sym = str(symbol or ticker or "").strip().upper()
        meta = {"tool": "get_financial_growth", "symbol": sym, "period": period, "limit": limit}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)
        perr = self._validate_growth_period(period)
        if perr:
            return perr
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            return mcp_envelope_err("limit must be a positive integer", meta=meta)

        params = {"symbol": sym}
        if period is not None:
            params["period"] = str(period).strip()
        if limit is not None:
            params["limit"] = limit
        raw = self._request_json("financial-growth", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        tf = f"{sym}: financial growth"
        if isinstance(rec, dict) and rec.get("date"):
            tf += f" as of {rec.get('date')}"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_revenue_product_segmentation(self, symbol: Optional[str] = None, *, ticker: Optional[str] = None) -> dict:
        """
        Fetches annual revenue breakdown by product line (product segmentation).

        Use case
        - You want to understand what product categories drive a company's revenue and how the mix changes over time.

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
        get_revenue_product_segmentation(symbol="AAPL")
        ```

        Data source: FMP Stable Revenue Product Segmentation API
        - GET `https://financialmodelingprep.com/stable/revenue-product-segmentation?symbol=...`
        """
        sym = str(symbol or ticker or "").strip().upper()
        meta = {"tool": "get_revenue_product_segmentation", "symbol": sym}
        if not sym:
            return mcp_envelope_err("symbol is required (provide `symbol` or `ticker`)", meta=meta)

        params = {"symbol": sym}
        raw = self._request_json("revenue-product-segmentation", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        rec = self._coerce_first_record(raw)
        summary: dict = {"symbol": sym}
        seg_keys: list[str] = []
        if isinstance(rec, dict) and "error" not in rec:
            for k in ("date", "fiscalYear", "period", "reportedCurrency"):
                if k in rec:
                    summary[k] = rec.get(k)
            data = rec.get("data") if isinstance(rec.get("data"), dict) else None
            if isinstance(data, dict):
                try:
                    seg_keys = [str(k) for k in data.keys()]
                except Exception:
                    seg_keys = []

        tf = f"{summary.get('symbol')}: revenue product segmentation"
        if "fiscalYear" in summary:
            tf += f" FY{summary.get('fiscalYear')}"
        if "date" in summary:
            tf += f" as of {summary.get('date')}"
        if seg_keys:
            tf += f"; segments={len(seg_keys)}"
            tf += f" ({', '.join(seg_keys[:5])}{'...' if len(seg_keys) > 5 else ''})"

        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

if __name__ == "__main__":
    # Debug-purpose examples.
    # Prefer using environment variable FMP_API_KEY:
    #   export FMP_API_KEY="YOUR_KEY"
    tools = FinanceMCPTools(api_key=os.getenv("FMP_API_KEY"))

    print(tools.get_company_metrics(symbol="AAPL", period="annual", limit=1))
    print(tools.get_company_metrics_ttm(symbol="AAPL"))
    print(tools.get_company_finance(symbol="AAPL", period="annual", limit=1))
    print(tools.get_company_finance_ttm(symbol="AAPL"))

    print(tools.get_financial_scores(symbol="AAPL"))
    print(tools.get_owner_earnings(symbol="AAPL", limit=5))
    print(tools.get_enterprise_values(symbol="AAPL", period="annual", limit=5))
    print(tools.get_income_statement(symbol="AAPL", period="annual", limit=5))
    print(tools.get_balance_sheet_statement(symbol="AAPL", period="annual", limit=5))
    print(tools.get_cash_flow_statement(symbol="AAPL", period="annual", limit=5))
    print(tools.get_financial_reports_json(symbol="AAPL", year=2022, period="FY"))
    print(tools.get_income_statement_growth(symbol="AAPL", period="annual", limit=5))
    print(tools.get_balance_sheet_statement_growth(symbol="AAPL", period="annual", limit=5))
    print(tools.get_cash_flow_statement_growth(symbol="AAPL", period="annual", limit=5))
    print(tools.get_financial_growth(symbol="AAPL", period="annual", limit=5))
    print(tools.get_revenue_product_segmentation(symbol="AAPL"))
    