"""Tests for sync_state.yaml management."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from second_brain.vault.sync_state import SyncState


class TestSyncStateLoad:
    def test_load_existing_file(self, tmp_path: Path) -> None:
        state_file = tmp_path / "sync_state.yaml"
        state_file.write_text(
            yaml.dump({
                "last_sync": {"Benedict Evans": "2026-03-01T12:00:00Z"},
                "global_last_run": "2026-03-07T15:00:00Z",
            })
        )
        state = SyncState(state_file)
        last = state.get_last_sync("Benedict Evans")
        assert last is not None
        assert last.year == 2026
        assert last.month == 3
        assert last.day == 1

    def test_load_nonexistent_file(self, tmp_path: Path) -> None:
        state_file = tmp_path / "missing.yaml"
        state = SyncState(state_file)
        assert state.get_last_sync("Anything") is None
        assert state.get_global_last_run() is None

    def test_load_empty_file(self, tmp_path: Path) -> None:
        state_file = tmp_path / "empty.yaml"
        state_file.write_text("")
        state = SyncState(state_file)
        assert state.get_last_sync("X") is None


class TestSyncStateGetters:
    def test_get_last_sync_known_source(self, tmp_path: Path) -> None:
        state_file = tmp_path / "sync.yaml"
        state_file.write_text(
            yaml.dump({"last_sync": {"Data Elixir": "2026-03-05T17:00:20Z"}})
        )
        state = SyncState(state_file)
        ts = state.get_last_sync("Data Elixir")
        assert ts is not None
        assert ts.month == 3
        assert ts.day == 5

    def test_get_last_sync_unknown_source(self, tmp_path: Path) -> None:
        state_file = tmp_path / "sync.yaml"
        state_file.write_text(yaml.dump({"last_sync": {"A": "2026-01-01T00:00:00Z"}}))
        state = SyncState(state_file)
        assert state.get_last_sync("Unknown") is None

    def test_get_global_last_run(self, tmp_path: Path) -> None:
        state_file = tmp_path / "sync.yaml"
        state_file.write_text(yaml.dump({"global_last_run": "2026-03-08T12:00:00Z"}))
        state = SyncState(state_file)
        ts = state.get_global_last_run()
        assert ts is not None
        assert ts.day == 8


class TestSyncStateWrites:
    def test_update_sync_creates_file(self, tmp_path: Path) -> None:
        state_file = tmp_path / "sync.yaml"
        state = SyncState(state_file)
        ts = datetime(2026, 3, 7, 14, 30, 0, tzinfo=timezone.utc)
        state.update_sync("Benedict Evans", ts)

        assert state_file.exists()
        # Re-read to verify
        state2 = SyncState(state_file)
        last = state2.get_last_sync("Benedict Evans")
        assert last is not None

    def test_update_sync_preserves_other_entries(self, tmp_path: Path) -> None:
        state_file = tmp_path / "sync.yaml"
        state_file.write_text(
            yaml.dump({"last_sync": {"A": "2026-01-01T00:00:00Z"}})
        )
        state = SyncState(state_file)
        ts = datetime(2026, 3, 7, 0, 0, 0, tzinfo=timezone.utc)
        state.update_sync("B", ts)

        state2 = SyncState(state_file)
        assert state2.get_last_sync("A") is not None
        assert state2.get_last_sync("B") is not None

    def test_update_global_last_run(self, tmp_path: Path) -> None:
        state_file = tmp_path / "sync.yaml"
        state = SyncState(state_file)
        ts = datetime(2026, 3, 8, 15, 0, 0, tzinfo=timezone.utc)
        state.update_global_last_run(ts)

        state2 = SyncState(state_file)
        glr = state2.get_global_last_run()
        assert glr is not None

    def test_update_global_last_run_default_now(self, tmp_path: Path) -> None:
        state_file = tmp_path / "sync.yaml"
        state = SyncState(state_file)
        state.update_global_last_run()
        assert state_file.exists()
        state2 = SyncState(state_file)
        assert state2.get_global_last_run() is not None

    def test_atomic_write_readable_as_yaml(self, tmp_path: Path) -> None:
        state_file = tmp_path / "sync.yaml"
        state = SyncState(state_file)
        state.update_sync("Test", datetime(2026, 1, 1, tzinfo=timezone.utc))

        with open(state_file) as f:
            data = yaml.safe_load(f)
        assert "last_sync" in data
        assert "Test" in data["last_sync"]
