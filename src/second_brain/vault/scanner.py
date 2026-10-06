"""Scan 00 Inbox/ for unprocessed items."""

from __future__ import annotations

import logging
from pathlib import Path

import frontmatter
from pydantic import ValidationError

from second_brain.dates import parse_date
from second_brain.models import IngestItem
from second_brain.vault.base import VaultBackend

logger = logging.getLogger(__name__)

# Web clippers store the original URL under different frontmatter keys depending
# on the template. Check them in priority order so the URL is never lost.
_URL_KEYS = ("source", "url", "link", "clipped_url", "permalink")


def _extract_source_url(metadata: dict) -> str:
    """Return the first non-empty URL-like frontmatter value, or ''."""
    for key in _URL_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _as_list(value: object) -> list[str]:
    """Normalize a scalar/list/None frontmatter value into a list of strings."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def scan_inbox(
    backend: VaultBackend,
    inbox_folder: str = "00 Inbox",
    errors: list[str] | None = None,
) -> list[IngestItem]:
    """Scan the inbox folder and return unprocessed items.

    A note that can't be read (e.g. an iCloud placeholder that fails to
    download) or whose frontmatter can't be validated is skipped and reported
    in *errors*, so it never stops the scan; it stays in the inbox for the
    next run.
    """
    items: list[IngestItem] = []

    for path in backend.list_folder(inbox_folder):
        if path.suffix == ".md":
            try:
                item = _parse_markdown_item(backend, path)
            except OSError as exc:
                logger.error("Could not read inbox item %s, skipped: %s", path.name, exc)
                if errors is not None:
                    errors.append(f"{path.name}: could not read ({exc})")
                continue
            except ValidationError as exc:
                logger.error("Invalid frontmatter in inbox item %s, skipped: %s", path.name, exc)
                if errors is not None:
                    errors.append(f"{path.name}: invalid frontmatter ({exc})")
                continue
            if item is not None:
                items.append(item)
        elif path.suffix == ".pdf":
            items.append(_create_pdf_item(path))
        # Skip other file types silently

    return items


def _parse_markdown_item(backend: VaultBackend, path: Path) -> IngestItem | None:
    """Parse a markdown file from inbox. Returns None if already classified."""
    content = backend.read_note(path)
    post = frontmatter.loads(content)

    # Skip items that are already classified or processed
    status = post.metadata.get("status", "inbox")
    if status in ("classified", "processed"):
        return None

    return IngestItem(
        source_type="inbox",
        title=str(post.metadata.get("title") or path.stem),
        content=post.content,
        source_url=_extract_source_url(post.metadata),
        author=_as_list(post.metadata.get("author")),
        published=parse_date(post.metadata.get("published")),
        metadata={
            "original_path": str(path),
            "existing_frontmatter": dict(post.metadata),
        },
    )


def _create_pdf_item(path: Path) -> IngestItem:
    """Create an IngestItem for a raw PDF file."""
    return IngestItem(
        source_type="inbox",
        title=path.stem,
        content=f"[PDF file: {path.name}]",
        metadata={
            "original_path": str(path),
            "is_pdf": True,
        },
    )
