from .basic import *

class CompanyBasicMCPTools(FMPMCPTools):
    """
    Company Basic MCP Tools - Define company basic tools through MCP
    """
    def __init__(self, identity: str, logger=None):
        """
        Initialize Company Basic MCP Tools.
        """
        super().__init__(identity, logger)
    
    @mcp_tool()
    def get_company_metrics(
        self,
        symbol: str,
        period: Literal["annual", "quarter"] = "annual",
        limit: int = 1,
    ) -> dict:
        """
        Get basic company key metrics (revenue, net income, P/E, EBITDA, etc.).

        Data source: FinancialModelingPrep Stable Key Metrics API:
        - GET https://financialmodelingprep.com/stable/key-metrics?symbol=...

        Returns:
            {
              "symbol": "AAPL",
              "period": "annual" | "quarter",
              "summary": { ...selected headline metrics... },
              "raw": <full FMP response>
            }
        """
        if not symbol:
            return self._error("symbol is required")
        if period not in ("annual", "quarter"):
            return self._error("period must be 'annual' or 'quarter'")
        if not isinstance(limit, int) or limit < 1:
            return self._error("limit must be a positive integer")

        params = {"symbol": str(symbol).upper(), "period": period, "limit": limit}
        raw = self._request_json("key-metrics", params=params)
        if isinstance(raw, dict) and "error" in raw:
            return raw

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

        return {"symbol": str(symbol).upper(), "period": period, "summary": summary, "raw": raw}

    @mcp_tool()
    def get_company_metrics_ttm(self, symbol: str) -> dict:
        """
        Get basic company key metrics trailing twelve months (TTM).

        Data source: FinancialModelingPrep Stable Key Metrics TTM API:
        - GET https://financialmodelingprep.com/stable/key-metrics-ttm?symbol=...
        """
        if not symbol:
            return self._error("symbol is required")

        params = {"symbol": str(symbol).upper()}
        raw = self._request_json("key-metrics-ttm", params=params)
        if isinstance(raw, dict) and "error" in raw:
            return raw

        rec = self._coerce_first_record(raw)
        summary: dict = {"symbol": str(symbol).upper(), "ttm": True}
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

        return {"symbol": str(symbol).upper(), "summary": summary, "raw": raw}

    @mcp_tool()
    def get_company_finance(
        self,
        symbol: str,
        period: Literal["annual", "quarter"] = "annual",
        limit: int = 1,
    ) -> dict:
        """
        Get detailed financial ratios (profitability, liquidity, efficiency, leverage, valuation).

        Data source: FinancialModelingPrep Stable Financial Ratios API:
        - GET https://financialmodelingprep.com/stable/ratios?symbol=...

        Returns:
            {
              "symbol": "AAPL",
              "period": "annual" | "quarter",
              "summary": {
                  "profitability": {...},
                  "liquidity": {...},
                  "efficiency": {...},
                  "leverage": {...},
                  "valuation": {...},
                  "coverage": {...}
              },
              "raw": <full FMP response>
            }
        """
        if not symbol:
            return self._error("symbol is required")
        if period not in ("annual", "quarter"):
            return self._error("period must be 'annual' or 'quarter'")
        if not isinstance(limit, int) or limit < 1:
            return self._error("limit must be a positive integer")

        params = {"symbol": str(symbol).upper(), "period": period, "limit": limit}
        raw = self._request_json("ratios", params=params)
        if isinstance(raw, dict) and "error" in raw:
            return raw

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

        return {"symbol": str(symbol).upper(), "period": period, "summary": summary, "raw": raw}

    @mcp_tool()
    def get_company_finance_ttm(self, symbol: str) -> dict:
        """
        Get detailed financial ratios trailing twelve months (TTM).

        Data source: FinancialModelingPrep Stable Financial Ratios TTM API:
        - GET https://financialmodelingprep.com/stable/ratios-ttm?symbol=...
        """
        if not symbol:
            return self._error("symbol is required")

        params = {"symbol": str(symbol).upper()}
        raw = self._request_json("ratios-ttm", params=params)
        if isinstance(raw, dict) and "error" in raw:
            return raw

        rec = self._coerce_first_record(raw)
        summary: dict = {
            "symbol": str(symbol).upper(),
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

        return {"symbol": str(symbol).upper(), "summary": summary, "raw": raw}
    