"""Tests for LLM layer — prompts and response parsing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from second_brain.config import TaxonomyConfig
from second_brain.llm.prompts import (
    CLASSIFY_CONTENT_TOOL,
    build_analysis_prompt,
    build_system_prompt,
)
from second_brain.models import ContentAnalysis


@pytest.fixture
def taxonomy() -> TaxonomyConfig:
    return TaxonomyConfig(
        descriptive={
            "ai/industry-news": "Funding, M&A, product launches",
            "data/tools": "Python, SQL, ML libraries",
        },
        functional={
            "func/trend-monitoring": "Staying current on AI/tech/data",
        },
        classification_rules=[
            "Use 1-3 descriptive tags",
            "When in doubt, use fewer tags",
        ],
    )


class TestBuildSystemPrompt:
    def test_includes_taxonomy_tags(self, taxonomy: TaxonomyConfig) -> None:
        prompt = build_system_prompt(taxonomy)
        assert "ai/industry-news" in prompt
        assert "data/tools" in prompt
        assert "func/trend-monitoring" in prompt

    def test_includes_scope_descriptions(self, taxonomy: TaxonomyConfig) -> None:
        prompt = build_system_prompt(taxonomy)
        assert "Funding, M&A, product launches" in prompt

    def test_includes_rules(self, taxonomy: TaxonomyConfig) -> None:
        prompt = build_system_prompt(taxonomy)
        assert "Use 1-3 descriptive tags" in prompt
        assert "When in doubt, use fewer tags" in prompt

    def test_includes_tool_instruction(self, taxonomy: TaxonomyConfig) -> None:
        prompt = build_system_prompt(taxonomy)
        assert "classify_content" in prompt


class TestBuildAnalysisPrompt:
    def test_without_hint(self) -> None:
        prompt = build_analysis_prompt("Some content here")
        assert "Some content here" in prompt

    def test_with_hint(self) -> None:
        prompt = build_analysis_prompt("Content", hint="Benedict Evans")
        assert "Benedict Evans" in prompt
        assert "Content" in prompt

    def test_truncation(self) -> None:
        long_content = "x" * 50_000
        prompt = build_analysis_prompt(long_content)
        assert "truncated" in prompt
        assert len(prompt) < 50_000

    def test_no_truncation_short_content(self) -> None:
        prompt = build_analysis_prompt("short text")
        assert "truncated" not in prompt


class TestClassifyContentTool:
    def test_tool_schema_has_required_fields(self) -> None:
        schema = CLASSIFY_CONTENT_TOOL
        assert schema["name"] == "classify_content"
        props = schema["input_schema"]["properties"]
        assert "summary" in props
        assert "key_takeaways" in props
        assert "tags" in props
        assert "content_type" in props
        assert "description" in props

    def test_content_type_enum(self) -> None:
        enum = CLASSIFY_CONTENT_TOOL["input_schema"]["properties"]["content_type"]["enum"]
        assert "newsletter" in enum
        assert "clipping" in enum
        assert "paper" in enum
        assert "book" in enum


class TestClaudeProviderParseResponse:
    def test_parse_tool_use_response(self) -> None:
        from second_brain.llm.claude import ClaudeProvider, parse_classify_response

        provider = ClaudeProvider.__new__(ClaudeProvider)

        # Create a mock response with tool_use block
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "classify_content"
        tool_block.input = {
            "summary": "Test summary",
            "key_takeaways": ["Point 1", "Point 2"],
            "tags": ["ai/industry-news"],
            "content_type": "newsletter",
            "description": "Test description",
        }

        response = MagicMock()
        response.content = [tool_block]

        result = parse_classify_response(response)
        assert isinstance(result, ContentAnalysis)
        assert result.summary == "Test summary"
        assert result.tags == ["ai/industry-news"]
        assert result.content_type == "newsletter"

    def test_parse_response_no_tool_use_raises(self) -> None:
        from second_brain.llm.claude import ClaudeProvider, parse_classify_response

        provider = ClaudeProvider.__new__(ClaudeProvider)

        text_block = MagicMock()
        text_block.type = "text"
        text_block.model_dump.return_value = {"type": "text", "text": "Hello"}

        response = MagicMock()
        response.content = [text_block]

        with pytest.raises(ValueError, match="No classify_content"):
            parse_classify_response(response)

    def test_parse_response_wrong_tool_name_raises(self) -> None:
        from second_brain.llm.claude import ClaudeProvider, parse_classify_response

        provider = ClaudeProvider.__new__(ClaudeProvider)

        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "other_tool"
        tool_block.model_dump.return_value = {"type": "tool_use", "name": "other_tool"}

        response = MagicMock()
        response.content = [tool_block]

        with pytest.raises(ValueError, match="No classify_content"):
            parse_classify_response(response)
