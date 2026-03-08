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
    ) -> ContentAnalysis:
        """Analyze content and return structured classification + summary."""
        ...
