"""Tests for LLM layer — prompts, request params and response parsing."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from second_brain.config import TaxonomyConfig
from second_brain.llm.claude import build_request_params, parse_analysis_response
from second_brain.llm.prompts import (
    MAX_CONTENT_CHARS,
    build_analysis_prompt,
    build_output_schema,
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
            "Never leave a note without tags",
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
        assert "Never leave a note without tags" in prompt

    def test_content_type_instruction_is_optional(self, taxonomy: TaxonomyConfig) -> None:
        assert "content_type" in build_system_prompt(taxonomy)
        assert "content_type" not in build_system_prompt(taxonomy, include_content_type=False)


class TestBuildAnalysisPrompt:
    def test_without_hint(self) -> None:
        prompt = build_analysis_prompt("Some content here")
        assert "<content>\nSome content here\n</content>" in prompt
        assert "Source:" not in prompt

    def test_with_hint(self) -> None:
        prompt = build_analysis_prompt("Content", hint="Benedict Evans")
        assert "Source: Benedict Evans" in prompt
        assert "Content" in prompt

    def test_truncation(self) -> None:
        prompt = build_analysis_prompt("x" * (MAX_CONTENT_CHARS + 5_000))
        assert "truncated, 5000 chars omitted" in prompt
        assert len(prompt) < MAX_CONTENT_CHARS + 200

    def test_no_truncation_below_limit(self) -> None:
        prompt = build_analysis_prompt("x" * MAX_CONTENT_CHARS)
        assert "truncated" not in prompt


class TestOutputSchema:
    def test_required_fields_and_strictness(self, taxonomy: TaxonomyConfig) -> None:
        schema = build_output_schema(taxonomy)
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {
            "summary", "key_takeaways", "descriptive_tags", "functional_tags",
            "content_type", "description",
        }

    def test_tag_lists_restricted_to_taxonomy(self, taxonomy: TaxonomyConfig) -> None:
        props = build_output_schema(taxonomy)["properties"]
        assert props["descriptive_tags"]["items"]["enum"] == ["ai/industry-news", "data/tools"]
        assert props["functional_tags"]["items"]["enum"] == ["func/trend-monitoring"]

    def test_content_type_can_be_omitted(self, taxonomy: TaxonomyConfig) -> None:
        schema = build_output_schema(taxonomy, include_content_type=False)
        assert "content_type" not in schema["properties"]
        assert "content_type" not in schema["required"]


class TestBuildRequestParams:
    def test_uses_structured_output_without_tools(self, taxonomy: TaxonomyConfig) -> None:
        params = build_request_params("m", 100, taxonomy, "body", "hint")
        assert "tools" not in params
        assert params["output_config"]["format"]["type"] == "json_schema"
        assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert "Source: hint" in params["messages"][0]["content"]

    def test_thinking_and_effort_omitted_by_default(self, taxonomy: TaxonomyConfig) -> None:
        params = build_request_params("m", 100, taxonomy, "body")
        assert "thinking" not in params
        assert "effort" not in params["output_config"]

    def test_thinking_and_effort_when_set(self, taxonomy: TaxonomyConfig) -> None:
        params = build_request_params(
            "m", 100, taxonomy, "body", thinking="disabled", effort="low"
        )
        assert params["thinking"] == {"type": "disabled"}
        assert params["output_config"]["effort"] == "low"


def _response(text: str | None, stop_reason: str = "end_turn") -> MagicMock:
    response = MagicMock()
    response.stop_reason = stop_reason
    if text is None:
        response.content = []
    else:
        block = MagicMock()
        block.type = "text"
        block.text = text
        response.content = [block]
    return response


VALID = {
    "summary": "Test summary",
    "key_takeaways": ["Point 1", "Point 2"],
    "descriptive_tags": ["ai/industry-news"],
    "functional_tags": ["func/trend-monitoring"],
    "description": "Test description",
}


class TestParseAnalysisResponse:
    def test_parses_json_text(self) -> None:
        result = parse_analysis_response(_response(json.dumps({**VALID, "content_type": "newsletter"})))
        assert isinstance(result, ContentAnalysis)
        assert result.summary == "Test summary"
        assert result.tags == ["ai/industry-news", "func/trend-monitoring"]
        assert result.content_type == "newsletter"

    def test_content_type_optional(self) -> None:
        assert parse_analysis_response(_response(json.dumps(VALID))).content_type is None

    @pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
    def test_incomplete_response_raises(self, stop_reason: str) -> None:
        with pytest.raises(ValueError, match=stop_reason):
            parse_analysis_response(_response(json.dumps(VALID), stop_reason))

    def test_no_text_block_raises(self) -> None:
        with pytest.raises(ValueError, match="No text block"):
            parse_analysis_response(_response(None))

    def test_invalid_output_logs_raw_text(self, caplog) -> None:
        bad = json.dumps({"summary": "raw-marker", "descriptive_tags": [], "functional_tags": []})
        with pytest.raises(ValidationError):
            parse_analysis_response(_response(bad))
        assert "raw-marker" in caplog.text
