"""Tests for web enrichment — URL cleaning and article fetch/extract."""

from __future__ import annotations

import pytest

import second_brain.enrich.web as web
from second_brain.enrich.web import WebArticle, clean_url, fetch_article


class TestCleanUrl:
    def test_strips_utm_and_tracking_params(self) -> None:
        url = "https://ex.com/a?utm_source=x&utm_campaign=y&id=42&fbclid=z"
        assert clean_url(url) == "https://ex.com/a?id=42"

    def test_no_query_string_unchanged(self) -> None:
        assert clean_url("https://ex.com/a/b") == "https://ex.com/a/b"

    def test_all_tracking_drops_query_entirely(self) -> None:
        assert clean_url("https://ex.com/a?utm_source=x&utm_medium=y") == "https://ex.com/a"

    def test_keeps_meaningful_params(self) -> None:
        url = "https://ex.com/search?q=hello&page=2"
        assert clean_url(url) == "https://ex.com/search?q=hello&page=2"

    def test_empty_string(self) -> None:
        assert clean_url("") == ""

    def test_preserves_fragment_and_path(self) -> None:
        url = "https://ex.com/a/b?utm_source=x#section"
        assert clean_url(url) == "https://ex.com/a/b#section"


class TestFetchArticle:
    def test_empty_url_returns_none(self) -> None:
        assert fetch_article("") is None

    def test_download_failure_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(web.trafilatura, "fetch_url", lambda url, config=None: None)
        assert fetch_article("https://x.com") is None

    def test_extracts_text_and_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(web.trafilatura, "fetch_url", lambda url, config=None: "<html>x</html>")
        monkeypatch.setattr(web.trafilatura, "extract", lambda *a, **k: "  body text  ")

        class _Meta:
            author = "Jane Doe"
            date = "2026-01-02"
            title = "The Title"
            url = "https://x.com/canonical"

        monkeypatch.setattr(web.trafilatura, "extract_metadata", lambda html: _Meta())

        article = fetch_article("https://x.com/post")
        assert isinstance(article, WebArticle)
        assert article.text == "body text"  # stripped
        assert article.author == "Jane Doe"
        assert article.date == "2026-01-02"
        assert article.title == "The Title"
        assert article.canonical_url == "https://x.com/canonical"

    def test_downloaded_but_no_extractable_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # JS-only page: downloads, but no article text can be extracted.
        monkeypatch.setattr(web.trafilatura, "fetch_url", lambda url, config=None: "<html>shell</html>")
        monkeypatch.setattr(web.trafilatura, "extract", lambda *a, **k: None)
        monkeypatch.setattr(web.trafilatura, "extract_metadata", lambda html: None)

        article = fetch_article("https://x.com/js-app")
        assert article is not None
        assert article.text is None  # caller falls back to captured content

    def test_extraction_error_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(web.trafilatura, "fetch_url", lambda url, config=None: "<html>x</html>")

        def _boom(*a, **k):
            raise RuntimeError("parser exploded")

        monkeypatch.setattr(web.trafilatura, "extract", _boom)
        monkeypatch.setattr(web.trafilatura, "extract_metadata", lambda html: None)

        article = fetch_article("https://x.com/post")
        assert article is not None
        assert article.text is None
