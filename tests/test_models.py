"""Tests for second_brain.models — domain model construction and serialization."""

from __future__ import annotations

from datetime import date

import yaml
import pytest

from second_brain.models import ContentAnalysis, IngestItem, NoteFrontmatter


# ===================================================================
# ContentAnalysis
# ===================================================================


class TestContentAnalysis:
    """Basic construction of ContentAnalysis."""

    def test_construction(self) -> None:
        ca = ContentAnalysis(
            summary="A brief summary of the content.",
            key_takeaways=["Point one", "Point two"],
            tags=["ai/llm", "func/research"],
            content_type="newsletter",
            description="Short description of the content.",
        )
        assert ca.summary == "A brief summary of the content."
        assert len(ca.key_takeaways) == 2
        assert ca.tags == ["ai/llm", "func/research"]
        assert ca.content_type == "newsletter"
        assert ca.description == "Short description of the content."

    def test_empty_takeaways(self) -> None:
        ca = ContentAnalysis(
            summary="s",
            key_takeaways=[],
            tags=[],
            content_type="clipping",
            description="d",
        )
        assert ca.key_takeaways == []
        assert ca.tags == []


# ===================================================================
# NoteFrontmatter — construction
# ===================================================================


class TestNoteFrontmatterConstruction:
    """Verify field defaults and required fields."""

    def _base_kwargs(self) -> dict:
        return {
            "title": "Test Note",
            "source": "https://example.com",
            "author": ["[[Alice]]"],
            "created": date(2026, 3, 8),
            "type": "clipping",
            "status": "classified",
            "tags": ["ai/llm"],
        }

    def test_basic_construction(self) -> None:
        fm = NoteFrontmatter(**self._base_kwargs())
        assert fm.title == "Test Note"
        assert fm.author == ["[[Alice]]"]
        assert fm.created == date(2026, 3, 8)
        assert fm.status == "classified"

    def test_default_optionals_are_none(self) -> None:
        fm = NoteFrontmatter(**self._base_kwargs())
        assert fm.newsletter is None
        assert fm.published is None
        assert fm.rating is None
        assert fm.journal is None
        assert fm.doi is None
        assert fm.year is None
        assert fm.isbn is None
        assert fm.description == ""

    def test_missing_required_field_raises(self) -> None:
        with pytest.raises(Exception):
            NoteFrontmatter(
                title="Oops",
                # source is missing
                author=["[[Bob]]"],
                created=date(2026, 1, 1),
                type="note",
                status="inbox",
                tags=[],
            )  # type: ignore[call-arg]


# ===================================================================
# NoteFrontmatter.to_yaml() — general behavior
# ===================================================================


class TestNoteFrontmatterToYaml:
    """Ensure to_yaml() produces valid YAML with --- delimiters."""

    def _base_fm(self, **overrides) -> NoteFrontmatter:
        defaults = {
            "title": "YAML Test",
            "source": "https://example.com/article",
            "author": ["[[Author One]]", "[[Author Two]]"],
            "created": date(2026, 3, 8),
            "type": "clipping",
            "status": "classified",
            "tags": ["ai/llm", "func/blog"],
            "description": "A test description.",
        }
        defaults.update(overrides)
        return NoteFrontmatter(**defaults)

    def test_yaml_starts_and_ends_with_delimiters(self) -> None:
        output = self._base_fm().to_yaml()
        assert output.startswith("---\n")
        assert output.endswith("\n---")

    def test_yaml_is_parseable(self) -> None:
        output = self._base_fm().to_yaml()
        # Strip delimiters and parse the body
        body = output.removeprefix("---\n").removesuffix("\n---")
        parsed = yaml.safe_load(body)
        assert isinstance(parsed, dict)
        assert parsed["title"] == "YAML Test"
        assert parsed["source"] == "https://example.com/article"
        assert parsed["created"] == "2026-03-08"
        assert parsed["tags"] == ["ai/llm", "func/blog"]

    def test_author_list_preserved(self) -> None:
        output = self._base_fm().to_yaml()
        body = output.removeprefix("---\n").removesuffix("\n---")
        parsed = yaml.safe_load(body)
        assert parsed["author"] == ["[[Author One]]", "[[Author Two]]"]

    def test_description_included(self) -> None:
        output = self._base_fm().to_yaml()
        body = output.removeprefix("---\n").removesuffix("\n---")
        parsed = yaml.safe_load(body)
        assert parsed["description"] == "A test description."

    def test_optional_none_fields_omitted(self) -> None:
        """Fields that are None should not appear in output for generic types."""
        output = self._base_fm().to_yaml()
        body = output.removeprefix("---\n").removesuffix("\n---")
        parsed = yaml.safe_load(body)
        assert "newsletter" not in parsed
        assert "journal" not in parsed
        assert "doi" not in parsed
        assert "isbn" not in parsed
        assert "year" not in parsed
        assert "published" not in parsed
        assert "rating" not in parsed


