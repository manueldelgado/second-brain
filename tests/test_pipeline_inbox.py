"""Tests for the inbox classification pipeline."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from second_brain.config import (
    ProcessingConfig,
    Settings,
    TaxonomyConfig,
    VaultConfig,
)
from second_brain.models import ContentAnalysis
from second_brain.pipeline.inbox import run_inbox_pipeline
from second_brain.vault.filesystem import FilesystemBackend


class MockLLM:
    def __init__(self, result: ContentAnalysis) -> None:
        self.result = result
        self.calls: list[tuple] = []

    def analyze_content(self, content, taxonomy, content_hint=None):
        self.calls.append((content, content_hint))
        return self.result


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        vault=VaultConfig(root=tmp_path),
        processing=ProcessingConfig(),
    )


@pytest.fixture
def taxonomy() -> TaxonomyConfig:
    return TaxonomyConfig(
        descriptive={"ai/industry-news": "AI news"},
        functional={"func/trend-monitoring": "Trends"},
        classification_rules=["Use 1-3 tags"],
    )


@pytest.fixture
def analysis() -> ContentAnalysis:
    return ContentAnalysis(
        summary="A useful article about AI trends.",
        key_takeaways=["Takeaway 1", "Takeaway 2"],
        tags=["ai/industry-news", "func/trend-monitoring"],
        content_type="clipping",
        description="Article about AI trends.",
    )


def _seed_inbox_note(vault: FilesystemBackend, filename: str, content: str) -> Path:
    """Create a markdown file in the inbox folder."""
    return vault.create_note("00 Inbox", filename, content)


class TestRunInboxPipeline:
    def test_classifies_inbox_item(
        self,
        tmp_path: Path,
        settings: Settings,
        taxonomy: TaxonomyConfig,
        analysis: ContentAnalysis,
    ) -> None:
        vault = FilesystemBackend(tmp_path)
        note_content = (
            "---\n"
            "title: Test Article\n"
            "source: https://example.com\n"
            "author:\n"
            "  - '[[Author]]'\n"
            "created: 2026-03-07\n"
            "type: clipping\n"
            "status: inbox\n"
            "tags: []\n"
            "---\n\n"
            "# Test Article\n\nSome content here."
        )
        _seed_inbox_note(vault, "test-article.md", note_content)

        llm = MockLLM(analysis)

        report = run_inbox_pipeline(
            settings=settings,
            taxonomy=taxonomy,
            vault=vault,
            llm=llm,
        )

        assert report.items_processed == 1
        assert report.items_created == 1

        # Note should be moved to 01 Notes
        inbox_files = vault.list_folder("00 Inbox")
        notes_files = vault.list_folder("01 Notes")
        assert len(inbox_files) == 0
        assert len(notes_files) == 1

    def test_skips_already_classified(
        self,
        tmp_path: Path,
        settings: Settings,
        taxonomy: TaxonomyConfig,
        analysis: ContentAnalysis,
    ) -> None:
        vault = FilesystemBackend(tmp_path)
        note_content = (
            "---\n"
            "title: Already Done\n"
            "source: ''\n"
            "author: []\n"
            "created: 2026-03-07\n"
            "type: clipping\n"
            "status: classified\n"
            "tags:\n  - ai/industry-news\n"
            "---\n\n"
            "Content."
        )
        _seed_inbox_note(vault, "done.md", note_content)

        llm = MockLLM(analysis)

        report = run_inbox_pipeline(
            settings=settings,
            taxonomy=taxonomy,
            vault=vault,
            llm=llm,
        )

        # Should skip already-classified items
        assert report.items_processed == 0
        assert len(llm.calls) == 0

    def test_skips_items_with_no_tags(
        self,
        tmp_path: Path,
        settings: Settings,
        taxonomy: TaxonomyConfig,
    ) -> None:
        vault = FilesystemBackend(tmp_path)
        note_content = (
            "---\n"
            "title: Unclear Item\n"
            "source: ''\n"
            "author: []\n"
            "created: 2026-03-07\n"
            "type: note\n"
            "status: inbox\n"
            "tags: []\n"
            "---\n\n"
            "Vague content."
        )
        _seed_inbox_note(vault, "unclear.md", note_content)

        no_tags = ContentAnalysis(
            summary="Unclear", key_takeaways=[], tags=[],
            content_type="note", description="Unclear",
        )
        llm = MockLLM(no_tags)

        report = run_inbox_pipeline(
            settings=settings,
            taxonomy=taxonomy,
            vault=vault,
            llm=llm,
        )

        assert report.items_processed == 1
        assert report.items_skipped == 1
        # Item should stay in inbox
        inbox_files = vault.list_folder("00 Inbox")
        assert len(inbox_files) == 1

    def test_dry_run_no_modifications(
        self,
        tmp_path: Path,
        settings: Settings,
        taxonomy: TaxonomyConfig,
        analysis: ContentAnalysis,
    ) -> None:
        vault = FilesystemBackend(tmp_path)
        original_content = (
            "---\n"
            "title: Dry Run Test\n"
            "source: ''\n"
            "author: []\n"
            "created: 2026-03-07\n"
            "type: note\n"
            "status: inbox\n"
            "tags: []\n"
            "---\n\n"
            "Original content."
        )
        _seed_inbox_note(vault, "dry.md", original_content)

        llm = MockLLM(analysis)

        report = run_inbox_pipeline(
            settings=settings,
            taxonomy=taxonomy,
            vault=vault,
            llm=llm,
            dry_run=True,
        )

        assert report.items_created == 1
        # File should still be in inbox, unchanged
        inbox_files = vault.list_folder("00 Inbox")
        assert len(inbox_files) == 1
        content = vault.read_note(inbox_files[0])
        assert content == original_content

    def test_pdf_processing(
        self,
        tmp_path: Path,
        settings: Settings,
        taxonomy: TaxonomyConfig,
    ) -> None:
        vault = FilesystemBackend(tmp_path)

        # Create a fake PDF in inbox
        inbox_dir = tmp_path / "00 Inbox"
        inbox_dir.mkdir(parents=True)
        pdf = inbox_dir / "research-paper.pdf"
        pdf.write_bytes(b"fake pdf content")

        analysis = ContentAnalysis(
            summary="Research paper about CLV.",
            key_takeaways=["Finding 1"],
            tags=["marketing/clv-modeling"],
            content_type="paper",
            description="CLV research paper.",
        )
        llm = MockLLM(analysis)

        report = run_inbox_pipeline(
            settings=settings,
            taxonomy=taxonomy,
            vault=vault,
            llm=llm,
        )

        assert report.items_processed == 1
        assert report.items_created == 1

        # PDF should be in assets
        assets = vault.list_folder("04 Assets")
        assert any(f.name == "research-paper.pdf" for f in assets)

        # Wrapper note should be in notes
        notes = vault.list_folder("01 Notes")
        assert len(notes) == 1
        note_content = vault.read_note(notes[0])
        assert "research-paper.pdf" in note_content

    def test_preserves_existing_frontmatter(
        self,
        tmp_path: Path,
        settings: Settings,
        taxonomy: TaxonomyConfig,
        analysis: ContentAnalysis,
    ) -> None:
        vault = FilesystemBackend(tmp_path)
        note_content = (
            "---\n"
            "title: My Custom Title\n"
            "source: https://custom.url\n"
            "author:\n"
            "  - '[[Custom Author]]'\n"
            "created: 2026-03-01\n"
            "type: clipping\n"
            "status: inbox\n"
            "rating: 5\n"
            "tags: []\n"
            "---\n\n"
            "Content here."
        )
        _seed_inbox_note(vault, "custom.md", note_content)

        llm = MockLLM(analysis)

        report = run_inbox_pipeline(
            settings=settings,
            taxonomy=taxonomy,
            vault=vault,
            llm=llm,
        )

        assert report.items_created == 1
        notes = vault.list_folder("01 Notes")
        content = vault.read_note(notes[0])
        # Existing fields should be preserved
        assert "My Custom Title" in content
        assert "https://custom.url" in content

    def test_empty_inbox(
        self,
        tmp_path: Path,
        settings: Settings,
        taxonomy: TaxonomyConfig,
        analysis: ContentAnalysis,
    ) -> None:
        vault = FilesystemBackend(tmp_path)
        llm = MockLLM(analysis)

        report = run_inbox_pipeline(
            settings=settings,
            taxonomy=taxonomy,
            vault=vault,
            llm=llm,
        )

        assert report.items_processed == 0
        assert report.items_created == 0
        assert len(report.errors) == 0
