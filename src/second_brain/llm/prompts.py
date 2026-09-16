"""Prompt templates and output schema for LLM content analysis."""

from __future__ import annotations

from second_brain.config import TaxonomyConfig

MAX_CONTENT_CHARS = 25_000

CONTENT_TYPES = ["newsletter", "clipping", "paper", "book", "tool", "note"]


def build_system_prompt(taxonomy: TaxonomyConfig, include_content_type: bool = True) -> str:
    """Build system prompt with full taxonomy context."""
    tag_lines = [f"  - {tag}: {scope}" for tag, scope in taxonomy.descriptive.items()]
    func_lines = [f"  - {tag}: {scope}" for tag, scope in taxonomy.functional.items()]
    rules = "\n".join(f"- {r}" for r in taxonomy.classification_rules)
    context = f"{taxonomy.context.strip()}\n\n" if taxonomy.context.strip() else ""
    content_type_line = (
        "- content_type: what the item is — newsletter, clipping (a saved web article), "
        "paper, book, tool or note.\n"
        if include_content_type
        else ""
    )

    return f"""\
You summarize and tag content for a Second Brain, an Obsidian knowledge base.

{context}## What to write

- summary: 2-4 sentences, at most 80 words, in English whatever the source language. State \
the author's central argument or news and the reasoning or evidence behind it, not a list of \
topics.
- key_takeaways: 3-6 items, each a single sentence of at most 25 words. Prefer specific, \
reusable ideas: a claim worth writing about, a framework, a number, a practice to recommend. \
Leave out housekeeping such as sponsors, surveys, event plugs and subscription notes.
- descriptive_tags and functional_tags: see Tags below.
{content_type_line}\
- description: one sentence of at most 30 words saying what the piece is and why it is worth \
keeping.

## Tags

Descriptive tags (what the content is about):
{chr(10).join(tag_lines)}

Functional tags (how the owner can use it):
{chr(10).join(func_lines)}

- descriptive_tags: at least one, from the descriptive list.
- functional_tags: go through every functional tag in turn and include each one whose \
definition the content meets; leave it out when it does not, since a related topic alone is \
not enough. The list may be empty.

{rules}"""


def build_output_schema(taxonomy: TaxonomyConfig, include_content_type: bool = True) -> dict:
    """JSON schema for structured outputs; tag lists are restricted to the taxonomy.

    Separate descriptive/functional lists make the model decide on functional tags explicitly.
    """
    properties: dict = {
        "summary": {"type": "string"},
        "key_takeaways": {"type": "array", "items": {"type": "string"}},
        "descriptive_tags": {
            "type": "array",
            "items": {"type": "string", "enum": list(taxonomy.descriptive)},
        },
        "functional_tags": {
            "type": "array",
            "items": {"type": "string", "enum": list(taxonomy.functional)},
        },
    }
    if include_content_type:
        properties["content_type"] = {"type": "string", "enum": CONTENT_TYPES}
    properties["description"] = {"type": "string"}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def build_analysis_prompt(content: str, hint: str | None = None) -> str:
    """Build the user message for content analysis."""
    parts = []
    if hint:
        parts.append(f"Source: {hint}\n")
    if len(content) > MAX_CONTENT_CHARS:
        omitted = len(content) - MAX_CONTENT_CHARS
        content = f"{content[:MAX_CONTENT_CHARS]}\n[... truncated, {omitted} chars omitted]"
    parts.append(f"<content>\n{content}\n</content>")
    return "\n".join(parts)
