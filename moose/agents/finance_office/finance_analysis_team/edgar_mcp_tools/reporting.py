from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from edgar import Company, get_filings
from edgar.entity.filings import EntityFilings

from .basic import (
    EdgarMCPTools,
    _diff_highlights,
    _extract_section_by_markers,
    _since_date_range,
    filings_is_empty,
    mcp_envelope_err,
    mcp_envelope_ok,
    mcp_json_safe,
    mcp_tool,
    pd,
)


class CompanyReportingMCPTools(EdgarMCPTools):
    """
    Company reporting / governance / ownership-change tools from SEC filings.

    Forms and topics covered include (best-effort): 10-K, 10-Q, 6-K, DEF 14A, Schedule 13D/13G, NT 10-K/10-Q,
    and Reg CF (Form C).

    Use this category when you need qualitative/forensic signals from filings: compare sections (Risk Factors, MD&A),
    screen common reporting red flags, summarize proxy comp/governance, track major-holder changes and intent, and
    detect late-reporting notices.
    """

    # ---- major holders (13D/13G) ----
    def get_major_holder_changes(
        self,
        ticker: str,
        since_days: int = 365,
        include_intent: bool = True,
        limit_filings: int = 50,
    ) -> Dict[str, Any]:
        meta = {
            "tool": "get_major_holder_changes",
            "ticker": ticker,
            "since_days": since_days,
            "include_intent": include_intent,
            "limit_filings": limit_filings,
        }
        if not isinstance(since_days, int) or since_days < 0:
            return mcp_envelope_err("since_days must be a non-negative integer", meta=meta)

        forms = ["SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"]

        try:
            company = Company(ticker)
            filings = company.get_filings(form=forms, filing_date=_since_date_range(since_days))
            if filings_is_empty(filings):
                return mcp_envelope_err(f"No 13D/13G filings found for {ticker} in the last {since_days} days.", meta=meta)

            out: List[Dict[str, Any]] = []
            processed = 0
            for filing in filings.head(limit_filings):
                processed += 1
                entry: Dict[str, Any] = {
                    "filing_date": getattr(filing, "filing_date", None),
                    "accession_no": getattr(filing, "accession_no", None),
                    "form": getattr(filing, "form", None),
                    "company": getattr(filing, "company", None),
                }
                try:
                    obj = filing.obj()
                    entry["issuer_info"] = mcp_json_safe(getattr(obj, "issuer_info", None))
                    entry["security_info"] = mcp_json_safe(getattr(obj, "security_info", None))
                    entry["reporting_persons"] = mcp_json_safe(getattr(obj, "reporting_persons", None))
                    entry["date_of_event"] = mcp_json_safe(getattr(obj, "date_of_event", None))

                    if include_intent:
                        items = getattr(obj, "items", None)
                        intent = getattr(items, "item4_purpose_of_transaction", None) if items is not None else None
                        if intent:
                            entry["purpose_of_transaction"] = str(intent)
                except Exception as e:
                    entry["error"] = f"Failed to parse Schedule 13D/13G object: {e}"

                out.append(entry)

            text_fallback = f"Found {len(out)} 13D/13G filings for {ticker} in last {since_days} days."
            return mcp_envelope_ok(
                data={"filings": out, "summary": {"filings_processed": processed}},
                meta=meta,
                text_fallback=text_fallback,
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to load 13D/13G filings: {e}", meta=meta)

    @mcp_tool()
    def track_major_holder_changes(
        self,
        ticker: str,
        since_days: int = 365,
        include_intent: bool = True,
        limit_filings: int = 50,
    ) -> Dict[str, Any]:
        """
        Track major holder changes (Schedule 13D/13G filings) for a company and return the parsed index as a JSON envelope.

        General description
        - This tool wraps `get_major_holder_changes(...)` and normalizes `meta.tool` to the public tool name.
        - It returns a list of recent 13D/13G filings and (optionally) the “purpose of transaction” intent excerpt when available.

        Use case
        - You want to see whether a notable holder filed a 13D/13G update recently and scan for “purpose of transaction” intent language.

        Parameters
        - ticker: Stock ticker (e.g., "NVDA").
        - since_days: Lookback window in days (non-negative integer).
        - include_intent: Whether to attempt to include Item 4 “purpose of transaction” excerpts.
        - limit_filings: Max number of filings to process.

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.filings: list of parsed 13D/13G entries
          - meta: tool inputs

        Concrete example (code)
        ```python
        track_major_holder_changes(ticker="NVDA", since_days=365, include_intent=True, limit_filings=50)
        ```
        """
        env = self.get_major_holder_changes(ticker=ticker, since_days=since_days, include_intent=include_intent, limit_filings=limit_filings)
        try:
            if isinstance(env.get("meta"), dict):
                env["meta"]["tool"] = "track_major_holder_changes"
        except Exception:
            pass
        return env

    @mcp_tool()
    def summarize_major_holder_intent(
        self,
        ticker: str,
        latest_only: bool = True,
        since_days: int = 3650,
    ) -> Dict[str, Any]:
        """
        Summarize “purpose of transaction” intent language from recent Schedule 13D/13G filings for a company.

        General description
        - Calls `track_major_holder_changes(...)` with `include_intent=True` and filters to filings that contain an intent excerpt.
        - Intended to quickly surface activist/strategic intent language for qualitative analysis.

        Use case
        - You want a quick view of the latest Item 4 “purpose of transaction” intent excerpt to investigate potential activism.

        Parameters
        - ticker: Stock ticker (e.g., "AAPL").
        - latest_only: If True, return only the most recent filing that contains intent text.
        - since_days: Lookback window in days (non-negative integer).

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.intent_filings: list of filings with `purpose_of_transaction`
          - data.count: number of intent filings returned
          - meta: tool inputs

        Concrete example (code)
        ```python
        summarize_major_holder_intent(ticker="AAPL", latest_only=True, since_days=3650)
        ```
        """
        meta = {"tool": "summarize_major_holder_intent", "ticker": ticker, "latest_only": latest_only, "since_days": since_days}
        if not isinstance(since_days, int) or since_days < 0:
            return mcp_envelope_err("since_days must be a non-negative integer", meta=meta)

        try:
            base = self.track_major_holder_changes(ticker=ticker, since_days=since_days, include_intent=True, limit_filings=200)
            if not base.get("ok"):
                try:
                    if isinstance(base.get("meta"), dict):
                        base["meta"]["tool"] = "summarize_major_holder_intent"
                except Exception:
                    pass
                return base

            filings = (base.get("data") or {}).get("filings") or []
            intent_filings = []
            for f in filings:
                if f.get("purpose_of_transaction"):
                    intent_filings.append(
                        {
                            "filing_date": f.get("filing_date"),
                            "accession_no": f.get("accession_no"),
                            "form": f.get("form"),
                            "reporting_persons": f.get("reporting_persons"),
                            "issuer_info": f.get("issuer_info"),
                            "purpose_of_transaction": f.get("purpose_of_transaction"),
                        }
                    )

            if latest_only and intent_filings:
                intent_filings = intent_filings[:1]

            text_fallback = (
                f"Found {len(intent_filings)} major-holder intent filings for {ticker}."
                + (f" Latest intent excerpt: {str(intent_filings[0].get('purpose_of_transaction'))[:400]}" if intent_filings else "")
            )
            return mcp_envelope_ok(data={"intent_filings": intent_filings, "count": len(intent_filings)}, meta=meta, text_fallback=text_fallback)
        except Exception as e:
            return mcp_envelope_err(f"Failed to summarize major holder intent: {e}", meta=meta)

    # ---- text diff tools (10-K/10-Q) ----
    @mcp_tool()
    def compare_risk_factors(
        self,
        ticker: str,
        from_filing_date: str,
        to_filing_date: str,
        annual_or_quarter: Literal["annual", "quarter"] = "annual",
    ) -> Dict[str, Any]:
        """
        Compare the “Risk Factors” section between two SEC filings and return a lightweight diff as a JSON envelope.

        General description
        - Loads two filings (by filing date range) and extracts the Risk Factors section (Item 1A) using marker-based parsing.
        - Produces a lightweight diff summary including unified diff lines and example added/removed snippets.

        Use case
        - You want to see what changed in a company’s risk factors between two filings to detect newly disclosed risks.

        Parameters
        - ticker: Stock ticker (e.g., "NVDA").
        - from_filing_date: Start filing date (YYYY-MM-DD) used to locate the “before” filing.
        - to_filing_date: Start filing date (YYYY-MM-DD) used to locate the “after” filing.
        - annual_or_quarter: `"annual"` for 10-K or `"quarter"` for 10-Q (controls form type and section markers).

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.before / data.after: extracted section text
          - data.diff: {unified_diff, added_snippets, removed_snippets}
          - meta: tool inputs

        Concrete example (code)
        ```python
        compare_risk_factors(
            ticker="NVDA",
            from_filing_date="2023-02-01",
            to_filing_date="2024-02-01",
            annual_or_quarter="annual",
        )
        ```
        """
        meta = {
            "tool": "compare_risk_factors",
            "ticker": ticker,
            "from_filing_date": from_filing_date,
            "to_filing_date": to_filing_date,
            "annual_or_quarter": annual_or_quarter,
        }
        if annual_or_quarter not in ("annual", "quarter"):
            return mcp_envelope_err("annual_or_quarter must be 'annual' or 'quarter'", meta=meta)

        form = "10-K" if annual_or_quarter == "annual" else "10-Q"
        try:
            c = Company(ticker)
            f_from = c.get_filings(form=form, filing_date=f"{from_filing_date}:").latest()
            f_to = c.get_filings(form=form, filing_date=f"{to_filing_date}:").latest()

            before_text = getattr(f_from, "text", None)  # type: ignore[attr-defined]
            after_text = getattr(f_to, "text", None)  # type: ignore[attr-defined]
            if not isinstance(before_text, str) or not isinstance(after_text, str):
                return mcp_envelope_err("Unable to load filing text for comparison.", meta=meta)

            before_section = _extract_section_by_markers(
                before_text,
                start_markers=["ITEM 1A", "RISK FACTORS"],
                end_markers=["ITEM 1B", "ITEM 2", "UNRESOLVED STAFF COMMENTS"],
            )
            after_section = _extract_section_by_markers(
                after_text,
                start_markers=["ITEM 1A", "RISK FACTORS"],
                end_markers=["ITEM 1B", "ITEM 2", "UNRESOLVED STAFF COMMENTS"],
            )
            if not before_section or not after_section:
                return mcp_envelope_err("Failed to extract Risk Factors section from one or both filings.", meta=meta)

            diff = _diff_highlights(before_section, after_section)
            text_fallback = (
                "RISK FACTORS DIFF (truncated)\n\n"
                + "ADDED (examples):\n- "
                + "\n- ".join(diff["added_snippets"][:5])
                + "\n\nREMOVED (examples):\n- "
                + "\n- ".join(diff["removed_snippets"][:5])
            )
            return mcp_envelope_ok(
                data={
                    "form_used": form,
                    "before_filing": {"filing_date": getattr(f_from, "filing_date", None), "accession_no": getattr(f_from, "accession_no", None)},
                    "after_filing": {"filing_date": getattr(f_to, "filing_date", None), "accession_no": getattr(f_to, "accession_no", None)},
                    "before": before_section,
                    "after": after_section,
                    "diff": diff,
                },
                meta=meta,
                text_fallback=text_fallback,
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to compare risk factors: {e}", meta=meta)

    @mcp_tool()
    def compare_management_discussion(
        self,
        ticker: str,
        annual_or_quarter: Literal["annual", "quarter"] = "quarter",
        periods: int = 2,
    ) -> Dict[str, Any]:
        """
        Compare the MD&A section between the two most recent filings and return a lightweight diff as a JSON envelope.

        General description
        - Fetches the latest `periods` filings for the chosen form type (10-Q or 10-K), then compares the newest vs the prior one.
        - Extracts MD&A using marker-based parsing and produces a diff summary (added/removed snippets).

        Use case
        - You want to see what management emphasized differently between two recent reporting periods.

        Parameters
        - ticker: Stock ticker (e.g., "MSFT").
        - annual_or_quarter: `"quarter"` for 10-Q or `"annual"` for 10-K.
        - periods: Number of filings to load (must be >= 2; the tool compares the newest two).

        Return value
        - Dict JSON envelope containing `data.diff` plus `data.older`/`data.newer` extracted sections.

        Concrete example (code)
        ```python
        compare_management_discussion(ticker="MSFT", annual_or_quarter="quarter", periods=2)
        ```
        """
        meta = {"tool": "compare_management_discussion", "ticker": ticker, "annual_or_quarter": annual_or_quarter, "periods": periods}
        if annual_or_quarter not in ("annual", "quarter"):
            return mcp_envelope_err("annual_or_quarter must be 'annual' or 'quarter'", meta=meta)
        if not isinstance(periods, int) or periods < 2:
            return mcp_envelope_err("periods must be an integer >= 2", meta=meta)

        form = "10-K" if annual_or_quarter == "annual" else "10-Q"
        try:
            c = Company(ticker)
            filings = c.get_filings(form=form).latest(periods)
            filings_list = list(filings) if isinstance(filings, EntityFilings) else [filings]
            if len(filings_list) < 2:
                return mcp_envelope_err(f"Need at least 2 {form} filings to compare.", meta=meta)

            f_new, f_old = filings_list[0], filings_list[1]
            t_new = getattr(f_new, "text", None)  # type: ignore[attr-defined]
            t_old = getattr(f_old, "text", None)  # type: ignore[attr-defined]
            if not isinstance(t_new, str) or not isinstance(t_old, str):
                return mcp_envelope_err("Unable to load filing text for MD&A comparison.", meta=meta)

            if form == "10-Q":
                start = ["ITEM 2", "MANAGEMENT'S DISCUSSION", "MANAGEMENT’S DISCUSSION"]
                end = ["ITEM 3", "ITEM 4", "QUANTITATIVE AND QUALITATIVE DISCLOSURES"]
            else:
                start = ["ITEM 7", "MANAGEMENT'S DISCUSSION", "MANAGEMENT’S DISCUSSION"]
                end = ["ITEM 7A", "ITEM 8", "QUANTITATIVE AND QUALITATIVE DISCLOSURES"]

            new_sec = _extract_section_by_markers(t_new, start_markers=start, end_markers=end)
            old_sec = _extract_section_by_markers(t_old, start_markers=start, end_markers=end)
            if not new_sec or not old_sec:
                return mcp_envelope_err("Failed to extract MD&A section from one or both filings.", meta=meta)

            diff = _diff_highlights(old_sec, new_sec)
            text_fallback = (
                "MD&A DIFF (truncated)\n\n"
                + "ADDED (examples):\n- "
                + "\n- ".join(diff["added_snippets"][:5])
                + "\n\nREMOVED (examples):\n- "
                + "\n- ".join(diff["removed_snippets"][:5])
            )
            return mcp_envelope_ok(
                data={
                    "form_used": form,
                    "newer_filing": {"filing_date": getattr(f_new, "filing_date", None), "accession_no": getattr(f_new, "accession_no", None)},
                    "older_filing": {"filing_date": getattr(f_old, "filing_date", None), "accession_no": getattr(f_old, "accession_no", None)},
                    "older": old_sec,
                    "newer": new_sec,
                    "diff": diff,
                },
                meta=meta,
                text_fallback=text_fallback,
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to compare management discussion: {e}", meta=meta)

    @mcp_tool()
    def screen_reporting_red_flags(
        self,
        ticker: str,
        annual_or_quarter: Literal["annual", "quarter"] = "quarter",
        lookback_filings: int = 4,
        max_snippets_per_flag: int = 5,
    ) -> Dict[str, Any]:
        """
        Screen recent filings for common reporting red flags (going concern, restatement, ICFR weakness, covenant/default, auditor change).

        General description
        - Scans the full text of the most recent filings (10-Q or 10-K) for keyword patterns associated with common accounting/reporting risks.
        - Returns evidence snippets per flag category (best-effort; keyword-based, not a definitive determination).

        Use case
        - You want a fast automated screen for “restatement” or “material weakness” language across recent filings.

        Parameters
        - ticker: Stock ticker (e.g., "AAPL").
        - annual_or_quarter: `"quarter"` or `"annual"` to choose 10-Q vs 10-K.
        - lookback_filings: Number of recent filings to scan.
        - max_snippets_per_flag: Max evidence snippets to store per flag category.

        Return value
        - Dict JSON envelope:
          - data.triggered_flags: list of triggered categories
          - data.evidence: per-category evidence snippets with accession_no and filing_date

        Concrete example (code)
        ```python
        screen_reporting_red_flags(ticker="AAPL", annual_or_quarter="quarter", lookback_filings=4, max_snippets_per_flag=5)
        ```
        """
        meta = {
            "tool": "screen_reporting_red_flags",
            "ticker": ticker,
            "annual_or_quarter": annual_or_quarter,
            "lookback_filings": lookback_filings,
            "max_snippets_per_flag": max_snippets_per_flag,
        }
        if annual_or_quarter not in ("annual", "quarter"):
            return mcp_envelope_err("annual_or_quarter must be 'annual' or 'quarter'", meta=meta)
        if not isinstance(lookback_filings, int) or lookback_filings < 1:
            return mcp_envelope_err("lookback_filings must be a positive integer", meta=meta)

        form = "10-K" if annual_or_quarter == "annual" else "10-Q"
        flags: Dict[str, List[str]] = {
            "going_concern": [r"substantial doubt", r"going concern"],
            "restatement": [r"restatement", r"restate", r"revision to", r"previously issued"],
            "material_weakness": [r"material weakness", r"internal control", r"icfr", r"sox 404"],
            "liquidity_covenant_default": [r"covenant", r"default", r"liquidity", r"breach", r"waiver"],
            "auditor_change": [r"resigned", r"dismissed", r"independent registered public accounting firm"],
        }

        try:
            c = Company(ticker)
            filings = c.get_filings(form=form).latest(lookback_filings)
            filings_list = list(filings) if isinstance(filings, EntityFilings) else [filings]

            evidence: Dict[str, List[Dict[str, Any]]] = {k: [] for k in flags.keys()}
            scanned = 0

            for f in filings_list:
                scanned += 1
                try:
                    txt = getattr(f, "text", None)  # type: ignore[attr-defined]
                except Exception:
                    txt = None
                if not isinstance(txt, str) or not txt:
                    continue

                for flag, pats in flags.items():
                    if len(evidence[flag]) >= max_snippets_per_flag:
                        continue
                    pat_re = re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE)
                    m = pat_re.search(txt)
                    if not m:
                        continue
                    start = max(0, m.start() - 200)
                    end = min(len(txt), m.end() + 200)
                    snippet = txt[start:end].replace("\n", " ")
                    evidence[flag].append(
                        {
                            "filing_date": getattr(f, "filing_date", None),
                            "accession_no": getattr(f, "accession_no", None),
                            "snippet": snippet,
                        }
                    )

            triggered = [k for k, v in evidence.items() if v]
            score = len(triggered)
            text_fallback = f"Red-flag scan for {ticker}: {score} categories triggered: {', '.join(triggered) if triggered else 'none'}."

            return mcp_envelope_ok(
                data={
                    "form_used": form,
                    "scanned_filings": scanned,
                    "triggered_flags": triggered,
                    "flag_count": score,
                    "evidence": evidence,
                },
                meta=meta,
                text_fallback=text_fallback,
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to screen red flags: {e}", meta=meta)

    # ---- foreign issuer updates (6-K) ----
    @mcp_tool()
    def summarize_foreign_issuer_updates(
        self,
        ticker: str,
        n: int = 5,
        since_days: Optional[int] = None,
        include_exhibits: bool = True,
    ) -> Dict[str, Any]:
        """
        Summarize recent foreign issuer updates (Form 6-K) and return a compact index as a JSON envelope.

        General description
        - Retrieves 6-K filings for `ticker` either by latest `n` or within `since_days`, and returns metadata plus optional exhibit info.
        - Includes an optional `text_preview` to quickly triage filings without downloading full exhibits.

        Use case
        - You want to quickly see the latest 6-K updates for a foreign issuer and any attached exhibit documents.

        Parameters
        - ticker: Stock ticker (e.g., "TSM").
        - n: Number of filings to return.
        - since_days: Optional lookback window; if provided, returns up to `n` filings from that range.
        - include_exhibits: Whether to include exhibits metadata for each 6-K.

        Return value
        - Dict JSON envelope with `data.filings` containing accession_no, filing_date, and optional exhibits/text_preview.

        Concrete example (code)
        ```python
        summarize_foreign_issuer_updates(ticker="TSM", n=5, since_days=90, include_exhibits=True)
        ```
        """
        meta = {"tool": "summarize_foreign_issuer_updates", "ticker": ticker, "n": n, "since_days": since_days, "include_exhibits": include_exhibits}
        if not isinstance(n, int) or n < 1:
            return mcp_envelope_err("n must be a positive integer", meta=meta)
        if since_days is not None and (not isinstance(since_days, int) or since_days < 0):
            return mcp_envelope_err("since_days must be a non-negative integer", meta=meta)

        try:
            company = Company(ticker)
            if since_days is None:
                filings = company.get_filings(form="6-K")
                if filings_is_empty(filings):
                    return mcp_envelope_err(f"No foreign issuer updates found for {ticker}.", meta=meta)
                selected = filings.latest(n)
                selected_list = list(selected) if isinstance(selected, EntityFilings) else [selected]
            else:
                filings = company.get_filings(form="6-K", filing_date=_since_date_range(since_days))
                if filings_is_empty(filings):
                    return mcp_envelope_err(f"No foreign issuer updates found for {ticker} in last {since_days} days.", meta=meta)
                selected_list = list(filings.head(n))

            out: List[Dict[str, Any]] = []
            for filing in selected_list:
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
                                {"sequence": getattr(ex, "sequence", None), "description": getattr(ex, "description", None), "document": getattr(ex, "document", None), "type": getattr(ex, "type", None)}
                                for ex in exhibits
                            ]
                    except Exception:
                        entry["exhibits"] = None

                try:
                    txt = getattr(filing, "text", None)  # type: ignore[attr-defined]
                    if isinstance(txt, str) and txt:
                        entry["text_preview"] = txt[:2000]
                except Exception:
                    entry["text_preview"] = None

                out.append(entry)

            return mcp_envelope_ok(data={"filings": out}, meta=meta, text_fallback=f"Found {len(out)} foreign issuer updates for {ticker}.")
        except Exception as e:
            return mcp_envelope_err(f"Failed to load foreign issuer updates: {e}", meta=meta)

    # ---- proxy (DEF 14A) ----
    @mcp_tool()
    def summarize_proxy_comp_and_governance(
        self,
        ticker: str,
        year: int = 2024,
        max_filings: int = 5,
    ) -> Dict[str, Any]:
        """
        Summarize proxy compensation/governance themes from proxy filings (DEF 14A variants) as a JSON envelope.

        General description
        - Scans proxy filings for keyword hits related to compensation and governance topics.
        - Returns small text snippets per filing to help triage where to read deeper.

        Use case
        - You want quick context on executive compensation and governance issues discussed in a company’s proxy statement.

        Parameters
        - ticker: Stock ticker (e.g., "AAPL").
        - year: Filing year filter (e.g., 2024).
        - max_filings: Max number of proxy filings to scan.

        Return value
        - Dict JSON envelope with `data.filings[]` including accession_no and extracted `snippets`.

        Concrete example (code)
        ```python
        summarize_proxy_comp_and_governance(ticker="AAPL", year=2024, max_filings=5)
        ```
        """
        meta = {"tool": "summarize_proxy_comp_and_governance", "ticker": ticker, "year": year, "max_filings": max_filings}
        if not ticker:
            return mcp_envelope_err("ticker must be a non-empty string", meta=meta)
        if not isinstance(year, int) or year < 1900:
            return mcp_envelope_err("year must be a reasonable integer (e.g., 2024)", meta=meta)

        try:
            company = Company(ticker)
            filings = company.get_filings(form=["DEF 14A", "DEFA14A", "PRE 14A"], year=year)
            if filings_is_empty(filings):
                return mcp_envelope_err(f"No proxy filings found for {ticker} in {year}.", meta=meta)

            pats = re.compile(r"(say-on-pay|executive compensation|compensation discussion|board of directors|corporate governance|shareholder proposal|equity plan)", re.IGNORECASE)

            out: List[Dict[str, Any]] = []
            for f in filings.head(max_filings):
                entry = {"filing_date": getattr(f, "filing_date", None), "accession_no": getattr(f, "accession_no", None), "form": getattr(f, "form", None)}
                snippets: List[str] = []
                try:
                    txt = getattr(f, "text", None)  # type: ignore[attr-defined]
                    if isinstance(txt, str):
                        for m in pats.finditer(txt):
                            start = max(0, m.start() - 160)
                            end = min(len(txt), m.end() + 160)
                            snippets.append(txt[start:end].replace("\n", " "))
                            if len(snippets) >= 8:
                                break
                except Exception:
                    pass
                entry["snippets"] = snippets
                out.append(entry)

            return mcp_envelope_ok(data={"filings": out}, meta=meta, text_fallback=f"Proxy comp/governance snippets for {ticker} ({year}): {len(out)} filings scanned.")
        except Exception as e:
            return mcp_envelope_err(f"Failed to summarize proxy comp/governance: {e}", meta=meta)

    # ---- late filing notices (NT 10-K/NT 10-Q) ----
    @mcp_tool()
    def alert_late_reporting(
        self,
        ticker: str,
        since_days: int = 365,
        max_filings: int = 20,
    ) -> Dict[str, Any]:
        """
        Alert on late reporting notices (NT 10-K / NT 10-Q) and return matched snippets as a JSON envelope.

        General description
        - Searches for late filing notices in the lookback window and extracts a few keyword-context snippets per filing.
        - Useful as an operational risk indicator (delays, internal control issues, restatement hints).

        Use case
        - You want to know if a company recently filed an NT notice indicating a delayed periodic report.

        Parameters
        - ticker: Stock ticker (e.g., "NVDA").
        - since_days: Lookback window in days.
        - max_filings: Max number of NT filings to scan.

        Return value
        - Dict JSON envelope with `data.alerts` (each includes accession_no, filing_date, form, and snippets).

        Concrete example (code)
        ```python
        alert_late_reporting(ticker="NVDA", since_days=365, max_filings=20)
        ```
        """
        meta = {"tool": "alert_late_reporting", "ticker": ticker, "since_days": since_days, "max_filings": max_filings}
        if not ticker:
            return mcp_envelope_err("ticker must be a non-empty string", meta=meta)
        if not isinstance(since_days, int) or since_days < 0:
            return mcp_envelope_err("since_days must be a non-negative integer", meta=meta)

        try:
            company = Company(ticker)
            filings = company.get_filings(form=["NT 10-K", "NT 10-Q"], filing_date=_since_date_range(since_days))
            if filings_is_empty(filings):
                return mcp_envelope_ok(data={"alerts": [], "count": 0}, meta=meta, text_fallback=f"No late reporting notices for {ticker} in last {since_days} days.")

            pats = re.compile(r"(unable to file|could not be filed|delay|late|restatement|material weakness|internal control)", re.IGNORECASE)
            out: List[Dict[str, Any]] = []
            for f in filings.head(max_filings):
                entry = {"filing_date": getattr(f, "filing_date", None), "accession_no": getattr(f, "accession_no", None), "form": getattr(f, "form", None)}
                snippets: List[str] = []
                try:
                    txt = getattr(f, "text", None)  # type: ignore[attr-defined]
                    if isinstance(txt, str):
                        for m in pats.finditer(txt):
                            start = max(0, m.start() - 180)
                            end = min(len(txt), m.end() + 180)
                            snippets.append(txt[start:end].replace("\n", " "))
                            if len(snippets) >= 5:
                                break
                except Exception:
                    pass
                entry["snippets"] = snippets
                out.append(entry)

            return mcp_envelope_ok(data={"alerts": out, "count": len(out)}, meta=meta, text_fallback=f"Found {len(out)} late reporting notice(s) for {ticker} in last {since_days} days.")
        except Exception as e:
            return mcp_envelope_err(f"Failed to check late reporting notices: {e}", meta=meta)

    # ---- crowdfunding (Form C) ----
    @mcp_tool()
    def screen_crowdfunding_offerings(
        self,
        year: int,
        quarter: int,
        min_maximum_amount_usd: float = 1_000_000.0,
        max_filings: int = 200,
    ) -> Dict[str, Any]:
        """
        Screen Form C crowdfunding offerings for large raises in a given quarter, returned as a JSON envelope.

        General description
        - Loads Reg CF (Form C) filings for the specified year/quarter and extracts maximum offering amount when available.
        - Filters to offerings with maximum offering amount >= `min_maximum_amount_usd` (best-effort; depends on form parsing).

        Use case
        - You want to find the largest crowdfunding offerings filed in a given quarter.

        Parameters
        - year: Filing year (e.g., 2024).
        - quarter: Filing quarter (1–4).
        - min_maximum_amount_usd: Minimum maximum offering amount threshold for inclusion.
        - max_filings: Max number of filings to scan.

        Return value
        - Dict JSON envelope:
          - data.results: matched offerings with maximum_offering_amount_usd
          - data.summary: processed and matched counts

        Concrete example (code)
        ```python
        screen_crowdfunding_offerings(year=2024, quarter=2, min_maximum_amount_usd=1_000_000, max_filings=200)
        ```
        """
        meta = {"tool": "screen_crowdfunding_offerings", "year": year, "quarter": quarter, "min_maximum_amount_usd": min_maximum_amount_usd, "max_filings": max_filings}
        if not isinstance(year, int) or year < 1990:
            return mcp_envelope_err("year must be an integer >= 1990", meta=meta)
        if quarter not in (1, 2, 3, 4):
            return mcp_envelope_err("quarter must be 1, 2, 3, or 4", meta=meta)
        if min_maximum_amount_usd <= 0:
            return mcp_envelope_err("min_maximum_amount_usd must be > 0", meta=meta)

        try:
            filings = get_filings(year, quarter, form="C")
            if filings_is_empty(filings):
                return mcp_envelope_err(f"No crowdfunding filings found for {year} Q{quarter}.", meta=meta)

            results: List[Dict[str, Any]] = []
            processed = 0
            for f in filings.head(max_filings):
                processed += 1
                entry = {"filing_date": getattr(f, "filing_date", None), "accession_no": getattr(f, "accession_no", None), "company": getattr(f, "company", None), "cik": getattr(f, "cik", None), "form": getattr(f, "form", None)}
                try:
                    obj = f.obj()
                    offering = getattr(obj, "offering_information", None)
                    max_amt = None
                    if offering is not None:
                        max_amt = getattr(offering, "maximum_offering_amount", None)
                    entry["maximum_offering_amount"] = mcp_json_safe(max_amt)
                    entry["issuer_name"] = getattr(obj, "issuer_name", None)
                    entry["campaign_status"] = getattr(obj, "campaign_status", None)
                except Exception:
                    entry["maximum_offering_amount"] = None

                try:
                    mv = entry["maximum_offering_amount"]
                    mv_f = float(mv) if mv is not None else None
                except Exception:
                    mv_f = None
                if mv_f is not None and mv_f >= float(min_maximum_amount_usd):
                    results.append({**entry, "maximum_offering_amount_usd": mv_f})

            results = sorted(results, key=lambda r: float(r.get("maximum_offering_amount_usd") or 0.0), reverse=True)
            return mcp_envelope_ok(
                data={"results": results, "summary": {"processed": processed, "matched": len(results)}},
                meta=meta,
                text_fallback=f"Found {len(results)} crowdfunding offerings with maximum >= ${min_maximum_amount_usd:,.0f} in {year} Q{quarter}.",
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to screen crowdfunding offerings: {e}", meta=meta)


