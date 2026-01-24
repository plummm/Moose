"""
Per-project SQLite trace store for Moose tracing.

This module implements:
- schema creation/migrations
- a background writer thread (non-blocking for callers)
- a SpanExporter that consumes spans from Moose tracing

DB location:
  projects/<project_id>/logs/trace.db
"""

from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from moose.framework.logging.tracing import Span, SpanExporter, register_exporter


SCHEMA_VERSION = 3


def _json_dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        try:
            return json.dumps(str(obj), ensure_ascii=False)
        except Exception:
            return "{}"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=30000;")

    row = conn.execute("PRAGMA user_version;").fetchone()
    user_version = int(row[0] or 0) if row else 0

    if user_version < 1:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS traces (
              request_id TEXT PRIMARY KEY,
              project_id TEXT,
              started_at TEXT,
              root_agent TEXT,
              root_kind TEXT,
              status TEXT,
              attrs_json TEXT
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS spans (
              span_id TEXT PRIMARY KEY,
              request_id TEXT NOT NULL,
              parent_span_id TEXT,
              kind TEXT,
              name TEXT,
              start_ts REAL,
              end_ts REAL,
              status TEXT,
              error TEXT,
              attrs_json TEXT,
              project_id TEXT,
              agent_name TEXT
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_request_start ON spans(request_id, start_ts);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_request_parent ON spans(request_id, parent_span_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_kind ON spans(kind);")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_calls (
              span_id TEXT PRIMARY KEY,
              agent_name TEXT,
              node_name TEXT,
              model TEXT,
              provider TEXT,
              usage_json TEXT,
              cost REAL,
              error TEXT
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_calls_agent ON llm_calls(agent_name);")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              span_id TEXT NOT NULL,
              role TEXT,
              idx INTEGER,
              content TEXT,
              name TEXT,
              tool_call_id TEXT,
              tool_calls_json TEXT
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_messages_span_idx ON llm_messages(span_id, idx);")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_media (
              media_id TEXT PRIMARY KEY,
              span_id TEXT,
              mime_type TEXT,
              data BLOB,
              created_at REAL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_media_span ON llm_media(span_id);")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS http_calls (
              span_id TEXT PRIMARY KEY,
              method TEXT,
              url TEXT,
              status_code INTEGER,
              peer_agent TEXT,
              request_json TEXT,
              response_json TEXT,
              error TEXT
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_http_calls_peer ON http_calls(peer_agent);")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_calls (
              span_id TEXT PRIMARY KEY,
              tool_name TEXT,
              tool_call_id TEXT,
              args_json TEXT,
              result_json TEXT,
              error TEXT
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_calls_name ON tool_calls(tool_name);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_calls_call_id ON tool_calls(tool_call_id);")

        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION};")
        conn.commit()
    elif user_version < 2:
        # Add tool_call_id for linking tool calls to LLM tool_call_id.
        try:
            conn.execute("ALTER TABLE tool_calls ADD COLUMN tool_call_id TEXT;")
        except Exception:
            pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_calls_call_id ON tool_calls(tool_call_id);")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION};")
        conn.commit()
    elif user_version < 3:
        # Media attachments for multimodal messages.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_media (
              media_id TEXT PRIMARY KEY,
              span_id TEXT,
              mime_type TEXT,
              data BLOB,
              created_at REAL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_media_span ON llm_media(span_id);")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION};")
        conn.commit()


