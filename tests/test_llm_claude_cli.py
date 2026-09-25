"""Tests for the Claude Code CLI provider (subprocess mocked)."""

from __future__ import annotations

import json
import subprocess

import pytest

from second_brain.config import TaxonomyConfig
from second_brain.llm import claude_cli
from second_brain.llm.claude_cli import ClaudeCLIProvider


@pytest.fixture
def taxonomy() -> TaxonomyConfig:
    return TaxonomyConfig(
        descriptive={"ai/industry-news": "AI news"},
        functional={"func/blog": "Blog material"},
        classification_rules=["Use 1-3 tags"],
    )


def _result(**overrides) -> dict:
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 2,
        "total_cost_usd": 0.01,
        "duration_ms": 3000,
        "usage": {"input_tokens": 10, "output_tokens": 50},
        "modelUsage": {"claude-opus-5-5": {}},
        "structured_output": {
            "summary": "S",
            "key_takeaways": ["T1"],
            "descriptive_tags": ["ai/industry-news"],
            "functional_tags": ["func/blog"],
            "description": "D",
        },
    }
    result.update(overrides)
    return result


class FakeRun:
    """Stands in for subprocess.run: answers `auth status`, then analysis calls."""

    def __init__(self, auth_method="claude.ai", results=None, logged_in=True):
        self.auth_method = auth_method
        self.logged_in = logged_in
        self.results = list(results or [_result()])
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if cmd[1:] == ["auth", "status"]:
            out = json.dumps({"loggedIn": self.logged_in, "authMethod": self.auth_method})
        else:
            out = json.dumps(self.results.pop(0))
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


@pytest.fixture
def fake_run(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(claude_cli.subprocess, "run", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    return fake


def test_parses_structured_output_and_merges_tags(fake_run, taxonomy) -> None:
    analysis = ClaudeCLIProvider().analyze_content("Body", taxonomy, include_content_type=False)
    assert analysis.summary == "S"
    assert analysis.tags == ["ai/industry-news", "func/blog"]


def test_never_passes_api_key_to_the_cli(fake_run, taxonomy) -> None:
    ClaudeCLIProvider().analyze_content("Body", taxonomy)
    for _, kwargs in fake_run.calls:
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]


def test_command_isolates_the_session(fake_run, taxonomy) -> None:
    ClaudeCLIProvider(model="claude-opus-5-5", effort="low").analyze_content(
        "Body", taxonomy, content_hint="Hint"
    )
    cmd, kwargs = fake_run.calls[-1]
    assert cmd[0].endswith("claude") and cmd[1] == "-p"
    assert "--bare" not in cmd  # --bare only accepts API-key auth
    for flag, value in [("--model", "claude-opus-5-5"), ("--tools", ""), ("--setting-sources", ""),
                        ("--output-format", "json"), ("--effort", "low")]:
        assert cmd[cmd.index(flag) + 1] == value
    schema = json.loads(cmd[cmd.index("--json-schema") + 1])
    assert schema["properties"]["functional_tags"]["items"]["enum"] == ["func/blog"]
    assert "Body" in kwargs["input"] and "Hint" in kwargs["input"]


def test_refuses_non_subscription_auth(monkeypatch) -> None:
    monkeypatch.setattr(claude_cli.subprocess, "run", FakeRun(auth_method="api_key"))
    with pytest.raises(RuntimeError, match="subscription"):
        ClaudeCLIProvider()


def test_retries_once_then_raises_on_error_result(monkeypatch, taxonomy) -> None:
    error = _result(is_error=True, subtype="error_during_execution", result="overloaded")
    fake = FakeRun(results=[error, _result()])
    monkeypatch.setattr(claude_cli.subprocess, "run", fake)
    assert ClaudeCLIProvider().analyze_content("Body", taxonomy).summary == "S"

    fake = FakeRun(results=[error, error])
    monkeypatch.setattr(claude_cli.subprocess, "run", fake)
    with pytest.raises(RuntimeError, match="overloaded"):
        ClaudeCLIProvider().analyze_content("Body", taxonomy)


# --- Failures that affect every request: stop the run, no retries -------------

from second_brain.llm.base import LLMUnavailableError  # noqa: E402

# What `claude -p --output-format json` prints when logged out (captured from CLI 2.1.282)
_LOGGED_OUT = _result(
    is_error=True, subtype="success", terminal_reason="api_error", api_error_status=None,
    result="Not logged in · Please run /login", structured_output=None,
)


@pytest.mark.parametrize(
    "error, match",
    [
        (_LOGGED_OUT, "authentication"),
        (_result(is_error=True, api_error_status=401, result="OAuth token has expired"), "authentication"),
        (_result(is_error=True, result="Claude AI usage limit reached|1790000000"), "usage limit"),
    ],
)
def test_account_errors_raise_unavailable_without_retry(monkeypatch, taxonomy, error, match) -> None:
    fake = FakeRun(results=[error, _result()])
    monkeypatch.setattr(claude_cli.subprocess, "run", fake)
    with pytest.raises(LLMUnavailableError, match=match) as exc_info:
        ClaudeCLIProvider().analyze_content("Body", taxonomy)
    assert exc_info.value.hint
    assert len(fake.calls) == 2  # auth status + one attempt, no retry


def test_logged_out_is_detected_before_any_request(monkeypatch) -> None:
    monkeypatch.setattr(claude_cli.subprocess, "run", FakeRun(auth_method="none", logged_in=False))
    with pytest.raises(LLMUnavailableError, match="not logged in"):
        ClaudeCLIProvider()


def test_missing_executable_is_unavailable(monkeypatch) -> None:
    def missing(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(claude_cli.subprocess, "run", missing)
    with pytest.raises(LLMUnavailableError, match="not found"):
        ClaudeCLIProvider()


def test_finds_claude_outside_a_minimal_launchd_path(monkeypatch, tmp_path) -> None:
    fake_bin = tmp_path / "claude"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(claude_cli, "_INSTALL_DIRS", (str(tmp_path),))
    assert claude_cli._resolve_executable("claude") == str(fake_bin)
