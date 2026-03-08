"""VaultBackend protocol — abstract interface for vault operations."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class VaultBackend(Protocol):
    """Abstract interface for Obsidian vault file operations."""

    def create_note(self, folder: str, filename: str, content: str) -> Path:
        """Create a new note in the given folder. Returns the full path."""
        ...

    def read_note(self, path: Path) -> str:
        """Read and return the full content of a note."""
        ...

    def move_note(self, source: Path, dest_folder: str) -> Path:
        """Move a note to dest_folder. Returns the new path."""
        ...

    def list_folder(self, folder: str) -> list[Path]:
        """List all files in a vault folder."""
        ...

    def copy_asset(self, source: Path, dest_folder: str) -> Path:
        """Copy a binary asset to dest_folder. Returns the new path."""
        ...