class _TraceDbWriter(threading.Thread):
    def __init__(self, db_path: Path):
        super().__init__(name="trace_db_writer", daemon=True)
        self.db_path = Path(db_path)
        self.q: "queue.Queue[Tuple[str, Dict[str, Any]]]" = queue.Queue(maxsize=20000)
        self._stop = threading.Event()
        self._conn: Optional[sqlite3.Connection] = None
        self._llm_next_idx: Dict[str, int] = {}

    def stop(self) -> None:
        self._stop.set()

    def enqueue(self, kind: str, payload: Dict[str, Any]) -> None:
        try:
            self.q.put_nowait((kind, payload))
        except queue.Full:
            pass

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        _ensure_schema(conn)
        return conn

    def run(self) -> None:
        try:
            self._conn = self._connect()
        except Exception:
            self._conn = None
            return

        conn = self._conn
        while not self._stop.is_set():
            try:
                kind, payload = self.q.get(timeout=0.25)
            except queue.Empty:
                continue

            try:
                if kind == "span_start":
                    _db_span_start(conn, payload)
                elif kind == "span_end":
                    _db_span_end(conn, payload)
                elif kind == "llm_message":
                    _db_llm_message(conn, payload, next_idx_map=self._llm_next_idx)
                elif kind == "llm_media":
                    _db_llm_media(conn, payload)
                elif kind == "llm_call_update":
                    _db_llm_call_update(conn, payload)
                elif kind == "tool_call":
                    _db_tool_call(conn, payload)
            except Exception:
                pass
            finally:
                try:
                    self.q.task_done()
                except Exception:
                    pass

        try:
            conn.commit()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _db_ensure_trace(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    project_id: str,
    root_agent: str,
    root_kind: str,
    started_at_iso: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO traces(request_id, project_id, started_at, root_agent, root_kind, status, attrs_json)
        VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        (request_id, project_id, started_at_iso, root_agent, root_kind, "running", "{}"),
    )


def _db_span_start(conn: sqlite3.Connection, payload: Dict[str, Any]) -> None:
    try:
        _db_ensure_trace(
            conn,
            request_id=str(payload.get("request_id") or ""),
            project_id=str(payload.get("project_id") or ""),
            root_agent=str(payload.get("agent_name") or ""),
            root_kind=str(payload.get("kind") or ""),
            started_at_iso=str(payload.get("trace_started_at") or ""),
        )
    except Exception:
        pass

    conn.execute(
        """
        INSERT OR REPLACE INTO spans(
          span_id, request_id, parent_span_id, kind, name, start_ts, end_ts, status, error, attrs_json, project_id, agent_name
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            payload.get("span_id"),
            payload.get("request_id"),
            payload.get("parent_span_id"),
            payload.get("kind"),
            payload.get("name"),
            payload.get("start_ts"),
            None,
            "running",
            None,
            payload.get("attrs_json") or "{}",
            payload.get("project_id"),
            payload.get("agent_name"),
        ),
    )
    conn.commit()


def _db_span_end(conn: sqlite3.Connection, payload: Dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE spans
        SET end_ts = ?, status = ?, error = ?, attrs_json = ?
        WHERE span_id = ?;
        """,
        (
            payload.get("end_ts"),
            payload.get("status") or "ok",
            payload.get("error"),
            payload.get("attrs_json") or "{}",
            payload.get("span_id"),
        ),
    )
    conn.commit()


def _db_llm_message(conn: sqlite3.Connection, payload: Dict[str, Any], *, next_idx_map: Dict[str, int]) -> None:
    span_id = str(payload.get("span_id") or "").strip()
    if not span_id:
        return
    idx = int(next_idx_map.get(span_id, 0) or 0)
    next_idx_map[span_id] = idx + 1
    content = payload.get("content")
    if isinstance(content, (list, dict)):
        content = _json_dumps(content)

    conn.execute(
        """
        INSERT INTO llm_messages(span_id, role, idx, content, name, tool_call_id, tool_calls_json)
        VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        (
            span_id,
            payload.get("role"),
            idx,
            content,
            payload.get("name"),
            payload.get("tool_call_id"),
            payload.get("tool_calls_json"),
        ),
    )
    conn.commit()


def _db_llm_call_update(conn: sqlite3.Connection, payload: Dict[str, Any]) -> None:
    span_id = str(payload.get("span_id") or "").strip()
    if not span_id:
        return

    conn.execute(
        """
        INSERT OR IGNORE INTO llm_calls(span_id, agent_name, node_name, model, provider, usage_json, cost, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            span_id,
            payload.get("agent_name"),
            payload.get("node_name"),
            payload.get("model"),
            payload.get("provider"),
            payload.get("usage_json"),
            payload.get("cost"),
            payload.get("error"),
        ),
    )
    conn.execute(
        """
        UPDATE llm_calls
        SET agent_name = COALESCE(?, agent_name),
            node_name = COALESCE(?, node_name),
            model = COALESCE(?, model),
            provider = COALESCE(?, provider),
            usage_json = COALESCE(?, usage_json),
            cost = COALESCE(?, cost),
            error = COALESCE(?, error)
        WHERE span_id = ?;
        """,
        (
            payload.get("agent_name"),
            payload.get("node_name"),
            payload.get("model"),
            payload.get("provider"),
            payload.get("usage_json"),
            payload.get("cost"),
            payload.get("error"),
            span_id,
        ),
    )
    conn.commit()


