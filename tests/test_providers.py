"""Tests for core/providers.py — approval detection and URL helpers."""
from echidna.mythic.agent_functions.core.providers import (
    ProviderMixin,
    _anthropic_root,
)


class FakeProvider(ProviderMixin):
    pass


provider = FakeProvider()


class TestFindApprovalNeeded:
    def test_no_approval_when_disabled(self):
        tools = [{"name": "execute_command", "input": {}}]
        assert provider._find_approval_needed(tools, False) is None

    def test_finds_execute_command_anthropic_format(self):
        tools = [
            {"name": "list_callbacks", "input": {}},
            {"name": "execute_command", "input": {"callback_id": 1}},
        ]
        result = provider._find_approval_needed(tools, True)
        assert result["name"] == "execute_command"

    def test_finds_execute_command_openai_format(self):
        tools = [
            {"function": {"name": "list_callbacks"}, "id": "a"},
            {"function": {"name": "execute_command"}, "id": "b"},
        ]
        result = provider._find_approval_needed(tools, True)
        assert result["function"]["name"] == "execute_command"

    def test_none_when_no_execute_command(self):
        tools = [
            {"name": "list_callbacks", "input": {}},
            {"name": "credential_create", "input": {}},
        ]
        assert provider._find_approval_needed(tools, True) is None

    def test_empty_tool_list(self):
        assert provider._find_approval_needed([], True) is None


class TestAnthropicRoot:
    def test_strips_v1(self):
        assert _anthropic_root("https://example.com/v1") == "https://example.com"

    def test_strips_v1_with_slash(self):
        assert _anthropic_root("https://example.com/v1/") == "https://example.com"

    def test_no_v1(self):
        assert _anthropic_root("https://example.com") == "https://example.com"

    def test_empty(self):
        assert _anthropic_root("") == ""

    def test_none(self):
        assert _anthropic_root(None) == ""

    def test_nested_path_with_v1(self):
        assert _anthropic_root("https://proxy.local/api/v1") == "https://proxy.local/api"
