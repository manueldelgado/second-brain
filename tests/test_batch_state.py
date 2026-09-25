"""Tests for pending-batch persistence."""

from __future__ import annotations

from datetime import datetime, timezone

from second_brain.models import IngestItem
from second_brain.pipeline.batch_state import BatchStateManager, PendingBatch, PendingBatchItem


def _batch(batch_id: str, message_id: str) -> PendingBatch:
    item = IngestItem(source_type="gmail", title=message_id, content="Body", metadata={"message_id": message_id})
    return PendingBatch(
        batch_id=batch_id,
        pipeline="newsletters",
        submitted_at=datetime.now(timezone.utc),
        items=[PendingBatchItem(custom_id="item0000", item=item)],
    )


def test_concurrent_submit_is_not_lost_when_resume_removes_a_batch(tmp_path) -> None:
    path = tmp_path / "batch_state.yaml"
    BatchStateManager(path).add_batch(_batch("old", "m1"))

    resume = BatchStateManager(path)           # resume-batch loads the file…
    BatchStateManager(path).add_batch(_batch("new", "m2"))  # …a submit adds a batch…
    resume.remove_batch("old")                 # …then resume saves its removal

    assert BatchStateManager(path).all_batch_ids() == ["new"]


def test_pending_item_keys_filters_by_pipeline(tmp_path) -> None:
    state = BatchStateManager(tmp_path / "batch_state.yaml")
    state.add_batch(_batch("b1", "m1"))
    assert state.pending_item_keys("newsletters") == {"m1"}
    assert state.pending_item_keys("inbox") == set()
