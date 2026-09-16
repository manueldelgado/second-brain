"""Claude API implementation of LLMProvider."""

from __future__ import annotations

import json
import logging
import time

import anthropic
from pydantic import ValidationError

from second_brain.config import TaxonomyConfig
from second_brain.llm.prompts import (
    build_analysis_prompt,
    build_output_schema,
    build_system_prompt,
)
from second_brain.models import ContentAnalysis

logger = logging.getLogger(__name__)


def build_request_params(
    model: str,
    max_tokens: int,
    taxonomy: TaxonomyConfig,
    content: str,
    content_hint: str | None = None,
    include_content_type: bool = True,
    thinking: str | None = None,
    effort: str | None = None,
) -> dict:
    """Messages API params for one analysis request (shared by sync and batch)."""
    output_config: dict = {
        "format": {
            "type": "json_schema",
            "schema": build_output_schema(taxonomy, include_content_type),
        }
    }
    if effort:
        output_config["effort"] = effort
    params: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [
            {
                "type": "text",
                "text": build_system_prompt(taxonomy, include_content_type),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": build_analysis_prompt(content, content_hint)}],
        "output_config": output_config,
    }
    if thinking:
        params["thinking"] = {"type": thinking}
    return params


def log_usage(response: anthropic.types.Message, label: str) -> None:
    u = response.usage
    logger.info(
        "LLM usage [%s] model=%s input=%d cache_read=%d cache_write=%d output=%d stop=%s",
        label,
        response.model,
        u.input_tokens,
        u.cache_read_input_tokens or 0,
        u.cache_creation_input_tokens or 0,
        u.output_tokens,
        response.stop_reason,
    )


def parse_analysis_response(response: anthropic.types.Message) -> ContentAnalysis:
    """Extract ContentAnalysis from a structured-output (JSON schema) response.

    Shared by ClaudeProvider (sync) and ClaudeBatchProvider (batch).
    """
    if response.stop_reason in ("max_tokens", "refusal"):
        raise ValueError(f"Incomplete analysis response (stop_reason={response.stop_reason})")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ValueError(f"No text block in response (stop_reason={response.stop_reason})")

    try:
        data = json.loads(text)
        data["tags"] = data.pop("descriptive_tags") + data.pop("functional_tags")
        return ContentAnalysis(**data)
    except (json.JSONDecodeError, ValidationError, TypeError, KeyError):
        logger.warning(
            "Invalid analysis output (stop_reason=%s): %s", response.stop_reason, text
        )
        raise


class ClaudeProvider:
    """LLM provider using the Anthropic Claude API with structured outputs."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 4096,
        thinking: str | None = None,
        effort: str | None = None,
    ) -> None:
        self.client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY env var
        self.model = model
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.effort = effort

    def analyze_content(
        self,
        content: str,
        taxonomy: TaxonomyConfig,
        content_hint: str | None = None,
        include_content_type: bool = True,
    ) -> ContentAnalysis:
        """Analyze content via Claude API with JSON-schema structured output."""
        params = build_request_params(
            self.model,
            self.max_tokens,
            taxonomy,
            content,
            content_hint,
            include_content_type,
            self.thinking,
            self.effort,
        )
        response = self._call_with_retry(params)
        log_usage(response, content_hint or "sync")
        return parse_analysis_response(response)

    def _call_with_retry(self, params: dict, max_retries: int = 3) -> anthropic.types.Message:
        """Call the API with exponential backoff on rate limits."""
        for attempt in range(max_retries):
            try:
                return self.client.messages.create(**params)
            except anthropic.RateLimitError:
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** (attempt + 1)
                logger.warning("Rate limited, retrying in %ds...", wait)
                time.sleep(wait)
        raise RuntimeError("Unreachable")  # pragma: no cover
