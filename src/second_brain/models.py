"""Domain models for Second Brain content processing."""

from __future__ import annotations

from datetime import date
from typing import Literal

import yaml
from pydantic import BaseModel


class ContentAnalysis(BaseModel):
    """LLM output — provider-agnostic structured classification."""

    summary: str
    key_takeaways: list[str]
    tags: list[str]
    content_type: str
    description: str


class NoteFrontmatter(BaseModel):
    """Complete frontmatter for an Obsidian note."""

    title: str
    source: str
    author: list[str]
    created: date
    type: str
    status: str
    tags: list[str]
    description: str = ""
    newsletter: str | None = None
    published: date | None = None
    gmail_url: str | None = None
    rating: int | None = None
    journal: str | None = None
    doi: str | None = None
    year: int | None = None
    isbn: str | None = None

    def to_yaml(self) -> str:
        """Render as YAML frontmatter block (with --- delimiters)."""
        data: dict = {
            "title": self.title,
            "source": self.source,
            "author": self.author,
        }
        if self.newsletter is not None:
            data["newsletter"] = self.newsletter
        if self.published is not None:
            data["published"] = self.published.isoformat()
        if self.gmail_url is not None:
            data["gmail_url"] = self.gmail_url
        data["created"] = self.created.isoformat()
        data["description"] = self.description
        data["type"] = self.type
        data["status"] = self.status
        if self.rating is not None:
            data["rating"] = self.rating
        # Type-specific optional fields
        if self.type == "paper":
            data["journal"] = self.journal or ""
            data["doi"] = self.doi or ""
            data["year"] = self.year
        if self.type == "book":
            data["year"] = self.year
            data["isbn"] = self.isbn or ""
        data["tags"] = self.tags

        yaml_body = yaml.dump(
            data,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ).rstrip("\n")
        return f"---\n{yaml_body}\n---"


class IngestItem(BaseModel):
    """A content item to be processed (from Gmail or Inbox)."""

    source_type: Literal["gmail", "inbox"]
    title: str
    content: str
    raw_html: str | None = None
    source_url: str = ""
    author: list[str] = []
    published: date | None = None
    newsletter_name: str | None = None
    metadata: dict = {}
