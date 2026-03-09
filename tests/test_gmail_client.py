"""Tests for Gmail client — parsing and extraction."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from second_brain.gmail.client import GmailClient, _extract_display_name


@pytest.fixture
def gmail_client(tmp_path: Path) -> GmailClient:
    """Create a GmailClient without actual credentials."""
    return GmailClient(
        credentials_file=tmp_path / "creds.json",
        token_file=tmp_path / "token.json",
    )


def _encode_body(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


class TestExtractBody:
    def test_simple_text_plain(self, gmail_client: GmailClient) -> None:
        payload = {
            "mimeType": "text/plain",
            "body": {"data": _encode_body("Hello world")},
        }
        result = gmail_client._extract_body(payload, "text/plain")
        assert result == "Hello world"

    def test_simple_text_html(self, gmail_client: GmailClient) -> None:
        html = "<p>Hello <b>world</b></p>"
        payload = {
            "mimeType": "text/html",
            "body": {"data": _encode_body(html)},
        }
        result = gmail_client._extract_body(payload, "text/html")
        assert result == html

    def test_multipart_extracts_html(self, gmail_client: GmailClient) -> None:
        html = "<h1>Newsletter</h1><p>Content here</p>"
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _encode_body("plain text")},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": _encode_body(html)},
                },
            ],
        }
        result = gmail_client._extract_body(payload, "text/html")
        assert result == html

    def test_missing_mime_type_returns_none(self, gmail_client: GmailClient) -> None:
        payload = {
            "mimeType": "text/plain",
            "body": {"data": _encode_body("text")},
        }
        result = gmail_client._extract_body(payload, "text/html")
        assert result is None

    def test_nested_multipart(self, gmail_client: GmailClient) -> None:
        html = "<p>Deep content</p>"
        payload = {
            "mimeType": "multipart/mixed",
            "body": {},
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "body": {},
                    "parts": [
                        {
                            "mimeType": "text/html",
                            "body": {"data": _encode_body(html)},
                        },
                    ],
                },
            ],
        }
        result = gmail_client._extract_body(payload, "text/html")
        assert result == html


class TestMessageToIngestItem:
    def test_basic_conversion(self, gmail_client: GmailClient) -> None:
        msg = {
            "id": "msg123",
            "threadId": "thread456",
            "internalDate": "1741363200000",  # 2025-03-07T12:00:00Z
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "Subject", "value": "Weekly AI Roundup"},
                    {"name": "From", "value": "Benedict Evans <ben@ben-evans.com>"},
                ],
                "body": {"data": _encode_body("Newsletter content here")},
            },
        }
        item = gmail_client._message_to_ingest_item(msg, "Benedict Evans")
        assert item.title == "Weekly AI Roundup"
        assert item.source_type == "gmail"
        assert item.newsletter_name == "Benedict Evans"
        assert item.metadata["message_id"] == "msg123"
        assert "Newsletter content here" in item.content

    def test_html_converted_to_markdown(self, gmail_client: GmailClient) -> None:
        html = "<h1>Title</h1><p>Paragraph with <b>bold</b></p>"
        msg = {
            "id": "msg1",
            "threadId": "t1",
            "internalDate": "1741363200000",
            "payload": {
                "mimeType": "text/html",
                "headers": [
                    {"name": "Subject", "value": "Test"},
                    {"name": "From", "value": "test@example.com"},
                ],
                "body": {"data": _encode_body(html)},
            },
        }
        item = gmail_client._message_to_ingest_item(msg, "Test Newsletter")
        # markdownify should convert HTML to markdown
        assert "Title" in item.content
        assert "**bold**" in item.content

    def test_author_extraction_from_header(self, gmail_client: GmailClient) -> None:
        msg = {
            "id": "msg1",
            "internalDate": "1741363200000",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "Subject", "value": "Test"},
                    {"name": "From", "value": '"John Doe" <john@example.com>'},
                ],
                "body": {"data": _encode_body("content")},
            },
        }
        item = gmail_client._message_to_ingest_item(msg, "Newsletter")
        assert item.author == ["[[John Doe]]"]

    def test_published_date_from_internal_date(self, gmail_client: GmailClient) -> None:
        msg = {
            "id": "msg1",
            "internalDate": "1741363200000",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "Subject", "value": "Test"},
                    {"name": "From", "value": "x@y.com"},
                ],
                "body": {"data": _encode_body("content")},
            },
        }
        item = gmail_client._message_to_ingest_item(msg, "NL")
        assert item.published is not None


class TestExtractDisplayName:
    def test_with_angle_brackets(self) -> None:
        msg = {"payload": {"headers": [{"name": "From", "value": "Alice Blog <noreply@wp.com>"}]}}
        assert _extract_display_name(msg) == "Alice Blog"

    def test_with_quoted_name(self) -> None:
        msg = {"payload": {"headers": [{"name": "From", "value": '"Alice Blog" <noreply@wp.com>'}]}}
        assert _extract_display_name(msg) == "Alice Blog"

    def test_bare_email(self) -> None:
        msg = {"payload": {"headers": [{"name": "From", "value": "noreply@wp.com"}]}}
        assert _extract_display_name(msg) == "noreply@wp.com"

    def test_missing_from_header(self) -> None:
        msg = {"payload": {"headers": [{"name": "Subject", "value": "test"}]}}
        assert _extract_display_name(msg) == ""


class TestSearchEmails:
    def test_search_constructs_correct_query(self, gmail_client: GmailClient) -> None:
        from datetime import date

        mock_service = MagicMock()
        mock_list = mock_service.users().messages().list
        mock_list.return_value.execute.return_value = {"messages": []}
        gmail_client._service = mock_service

        gmail_client.search_emails("ben@ben-evans.com", date(2026, 3, 1))

        mock_list.assert_called_once()
        call_kwargs = mock_list.call_args[1]
        assert call_kwargs["userId"] == "me"
        assert "from:ben@ben-evans.com" in call_kwargs["q"]
        assert "after:2026/03/01" in call_kwargs["q"]

    def test_search_with_sender_name_uses_display_name(self, gmail_client: GmailClient) -> None:
        from datetime import date

        mock_service = MagicMock()
        mock_list = mock_service.users().messages().list
        mock_list.return_value.execute.return_value = {"messages": []}
        gmail_client._service = mock_service

        gmail_client.search_emails("noreply@wp.com", date(2026, 3, 1), sender_name="Alice Blog")

        call_kwargs = mock_list.call_args[1]
        assert 'from:"Alice Blog"' in call_kwargs["q"]
        assert "noreply@wp.com" not in call_kwargs["q"]


class TestFetchNewslettersFiltering:
    """Tests for sender_name client-side filtering in fetch_newsletters."""

    def _make_msg(self, msg_id: str, from_header: str, internal_date: str = "1741363200000") -> dict:
        return {
            "id": msg_id,
            "threadId": "t1",
            "internalDate": internal_date,
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "Subject", "value": f"Subject {msg_id}"},
                    {"name": "From", "value": from_header},
                ],
                "body": {"data": _encode_body("content")},
            },
        }

    def test_no_sender_name_returns_all(self, gmail_client: GmailClient) -> None:
        from datetime import date

        msg_a = self._make_msg("m1", "Alice Blog <noreply@wp.com>")
        msg_b = self._make_msg("m2", "Bob Digest <noreply@wp.com>")

        mock_service = MagicMock()
        mock_list = mock_service.users().messages().list
        mock_list.return_value.execute.return_value = {"messages": [{"id": "m1"}, {"id": "m2"}]}
        mock_get = mock_service.users().messages().get
        mock_get.return_value.execute.side_effect = [msg_a, msg_b]
        gmail_client._service = mock_service

        items = gmail_client.fetch_newsletters("noreply@wp.com", "Test", date(2026, 3, 1))
        assert len(items) == 2

    def test_sender_name_filters_non_matching(self, gmail_client: GmailClient) -> None:
        from datetime import date

        msg_a = self._make_msg("m1", "Alice Blog <noreply@wp.com>")
        msg_b = self._make_msg("m2", "Bob Digest <noreply@wp.com>")

        mock_service = MagicMock()
        mock_list = mock_service.users().messages().list
        mock_list.return_value.execute.return_value = {"messages": [{"id": "m1"}, {"id": "m2"}]}
        mock_get = mock_service.users().messages().get
        mock_get.return_value.execute.side_effect = [msg_a, msg_b]
        gmail_client._service = mock_service

        items = gmail_client.fetch_newsletters(
            "noreply@wp.com", "Alice Blog", date(2026, 3, 1), sender_name="Alice Blog",
        )
        assert len(items) == 1
        assert items[0].title == "Subject m1"

    def test_sender_name_case_insensitive(self, gmail_client: GmailClient) -> None:
        from datetime import date

        msg = self._make_msg("m1", "alice blog <noreply@wp.com>")

        mock_service = MagicMock()
        mock_list = mock_service.users().messages().list
        mock_list.return_value.execute.return_value = {"messages": [{"id": "m1"}]}
        mock_get = mock_service.users().messages().get
        mock_get.return_value.execute.return_value = msg
        gmail_client._service = mock_service

        items = gmail_client.fetch_newsletters(
            "noreply@wp.com", "Alice Blog", date(2026, 3, 1), sender_name="Alice Blog",
        )
        assert len(items) == 1

    def test_sender_name_bare_email_filtered_out(self, gmail_client: GmailClient) -> None:
        from datetime import date

        msg = self._make_msg("m1", "noreply@wp.com")

        mock_service = MagicMock()
        mock_list = mock_service.users().messages().list
        mock_list.return_value.execute.return_value = {"messages": [{"id": "m1"}]}
        mock_get = mock_service.users().messages().get
        mock_get.return_value.execute.return_value = msg
        gmail_client._service = mock_service

        items = gmail_client.fetch_newsletters(
            "noreply@wp.com", "Test", date(2026, 3, 1), sender_name="Alice Blog",
        )
        assert len(items) == 0
