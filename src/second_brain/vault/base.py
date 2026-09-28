"""VaultBackend protocol — abstract interface for vault operations."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class VaultBackend(Protocol):
    """Abstract interface for Obsidian vault file operations."""

    def create_note(
        self, folder: str, filename: str, content: str, when: date | None = None
    ) -> Path:
        """Create a new note in the given folder. Returns the full path.

        Never overwrites: if ``filename`` is taken, the note is saved as
        ``<stem> (<when or today>).md``, then ``<stem> (<date>) 1.md``, … —
        callers must use the returned path.
        """
        ...

    def update_note(self, path: Path, content: str) -> None:
        """Deliberately replace the content of an existing note."""
        ...

    def read_note(self, path: Path) -> str:
        """Read and return the full content of a note."""
        ...

    def move_note(self, source: Path, dest_folder: str, when: date | None = None) -> Path:
        """Move a note to dest_folder. Returns the new path (renamed if the name is taken)."""
        ...

    def list_folder(self, folder: str) -> list[Path]:
        """List all files in a vault folder."""
        ...

    def copy_asset(self, source: Path, dest_folder: str, when: date | None = None) -> Path:
        """Copy a binary asset to dest_folder. Returns the new path (renamed if the name is taken)."""
        ...
