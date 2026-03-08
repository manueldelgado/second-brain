"""Shared test fixtures for the Second Brain test suite."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from second_brain.config import TaxonomyConfig
from second_brain.models import ContentAnalysis, IngestItem
from second_brain.vault.filesystem import FilesystemBackend


@pytest.fixture
def taxonomy() -> TaxonomyConfig:
    """Minimal taxonomy for tests."""
    return TaxonomyConfig(
        descriptive={
            "ai/industry-news": "Funding, M&A, product launches",
            "ai/strategy": "Platform plays, moats",
            "data/tools": "Python, SQL, ML libraries",
        },
        functional={
            "func/trend-monitoring": "Staying current on AI/tech/data",
            "func/teaching": "Usable in courses",
        },
        classification_rules=[
            "Use 1-3 descriptive tags and 0-2 functional tags per note",
        ],
    )


@pytest.fixture
def sample_analysis() -> ContentAnalysis:
    """Sample LLM analysis result."""
    return ContentAnalysis(
        summary="This newsletter covers the latest AI industry developments.",
        key_takeaways=[
            "OpenAI released a new model",
            "Google announced Gemini updates",
            "Anthropic raised funding",
        ],
        tags=["ai/industry-news", "func/trend-monitoring"],
        content_type="newsletter",
        description="Weekly AI industry roundup with major announcements.",
    )


@pytest.fixture
def vault_backend(tmp_path: Path) -> FilesystemBackend:
    """Filesystem vault backend rooted at a tmp directory."""
    return FilesystemBackend(tmp_path)


@pytest.fixture
def sample_ingest_item() -> IngestItem:
    """Sample IngestItem from Gmail."""
    return IngestItem(
        source_type="gmail",
        title="Weekly AI Roundup #42",
        content="# AI News\n\nThis week in AI...",
        source_url="",
        author=["[[Benedict Evans]]"],
        published=date(2026, 3, 7),
        newsletter_name="Benedict Evans",
        metadata={
            "message_id": "abc123",
            "internal_date": 1741363200000,
            "internal_date_iso": "2026-03-07T12:00:00+00:00",
        },
    )
