"""Notes in the notes folder that make unattended runs visible in Obsidian.

Scheduled runs happen unattended (launchd), so problems would otherwise only show
up in a log file. Two kinds of note, both in the notes folder (``01 Notes``) —
never the inbox, which the inbox pipeline would pick up:

- **Alert** (``🚨 Second Brain - Action needed (<component>).md``): written when a
  run is stopped by a :class:`~second_brain.errors.BlockingError` (Claude CLI
  logged out, Gmail authorization revoked, a config file that won't load…), or
  by an unexpected exception (component = the pipeline, e.g. "Inbox"), keeping the first-seen time and a failed-run count; deleted by the next run
  in which that component works. One
  note per component, so a working inbox run never hides a Gmail problem.
- **Status** (``💚 Second Brain - Status.md``): rewritten after every run that
  completes, with its time and counts — a heartbeat. If it goes stale, runs
  have stopped happening at all (Mac asleep, agent unloaded), which no alert
  can report.

Unlike generated notes, their names carry an emoji so they stand out in
Obsidian's file tree. These are the only vault files the program overwrites.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import frontmatter

from second_brain.vault.safe_io import rewrite

logger = logging.getLogger(__name__)

STATUS_FILENAME = "💚 Second Brain - Status.md"
_LOG_DIR = "~/.local/log/second-brain/"
# Names used before the emoji prefix; removed on the next write so they don't linger.
_LEGACY_STATUS_FILENAME = "Second Brain - Status.md"


def alert_filename(component: str) -> str:
    return f"🚨 Second Brain - Action needed ({component}).md"


def _legacy_alert_filename(component: str) -> str:
    return f"Second Brain - Action needed ({component}).md"


def raise_alert(folder: Path, component: str, problem: str, hint: str, command: str) -> Path:
    """Create or refresh the alert note for *component* in *folder*."""
    path = Path(folder) / alert_filename(component)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = Path(folder) / _legacy_alert_filename(component)
    now = _now()
    first_seen, failed_runs = now, 0
    previous_path = path if path.exists() else legacy
    if previous_path.exists():
        try:
            previous = frontmatter.load(previous_path).metadata
            first_seen = datetime.fromisoformat(str(previous["first_seen"]))
            failed_runs = int(previous.get("failed_runs", 0))
        except Exception:
            pass  # unreadable previous alert — start over
    failed_runs += 1

    content = (
        "---\n"
        "type: alert\n"
        "status: open\n"
        f"component: {component}\n"
        f"first_seen: '{first_seen.isoformat()}'\n"
        f"last_seen: '{now.isoformat()}'\n"
        f"failed_runs: {failed_runs}\n"
        "---\n\n"
        f"> [!danger] Second Brain automation is blocked by {component}\n"
        f"> Scheduled runs have been failing since **{first_seen:%Y-%m-%d %H:%M}** "
        f"({failed_runs} failed run{'s' if failed_runs != 1 else ''}, "
        f"last at {now:%Y-%m-%d %H:%M}).\n\n"
        "## Problem\n\n"
        f"{problem}\n\n"
        "## How to fix\n\n"
        f"{hint}\n\n"
        "Nothing is lost while this lasts: unprocessed newsletters and inbox items are "
        "picked up by the next run that works, and this note is then deleted automatically.\n\n"
        "## Details\n\n"
        f"- Command: `second-brain {command}`\n"
        f"- Logs: `{_LOG_DIR}`\n"
    )
    rewrite(path, content)
    legacy.unlink(missing_ok=True)
    logger.error("Alert raised in vault: %s — %s", path.name, problem)
    return path


def clear_alert(folder: Path, component: str) -> None:
    """Delete *component*'s alert note, if any — called after a run where it worked."""
    for path in (Path(folder) / alert_filename(component), Path(folder) / _legacy_alert_filename(component)):
        if path.exists():
            path.unlink()
            logger.info("Problem resolved — removed alert note %s", path.name)


def record_run(folder: Path, pipeline: str, summary: str, errors: int) -> Path:
    """Update the status note with a completed run of *pipeline* (e.g. "newsletters")."""
    path = Path(folder) / STATUS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = Path(folder) / _LEGACY_STATUS_FILENAME
    runs: dict[str, dict] = {}
    previous_path = path if path.exists() else legacy
    if previous_path.exists():
        try:
            runs = dict(frontmatter.load(previous_path).metadata.get("runs") or {})
        except Exception:
            pass
    now = _now()
    runs[pipeline] = {"at": now.isoformat(), "summary": summary, "errors": errors}

    lines = []
    for name in sorted(runs):
        run = runs[name]
        at = datetime.fromisoformat(str(run["at"]))
        flag = f" · ⚠️ {run['errors']} item error(s), see log" if run.get("errors") else ""
        lines.append(f"| {name} | {at:%a %d %b %H:%M} | {run['summary']}{flag} |")

    runs_yaml = "".join(
        f"  {name}:\n    at: '{run['at']}'\n    summary: '{run['summary']}'\n"
        f"    errors: {run.get('errors', 0)}\n"
        for name, run in sorted(runs.items())
    )
    content = (
        "---\n"
        "type: status\n"
        f"last_success: '{now.isoformat()}'\n"
        f"runs:\n{runs_yaml}"
        "---\n\n"
        f"> [!info] Last successful run: **{now:%a %d %b %Y, %H:%M}**\n"
        "> Runs are scheduled every ~30 min from 07:03 to 23:03. If this time is more "
        "than about an hour old in that window, the automation has **stopped running** "
        "(Mac asleep or off, agent unloaded) — see *If it's stale* below.\n\n"
        "| Pipeline | Last completed | Result |\n"
        "|---|---|---|\n"
        + "\n".join(lines)
        + "\n\n"
        "Problems that stop a run (Claude or Gmail logged out, a broken config file) "
        "get their own *🚨 Second Brain - Action needed* note instead.\n\n"
        "## If it's stale\n\n"
        "- `launchctl list | grep second-brain` — the agent should be listed; "
        "the second column is the last exit code.\n"
        f"- Logs: `{_LOG_DIR}run.log`\n"
        "- Run now: `launchctl kickstart gui/$(id -u)/com.second-brain.run`\n"
    )
    rewrite(path, content)
    legacy.unlink(missing_ok=True)
    return path


def _now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)
