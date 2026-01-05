from __future__ import annotations

from pathlib import Path
import tempfile

from moose.agents.alpaca_trader.storage.db import TraderDb


def test_dedupe_check_and_set_ttl() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = TraderDb(Path(td) / "test.sqlite")
        assert db.dedupe_check_and_set(key="k", ttl_s=60) is True
        assert db.dedupe_check_and_set(key="k", ttl_s=60) is False


