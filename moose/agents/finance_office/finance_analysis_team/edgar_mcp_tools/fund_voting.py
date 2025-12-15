from __future__ import annotations

from typing import Any, Dict, List

from edgar import Company
from edgar._filings import get_by_accession_number
from edgar.entity.filings import EntityFilings

from .basic import EdgarMCPTools, mcp_envelope_err, mcp_envelope_ok, mcp_tool, pd


class FundVotingMCPTools(EdgarMCPTools):
    """
    Fund proxy voting (N-PX) tools.
    """

    @mcp_tool()
    def list_fund_voting_records_index(self, fund_cik: str, n: int = 2) -> Dict[str, Any]:
        """
        List a fund’s proxy voting record filings (Form N-PX / N-PX/A) and return an index as a JSON envelope.

        General description
        - Queries SEC filings for a fund (identified by CIK) and returns the latest N-PX filings with accession numbers.
        - This is an **index step** typically followed by fetching the detailed vote table.

        Use case (with example)
        - You want to analyze how a large fund voted on a set of shareholder proposals in the most recent N-PX filing.

        Parameters
        - fund_cik: Fund CIK (e.g., "0001166559"). Must be non-empty.
        - n: Number of latest N-PX filings to return (positive integer).

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.filings: list of {filing_date, accession_no, form, company}
          - recommended_next_tools: suggests `fetch_fund_proxy_votes`

        Concrete example (code)
        ```python
        list_fund_voting_records_index(fund_cik="0001166559", n=2)
        ```
        """
        meta = {"tool": "list_fund_voting_records_index", "fund_cik": fund_cik, "n": n}
        if not fund_cik:
            return mcp_envelope_err("fund_cik must be provided", meta=meta)
        try:
            c = Company(fund_cik)
            filings = c.get_filings(form=["N-PX", "N-PX/A"])
            if filings is None or filings.empty:
                return mcp_envelope_err(f"No voting record filings found for {fund_cik}.", meta=meta)
            latest = filings.latest(n)
            lst = list(latest) if isinstance(latest, EntityFilings) else [latest]
            out = [
                {
                    "filing_date": getattr(f, "filing_date", None),
                    "accession_no": getattr(f, "accession_no", None),
                    "form": getattr(f, "form", None),
                    "company": getattr(f, "company", None),
                }
                for f in lst
            ]
            rec = [
                {
                    "tool": "fetch_fund_proxy_votes",
                    "reason": "Fetch vote records as a table for analysis/search.",
                    "args_template": {"accession_no": "{data.filings[0].accession_no}"},
                }
            ] if out else []
            return mcp_envelope_ok(
                data={"filings": out},
                meta=meta,
                text_fallback=f"Found {len(out)} voting filings for {fund_cik}.",
                outputs=[{"name": "filings", "path": "data.filings", "description": "Voting record filings index"}],
                recommended_next_tools=rec,
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to list voting record filings: {e}", meta=meta)

    @mcp_tool()
    def fetch_fund_proxy_votes(self, accession_no: str, limit_votes: int = 1000) -> Dict[str, Any]:
        """
        Fetch and return proxy vote records from a specific N-PX filing (by accession number) as a JSON envelope.

        General description
        - Loads the filing referenced by `accession_no`, extracts proxy vote records, and returns them as a table (JSON-safe).
        - Useful for searching/filtering votes by issuer, meeting date, proposal text, vote cast, etc. (depending on edgartools fields).

        Use case (with example)
        - After selecting the latest N-PX filing from the index, you want the top 1,000 vote rows for analysis.

        Parameters
        - accession_no: SEC accession number for an N-PX/N-PX/A filing.
        - limit_votes: Maximum number of vote rows to return.

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.votes: vote rows (JSON-safe)
          - meta: tool inputs

        Concrete example (code)
        ```python
        fetch_fund_proxy_votes(accession_no="0001166559-24-000123", limit_votes=1000)
        ```
        """
        meta = {"tool": "fetch_fund_proxy_votes", "accession_no": accession_no, "limit_votes": limit_votes}
        if not accession_no:
            return mcp_envelope_err("accession_no must be provided", meta=meta)
        try:
            filing = get_by_accession_number(accession_no)
            if filing is None:
                return mcp_envelope_err(f"Filing not found for accession {accession_no}", meta=meta)
            obj = filing.obj()  # type: ignore[union-attr]
            votes = getattr(obj, "proxy_votes", None)
            df = votes.to_dataframe() if votes is not None else None
            if pd is not None and isinstance(df, pd.DataFrame):
                df = df.head(limit_votes)
            return mcp_envelope_ok(
                data={"accession_no": accession_no, "votes": df},
                meta=meta,
                text_fallback=f"Fetched proxy votes for {accession_no}.",
                outputs=[{"name": "votes", "path": "data.votes", "description": "Proxy vote rows"}],
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to fetch proxy votes: {e}", meta=meta)


