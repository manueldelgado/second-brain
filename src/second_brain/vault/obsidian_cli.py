"""Obsidian CLI vault backend — uses the official Obsidian CLI for vault operations."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class ObsidianCLIBackend:
    """Vault operations via the Obsidian CLI.

    Uses `obsidian` CLI commands for operations that benefit from
    Obsidian-awareness (e.g., move updates wikilinks). Falls back to
    direct I/O for simple reads/writes.
    """

    def __init__(self, vault_root: Path, vault_name: str = "Personal") -> None:
        self.vault_root = vault_root
        self.vault_name = vault_name

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        cmd = ["obsidian", f'vault="{self.vault_name}"', *args]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )

    def create_note(self, folder: str, filename: str, content: str) -> Path:
        # Write directly — CLI create is for template-based creation
        dest_dir = self.vault_root / folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / filename
        path.write_text(content, encoding="utf-8")
        return path

    def read_note(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def move_note(self, source: Path, dest_folder: str) -> Path:
        """Move note via CLI — automatically updates wikilinks across the vault."""
        rel_source = source.relative_to(self.vault_root)
        dest = Path(dest_folder) / source.name
        try:
            self._run_cli("move", str(rel_source), str(dest))
            return self.vault_root / dest
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback to filesystem move if CLI is unavailable
            dest_dir = self.vault_root / dest_folder
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / source.name
            shutil.move(str(source), str(dest_path))
            return dest_path

    def list_folder(self, folder: str) -> list[Path]:
        folder_path = self.vault_root / folder
        if not folder_path.exists():
            return []
        return sorted(folder_path.iterdir())

    def copy_asset(self, source: Path, dest_folder: str) -> Path:
        dest_dir = self.vault_root / dest_folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / source.name
        shutil.copy2(str(source), str(dest))
        return dest