# ===================================================================
# NoteFrontmatter.to_yaml() — newsletter type
# ===================================================================


class TestNoteFrontmatterNewsletter:
    """Newsletter notes should include the newsletter field."""

    def test_newsletter_field_present(self) -> None:
        fm = NoteFrontmatter(
            title="NL Issue #42",
            source="email",
            author=["[[Sender]]"],
            created=date(2026, 3, 1),
            type="newsletter",
            status="classified",
            tags=["ai/llm"],
            newsletter="The Weekly AI",
            published=date(2026, 2, 28),
        )
        output = fm.to_yaml()
        body = output.removeprefix("---\n").removesuffix("\n---")
        parsed = yaml.safe_load(body)
        assert parsed["newsletter"] == "The Weekly AI"
        assert parsed["published"] == "2026-02-28"

    def test_newsletter_none_omitted(self) -> None:
        fm = NoteFrontmatter(
            title="Non-NL Note",
            source="web",
            author=["[[A]]"],
            created=date(2026, 3, 1),
            type="clipping",
            status="inbox",
            tags=[],
        )
        output = fm.to_yaml()
        assert "newsletter" not in output


# ===================================================================
# NoteFrontmatter.to_yaml() — paper type
# ===================================================================


class TestNoteFrontmatterPaper:
    """Paper notes should include journal, doi, and year."""

    def test_paper_fields_present(self) -> None:
        fm = NoteFrontmatter(
            title="A Study on LLMs",
            source="https://arxiv.org/abs/1234",
            author=["[[Researcher A]]"],
            created=date(2026, 3, 8),
            type="paper",
            status="processed",
            tags=["ai/llm", "func/research"],
            journal="Nature Machine Intelligence",
            doi="10.1234/nmi.5678",
            year=2026,
        )
        output = fm.to_yaml()
        body = output.removeprefix("---\n").removesuffix("\n---")
        parsed = yaml.safe_load(body)
        assert parsed["journal"] == "Nature Machine Intelligence"
        assert parsed["doi"] == "10.1234/nmi.5678"
        assert parsed["year"] == 2026

    def test_paper_fields_default_to_empty_string(self) -> None:
        """When journal/doi are None, to_yaml() should output empty strings."""
        fm = NoteFrontmatter(
            title="Paper Without Details",
            source="https://arxiv.org",
            author=["[[Researcher B]]"],
            created=date(2026, 3, 8),
            type="paper",
            status="inbox",
            tags=[],
        )
        output = fm.to_yaml()
        body = output.removeprefix("---\n").removesuffix("\n---")
        parsed = yaml.safe_load(body)
        assert parsed["journal"] == ""
        assert parsed["doi"] == ""
        assert parsed["year"] is None


# ===================================================================
# NoteFrontmatter.to_yaml() — book type
# ===================================================================


