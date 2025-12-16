import os
from typing import Any, Dict, Optional

from .basic import FMPMCPTools, mcp_envelope_err, mcp_envelope_ok, mcp_tool


class EconomicsMCPTools(FMPMCPTools):
    """
    Macro/economics datasets (rates, indicators, risk premia) from FMP Stable.

    Use this category for **macro context**: treasury rates, named economic indicator series, and country-level
    market risk premium inputs.
    """

    def __init__(self, api_key: Optional[str] = None, logger=None):
        super().__init__(api_key, logger)

    def _validate_iso_date(self, value: str, field: str) -> Optional[dict]:
        v = str(value).strip()
        if len(v) != 10 or v[4] != "-" or v[7] != "-":
            return mcp_envelope_err(f"{field} must be in YYYY-MM-DD format", meta={"tool": "_validate_iso_date", "field": field})
        y, m, d = v.split("-")
        if not (y.isdigit() and m.isdigit() and d.isdigit()):
            return mcp_envelope_err(f"{field} must be in YYYY-MM-DD format", meta={"tool": "_validate_iso_date", "field": field})
        return None

    @mcp_tool()
    def get_treasury_rates(self, from_date: str, to_date: str) -> dict:
        """
        Retrieves US Treasury rates for a date range.

        Use case
        - You want risk-free rate inputs (by maturity) over a time window for discounting and macro context.

        Parameters
        - from_date: Start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: End date (`YYYY-MM-DD`) mapped to query parameter `to`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary

        Concrete example (code)

        ```python
        get_treasury_rates(from_date="2025-01-01", to_date="2025-01-31")
        ```

        Data source: FMP Stable Treasury Rates API
        - GET `https://financialmodelingprep.com/stable/treasury-rates?from=YYYY-MM-DD&to=YYYY-MM-DD`
        Reference: [`https://site.financialmodelingprep.com/developer/docs`](https://site.financialmodelingprep.com/developer/docs)
        """
        meta = {"tool": "get_treasury_rates", "from_date": from_date, "to_date": to_date}
        if not from_date:
            return mcp_envelope_err("from_date is required", meta=meta)
        if not to_date:
            return mcp_envelope_err("to_date is required", meta=meta)
        err = self._validate_iso_date(from_date, "from_date")
        if err:
            return err
        err = self._validate_iso_date(to_date, "to_date")
        if err:
            return err

        params: Dict[str, Any] = {"from": str(from_date).strip(), "to": str(to_date).strip()}
        raw = self._request_json("treasury-rates", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        tf = f"Treasury rates from {str(from_date).strip()} to {str(to_date).strip()}"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_economic_indicators(self, name: str, from_date: str, to_date: str) -> dict:
        """
        Retrieves an economic indicator time series by name for a date range.

        Use case
        - You want a macro indicator series (e.g., GDP, CPI, unemploymentRate) for research, modeling, or backtesting.

        Parameters
        - name: Indicator name. Allowed values:
          - GDP, realGDP, nominalPotentialGDP, realGDPPerCapita, federalFunds, CPI, inflationRate, inflation,
            retailSales, consumerSentiment, durableGoods, unemploymentRate, totalNonfarmPayroll, initialClaims,
            industrialProductionTotalIndex, newPrivatelyOwnedHousingUnitsStartedTotalUnits, totalVehicleSales,
            retailMoneyFunds, smoothedUSRecessionProbabilities,
            3MonthOr90DayRatesAndYieldsCertificatesOfDeposit, commercialBankInterestRateOnCreditCardPlansAllAccounts,
            30YearFixedRateMortgageAverage, 15YearFixedRateMortgageAverage, tradeBalanceGoodsAndServices
        - from_date: Start date (`YYYY-MM-DD`) mapped to query parameter `from`.
        - to_date: End date (`YYYY-MM-DD`) mapped to query parameter `to`.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary

        Concrete example (code)

        ```python
        get_economic_indicators(name="CPI", from_date="2020-01-01", to_date="2025-01-01")
        ```

        Data source: FMP Stable Economics Indicators API
        - GET `https://financialmodelingprep.com/stable/economic-indicators?name=...&from=YYYY-MM-DD&to=YYYY-MM-DD`
        Reference: [`https://site.financialmodelingprep.com/developer/docs`](https://site.financialmodelingprep.com/developer/docs)
        """
        meta = {"tool": "get_economic_indicators", "name": name, "from_date": from_date, "to_date": to_date}
        if not name:
            return mcp_envelope_err("name is required", meta=meta)
        if not from_date:
            return mcp_envelope_err("from_date is required", meta=meta)
        if not to_date:
            return mcp_envelope_err("to_date is required", meta=meta)

        allowed = {
            "GDP",
            "realGDP",
            "nominalPotentialGDP",
            "realGDPPerCapita",
            "federalFunds",
            "CPI",
            "inflationRate",
            "inflation",
            "retailSales",
            "consumerSentiment",
            "durableGoods",
            "unemploymentRate",
            "totalNonfarmPayroll",
            "initialClaims",
            "industrialProductionTotalIndex",
            "newPrivatelyOwnedHousingUnitsStartedTotalUnits",
            "totalVehicleSales",
            "retailMoneyFunds",
            "smoothedUSRecessionProbabilities",
            "3MonthOr90DayRatesAndYieldsCertificatesOfDeposit",
            "commercialBankInterestRateOnCreditCardPlansAllAccounts",
            "30YearFixedRateMortgageAverage",
            "15YearFixedRateMortgageAverage",
            "tradeBalanceGoodsAndServices",
        }
        nm = str(name).strip()
        if nm not in allowed:
            return mcp_envelope_err(
                "name must be one of the documented indicator names",
                meta={**meta, "allowed_names": sorted(allowed)},
            )

        err = self._validate_iso_date(from_date, "from_date")
        if err:
            return err
        err = self._validate_iso_date(to_date, "to_date")
        if err:
            return err

        params: Dict[str, Any] = {"name": nm, "from": str(from_date).strip(), "to": str(to_date).strip()}
        raw = self._request_json("economic-indicators", params=params)
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        tf = f"Economic indicator {nm} from {str(from_date).strip()} to {str(to_date).strip()}"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)

    @mcp_tool()
    def get_market_risk_premium(self) -> dict:
        """
        Retrieves market risk premium data by country.

        Use case
        - You want equity risk premium inputs (total and country risk premium) for discount rates and cost-of-equity modeling.

        Parameters
        - None.

        Return value
        - MCP JSON envelope:
          - ok: bool
          - data: raw JSON list/dict from the endpoint
          - meta: tool inputs (and optional error context)
          - text_fallback: short human-readable summary

        Concrete example (code)

        ```python
        get_market_risk_premium()
        ```

        Data source: FMP Stable Market Risk Premium API
        - GET `https://financialmodelingprep.com/stable/market-risk-premium`
        Reference: [FMP stable market-risk-premium endpoint](https://financialmodelingprep.com/stable/market-risk-premium?apikey=ixF8ja8u0jxXUkWEGL58R9OGXH9MySq3)
        """
        meta = {"tool": "get_market_risk_premium"}
        raw = self._request_json("market-risk-premium", params={})
        if isinstance(raw, dict) and "error" in raw:
            details = raw.get("details") if isinstance(raw.get("details"), dict) else None
            return mcp_envelope_err(
                str(raw.get("error") or "Failed to fetch data from FinancialModelingPrep."),
                meta={**meta, **({"fmp_error_details": details} if details else {})},
                text_fallback=str(raw.get("error") or "FMP request failed."),
            )

        tf = "Market risk premium by country"
        if isinstance(raw, list):
            tf += f" ({len(raw)} records)"
        return mcp_envelope_ok(data=raw, meta=meta, text_fallback=tf)


if __name__ == "__main__":
    tools = EconomicsMCPTools(api_key=os.getenv("FMP_API_KEY"))
    print(tools.get_treasury_rates(from_date="2025-01-01", to_date="2025-01-31"))
    print(tools.get_economic_indicators(name="CPI", from_date="2020-01-01", to_date="2025-01-01"))
    print(tools.get_market_risk_premium())


