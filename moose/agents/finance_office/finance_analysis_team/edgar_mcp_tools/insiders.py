from __future__ import annotations

from typing import Any, Dict, List

from edgar import Company
from edgar._filings import get_by_accession_number

from .basic import EdgarMCPTools, _since_date_range, filings_is_empty, mcp_envelope_err, mcp_envelope_ok, mcp_tool, pd


class InsiderTradeMCPTools(EdgarMCPTools):
    """
    Insider trading (Forms 3/4/5) tools.
    """

    @mcp_tool()
    def list_insider_filings_index(self, ticker: str, since_days: int = 30, limit_filings: int = 50) -> Dict[str, Any]:
        """
        List recent insider trading-related filings (Forms 3/4/5) for a company and return an index as a JSON envelope.

        General description
        - Queries SEC filings for the given `ticker` and returns a small list of recent insider forms with accession numbers.
        - This is typically an **index step** you run before parsing transactions from a specific filing.

        Use case
        - You want to see whether insiders have been active recently for a company and then drill into a specific filing.

        Parameters
        - ticker: Stock ticker (e.g., "NVDA").
        - since_days: Lookback window in days (non-negative integer).
        - limit_filings: Max number of filings to return in the index.

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.filings: list of {filing_date, accession_no, form, company}
          - meta: tool inputs + tool name
          - recommended_next_tools: typically suggests `fetch_insider_transactions`

        Concrete example (code)
        ```python
        list_insider_filings_index(ticker="NVDA", since_days=30, limit_filings=25)
        ```
        """
        meta = {"tool": "list_insider_filings_index", "ticker": ticker, "since_days": since_days, "limit_filings": limit_filings}
        if not isinstance(since_days, int) or since_days < 0:
            return mcp_envelope_err("since_days must be a non-negative integer", meta=meta)
        try:
            c = Company(ticker)
            filings = c.get_filings(form=["3", "4", "5"], filing_date=_since_date_range(since_days))
            if filings_is_empty(filings):
                return mcp_envelope_err(f"No insider filings found for {ticker} in last {since_days} days.", meta=meta)
            out = []
            for f in filings.head(limit_filings):
                out.append(
                    {
                        "filing_date": getattr(f, "filing_date", None),
                        "accession_no": getattr(f, "accession_no", None),
                        "form": getattr(f, "form", None),
                        "company": getattr(f, "company", None),
                    }
                )
            rec = [
                {
                    "tool": "fetch_insider_transactions",
                    "reason": "Parse a specific insider filing into normalized transactions.",
                    "args_template": {"accession_no": "{data.filings[0].accession_no}"},
                }
            ] if out else []
            return mcp_envelope_ok(
                data={"filings": out},
                meta=meta,
                text_fallback=f"Found {len(out)} insider filings for {ticker}.",
                outputs=[{"name": "filings", "path": "data.filings", "description": "Insider filing index with accession_no"}],
                recommended_next_tools=rec,
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to list insider filings: {e}", meta=meta)

    @mcp_tool()
    def fetch_insider_transactions(self, accession_no: str, detailed: bool = True, include_metadata: bool = True) -> Dict[str, Any]:
        """
        Parse a single insider filing (by accession number) into a normalized transactions table, returned as a JSON envelope.

        General description
        - Loads the filing referenced by `accession_no` and converts it into a transactions table.
        - Useful after `list_insider_filings_index`, which provides the accession numbers.

        Use case
        - You want to parse a specific Form 4 filing into normalized buy/sell transactions with share counts and prices.

        Parameters
        - accession_no: SEC accession number from an index tool result.
        - detailed: Whether to request a more detailed parsed table (depends on edgartools support).
        - include_metadata: Whether to include extra metadata columns (owner, role, etc.).

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.transactions: list/dict table of parsed transaction rows (JSON-safe)
          - meta: tool inputs

        Concrete example (code)
        ```python
        fetch_insider_transactions(accession_no="0001045810-24-000123", detailed=True, include_metadata=True)
        ```
        """
        meta = {"tool": "fetch_insider_transactions", "accession_no": accession_no, "detailed": detailed, "include_metadata": include_metadata}
        if not accession_no:
            return mcp_envelope_err("accession_no must be provided", meta=meta)
        try:
            filing = get_by_accession_number(accession_no)
            if filing is None:
                return mcp_envelope_err(f"Filing not found for accession {accession_no}", meta=meta)
            obj = filing.obj()
            tx_df = obj.to_dataframe(detailed=detailed, include_metadata=include_metadata)  # type: ignore[attr-defined]
            return mcp_envelope_ok(
                data={"accession_no": accession_no, "transactions": tx_df},
                meta=meta,
                text_fallback=f"Parsed insider transactions for {accession_no}.",
                dependencies=[{"after_tool": "list_insider_filings_index", "field_paths": ["data.filings[].accession_no"], "notes": "Use accession_no from insider index."}],
                outputs=[{"name": "transactions", "path": "data.transactions", "description": "Normalized insider transaction rows"}],
            )
        except Exception as e:
            return mcp_envelope_err(f"Failed to parse insider transactions: {e}", meta=meta)

    @mcp_tool()
    def summarize_insider_activity(
        self,
        ticker: str,
        since_days: int = 90,
        limit_filings: int = 200,
    ) -> Dict[str, Any]:
        """
        Summarize insider buying/selling activity over a lookback window, returned as a JSON envelope.

        General description
        - Calls `list_insider_filings_index` and then parses each filing into transactions.
        - Produces a high-level summary: filings count, approximate buy/sell rows, and share totals by owner (best-effort).

        Use case
        - You want a quick “insider sentiment” snapshot for a company before deeper research or a trading decision.

        Parameters
        - ticker: Stock ticker (e.g., "AAPL").
        - since_days: Lookback window in days (non-negative integer).
        - limit_filings: Max number of filings to consider (upper bound for work).

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.by_owner: aggregated share acquired/disposed by owner (best-effort)
          - data.approx_trade_rows: approximate buy/sell row counts
          - meta: tool inputs

        Concrete example (code)
        ```python
        summarize_insider_activity(ticker="AAPL", since_days=180, limit_filings=200)
        ```
        """
        meta = {"tool": "summarize_insider_activity", "ticker": ticker, "since_days": since_days, "limit_filings": limit_filings}
        if not isinstance(since_days, int) or since_days < 0:
            return mcp_envelope_err("since_days must be a non-negative integer", meta=meta)

        try:
            idx = self.list_insider_filings_index(ticker=ticker, since_days=since_days, limit_filings=limit_filings)
            if not idx.get("ok"):
                try:
                    if isinstance(idx.get("meta"), dict):
                        idx["meta"]["tool"] = "summarize_insider_activity"
                except Exception:
                    pass
                return idx
            filing_rows = (idx.get("data") or {}).get("filings") or []

            filings: List[Dict[str, Any]] = []
            for f in filing_rows:
                acc = f.get("accession_no")
                if not acc:
                    continue
                parsed = self.fetch_insider_transactions(acc, detailed=True, include_metadata=True)
                if not parsed.get("ok"):
                    continue
                filings.append({"filing_date": f.get("filing_date"), "accession_no": acc, "transactions": (parsed.get("data") or {}).get("transactions")})

            buy_count = 0
            sell_count = 0
            per_owner: Dict[str, Dict[str, float]] = {}

            for f in filings:
                tx = f.get("transactions")
                if pd is None or tx is None or not isinstance(tx, list):
                    continue
                for row in tx:
                    code = str(row.get("code") or row.get("TransactionCode") or row.get("transaction_code") or "").upper()
                    buy_sell = str(row.get("buy_sell") or row.get("BuySell") or row.get("acquired_disposed") or "").upper()
                    owner = str(row.get("owner_name") or row.get("Owner") or row.get("reporting_owner") or row.get("ReportingOwner") or "UNKNOWN")
                    shares = row.get("shares") or row.get("Shares") or row.get("transaction_shares") or 0
                    try:
                        shares_f = float(shares) if shares is not None else 0.0
                    except Exception:
                        shares_f = 0.0

                    if buy_sell == "A":
                        buy_count += 1
                        per_owner.setdefault(owner, {"shares_acquired": 0.0, "shares_disposed": 0.0})["shares_acquired"] += shares_f
                    elif buy_sell == "D":
                        sell_count += 1
                        per_owner.setdefault(owner, {"shares_acquired": 0.0, "shares_disposed": 0.0})["shares_disposed"] += shares_f
                    else:
                        if code == "P":
                            buy_count += 1
                        if code == "S":
                            sell_count += 1

            data = {
                "ticker": ticker,
                "since_days": since_days,
                "filings_count": len(filings),
                "approx_trade_rows": {"buys": buy_count, "sells": sell_count},
                "by_owner": per_owner,
            }
            text_fallback = f"Insider activity for {ticker}: {len(filings)} filings, ~{buy_count} buy rows, ~{sell_count} sell rows."
            return mcp_envelope_ok(data=data, meta=meta, text_fallback=text_fallback)
        except Exception as e:
            return mcp_envelope_err(f"Failed to summarize insider activity: {e}", meta=meta)

    @mcp_tool()
    def alert_large_insider_sales(
        self,
        ticker: str,
        min_value_usd: float = 1_000_000.0,
        since_days: int = 30,
        limit_filings: int = 200,
    ) -> Dict[str, Any]:
        """
        Identify potentially significant insider sales over a lookback window, returned as a JSON envelope.

        General description
        - Parses recent insider filings and flags sale rows whose estimated value (shares × price) exceeds `min_value_usd`.
        - Intended as a screening tool for large/meaningful insider selling events.

        Use case
        - You want to alert on large insider selling over a recent window to evaluate potential downside risk.

        Parameters
        - ticker: Stock ticker (e.g., "NVDA").
        - min_value_usd: Minimum estimated sale value (USD) to include in alerts.
        - since_days: Lookback window in days (non-negative integer).
        - limit_filings: Max number of filings to parse.

        Return value
        - Dict JSON envelope:
          - ok: bool
          - data.alerts: list of alert rows with estimated_value_usd and underlying transaction row
          - data.count: number of alerts returned
          - meta: tool inputs

        Concrete example (code)
        ```python
        alert_large_insider_sales(ticker="NVDA", min_value_usd=2_000_000, since_days=30, limit_filings=200)
        ```
        """
        meta = {
            "tool": "alert_large_insider_sales",
            "ticker": ticker,
            "min_value_usd": min_value_usd,
            "since_days": since_days,
            "limit_filings": limit_filings,
        }
        if not isinstance(since_days, int) or since_days < 0:
            return mcp_envelope_err("since_days must be a non-negative integer", meta=meta)
        try:
            idx = self.list_insider_filings_index(ticker=ticker, since_days=since_days, limit_filings=limit_filings)
            if not idx.get("ok"):
                try:
                    if isinstance(idx.get("meta"), dict):
                        idx["meta"]["tool"] = "alert_large_insider_sales"
                except Exception:
                    pass
                return idx

            filing_rows = (idx.get("data") or {}).get("filings") or []
            filings: List[Dict[str, Any]] = []
            for f in filing_rows:
                acc = f.get("accession_no")
                if not acc:
                    continue
                parsed = self.fetch_insider_transactions(accession_no=acc, detailed=True, include_metadata=True)
                if not parsed.get("ok"):
                    continue
                filings.append(
                    {
                        "filing_date": f.get("filing_date"),
                        "accession_no": acc,
                        "transactions": (parsed.get("data") or {}).get("transactions"),
                    }
                )

            alerts: List[Dict[str, Any]] = []
            for f in filings:
                tx = f.get("transactions")
                if tx is None or not isinstance(tx, list):
                    continue
                for row in tx:
                    buy_sell = str(row.get("buy_sell") or row.get("BuySell") or row.get("acquired_disposed") or "").upper()
                    code = str(row.get("code") or row.get("TransactionCode") or row.get("transaction_code") or "").upper()
                    is_sale = buy_sell == "D" or code == "S"
                    if not is_sale:
                        continue

                    shares = row.get("shares") or row.get("Shares") or row.get("transaction_shares")
                    price = row.get("price") or row.get("Price") or row.get("price_per_share") or row.get("PricePerShare")
                    try:
                        shares_f = float(shares) if shares is not None else 0.0
                        price_f = float(price) if price is not None else None
                    except Exception:
                        shares_f = 0.0
                        price_f = None

                    est_value = shares_f * price_f if price_f is not None else None
                    if est_value is None or est_value < float(min_value_usd):
                        continue

                    alerts.append(
                        {
                            "filing_date": f.get("filing_date"),
                            "accession_no": f.get("accession_no"),
                            "owner": row.get("owner_name") or row.get("Owner") or row.get("ReportingOwner"),
                            "shares": shares_f,
                            "price": price_f,
                            "estimated_value_usd": est_value,
                            "row": row,
                        }
                    )

            alerts = sorted(alerts, key=lambda r: float(r.get("estimated_value_usd") or 0.0), reverse=True)[:100]
            text_fallback = f"Found {len(alerts)} large insider sale rows for {ticker} in last {since_days} days (>= ${min_value_usd:,.0f})."
            return mcp_envelope_ok(data={"alerts": alerts, "count": len(alerts)}, meta=meta, text_fallback=text_fallback)
        except Exception as e:
            return mcp_envelope_err(f"Failed to alert on insider sales: {e}", meta=meta)


