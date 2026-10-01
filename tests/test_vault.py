"""Tests for vault backends."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from second_brain.vault.base import VaultBackend
from second_brain.vault.filesystem import FilesystemBackend


class TestFilesystemBackend:
    """Tests for the direct filesystem vault backend."""

    def test_satisfies_protocol(self, tmp_path: Path) -> None:
        backend = FilesystemBackend(tmp_path)
        assert isinstance(backend, VaultBackend)

    def test_create_note(self, tmp_path: Path) -> None:
        backend = FilesystemBackend(tmp_path)
        path = backend.create_note("01 Notes", "test.md", "# Hello")
        assert path.exists()
        assert path.read_text() == "# Hello"
        assert path.parent.name == "01 Notes"

    def test_create_note_creates_folder(self, tmp_path: Path) -> None:
        backend = FilesystemBackend(tmp_path)
        path = backend.create_note("new_folder", "note.md", "content")
        assert (tmp_path / "new_folder").is_dir()
        assert path.exists()

    def test_read_note(self, tmp_path: Path) -> None:
        backend = FilesystemBackend(tmp_path)
        note_path = tmp_path / "01 Notes" / "test.md"
        note_path.parent.mkdir(parents=True)
        note_path.write_text("# Test Content", encoding="utf-8")
        content = backend.read_note(note_path)
        assert content == "# Test Content"

    def test_move_note(self, tmp_path: Path) -> None:
        backend = FilesystemBackend(tmp_path)
        # Create a note in inbox
        source = backend.create_note("00 Inbox", "item.md", "inbox content")
        assert source.exists()

        # Move to notes
        dest = backend.move_note(source, "01 Notes")
        assert dest.exists()
        assert not source.exists()
        assert dest.parent.name == "01 Notes"
        assert dest.read_text() == "inbox content"

    def test_move_note_creates_dest_folder(self, tmp_path: Path) -> None:
        backend = FilesystemBackend(tmp_path)
        source = backend.create_note("00 Inbox", "item.md", "content")
        dest = backend.move_note(source, "new_dest")
        assert (tmp_path / "new_dest").is_dir()
        assert dest.exists()

    def test_list_folder(self, tmp_path: Path) -> None:
        backend = FilesystemBackend(tmp_path)
        backend.create_note("00 Inbox", "a.md", "aaa")
        backend.create_note("00 Inbox", "b.md", "bbb")
        files = backend.list_folder("00 Inbox")
        names = [f.name for f in files]
        assert "a.md" in names
        assert "b.md" in names

    def test_list_folder_empty(self, tmp_path: Path) -> None:
        backend = FilesystemBackend(tmp_path)
        files = backend.list_folder("nonexistent")
        assert files == []

    def test_list_folder_sorted(self, tmp_path: Path) -> None:
        backend = FilesystemBackend(tmp_path)
        backend.create_note("folder", "c.md", "")
        backend.create_note("folder", "a.md", "")
        backend.create_note("folder", "b.md", "")
        files = backend.list_folder("folder")
        names = [f.name for f in files]
        assert names == ["a.md", "b.md", "c.md"]

    def test_copy_asset(self, tmp_path: Path) -> None:
        backend = FilesystemBackend(tmp_path)
        # Create a source file
        source = tmp_path / "paper.pdf"
        source.write_bytes(b"fake pdf content")

        dest = backend.copy_asset(source, "04 Assets")
        assert dest.exists()
        assert dest.read_bytes() == b"fake pdf content"
        # Source should still exist
        assert source.exists()

    def test_copy_asset_creates_folder(self, tmp_path: Path) -> None:
        backend = FilesystemBackend(tmp_path)
        source = tmp_path / "image.png"
        source.write_bytes(b"png data")
        dest = backend.copy_asset(source, "04 Assets")
        assert (tmp_path / "04 Assets").is_dir()
        assert dest.exists()


class TestObsidianCLIBackend:
    """Tests for the Obsidian CLI vault backend."""

    def test_create_note_uses_filesystem(self, tmp_path: Path) -> None:
        from second_brain.vault.obsidian_cli import ObsidianCLIBackend

        backend = ObsidianCLIBackend(tmp_path)
        path = backend.create_note("01 Notes", "test.md", "# Content")
        assert path.exists()
        assert path.read_text() == "# Content"

    def test_move_note_fallback_on_cli_error(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from second_brain.vault.obsidian_cli import ObsidianCLIBackend

        backend = ObsidianCLIBackend(tmp_path)
        source = backend.create_note("00 Inbox", "item.md", "content")

        # Mock CLI to fail, should fall back to filesystem move
        with patch("subprocess.run", side_effect=FileNotFoundError("obsidian not found")):
            dest = backend.move_note(source, "01 Notes")

        assert dest.exists()
        assert not source.exists()
        assert dest.parent.name == "01 Notes"

    def test_read_note(self, tmp_path: Path) -> None:
        from second_brain.vault.obsidian_cli import ObsidianCLIBackend

        backend = ObsidianCLIBackend(tmp_path)
        path = backend.create_note("folder", "note.md", "hello")
        content = backend.read_note(path)
        assert content == "hello"

    def test_list_folder(self, tmp_path: Path) -> None:
        from second_brain.vault.obsidian_cli import ObsidianCLIBackend

        backend = ObsidianCLIBackend(tmp_path)
        backend.create_note("folder", "a.md", "")
        backend.create_note("folder", "b.md", "")
        files = backend.list_folder("folder")
        assert len(files) == 2


@pytest.fixture(params=["filesystem", "obsidian_cli"])
def backend(request, tmp_path: Path):
    if request.param == "filesystem":
        return FilesystemBackend(tmp_path)
    from second_brain.vault.obsidian_cli import ObsidianCLIBackend

    return ObsidianCLIBackend(tmp_path)


class TestNoOverwrite:
    """Vault writes must never replace an existing file."""

    def test_create_note_keeps_existing(self, backend, tmp_path: Path) -> None:
        published = date(2026, 3, 14)
        first = backend.create_note("01 Notes", "Weekly.md", "first", published)
        second = backend.create_note("01 Notes", "Weekly.md", "second", published)
        third = backend.create_note("01 Notes", "Weekly.md", "third", published)
        fourth = backend.create_note("01 Notes", "Weekly.md", "fourth", published)

        assert first.read_text() == "first"
        assert second.name == "Weekly (2026-03-14).md"
        assert second.read_text() == "second"
        assert third.name == "Weekly (2026-03-14) 1.md"  # same date too → numbered
        assert fourth.name == "Weekly (2026-03-14) 2.md"

    def test_create_note_without_date_uses_today(self, backend) -> None:
        backend.create_note("01 Notes", "Weekly.md", "first")
        second = backend.create_note("01 Notes", "Weekly.md", "second")
        assert second.name == f"Weekly ({date.today().isoformat()}).md"

    def test_move_note_keeps_existing(self, backend, tmp_path: Path) -> None:
        from unittest.mock import patch

        existing = backend.create_note("01 Notes", "item.md", "keep me")
        source = backend.create_note("00 Inbox", "item.md", "incoming")

        # CLI unavailable → exercises the filesystem path for both backends
        with patch("subprocess.run", side_effect=FileNotFoundError("obsidian not found")):
            dest = backend.move_note(source, "01 Notes")

        assert existing.read_text() == "keep me"
        assert dest.name == f"item ({date.today().isoformat()}).md"
        assert dest.read_text() == "incoming"
        assert not source.exists()

    def test_copy_asset_keeps_existing(self, backend, tmp_path: Path) -> None:
        assets = tmp_path / "04 Assets"
        assets.mkdir()
        (assets / "paper.pdf").write_bytes(b"old pdf")
        source = tmp_path / "paper.pdf"
        source.write_bytes(b"new pdf")

        dest = backend.copy_asset(source, "04 Assets")

        assert (assets / "paper.pdf").read_bytes() == b"old pdf"
        assert dest.name == f"paper ({date.today().isoformat()}).pdf"
        assert dest.read_bytes() == b"new pdf"

    def test_update_note_replaces_content_in_place(self, backend) -> None:
        path = backend.create_note("00 Inbox", "item.md", "before")
        backend.update_note(path, "after")
        assert path.read_text() == "after"
        assert [p.name for p in path.parent.iterdir()] == ["item.md"]  # no temp left behind


def test_obsidian_cli_move_targets_free_name(tmp_path: Path) -> None:
    from unittest.mock import patch

    from second_brain.vault.obsidian_cli import ObsidianCLIBackend

    backend = ObsidianCLIBackend(tmp_path)
    backend.create_note("01 Notes", "item.md", "keep me")
    source = backend.create_note("00 Inbox", "item.md", "incoming")

    with patch("subprocess.run") as run:
        dest = backend.move_note(source, "01 Notes", date(2026, 3, 14))

    assert run.call_args.args[0][-1] == str(Path("01 Notes") / "item (2026-03-14).md")
    assert dest == tmp_path / "01 Notes" / "item (2026-03-14).md"


def test_obsidian_cli_vault_name_defaults_to_folder_name(tmp_path: Path) -> None:
    from unittest.mock import patch

    from second_brain.config import VaultConfig
    from second_brain.vault.obsidian_cli import ObsidianCLIBackend

    root = tmp_path / "My Vault"
    assert VaultConfig(root=root).vault_name == "My Vault"
    assert VaultConfig(root=root, name="Work").vault_name == "Work"

    backend = ObsidianCLIBackend(root)
    with patch("subprocess.run") as run:
        backend._run_cli("move", "a.md", "b.md")
    assert run.call_args.args[0][1] == 'vault="My Vault"'


class TestICloudMaterialization:
    def test_turns_on_dataless_downloads_for_the_process(self, monkeypatch) -> None:
        from unittest.mock import MagicMock

        from second_brain.vault import icloud

        libc = MagicMock()
        libc.setiopolicy_np.return_value = 0
        monkeypatch.setattr(icloud.sys, "platform", "darwin")
        monkeypatch.setattr(icloud.ctypes, "CDLL", lambda *a, **k: libc)
        icloud.enable_dataless_materialization()
        # IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES, IOPOL_SCOPE_PROCESS, ..._ON
        libc.setiopolicy_np.assert_called_once_with(3, 0, 2)

    def test_failure_is_only_logged(self, monkeypatch, caplog) -> None:
        from unittest.mock import MagicMock

        from second_brain.vault import icloud

        libc = MagicMock()
        libc.setiopolicy_np.return_value = -1
        monkeypatch.setattr(icloud.sys, "platform", "darwin")
        monkeypatch.setattr(icloud.ctypes, "CDLL", lambda *a, **k: libc)
        icloud.enable_dataless_materialization()
        assert "Could not enable iCloud" in caplog.text

    def test_noop_outside_macos(self, monkeypatch) -> None:
        from second_brain.vault import icloud

        monkeypatch.setattr(icloud.sys, "platform", "linux")
        monkeypatch.setattr(icloud.ctypes, "CDLL", lambda *a, **k: pytest.fail("called"))
        icloud.enable_dataless_materialization()
