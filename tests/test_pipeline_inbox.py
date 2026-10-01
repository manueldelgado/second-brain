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

    def analyze_content(self, content, taxonomy, content_hint=None, include_content_type=True):
        self.calls.append((content, content_hint))
        return self.result


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Web enrichment is off by default in tests so they never hit the network;
    # enrichment behaviour is exercised separately with a mocked fetch_article.
    return Settings(
        vault=VaultConfig(root=tmp_path),
        processing=ProcessingConfig(enrich_from_web=False),
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

    def test_untagged_item_written_as_needs_tags(
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

        # New behaviour: instead of being abandoned in the inbox, the enriched
        # note is written and moved to Notes flagged for manual tagging.
        assert report.items_processed == 1
        assert report.items_created == 1
        assert len(vault.list_folder("00 Inbox")) == 0
        notes = vault.list_folder("01 Notes")
        assert len(notes) == 1
        content = vault.read_note(notes[0])
        assert "status: needs-tags" in content
        # The summary and original content survive even without tags.
        assert "Vague content." in content

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

    def test_clipping_does_not_overwrite_existing_note(
        self,
        tmp_path: Path,
        settings: Settings,
        taxonomy: TaxonomyConfig,
        analysis: ContentAnalysis,
    ) -> None:
        vault = FilesystemBackend(tmp_path)
        existing = vault.create_note("01 Notes", "article.md", "my older note")
        _seed_inbox_note(
            vault,
            "article.md",
            "---\ntitle: Article\nstatus: inbox\n---\n\nNew clipping body.",
        )

        report = run_inbox_pipeline(
            settings=settings, taxonomy=taxonomy, vault=vault, llm=MockLLM(analysis)
        )

        assert report.items_created == 1
        assert existing.read_text() == "my older note"
        moved = tmp_path / "01 Notes" / f"article ({date.today().isoformat()}).md"
        assert "New clipping body." in moved.read_text()
        assert vault.list_folder("00 Inbox") == []

    def test_pdf_does_not_overwrite_existing_asset_or_note(
        self,
        tmp_path: Path,
        settings: Settings,
        taxonomy: TaxonomyConfig,
        analysis: ContentAnalysis,
    ) -> None:
        vault = FilesystemBackend(tmp_path)
        assets = tmp_path / "04 Assets"
        assets.mkdir()
        (assets / "paper.pdf").write_bytes(b"older pdf")
        existing_note = vault.create_note("01 Notes", "paper.md", "my older note")
        inbox = tmp_path / "00 Inbox"
        inbox.mkdir()
        (inbox / "paper.pdf").write_bytes(b"new pdf")

        report = run_inbox_pipeline(
            settings=settings, taxonomy=taxonomy, vault=vault, llm=MockLLM(analysis)
        )

        assert report.items_created == 1
        assert (assets / "paper.pdf").read_bytes() == b"older pdf"
        today = date.today().isoformat()
        assert (assets / f"paper ({today}).pdf").read_bytes() == b"new pdf"
        assert existing_note.read_text() == "my older note"
        wrapper = (tmp_path / "01 Notes" / f"paper ({today}).md").read_text()
        assert f"![[paper ({today}).pdf]]" in wrapper  # embeds the asset under its actual name

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

    def test_merges_and_validates_tags(
        self,
        tmp_path: Path,
        settings: Settings,
        taxonomy: TaxonomyConfig,
    ) -> None:
        vault = FilesystemBackend(tmp_path)
        note_content = (
            "---\n"
            "title: Tagged Clip\n"
            "source: ''\n"
            "author: []\n"
            "created: 2026-03-07\n"
            "type: clipping\n"
            "status: inbox\n"
            "tags:\n  - clippings\n"
            "---\n\n"
            "Body."
        )
        _seed_inbox_note(vault, "clip.md", note_content)

        # LLM returns one valid taxonomy tag and one hallucinated one.
        llm = MockLLM(
            ContentAnalysis(
                summary="s",
                key_takeaways=["k"],
                tags=["ai/industry-news", "ai/hallucinated"],
                content_type="clipping",
                description="d",
            )
        )

        run_inbox_pipeline(settings=settings, taxonomy=taxonomy, vault=vault, llm=llm)

        content = vault.read_note(vault.list_folder("01 Notes")[0])
        assert "clippings" in content            # existing tag kept verbatim
        assert "ai/industry-news" in content     # valid LLM tag added
        assert "ai/hallucinated" not in content  # invalid LLM tag dropped
        assert "status: classified" in content   # a valid tag → classified

    def test_enrichment_recovers_content_and_fixes_metadata(
        self,
        tmp_path: Path,
        taxonomy: TaxonomyConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from second_brain.enrich import WebArticle
        from second_brain.pipeline import inbox as inbox_mod

        settings = Settings(
            vault=VaultConfig(root=tmp_path),
            processing=ProcessingConfig(enrich_from_web=True),
        )
        vault = FilesystemBackend(tmp_path)
        note_content = (
            "---\n"
            "title: Stub Clip\n"
            "source: https://example.com/2026/06/11/post?utm_source=news&id=9\n"
            "author:\n"
            "published: 2000-06-11\n"
            "created: 2026-07-14\n"
            "type: clipping\n"
            "status: inbox\n"
            "tags:\n  - clippings\n"
            "---\n"
            "short stub"
        )
        _seed_inbox_note(vault, "stub.md", note_content)

        full = "This is the full recovered article body, far longer than the stub. " * 5

        def fake_fetch(url: str, timeout_seconds: int = 20) -> WebArticle:
            return WebArticle(
                text=full,
                title="Stub Clip",
                author="Jane Doe",
                date="2026-06-11",
                canonical_url="https://example.com/2026/06/11/post",
            )

        monkeypatch.setattr(inbox_mod, "fetch_article", fake_fetch)

        llm = MockLLM(
            ContentAnalysis(
                summary="s", key_takeaways=["k"], tags=["ai/industry-news"],
                content_type="clipping", description="d",
            )
        )
        run_inbox_pipeline(settings=settings, taxonomy=taxonomy, vault=vault, llm=llm)

        content = vault.read_note(vault.list_folder("01 Notes")[0])
        assert "full recovered article body" in content   # content recovered
        assert "utm_source" not in content                # tracking stripped
        assert "[[Jane Doe]]" in content                  # author from fetch
        assert "2026-06-11" in content                    # corrected date
        assert "2000-06-11" not in content                # garbage date dropped
        # The LLM was given the recovered content, not the stub.
        assert "full recovered article body" in llm.calls[0][0]

    def test_enrichment_keeps_captured_when_fetch_is_shorter(
        self,
        tmp_path: Path,
        taxonomy: TaxonomyConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from second_brain.enrich import WebArticle
        from second_brain.pipeline import inbox as inbox_mod

        settings = Settings(
            vault=VaultConfig(root=tmp_path),
            processing=ProcessingConfig(enrich_from_web=True),
        )
        vault = FilesystemBackend(tmp_path)
        long_capture = "Full paywalled content captured in the browser. " * 10
        note_content = (
            "---\n"
            "title: Paywalled\n"
            "source: https://paywall.example/article\n"
            "author: []\n"
            "created: 2026-07-14\n"
            "type: clipping\n"
            "status: inbox\n"
            "tags: []\n"
            "---\n"
            + long_capture
        )
        _seed_inbox_note(vault, "pw.md", note_content)

        def fake_fetch(url: str, timeout_seconds: int = 20) -> WebArticle:
            return WebArticle(text="Short anonymous teaser.")

        monkeypatch.setattr(inbox_mod, "fetch_article", fake_fetch)

        llm = MockLLM(
            ContentAnalysis(
                summary="s", key_takeaways=["k"], tags=["ai/industry-news"],
                content_type="clipping", description="d",
            )
        )
        run_inbox_pipeline(settings=settings, taxonomy=taxonomy, vault=vault, llm=llm)

        content = vault.read_note(vault.list_folder("01 Notes")[0])
        assert "Full paywalled content captured" in content  # captured kept
        assert "Short anonymous teaser." not in content      # weaker fetch ignored

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

    def test_unreadable_item_is_reported_and_others_processed(
        self,
        tmp_path: Path,
        settings: Settings,
        taxonomy: TaxonomyConfig,
        analysis: ContentAnalysis,
        monkeypatch,
    ) -> None:
        vault = FilesystemBackend(tmp_path)
        _seed_inbox_note(vault, "Placeholder.md", "---\ntitle: Placeholder\n---\n\nBody.\n")
        _seed_inbox_note(vault, "Readable.md", "---\ntitle: Readable\n---\n\nBody.\n")
        read_note = vault.read_note

        def read_or_deadlock(path):
            # What an iCloud placeholder read gives a process with downloads off
            if path.name == "Placeholder.md":
                raise OSError(11, "Resource deadlock avoided")
            return read_note(path)

        monkeypatch.setattr(vault, "read_note", read_or_deadlock)
        report = run_inbox_pipeline(
            settings=settings,
            taxonomy=taxonomy,
            vault=vault,
            llm=MockLLM(analysis),
        )

        assert report.items_created == 1
        assert len(report.errors) == 1 and "Placeholder.md" in report.errors[0]
        assert (tmp_path / "00 Inbox" / "Placeholder.md").exists()  # left for the next run


class FakeBatchProvider:
    def __init__(self, analysis: ContentAnalysis) -> None:
        self.analysis = analysis
        self.submitted: list[list] = []

    def submit_batch(self, requests):
        self.submitted.append(requests)
        return f"batch_{len(self.submitted)}"

    def get_batch_results(self, batch_id):
        from second_brain.llm.batch import BatchResult

        requests = self.submitted[int(batch_id.split("_")[1]) - 1]
        return [BatchResult(custom_id=r.custom_id, analysis=self.analysis) for r in requests]


_CLIPPING = "---\ntitle: Test Article\nsource: https://example.com\nstatus: inbox\n---\n\nBody text.\n"


class TestNoDuplicateBatches:
    def test_second_submit_skips_items_in_pending_batch(
        self, tmp_path, settings, taxonomy, analysis
    ) -> None:
        from second_brain.pipeline.batch_state import BatchStateManager

        vault = FilesystemBackend(tmp_path)
        _seed_inbox_note(vault, "Test Article.md", _CLIPPING)
        batch_state = BatchStateManager(tmp_path / "batch_state.yaml")
        provider = FakeBatchProvider(analysis)

        for _ in range(2):
            run_inbox_pipeline(
                settings=settings, taxonomy=taxonomy, vault=vault, llm=MockLLM(analysis),
                batch_provider=provider, batch_state=batch_state, no_wait=True,
            )

        assert len(provider.submitted) == 1
        assert len(batch_state.get_pending()) == 1

    def test_finalizing_an_already_moved_item_does_not_duplicate(
        self, tmp_path, settings, taxonomy, analysis
    ) -> None:
        """Two batches holding the same clipping (e.g. submitted before this fix)
        must produce one note, not 'Title.md' + 'Title 1.md'."""
        from second_brain.pipeline.inbox import finalize_inbox_batch
        from second_brain.pipeline.batch_state import BatchStateManager

        vault = FilesystemBackend(tmp_path)
        _seed_inbox_note(vault, "Test Article.md", _CLIPPING)
        provider = FakeBatchProvider(analysis)
        # Separate state files simulate two submits that did not see each other
        for n in (1, 2):
            run_inbox_pipeline(
                settings=settings, taxonomy=taxonomy, vault=vault, llm=MockLLM(analysis),
                batch_provider=provider,
                batch_state=BatchStateManager(tmp_path / f"state{n}.yaml"), no_wait=True,
            )

        outcomes = []
        for n in (1, 2):
            pending = BatchStateManager(tmp_path / f"state{n}.yaml").get_pending()[0]
            outcomes.append(finalize_inbox_batch(
                results=provider.get_batch_results(pending.batch_id), pending=pending,
                vault=vault, settings=settings, taxonomy=taxonomy, dry_run=False,
            ))

        assert outcomes == [(1, 0, []), (0, 1, [])]
        assert [p.name for p in (tmp_path / "01 Notes").iterdir()] == ["Test Article.md"]
        assert not (tmp_path / "00 Inbox" / "Test Article.md").exists()
