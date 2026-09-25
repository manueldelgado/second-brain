"""LLMProvider protocol — abstract interface for content analysis."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from second_brain.config import TaxonomyConfig
from second_brain.models import ContentAnalysis


@runtime_checkable
class LLMProvider(Protocol):
    """Abstract interface for LLM-powered content analysis."""

    def analyze_content(
        self,
        content: str,
        taxonomy: TaxonomyConfig,
        content_hint: str | None = None,
        include_content_type: bool = True,
    ) -> ContentAnalysis:
        """Analyze content and return structured classification + summary."""
        ...


class LLMUnavailableError(RuntimeError):
    """The provider cannot serve *any* request (logged out, plan limit, missing CLI).

    Pipelines stop the run instead of failing every remaining item; the CLI turns
    it into an alert note in the vault. *hint* tells the user how to fix it.
    """

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint
