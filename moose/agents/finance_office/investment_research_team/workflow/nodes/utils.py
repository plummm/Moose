from __future__ import annotations

import os
import sqlite3
import json
from typing import Any, Dict, Optional
from pathlib import Path


def extract_json(text: str) -> Optional[dict]:
    s = (text or "").strip()
    if not s:
        return None
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except Exception:
        return None


def json_decode_error(text: str) -> str:
    """
    Best-effort JSON error message for LLM outputs.
    We intentionally keep extraction rules aligned with `extract_json`.
    """
    s = (text or "").strip()
    if not s:
        return "Empty output."
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return "No JSON object boundaries found (missing '{' or '}')."
    try:
        json.loads(s[start : end + 1])
        return "Unknown JSON error (parsed successfully in diagnostic)."
    except Exception as e:
        return f"{type(e).__name__}: {e}"


async def repair_json_once(
    llm_client: Any,
    *,
    bad_output: str,
    error_hint: str,
) -> Any:
    """
    Ask the model to re-emit a STRICT JSON object, given its previous invalid output.
    Minimal-context: we only provide the invalid output and a parse error hint.
    Returns the new LLM response object (provider-specific type).
    """
    repair_system_message = (
        "You are a JSON repair tool.\n"
        "CRITICAL OUTPUT REQUIREMENT:\n"
        "- Return ONLY a single valid JSON object (no markdown fences, no leading/trailing quotes, no commentary).\n"
        "- JSON strings MUST be valid: do not include raw double quotes (\") inside string values.\n"
        "  If you need to quote text, use \\\" ... \\\" or use Chinese quotes 「...」.\n"
        "- Use \\n for newlines inside string values.\n"
    )
    repair_user_message = (
        "Your previous output was invalid JSON and could not be parsed.\n"
        f"Parser error: {error_hint}\n\n"
        "Fix the INVALID OUTPUT below so it becomes strict valid JSON.\n"
        "Keep the same keys/structure and preserve content as much as possible.\n\n"
        "INVALID OUTPUT:\n"
        + str(bad_output or "")
        + "\n\nNow output the corrected JSON object ONLY."
    )
    return await llm_client.send_message(message=repair_user_message, system_message=repair_system_message)


