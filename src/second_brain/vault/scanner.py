"""Scan 00 Inbox/ for unprocessed items."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import frontmatter

from second_brain.models import IngestItem
from second_brain.vault.base import VaultBackend


def scan_inbox(backend: VaultBackend, inbox_folder: str = "00 Inbox") -> list[IngestItem]:
    """Scan the inbox folder and return unprocessed items."""
    items: list[IngestItem] = []

    for path in backend.list_folder(inbox_folder):
        if path.suffix == ".md":
            item = _parse_markdown_item(backend, path)
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
        title=post.metadata.get("title", path.stem),
        content=post.content,
        source_url=post.metadata.get("source", ""),
        author=post.metadata.get("author") or [],
        published=post.metadata.get("published"),
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
