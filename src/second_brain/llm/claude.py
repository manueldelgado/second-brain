"""Claude API implementation of LLMProvider."""

from __future__ import annotations

import json
import logging
import time

import anthropic

from second_brain.config import TaxonomyConfig
from second_brain.llm.prompts import (
    CLASSIFY_CONTENT_TOOL,
    build_analysis_prompt,
    build_system_prompt,
)
from second_brain.models import ContentAnalysis

logger = logging.getLogger(__name__)


def parse_classify_response(response: anthropic.types.Message) -> ContentAnalysis:
    """Extract ContentAnalysis from a classify_content tool-use response.

    Shared by ClaudeProvider (sync) and ClaudeBatchProvider (batch).
    """
    for block in response.content:
        if block.type == "tool_use" and block.name == "classify_content":
            data = block.input
            # Handle case where input is a string (malformed response)
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse tool input as JSON: %s", data)
                    raise ValueError(f"Tool input is not valid JSON: {data}")

            # Normalize key_takeaways if it's a string
            if isinstance(data.get("key_takeaways"), str):
                takeaways_str = data["key_takeaways"].strip()
                if takeaways_str.startswith("["):
                    try:
                        data["key_takeaways"] = json.loads(takeaways_str)
                    except json.JSONDecodeError:
                        data["key_takeaways"] = [takeaways_str]
                else:
                    lines = [
                        line.strip("- ").strip()
                        for line in takeaways_str.split("\n")
                        if line.strip() and line.strip() != "-"
                    ]
                    data["key_takeaways"] = lines if lines else ["Unable to extract takeaways"]

            # Ensure tags is a list
            if isinstance(data.get("tags"), str):
                tags_str = data["tags"].strip()
                if tags_str.startswith("["):
                    try:
                        data["tags"] = json.loads(tags_str)
                    except json.JSONDecodeError:
                        data["tags"] = []
                else:
                    data["tags"] = [t.strip() for t in tags_str.split(",") if t.strip()]

            if "tags" not in data:
                data["tags"] = []

            return ContentAnalysis(**data)

    raise ValueError(
        f"No classify_content tool use in response: "
        f"{json.dumps([b.model_dump() for b in response.content], indent=2)}"
    )


class ClaudeProvider:
    """LLM provider using the Anthropic Claude API with tool use."""

    def __init__(self, model: str = "claude-sonnet-4-20250514", max_tokens: int = 4096) -> None:
        self.client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY env var
        self.model = model
        self.max_tokens = max_tokens

    def analyze_content(
        self,
        content: str,
        taxonomy: TaxonomyConfig,
        content_hint: str | None = None,
    ) -> ContentAnalysis:
        """Analyze content via Claude API with structured tool-use output."""
        system_prompt = build_system_prompt(taxonomy)
        user_message = build_analysis_prompt(content, content_hint)

        response = self._call_with_retry(system_prompt, user_message)
        return parse_classify_response(response)

    def _call_with_retry(
        self,
        system: str,
        user_message: str,
        max_retries: int = 3,
    ) -> anthropic.types.Message:
        """Call the API with exponential backoff on rate limits."""
        for attempt in range(max_retries):
            try:
                return self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                    tools=[CLASSIFY_CONTENT_TOOL],
                    tool_choice={"type": "tool", "name": "classify_content"},
                    messages=[{"role": "user", "content": user_message}],
                )
            except anthropic.RateLimitError:
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** (attempt + 1)
                logger.warning("Rate limited, retrying in %ds...", wait)
                time.sleep(wait)
        raise RuntimeError("Unreachable")  # pragma: no cover
