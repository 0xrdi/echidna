"""Tests for echidna_chat.py — config, tokens, context resets, slash routing."""
import sys
import json
import types
import pytest
from echidna.mythic.agent_functions.echidna_chat import EchidnaChat, PLAYBOOKS
from echidna.mythic.agent_functions.core.state import StateStore, get_store

_cb = sys.modules["mythic_container.ChatBase"]
FakeChatRequest = _cb.ChatRequest
FakeConfigView = _cb.ChatConfigView
FakeSecretView = _cb.ChatSecretView


@pytest.fixture
def chat():
    return EchidnaChat()


@pytest.fixture
def request_obj():
    req = FakeChatRequest(ChannelID=42, OperationID=1)
    req.Configuration = {"provider": "Anthropic"}
    req.Secrets = {}
    return req


# ---- _resolve_api_key ----

class TestResolveApiKey:
    def test_from_config(self, chat):
        config = FakeConfigView({"api_key": "sk-test"})
        secrets = FakeSecretView({})
        assert chat._resolve_api_key("Anthropic", config, secrets, "") == "sk-test"

    def test_from_secrets(self, chat):
        config = FakeConfigView({})
        secrets = FakeSecretView({"anthropic_api_key": "sk-secret"})
        assert chat._resolve_api_key("Anthropic", config, secrets, "") == "sk-secret"

    def test_keyless_with_base_url(self, chat):
        config = FakeConfigView({})
        secrets = FakeSecretView({})
        assert chat._resolve_api_key("Custom", config, secrets, "http://local") == "not-needed"

    def test_raises_no_key_no_url(self, chat):
        config = FakeConfigView({})
        secrets = FakeSecretView({})
        with pytest.raises(RuntimeError, match="API Key"):
            chat._resolve_api_key("Anthropic", config, secrets, "")

    def test_config_key_takes_precedence(self, chat):
        config = FakeConfigView({"api_key": "from-config"})
        secrets = FakeSecretView({"anthropic_api_key": "from-secret"})
        assert chat._resolve_api_key("Anthropic", config, secrets, "") == "from-config"


# ---- _chat_completions_url ----

class TestChatCompletionsUrl:
    def test_openai_default(self, chat):
        url = chat._chat_completions_url("OpenAI", "")
        assert url == "https://api.openai.com/v1/chat/completions"

    def test_custom_base_url(self, chat):
        url = chat._chat_completions_url("Custom", "http://local/v1")
        assert url == "http://local/v1/chat/completions"

    def test_custom_no_base_url_raises(self, chat):
        with pytest.raises(RuntimeError, match="base_url required"):
            chat._chat_completions_url("Custom", "")


# ---- _track_tokens ----
# Each test uses a unique channel ID: the in-memory store is shared
# across the whole test process.

class TestTrackTokens:
    def test_first_usage(self, chat):
        chat._track_tokens(991, {"input": 100, "output": 50})
        assert get_store().get_tokens(991) == {"input": 100, "output": 50}

    def test_accumulates(self, chat):
        chat._track_tokens(992, {"input": 100, "output": 50})
        chat._track_tokens(992, {"input": 200, "output": 100})
        assert get_store().get_tokens(992) == {"input": 300, "output": 150}

    def test_skips_none(self, chat):
        chat._track_tokens(993, None)
        assert get_store().get_tokens(993) is None

    def test_skips_empty(self, chat):
        chat._track_tokens(994, {"input": 10, "output": 5})
        chat._track_tokens(994, {})
        assert get_store().get_tokens(994) == {"input": 10, "output": 5}


# ---- context reset logic ----

class TestContextReset:
    def test_pending_reset_sets_cutoff(self, chat):
        chat._pending_resets.add(942)
        msg = types.SimpleNamespace(ID=100, AuthorType="ai",
                                     SenderDisplayName="Echidna",
                                     Message="report", CreatedAt="")
        req = FakeChatRequest(ChannelID=942, Context=[msg])
        req.Configuration = {"provider": "Anthropic"}
        req.Secrets = {"anthropic_api_key": "sk-test"}

        # Simulate the pending reset resolution from chat()
        if req.ChannelID in chat._pending_resets:
            chat._pending_resets.discard(req.ChannelID)
            if req.Context:
                get_store().set_cutoff(req.ChannelID, req.Context[-1].ID)

        assert get_store().get_cutoff(942) == 100
        assert 942 not in chat._pending_resets

    def test_cutoff_filters_old_messages(self, chat):
        get_store().set_cutoff(943, 50)
        old = types.SimpleNamespace(ID=30)
        boundary = types.SimpleNamespace(ID=50)
        new = types.SimpleNamespace(ID=70)
        context = [old, boundary, new]

        cutoff = get_store().get_cutoff(943)
        filtered = [m for m in context if m.ID > cutoff]
        assert len(filtered) == 1
        assert filtered[0].ID == 70


# ---- dangerous flag ----

class TestDangerousFlag:
    def test_strips_dangerous_prefix(self):
        prompt = "--dangerous run whoami on callback #1"
        cleaned = prompt.lstrip().removeprefix("--dangerous").lstrip()
        assert cleaned == "run whoami on callback #1"

    def test_strips_with_leading_space(self):
        prompt = "  --dangerous  ls"
        cleaned = prompt.lstrip().removeprefix("--dangerous").lstrip()
        assert cleaned == "ls"


# ---- playbooks loaded ----

class TestPlaybooks:
    def test_playbooks_loaded(self):
        assert len(PLAYBOOKS) > 0

    def test_playbooks_have_descriptions(self):
        for name, pb in PLAYBOOKS.items():
            assert "description" in pb
            assert "prompt" in pb

    def test_known_playbook_exists(self):
        assert "post-exploitation" in PLAYBOOKS


# ---- class structure ----

class TestClassStructure:
    def test_mro(self):
        mro_names = [c.__name__ for c in EchidnaChat.__mro__]
        assert "ProviderMixin" in mro_names
        assert "ToolHandlerMixin" in mro_names
        assert "ReportMixin" in mro_names

    def test_state_is_sqlite_backed(self):
        assert hasattr(EchidnaChat, "_pending_resets")
        assert isinstance(get_store(), StateStore)
        # per-channel state moved to core/state.py (SQLite)
        assert not hasattr(EchidnaChat, "_token_usage")
        assert not hasattr(EchidnaChat, "_pinned_callbacks")
        assert not hasattr(EchidnaChat, "_context_resets")

    def test_semver(self):
        assert EchidnaChat.semver == "1.2.0"
