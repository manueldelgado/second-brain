"""Tests for sanitize_filename: names must be linkable from Obsidian."""

from __future__ import annotations

import pytest

from second_brain.pipeline.base import sanitize_filename


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("#064 🪞 La ilusión del final", "064 🪞 La ilusión del final.md"),
        ("Data Engineering Weekly #280", "Data Engineering Weekly 280.md"),
        ("🏡 # 279 ¿Qué es el centralismo?", "🏡 279 ¿Qué es el centralismo.md"),
        ("#64Invest in PETs", "64 Invest in PETs.md"),
        ("How OpenAI Made Chip Design 3× Faster [Guest]", "How OpenAI Made Chip Design 3× Faster (Guest).md"),
        ("CLV | Marketing Thought", "CLV Marketing Thought.md"),
        ("Block ^ref title", "Block ref title.md"),
        ("ClaudeDevs (@ClaudeDevs)\n17 mil me gusta", "ClaudeDevs (@ClaudeDevs) 17 mil me gusta.md"),
    ],
)
def test_sanitize_filename_is_obsidian_linkable(title: str, expected: str) -> None:
    assert sanitize_filename(title) == expected


def test_sanitize_filename_has_no_link_breaking_characters() -> None:
    name = sanitize_filename("A #1 [draft] ^x | y")
    assert not any(c in name for c in "#^[]|")