def normalize_usage(u: Any) -> Dict[str, int]:
    if not isinstance(u, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    try:
        it = int(u.get("input_tokens", 0) or 0)
        ot = int(u.get("output_tokens", 0) or 0)
        tt = int(u.get("total_tokens", it + ot) or (it + ot))
    except Exception:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return {"input_tokens": max(0, it), "output_tokens": max(0, ot), "total_tokens": max(0, tt)}


def raw_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    snap = dict(state or {})
    # Avoid recursive embedding if caller already set an envelope
    if isinstance(snap.get("final"), dict) and set(snap["final"].keys()) >= {"ok", "error", "result", "raw"}:
        snap.pop("final", None)
    return snap


def shrink_for_state(x: Any, *, max_str: int = 2000, max_list: int = 50, max_depth: int = 4) -> Any:
    """
    Keep objects small so it is safe to carry through LangGraph state.
    - Truncates long strings
    - Limits list sizes
    - Limits recursion depth
    """
    if max_depth <= 0:
        return None
    if isinstance(x, str):
        s = x.strip()
        return s if len(s) <= max_str else (s[:max_str] + "…")
    if isinstance(x, (int, float, bool)) or x is None:
        return x
    if isinstance(x, dict):
        out: Dict[str, Any] = {}
        for k, v in x.items():
            kk = str(k)
            out[kk] = shrink_for_state(v, max_str=max_str, max_list=max_list, max_depth=max_depth - 1)
        return out
    if isinstance(x, list):
        trimmed = x[:max_list]
        return [shrink_for_state(v, max_str=max_str, max_list=max_list, max_depth=max_depth - 1) for v in trimmed]
    # Fallback: stringify unknown types
    try:
        return shrink_for_state(str(x), max_str=max_str, max_list=max_list, max_depth=max_depth - 1)
    except Exception:
        return None


#### SQLite DB utils ####

def get_db_path(*, base_news_dir: str) -> Path:
    """
    Return the sqlite db path for storing memory snapshots.

    Default: {NEWS_RESULT_DIR}/db.sqlite
    Optional override: NEWS_MEMORY_DB_PATH
    """
    override = str(os.getenv("NEWS_MEMORY_DB_PATH") or "").strip()
    if override:
        return Path(override)
    return Path(base_news_dir) / "db.sqlite"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    # Best-effort concurrency settings for multi-process writers
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            year TEXT NOT NULL,
            month TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sentiment TEXT,
            sentiment_number REAL,
            memory_weight REAL,
            memory_weight_ratio REAL,
            memory_file_path TEXT,
            memory_json TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_current (
            ticker TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sentiment TEXT,
            sentiment_number REAL,
            memory_weight REAL,
            memory_weight_ratio REAL,
            memory_file_path TEXT,
            memory_json TEXT NOT NULL,
            PRIMARY KEY (ticker)
        );
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mem_snap_ticker_updated_at ON memory_snapshot(ticker, updated_at);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mem_snap_ticker_year_month ON memory_snapshot(ticker, year, month);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mem_current_ticker_updated_at ON memory_current(ticker, updated_at);"
    )

    # Best-effort forward migration from older schema (monthly_memory_current) if present.
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='monthly_memory_current' LIMIT 1;"
        ).fetchone()
        if row:
            # For each ticker, pick the latest updated_at row across months and store it in memory_current.
            conn.execute(
                """
                INSERT INTO memory_current (
                    ticker, updated_at,
                    sentiment, sentiment_number, memory_weight, memory_weight_ratio,
                    memory_file_path, memory_json
                )
                SELECT
                    t.ticker, t.updated_at,
                    t.sentiment, t.sentiment_number, t.memory_weight, t.memory_weight_ratio,
                    t.memory_file_path, t.memory_json
                FROM monthly_memory_current t
                INNER JOIN (
                    SELECT ticker, MAX(updated_at) AS max_updated_at
                    FROM monthly_memory_current
                    GROUP BY ticker
                ) latest
                ON latest.ticker = t.ticker AND latest.max_updated_at = t.updated_at
                ON CONFLICT(ticker) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    sentiment=excluded.sentiment,
                    sentiment_number=excluded.sentiment_number,
                    memory_weight=excluded.memory_weight,
                    memory_weight_ratio=excluded.memory_weight_ratio,
                    memory_file_path=excluded.memory_file_path,
                    memory_json=excluded.memory_json
                """
            )
    except Exception:
        # Never fail schema creation due to migration issues.
        pass


def _to_float(val: Any) -> Optional[float]:
    try:
        if val is None:
            return None
        return float(val)
    except Exception:
        return None


