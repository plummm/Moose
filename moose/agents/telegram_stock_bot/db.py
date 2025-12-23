import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Iterable, Any


def _utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@dataclass(frozen=True)
class ChatRow:
    chat_id: int
    chat_type: str
    title: Optional[str]
    timezone: str
    market_open_sent_date: Optional[str]
    market_close_sent_date: Optional[str]


class StockBotDB:
    """
    Small SQLite wrapper.
    - Uses a single connection with a lock to serialize access.
    - DB file path should be on a persistent volume (e.g., /data/db.sqlite).
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
        self.init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chats (
                  chat_id INTEGER PRIMARY KEY,
                  chat_type TEXT NOT NULL,
                  title TEXT,
                  timezone TEXT NOT NULL DEFAULT 'America/New_York',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  market_open_sent_date TEXT,
                  market_close_sent_date TEXT
                );

                CREATE TABLE IF NOT EXISTS watchlist (
                  chat_id INTEGER NOT NULL,
                  symbol TEXT NOT NULL,
                  asset_type TEXT NOT NULL CHECK(asset_type IN ('stock','crypto')),
                  created_at TEXT NOT NULL,
                  PRIMARY KEY (chat_id, symbol),
                  FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS daily_state (
                  chat_id INTEGER NOT NULL,
                  symbol TEXT NOT NULL,
                  day_key TEXT NOT NULL,
                  base_price REAL,
                  last_price REAL,
                  last_alert_threshold INTEGER,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (chat_id, symbol, day_key),
                  FOREIGN KEY(chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS chat_messages (
                  chat_id INTEGER NOT NULL,
                  message_id INTEGER NOT NULL,
                  date_ts INTEGER NOT NULL,
                  from_user_id INTEGER,
                  from_username TEXT,
                  from_is_bot INTEGER NOT NULL,
                  text TEXT NOT NULL,
                  reply_to_message_id INTEGER,
                  entities_json TEXT,
                  PRIMARY KEY (chat_id, message_id)
                );

                CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_ts
                  ON chat_messages(chat_id, date_ts);

                CREATE TABLE IF NOT EXISTS pending_flows (
                  chat_id INTEGER NOT NULL,
                  flow_id TEXT NOT NULL,
                  status TEXT NOT NULL,
                  created_at_ts INTEGER NOT NULL,
                  expires_at_ts INTEGER NOT NULL,
                  initiator_message_id INTEGER NOT NULL,
                  context_message_ids_json TEXT NOT NULL,
                  router_state_json TEXT NOT NULL,
                  clarification_message_id INTEGER,
                  expected_fields_json TEXT NOT NULL,
                  answers_json TEXT NOT NULL,
                  PRIMARY KEY (chat_id, flow_id)
                );
                """
            )
            self._conn.commit()

    def add_chat_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        date_ts: int,
        from_user_id: Optional[int],
        from_username: Optional[str],
        from_is_bot: bool,
        text: str,
        reply_to_message_id: Optional[int] = None,
        entities: Optional[list[dict[str, Any]]] = None,
        keep_last: int = 100,
    ) -> None:
        """
        Insert one message into rolling chat history and enforce retention.
        """
        entities_json = None
        try:
            if entities is not None:
                entities_json = json.dumps(entities, ensure_ascii=False)
        except Exception:
            entities_json = None

        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO chat_messages
                (chat_id, message_id, date_ts, from_user_id, from_username, from_is_bot, text, reply_to_message_id, entities_json)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    chat_id,
                    message_id,
                    date_ts,
                    from_user_id,
                    from_username,
                    1 if from_is_bot else 0,
                    text,
                    reply_to_message_id,
                    entities_json,
                ),
            )
            # Retention: keep newest N by date_ts
            self._conn.execute(
                """
                DELETE FROM chat_messages
                WHERE chat_id=?
                  AND message_id NOT IN (
                    SELECT message_id FROM chat_messages
                    WHERE chat_id=?
                    ORDER BY date_ts DESC
                    LIMIT ?
                  )
                """,
                (chat_id, chat_id, int(keep_last)),
            )
            self._conn.commit()

    def get_context_window_before(
        self,
        *,
        chat_id: int,
        before_date_ts: int,
        limit: int = 10,
        max_age_seconds: int = 5 * 60,
    ) -> list[sqlite3.Row]:
        """
        Return up to N messages prior to the given timestamp, ordered oldest->newest.
        """
        min_date_ts = int(before_date_ts) - int(max_age_seconds)
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE chat_id=?
                  AND date_ts < ?
                  AND date_ts >= ?
                ORDER BY date_ts DESC
                LIMIT ?
                """,
                (chat_id, int(before_date_ts), int(min_date_ts), int(limit)),
            )
            rows = cur.fetchall()
        return list(reversed(rows))

    def create_pending_flow(
        self,
        *,
        chat_id: int,
        flow_id: str,
        status: str,
        created_at_ts: int,
        expires_at_ts: int,
        initiator_message_id: int,
        context_message_ids: list[int],
        router_state: dict[str, Any],
        expected_fields: list[dict[str, Any]],
        answers: dict[str, Any],
        clarification_message_id: Optional[int] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO pending_flows
                (chat_id, flow_id, status, created_at_ts, expires_at_ts, initiator_message_id,
                 context_message_ids_json, router_state_json, clarification_message_id, expected_fields_json, answers_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    chat_id,
                    flow_id,
                    status,
                    created_at_ts,
                    expires_at_ts,
                    initiator_message_id,
                    json.dumps(context_message_ids),
                    json.dumps(router_state, ensure_ascii=False),
                    clarification_message_id,
                    json.dumps(expected_fields, ensure_ascii=False),
                    json.dumps(answers, ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def get_pending_flow_by_clarification_message(
        self, chat_id: int, clarification_message_id: int
    ) -> Optional[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT * FROM pending_flows
                WHERE chat_id=? AND clarification_message_id=?
                """,
                (chat_id, clarification_message_id),
            )
            return cur.fetchone()

    def update_pending_flow(
        self,
        *,
        chat_id: int,
        flow_id: str,
        status: Optional[str] = None,
        router_state: Optional[dict[str, Any]] = None,
        expected_fields: Optional[list[dict[str, Any]]] = None,
        answers: Optional[dict[str, Any]] = None,
        clarification_message_id: Optional[int] = None,
    ) -> None:
        fields = []
        vals: list[Any] = []
        if status is not None:
            fields.append("status=?")
            vals.append(status)
        if router_state is not None:
            fields.append("router_state_json=?")
            vals.append(json.dumps(router_state, ensure_ascii=False))
        if expected_fields is not None:
            fields.append("expected_fields_json=?")
            vals.append(json.dumps(expected_fields, ensure_ascii=False))
        if answers is not None:
            fields.append("answers_json=?")
            vals.append(json.dumps(answers, ensure_ascii=False))
        if clarification_message_id is not None:
            fields.append("clarification_message_id=?")
            vals.append(clarification_message_id)
        if not fields:
            return
        vals.extend([chat_id, flow_id])
        with self._lock:
            self._conn.execute(
                f"UPDATE pending_flows SET {', '.join(fields)} WHERE chat_id=? AND flow_id=?",
                tuple(vals),
            )
            self._conn.commit()

    def get_pending_flow(self, chat_id: int, flow_id: str) -> Optional[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM pending_flows WHERE chat_id=? AND flow_id=?",
                (chat_id, flow_id),
            )
            return cur.fetchone()

    def upsert_chat(self, chat_id: int, chat_type: str, title: Optional[str]) -> None:
        now = _utc_now_iso()
        with self._lock:
            cur = self._conn.execute("SELECT chat_id FROM chats WHERE chat_id = ?", (chat_id,))
            exists = cur.fetchone() is not None
            if exists:
                self._conn.execute(
                    "UPDATE chats SET chat_type=?, title=?, updated_at=? WHERE chat_id=?",
                    (chat_type, title, now, chat_id),
                )
            else:
                self._conn.execute(
                    "INSERT INTO chats (chat_id, chat_type, title, timezone, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    (chat_id, chat_type, title, "America/New_York", now, now),
                )
            self._conn.commit()

    def get_chat(self, chat_id: int) -> Optional[ChatRow]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,))
            row = cur.fetchone()
        if not row:
            return None
        return ChatRow(
            chat_id=int(row["chat_id"]),
            chat_type=str(row["chat_type"]),
            title=row["title"],
            timezone=str(row["timezone"]),
            market_open_sent_date=row["market_open_sent_date"],
            market_close_sent_date=row["market_close_sent_date"],
        )

    def set_timezone(self, chat_id: int, timezone: str) -> None:
        now = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                "UPDATE chats SET timezone=?, updated_at=? WHERE chat_id=?",
                (timezone, now, chat_id),
            )
            self._conn.commit()

    def set_market_open_sent(self, chat_id: int, day: str) -> None:
        now = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                "UPDATE chats SET market_open_sent_date=?, updated_at=? WHERE chat_id=?",
                (day, now, chat_id),
            )
            self._conn.commit()

    def set_market_close_sent(self, chat_id: int, day: str) -> None:
        now = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                "UPDATE chats SET market_close_sent_date=?, updated_at=? WHERE chat_id=?",
                (day, now, chat_id),
            )
            self._conn.commit()

    def list_chats(self) -> list[ChatRow]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM chats")
            rows = cur.fetchall()
        out: list[ChatRow] = []
        for r in rows:
            out.append(
                ChatRow(
                    chat_id=int(r["chat_id"]),
                    chat_type=str(r["chat_type"]),
                    title=r["title"],
                    timezone=str(r["timezone"]),
                    market_open_sent_date=r["market_open_sent_date"],
                    market_close_sent_date=r["market_close_sent_date"],
                )
            )
        return out

    def add_to_watchlist(self, chat_id: int, symbol: str, asset_type: str) -> bool:
        """
        Returns True if inserted; False if already existed.
        """
        now = _utc_now_iso()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO watchlist (chat_id, symbol, asset_type, created_at) VALUES (?,?,?,?)",
                    (chat_id, symbol, asset_type, now),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def remove_from_watchlist(self, chat_id: int, symbol: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM watchlist WHERE chat_id=? AND symbol=?",
                (chat_id, symbol),
            )
            self._conn.commit()
            return (cur.rowcount or 0) > 0

    def list_watchlist(self, chat_id: int) -> list[tuple[str, str]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT symbol, asset_type FROM watchlist WHERE chat_id=? ORDER BY symbol ASC",
                (chat_id,),
            )
            rows = cur.fetchall()
        return [(str(r["symbol"]), str(r["asset_type"])) for r in rows]

    def get_daily_state(
        self, chat_id: int, symbol: str, day_key: str
    ) -> Optional[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM daily_state WHERE chat_id=? AND symbol=? AND day_key=?",
                (chat_id, symbol, day_key),
            )
            return cur.fetchone()

    def upsert_daily_state(
        self,
        chat_id: int,
        symbol: str,
        day_key: str,
        *,
        base_price: Optional[float] = None,
        last_price: Optional[float] = None,
        last_alert_threshold: Optional[int] = None,
    ) -> None:
        now = _utc_now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO daily_state (chat_id, symbol, day_key, base_price, last_price, last_alert_threshold, updated_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(chat_id, symbol, day_key) DO UPDATE SET
                  base_price=COALESCE(excluded.base_price, daily_state.base_price),
                  last_price=COALESCE(excluded.last_price, daily_state.last_price),
                  last_alert_threshold=COALESCE(excluded.last_alert_threshold, daily_state.last_alert_threshold),
                  updated_at=excluded.updated_at
                """,
                (chat_id, symbol, day_key, base_price, last_price, last_alert_threshold, now),
            )
            self._conn.commit()


