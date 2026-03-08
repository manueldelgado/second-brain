"""Anthropic Messages Batches implementation of BatchLLMProvider."""

from __future__ import annotations

import logging

import anthropic

from second_brain.llm.batch import BatchLLMProvider, BatchRequest, BatchResult, BatchStatus
from second_brain.llm.claude import parse_classify_response
from second_brain.llm.prompts import (
    CLASSIFY_CONTENT_TOOL,
    build_analysis_prompt,
    build_system_prompt,
)

logger = logging.getLogger(__name__)


class ClaudeBatchProvider:
    """BatchLLMProvider backed by the Anthropic Messages Batches API.

    Implements the provider-agnostic BatchLLMProvider protocol.  Results are
    available asynchronously: call submit_batch(), persist the batch_id, then
    poll get_batch_status() and call get_batch_results() once complete.

    Anthropic keeps batch results available for 29 days after completion.
    """

    def __init__(self, model: str = "claude-sonnet-4-20250514", max_tokens: int = 4096) -> None:
        self.client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    # ------------------------------------------------------------------
    # BatchLLMProvider protocol
    # ------------------------------------------------------------------

    def submit_batch(self, requests: list[BatchRequest]) -> str:
        """Submit a list of requests to the Anthropic batch API.

        Returns the Anthropic batch ID (e.g. ``msgbatch_01...``).
        Each request's custom_id must match ``[a-zA-Z0-9_-]{1,64}``.
        """
        anthropic_requests = [self._to_anthropic_request(req) for req in requests]
        batch = self.client.messages.batches.create(requests=anthropic_requests)
        logger.info("Submitted batch %s (%d requests)", batch.id, len(requests))
        return batch.id

    def get_batch_status(self, batch_id: str) -> BatchStatus:
        """Fetch current batch status from the Anthropic API."""
        batch = self.client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        total = counts.processing + counts.succeeded + counts.errored + counts.canceled + counts.expired

        state = _map_processing_status(batch.processing_status, counts)
        return BatchStatus(
            batch_id=batch_id,
            state=state,
            total=total,
            succeeded=counts.succeeded,
            failed=counts.errored + counts.expired,
            ended_at=batch.ended_at,
        )

    def get_batch_results(self, batch_id: str) -> list[BatchResult]:
        """Stream and parse all results for a completed batch."""
        results: list[BatchResult] = []
        for item in self.client.messages.batches.results(batch_id):
            result = item.result
            if result.type == "succeeded":
                try:
                    analysis = parse_classify_response(result.message)
                    results.append(BatchResult(custom_id=item.custom_id, analysis=analysis))
                except Exception as exc:
                    logger.warning("Failed to parse result for %s: %s", item.custom_id, exc)
                    results.append(BatchResult(custom_id=item.custom_id, analysis=None, error=str(exc)))
            elif result.type == "errored":
                error_msg = f"API error: {result.error}"
                logger.warning("Request %s errored: %s", item.custom_id, error_msg)
                results.append(BatchResult(custom_id=item.custom_id, analysis=None, error=error_msg))
            else:
                # expired or canceled
                results.append(
                    BatchResult(
                        custom_id=item.custom_id,
                        analysis=None,
                        error=f"Request {result.type}",
                    )
                )
        return results

    def cancel_batch(self, batch_id: str) -> None:
        """Request cancellation of a pending or in-progress batch."""
        self.client.messages.batches.cancel(batch_id)
        logger.info("Cancellation requested for batch %s", batch_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _to_anthropic_request(self, req: BatchRequest) -> dict:
        system_prompt = build_system_prompt(req.taxonomy)
        user_message = build_analysis_prompt(req.content, req.content_hint)
        return {
            "custom_id": req.custom_id,
            "params": {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                "tools": [CLASSIFY_CONTENT_TOOL],
                "tool_choice": {"type": "tool", "name": "classify_content"},
                "messages": [{"role": "user", "content": user_message}],
            },
        }


def _map_processing_status(
    processing_status: str,
    counts,
) -> str:
    """Translate Anthropic processing_status to our provider-agnostic state."""
    if processing_status in ("in_progress", "canceling"):
        return "in_progress"
    # "ended" — inspect counts to determine terminal state
    if counts.canceled > 0 and counts.succeeded == 0 and counts.errored == 0:
        return "cancelled"
    if counts.errored > 0 and counts.succeeded == 0:
        return "error"
    return "complete"


# Verify the class satisfies the protocol at import time (catches typos early).
assert isinstance(ClaudeBatchProvider(""), BatchLLMProvider)
