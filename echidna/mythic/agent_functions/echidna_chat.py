from mythic_container.ChatBase import (
    Chat,
    ChatRequest,
    ChatModelDefinition,
    ChatModelMetadata,
    ChatModelConfigurationOption,
    ChatModelConfigurationOptionType,
    ChatModelConfigurationOptionChoice,
    ChatSlashCommandDefinition,
    ChatConfigView,
    ChatSecretView,
)
from mythic_container.MythicRPC import *
from mythic_container.logging import logger
import asyncio
import json
import pathlib
import uuid

from .core import (
    SYSTEM_PROMPT,
    PROVIDER_DEFAULTS,
    SECRET_KEYS,
    ProviderMixin,
    ToolHandlerMixin,
    ReportMixin,
    get_store,
)


def _load_playbooks():
    playbooks = {}
    pb_dir = pathlib.Path(__file__).parent / "playbooks"
    if not pb_dir.is_dir():
        return playbooks
    for md in sorted(pb_dir.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        name = md.stem
        description = ""
        body = text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].strip().splitlines():
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip()
                body = parts[2].strip()
        playbooks[name] = {"description": description, "prompt": body}
    return playbooks


PLAYBOOKS = _load_playbooks()


class EchidnaChat(ProviderMixin, ToolHandlerMixin, ReportMixin, Chat):
    name = "echidna"
    description = "Virtual LLM agent for red team operations"
    semver = "1.2.0"
    agent_icon_path = "echidna/mythic/agent_functions/echidna.svg"

    models = [
        ChatModelDefinition(
            Name="echidna",
            Description="Agentic LLM with Mythic tool use for red team ops",
            Metadata=ChatModelMetadata(
                Provider="echidna",
                ConfigurationOptions=[
                    ChatModelConfigurationOption(
                        Name="provider",
                        DisplayName="Provider",
                        Type=ChatModelConfigurationOptionType.Choice,
                        Description="LLM provider",
                        Required=True,
                        DefaultValue="Anthropic",
                        Choices=[
                            ChatModelConfigurationOptionChoice(
                                Label="Anthropic", Value="Anthropic",
                            ),
                            ChatModelConfigurationOptionChoice(
                                Label="OpenAI", Value="OpenAI",
                            ),
                            ChatModelConfigurationOptionChoice(
                                Label="Google", Value="Google",
                            ),
                            ChatModelConfigurationOptionChoice(
                                Label="Kimi", Value="Kimi",
                            ),
                            ChatModelConfigurationOptionChoice(
                                Label="Custom", Value="Custom",
                                Description="Any OpenAI-compatible endpoint",
                            ),
                        ],
                    ),
                    ChatModelConfigurationOption(
                        Name="model",
                        DisplayName="Model",
                        Type=ChatModelConfigurationOptionType.String,
                        Description="Model name (empty = vendor default)",
                    ),
                    ChatModelConfigurationOption(
                        Name="api_key",
                        DisplayName="API Key",
                        Type=ChatModelConfigurationOptionType.String,
                        Description=(
                            "Provider API key. Not needed when "
                            "base_url points at a keyless endpoint."
                        ),
                    ),
                    ChatModelConfigurationOption(
                        Name="base_url",
                        DisplayName="Base URL",
                        Type=ChatModelConfigurationOptionType.String,
                        Description=(
                            "Custom endpoint URL with /v1 suffix "
                            "(empty = vendor API)"
                        ),
                    ),
                    ChatModelConfigurationOption(
                        Name="playbook",
                        DisplayName="Playbook",
                        Type=ChatModelConfigurationOptionType.Choice,
                        Description=(
                            "Active playbook — injects a specialized "
                            "system prompt for a specific kill chain phase"
                        ),
                        DefaultValue="None",
                        Choices=[
                            ChatModelConfigurationOptionChoice(
                                Label="None", Value="None",
                                Description="No playbook — general operator mode",
                            ),
                        ] + [
                            ChatModelConfigurationOptionChoice(
                                Label=name,
                                Value=name,
                                Description=pb["description"],
                            )
                            for name, pb in PLAYBOOKS.items()
                        ],
                    ),
                    ChatModelConfigurationOption(
                        Name="approval_mode",
                        DisplayName="Command Approval",
                        Type=ChatModelConfigurationOptionType.Choice,
                        Description=(
                            "Require operator approval before "
                            "executing commands on callbacks"
                        ),
                        DefaultValue="Enabled",
                        Choices=[
                            ChatModelConfigurationOptionChoice(
                                Label="Enabled", Value="Enabled",
                                Description="Ask before running commands on callbacks",
                            ),
                            ChatModelConfigurationOptionChoice(
                                Label="Disabled", Value="Disabled",
                                Description="Execute without asking (dangerous)",
                            ),
                        ],
                    ),
                ],
                OptionalUserSecrets=[
                    "anthropic_api_key",
                    "openai_api_key",
                    "google_api_key",
                    "kimi_api_key",
                ],
                SlashCommands=[
                    ChatSlashCommandDefinition(
                        Name="help",
                        Description="Show available commands and usage",
                    ),
                    ChatSlashCommandDefinition(
                        Name="callbacks",
                        Description="List active Mythic callbacks",
                    ),
                    ChatSlashCommandDefinition(
                        Name="playbooks",
                        Description="List available playbooks",
                    ),
                    ChatSlashCommandDefinition(
                        Name="reset",
                        Description="Clear conversation context",
                    ),
                    ChatSlashCommandDefinition(
                        Name="use",
                        Description="Pin a default callback (e.g. /use 1)",
                    ),
                    ChatSlashCommandDefinition(
                        Name="report",
                        Description="Generate operation report",
                    ),
                    ChatSlashCommandDefinition(
                        Name="export",
                        Description="Export chat as markdown",
                    ),
                ] + [
                    ChatSlashCommandDefinition(
                        Name=name,
                        Description=pb["description"] or name,
                    )
                    for name, pb in PLAYBOOKS.items()
                ],
            ),
        ),
    ]

    # ---- per-channel state ----

    # Pinned callbacks, token usage, and context reset cutoffs live in
    # the SQLite StateStore (core/state.py) so they survive restarts.
    # _pending_resets is transient by design: a missed deferred cutoff
    # just means the next context is not trimmed once.
    _pending_resets = set()

    # ---- main entry point ----

    async def chat(self, request: ChatRequest):
        response_key = str(uuid.uuid4())

        if request.InputResponse:
            ir = request.InputResponse
            logger.info(
                f"[echidna] approval response: "
                f"action={ir.Action!r}"
            )
            data = ir.InputRequest.get("data", {})
            if data.get("type") == "execute_command_approval":
                approved = ir.Action.lower() in (
                    "approve", "approved", "accept", "accepted", "yes",
                )
                cmd = data.get("command", "")
                params = data.get("params", "")
                cmd_str = f"{cmd} {params}".strip()
                cb_id = data.get("callback_id", "?")
                if approved:
                    operator_id = await self._get_operator_id(request)
                    func_args = {
                        "callback_id": data["callback_id"],
                        "command": cmd,
                    }
                    if params:
                        func_args["params"] = params
                    tool_key = f"{response_key}:tool:approved:0"
                    await self._send_tool_card(
                        request, tool_key, "execute_command",
                        func_args, "running",
                    )
                    result = await self._execute_tool(
                        "execute_command", func_args,
                        request, operator_id,
                    )
                    await self._send_tool_card(
                        request, tool_key, "execute_command",
                        func_args, "complete", result=result,
                    )
                    request.Prompt = (
                        f"[Command approved and executed] "
                        f"`{cmd_str}` on callback #{cb_id}.\n\n"
                        f"Output:\n{result}\n\n"
                        "Continue — analyze the output, tag the "
                        "task, and proceed with follow-up actions."
                    )
                else:
                    request.Prompt = (
                        f"[Command denied] `{cmd_str}` on callback "
                        f"#{cb_id} was not executed. Suggest an "
                        "alternative or ask the operator."
                    )

        require_approval = True
        prompt_text = request.Prompt or ""
        if prompt_text.lstrip().startswith("--dangerous"):
            require_approval = False
            request.Prompt = (
                prompt_text.lstrip()
                .removeprefix("--dangerous").lstrip()
            )
        if (
            request.SlashCommand
            and request.SlashCommand.Argument
            and request.SlashCommand.Argument.lstrip()
                .startswith("--dangerous")
        ):
            require_approval = False
            cleaned = (
                request.SlashCommand.Argument.lstrip()
                .removeprefix("--dangerous").lstrip()
            )
            request.SlashCommand.Argument = cleaned
            if not request.Prompt:
                request.Prompt = cleaned

        slash_playbook = None
        if request.SlashCommand:
            name = request.SlashCommand.Name
            if name == "help":
                await self._show_help(request, response_key)
                return
            if name == "callbacks":
                await self._list_callbacks_slash(request, response_key)
                return
            if name == "playbooks":
                await self._list_playbooks(request, response_key)
                return
            if name == "reset":
                get_store().set_cutoff(
                    request.ChannelID,
                    request.Context[-1].ID if request.Context else 0,
                )
                get_store().clear_pinned(request.ChannelID)
                await self._update_channel_metadata(request)
                await self.send_text(
                    request, response_key,
                    content=(
                        "Context cleared. Previous messages will "
                        "not be sent to the LLM. "
                        "Pinned callback cleared."
                    ),
                )
                await self.send_complete(
                    request, response_key, complete_request=True,
                )
                return
            if name == "use":
                await self._handle_use(request, response_key)
                return
            if name == "report":
                await self._generate_report(request, response_key)
                return
            if name == "export":
                await self._export_chat(request, response_key)
                return
            if name in PLAYBOOKS:
                slash_playbook = name

        if request.ChannelID in self._pending_resets:
            self._pending_resets.discard(request.ChannelID)
            if request.Context:
                get_store().set_cutoff(
                    request.ChannelID, request.Context[-1].ID,
                )
        cutoff = get_store().get_cutoff(request.ChannelID)
        if cutoff:
            request.Context = [
                m for m in request.Context if m.ID > cutoff
            ]

        config = ChatConfigView.from_request(request)
        secrets = ChatSecretView.from_request(request)
        provider = config.text("provider", "Anthropic")
        model = config.text("model") or PROVIDER_DEFAULTS.get(provider, "")
        base_url = config.text("base_url", "").rstrip("/")
        playbook_name = slash_playbook or config.text("playbook", "None")

        if config.text("approval_mode", "Enabled") == "Disabled":
            require_approval = False

        try:
            api_key = self._resolve_api_key(
                provider, config, secrets, base_url,
            )
        except RuntimeError as e:
            await self.send_error(request, response_key, str(e))
            return

        system_prompt = SYSTEM_PROMPT
        pinned = get_store().get_pinned(request.ChannelID)
        if pinned:
            system_prompt += (
                f"\n\nDEFAULT CALLBACK: The operator has pinned "
                f"callback #{pinned}. Use this callback for "
                "execute_command unless the operator specifies "
                "a different one."
            )
        if playbook_name != "None" and playbook_name in PLAYBOOKS:
            system_prompt += (
                f"\n\n## ACTIVE PLAYBOOK: {playbook_name}\n\n"
                + PLAYBOOKS[playbook_name]["prompt"]
            )

        await self._update_channel_metadata(request)

        try:
            if provider == "Anthropic":
                await self._agentic_anthropic(
                    request, response_key, api_key, model, base_url,
                    system_prompt, require_approval,
                )
            elif provider in ("OpenAI", "Kimi", "Custom"):
                url = self._chat_completions_url(provider, base_url)
                if provider == "Kimi" and not base_url:
                    url = "https://api.moonshot.ai/v1/chat/completions"
                await self._agentic_openai(
                    request, response_key, api_key, model, url,
                    system_prompt, require_approval,
                )
            elif provider == "Google":
                await self._chat_google(
                    request, response_key, api_key, model,
                    system_prompt,
                )
            else:
                await self.send_error(
                    request, response_key,
                    f"Unknown provider: {provider}",
                )
        except asyncio.CancelledError:
            logger.info("[echidna] chat request cancelled")
            raise
        except Exception as e:
            logger.exception(f"[echidna] chat error: {e}")
            await self.send_error(request, response_key, str(e))
        finally:
            await self._update_channel_metadata(request)

    # ---- config helpers ----

    def _resolve_api_key(self, provider, config, secrets, base_url):
        key = config.text("api_key", "")
        if not key:
            secret_name = SECRET_KEYS.get(provider, "anthropic_api_key")
            key = secrets.text(secret_name, "")
        if not key and base_url:
            return "not-needed"
        if not key:
            raise RuntimeError(
                "Set the API Key in channel config or as a user secret, "
                "or provide a base_url for a keyless endpoint."
            )
        return key

    def _chat_completions_url(self, provider, base_url):
        if base_url:
            return f"{base_url}/chat/completions"
        if provider == "OpenAI":
            return "https://api.openai.com/v1/chat/completions"
        if provider == "Custom":
            raise RuntimeError("base_url required for Custom provider")
        return ""

    async def _get_operator_id(self, request):
        try:
            search = await SendMythicRPCCallbackSearch(
                MythicRPCCallbackSearchMessage()
            )
            if search.Success and search.Results:
                for cb in search.Results:
                    if cb.OperatorID:
                        return cb.OperatorID
        except Exception:
            pass
        return 0

    def _track_tokens(self, channel_id, usage):
        if not usage:
            return
        get_store().add_tokens(
            channel_id,
            usage.get("input", 0),
            usage.get("output", 0),
        )

    async def _update_channel_metadata(self, request):
        config = ChatConfigView.from_request(request)
        provider = config.text("provider", "Anthropic")
        model = config.text("model") or PROVIDER_DEFAULTS.get(provider, "")
        playbook_name = config.text("playbook", "None")
        approval_mode = config.text("approval_mode", "Enabled")
        pinned = get_store().get_pinned(request.ChannelID)
        usage = get_store().get_tokens(request.ChannelID)
        items = [
            {"key": "provider", "label": "Provider", "value": provider, "order": 0},
            {"key": "model", "label": "Model", "value": model, "order": 1},
            {"key": "playbook", "label": "Playbook", "value": playbook_name if playbook_name != "None" else "—", "order": 2},
            {"key": "callback", "label": "Callback", "value": f"#{pinned}" if pinned else "—", "order": 3},
            {"key": "approval", "label": "Approval", "value": "Off" if approval_mode == "Disabled" else "On", "order": 4},
            {"key": "tokens", "label": "Tokens", "value": "0" if not usage else f"{usage['input']}in / {usage['output']}out", "order": 5},
        ]
        try:
            await self.update_channel_metadata(
                request, {"items": items},
            )
        except Exception:
            pass

    # ---- slash commands ----

    async def _handle_use(self, request, response_key):
        arg = (
            request.SlashCommand.Argument or ""
        ).strip().lstrip("#")
        if not arg or arg == "none":
            get_store().clear_pinned(request.ChannelID)
            await self.send_text(
                request, response_key,
                content="Callback unpinned.",
            )
        else:
            try:
                cb_id = int(arg)
            except ValueError:
                await self.send_error(
                    request, response_key,
                    f"Invalid callback ID: {arg}",
                )
                return
            get_store().set_pinned(request.ChannelID, cb_id)
            await self.send_text(
                request, response_key,
                content=(
                    f"Pinned to callback **#{cb_id}**. "
                    "Commands default to this callback "
                    "unless you specify another. "
                    "Use `/use none` to unpin."
                ),
            )
        await self._update_channel_metadata(request)
        await self.send_complete(
            request, response_key, complete_request=True,
        )

    async def _show_help(self, request, response_key):
        text = (
            f"**Echidna v{self.semver}** — Virtual LLM agent for Mythic C2\n\n"
            "**Slash Commands**\n"
            "- `/help` — this message\n"
            "- `/callbacks` — list active callbacks (direct, no LLM)\n"
            "- `/playbooks` — list available playbooks\n"
            "- `/reset` — clear conversation context (messages stay in UI)\n"
            "- `/use <N>` — pin a default callback (`/use none` to unpin)\n"
            "- `/report` — generate operation report\n"
            "- `/export` — export chat as markdown\n\n"
            "**Chat**\n"
            "Type naturally. Echidna uses LLM tool calling to interact "
            "with Mythic when needed:\n"
            "- *\"list callbacks\"* — queries Mythic for active implants\n"
            "- *\"run whoami on callback #1\"* — executes a command\n"
            "- General offensive security questions work without tools\n\n"
            "**Providers**: Anthropic, OpenAI, Google, Kimi, Custom\n"
            "Configure via channel settings (provider, model, API key, "
            "base URL).\n\n"
            "**Mythic Tools** (used by the LLM automatically):\n"
            "- `list_callbacks` — active implant inventory\n"
            "- `list_commands` — see which commands an implant supports\n"
            "- `execute_command` — run a command on a callback\n"
            "- `process_search` — search collected process data\n"
            "- `task_history` — recall previous tasks and their output\n"
            "- `credential_search` — search stored credentials\n"
            "- `credential_create` — store found creds in Mythic\n"
            "- `create_artifact` — log OPSEC artifacts\n"
            "- `event_log` — write to operation timeline\n"
            "- `tag_task` — tag tasks with ATT&CK techniques"
        )
        await self.send_text(request, response_key, content=text)
        await self.send_complete(
            request, response_key, complete_request=True,
        )

    async def _list_playbooks(self, request, response_key):
        if not PLAYBOOKS:
            await self.send_text(
                request, response_key,
                content="No playbooks found.",
            )
        else:
            lines = [
                f"**Playbooks ({len(PLAYBOOKS)})**\n",
                "Select a playbook in channel settings to activate it.\n",
            ]
            for name, pb in PLAYBOOKS.items():
                desc = pb["description"] or "No description"
                lines.append(f"- **{name}** — {desc}")
            await self.send_text(
                request, response_key,
                content="\n".join(lines),
            )
        await self.send_complete(
            request, response_key, complete_request=True,
        )

    async def _list_callbacks_slash(self, request, response_key):
        operator_id = await self._get_operator_id(request)
        result = await self._tool_list_callbacks({}, request, operator_id)
        try:
            data = json.loads(result)
            if "error" in data:
                await self.send_error(
                    request, response_key, data["error"],
                )
                return
            callbacks = data.get("callbacks", [])
            if not callbacks:
                await self.send_text(
                    request, response_key,
                    content="No active callbacks.",
                )
            else:
                lines = [f"**Active Callbacks ({len(callbacks)})**\n"]
                for cb in callbacks:
                    cid = cb.get("id", "?")
                    host = cb.get("host", "?")
                    user = cb.get("user", "?")
                    ptype = cb.get("payload_type", "?")
                    ip = cb.get("ip", "?")
                    pid = cb.get("pid", "")
                    proc = cb.get("process", "")
                    lines.append(
                        f"- **#{cid}** `{ptype}` — "
                        f"{user}@{host} ({ip}) "
                        f"pid {pid} `{proc}`"
                    )
                await self.send_text(
                    request, response_key,
                    content="\n".join(lines),
                )
        except json.JSONDecodeError:
            await self.send_text(
                request, response_key, content=result,
            )

        await self.send_complete(
            request, response_key, complete_request=True,
        )
