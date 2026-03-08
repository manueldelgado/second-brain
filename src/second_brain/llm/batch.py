"""Provider-agnostic batch LLM types and protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from second_brain.config import TaxonomyConfig
from second_brain.models import ContentAnalysis


@dataclass
class BatchRequest:
    """A single item to be analysed in a batch job."""

    custom_id: str  # unique within the batch; must match [a-zA-Z0-9_-]{1,64}
    content: str
    taxonomy: TaxonomyConfig
    content_hint: str | None = None


@dataclass
class BatchResult:
    """Result for a single item from a completed batch job."""

    custom_id: str
    analysis: ContentAnalysis | None  # None when the individual request failed
    error: str | None = None


@dataclass
class BatchStatus:
    """Snapshot of a submitted batch job's progress."""

    batch_id: str
    state: Literal["pending", "in_progress", "complete", "error", "cancelled"]
    total: int
    succeeded: int
    failed: int
    ended_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in ("complete", "error", "cancelled")


@runtime_checkable
class BatchLLMProvider(Protocol):
    """Optional protocol for providers that support asynchronous batch APIs.

    Implement this alongside LLMProvider when the underlying provider offers
    a batch submission endpoint (e.g. Anthropic Messages Batches, OpenAI Batch).
    The pipeline checks isinstance(provider, BatchLLMProvider) at runtime.
    """

    def submit_batch(self, requests: list[BatchRequest]) -> str:
        """Submit requests; returns an opaque provider batch_id."""
        ...

    def get_batch_status(self, batch_id: str) -> BatchStatus:
        """Fetch current status without blocking."""
        ...

    def get_batch_results(self, batch_id: str) -> list[BatchResult]:
        """Retrieve results. Only valid when status.state == 'complete'."""
        ...

    def cancel_batch(self, batch_id: str) -> None:
        """Cancel a pending or in-progress batch."""
        ...