def append_snapshot(
    *,
    db_path: Path,
    ticker: str,
    year: str,
    month: str,
    updated_at: str,
    mem_path: Path,
    memory_obj: dict,
    logger: Any = None,
    fail_closed: bool = False,
) -> None:
    """
    Append one snapshot row to sqlite. Best-effort; callers should treat failures as non-fatal.
    """
    if not isinstance(memory_obj, dict):
        return

    params = memory_obj.get("parameters") if isinstance(memory_obj.get("parameters"), dict) else {}
    sentiment = memory_obj.get("sentiment")
    sentiment_number = _to_float(params.get("sentiment_number"))
    memory_weight = _to_float(params.get("memory_weight"))
    memory_weight_ratio = _to_float(memory_obj.get("memory_weight_ratio"))

    memory_json = json.dumps(memory_obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _connect(db_path)
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO memory_snapshot (
                ticker, year, month, updated_at,
                sentiment, sentiment_number, memory_weight, memory_weight_ratio,
                memory_file_path, memory_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(ticker or "").strip().upper(),
                str(year or "").strip(),
                str(month or "").strip(),
                str(updated_at or "").strip(),
                str(sentiment) if sentiment is not None else None,
                sentiment_number,
                memory_weight,
                memory_weight_ratio,
                str(mem_path),
                memory_json,
            ),
        )
        conn.commit()
    except Exception as e:
        if fail_closed:
            raise
        try:
            if logger:
                logger.warning(f"SQLite snapshot append failed for {ticker}: {e}")
        except Exception:
            pass
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def upsert_current_memory(
    *,
    db_path: Path,
    ticker: str,
    updated_at: str,
    mem_path: Path,
    memory_obj: dict,
    logger: Any = None,
    fail_closed: bool = False,
) -> None:
    """
    Upsert the latest (current) memory for ticker (global carry-over across months).
    Best-effort; callers should treat failures as non-fatal.
    """
    if not isinstance(memory_obj, dict):
        return

    params = memory_obj.get("parameters") if isinstance(memory_obj.get("parameters"), dict) else {}
    sentiment = memory_obj.get("sentiment")
    sentiment_number = _to_float(params.get("sentiment_number"))
    memory_weight = _to_float(params.get("memory_weight"))
    memory_weight_ratio = _to_float(memory_obj.get("memory_weight_ratio"))

    memory_json = json.dumps(memory_obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _connect(db_path)
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO memory_current (
                ticker, updated_at,
                sentiment, sentiment_number, memory_weight, memory_weight_ratio,
                memory_file_path, memory_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                updated_at=excluded.updated_at,
                sentiment=excluded.sentiment,
                sentiment_number=excluded.sentiment_number,
                memory_weight=excluded.memory_weight,
                memory_weight_ratio=excluded.memory_weight_ratio,
                memory_file_path=excluded.memory_file_path,
                memory_json=excluded.memory_json
            """,
            (
                str(ticker or "").strip().upper(),
                str(updated_at or "").strip(),
                str(sentiment) if sentiment is not None else None,
                sentiment_number,
                memory_weight,
                memory_weight_ratio,
                str(mem_path),
                memory_json,
            ),
        )
        conn.commit()
    except Exception as e:
        if fail_closed:
            raise
        try:
            if logger:
                logger.warning(f"SQLite current-memory upsert failed for {ticker}: {e}")
        except Exception:
            pass
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def load_current_memory(
    *,
    db_path: Path,
    ticker: str,
    logger: Any = None,
    fail_closed: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Load the current memory for ticker from sqlite.
    Returns None if missing or on error.
    """
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _connect(db_path)
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT memory_json
            FROM memory_current
            WHERE ticker=?
            """,
            (str(ticker or "").strip().upper(),),
        ).fetchone()
        if not row:
            return None
        try:
            obj = json.loads(row[0])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    except Exception as e:
        if fail_closed:
            raise
        try:
            if logger:
                logger.warning(f"SQLite current-memory load failed for {ticker}: {e}")
        except Exception:
            pass
        return None
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def load_current_memories(
    *,
    db_path: Path,
    tickers: list[str],
    logger: Any = None,
    fail_closed: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """
    Bulk load current memories for many tickers.
    Returns mapping ticker->memory_obj for those found.
    """
    out: Dict[str, Dict[str, Any]] = {}
    items = [str(t or "").strip().upper() for t in (tickers or []) if str(t or "").strip()]
    if not items:
        return out

    # SQLite has a default variable limit; keep it conservative.
    CHUNK = 200
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _connect(db_path)
        _ensure_schema(conn)
        for i in range(0, len(items), CHUNK):
            chunk = items[i : i + CHUNK]
            placeholders = ",".join(["?"] * len(chunk))
            rows = conn.execute(
                f"""
                SELECT ticker, memory_json
                FROM memory_current
                WHERE ticker IN ({placeholders})
                """,
                [*chunk],
            ).fetchall()
            for t, mem_json in rows or []:
                try:
                    obj = json.loads(mem_json)
                    if isinstance(obj, dict):
                        out[str(t).strip().upper()] = obj
                except Exception:
                    continue
        return out
    except Exception as e:
        if fail_closed:
            raise
        try:
            if logger:
                logger.warning(f"SQLite current-memory bulk load failed: {e}")
        except Exception:
            pass
        return out
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


