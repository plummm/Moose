from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _utc_ts() -> float:
    return time.time()


def default_db_path(*, agent_name: str = "alpaca_trader") -> Path:
    """
    Resolve a container-friendly DB path under the mounted projects directory.
    """
    project_id = os.getenv("MOOSE_PROJECT_ID") or "default"
    base_dir = os.getenv("MOOSE_PROJECTS_DIR")
    projects_base = Path(base_dir) if base_dir else (Path.cwd() / "projects")
    out_dir = projects_base / str(project_id) / "agent_data" / agent_name
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / "state.sqlite"


@dataclass(frozen=True)
class StoredEvent:
    event_id: int
    ts: float
    source: str
    text: str
    symbols: List[str]
    text_hash: str


class TraderDb:
    """
    Lightweight SQLite persistence for alpaca_trader.

    Thread-safe: uses per-operation connections + a lock for schema setup.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _ensure_schema(self) -> None:
        with self._schema_lock:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS dedupe_keys (
                      key TEXT PRIMARY KEY,
                      expires_at REAL NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      ts REAL NOT NULL,
                      source TEXT NOT NULL,
                      text TEXT NOT NULL,
                      text_hash TEXT NOT NULL,
                      symbols_json TEXT NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS scores_global (
                      key TEXT PRIMARY KEY,
                      value REAL NOT NULL,
                      updated_at REAL NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS scores_watch (
                      symbol TEXT PRIMARY KEY,
                      value REAL NOT NULL,
                      updated_at REAL NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      symbol TEXT NOT NULL,
                      priority REAL NOT NULL,
                      reason TEXT NOT NULL,
                      strategy_hint TEXT,
                      created_at REAL NOT NULL,
                      status TEXT NOT NULL
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status_pri ON tasks(status, priority DESC)")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trade_plans (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      symbol TEXT NOT NULL,
                      strategy_id TEXT NOT NULL,
                      account_id TEXT NOT NULL,
                      side TEXT NOT NULL,
                      confidence REAL NOT NULL,
                      plan_json TEXT NOT NULL,
                      created_at REAL NOT NULL,
                      status TEXT NOT NULL
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_trade_plans_symbol_ts ON trade_plans(symbol, created_at DESC)")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trade_plan_audit (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      plan_id INTEGER NOT NULL,
                      ts REAL NOT NULL,
                      event TEXT NOT NULL,
                      detail_json TEXT NOT NULL,
                      FOREIGN KEY(plan_id) REFERENCES trade_plans(id) ON DELETE CASCADE
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_trade_plan_audit_plan_ts ON trade_plan_audit(plan_id, ts DESC)")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS orders (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      plan_id INTEGER UNIQUE NOT NULL,
                      account_id TEXT NOT NULL,
                      client_order_id TEXT NOT NULL,
                      alpaca_order_id TEXT,
                      symbol TEXT NOT NULL,
                      side TEXT NOT NULL,
                      notional_usd_hint REAL,
                      status TEXT NOT NULL,
                      raw_output TEXT,
                      parsed_json TEXT,
                      created_at REAL NOT NULL,
                      updated_at REAL NOT NULL,
                      FOREIGN KEY(plan_id) REFERENCES trade_plans(id) ON DELETE CASCADE
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_account_status ON orders(account_id, status)")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reconcile_snapshots (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      account_id TEXT NOT NULL,
                      ts REAL NOT NULL,
                      snapshot_json TEXT NOT NULL
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_reconcile_account_ts ON reconcile_snapshots(account_id, ts DESC)")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS positions_snapshots (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      account_id TEXT NOT NULL,
                      ts REAL NOT NULL,
                      positions_json TEXT NOT NULL
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_positions_account_ts ON positions_snapshots(account_id, ts DESC)")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS orders_snapshots (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      account_id TEXT NOT NULL,
                      ts REAL NOT NULL,
                      orders_json TEXT NOT NULL
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_orderssnap_account_ts ON orders_snapshots(account_id, ts DESC)")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fills (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      plan_id INTEGER,
                      client_order_id TEXT,
                      alpaca_order_id TEXT,
                      ts REAL NOT NULL,
                      fill_json TEXT NOT NULL
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_fills_order_ts ON fills(alpaca_order_id, ts DESC)")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS daily_summaries (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      date TEXT NOT NULL,
                      account_id TEXT NOT NULL,
                      summary_json TEXT NOT NULL,
                      created_at REAL NOT NULL,
                      UNIQUE(date, account_id)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notifications (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      ts REAL NOT NULL,
                      event TEXT NOT NULL,
                      payload_json TEXT NOT NULL
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_notifications_ts ON notifications(ts DESC)")

                # V2: market context snapshots (quotes/bars/spread info) used for planning + audits.
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS market_snapshots (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      symbol TEXT NOT NULL,
                      ts REAL NOT NULL,
                      snapshot_json TEXT NOT NULL
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_market_symbol_ts ON market_snapshots(symbol, ts DESC)")

                # V2: research packets (e.g., FinanceOffice summaries) used for planning + audits.
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS research_packets (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      symbol TEXT,
                      ts REAL NOT NULL,
                      source TEXT NOT NULL,
                      packet_json TEXT NOT NULL
                    )
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_research_symbol_ts ON research_packets(symbol, ts DESC)")
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Dedupe
    # ------------------------------------------------------------------
    def dedupe_check_and_set(self, *, key: str, ttl_s: float) -> bool:
        """
        Return True if the key is new (or expired and replaced), False if still active.
        """
        k = str(key or "").strip()
        if not k:
            return True  # don't block on empty keys
        now = _utc_ts()
        exp = now + float(ttl_s or 0.0)
        conn = self._connect()
        try:
            cur = conn.cursor()
            # prune expired keys (small table, cheap)
            cur.execute("DELETE FROM dedupe_keys WHERE expires_at <= ?", (now,))
            # try insert
            try:
                cur.execute("INSERT INTO dedupe_keys(key, expires_at) VALUES(?, ?)", (k, exp))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                # Exists and not expired (we just pruned expired)
                return False
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def insert_event(self, *, ts: float, source: str, text: str, text_hash: str, symbols: List[str]) -> StoredEvent:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO events(ts, source, text, text_hash, symbols_json) VALUES(?, ?, ?, ?, ?)",
                (float(ts), str(source), str(text), str(text_hash), json.dumps(list(symbols or []))),
            )
            eid = int(cur.lastrowid or 0)
            conn.commit()
            return StoredEvent(event_id=eid, ts=float(ts), source=str(source), text=str(text), symbols=list(symbols or []), text_hash=str(text_hash))
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Scores
    # ------------------------------------------------------------------
    def get_global_score(self, key: str, default: float = 0.0) -> float:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT value FROM scores_global WHERE key = ?", (str(key),))
            row = cur.fetchone()
            if not row:
                return float(default)
            return float(row[0] or 0.0)
        finally:
            conn.close()

    def set_global_score(self, *, key: str, value: float) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO scores_global(key, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (str(key), float(value), _utc_ts()),
            )
            conn.commit()
        finally:
            conn.close()

    def get_watch_score(self, symbol: str, default: float = 0.0) -> float:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT value FROM scores_watch WHERE symbol = ?", (str(symbol),))
            row = cur.fetchone()
            if not row:
                return float(default)
            return float(row[0] or 0.0)
        finally:
            conn.close()

    def set_watch_score(self, *, symbol: str, value: float) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO scores_watch(symbol, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (str(symbol), float(value), _utc_ts()),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------
    def insert_task(self, *, symbol: str, priority: float, reason: str, strategy_hint: Optional[str]) -> int:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO tasks(symbol, priority, reason, strategy_hint, created_at, status) VALUES(?, ?, ?, ?, ?, ?)",
                (str(symbol), float(priority), str(reason), (str(strategy_hint) if strategy_hint else None), _utc_ts(), "queued"),
            )
            tid = int(cur.lastrowid or 0)
            conn.commit()
            return tid
        finally:
            conn.close()

    def list_tasks(self, *, status: str = "queued", limit: int = 50) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, symbol, priority, reason, strategy_hint, created_at, status FROM tasks WHERE status=? ORDER BY priority DESC, created_at ASC LIMIT ?",
                (str(status), int(limit)),
            )
            out: List[Dict[str, Any]] = []
            for row in cur.fetchall() or []:
                out.append(
                    {
                        "id": int(row[0]),
                        "symbol": str(row[1]),
                        "priority": float(row[2] or 0.0),
                        "reason": str(row[3] or ""),
                        "strategy_hint": (str(row[4]) if row[4] is not None else None),
                        "created_at": float(row[5] or 0.0),
                        "status": str(row[6] or ""),
                    }
                )
            return out
        finally:
            conn.close()

    def set_task_status(self, *, task_id: int, status: str) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE tasks SET status=? WHERE id=?", (str(status), int(task_id)))
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Trade plans
    # ------------------------------------------------------------------
    def insert_trade_plan(self, *, symbol: str, strategy_id: str, account_id: str, side: str, confidence: float, plan: Dict[str, Any]) -> int:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO trade_plans(symbol, strategy_id, account_id, side, confidence, plan_json, created_at, status) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(symbol),
                    str(strategy_id),
                    str(account_id),
                    str(side),
                    float(confidence),
                    json.dumps(plan),
                    _utc_ts(),
                    "created",
                ),
            )
            pid = int(cur.lastrowid or 0)
            conn.commit()
            return pid
        finally:
            conn.close()

    def set_trade_plan_status(self, *, plan_id: int, status: str) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE trade_plans SET status=? WHERE id=?", (str(status), int(plan_id)))
            conn.commit()
        finally:
            conn.close()

    def add_trade_plan_audit(self, *, plan_id: int, event: str, detail: Dict[str, Any]) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO trade_plan_audit(plan_id, ts, event, detail_json) VALUES(?, ?, ?, ?)",
                (int(plan_id), _utc_ts(), str(event), json.dumps(detail)),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Orders + reconciliation snapshots
    # ------------------------------------------------------------------
    def get_order_by_plan_id(self, *, plan_id: int) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT plan_id, account_id, client_order_id, alpaca_order_id, symbol, side, notional_usd_hint, status, raw_output FROM orders WHERE plan_id=?",
                (int(plan_id),),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "plan_id": int(row[0]),
                "account_id": str(row[1]),
                "client_order_id": str(row[2]),
                "alpaca_order_id": (str(row[3]) if row[3] is not None else None),
                "symbol": str(row[4]),
                "side": str(row[5]),
                "notional_usd_hint": (float(row[6]) if row[6] is not None else None),
                "status": str(row[7]),
                "raw_output": (str(row[8]) if row[8] is not None else None),
            }
        finally:
            conn.close()

    def insert_order_stub(
        self,
        *,
        plan_id: int,
        account_id: str,
        client_order_id: str,
        symbol: str,
        side: str,
        notional_usd_hint: Optional[float],
        status: str,
    ) -> None:
        now = _utc_ts()
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO orders(plan_id, account_id, client_order_id, alpaca_order_id, symbol, side, notional_usd_hint,
                                   status, raw_output, parsed_json, created_at, updated_at)
                VALUES(?, ?, ?, NULL, ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    int(plan_id),
                    str(account_id),
                    str(client_order_id),
                    str(symbol),
                    str(side),
                    (float(notional_usd_hint) if notional_usd_hint is not None else None),
                    str(status),
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def update_order_execution_output(self, *, plan_id: int, raw_output: str) -> None:
        now = _utc_ts()
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE orders SET raw_output=?, updated_at=? WHERE plan_id=?",
                (str(raw_output), now, int(plan_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def finalize_order_submission(self, *, plan_id: int, alpaca_order_id: Optional[str], status: str, parsed_json: Dict[str, Any]) -> None:
        now = _utc_ts()
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE orders SET alpaca_order_id=?, status=?, parsed_json=?, updated_at=? WHERE plan_id=?",
                (
                    (str(alpaca_order_id) if alpaca_order_id else None),
                    str(status),
                    json.dumps(parsed_json),
                    now,
                    int(plan_id),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def insert_reconcile_snapshot(self, *, account_id: str, snapshot: Dict[str, Any]) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO reconcile_snapshots(account_id, ts, snapshot_json) VALUES(?, ?, ?)",
                (str(account_id), _utc_ts(), json.dumps(snapshot)),
            )
            conn.commit()
        finally:
            conn.close()

        # Best-effort also split positions/open_orders into dedicated tables if present.
        try:
            if isinstance(snapshot, dict):
                if "positions" in snapshot:
                    self.insert_positions_snapshot(account_id=account_id, positions=snapshot.get("positions"))
                if "open_orders" in snapshot:
                    self.insert_orders_snapshot(account_id=account_id, orders=snapshot.get("open_orders"))
        except Exception:
            pass

    def insert_positions_snapshot(self, *, account_id: str, positions: Any) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO positions_snapshots(account_id, ts, positions_json) VALUES(?, ?, ?)",
                (str(account_id), _utc_ts(), json.dumps(positions)),
            )
            conn.commit()
        finally:
            conn.close()

    def insert_orders_snapshot(self, *, account_id: str, orders: Any) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO orders_snapshots(account_id, ts, orders_json) VALUES(?, ?, ?)",
                (str(account_id), _utc_ts(), json.dumps(orders)),
            )
            conn.commit()
        finally:
            conn.close()

    def insert_fill(
        self,
        *,
        plan_id: Optional[int],
        client_order_id: Optional[str],
        alpaca_order_id: Optional[str],
        fill: Dict[str, Any],
        ts: Optional[float] = None,
    ) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO fills(plan_id, client_order_id, alpaca_order_id, ts, fill_json) VALUES(?, ?, ?, ?, ?)",
                (
                    (int(plan_id) if plan_id is not None else None),
                    (str(client_order_id) if client_order_id else None),
                    (str(alpaca_order_id) if alpaca_order_id else None),
                    float(ts) if ts is not None else _utc_ts(),
                    json.dumps(fill),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def upsert_daily_summary(self, *, date: str, account_id: str, summary: Dict[str, Any]) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO daily_summaries(date, account_id, summary_json, created_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(date, account_id) DO UPDATE SET summary_json=excluded.summary_json, created_at=excluded.created_at
                """,
                (str(date), str(account_id), json.dumps(summary), _utc_ts()),
            )
            conn.commit()
        finally:
            conn.close()

    def insert_notification(self, *, event: str, payload: Dict[str, Any]) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO notifications(ts, event, payload_json) VALUES(?, ?, ?)",
                (_utc_ts(), str(event), json.dumps(payload)),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # V2: market + research packets
    # ------------------------------------------------------------------
    def insert_market_snapshot(self, *, symbol: str, snapshot: Dict[str, Any], ts: Optional[float] = None) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO market_snapshots(symbol, ts, snapshot_json) VALUES(?, ?, ?)",
                (str(symbol).upper(), float(ts) if ts is not None else _utc_ts(), json.dumps(snapshot)),
            )
            conn.commit()
        finally:
            conn.close()

    def insert_research_packet(self, *, symbol: Optional[str], source: str, packet: Dict[str, Any], ts: Optional[float] = None) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO research_packets(symbol, ts, source, packet_json) VALUES(?, ?, ?, ?)",
                (
                    (str(symbol).upper() if symbol else None),
                    float(ts) if ts is not None else _utc_ts(),
                    str(source or "unknown"),
                    json.dumps(packet),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def compute_daily_summary(self, *, account_id: str, day_start_ts: float, day_end_ts: float) -> Dict[str, Any]:
        """
        Compute a lightweight daily summary from DB state.
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT status, COUNT(*) FROM trade_plans WHERE account_id=? AND created_at>=? AND created_at<? GROUP BY status",
                (str(account_id), float(day_start_ts), float(day_end_ts)),
            )
            plan_counts = {str(st): int(n or 0) for (st, n) in (cur.fetchall() or [])}
            cur.execute(
                "SELECT status, COUNT(*) FROM orders WHERE account_id=? AND created_at>=? AND created_at<? GROUP BY status",
                (str(account_id), float(day_start_ts), float(day_end_ts)),
            )
            order_counts = {str(st): int(n or 0) for (st, n) in (cur.fetchall() or [])}
            cur.execute(
                "SELECT COUNT(*) FROM fills WHERE ts>=? AND ts<? AND (alpaca_order_id IS NOT NULL OR client_order_id IS NOT NULL)",
                (float(day_start_ts), float(day_end_ts)),
            )
            fills_count = int((cur.fetchone() or [0])[0] or 0)
            return {
                "account_id": str(account_id),
                "plans": plan_counts,
                "orders": order_counts,
                "fills": {"count": fills_count},
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # V2 helpers
    # ------------------------------------------------------------------
    def get_latest_reconcile_snapshot(self, *, account_id: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT snapshot_json FROM reconcile_snapshots WHERE account_id=? ORDER BY ts DESC LIMIT 1",
                (str(account_id),),
            )
            row = cur.fetchone()
            if not row:
                return None
            try:
                j = json.loads(str(row[0] or ""))
            except Exception:
                j = None
            return j if isinstance(j, dict) else None
        finally:
            conn.close()

    def get_latest_market_snapshot(self, *, symbol: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT snapshot_json FROM market_snapshots WHERE symbol=? ORDER BY ts DESC LIMIT 1",
                (str(symbol).upper(),),
            )
            row = cur.fetchone()
            if not row:
                return None
            try:
                j = json.loads(str(row[0] or ""))
            except Exception:
                j = None
            return j if isinstance(j, dict) else None
        finally:
            conn.close()

    def sum_orders_notional(self, *, account_id: str, start_ts: float, end_ts: float) -> float:
        """
        Best-effort daily notional usage from orders table.

        Uses orders.notional_usd_hint (legacy name) as the executed/intended notional proxy.
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(SUM(COALESCE(notional_usd_hint, 0)), 0) FROM orders WHERE account_id=? AND created_at>=? AND created_at<?",
                (str(account_id), float(start_ts), float(end_ts)),
            )
            row = cur.fetchone() or [0]
            try:
                return float(row[0] or 0.0)
            except Exception:
                return 0.0
        finally:
            conn.close()


