from __future__ import annotations

import re
from typing import Any, Dict, List

from edgar import Company
from edgar._filings import get_by_accession_number

from .basic import EdgarMCPTools, _since_date_range, mcp_envelope_err, mcp_envelope_ok, mcp_tool


class FinancingMCPTools(EdgarMCPTools):
    """
    Financing / dilution monitoring tools (registration/prospectus filings).
    """

    @mcp_tool()
    def list_financing_documents_index(self, ticker: str, since_days: int = 365, max_filings: int = 50) -> Dict[str, Any]:
        """
        List recent financing-related registration/prospectus filings for a company and return an index as a JSON envelope.

        General description
        - Searches for common financing/dilution forms (e.g., S-1/S-3, 424B*, F-* variants) for `ticker`.
        - Returns a compact index of filings with accession numbers that can be used for deeper parsing.

        Use case (with example)
        - You’re evaluating dilution risk and want to quickly see if a company filed an S-3 shelf or prospectus supplement recently.

        Parameters
        - ticker: Stock ticker (e.g., "NVDA").
        - since_days: Lookback window in days (non-negative integer).
        - max_filings: Max number of filings to return.

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.filings: list of {filing_date, accession_no, form, company}
          - recommended_next_tools: typically suggests `fetch_financing_terms_from_filing`

        Concrete example (code)
        ```python
        list_financing_documents_index(ticker="NVDA", since_days=365, max_filings=25)
        ```
        """
        meta = {"tool": "list_financing_documents_index", "ticker": ticker, "since_days": since_days, "max_filings": max_filings}
        if not ticker:
            return mcp_envelope_err("ticker must be provided", meta=meta)
        if not isinstance(since_days, int) or since_days < 0:
            return mcp_envelope_err("since_days must be a non-negative integer", meta=meta)
        try:
            forms = ["S-1", "S-3", "S-4", "F-1", "F-3", "F-4", "424B1", "424B2", "424B3", "424B4", "424B5"]
            c = Company(ticker)
            filings = c.get_filings(form=forms, filing_date=_since_date_range(since_days))
            if filings is None or filings.empty:
                return mcp_envelope_err(f"No financing filings found for {ticker} in last {since_days} days.", meta=meta)
            out = [
                {"filing_date": getattr(f, "filing_date", None), "accession_no": getattr(f, "accession_no", None), "form": getattr(f, "form", None), "company": getattr(f, "company", None)}
                for f in filings.head(max_filings)
            ]
            rec = [
                {"tool": "fetch_financing_terms_from_filing", "reason": "Extract key financing/ATM/shelf terms from a filing.", "args_template": {"accession_no": "{data.filings[0].accession_no}"}}
            ] if out else []
            return mcp_envelope_ok(
                data={"filings": out},
                meta=meta,
                text_fallback=f"Found {len(out)} financing filings for {ticker}.",
                outputs=[{"name": "filings", "path": "data.filings", "description": "Financing filings index"}],
                recommended_next_tools=rec,
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to list financing filings: {e}", meta=meta)

    @mcp_tool()
    def fetch_financing_terms_from_filing(self, accession_no: str, max_snippets: int = 10) -> Dict[str, Any]:
        """
        Extract likely financing/dilution term snippets from a filing (by accession number), returned as a JSON envelope.

        General description
        - Downloads the filing text and searches for common financing terms (ATM, shelf, proceeds, sales agreement, etc.).
        - Returns a small list of matched text snippets to help you quickly identify offering structure.

        Use case (with example)
        - After spotting a 424B5 filing in the index, you want to quickly find the “ATM” / “gross proceeds” language.

        Parameters
        - accession_no: SEC accession number from `list_financing_documents_index`.
        - max_snippets: Max number of matched snippets to return.

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.snippets: list of matched context strings
          - text_fallback: first snippet (or “No matching terms found.”)

        Concrete example (code)
        ```python
        fetch_financing_terms_from_filing(accession_no="0000950123-24-012345", max_snippets=10)
        ```
        """
        meta = {"tool": "fetch_financing_terms_from_filing", "accession_no": accession_no, "max_snippets": max_snippets}
        if not accession_no:
            return mcp_envelope_err("accession_no must be provided", meta=meta)
        try:
            filing = get_by_accession_number(accession_no)
            if filing is None:
                return mcp_envelope_err(f"Filing not found for accession {accession_no}", meta=meta)
            txt = getattr(filing, "text", None)
            if not isinstance(txt, str) or not txt:
                try:
                    html = filing.html()
                    txt = str(html) if html is not None else ""
                except Exception:
                    txt = ""
            patterns = [
                r"at\s+the\s+market",
                r"\bATM\b",
                r"shelf",
                r"prospectus\s+supplement",
                r"gross\s+proceeds",
                r"net\s+proceeds",
                r"sales\s+agreement",
                r"offering\s+price",
            ]
            pat_re = re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)
            snippets: List[str] = []
            for m in pat_re.finditer(txt):
                start = max(0, m.start() - 180)
                end = min(len(txt), m.end() + 180)
                snippets.append(txt[start:end].replace("\n", " "))
                if len(snippets) >= max_snippets:
                    break
            return mcp_envelope_ok(
                data={"accession_no": accession_no, "snippets": snippets},
                meta=meta,
                text_fallback=(snippets[0] if snippets else "No matching terms found."),
                outputs=[{"name": "snippets", "path": "data.snippets", "description": "Matched financing term snippets"}],
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to extract financing terms: {e}", meta=meta)


