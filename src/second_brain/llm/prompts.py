"""Prompt templates for LLM content analysis."""

from __future__ import annotations

from second_brain.config import TaxonomyConfig


def build_system_prompt(taxonomy: TaxonomyConfig) -> str:
    """Build system prompt with full taxonomy context."""
    tag_descriptions = []
    for tag, scope in taxonomy.descriptive.items():
        tag_descriptions.append(f"  - {tag}: {scope}")
    for tag, scope in taxonomy.functional.items():
        tag_descriptions.append(f"  - {tag}: {scope}")

    rules = "\n".join(f"  - {r}" for r in taxonomy.classification_rules)

    return f"""\
You are a content analyst for a knowledge management system (Second Brain).
Your job is to analyze content and produce structured metadata.

## Available Tags

{chr(10).join(tag_descriptions)}

## Classification Rules

{rules}

## Output Requirements

- Summary: 2-4 sentences in English, regardless of source language
- Key takeaways: 3-7 bullet points capturing the main ideas
- Tags: select from the available tags above (1-3 descriptive + 0-2 functional)
- Content type: one of newsletter, clipping, paper, book, tool, note
- Description: one sentence describing the content

Always respond using the classify_content tool."""


CLASSIFY_CONTENT_TOOL = {
    "name": "classify_content",
    "description": "Classify and summarize a piece of content for the Second Brain.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "2-4 sentence summary in English.",
            },
            "key_takeaways": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-7 bullet points with main ideas.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags from the taxonomy (1-3 descriptive + 0-2 functional).",
            },
            "content_type": {
                "type": "string",
                "enum": ["newsletter", "clipping", "paper", "book", "tool", "note"],
                "description": "The type of content.",
            },
            "description": {
                "type": "string",
                "description": "One-sentence description of the content.",
            },
        },
        "required": ["summary", "key_takeaways", "tags", "content_type", "description"],
    },
}


def build_analysis_prompt(content: str, hint: str | None = None) -> str:
    """Build the user message for content analysis."""
    parts = []
    if hint:
        parts.append(f"Content source hint: {hint}")
    parts.append("Analyze the following content:\n")

    # Truncate to 6k chars to reduce token usage while keeping quality
    max_chars = 6_000
    if len(content) > max_chars:
        parts.append(content[:max_chars])
        parts.append(f"\n[... truncated, {len(content) - max_chars} chars omitted]")
    else:
        parts.append(content)

    return "\n".join(parts)
