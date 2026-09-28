"""Obsidian CLI vault backend — uses the official Obsidian CLI for vault operations."""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from second_brain.vault.safe_io import (
    copy_no_clobber,
    free_path,
    move_no_clobber,
    rewrite,
    write_new,
)


class ObsidianCLIBackend:
    """Vault operations via the Obsidian CLI.

    Uses `obsidian` CLI commands for operations that benefit from
    Obsidian-awareness (e.g., move updates wikilinks). Falls back to
    direct I/O for simple reads/writes.
    """

    def __init__(self, vault_root: Path, vault_name: str | None = None) -> None:
        self.vault_root = vault_root
        self.vault_name = vault_name or vault_root.name

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        cmd = ["obsidian", f'vault="{self.vault_name}"', *args]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )

    def create_note(
        self, folder: str, filename: str, content: str, when: date | None = None
    ) -> Path:
        # Write directly — CLI create is for template-based creation
        return write_new(self.vault_root / folder, filename, content, when)

    def update_note(self, path: Path, content: str) -> None:
        rewrite(path, content)

    def read_note(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def move_note(self, source: Path, dest_folder: str, when: date | None = None) -> Path:
        """Move note via CLI — automatically updates wikilinks across the vault."""
        rel_source = source.relative_to(self.vault_root)
        # The CLI creates the file itself, so pick a free name up front
        dest = free_path(self.vault_root / dest_folder, source.name, when).relative_to(
            self.vault_root
        )
        try:
            self._run_cli("move", str(rel_source), str(dest))
            return self.vault_root / dest
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback to filesystem move if CLI is unavailable
            return move_no_clobber(source, self.vault_root / dest_folder, when)

    def list_folder(self, folder: str) -> list[Path]:
        folder_path = self.vault_root / folder
        if not folder_path.exists():
            return []
        return sorted(folder_path.iterdir())

    def copy_asset(self, source: Path, dest_folder: str, when: date | None = None) -> Path:
        return copy_no_clobber(source, self.vault_root / dest_folder, when)
