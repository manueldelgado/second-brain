"""Direct filesystem vault backend — for testing and headless environments."""

from __future__ import annotations

from pathlib import Path

from second_brain.vault.safe_io import (
    copy_no_clobber,
    move_no_clobber,
    rewrite,
    write_new,
)


class FilesystemBackend:
    """Vault operations via direct file I/O."""

    def __init__(self, vault_root: Path) -> None:
        self.vault_root = vault_root

    def create_note(self, folder: str, filename: str, content: str) -> Path:
        return write_new(self.vault_root / folder, filename, content)

    def update_note(self, path: Path, content: str) -> None:
        rewrite(path, content)

    def read_note(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def move_note(self, source: Path, dest_folder: str) -> Path:
        return move_no_clobber(source, self.vault_root / dest_folder)

    def list_folder(self, folder: str) -> list[Path]:
        folder_path = self.vault_root / folder
        if not folder_path.exists():
            return []
        return sorted(folder_path.iterdir())

    def copy_asset(self, source: Path, dest_folder: str) -> Path:
        return copy_no_clobber(source, self.vault_root / dest_folder)
