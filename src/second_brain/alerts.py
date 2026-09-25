"""Alert note in the notes folder for problems that stop scheduled runs.

Scheduled runs happen unattended (launchd), so a failure that blocks every run —
e.g. ``claude -p`` logged out — would otherwise only show up in a log file. The
alert note makes it visible in Obsidian: it is created (or updated, keeping the
first-seen time and a failed-run count) when a run stops, and deleted by the
next run that works. It lives in the notes folder (``01 Notes``), next to what
the pipeline produces, and never in the inbox, which the inbox pipeline would
pick up. This is the one vault file the program overwrites.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import frontmatter

from second_brain.vault.safe_io import rewrite

logger = logging.getLogger(__name__)

ALERT_FILENAME = "Second Brain - Action needed.md"
_LOG_DIR = "~/.local/log/second-brain/"


def raise_alert(folder: Path, problem: str, hint: str, command: str) -> Path:
    """Create or refresh the alert note in *folder*."""
    path = Path(folder) / ALERT_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone().replace(microsecond=0)
    first_seen, failed_runs = now, 0
    if path.exists():
        try:
            previous = frontmatter.load(path).metadata
            first_seen = datetime.fromisoformat(str(previous["first_seen"]))
            failed_runs = int(previous.get("failed_runs", 0))
        except Exception:
            pass  # unreadable previous alert — start over
    failed_runs += 1

    content = (
        "---\n"
        "type: alert\n"
        "status: open\n"
        f"first_seen: '{first_seen.isoformat()}'\n"
        f"last_seen: '{now.isoformat()}'\n"
        f"failed_runs: {failed_runs}\n"
        "---\n\n"
        "> [!danger] Second Brain automation is stopped\n"
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
    logger.error("Alert raised in vault: %s — %s", ALERT_FILENAME, problem)
    return path


def clear_alert(folder: Path) -> None:
    """Delete the alert note in *folder*, if any — called after a run that works."""
    path = Path(folder) / ALERT_FILENAME
    if path.exists():
        path.unlink()
        logger.info("Problem resolved — removed alert note %s", ALERT_FILENAME)
