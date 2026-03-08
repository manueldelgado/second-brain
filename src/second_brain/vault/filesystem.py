"""Direct filesystem vault backend — for testing and headless environments."""

from __future__ import annotations

import shutil
from pathlib import Path


class FilesystemBackend:
    """Vault operations via direct file I/O."""

    def __init__(self, vault_root: Path) -> None:
        self.vault_root = vault_root

    def create_note(self, folder: str, filename: str, content: str) -> Path:
        dest_dir = self.vault_root / folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / filename
        path.write_text(content, encoding="utf-8")
        return path

    def read_note(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def move_note(self, source: Path, dest_folder: str) -> Path:
        dest_dir = self.vault_root / dest_folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / source.name
        shutil.move(str(source), str(dest))
        return dest

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
