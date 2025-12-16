from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from edgar import Company
from edgar.entity.filings import EntityFiling, EntityFilings
from edgar.xbrl import XBRLS
from edgar.xbrl.statements import StitchedStatement

from .basic import EdgarMCPTools, filings_is_empty, mcp_envelope_err, mcp_envelope_ok, mcp_tool


class FinancialStatementsMCPTools(EdgarMCPTools):
    """
    SEC XBRL financial statements (income statement, balance sheet, cash flow).

    Use this category when you need **structured financial statement tables** sourced from SEC XBRL filings
    (10-Q/10-K). Tools support both latest single-filing views and stitched multi-period statements.
    """

    @mcp_tool()
    def get_income_statement(
        self,
        ticker: str,
        quarter_or_annual: Literal["quarter", "annual"],
        periods: int = 1,
        detail_level: Literal["standard", "detailed"] = "standard",
        start_quarter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Retrieve an SEC income statement for a public company (XBRL-based), returned as a JSON envelope.

        This tool can return either:
        - A **detailed** single-filing statement (latest 10-K or 10-Q), or
        - A **standard** stitched statement across multiple filings (useful for multi-period comparison).

        Use case
        - You want an income statement to analyze revenue/expense trends across recent quarters or years.

        Parameters
        - ticker: Stock ticker symbol (e.g., "NVDA").
        - quarter_or_annual: `"quarter"` for 10-Q, `"annual"` for 10-K.
        - periods: Number of filings/periods to stitch (only used in `detail_level="standard"` when `start_quarter` is not provided).
        - detail_level:
          - `"standard"`: stitch across multiple filings (controlled by `periods` or `start_quarter`)
          - `"detailed"`: most detailed statement from the latest single filing (ignores `periods` and `start_quarter`)
        - start_quarter: Optional `"YYYY-Qn"` (e.g., `"2024-Q1"`). When provided in standard mode, stitches from that quarter onward.

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data: { statement_type, mode, text }
          - meta: inputs + tool name
          - text_fallback: a human-readable table string

        Concrete example (code)
        - Standard stitched quarterly income statement:

        ```python
        get_income_statement(ticker="NVDA", quarter_or_annual="quarter", periods=4, detail_level="standard")
        ```
        """
        meta = {
            "tool": "get_income_statement",
            "ticker": ticker,
            "quarter_or_annual": quarter_or_annual,
            "periods": periods,
            "detail_level": detail_level,
            "start_quarter": start_quarter,
        }

        if quarter_or_annual not in ("quarter", "annual"):
            return mcp_envelope_err("quarter_or_annual must be 'quarter' or 'annual'", meta=meta)
        if detail_level not in ("standard", "detailed"):
            return mcp_envelope_err("detail_level must be 'standard' or 'detailed'", meta=meta)

        is_annual = quarter_or_annual == "annual"
        form_name = "10-K" if is_annual else "10-Q"
        company = Company(ticker)

        if detail_level == "detailed":
            try:
                financials = company.get_financials() if is_annual else company.get_quarterly_financials()
                if not financials:
                    return mcp_envelope_err(f"No recent {form_name} financials found for {ticker}.", meta=meta)
                stmt = financials.income_statement()
                if not stmt:
                    return mcp_envelope_err(f"No income statement found in the latest {form_name} filing for {ticker}.", meta=meta)
                text = str(stmt)
                return mcp_envelope_ok(
                    data={"statement_type": "IncomeStatement", "mode": "detailed_single_filing", "text": text},
                    meta=meta,
                    text_fallback=text,
                )
            except Exception as e:
                return mcp_envelope_err(f"Failed to load detailed income statement: {e}", meta=meta)

        if start_quarter is None:
            if not isinstance(periods, int) or periods < 1:
                return mcp_envelope_err("periods must be a positive integer", meta=meta)

        include_dimensions = False
        standardize_labels = True

        if start_quarter is not None:
            try:
                year_str, q_str = start_quarter.split("-")
                year = int(year_str)
                q = int(q_str.upper().replace("Q", ""))
                if q not in (1, 2, 3, 4):
                    return mcp_envelope_err("quarter must be 1, 2, 3, or 4", meta=meta)
            except Exception:
                return mcp_envelope_err("start_quarter must be in 'YYYY-Qn' format, e.g. '2024-Q1'", meta=meta)

            start_month = 1 + (q - 1) * 3
            start_date = date(year, start_month, 1).isoformat()
            filings = company.get_filings(
                form=form_name,
                is_xbrl=True,
                amendments=False,
                filing_date=f"{start_date}:",
                trigger_full_load=True,
            )
            if filings_is_empty(filings):
                return mcp_envelope_err(f"No {form_name} XBRL filings found for {ticker} starting from {start_quarter}.", meta=meta)
            max_periods = len(filings)
        else:
            filings = company.get_filings(
                form=form_name,
                is_xbrl=True,
                amendments=False,
                trigger_full_load=False,
            ).latest(periods)
            if filings_is_empty(filings):
                return mcp_envelope_err(f"No recent {form_name} XBRL filings found for {ticker}.", meta=meta)
            max_periods = periods

        filings_for_xbrls: EntityFilings | List[EntityFiling]
        if isinstance(filings, EntityFilings):
            filings_for_xbrls = filings
        elif isinstance(filings, EntityFiling):
            filings_for_xbrls = [filings]
        else:
            filings_for_xbrls = list(filings) if isinstance(filings, list) else [filings]  # type: ignore[arg-type]

        xbrls = XBRLS.from_filings(filings_for_xbrls, filter_amendments=False)
        stmt = StitchedStatement(
            xbrls,
            "IncomeStatement",
            max_periods=max_periods,
            standard=standardize_labels,
            use_optimal_periods=True,
            include_dimensions=include_dimensions,
        )
        try:
            text = str(stmt.render())
            return mcp_envelope_ok(
                data={"statement_type": "IncomeStatement", "mode": "standard_stitched", "text": text},
                meta=meta,
                text_fallback=text,
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to render stitched income statement: {e}", meta=meta)

    @mcp_tool()
    def get_cashflow_statement(
        self,
        ticker: str,
        quarter_or_annual: Literal["quarter", "annual"],
        periods: int = 1,
        detail_level: Literal["standard", "detailed"] = "standard",
        start_quarter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Retrieve an SEC cash flow statement for a public company (XBRL-based), returned as a JSON envelope.

        Use case
        - You want to validate whether a company is generating cash consistently (operating cash flow) across recent periods.

        Parameters
        - ticker: Stock ticker symbol (e.g., "AAPL").
        - quarter_or_annual: `"quarter"` for 10-Q, `"annual"` for 10-K.
        - periods: Number of filings/periods to stitch (standard mode only, unless `start_quarter` is provided).
        - detail_level:
          - `"standard"`: stitched across multiple filings
          - `"detailed"`: latest single-filing statement
        - start_quarter: Optional `"YYYY-Qn"` to stitch from a specific quarter.

        Return value
        - Dict JSON envelope with `data.text` containing a rendered statement table string.

        Concrete example (code)
        - Latest quarterly cash flow statement (detailed):

        ```python
        get_cashflow_statement(ticker="AAPL", quarter_or_annual="quarter", detail_level="detailed")
        ```
        """
        meta = {
            "tool": "get_cashflow_statement",
            "ticker": ticker,
            "quarter_or_annual": quarter_or_annual,
            "periods": periods,
            "detail_level": detail_level,
            "start_quarter": start_quarter,
        }

        if quarter_or_annual not in ("quarter", "annual"):
            return mcp_envelope_err("quarter_or_annual must be 'quarter' or 'annual'", meta=meta)
        if detail_level not in ("standard", "detailed"):
            return mcp_envelope_err("detail_level must be 'standard' or 'detailed'", meta=meta)

        is_annual = quarter_or_annual == "annual"
        form_name = "10-K" if is_annual else "10-Q"
        company = Company(ticker)

        if detail_level == "detailed":
            try:
                financials = company.get_financials() if is_annual else company.get_quarterly_financials()
                if not financials:
                    return mcp_envelope_err(f"No recent {form_name} financials found for {ticker}.", meta=meta)
                stmt = financials.cashflow_statement()
                if not stmt:
                    return mcp_envelope_err(f"No cash flow statement found in the latest {form_name} filing for {ticker}.", meta=meta)
                text = str(stmt)
                return mcp_envelope_ok(
                    data={"statement_type": "CashFlowStatement", "mode": "detailed_single_filing", "text": text},
                    meta=meta,
                    text_fallback=text,
                )
            except Exception as e:
                return mcp_envelope_err(f"Failed to load detailed cash flow statement: {e}", meta=meta)

        if start_quarter is None:
            if not isinstance(periods, int) or periods < 1:
                return mcp_envelope_err("periods must be a positive integer", meta=meta)

        include_dimensions = False
        standardize_labels = True

        if start_quarter is not None:
            try:
                year_str, q_str = start_quarter.split("-")
                year = int(year_str)
                q = int(q_str.upper().replace("Q", ""))
                if q not in (1, 2, 3, 4):
                    return mcp_envelope_err("quarter must be 1, 2, 3, or 4", meta=meta)
            except Exception:
                return mcp_envelope_err("start_quarter must be in 'YYYY-Qn' format, e.g. '2024-Q1'", meta=meta)

            start_month = 1 + (q - 1) * 3
            start_date = date(year, start_month, 1).isoformat()
            filings = company.get_filings(
                form=form_name,
                is_xbrl=True,
                amendments=False,
                filing_date=f"{start_date}:",
                trigger_full_load=True,
            )
            if filings_is_empty(filings):
                return mcp_envelope_err(f"No {form_name} XBRL filings found for {ticker} starting from {start_quarter}.", meta=meta)
            max_periods = len(filings)
        else:
            filings = company.get_filings(
                form=form_name,
                is_xbrl=True,
                amendments=False,
                trigger_full_load=False,
            ).latest(periods)
            if filings_is_empty(filings):
                return mcp_envelope_err(f"No recent {form_name} XBRL filings found for {ticker}.", meta=meta)
            max_periods = periods

        filings_for_xbrls: EntityFilings | List[EntityFiling]
        if isinstance(filings, EntityFilings):
            filings_for_xbrls = filings
        elif isinstance(filings, EntityFiling):
            filings_for_xbrls = [filings]
        else:
            filings_for_xbrls = list(filings) if isinstance(filings, list) else [filings]  # type: ignore[arg-type]

        xbrls = XBRLS.from_filings(filings_for_xbrls, filter_amendments=False)
        stmt = StitchedStatement(
            xbrls,
            "CashFlowStatement",
            max_periods=max_periods,
            standard=standardize_labels,
            use_optimal_periods=True,
            include_dimensions=include_dimensions,
        )
        try:
            text = str(stmt.render())
            return mcp_envelope_ok(
                data={"statement_type": "CashFlowStatement", "mode": "standard_stitched", "text": text},
                meta=meta,
                text_fallback=text,
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to render stitched cash flow statement: {e}", meta=meta)

    @mcp_tool()
    def get_balance_sheet(
        self,
        ticker: str,
        quarter_or_annual: Literal["quarter", "annual"],
        periods: int = 1,
        detail_level: Literal["standard", "detailed"] = "standard",
        start_quarter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Retrieve an SEC balance sheet for a public company (XBRL-based), returned as a JSON envelope.

        Use case
        - You want to quickly assess liquidity and leverage from the latest filing (cash, debt, equity).

        Parameters
        - ticker: Stock ticker symbol (e.g., "MSFT").
        - quarter_or_annual: `"quarter"` for 10-Q, `"annual"` for 10-K.
        - periods: Number of filings/periods to stitch (standard mode only).
        - detail_level:
          - `"standard"`: stitched across multiple filings
          - `"detailed"`: latest single-filing statement
        - start_quarter: Optional `"YYYY-Qn"` to stitch from a specific quarter.

        Return value
        - Dict JSON envelope with:
          - `data.text`: rendered balance sheet table string
          - `meta`: tool inputs

        Concrete example (code)
        - Standard stitched annual balance sheet (last 3 filings):

        ```python
        get_balance_sheet(ticker="MSFT", quarter_or_annual="annual", periods=3, detail_level="standard")
        ```
        """
        meta = {
            "tool": "get_balance_sheet",
            "ticker": ticker,
            "quarter_or_annual": quarter_or_annual,
            "periods": periods,
            "detail_level": detail_level,
            "start_quarter": start_quarter,
        }

        if quarter_or_annual not in ("quarter", "annual"):
            return mcp_envelope_err("quarter_or_annual must be 'quarter' or 'annual'", meta=meta)
        if detail_level not in ("standard", "detailed"):
            return mcp_envelope_err("detail_level must be 'standard' or 'detailed'", meta=meta)

        is_annual = quarter_or_annual == "annual"
        form_name = "10-K" if is_annual else "10-Q"
        company = Company(ticker)

        if detail_level == "detailed":
            try:
                financials = company.get_financials() if is_annual else company.get_quarterly_financials()
                if not financials:
                    return mcp_envelope_err(f"No recent {form_name} financials found for {ticker}.", meta=meta)
                stmt = financials.balance_sheet()
                if not stmt:
                    return mcp_envelope_err(f"No balance sheet found in the latest {form_name} filing for {ticker}.", meta=meta)
                text = str(stmt)
                return mcp_envelope_ok(
                    data={"statement_type": "BalanceSheet", "mode": "detailed_single_filing", "text": text},
                    meta=meta,
                    text_fallback=text,
                )
            except Exception as e:
                return mcp_envelope_err(f"Failed to load detailed balance sheet: {e}", meta=meta)

        if start_quarter is None:
            if not isinstance(periods, int) or periods < 1:
                return mcp_envelope_err("periods must be a positive integer", meta=meta)

        include_dimensions = False
        standardize_labels = True

        if start_quarter is not None:
            try:
                year_str, q_str = start_quarter.split("-")
                year = int(year_str)
                q = int(q_str.upper().replace("Q", ""))
                if q not in (1, 2, 3, 4):
                    return mcp_envelope_err("quarter must be 1, 2, 3, or 4", meta=meta)
            except Exception:
                return mcp_envelope_err("start_quarter must be in 'YYYY-Qn' format, e.g. '2024-Q1'", meta=meta)

            start_month = 1 + (q - 1) * 3
            start_date = date(year, start_month, 1).isoformat()
            filings = company.get_filings(
                form=form_name,
                is_xbrl=True,
                amendments=False,
                filing_date=f"{start_date}:",
                trigger_full_load=True,
            )
            if filings_is_empty(filings):
                return mcp_envelope_err(f"No {form_name} XBRL filings found for {ticker} starting from {start_quarter}.", meta=meta)
            max_periods = len(filings)
        else:
            filings = company.get_filings(
                form=form_name,
                is_xbrl=True,
                amendments=False,
                trigger_full_load=False,
            ).latest(periods)
            if filings_is_empty(filings):
                return mcp_envelope_err(f"No recent {form_name} XBRL filings found for {ticker}.", meta=meta)
            max_periods = periods

        filings_for_xbrls: EntityFilings | List[EntityFiling]
        if isinstance(filings, EntityFilings):
            filings_for_xbrls = filings
        elif isinstance(filings, EntityFiling):
            filings_for_xbrls = [filings]
        else:
            filings_for_xbrls = list(filings) if isinstance(filings, list) else [filings]  # type: ignore[arg-type]

        xbrls = XBRLS.from_filings(filings_for_xbrls, filter_amendments=False)
        stmt = StitchedStatement(
            xbrls,
            "BalanceSheet",
            max_periods=max_periods,
            standard=standardize_labels,
            use_optimal_periods=True,
            include_dimensions=include_dimensions,
        )
        try:
            text = str(stmt.render())
            return mcp_envelope_ok(
                data={"statement_type": "BalanceSheet", "mode": "standard_stitched", "text": text},
                meta=meta,
                text_fallback=text,
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to render stitched balance sheet: {e}", meta=meta)


