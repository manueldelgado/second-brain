"""Gmail API client — auth, search, fetch, and extract email content."""

from __future__ import annotations

import base64
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from markdownify import markdownify
import trafilatura

from second_brain.models import IngestItem

logger = logging.getLogger(__name__)


def _extract_display_name(msg: dict) -> str:
    """Extract the display name from a Gmail message's From header.

    Returns the portion before ``<email>`` (stripped of quotes), or the
    full header value when no angle-bracket format is found.
    """
    headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
    from_header = headers.get("from", "")
    if "<" in from_header:
        return from_header.split("<")[0].strip().strip('"')
    return from_header


class GmailClient:
    """Gmail API wrapper for fetching newsletter emails."""

    def __init__(
        self,
        credentials_file: Path,
        token_file: Path,
        scopes: list[str] | None = None,
    ) -> None:
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.scopes = scopes or ["https://www.googleapis.com/auth/gmail.readonly"]
        self._service = None

    @property
    def service(self):
        """Lazy-init the Gmail API service."""
        if self._service is None:
            self._service = self._build_service()
        return self._service

    def _build_service(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if self.token_file.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_file), self.scopes)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_file), self.scopes
                )
                creds = flow.run_local_server(port=0)
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(creds.to_json())

        return build("gmail", "v1", credentials=creds)

    def get_or_create_label(self, name: str) -> str:
        """Return the label ID for *name*, creating the label if it doesn't exist.

        Gmail's API requires label IDs, not names.  Labels are cached on the
        client instance so the list RPC is only made once per process.
        """
        if not hasattr(self, "_label_cache"):
            self._label_cache: dict[str, str] = {}

        if name in self._label_cache:
            return self._label_cache[name]

        labels = self.service.users().labels().list(userId="me").execute().get("labels", [])
        for label in labels:
            self._label_cache[label["name"]] = label["id"]

        if name not in self._label_cache:
            created = self.service.users().labels().create(
                userId="me", body={"name": name}
            ).execute()
            self._label_cache[name] = created["id"]
            logger.info("Created Gmail label '%s' (id=%s)", name, created["id"])

        return self._label_cache[name]

    def apply_label(self, message_id: str, label_id: str) -> None:
        """Apply a label to a message by ID."""
        self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]},
        ).execute()

    def search_emails(
        self,
        sender: str,
        after_date: date | datetime,
        sender_name: str | None = None,
    ) -> list[dict]:
        """Search for emails from a sender after the given date/datetime.

        If a datetime is provided, it will be converted to a date for the query.
        Gmail's 'after:' filter matches emails after midnight on that date.

        When *sender_name* is provided, the ``from:`` clause uses the display
        name (quoted) instead of the email address, which lets Gmail do a
        coarse server-side match on the human-readable sender name.
        """
        if isinstance(after_date, datetime):
            query_date = after_date.date()
        else:
            query_date = after_date
        from_value = f'"{sender_name}"' if sender_name else sender
        query = f"from:{from_value} after:{query_date.strftime('%Y/%m/%d')}"
        logger.debug("Gmail query: %s", query)

        results = self.service.users().messages().list(
            userId="me", q=query, maxResults=50
        ).execute()

        messages = results.get("messages", [])
        logger.debug("Found %d messages for %s", len(messages), sender)
        return messages

    def fetch_email(self, message_id: str) -> dict:
        """Fetch full email content by message ID."""
        return self.service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()

    def fetch_newsletters(
        self,
        sender_email: str,
        newsletter_name: str,
        after_date: date | datetime,
        min_internal_date: datetime | None = None,
        sender_name: str | None = None,
    ) -> list[IngestItem]:
        """Fetch all newsletter emails from a sender after the given date.

        *after_date* drives the coarse Gmail ``after:YYYY/MM/DD`` query.
        *min_internal_date*, when provided, is a precise timestamp cutoff:
        any email whose ``internalDate`` is at or before it is skipped before
        content extraction, avoiding duplicate processing between runs.
        *sender_name*, when provided, restricts results to emails whose From
        header display name contains *sender_name* (case-insensitive).

        Returns IngestItems sorted oldest-first.
        """
        messages = self.search_emails(sender_email, after_date, sender_name=sender_name)
        cutoff_ms = int(min_internal_date.timestamp() * 1000) if min_internal_date else None
        items: list[IngestItem] = []

        for msg_ref in messages:
            try:
                msg = self.fetch_email(msg_ref["id"])
                if cutoff_ms is not None:
                    if int(msg.get("internalDate", 0)) <= cutoff_ms:
                        logger.debug("Skipping already-processed message %s", msg_ref["id"])
                        continue
                if sender_name is not None:
                    display_name = _extract_display_name(msg)
                    if sender_name.lower() not in display_name.lower():
                        logger.debug(
                            "Skipping message %s — display name '%s' doesn't match sender_name '%s'",
                            msg_ref["id"], display_name, sender_name,
                        )
                        continue
                item = self._message_to_ingest_item(msg, newsletter_name)
                items.append(item)
            except Exception:
                logger.exception("Failed to process message %s", msg_ref["id"])

        # Sort oldest first by internalDate
        items.sort(key=lambda x: x.metadata.get("internal_date", 0))
        return items

    def _message_to_ingest_item(self, msg: dict, newsletter_name: str) -> IngestItem:
        """Convert a Gmail API message to an IngestItem."""
        headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}

        subject = headers.get("subject", "Untitled")
        from_header = headers.get("from", "")
        internal_date_ms = int(msg.get("internalDate", 0))
        published = datetime.fromtimestamp(
            internal_date_ms / 1000, tz=timezone.utc
        ).date()

        # Extract body
        html_body = self._extract_body(msg["payload"], "text/html")
        text_body = self._extract_body(msg["payload"], "text/plain")

        if html_body:
            # Extract main content from HTML (removes boilerplate, ads, navigation)
            extracted = trafilatura.extract(html_body, include_comments=False)
            if extracted:
                content = extracted
            else:
                # Fallback to markdownify if trafilatura extracts nothing
                content = markdownify(html_body, heading_style="ATX", strip=["img", "script"])
        elif text_body:
            content = text_body
        else:
            content = ""

        # Extract author name from "From" header
        author_name = _extract_display_name(msg) or newsletter_name

        return IngestItem(
            source_type="gmail",
            title=subject,
            content=content.strip(),
            raw_html=html_body,
            source_url="",
            author=[f"[[{author_name}]]"],
            published=published,
            newsletter_name=newsletter_name,
            metadata={
                "message_id": msg["id"],
                "thread_id": msg.get("threadId", ""),
                "internal_date": internal_date_ms,
                "internal_date_iso": datetime.fromtimestamp(
                    internal_date_ms / 1000, tz=timezone.utc
                ).isoformat(),
            },
        )

    def _extract_body(self, payload: dict, mime_type: str) -> str | None:
        """Recursively extract body content of the given MIME type."""
        if payload.get("mimeType") == mime_type and "body" in payload:
            data = payload["body"].get("data")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        for part in payload.get("parts", []):
            result = self._extract_body(part, mime_type)
            if result:
                return result

        return None