class TestNoteFrontmatterBook:
    """Book notes should include year and isbn."""

    def test_book_fields_present(self) -> None:
        fm = NoteFrontmatter(
            title="Deep Work",
            source="book",
            author=["[[Cal Newport]]"],
            created=date(2026, 3, 8),
            type="book",
            status="processed",
            tags=["career/productivity", "func/book-review"],
            year=2016,
            isbn="978-1455586691",
        )
        output = fm.to_yaml()
        body = output.removeprefix("---\n").removesuffix("\n---")
        parsed = yaml.safe_load(body)
        assert parsed["year"] == 2016
        assert parsed["isbn"] == "978-1455586691"

    def test_book_isbn_default_empty(self) -> None:
        fm = NoteFrontmatter(
            title="Some Book",
            source="book",
            author=["[[Author]]"],
            created=date(2026, 3, 8),
            type="book",
            status="inbox",
            tags=[],
        )
        output = fm.to_yaml()
        body = output.removeprefix("---\n").removesuffix("\n---")
        parsed = yaml.safe_load(body)
        assert parsed["isbn"] == ""
        assert parsed["year"] is None

    def test_book_does_not_include_paper_fields(self) -> None:
        fm = NoteFrontmatter(
            title="A Book",
            source="book",
            author=["[[A]]"],
            created=date(2026, 3, 8),
            type="book",
            status="inbox",
            tags=[],
        )
        output = fm.to_yaml()
        body = output.removeprefix("---\n").removesuffix("\n---")
        parsed = yaml.safe_load(body)
        assert "journal" not in parsed
        assert "doi" not in parsed


# ===================================================================
# NoteFrontmatter.to_yaml() — rating field
# ===================================================================


class TestNoteFrontmatterRating:
    """Rating is optional and should appear when set."""

    def test_rating_included_when_set(self) -> None:
        fm = NoteFrontmatter(
            title="Rated Note",
            source="web",
            author=["[[A]]"],
            created=date(2026, 3, 8),
            type="clipping",
            status="processed",
            tags=[],
            rating=5,
        )
        output = fm.to_yaml()
        body = output.removeprefix("---\n").removesuffix("\n---")
        parsed = yaml.safe_load(body)
        assert parsed["rating"] == 5

    def test_rating_omitted_when_none(self) -> None:
        fm = NoteFrontmatter(
            title="Unrated",
            source="web",
            author=["[[A]]"],
            created=date(2026, 3, 8),
            type="clipping",
            status="inbox",
            tags=[],
        )
        output = fm.to_yaml()
        body = output.removeprefix("---\n").removesuffix("\n---")
        parsed = yaml.safe_load(body)
        assert "rating" not in parsed


# ===================================================================
# IngestItem
# ===================================================================


class TestIngestItem:
    """IngestItem construction with various source_types."""

    def test_gmail_source(self) -> None:
        item = IngestItem(
            source_type="gmail",
            title="Weekly AI Digest #10",
            content="Full email body here...",
            raw_html="<p>Full email body here...</p>",
            author=["[[Sender Name]]"],
            newsletter_name="AI Digest",
            published=date(2026, 3, 7),
        )
        assert item.source_type == "gmail"
        assert item.raw_html == "<p>Full email body here...</p>"
        assert item.newsletter_name == "AI Digest"
        assert item.published == date(2026, 3, 7)

    def test_inbox_source(self) -> None:
        item = IngestItem(
            source_type="inbox",
            title="Random Web Clipping",
            content="Some clipped content.",
            source_url="https://example.com/article",
        )
        assert item.source_type == "inbox"
        assert item.source_url == "https://example.com/article"
        assert item.raw_html is None
        assert item.newsletter_name is None

    def test_invalid_source_type_rejected(self) -> None:
        with pytest.raises(Exception):
            IngestItem(
                source_type="rss",  # type: ignore[arg-type]
                title="Bad Source",
                content="...",
            )

    def test_defaults(self) -> None:
        item = IngestItem(
            source_type="inbox",
            title="Minimal",
            content="c",
        )
        assert item.raw_html is None
        assert item.source_url == ""
        assert item.author == []
        assert item.published is None
        assert item.newsletter_name is None
        assert item.metadata == {}

    def test_metadata_dict(self) -> None:
        item = IngestItem(
            source_type="gmail",
            title="With Meta",
            content="c",
            metadata={"gmail_id": "abc123", "thread_id": "xyz"},
        )
        assert item.metadata["gmail_id"] == "abc123"
        assert item.metadata["thread_id"] == "xyz"
