from __future__ import annotations

from typing import Any, Dict, List

from edgar import Company
from edgar._filings import get_by_accession_number
from edgar.entity.filings import EntityFilings

from .basic import EdgarMCPTools, mcp_envelope_err, mcp_envelope_ok, mcp_tool


class CompanyUpdatesMCPTools(EdgarMCPTools):
    """
    Company material updates (8-K) pipeline tools.
    """

    @mcp_tool()
    def list_company_material_updates_index(
        self,
        ticker: str,
        n: int = 5,
        include_exhibits: bool = True,
    ) -> Dict[str, Any]:
        """
        List a company’s most recent material updates (Form 8-K) and return an index of filings/exhibits as a JSON envelope.

        General description
        - Fetches the latest 8-K filings for `ticker` and returns a compact index you can feed into downstream tools.
        - When `include_exhibits=True`, the response includes exhibit metadata so you can pull the exact press release exhibit (often EX-99.*).

        Use case (with example)
        - You’re analyzing a breaking catalyst and want to quickly locate the most relevant 8-K exhibit text for NVIDIA:

        Parameters
        - ticker: Stock ticker (e.g., "NVDA").
        - n: Number of latest 8-K filings to return.
        - include_exhibits: Whether to include filing exhibits metadata (recommended if you want to fetch EX-99.*).

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.filings: list of filing index rows (with accession_no and optionally exhibits[].document)
          - meta: tool inputs + pipeline hints
          - text_fallback: short summary string

        Concrete example (code)
        ```python
        list_company_material_updates_index(ticker="NVDA", n=5, include_exhibits=True)
        ```

        Data Dependency:
        - This tool is typically the first step. Downstream tools should use:
          - `data.filings[].accession_no`
          - `data.filings[].exhibits[].document`

        Forms used:
        - Form 8-K (current report of significant corporate events).
        """
        env = self._list_company_material_updates(ticker=ticker, n=n, include_exhibits=include_exhibits)
        # ensure the meta.tool matches the public tool name
        try:
            if isinstance(env.get("meta"), dict):
                env["meta"]["tool"] = "list_company_material_updates_index"
        except Exception:
            pass

        # Attach pipeline metadata (Option B)
        if env.get("ok") and isinstance(env.get("data"), dict):
            outputs = [
                {
                    "name": "filings",
                    "path": "data.filings",
                    "description": "Material update index entries with accession_no and exhibits[].document",
                },
            ]
            dependencies: List[Dict[str, Any]] = []
            rec: List[Dict[str, Any]] = []

            filings = (env["data"] or {}).get("filings") or []
            for i, f in enumerate(filings[: min(n, 3)]):
                acc = f.get("accession_no")
                exhibits = f.get("exhibits") or []
                for j, ex in enumerate(exhibits):
                    doc = ex.get("document")
                    desc = (ex.get("description") or "").upper()
                    if not acc or not doc:
                        continue
                    if "99.1" in desc or "99.2" in desc or desc.startswith("EX-99"):
                        rec.append(
                            {
                                "tool": "fetch_exhibit_text_by_accession",
                                "reason": f"Exhibit {ex.get('description')} often contains the substantive release/commentary.",
                                "args_template": {
                                    "accession_no": "{data.filings[" + str(i) + "].accession_no}",
                                    "document_name": "{data.filings[" + str(i) + "].exhibits[" + str(j) + "].document}",
                                },
                            }
                        )

            try:
                if isinstance(env.get("meta"), dict):
                    env["meta"]["dependencies"] = dependencies
                    env["meta"]["outputs"] = outputs
                    env["meta"]["recommended_next_tools"] = rec
            except Exception:
                pass

        return env

    @mcp_tool()
    def fetch_filing_text_by_accession(self, accession_no: str) -> Dict[str, Any]:
        """
        Fetch the full text for a filing identified by accession number, returned as a JSON envelope.

        General description
        - Downloads the filing content for `accession_no` and returns `data.text` (best-effort).
        - Useful after any index tool that yields `accession_no`.

        Use case (with example)
        - You already found a relevant 8-K accession number and want the raw filing text for quick summarization.

        Parameters
        - accession_no: SEC accession number (e.g., "0001045810-24-000123").

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.accession_no: echoed accession number
          - data.text: filing text (may be large; `text_fallback` is truncated)
          - meta: tool inputs

        Concrete example (code)
        ```python
        fetch_filing_text_by_accession(accession_no="0001045810-24-000123")
        ```

        Data Dependency:
        - Call after an index tool that returns `accession_no` (e.g., `list_company_material_updates_index`).
        - Use field: `data.filings[].accession_no`

        Returns:
        - data.text: filing text (best-effort, may be large; truncated in text_fallback)
        """
        meta = {"tool": "fetch_filing_text_by_accession", "accession_no": accession_no}
        if not accession_no:
            return mcp_envelope_err("accession_no must be provided", meta=meta)
        try:
            filing = get_by_accession_number(accession_no)
            if filing is None:
                return mcp_envelope_err(f"Filing not found for accession {accession_no}", meta=meta)
            txt = getattr(filing, "text", None)
            if not isinstance(txt, str):
                try:
                    html = filing.html()
                    txt = str(html) if html is not None else ""
                except Exception:
                    txt = ""
            return mcp_envelope_ok(
                data={"accession_no": accession_no, "text": txt},
                meta=meta,
                text_fallback=(txt[:2000] if isinstance(txt, str) else None),
                dependencies=[
                    {
                        "after_tool": "list_company_material_updates_index",
                        "field_paths": ["data.filings[].accession_no"],
                        "notes": "Use accession_no from an index tool.",
                    }
                ],
                outputs=[{"name": "text", "path": "data.text", "description": "Raw filing text (may be large)"}],
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to fetch filing text: {e}", meta=meta)

    @mcp_tool()
    def fetch_exhibit_text_by_accession(self, accession_no: str, document_name: str) -> Dict[str, Any]:
        """
        Fetch a specific exhibit/document text for a filing (by accession number + document name), returned as a JSON envelope.

        General description
        - Locates the exhibit referenced by `document_name` inside the filing’s exhibits list and downloads its content.
        - This is the most direct way to retrieve press release text attached to an 8-K (often EX-99.*).

        Use case (with example)
        - After listing 8-Ks for a ticker, you want the press release exhibit text to extract key details.

        Parameters
        - accession_no: SEC accession number from an index tool result.
        - document_name: Exhibit document file name from `data.filings[].exhibits[].document`.

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.text: exhibit content decoded to text
          - meta: tool inputs + dependency hints

        Concrete example (code)
        ```python
        fetch_exhibit_text_by_accession(
            accession_no="0001045810-24-000123",
            document_name="ex99_1.htm",
        )
        ```

        Data Dependency:
        - Call after an index tool that provides:
          - `data.filings[].accession_no`
          - `data.filings[].exhibits[].document`
        - Recommended upstream: `list_company_material_updates_index()`.
        """
        meta = {"tool": "fetch_exhibit_text_by_accession", "accession_no": accession_no, "document_name": document_name}
        if not accession_no or not document_name:
            return mcp_envelope_err("accession_no and document_name are required", meta=meta)
        try:
            filing = get_by_accession_number(accession_no)
            if filing is None:
                return mcp_envelope_err(f"Filing not found for accession {accession_no}", meta=meta)
            exhibits = getattr(filing, "exhibits", None)
            if not exhibits:
                return mcp_envelope_err("No exhibits available on this filing.", meta=meta)
            target = None
            for ex in exhibits:
                if getattr(ex, "document", None) == document_name:
                    target = ex
                    break
            if target is None:
                return mcp_envelope_err(f"Exhibit document not found: {document_name}", meta=meta)
            content = target.download()
            if isinstance(content, bytes):
                text = content.decode("utf-8", errors="ignore")
            else:
                text = str(content)
            return mcp_envelope_ok(
                data={"accession_no": accession_no, "document_name": document_name, "text": text},
                meta=meta,
                text_fallback=text[:2000],
                dependencies=[
                    {
                        "after_tool": "list_company_material_updates_index",
                        "field_paths": ["data.filings[].accession_no", "data.filings[].exhibits[].document"],
                        "notes": "Use accession_no and exhibits[].document from an index tool.",
                    }
                ],
                outputs=[{"name": "text", "path": "data.text", "description": "Downloaded exhibit content (decoded text)"}],
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to fetch exhibit text: {e}", meta=meta)

    # ---- internal helpers ----
    def _list_company_material_updates(self, ticker: str, n: int = 5, include_exhibits: bool = True) -> Dict[str, Any]:
        meta = {
            "tool": "_list_company_material_updates",
            "ticker": ticker,
            "n": n,
            "include_exhibits": include_exhibits,
        }
        if not isinstance(n, int) or n < 1:
            return mcp_envelope_err("n must be a positive integer", meta=meta)

        try:
            company = Company(ticker)
            filings = company.get_filings(form="8-K")
            if filings is None or filings.empty:
                return mcp_envelope_err(f"No 8-K filings found for {ticker}.", meta=meta)

            latest = filings.latest(n)
            latest_list = list(latest) if isinstance(latest, EntityFilings) else [latest]

            out: List[Dict[str, Any]] = []
            for filing in latest_list:
                entry: Dict[str, Any] = {
                    "filing_date": getattr(filing, "filing_date", None),
                    "accession_no": getattr(filing, "accession_no", None),
                    "form": getattr(filing, "form", None),
                    "company": getattr(filing, "company", None),
                }
                if include_exhibits:
                    try:
                        exhibits = getattr(filing, "exhibits", None)
                        if exhibits is not None:
                            entry["exhibits"] = [
                                {
                                    "sequence": getattr(ex, "sequence", None),
                                    "description": getattr(ex, "description", None),
                                    "document": getattr(ex, "document", None),
                                    "type": getattr(ex, "type", None),
                                }
                                for ex in exhibits
                            ]
                    except Exception:
                        entry["exhibits"] = None

                try:
                    txt = filing.text  # type: ignore[attr-defined]
                    if isinstance(txt, str) and txt:
                        entry["text_preview"] = txt[:2000]
                except Exception:
                    entry["text_preview"] = None

                out.append(entry)

            text_fallback = f"Latest {len(out)} 8-K filings for {ticker}."
            return mcp_envelope_ok(data={"filings": out}, meta=meta, text_fallback=text_fallback)
        except Exception as e:
            return mcp_envelope_err(f"Failed to load 8-K filings: {e}", meta=meta)


