"""Tests for core/tools.py — tool definitions and prompt generation."""
from echidna.mythic.agent_functions.core.tools import (
    OPENAI_TOOLS,
    ANTHROPIC_TOOLS,
    TOOL_SUMMARIES,
    tool_prompt_section,
)


def test_openai_tools_count():
    assert len(OPENAI_TOOLS) == 6


def test_anthropic_tools_match_openai():
    openai_names = [t["function"]["name"] for t in OPENAI_TOOLS]
    anthropic_names = [t["name"] for t in ANTHROPIC_TOOLS]
    assert openai_names == anthropic_names


def test_anthropic_tools_have_input_schema():
    for t in ANTHROPIC_TOOLS:
        assert "input_schema" in t
        assert t["input_schema"]["type"] == "object"


def test_summaries_cover_all_tools():
    tool_names = {t["function"]["name"] for t in OPENAI_TOOLS}
    summary_names = set(TOOL_SUMMARIES.keys())
    assert tool_names == summary_names, (
        f"Missing summaries: {tool_names - summary_names}, "
        f"Extra summaries: {summary_names - tool_names}"
    )


def test_tool_prompt_section_format():
    section = tool_prompt_section()
    assert section.startswith("You have tools to interact with Mythic directly:")
    for t in OPENAI_TOOLS:
        name = t["function"]["name"]
        assert f"- {name}:" in section


def test_execute_command_has_required_params():
    exec_tool = next(
        t for t in OPENAI_TOOLS if t["function"]["name"] == "execute_command"
    )
    params = exec_tool["function"]["parameters"]
    assert "callback_id" in params["properties"]
    assert "command" in params["properties"]
    assert "callback_id" in params["required"]
    assert "command" in params["required"]


def test_tag_task_has_required_params():
    tag_tool = next(
        t for t in OPENAI_TOOLS if t["function"]["name"] == "tag_task"
    )
    params = tag_tool["function"]["parameters"]
    assert "task_id" in params["required"]
    assert "technique_id" in params["required"]


def test_list_callbacks_no_required_params():
    lc_tool = next(
        t for t in OPENAI_TOOLS if t["function"]["name"] == "list_callbacks"
    )
    assert lc_tool["function"]["parameters"]["required"] == []
