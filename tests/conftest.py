"""Stub mythic_container so tests can import echidna modules locally."""
import os
import sys
import types
import enum
import asyncio

# Use an in-memory SQLite store for tests (see core/state.py).
os.environ.setdefault("ECHIDNA_STATE_DB", ":memory:")

# ---- mythic_container stubs ----

mc = types.ModuleType("mythic_container")
sys.modules["mythic_container"] = mc

logging_mod = types.ModuleType("mythic_container.logging")
logging_mod.logger = types.SimpleNamespace(
    info=lambda *a, **k: None,
    warning=lambda *a, **k: None,
    exception=lambda *a, **k: None,
)
sys.modules["mythic_container.logging"] = logging_mod
mc.logging = logging_mod

# ChatBase
cb_mod = types.ModuleType("mythic_container.ChatBase")


class FakeOptionType(enum.Enum):
    Choice = "choice"
    String = "string"


class FakeChat:
    def build_chat_messages(self, request, system_prompt=""):
        return [{"role": "system", "content": system_prompt}]

    async def send_text(self, request, response_key, content=""):
        pass

    async def send_complete(self, request, response_key=None, complete_request=False):
        pass

    async def send_error(self, request, response_key, msg=""):
        pass

    async def send_response(self, request, response_key="", content="",
                            status="", complete=False, metadata=None):
        pass

    async def send_subagent_status(self, request, **kwargs):
        pass

    async def send_approval_request(self, request, **kwargs):
        pass

    async def update_channel_metadata(self, request, data):
        pass


class FakeChatRequest:
    def __init__(self, **kwargs):
        self.Prompt = kwargs.get("Prompt", "")
        self.ChannelID = kwargs.get("ChannelID", 1)
        self.ChannelName = kwargs.get("ChannelName", "test")
        self.OperationID = kwargs.get("OperationID", 1)
        self.APITokenID = kwargs.get("APITokenID", 0)
        self.Context = kwargs.get("Context", [])
        self.SlashCommand = kwargs.get("SlashCommand", None)
        self.InputResponse = kwargs.get("InputResponse", None)
        self.RequestMessageID = kwargs.get("RequestMessageID", 0)
        self.Configuration = kwargs.get("Configuration", {})
        self.Secrets = kwargs.get("Secrets", {})


class FakeConfigView:
    def __init__(self, data):
        self._data = data

    @classmethod
    def from_request(cls, request):
        return cls(getattr(request, "Configuration", {}))

    def text(self, key, default=""):
        return self._data.get(key, default)


class FakeSecretView:
    def __init__(self, data):
        self._data = data

    @classmethod
    def from_request(cls, request):
        return cls(getattr(request, "Secrets", {}))

    def text(self, key, default=""):
        return self._data.get(key, default)


cb_mod.Chat = FakeChat
cb_mod.ChatRequest = FakeChatRequest
cb_mod.ChatConfigView = FakeConfigView
cb_mod.ChatSecretView = FakeSecretView
cb_mod.ChatModelDefinition = type("ChatModelDefinition", (), {"__init__": lambda s, **k: None})
cb_mod.ChatModelMetadata = type("ChatModelMetadata", (), {"__init__": lambda s, **k: None})
cb_mod.ChatModelConfigurationOption = type("ChatModelConfigurationOption", (), {"__init__": lambda s, **k: None})
cb_mod.ChatModelConfigurationOptionType = FakeOptionType
cb_mod.ChatModelConfigurationOptionChoice = type("ChatModelConfigurationOptionChoice", (), {"__init__": lambda s, **k: None})
cb_mod.ChatSlashCommandDefinition = type("ChatSlashCommandDefinition", (), {"__init__": lambda s, **k: None})

sys.modules["mythic_container.ChatBase"] = cb_mod
mc.ChatBase = cb_mod

# MythicRPC
rpc_mod = types.ModuleType("mythic_container.MythicRPC")
for fn in [
    "SendMythicRPCCallbackSearch", "SendMythicRPCTaskSearch",
    "SendMythicRPCTaskCreate", "SendMythicRPCResponseSearch",
    "SendMythicRPCCredentialSearch", "SendMythicRPCCredentialCreate",
    "SendMythicRPCArtifactCreate", "SendMythicRPCArtifactSearch",
    "SendMythicRPCOperationEventLogCreate", "SendMythicRPCAPITokenCreate",
    "SendMythicRPCCallbackSearchCommand", "SendMythicRPCProcessSearch",
]:
    setattr(rpc_mod, fn, lambda *a, **k: None)


def _msg_init(self, **kwargs):
    for k, v in kwargs.items():
        setattr(self, k, v)


for cls_name in [
    "MythicRPCCallbackSearchMessage", "MythicRPCTaskSearchMessage",
    "MythicRPCTaskCreateMessage", "MythicRPCResponseSearchMessage",
    "MythicRPCCredentialSearchMessage", "MythicRPCCredentialCreateMessage",
    "MythicRPCArtifactCreateMessage", "MythicRPCArtifactSearchMessage",
    "MythicRPCArtifactSearchArtifactData",
    "MythicRPCOperationEventLogCreateMessage", "MythicRPCAPITokenCreateMessage",
    "MythicRPCCallbackSearchCommandMessage", "MythicRPCProcessesSearchMessage",
    "MythicRPCProcessSearchData",
]:
    setattr(rpc_mod, cls_name, type(cls_name, (), {"__init__": _msg_init}))
sys.modules["mythic_container.MythicRPC"] = rpc_mod
mc.MythicRPC = rpc_mod

gorpc_mod = types.ModuleType("mythic_container.MythicGoRPC")
sys.modules["mythic_container.MythicGoRPC"] = gorpc_mod
cred_mod = types.ModuleType(
    "mythic_container.MythicGoRPC.send_mythic_rpc_credential_create"
)
cred_mod.MythicRPCCredentialData = type(
    "MythicRPCCredentialData", (), {"__init__": lambda s, **k: None}
)
sys.modules[
    "mythic_container.MythicGoRPC.send_mythic_rpc_credential_create"
] = cred_mod

svc_mod = types.ModuleType("mythic_container.mythic_service")
sys.modules["mythic_container.mythic_service"] = svc_mod
mc.mythic_service = svc_mod
