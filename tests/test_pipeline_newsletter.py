"""Tests for the newsletter ingestion pipeline."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from second_brain.config import (
    NewsletterSource,
    NewslettersConfig,
    ProcessingConfig,
    Settings,
    TaxonomyConfig,
    VaultConfig,
)
from second_brain.models import ContentAnalysis, IngestItem
from second_brain.pipeline.newsletter import _compute_after_date, run_newsletter_pipeline
from second_brain.vault.filesystem import FilesystemBackend
from second_brain.vault.sync_state import SyncState


class MockLLM:
    """Mock LLM provider for testing."""

    def __init__(self, result: ContentAnalysis) -> None:
        self.result = result
        self.calls: list[tuple] = []

    def analyze_content(self, content, taxonomy, content_hint=None):
        self.calls.append((content, content_hint))
        return self.result


class MockGmail:
    """Mock Gmail client for testing."""

    def __init__(self, items_by_source: dict[str, list[IngestItem]] | None = None) -> None:
        self.items_by_source = items_by_source or {}

    def fetch_newsletters(self, sender_email, newsletter_name, after_date, min_internal_date=None, sender_name=None):
        return self.items_by_source.get(newsletter_name, [])

    def get_or_create_label(self, name):
        return "label_mock"

    def apply_label(self, message_id, label_id):
        pass


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        vault=VaultConfig(root=tmp_path),
        processing=ProcessingConfig(default_lookback_days=7),
    )


@pytest.fixture
def taxonomy() -> TaxonomyConfig:
    return TaxonomyConfig(
        descriptive={"ai/industry-news": "AI news"},
        functional={"func/trend-monitoring": "Trends"},
        classification_rules=["Use 1-3 tags"],
    )


@pytest.fixture
def newsletters() -> NewslettersConfig:
    return NewslettersConfig(
        sources=[
            NewsletterSource(email="ben@ben-evans.com", name="Benedict Evans"),
        ]
    )


@pytest.fixture
def analysis() -> ContentAnalysis:
    return ContentAnalysis(
        summary="AI industry developments this week.",
        key_takeaways=["Point 1", "Point 2"],
        tags=["ai/industry-news"],
        content_type="newsletter",
        description="Weekly AI roundup.",
    )


class TestComputeAfterDate:
    def test_with_last_sync(self, tmp_path: Path, settings: Settings) -> None:
        from datetime import datetime, timezone
        state_file = tmp_path / "sync.yaml"
        state_file.write_text(
            yaml.dump({"last_sync": {"Benedict Evans": "2026-03-05T12:00:00Z"}})
        )
        sync = SyncState(state_file)
        after_date, min_internal_date = _compute_after_date(sync, "Benedict Evans", settings)
        expected = datetime(2026, 3, 5, 12, 0, 0, tzinfo=timezone.utc)
        # No buffer applied — both values equal last_sync exactly
        assert after_date == expected
        assert min_internal_date == expected

    def test_no_last_sync_ignores_global_last_run(self, tmp_path: Path, settings: Settings) -> None:
        from datetime import datetime, timezone
        state_file = tmp_path / "sync.yaml"
        state_file.write_text(
            yaml.dump({"global_last_run": "2026-03-07T10:00:00Z"})
        )
        sync = SyncState(state_file)
        after_date, min_internal_date = _compute_after_date(sync, "Unknown Source", settings)
        # global_last_run must not be used — new/silent sources always get the lookback window
        assert isinstance(after_date, datetime)
        assert after_date < datetime(2026, 3, 7, 10, 0, 0, tzinfo=timezone.utc)
        assert min_internal_date is None

    def test_fallback_to_default_lookback(self, tmp_path: Path, settings: Settings) -> None:
        from datetime import datetime
        state_file = tmp_path / "sync.yaml"
        sync = SyncState(state_file)
        after_date, min_internal_date = _compute_after_date(sync, "Unknown", settings)
        # Fallback: coarse date cutoff, no precise filter
        assert isinstance(after_date, datetime)
        assert min_internal_date is None


class TestRunNewsletterPipeline:
    def test_end_to_end(
        self,
        tmp_path: Path,
        settings: Settings,
        newsletters: NewslettersConfig,
        taxonomy: TaxonomyConfig,
        analysis: ContentAnalysis,
    ) -> None:
        vault = FilesystemBackend(tmp_path)
        sync_state = SyncState(tmp_path / "sync.yaml")

        item = IngestItem(
            source_type="gmail",
            title="AI Weekly #1",
            content="# AI News\nContent here",
            author=["[[Benedict Evans]]"],
            published=date(2026, 3, 7),
            newsletter_name="Benedict Evans",
            metadata={
                "message_id": "msg1",
                "internal_date": 1741363200000,
                "internal_date_iso": "2026-03-07T12:00:00+00:00",
            },
        )

        gmail = MockGmail({"Benedict Evans": [item]})
        llm = MockLLM(analysis)

        report = run_newsletter_pipeline(
            settings=settings,
            newsletters=newsletters,
            taxonomy=taxonomy,
            vault=vault,
            gmail=gmail,
            llm=llm,
            sync_state=sync_state,
        )

        assert report.items_processed == 1
        assert report.items_created == 1
        assert len(report.errors) == 0

        # Verify note was created
        notes = vault.list_folder("01 Notes")
        assert len(notes) == 1
        content = vault.read_note(notes[0])
        assert "AI Weekly #1" in content
        assert "AI News" in content

    def test_dry_run_no_files_created(
        self,
        tmp_path: Path,
        settings: Settings,
        newsletters: NewslettersConfig,
        taxonomy: TaxonomyConfig,
        analysis: ContentAnalysis,
    ) -> None:
        vault = FilesystemBackend(tmp_path)
        sync_state = SyncState(tmp_path / "sync.yaml")

        item = IngestItem(
            source_type="gmail",
            title="Test",
            content="content",
            newsletter_name="Benedict Evans",
            metadata={"internal_date_iso": "2026-03-07T12:00:00+00:00"},
        )

        gmail = MockGmail({"Benedict Evans": [item]})
        llm = MockLLM(analysis)

        report = run_newsletter_pipeline(
            settings=settings,
            newsletters=newsletters,
            taxonomy=taxonomy,
            vault=vault,
            gmail=gmail,
            llm=llm,
            sync_state=sync_state,
            dry_run=True,
        )

        assert report.items_processed == 1
        assert report.items_created == 1
        notes = vault.list_folder("01 Notes")
        assert len(notes) == 0

    def test_no_new_emails(
        self,
        tmp_path: Path,
        settings: Settings,
        newsletters: NewslettersConfig,
        taxonomy: TaxonomyConfig,
    ) -> None:
        vault = FilesystemBackend(tmp_path)
        sync_state = SyncState(tmp_path / "sync.yaml")
        gmail = MockGmail({})
        llm = MockLLM(ContentAnalysis(
            summary="x", key_takeaways=[], tags=[], content_type="newsletter", description="x"
        ))

        report = run_newsletter_pipeline(
            settings=settings,
            newsletters=newsletters,
            taxonomy=taxonomy,
            vault=vault,
            gmail=gmail,
            llm=llm,
            sync_state=sync_state,
        )

        assert report.items_processed == 0
        assert report.items_created == 0

    def test_gmail_error_recorded(
        self,
        tmp_path: Path,
        settings: Settings,
        newsletters: NewslettersConfig,
        taxonomy: TaxonomyConfig,
    ) -> None:
        vault = FilesystemBackend(tmp_path)
        sync_state = SyncState(tmp_path / "sync.yaml")
        llm = MockLLM(ContentAnalysis(
            summary="x", key_takeaways=[], tags=[], content_type="newsletter", description="x"
        ))

        class FailingGmail:
            def get_or_create_label(self, name):
                return "label_mock"

            def fetch_newsletters(self, *args, **kwargs):
                raise ConnectionError("Gmail API error")

        report = run_newsletter_pipeline(
            settings=settings,
            newsletters=newsletters,
            taxonomy=taxonomy,
            vault=vault,
            gmail=FailingGmail(),
            llm=llm,
            sync_state=sync_state,
        )

        assert len(report.errors) == 1
        assert "Gmail fetch failed" in report.errors[0]
