"""Tests for core/constants.py — system prompt and config constants."""
from echidna.mythic.agent_functions.core.constants import (
    SYSTEM_PROMPT,
    PROVIDER_DEFAULTS,
    SECRET_KEYS,
    MAX_TOOL_ROUNDS,
    TASK_POLL_TIMEOUT,
    LLM_MAX_RETRIES,
    LLM_RETRY_BACKOFF,
)


def test_system_prompt_has_identity():
    assert "You are Echidna" in SYSTEM_PROMPT


def test_system_prompt_has_tool_section():
    assert "list_callbacks:" in SYSTEM_PROMPT
    assert "execute_command:" in SYSTEM_PROMPT
    assert "tag_task:" in SYSTEM_PROMPT


def test_system_prompt_has_critical_rules():
    assert "CRITICAL RULES" in SYSTEM_PROMPT
    assert "ZERO knowledge" in SYSTEM_PROMPT
    assert "NEVER fabricate" in SYSTEM_PROMPT


def test_system_prompt_has_when_to_use():
    assert "WHEN TO USE TOOLS" in SYSTEM_PROMPT


def test_provider_defaults():
    assert "Anthropic" in PROVIDER_DEFAULTS
    assert "OpenAI" in PROVIDER_DEFAULTS
    assert "Google" in PROVIDER_DEFAULTS
    assert "Kimi" in PROVIDER_DEFAULTS


def test_secret_keys_cover_providers():
    for provider in PROVIDER_DEFAULTS:
        assert provider in SECRET_KEYS


def test_custom_provider_secret():
    assert SECRET_KEYS["Custom"] == "openai_api_key"


def test_retry_backoff_length():
    assert len(LLM_RETRY_BACKOFF) == LLM_MAX_RETRIES


def test_retry_backoff_increasing():
    for i in range(1, len(LLM_RETRY_BACKOFF)):
        assert LLM_RETRY_BACKOFF[i] > LLM_RETRY_BACKOFF[i - 1]


def test_limits_positive():
    assert MAX_TOOL_ROUNDS > 0
    assert TASK_POLL_TIMEOUT > 0
    assert LLM_MAX_RETRIES > 0
