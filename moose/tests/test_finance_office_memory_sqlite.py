import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
import importlib.util
from pathlib import Path

def _load_utils_module():
    """
    Load the nodes utils module directly from file path to avoid importing
    moose.agents.finance_office (which may pull optional deps like langgraph).
    """
    root = Path(__file__).resolve().parents[1]  # .../moose
    mod_path = (
        root
        / "agents"
        / "finance_office"
        / "investment_research_team"
        / "workflow"
        / "nodes"
        / "utils.py"
    )
    spec = importlib.util.spec_from_file_location("finance_office_nodes_utils", mod_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module spec for: {mod_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_utils = _load_utils_module()
append_snapshot = _utils.append_snapshot
get_db_path = _utils.get_db_path
load_current_memory = _utils.load_current_memory
upsert_current_memory = _utils.upsert_current_memory


class TestFinanceOfficeMemorySQLite(unittest.TestCase):
    def test_append_snapshot_creates_db_and_appends(self) -> None:
        os.environ.pop("NEWS_MEMORY_DB_PATH", None)

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            db_path = get_db_path(base_news_dir=str(base))
            mem_path = base / "AAPL" / "2025" / "12" / "memory.json"
            mem_path.parent.mkdir(parents=True, exist_ok=True)

            obj = {
                "sentiment": "bullish",
                "trading_insights": "foo",
                "memory_weight_ratio": 0.25,
                "parameters": {"sentiment_number": 1.5, "memory_weight": 2.0},
                "memory_list": [],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            # Upsert current memory (DB-first source of truth)
            upsert_current_memory(
                db_path=db_path,
                ticker="AAPL",
                updated_at=obj["updated_at"],
                mem_path=mem_path,
                memory_obj=obj,
                logger=None,
            )
            loaded = load_current_memory(db_path=db_path, ticker="AAPL", logger=None)
            self.assertIsInstance(loaded, dict)
            self.assertEqual(str(loaded.get("sentiment")), "bullish")

            append_snapshot(
                db_path=db_path,
                ticker="AAPL",
                year="2025",
                month="12",
                updated_at=obj["updated_at"],
                mem_path=mem_path,
                memory_obj=obj,
                logger=None,
            )

            obj2 = dict(obj)
            obj2["updated_at"] = datetime.now(timezone.utc).isoformat()
            obj2["parameters"] = {"sentiment_number": -0.5, "memory_weight": 3.0}

            append_snapshot(
                db_path=db_path,
                ticker="AAPL",
                year="2025",
                month="12",
                updated_at=obj2["updated_at"],
                mem_path=mem_path,
                memory_obj=obj2,
                logger=None,
            )

            self.assertTrue(db_path.exists())
            conn = sqlite3.connect(str(db_path))
            try:
                n = conn.execute("SELECT COUNT(*) FROM memory_snapshot").fetchone()[0]
                self.assertEqual(n, 2)

                row = conn.execute(
                    "SELECT ticker, sentiment_number, memory_weight FROM memory_snapshot ORDER BY id ASC LIMIT 1"
                ).fetchone()
                self.assertEqual(row[0], "AAPL")
                self.assertAlmostEqual(float(row[1]), 1.5)
                self.assertAlmostEqual(float(row[2]), 2.0)

                cur = conn.execute(
                    "SELECT sentiment_number FROM memory_current WHERE ticker='AAPL'"
                ).fetchone()
                self.assertIsNotNone(cur)
            finally:
                conn.close()


