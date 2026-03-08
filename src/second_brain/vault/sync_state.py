"""Read/write sync_state.yaml for newsletter processing state."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml


class SyncState:
    """Manages newsletter sync state with atomic writes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"last_sync": {}, "global_last_run": None}
        with open(self.path) as f:
            data = yaml.safe_load(f) or {}
        return {
            "last_sync": data.get("last_sync", {}),
            "global_last_run": data.get("global_last_run"),
        }

    def _save(self) -> None:
        """Atomic write: write to temp file, then rename."""
        content = (
            "# Newsletter Sync State\n"
            "# Tracks the last processed email per newsletter source\n\n"
        )
        content += yaml.dump(
            self._data,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".yaml")
        try:
            with open(fd, "w") as f:
                f.write(content)
            Path(tmp_path).replace(self.path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def get_last_sync(self, newsletter_name: str) -> datetime | None:
        ts = self._data["last_sync"].get(newsletter_name)
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts
        return datetime.fromisoformat(str(ts))

    def get_global_last_run(self) -> datetime | None:
        ts = self._data.get("global_last_run")
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts
        return datetime.fromisoformat(str(ts))

    def update_sync(self, newsletter_name: str, timestamp: datetime) -> None:
        self._data["last_sync"][newsletter_name] = timestamp.isoformat()
        self._save()

    def update_global_last_run(self, timestamp: datetime | None = None) -> None:
        ts = timestamp or datetime.now(timezone.utc)
        self._data["global_last_run"] = ts.isoformat()
        self._save()
