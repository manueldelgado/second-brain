"""Persistent state for in-flight LLM batch jobs.

batch_state.yaml is the source of truth for all submitted-but-not-yet-finalized
batches.  ``second-brain resume-batch`` reads this file, polls each pending batch,
finalizes any that are complete, and removes them from the file.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from second_brain.models import IngestItem


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

class PendingBatchItem:
    """Metadata for a single item within a pending batch."""

    def __init__(self, custom_id: str, item: IngestItem) -> None:
        self.custom_id = custom_id
        self.item = item

    def to_dict(self) -> dict:
        return {
            "custom_id": self.custom_id,
            "item": self.item.model_dump(mode="json"),
        }

    @classmethod
    def from_dict(cls, data: dict) -> PendingBatchItem:
        return cls(
            custom_id=data["custom_id"],
            item=IngestItem(**data["item"]),
        )


class PendingBatch:
    """A batch job submitted to the LLM provider that has not yet been finalized."""

    #: Anthropic keeps batch results for 29 days; we use the same TTL.
    RESULT_TTL_DAYS = 29

    def __init__(
        self,
        batch_id: str,
        pipeline: str,
        submitted_at: datetime,
        items: list[PendingBatchItem],
        expires_at: datetime | None = None,
    ) -> None:
        self.batch_id = batch_id
        self.pipeline = pipeline
        self.submitted_at = submitted_at
        self.items = items
        self.expires_at = expires_at or (submitted_at + timedelta(days=self.RESULT_TTL_DAYS))

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "pipeline": self.pipeline,
            "submitted_at": self.submitted_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "items": [i.to_dict() for i in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict) -> PendingBatch:
        submitted_at = _parse_dt(data["submitted_at"])
        expires_at = _parse_dt(data["expires_at"])
        items = [PendingBatchItem.from_dict(i) for i in data.get("items", [])]
        return cls(
            batch_id=data["batch_id"],
            pipeline=data["pipeline"],
            submitted_at=submitted_at,
            expires_at=expires_at,
            items=items,
        )


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class BatchStateManager:
    """Read/write batch_state.yaml with atomic writes (mirrors SyncState)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._batches: dict[str, PendingBatch] = self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_batch(self, batch: PendingBatch) -> None:
        """Persist a newly submitted batch."""
        self._batches[batch.batch_id] = batch
        self._save()

    def get_pending(self) -> list[PendingBatch]:
        """Return all non-expired pending batches."""
        live = [b for b in self._batches.values() if not b.is_expired]
        expired = [b for b in self._batches.values() if b.is_expired]
        for b in expired:
            del self._batches[b.batch_id]
        if expired:
            self._save()
        return live

    def remove_batch(self, batch_id: str) -> None:
        """Remove a finalized (or cancelled/errored) batch from the state."""
        if batch_id in self._batches:
            del self._batches[batch_id]
            self._save()

    def all_batch_ids(self) -> list[str]:
        return list(self._batches.keys())

    def get_batch(self, batch_id: str) -> PendingBatch | None:
        return self._batches.get(batch_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, PendingBatch]:
        if not self.path.exists():
            return {}
        with open(self.path) as f:
            raw: dict = yaml.safe_load(f) or {}
        result: dict[str, PendingBatch] = {}
        for entry in raw.get("pending_batches", []):
            try:
                b = PendingBatch.from_dict(entry)
                result[b.batch_id] = b
            except Exception:
                pass  # skip corrupt entries silently
        return result

    def _save(self) -> None:
        content = (
            "# Second Brain — Pending LLM Batch Jobs\n"
            "# Managed automatically by second-brain. Do not edit by hand.\n\n"
        )
        data: dict[str, Any] = {
            "pending_batches": [b.to_dict() for b in self._batches.values()],
        }
        content += yaml.dump(
            data,
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


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
