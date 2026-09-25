"""No-clobber file operations: vault writes never replace an existing file.

When the requested name is taken, the file gets Obsidian's own duplicate
naming — ``Title 1.md``, ``Title 2.md``, … — instead of overwriting.
Names are claimed with exclusive creation (``O_EXCL``), so the check and
the write cannot race with another writer such as Obsidian itself.
"""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

_MAX_ATTEMPTS = 1000


def _candidates(dest_dir: Path, filename: str) -> Iterator[Path]:
    stem, suffix = os.path.splitext(filename)
    yield dest_dir / filename
    for n in range(1, _MAX_ATTEMPTS):
        yield dest_dir / f"{stem} {n}{suffix}"


def _claim(dest_dir: Path, filename: str) -> Path:
    """Atomically create the first free name as an empty placeholder we own."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for candidate in _candidates(dest_dir, filename):
        try:
            candidate.open("x").close()
            return candidate
        except FileExistsError:
            continue
    raise FileExistsError(
        f"No free name for {filename!r} in {dest_dir} after {_MAX_ATTEMPTS} tries"
    )


def free_path(dest_dir: Path, filename: str) -> Path:
    """First name not currently taken (no claim — for tools that create the file themselves)."""
    for candidate in _candidates(dest_dir, filename):
        if not candidate.exists():
            return candidate
    raise FileExistsError(
        f"No free name for {filename!r} in {dest_dir} after {_MAX_ATTEMPTS} tries"
    )


def write_new(dest_dir: Path, filename: str, content: str) -> Path:
    """Write a new text file without replacing any existing one."""
    path = _claim(dest_dir, filename)
    try:
        path.write_text(content, encoding="utf-8")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def move_no_clobber(source: Path, dest_dir: Path) -> Path:
    """Move ``source`` into ``dest_dir`` without replacing any existing file."""
    path = _claim(dest_dir, source.name)
    try:
        # Replacing our own empty placeholder is safe; it is atomic on one filesystem.
        os.replace(source, path)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            path.unlink(missing_ok=True)
            raise
        shutil.copy2(source, path)
        source.unlink()
    return path


def copy_no_clobber(source: Path, dest_dir: Path) -> Path:
    """Copy ``source`` into ``dest_dir`` without replacing any existing file."""
    path = _claim(dest_dir, source.name)
    try:
        shutil.copy2(source, path)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return path


def rewrite(path: Path, content: str) -> None:
    """Deliberately replace an existing note's content, atomically (temp file + rename)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
