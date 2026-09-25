"""Tests for the vault alert note and its CLI wiring."""

from __future__ import annotations

import click
import frontmatter
import pytest

from second_brain.alerts import ALERT_FILENAME, clear_alert, raise_alert
from second_brain.config import LLMConfig, Settings, VaultConfig
from second_brain.llm.base import LLMUnavailableError
from second_brain.main import _alert_on_unavailable_llm


def test_raise_keeps_first_seen_and_counts_runs(tmp_path) -> None:
    raise_alert(tmp_path, "Not logged in", "Run claude auth login", "newsletters")
    first = frontmatter.load(tmp_path / ALERT_FILENAME).metadata
    raise_alert(tmp_path, "Not logged in", "Run claude auth login", "inbox")
    note = frontmatter.load(tmp_path / ALERT_FILENAME)

    assert note["first_seen"] == first["first_seen"]
    assert note["failed_runs"] == 2
    assert "Run claude auth login" in note.content and "second-brain inbox" in note.content


def test_clear_removes_the_note_and_tolerates_absence(tmp_path) -> None:
    raise_alert(tmp_path, "p", "h", "run")
    clear_alert(tmp_path)
    clear_alert(tmp_path)
    assert not (tmp_path / ALERT_FILENAME).exists()


def _alert(tmp_path):
    return tmp_path / "01 Notes" / ALERT_FILENAME


def _settings(tmp_path) -> Settings:
    return Settings(vault=VaultConfig(root=tmp_path), llm=LLMConfig(provider="claude_cli"))


def test_cli_wrapper_raises_alert_and_fails_the_command(tmp_path) -> None:
    with pytest.raises(click.ClickException, match="claude auth login"):
        with _alert_on_unavailable_llm(_settings(tmp_path), "newsletters", dry_run=False):
            raise LLMUnavailableError("Not logged in", hint="Run `claude auth login`")
    assert _alert(tmp_path).exists()
    assert not (tmp_path / ALERT_FILENAME).exists()  # not in the vault root


def test_cli_wrapper_clears_alert_after_a_working_run(tmp_path) -> None:
    raise_alert(tmp_path / "01 Notes", "p", "h", "run")
    with _alert_on_unavailable_llm(_settings(tmp_path), "inbox", dry_run=False):
        pass
    assert not (tmp_path / ALERT_FILENAME).exists()


def test_dry_run_writes_no_alert(tmp_path) -> None:
    with pytest.raises(click.ClickException):
        with _alert_on_unavailable_llm(_settings(tmp_path), "run", dry_run=True):
            raise LLMUnavailableError("Not logged in")
    assert not _alert(tmp_path).exists()
