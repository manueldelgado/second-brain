"""LLMProvider that runs analysis through the local Claude Code CLI (``claude -p``).

Requests are billed to the Claude subscription the CLI is logged in with, not to
the API key — which is the point of this provider. To guarantee that:

- ``ANTHROPIC_API_KEY`` (loaded from ``.env`` for the API providers) is removed
  from the subprocess environment; if present, Claude Code would bill the API.
- ``--bare`` is not used: it only accepts API-key auth.
- The login is checked once (``claude auth status``) and must be ``claude.ai``.

Each call replaces Claude Code's system prompt with ours, disables all tools,
settings and MCP servers, and runs in an empty directory so no CLAUDE.md is
loaded. ``--json-schema`` validates the answer against the same schema the API
providers use, so tags stay restricted to the taxonomy.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile

from pydantic import ValidationError

from second_brain.config import TaxonomyConfig
from second_brain.llm.base import LLMUnavailableError
from second_brain.llm.prompts import (
    build_analysis_prompt,
    build_output_schema,
    build_system_prompt,
)
from second_brain.models import ContentAnalysis

logger = logging.getLogger(__name__)

_LOGIN_HINT = (
    "Log in again with your Claude subscription: run `claude auth login` in a terminal "
    "(or `/login` inside `claude`), then check with `claude auth status`."
)
_LIMIT_HINT = (
    "The Claude plan's usage limit was reached. Nothing to do: items stay pending and "
    "are processed by the next scheduled run after the limit resets."
)
# Error texts from `claude -p` that no retry or other item will fix. Logged out is
# "Not logged in · Please run /login"; expired/revoked tokens surface as 401/403.
_AUTH_ERROR = re.compile(
    r"log ?in|/login|oauth|token.{0,20}(expired|revoked|invalid)|authenticat|unauthori[sz]ed|credential",
    re.I,
)
_LIMIT_ERROR = re.compile(r"usage limit|hit your limit|limit reached|rate.?limit", re.I)

# Where Claude Code installs itself; launchd jobs get a PATH without these.
_INSTALL_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin", "~/.claude/local")

# Credentials that would make Claude Code bill the API instead of the subscription.
_API_BILLING_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


class ClaudeCLIProvider:
    """LLM provider backed by ``claude -p`` on the local machine."""

    def __init__(
        self,
        model: str = "claude-opus-5-5",
        effort: str | None = None,
        executable: str = "claude",
        timeout_seconds: int = 300,
        max_attempts: int = 2,
    ) -> None:
        self.model = model
        self.effort = effort
        self.executable = _resolve_executable(executable)
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self._env = {k: v for k, v in os.environ.items() if k not in _API_BILLING_ENV}
        self._workdir = tempfile.mkdtemp(prefix="second-brain-cli-")
        self._check_subscription_auth()

    def analyze_content(
        self,
        content: str,
        taxonomy: TaxonomyConfig,
        content_hint: str | None = None,
        include_content_type: bool = True,
    ) -> ContentAnalysis:
        """Analyze content via ``claude -p`` with JSON-schema structured output."""
        cmd = [
            self.executable,
            "-p",
            "--model", self.model,
            "--output-format", "json",
            "--system-prompt", build_system_prompt(taxonomy, include_content_type),
            "--json-schema", json.dumps(build_output_schema(taxonomy, include_content_type)),
            "--tools", "",
            "--setting-sources", "",
            "--strict-mcp-config",
            "--no-session-persistence",
        ]
        if self.effort:
            cmd += ["--effort", self.effort]
        prompt = build_analysis_prompt(content, content_hint)

        for attempt in range(1, self.max_attempts + 1):
            try:
                result = self._run(cmd, prompt)
                log_cli_usage(result, content_hint or "cli")
                return parse_cli_result(result)
            except LLMUnavailableError:
                raise
            except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                if attempt == self.max_attempts:
                    raise
                logger.warning("claude -p failed (attempt %d): %s — retrying", attempt, exc)
        raise RuntimeError("Unreachable")  # pragma: no cover

    def _run(self, cmd: list[str], prompt: str) -> dict:
        proc = self._subprocess(cmd, input=prompt, timeout=self.timeout_seconds)
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(
                f"claude -p exited {proc.returncode} without JSON output: "
                f"{(proc.stderr or proc.stdout).strip()[:500]}"
            ) from None
        if result.get("is_error") or result.get("subtype") != "success":
            _raise_if_unavailable(result)
            raise RuntimeError(
                f"claude -p error ({result.get('subtype')}, "
                f"api_error_status={result.get('api_error_status')}): "
                f"{str(result.get('result'))[:500]}"
            )
        return result

    def _check_subscription_auth(self) -> None:
        """Refuse to run unless the CLI is logged in with a Claude subscription."""
        proc = self._subprocess([self.executable, "auth", "status"], timeout=60)
        try:
            status = json.loads(proc.stdout)
        except json.JSONDecodeError:
            status = {}
        if not status.get("loggedIn"):
            raise LLMUnavailableError("Claude CLI is not logged in", hint=_LOGIN_HINT)
        if status.get("authMethod") != "claude.ai":
            raise LLMUnavailableError(
                f"Claude CLI is not using a Claude subscription "
                f"(authMethod={status.get('authMethod')!r}) — refusing to risk API billing",
                hint=_LOGIN_HINT,
            )

    def _subprocess(self, cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, env=self._env, cwd=self._workdir, **kwargs
            )
        except FileNotFoundError:
            raise LLMUnavailableError(
                f"Claude CLI executable not found: {self.executable!r}",
                hint="Install Claude Code, or make sure `claude` is on the PATH used by the "
                "scheduled job (launchd does not load your shell profile).",
            ) from None


def _resolve_executable(name: str) -> str:
    """Find *name* on PATH, else in the usual install dirs (launchd's PATH is minimal)."""
    found = shutil.which(name) or shutil.which(
        name, path=os.pathsep.join(os.path.expanduser(d) for d in _INSTALL_DIRS)
    )
    return found or name  # unresolved: running it raises the "not found" alert


def _raise_if_unavailable(result: dict) -> None:
    """Map errors that affect every request to LLMUnavailableError."""
    text = str(result.get("result") or "")
    status = result.get("api_error_status")
    if status in (401, 403) or _AUTH_ERROR.search(text):
        raise LLMUnavailableError(f"Claude CLI authentication failed: {text[:300]}", hint=_LOGIN_HINT)
    if status == 429 or _LIMIT_ERROR.search(text):
        raise LLMUnavailableError(f"Claude plan usage limit reached: {text[:300]}", hint=_LIMIT_HINT)


def parse_cli_result(result: dict) -> ContentAnalysis:
    """Extract ContentAnalysis from ``claude -p --output-format json`` output."""
    data = result.get("structured_output")
    if not isinstance(data, dict):
        raise ValueError(f"No structured_output in CLI result: {str(result.get('result'))[:500]}")
    try:
        data = dict(data)
        data["tags"] = data.pop("descriptive_tags") + data.pop("functional_tags")
        return ContentAnalysis(**data)
    except (ValidationError, TypeError, KeyError):
        logger.warning("Invalid analysis output from claude -p: %s", result.get("structured_output"))
        raise


def log_cli_usage(result: dict, label: str) -> None:
    """Log token usage; ``cost`` is Claude Code's list-price estimate, not a charge."""
    u = result.get("usage", {})
    models = ",".join(result.get("modelUsage", {})) or "?"
    logger.info(
        "LLM usage [%s] model=%s input=%d cache_read=%d cache_write=%d output=%d "
        "thinking=%d turns=%s list_cost=$%.4f duration=%.1fs",
        label,
        models,
        u.get("input_tokens", 0),
        u.get("cache_read_input_tokens", 0),
        u.get("cache_creation_input_tokens", 0),
        u.get("output_tokens", 0),
        (u.get("output_tokens_details") or {}).get("thinking_tokens", 0),
        result.get("num_turns"),
        result.get("total_cost_usd") or 0.0,
        (result.get("duration_ms") or 0) / 1000,
    )
