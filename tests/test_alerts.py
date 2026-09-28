"""Tests for the vault alert/status notes and their CLI wiring."""

from __future__ import annotations

import click
import frontmatter
import pytest

from second_brain.alerts import STATUS_FILENAME, alert_filename, clear_alert, raise_alert, record_run
from second_brain.config import LLMConfig, Settings, VaultConfig
from second_brain.errors import GmailAuthError
from second_brain.llm.base import LLMUnavailableError
from second_brain.main import _monitored_run
from second_brain.pipeline.base import PipelineReport


def _notes(tmp_path):
    return tmp_path / "01 Notes"


def _settings(tmp_path) -> Settings:
    return Settings(vault=VaultConfig(root=tmp_path), llm=LLMConfig(provider="claude_cli"))


def test_raise_keeps_first_seen_and_counts_runs(tmp_path) -> None:
    raise_alert(tmp_path, "Claude", "Not logged in", "Run claude auth login", "newsletters")
    first = frontmatter.load(tmp_path / alert_filename("Claude")).metadata
    raise_alert(tmp_path, "Claude", "Not logged in", "Run claude auth login", "inbox")
    note = frontmatter.load(tmp_path / alert_filename("Claude"))

    assert note["first_seen"] == first["first_seen"]
    assert note["failed_runs"] == 2
    assert "Run claude auth login" in note.content and "second-brain inbox" in note.content


def test_clear_only_removes_its_component(tmp_path) -> None:
    raise_alert(tmp_path, "Claude", "p", "h", "run")
    raise_alert(tmp_path, "Gmail", "p", "h", "run")
    clear_alert(tmp_path, "Claude")
    clear_alert(tmp_path, "Claude")  # tolerates absence
    assert not (tmp_path / alert_filename("Claude")).exists()
    assert (tmp_path / alert_filename("Gmail")).exists()


def test_note_names_carry_an_emoji() -> None:
    assert STATUS_FILENAME == "💚 Second Brain - Status.md"
    assert alert_filename("Gmail") == "🚨 Second Brain - Action needed (Gmail).md"


def test_notes_under_pre_emoji_names_are_carried_over_and_removed(tmp_path) -> None:
    legacy_alert = tmp_path / "Second Brain - Action needed (Claude).md"
    legacy_alert.write_text("---\nfirst_seen: '2026-09-01T08:00:00+02:00'\nfailed_runs: 5\n---\n")
    legacy_status = tmp_path / "Second Brain - Status.md"
    legacy_status.write_text("---\nruns:\n  inbox:\n    at: '2026-09-01T08:00:00+02:00'\n"
                             "    summary: 'old'\n    errors: 0\n---\n")

    raise_alert(tmp_path, "Claude", "p", "h", "run")
    record_run(tmp_path, "newsletters", "1 processed, 1 created", 0)

    alert = frontmatter.load(tmp_path / alert_filename("Claude"))
    assert alert["failed_runs"] == 6 and "2026-09-01" in str(alert["first_seen"])
    assert set(frontmatter.load(tmp_path / STATUS_FILENAME)["runs"]) == {"inbox", "newsletters"}
    assert not legacy_alert.exists() and not legacy_status.exists()

    legacy_alert.write_text("stale")
    clear_alert(tmp_path, "Claude")
    assert not legacy_alert.exists()


def test_status_note_keeps_each_pipeline_last_run(tmp_path) -> None:
    record_run(tmp_path, "newsletters", "3 processed, 3 created", 0)
    record_run(tmp_path, "inbox", "1 processed, 1 created", 1)
    note = frontmatter.load(tmp_path / STATUS_FILENAME)

    assert set(note["runs"]) == {"newsletters", "inbox"}
    assert note["runs"]["inbox"]["errors"] == 1
    assert "Last successful run" in note.content and "1 item error(s)" in note.content


@pytest.mark.parametrize("error", [
    LLMUnavailableError("Not logged in", hint="Run `claude auth login`"),
    GmailAuthError("Token revoked", hint="Run `second-brain gmail login`"),
])
def test_blocking_error_raises_alert_in_notes_folder_and_fails(tmp_path, error) -> None:
    with pytest.raises(click.ClickException, match=error.hint):
        with _monitored_run(_settings(tmp_path), "newsletters", False, ("Gmail", "Claude")):
            raise error
    assert (_notes(tmp_path) / alert_filename(error.component)).exists()
    assert not (tmp_path / alert_filename(error.component)).exists()  # not in the vault root
    assert not (_notes(tmp_path) / STATUS_FILENAME).exists()  # a blocked run isn't a success


def test_completed_run_records_status_and_clears_exercised_components(tmp_path) -> None:
    for component in ("Claude", "Gmail"):
        raise_alert(_notes(tmp_path), component, "p", "h", "run")
    with _monitored_run(_settings(tmp_path), "inbox", False, ("Claude",)) as run:
        run["report"] = PipelineReport(pipeline_name="inbox", items_processed=2, items_created=2)

    assert not (_notes(tmp_path) / alert_filename("Claude")).exists()
    assert (_notes(tmp_path) / alert_filename("Gmail")).exists()  # inbox never touched Gmail
    status = frontmatter.load(_notes(tmp_path) / STATUS_FILENAME)
    assert status["runs"]["inbox"]["summary"] == "2 processed, 2 created"


def test_dry_run_writes_nothing(tmp_path) -> None:
    with pytest.raises(click.ClickException):
        with _monitored_run(_settings(tmp_path), "run", True, ("Claude",)):
            raise LLMUnavailableError("Not logged in")
    with _monitored_run(_settings(tmp_path), "run", True, ("Claude",)) as run:
        run["report"] = PipelineReport(pipeline_name="inbox")
    assert not _notes(tmp_path).exists()


def test_run_continues_with_inbox_after_a_gmail_block(monkeypatch) -> None:
    from click.testing import CliRunner

    from second_brain import main

    calls = []

    @click.command()
    def blocked_newsletters(**kwargs):
        calls.append("newsletters")
        raise click.ClickException("Gmail revoked") from GmailAuthError("Gmail revoked")

    @click.command()
    def working_inbox(**kwargs):
        calls.append("inbox")

    monkeypatch.setattr(main, "newsletters", blocked_newsletters)
    monkeypatch.setattr(main, "inbox", working_inbox)
    result = CliRunner().invoke(main.cli, ["--config-dir", ".", "run"])
    assert calls == ["newsletters", "inbox"]
    assert result.exit_code == 1


def test_run_stops_after_a_claude_block(monkeypatch) -> None:
    from click.testing import CliRunner

    from second_brain import main

    calls = []

    @click.command()
    def blocked_newsletters(**kwargs):
        calls.append("newsletters")
        raise click.ClickException("logged out") from LLMUnavailableError("logged out")

    monkeypatch.setattr(main, "newsletters", blocked_newsletters)
    result = CliRunner().invoke(main.cli, ["--config-dir", ".", "run"])
    assert calls == ["newsletters"]
    assert result.exit_code == 1