def _db_llm_media(conn: sqlite3.Connection, payload: Dict[str, Any]) -> None:
    media_id = str(payload.get("media_id") or "").strip()
    if not media_id:
        return
    data = payload.get("data")
    if data is None:
        return

    conn.execute(
        """
        INSERT OR REPLACE INTO llm_media(media_id, span_id, mime_type, data, created_at)
        VALUES (?, ?, ?, ?, ?);
        """,
        (
            media_id,
            payload.get("span_id"),
            payload.get("mime_type"),
            data,
            payload.get("created_at"),
        ),
    )
    conn.commit()


def _db_tool_call(conn: sqlite3.Connection, payload: Dict[str, Any]) -> None:
    span_id = str(payload.get("span_id") or "").strip()
    if not span_id:
        return
    conn.execute(
        """
        INSERT OR REPLACE INTO tool_calls(span_id, tool_name, tool_call_id, args_json, result_json, error)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (
            span_id,
            payload.get("tool_name"),
            payload.get("tool_call_id"),
            payload.get("args_json"),
            payload.get("result_json"),
            payload.get("error"),
        ),
    )
    conn.commit()


class SQLiteSpanExporter(SpanExporter):
    def __init__(self, *, project_id: str, db_path: Path, agent_name: Optional[str] = None):
        self.project_id = str(project_id or "").strip() or "default"
        self.db_path = Path(db_path)
        self.agent_name = str(agent_name or "").strip() or None
        self._writer = _TraceDbWriter(self.db_path)
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._writer.start()

    def on_span_start(self, span: Span) -> None:
        if not self._started:
            self.start()
        started_at_iso = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(span.start_ts))
        self._writer.enqueue(
            "span_start",
            {
                "span_id": span.span_id,
                "request_id": span.request_id,
                "parent_span_id": span.parent_span_id,
                "kind": span.kind,
                "name": span.name,
                "start_ts": float(span.start_ts),
                "attrs_json": _json_dumps(span.attrs or {}),
                "project_id": span.project_id or self.project_id,
                "agent_name": span.agent_name or self.agent_name,
                "trace_started_at": started_at_iso,
            },
        )

    def on_span_end(self, span: Span) -> None:
        if not self._started:
            self.start()
        self._writer.enqueue(
            "span_end",
            {
                "span_id": span.span_id,
                "end_ts": float(span.end_ts or time.time()),
                "status": span.status,
                "error": span.error,
                "attrs_json": _json_dumps(span.attrs or {}),
            },
        )


_EXPORTER_SINGLETON: Optional[SQLiteSpanExporter] = None


def init_trace_db(*, project_id: str, log_dir: Path, agent_name: Optional[str] = None) -> Optional[SQLiteSpanExporter]:
    global _EXPORTER_SINGLETON
    if _EXPORTER_SINGLETON is not None:
        return _EXPORTER_SINGLETON

    try:
        log_dir = Path(log_dir)
        db_path = log_dir / "trace.db"
        ex = SQLiteSpanExporter(project_id=project_id, db_path=db_path, agent_name=agent_name)
        register_exporter(ex)
        _EXPORTER_SINGLETON = ex
        return ex
    except Exception:
        return None


def enqueue_event(kind: str, payload: Dict[str, Any]) -> None:
    ex = _EXPORTER_SINGLETON
    if ex is None:
        return
    try:
        ex.start()
        ex._writer.enqueue(str(kind), payload if isinstance(payload, dict) else {})  # type: ignore[attr-defined]
    except Exception:
        return
