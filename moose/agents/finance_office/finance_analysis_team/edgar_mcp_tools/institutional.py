from __future__ import annotations

from typing import Any, Dict, List, Tuple

from edgar import Company
from edgar._filings import get_by_accession_number
from edgar.entity.filings import EntityFilings

from .basic import EdgarMCPTools, _df_keyed_holdings, filings_is_empty, mcp_envelope_err, mcp_envelope_ok, mcp_tool, pd


class InstitutionalHoldingsMCPTools(EdgarMCPTools):
    """
    Institutional holdings (13F) tools.
    """

    @mcp_tool()
    def list_institutional_disclosures_index(self, manager: str, n: int = 2) -> Dict[str, Any]:
        """
        List recent institutional holdings disclosures (Form 13F-HR) for an investment manager and return an index as a JSON envelope.

        General description
        - Looks up 13F-HR filings for `manager` and returns the latest `n` filing accession numbers.
        - This is an **index step** used to drive downstream tools that fetch holdings tables or compare filings.

        Use case
        - You want to compare an investment manager’s latest 13F to the prior quarter to see major adds/cuts.

        Parameters
        - manager: Manager identifier usable by `edgar.Company(...)` (e.g., a manager name, ticker, or CIK depending on edgartools support).
        - n: Number of latest disclosures to return (positive integer).

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.filings: list of {filing_date, accession_no, form, company}
          - recommended_next_tools: suggests fetching holdings or comparing two filings

        Concrete example (code)
        ```python
        list_institutional_disclosures_index(manager="BERKSHIRE HATHAWAY INC", n=2)
        ```
        """
        meta = {"tool": "list_institutional_disclosures_index", "manager": manager, "n": n}
        if not manager:
            return mcp_envelope_err("manager must be provided", meta=meta)
        if not isinstance(n, int) or n < 1:
            return mcp_envelope_err("n must be a positive integer", meta=meta)
        try:
            c = Company(manager)
            filings = c.get_filings(form="13F-HR")
            if filings_is_empty(filings):
                return mcp_envelope_err(f"No holdings disclosures found for {manager}.", meta=meta)
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
            rec: List[Dict[str, Any]] = []
            if len(out) >= 1:
                rec.append(
                    {
                        "tool": "fetch_institutional_holdings_table",
                        "reason": "Fetch the holdings table for a disclosure.",
                        "args_template": {"accession_no": "{data.filings[0].accession_no}"},
                    }
                )
            if len(out) >= 2:
                rec.append(
                    {
                        "tool": "compare_institutional_holdings",
                        "reason": "Compare two disclosures by explicit accession numbers.",
                        "args_template": {
                            "accession_no_new": "{data.filings[0].accession_no}",
                            "accession_no_old": "{data.filings[1].accession_no}",
                        },
                    }
                )
            return mcp_envelope_ok(
                data={"filings": out},
                meta=meta,
                text_fallback=f"Found {len(out)} disclosures for {manager}.",
                outputs=[{"name": "filings", "path": "data.filings", "description": "Holdings disclosure index"}],
                recommended_next_tools=rec,
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to list institutional disclosures: {e}", meta=meta)

    @mcp_tool()
    def fetch_institutional_holdings_table(self, accession_no: str, top_n: int = 200) -> Dict[str, Any]:
        """
        Fetch and return a manager’s 13F holdings table for a specific filing (by accession number) as a JSON envelope.

        General description
        - Loads the filing referenced by `accession_no`, extracts its 13F infotable, and returns holdings rows.
        - Optionally limits to the top holdings by value (`top_n`) when a DataFrame is available.

        Use case
        - You want the top holdings positions after selecting a 13F filing accession number from the index.

        Parameters
        - accession_no: SEC accession number for a 13F-HR filing.
        - top_n: Max number of holdings rows to return (best-effort; only applied when sortable).

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.holdings: holdings table rows (JSON-safe)
          - meta: tool inputs

        Concrete example (code)
        ```python
        fetch_institutional_holdings_table(accession_no="0000950123-24-012345", top_n=100)
        ```
        """
        meta = {"tool": "fetch_institutional_holdings_table", "accession_no": accession_no, "top_n": top_n}
        if not accession_no:
            return mcp_envelope_err("accession_no must be provided", meta=meta)
        try:
            filing = get_by_accession_number(accession_no)
            if filing is None:
                return mcp_envelope_err(f"Filing not found for accession {accession_no}", meta=meta)
            obj = filing.obj()  # type: ignore[union-attr]
            df = getattr(obj, "infotable", None)
            if pd is not None and isinstance(df, pd.DataFrame):
                df = df.sort_values("Value", ascending=False).head(top_n)
            return mcp_envelope_ok(
                data={"accession_no": accession_no, "holdings": df},
                meta=meta,
                text_fallback=f"Fetched holdings for {accession_no}.",
                outputs=[{"name": "holdings", "path": "data.holdings", "description": "Holdings table rows"}],
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to fetch holdings table: {e}", meta=meta)

    @mcp_tool()
    def compare_institutional_holdings(self, accession_no_new: str, accession_no_old: str, top_n_changes: int = 30) -> Dict[str, Any]:
        """
        Compare two 13F holdings disclosures (new vs old) and summarize changes as a JSON envelope.

        General description
        - Fetches holdings tables for `accession_no_new` and `accession_no_old`.
        - Produces:
          - new positions (present in new, absent in old)
          - closed positions (present in old, absent in new)
          - top adds/cuts by estimated value change

        Use case
        - You want to identify what a manager meaningfully added or cut between two reporting periods.

        Parameters
        - accession_no_new: Accession number for the newer 13F-HR filing.
        - accession_no_old: Accession number for the older 13F-HR filing.
        - top_n_changes: Number of items to return per section (adds/cuts/new/closed).

        Return value
        - Dict JSON envelope with `data.top_adds`, `data.top_cuts`, `data.new_positions`, `data.closed_positions`.

        Concrete example (code)
        ```python
        compare_institutional_holdings(
            accession_no_new="0000950123-24-012345",
            accession_no_old="0000950123-23-067890",
            top_n_changes=25,
        )
        ```
        """
        meta = {
            "tool": "compare_institutional_holdings",
            "accession_no_new": accession_no_new,
            "accession_no_old": accession_no_old,
            "top_n_changes": top_n_changes,
        }
        if not accession_no_new or not accession_no_old:
            return mcp_envelope_err("Both accession_no_new and accession_no_old are required", meta=meta)
        try:
            newer = self.fetch_institutional_holdings_table(accession_no_new, top_n=5000)
            older = self.fetch_institutional_holdings_table(accession_no_old, top_n=5000)
            if not newer.get("ok") or not older.get("ok"):
                return mcp_envelope_err("Failed to fetch holdings for comparison.", meta=meta)
            cur_df = (newer.get("data") or {}).get("holdings")
            prev_df = (older.get("data") or {}).get("holdings")
            cur_map, _ = _df_keyed_holdings(cur_df)
            prev_map, _ = _df_keyed_holdings(prev_df)
            cur_keys = set(cur_map.keys())
            prev_keys = set(prev_map.keys())
            new_positions = sorted(cur_keys - prev_keys)
            closed_positions = sorted(prev_keys - cur_keys)

            def _num(x: Any) -> float:
                try:
                    return float(x) if x is not None else 0.0
                except Exception:
                    return 0.0

            changes = []
            for k in sorted(cur_keys & prev_keys):
                cur = cur_map[k]
                pre = prev_map[k]
                dv = _num(cur.get("Value")) - _num(pre.get("Value"))
                if dv != 0:
                    changes.append(
                        {
                            "key": k,
                            "ticker": cur.get("Ticker") or pre.get("Ticker"),
                            "issuer": cur.get("Issuer") or pre.get("Issuer"),
                            "value_change_k": dv,
                        }
                    )
            adds = sorted(changes, key=lambda r: r["value_change_k"], reverse=True)[:top_n_changes]
            cuts = sorted(changes, key=lambda r: r["value_change_k"])[:top_n_changes]
            return mcp_envelope_ok(
                data={
                    "accession_no_new": accession_no_new,
                    "accession_no_old": accession_no_old,
                    "new_positions": [cur_map[k] for k in new_positions[:top_n_changes]],
                    "closed_positions": [prev_map[k] for k in closed_positions[:top_n_changes]],
                    "top_adds": adds,
                    "top_cuts": cuts,
                },
                meta=meta,
                text_fallback=f"Compared holdings: {len(new_positions)} new, {len(closed_positions)} closed.",
                dependencies=[
                    {
                        "after_tool": "list_institutional_disclosures_index",
                        "field_paths": ["data.filings[0].accession_no", "data.filings[1].accession_no"],
                        "notes": "Use the latest two disclosures.",
                    }
                ],
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to compare holdings disclosures: {e}", meta=meta)

    @mcp_tool()
    def detect_crowded_institutional_trades(
        self,
        managers: List[str],
        min_owner_count: int = 5,
        top_n: int = 50,
        per_manager_limit: int = 2000,
    ) -> Dict[str, Any]:
        """
        Detect “crowded” stocks across multiple managers’ latest 13F filings and return results as a JSON envelope.

        General description
        - Aggregates holdings from each manager’s latest 13F and finds tickers held by at least `min_owner_count` managers.
        - Useful for identifying consensus / crowded positioning (best-effort; depends on holdings tables having tickers).

        Use case
        - You want to screen for crowded longs across a basket of funds to understand positioning risk.

        Parameters
        - managers: List of manager identifiers (name/ticker/CIK depending on edgartools support).
        - min_owner_count: Minimum number of managers that must hold a ticker to include it.
        - top_n: Number of crowded tickers to return.
        - per_manager_limit: Max rows per manager holdings table to scan (performance control).

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.crowded: list of {ticker, owner_count, owners, total_value_k_estimate}
          - data.summary: processed managers and skipped reasons

        Concrete example (code)
        ```python
        detect_crowded_institutional_trades(
            managers=["MANAGER_A", "MANAGER_B", "MANAGER_C"],
            min_owner_count=2,
            top_n=20,
            per_manager_limit=1500,
        )
        ```
        """
        env = self.find_crowded_trades(
            managers=managers,
            min_owner_count=min_owner_count,
            top_n=top_n,
            per_manager_limit=per_manager_limit,
        )
        try:
            if isinstance(env.get("meta"), dict):
                env["meta"]["tool"] = "detect_crowded_institutional_trades"
        except Exception:
            pass
        return env

    def find_crowded_trades(
        self,
        managers: List[str],
        min_owner_count: int = 5,
        top_n: int = 50,
        per_manager_limit: int = 2000,
    ) -> Dict[str, Any]:
        meta = {
            "tool": "find_crowded_trades",
            "managers": managers,
            "min_owner_count": min_owner_count,
            "top_n": top_n,
            "per_manager_limit": per_manager_limit,
        }
        if not isinstance(managers, list) or not managers:
            return mcp_envelope_err("managers must be a non-empty list of tickers/CIKs", meta=meta)
        if not isinstance(min_owner_count, int) or min_owner_count < 1:
            return mcp_envelope_err("min_owner_count must be a positive integer", meta=meta)
        if not isinstance(top_n, int) or top_n < 1:
            return mcp_envelope_err("top_n must be a positive integer", meta=meta)

        owners_by_ticker: Dict[str, List[Dict[str, Any]]] = {}
        processed = 0
        skipped: List[Dict[str, Any]] = []

        for m in managers:
            try:
                c = Company(m)
                filings = c.get_filings(form="13F-HR")
                if filings_is_empty(filings):
                    skipped.append({"manager": m, "reason": "no_13f_filings"})
                    continue
                filing = filings.latest()
                thirteenf = filing.obj()  # type: ignore[union-attr]
                if not getattr(thirteenf, "has_infotable", lambda: False)():
                    skipped.append({"manager": m, "reason": "no_infotable"})
                    continue
                df = getattr(thirteenf, "infotable", None)
                if pd is None or df is None or not isinstance(df, pd.DataFrame):
                    skipped.append({"manager": m, "reason": "infotable_not_dataframe"})
                    continue

                processed += 1
                df2 = df.head(per_manager_limit)
                for _, row in df2.iterrows():
                    r = row.to_dict()
                    t = str(r.get("Ticker") or "").strip().upper()
                    if not t:
                        continue
                    owners_by_ticker.setdefault(t, []).append(
                        {
                            "manager": m,
                            "report_period": getattr(thirteenf, "report_period", None),
                            "value_k": r.get("Value"),
                            "shares": r.get("Shares"),
                            "issuer": r.get("Issuer"),
                            "cusip": r.get("Cusip"),
                        }
                    )
            except Exception as e:
                skipped.append({"manager": m, "reason": f"error: {e}"})

        crowded = []
        for t, owners in owners_by_ticker.items():
            if len(owners) >= int(min_owner_count):
                total_value = 0.0
                for o in owners:
                    try:
                        total_value += float(o.get("value_k") or 0.0)
                    except Exception:
                        pass
                crowded.append({"ticker": t, "owner_count": len(owners), "total_value_k_estimate": total_value, "owners": owners})

        crowded = sorted(crowded, key=lambda r: (int(r.get("owner_count") or 0), float(r.get("total_value_k_estimate") or 0.0)), reverse=True)[:top_n]
        return mcp_envelope_ok(
            data={"crowded": crowded, "summary": {"processed_managers": processed, "skipped": skipped}},
            meta=meta,
            text_fallback=f"Found {len(crowded)} crowded tickers from {processed} managers.",
        )

    @mcp_tool()
    def track_institutional_owners_of_stock(
        self,
        ticker: str,
        managers: List[str],
        top_n: int = 20,
        per_manager_limit: int = 5000,
    ) -> Dict[str, Any]:
        """
        Find which managers (from a provided list) currently disclose ownership of a given stock in their latest 13F filings.

        General description
        - For each manager in `managers`, loads the latest 13F holdings table and checks for `ticker`.
        - Returns the top owners by estimated value (best-effort).

        Use case
        - You want to see which managers (from a tracked list) currently own a given stock based on their latest 13F filings.

        Parameters
        - ticker: Stock ticker to search for (e.g., "NVDA").
        - managers: List of manager identifiers to scan.
        - top_n: Number of owners to return (sorted by value).
        - per_manager_limit: Max holdings rows to scan per manager (performance control).

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.owners: list of owners with value/shares/cusip/issuer
          - data.summary.skipped: managers skipped and reasons

        Concrete example (code)
        ```python
        track_institutional_owners_of_stock(
            ticker="NVDA",
            managers=["MANAGER_A", "MANAGER_B", "MANAGER_C"],
            top_n=10,
        )
        ```
        """
        meta = {"tool": "track_institutional_owners_of_stock", "ticker": ticker, "managers": managers, "top_n": top_n}
        if not ticker:
            return mcp_envelope_err("ticker must be a non-empty string", meta=meta)
        if not isinstance(managers, list) or not managers:
            return mcp_envelope_err("managers must be a non-empty list", meta=meta)
        if not isinstance(top_n, int) or top_n < 1:
            return mcp_envelope_err("top_n must be a positive integer", meta=meta)

        owners: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        for m in managers:
            try:
                c = Company(m)
                filings = c.get_filings(form="13F-HR")
                if filings_is_empty(filings):
                    skipped.append({"manager": m, "reason": "no_filings"})
                    continue
                filing = filings.latest()
                thirteenf = filing.obj()  # type: ignore[union-attr]
                if not getattr(thirteenf, "has_infotable", lambda: False)():
                    skipped.append({"manager": m, "reason": "no_infotable"})
                    continue
                df = getattr(thirteenf, "infotable", None)
                if pd is None or df is None or not isinstance(df, pd.DataFrame):
                    skipped.append({"manager": m, "reason": "infotable_not_dataframe"})
                    continue
                df2 = df.head(per_manager_limit)
                hit = df2[df2["Ticker"].astype(str).str.upper() == ticker.upper()]
                if hit.empty:
                    continue
                row = hit.iloc[0].to_dict()
                owners.append(
                    {
                        "manager": m,
                        "report_period": getattr(thirteenf, "report_period", None),
                        "value_k": row.get("Value"),
                        "shares": row.get("Shares"),
                        "cusip": row.get("Cusip"),
                        "issuer": row.get("Issuer"),
                    }
                )
            except Exception as e:
                skipped.append({"manager": m, "reason": f"error: {e}"})

        owners_sorted = sorted(owners, key=lambda r: float(r.get("value_k") or 0.0), reverse=True)[:top_n]
        text_fallback = f"Found {len(owners_sorted)} managers (out of {len(managers)}) disclosing {ticker} holdings."
        return mcp_envelope_ok(
            data={"ticker": ticker, "owners": owners_sorted, "summary": {"owners_found": len(owners_sorted), "skipped": skipped}},
            meta=meta,
            text_fallback=text_fallback,
        )


