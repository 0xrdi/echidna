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
import aiohttp
import asyncio
import json
import uuid

MAX_TOOL_ROUNDS = 15
TASK_POLL_TIMEOUT = 120

SYSTEM_PROMPT = (
    "You are Echidna, a virtual red team operator embedded in Mythic C2. "
    "You assist with offensive security operations. Be direct and actionable. "
    "Reference MITRE ATT&CK IDs where relevant.\n\n"
    "You have tools to interact with Mythic directly:\n"
    "- list_callbacks: see active implants\n"
    "- execute_command: run a command on an implant callback\n"
    "- credential_create: store found credentials in Mythic\n"
    "- create_artifact: log OPSEC artifacts (files dropped, services created)\n"
    "- event_log: write to the operation timeline\n"
    "- tag_task: tag a task with a MITRE ATT&CK technique ID\n\n"
    "WHEN TO USE TOOLS:\n"
    "- Use tools when the operator asks about targets, callbacks, implants, "
    "hosts, files, processes, users, system state, or capabilities.\n"
    "- The word 'callbacks' ALWAYS means call list_callbacks.\n"
    "- After finding credentials, ALWAYS call credential_create to store them.\n"
    "- After dropping files or creating persistence, call create_artifact.\n"
    "- After significant milestones, call event_log.\n"
    "- After executing commands, call tag_task with the ATT&CK technique.\n"
    "- Do NOT use tools for greetings, general offensive security questions, "
    "or conversation that does not involve live Mythic data.\n\n"
    "CRITICAL RULES:\n"
    "- You have ZERO knowledge of callbacks, targets, or any live "
    "data. Even if prior messages mention them, that data may be stale.\n"
    "- ALWAYS call the tool to get fresh data. NEVER answer from memory.\n"
    "- NEVER fabricate tool output, callback lists, or command "
    "results. If you did not call a tool in THIS response, you do not have "
    "the data.\n"
    "- If a tool fails, say so. Do not fill in with guessed data.\n"
    "- Run commands one at a time and report only confirmed results.\n"
    "- NEVER claim you called a tool when you did not.\n"
    "- NEVER print tool call JSON/parameters as text. If you need to run a "
    "command, USE the execute_command tool. Do not show the parameters as a "
    "code block — that does nothing. Actually call the tool.\n"
    "- When the operator says 'do it', 'run it', 'go ahead', or similar, "
    "that means call the appropriate tool NOW, not describe what you would do."
)

PROVIDER_DEFAULTS = {
    "Anthropic": "claude-sonnet-4-20250514",
    "OpenAI": "gpt-4o",
    "Google": "gemini-2.5-flash",
    "Kimi": "kimi-k3",
}

SECRET_KEYS = {
    "Anthropic": "anthropic_api_key",
    "OpenAI": "openai_api_key",
    "Google": "google_api_key",
    "Kimi": "kimi_api_key",
    "Custom": "openai_api_key",
}

# Tool definitions — OpenAI function-calling format
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_callbacks",
            "description": (
                "List active Mythic callbacks (implants) in the current "
                "operation. Returns callback ID, host, user, payload type, "
                "IP, OS, and process info."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": (
                "Execute a command on a Mythic callback and return the "
                "output. Use the callback's payload type commands "
                "(e.g. 'shell' for Poseidon/Apollo to run shell commands, "
                "'ls' to list files, 'download' to download a file). "
                "Waits for the task to complete and returns the output."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "callback_id": {
                        "type": "integer",
                        "description": "Callback display ID (the # number)",
                    },
                    "command": {
                        "type": "string",
                        "description": "Command name (e.g. shell, ls, pwd, whoami, download)",
                    },
                    "params": {
                        "type": "string",
                        "description": "Command parameters (e.g. 'hostname' for shell)",
                    },
                },
                "required": ["callback_id", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "credential_create",
            "description": (
                "Store a credential in the Mythic credential store. Use "
                "this whenever you discover credentials during operations "
                "(passwords, hashes, tokens, API keys, SSH keys). The "
                "credential is stored in the current operation and visible "
                "to all operators."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "credential_type": {
                        "type": "string",
                        "description": (
                            "Type: plaintext, hash, ticket, certificate, "
                            "token, key, or other"
                        ),
                    },
                    "account": {
                        "type": "string",
                        "description": "Username or account name",
                    },
                    "credential": {
                        "type": "string",
                        "description": "The credential value (password, hash, etc.)",
                    },
                    "realm": {
                        "type": "string",
                        "description": "Domain, host, or service (e.g. CORP.LOCAL, ssh://10.0.0.1)",
                    },
                    "comment": {
                        "type": "string",
                        "description": "How/where the credential was found",
                    },
                },
                "required": ["credential_type", "account", "credential"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_artifact",
            "description": (
                "Log an OPSEC artifact in Mythic. Use this to track files "
                "dropped, registry keys modified, services created, or any "
                "other forensic footprint left on a target. Artifacts appear "
                "in the Mythic artifacts view for OPSEC review and cleanup."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact": {
                        "type": "string",
                        "description": "What was created/modified (e.g. /tmp/.payload, HKLM\\...\\Run\\backdoor)",
                    },
                    "artifact_type": {
                        "type": "string",
                        "description": "Type: File, Registry, Service, Scheduled Task, User Account, Process, Network, Other",
                    },
                    "host": {
                        "type": "string",
                        "description": "Host where the artifact exists",
                    },
                    "needs_cleanup": {
                        "type": "boolean",
                        "description": "Whether this artifact should be cleaned up before exiting",
                    },
                },
                "required": ["artifact", "artifact_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "event_log",
            "description": (
                "Write an entry to the Mythic operation event log. Use "
                "this to record significant events: access gained, "
                "credentials found, persistence installed, lateral "
                "movement completed, or any milestone worth logging "
                "in the operation timeline."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Event description for the operation log",
                    },
                    "level": {
                        "type": "string",
                        "description": "Level: info, warning. Default info.",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tag_task",
            "description": (
                "Tag a Mythic task with a MITRE ATT&CK technique ID. "
                "Use this after executing a command to tag the task with "
                "the relevant technique. Tags are searchable in Mythic "
                "and appear in reporting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "Mythic task ID to tag (from execute_command output)",
                    },
                    "technique_id": {
                        "type": "string",
                        "description": "MITRE ATT&CK technique ID (e.g. T1548.003, T1059.004)",
                    },
                },
                "required": ["task_id", "technique_id"],
            },
        },
    },
]

# Same tools in Anthropic format
ANTHROPIC_TOOLS = [
    {
        "name": t["function"]["name"],
        "description": t["function"]["description"],
        "input_schema": t["function"]["parameters"],
    }
    for t in OPENAI_TOOLS
]


def _anthropic_root(base_url: str) -> str:
    root = (base_url or "").rstrip("/")
    return root[:-3].rstrip("/") if root.endswith("/v1") else root


class EchidnaChat(Chat):
    name = "echidna"
    description = "Virtual LLM agent for red team operations"
    semver = "1.1.0"
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
                ],
            ),
        ),
    ]

    async def chat(self, request: ChatRequest):
        response_key = str(uuid.uuid4())

        if request.SlashCommand:
            if request.SlashCommand.Name == "help":
                await self._show_help(request, response_key)
                return
            if request.SlashCommand.Name == "callbacks":
                await self._list_callbacks_slash(request, response_key)
                return

        config = ChatConfigView.from_request(request)
        secrets = ChatSecretView.from_request(request)
        provider = config.text("provider", "Anthropic")
        model = config.text("model") or PROVIDER_DEFAULTS.get(provider, "")
        base_url = config.text("base_url", "").rstrip("/")

        try:
            api_key = self._resolve_api_key(provider, config, secrets, base_url)
        except RuntimeError as e:
            await self.send_error(request, response_key, str(e))
            return

        tool_count = len(OPENAI_TOOLS) if provider != "Google" else 0
        items = [
            {"key": "provider", "label": "Provider", "value": provider, "order": 0},
            {"key": "model", "label": "Model", "value": model, "order": 1},
            {"key": "mythic_tools", "label": "Mythic Tools", "value": tool_count, "order": 2},
            {"key": "max_rounds", "label": "Max Rounds", "value": MAX_TOOL_ROUNDS, "order": 3},
        ]
        try:
            await self.update_channel_metadata(
                request, {"items": items},
            )
        except Exception:
            pass

        try:
            if provider == "Anthropic":
                await self._agentic_anthropic(
                    request, response_key, api_key, model, base_url,
                )
            elif provider in ("OpenAI", "Kimi", "Custom"):
                url = self._chat_completions_url(provider, base_url)
                if provider == "Kimi" and not base_url:
                    url = "https://api.moonshot.ai/v1/chat/completions"
                await self._agentic_openai(
                    request, response_key, api_key, model, url,
                )
            elif provider == "Google":
                await self._chat_google(
                    request, response_key, api_key, model,
                )
            else:
                await self.send_error(
                    request, response_key, f"Unknown provider: {provider}",
                )
        except asyncio.CancelledError:
            logger.info("[echidna] chat request cancelled")
            raise
        except Exception as e:
            logger.exception(f"[echidna] chat error: {e}")
            await self.send_error(request, response_key, str(e))

    # ---- agentic loops ----

    async def _stream_openai(self, session, url, headers, payload):
        """Stream an OpenAI-compatible response, returning accumulated message.

        Yields control on each SSE chunk so asyncio.CancelledError can
        propagate promptly. Returns (content, tool_calls, finish_reason).
        """
        payload["stream"] = True
        content_parts = []
        tool_calls_by_idx = {}
        finish_reason = ""

        async with session.post(
            url, headers=headers, json=payload,
            timeout=aiohttp.ClientTimeout(total=180, sock_read=60),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"LLM API {resp.status}: {text[:500]}")

            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                delta = chunk.get("choices", [{}])[0].get("delta", {})
                fr = chunk.get("choices", [{}])[0].get("finish_reason")
                if fr:
                    finish_reason = fr

                if delta.get("content"):
                    content_parts.append(delta["content"])

                for tc_delta in delta.get("tool_calls", []):
                    idx = tc_delta.get("index", 0)
                    if idx not in tool_calls_by_idx:
                        tool_calls_by_idx[idx] = {
                            "id": tc_delta.get("id", ""),
                            "function": {"name": "", "arguments": ""},
                        }
                    entry = tool_calls_by_idx[idx]
                    if tc_delta.get("id"):
                        entry["id"] = tc_delta["id"]
                    fn = tc_delta.get("function", {})
                    if fn.get("name"):
                        entry["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        entry["function"]["arguments"] += fn["arguments"]

        content = "".join(content_parts)
        tool_calls = [tool_calls_by_idx[i] for i in sorted(tool_calls_by_idx)]
        return content, tool_calls, finish_reason

    async def _agentic_openai(self, request, response_key, api_key, model,
                              url):
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        messages = self.build_chat_messages(
            request, system_prompt=SYSTEM_PROMPT,
        )
        messages = [
            m for m in messages
            if m.get("content") or m["role"] == "system"
        ]
        operator_id = await self._get_operator_id(request)
        tool_idx = 0

        async with aiohttp.ClientSession() as session:
            for round_num in range(MAX_TOOL_ROUNDS):
                logger.info(
                    f"[echidna] round {round_num}, "
                    f"{len(messages)} messages"
                )
                payload = {
                    "model": model,
                    "messages": messages,
                    "tools": OPENAI_TOOLS,
                }

                content, tool_calls, finish = await self._stream_openai(
                    session, url, headers, payload,
                )
                logger.info(
                    f"[echidna] round {round_num} done: "
                    f"finish={finish}, tools={len(tool_calls)}, "
                    f"content_len={len(content)}"
                )

                if not tool_calls:
                    await self.send_text(
                        request, response_key, content=content,
                    )
                    await self.send_complete(
                        request, response_key, complete_request=True,
                    )
                    return

                assistant_msg = {
                    "role": "assistant",
                    "content": content or " ",
                    "tool_calls": [
                        {"id": tc["id"], "type": "function",
                         "function": tc["function"]}
                        for tc in tool_calls
                    ],
                }
                messages.append(assistant_msg)

                for tc in tool_calls:
                    func_name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        args = {}

                    tool_key = f"tool:{tool_idx}"
                    tool_idx += 1
                    await self._send_tool_card(
                        request, tool_key, func_name, args, "running",
                    )

                    result = await self._execute_tool(
                        func_name, args, request, operator_id,
                    )
                    logger.info(
                        f"[echidna] tool {func_name} result: "
                        f"{len(result)} bytes"
                    )

                    await self._send_tool_card(
                        request, tool_key, func_name, args, "complete",
                        result=result,
                    )

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })

        await self.send_text(
            request, response_key,
            content="Reached maximum tool rounds.",
        )
        await self.send_complete(
            request, response_key, complete_request=True,
        )

    async def _stream_anthropic(self, session, url, headers, payload):
        """Stream an Anthropic response, returning accumulated content blocks.

        Returns (text_parts, tool_uses) where tool_uses are dicts with
        id, name, and input (parsed JSON).
        """
        payload["stream"] = True
        text_parts = []
        tool_uses = []
        current_block = None

        async with session.post(
            url, headers=headers, json=payload,
            timeout=aiohttp.ClientTimeout(total=180, sock_read=60),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(
                    f"Anthropic API {resp.status}: {text[:500]}"
                )

            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line.startswith("event: "):
                    event_type = line[7:]
                    if event_type == "message_stop":
                        break
                    continue
                if not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue

                dtype = data.get("type", "")

                if dtype == "content_block_start":
                    block = data.get("content_block", {})
                    if block.get("type") == "text":
                        current_block = {"type": "text", "text": ""}
                    elif block.get("type") == "tool_use":
                        current_block = {
                            "type": "tool_use",
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "input_json": "",
                        }

                elif dtype == "content_block_delta":
                    delta = data.get("delta", {})
                    if current_block and current_block["type"] == "text":
                        current_block["text"] += delta.get("text", "")
                    elif current_block and current_block["type"] == "tool_use":
                        current_block["input_json"] += delta.get(
                            "partial_json", ""
                        )

                elif dtype == "content_block_stop":
                    if current_block:
                        if current_block["type"] == "text":
                            text_parts.append(current_block["text"])
                        elif current_block["type"] == "tool_use":
                            try:
                                inp = json.loads(
                                    current_block["input_json"] or "{}"
                                )
                            except json.JSONDecodeError:
                                inp = {}
                            tool_uses.append({
                                "type": "tool_use",
                                "id": current_block["id"],
                                "name": current_block["name"],
                                "input": inp,
                            })
                        current_block = None

        return text_parts, tool_uses

    async def _agentic_anthropic(self, request, response_key, api_key, model,
                                 base_url):
        url = (
            f"{_anthropic_root(base_url)}/v1/messages"
            if base_url
            else "https://api.anthropic.com/v1/messages"
        )
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        raw_messages = self.build_chat_messages(
            request, system_prompt=SYSTEM_PROMPT,
        )
        system_text = ""
        messages = []
        for m in raw_messages:
            if m["role"] == "system":
                system_text = m["content"]
            elif m.get("content"):
                messages.append(m)

        operator_id = await self._get_operator_id(request)
        tool_idx = 0

        async with aiohttp.ClientSession() as session:
            for round_num in range(MAX_TOOL_ROUNDS):
                logger.info(
                    f"[echidna] anthropic round {round_num}, "
                    f"{len(messages)} messages"
                )
                payload = {
                    "model": model,
                    "max_tokens": 8192,
                    "messages": messages,
                    "tools": ANTHROPIC_TOOLS,
                }
                if system_text:
                    payload["system"] = system_text

                text_parts, tool_uses = await self._stream_anthropic(
                    session, url, headers, payload,
                )
                logger.info(
                    f"[echidna] anthropic round {round_num} done: "
                    f"text_blocks={len(text_parts)}, "
                    f"tool_uses={len(tool_uses)}"
                )

                if not tool_uses:
                    full_text = "\n".join(text_parts)
                    await self.send_text(
                        request, response_key, content=full_text,
                    )
                    await self.send_complete(
                        request, response_key, complete_request=True,
                    )
                    return

                content_blocks = []
                for tp in text_parts:
                    content_blocks.append({"type": "text", "text": tp})
                for tu in tool_uses:
                    content_blocks.append(tu)
                messages.append({
                    "role": "assistant", "content": content_blocks,
                })

                if text_parts:
                    thinking_key = f"thinking:{tool_idx}"
                    await self.send_text(
                        request, thinking_key,
                        content="\n".join(text_parts),
                    )
                    await self.send_complete(request, thinking_key)

                tool_results = []
                for tu in tool_uses:
                    func_name = tu["name"]
                    args = tu.get("input", {})
                    tool_use_id = tu["id"]

                    tool_key = f"tool:{tool_idx}"
                    tool_idx += 1
                    await self._send_tool_card(
                        request, tool_key, func_name, args, "running",
                    )

                    result = await self._execute_tool(
                        func_name, args, request, operator_id,
                    )

                    await self._send_tool_card(
                        request, tool_key, func_name, args, "complete",
                        result=result,
                    )

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result,
                    })

                messages.append({"role": "user", "content": tool_results})

        await self.send_text(
            request, response_key,
            content="Reached maximum tool rounds.",
        )
        await self.send_complete(
            request, response_key, complete_request=True,
        )

    async def _chat_google(self, request, response_key, api_key, model):
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        messages = self.build_chat_messages(
            request, system_prompt=SYSTEM_PROMPT,
        )
        contents = []
        system_instruction = ""
        for m in messages:
            if m["role"] == "system":
                system_instruction = m["content"]
            else:
                role = "model" if m["role"] == "assistant" else "user"
                contents.append(
                    {"role": role, "parts": [{"text": m["content"]}]},
                )
        payload = {"contents": contents}
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}],
            }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Google API {resp.status}: {text[:500]}")
                data = await resp.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        await self.send_text(request, response_key, content=text)
        await self.send_complete(
            request, response_key, complete_request=True,
        )

    # ---- tool execution ----

    async def _execute_tool(self, name, args, request, operator_id):
        if name == "list_callbacks":
            return await self._tool_list_callbacks(request, operator_id)
        if name == "execute_command":
            return await self._tool_execute_command(
                args, request, operator_id,
            )
        if name == "credential_create":
            return await self._tool_credential_create(args, request)
        if name == "create_artifact":
            return await self._tool_create_artifact(args, request)
        if name == "event_log":
            return await self._tool_event_log(args, request)
        if name == "tag_task":
            return await self._tool_tag_task(args, request)
        return json.dumps({"error": f"Unknown tool: {name}"})

    async def _tool_list_callbacks(self, request, operator_id):
        try:
            search = await SendMythicRPCCallbackSearch(
                MythicRPCCallbackSearchMessage()
            )
            if not search.Success:
                return json.dumps({"error": f"Search failed: {search.Error}"})

            callbacks = []
            for cb in search.Results:
                if not cb.Active:
                    continue
                callbacks.append({
                    "id": cb.DisplayID,
                    "payload_type": cb.PayloadType,
                    "host": cb.Host,
                    "user": cb.User,
                    "ip": cb.Ip,
                    "os": cb.Os,
                    "pid": cb.PID,
                    "process": cb.ProcessName,
                    "integrity": cb.IntegrityLevel,
                    "description": cb.Description,
                })
            return json.dumps({"callbacks": callbacks})
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _tool_execute_command(self, args, request, operator_id):
        display_id = args.get("callback_id")
        command = args.get("command", "")
        params = args.get("params", "")

        if not display_id or not command:
            return json.dumps({
                "error": "callback_id and command are required",
            })

        try:
            cb_search = await SendMythicRPCCallbackSearch(
                MythicRPCCallbackSearchMessage(
                    SearchCallbackDisplayID=int(display_id),
                )
            )
            if not cb_search.Success or not cb_search.Results:
                return json.dumps({
                    "error": f"Callback #{display_id} not found",
                })
            agent_callback_id = cb_search.Results[0].AgentCallbackID
            if not operator_id:
                operator_id = cb_search.Results[0].OperatorID

            task_resp = await SendMythicRPCTaskCreate(
                MythicRPCTaskCreateMessage(
                    AgentCallbackID=agent_callback_id,
                    CommandName=command,
                    Params=str(params),
                    OperatorID=operator_id,
                )
            )
            if not task_resp.Success:
                return json.dumps({
                    "error": f"Task creation failed: {task_resp.Error}",
                })

            task_id = task_resp.TaskID
            task_display_id = task_resp.TaskDisplayID or task_id

            for _ in range(TASK_POLL_TIMEOUT // 2):
                await asyncio.sleep(2)
                search = await SendMythicRPCTaskSearch(
                    MythicRPCTaskSearchMessage(
                        TaskID=task_id,
                        SearchTaskID=task_id,
                    )
                )
                if (search.Success and search.Tasks
                        and search.Tasks[0].Completed):
                    break
            else:
                return json.dumps({
                    "task_id": task_display_id,
                    "status": "timeout",
                    "error": "Task did not complete within timeout",
                })

            resp_search = await SendMythicRPCResponseSearch(
                MythicRPCResponseSearchMessage(TaskID=task_id)
            )
            output_parts = []
            if resp_search.Success and resp_search.Responses:
                for r in resp_search.Responses:
                    if hasattr(r, "Response") and r.Response:
                        output_parts.append(str(r.Response))

            return json.dumps({
                "task_id": task_display_id,
                "status": "completed",
                "output": "\n".join(output_parts),
            })

        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _get_any_task_id(self):
        """Find a valid TaskID from any callback in the operation."""
        try:
            cb_search = await SendMythicRPCCallbackSearch(
                MythicRPCCallbackSearchMessage()
            )
            if cb_search.Success and cb_search.Results:
                for cb in cb_search.Results:
                    task_search = await SendMythicRPCTaskSearch(
                        MythicRPCTaskSearchMessage(
                            TaskID=0,
                            SearchCallbackID=cb.DisplayID,
                        )
                    )
                    if task_search.Success and task_search.Tasks:
                        return task_search.Tasks[0].TaskID
        except Exception:
            pass
        return 0

    async def _tool_credential_create(self, args, request):
        try:
            from mythic_container.MythicGoRPC.send_mythic_rpc_credential_create import (
                MythicRPCCredentialData,
            )
            task_id = await self._get_any_task_id()
            cred = MythicRPCCredentialData(
                CredentialType=args.get("credential_type", "plaintext"),
                Account=args.get("account", ""),
                Credential=args.get("credential", ""),
                Realm=args.get("realm", ""),
                Comment=args.get("comment", ""),
            )
            resp = await SendMythicRPCCredentialCreate(
                MythicRPCCredentialCreateMessage(
                    TaskID=task_id,
                    Credentials=[cred],
                )
            )
            if resp.Success:
                return json.dumps({
                    "status": "stored",
                    "account": args.get("account", ""),
                    "type": args.get("credential_type", ""),
                    "realm": args.get("realm", ""),
                })
            return json.dumps({"error": resp.Error})
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _tool_create_artifact(self, args, request):
        try:
            task_id = await self._get_any_task_id()
            resp = await SendMythicRPCArtifactCreate(
                MythicRPCArtifactCreateMessage(
                    TaskID=task_id,
                    ArtifactMessage=args.get("artifact", ""),
                    BaseArtifactType=args.get("artifact_type", "Other"),
                    ArtifactHost=args.get("host", ""),
                    NeedsCleanup=args.get("needs_cleanup", False),
                )
            )
            if resp.Success:
                return json.dumps({
                    "status": "logged",
                    "artifact": args.get("artifact", ""),
                    "type": args.get("artifact_type", ""),
                    "needs_cleanup": args.get("needs_cleanup", False),
                })
            return json.dumps({"error": resp.Error})
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _tool_event_log(self, args, request):
        try:
            level = args.get("level", "info")
            resp = await SendMythicRPCOperationEventLogCreate(
                MythicRPCOperationEventLogCreateMessage(
                    OperationID=request.OperationID,
                    Message=f"[Echidna] {args.get('message', '')}",
                    MessageLevel=level,
                    Warning=(level == "warning"),
                )
            )
            if resp.Success:
                return json.dumps({
                    "status": "logged",
                    "message": args.get("message", ""),
                })
            return json.dumps({"error": resp.Error})
        except Exception as e:
            return json.dumps({"error": str(e)})

    _TECHNIQUE_RE = __import__("re").compile(r"^T\d{4}(\.\d{3})?$")

    async def _tool_tag_task(self, args, request):
        try:
            task_id = args.get("task_id", 0)
            technique_id = args.get("technique_id", "")
            if not task_id or not technique_id:
                return json.dumps({
                    "error": "task_id and technique_id are required",
                })
            if not self._TECHNIQUE_RE.match(technique_id):
                return json.dumps({
                    "error": (
                        f"Invalid technique_id format: {technique_id}. "
                        "Expected Tnnnn or Tnnnn.nnn"
                    ),
                })
            token_resp = await SendMythicRPCAPITokenCreate(
                MythicRPCAPITokenCreateMessage(
                    OperationID=request.OperationID,
                    APITokenID=request.APITokenID,
                    ChatChannelID=request.ChannelID,
                )
            )
            if not token_resp.Success:
                return json.dumps({
                    "error": f"API token: {token_resp.Error}",
                })
            gql = (
                "mutation { addAttackToTask("
                f't_num: "{technique_id}", '
                f"task_display_id: {int(task_id)}"
                ") { status error } }"
            )
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://mythic_nginx:7443/graphql/",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token_resp.APIToken}",
                    },
                    json={"query": gql},
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
            result = data.get("data", {}).get("addAttackToTask", {})
            if result.get("status") == "success":
                return json.dumps({
                    "status": "tagged",
                    "task_id": task_id,
                    "technique": technique_id,
                })
            err = result.get("error") or data.get("errors", "unknown")
            return json.dumps({"error": str(err)})
        except Exception as e:
            return json.dumps({"error": str(e)})


    # ---- tool use cards ----

    TOOL_ICONS = {
        "list_callbacks": "T",
        "execute_command": "T",
        "credential_create": "T",
        "create_artifact": "T",
        "event_log": "T",
        "tag_task": "T",
    }

    async def _send_tool_card(self, request, response_key, tool_name, args,
                              status, result=None):
        delegation_id = f"tool_{response_key}"
        title = f"Mythic tool: {tool_name}"
        icon = self.TOOL_ICONS.get(tool_name, tool_name[:2].upper())
        args_str = json.dumps(args, indent=2) if args else ""

        if status == "running":
            await self.send_subagent_status(
                request,
                title=title,
                prompt=title,
                delegation_id=delegation_id,
                icon=icon,
                status="running",
                response_key=response_key,
            )
            if args_str and args_str != "{}":
                await self.send_response(
                    request,
                    response_key=f"{response_key}:input",
                    content=f"**Input:**\n```json\n{args_str}\n```",
                    status="complete",
                    complete=True,
                    metadata={"delegation_id": delegation_id},
                )
        elif status == "complete":
            output = ""
            if result:
                try:
                    parsed = json.loads(result)
                    output = parsed.get("output", "")
                    if not output:
                        output = json.dumps(parsed, indent=2)
                except (json.JSONDecodeError, AttributeError):
                    output = result
            preview = output[:2000]
            if len(output) > 2000:
                preview += "\n..."
            await self.send_response(
                request,
                response_key=f"{response_key}:output",
                content=f"**Output:**\n```\n{preview}\n```",
                status="complete",
                complete=True,
                metadata={"delegation_id": delegation_id},
            )
            await self.send_subagent_status(
                request,
                title=title,
                prompt=title,
                delegation_id=delegation_id,
                icon=icon,
                status="finished",
                content=preview[:200] if preview else "Done",
                response_key=response_key,
                complete=True,
            )

    # ---- helpers ----

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

    # ---- slash commands ----

    async def _show_help(self, request, response_key):
        text = (
            f"**Echidna v{self.semver}** — Virtual LLM agent for Mythic C2\n\n"
            "**Slash Commands**\n"
            "- `/help` — this message\n"
            "- `/callbacks` — list active callbacks (direct, no LLM)\n\n"
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
            "- `execute_command` — run a command on a callback\n"
            "- `credential_create` — store found creds in Mythic\n"
            "- `create_artifact` — log OPSEC artifacts\n"
            "- `event_log` — write to operation timeline\n"
            "- `tag_task` — tag tasks with ATT&CK techniques"
        )
        await self.send_text(request, response_key, content=text)
        await self.send_complete(
            request, response_key, complete_request=True,
        )

    async def _list_callbacks_slash(self, request, response_key):
        operator_id = await self._get_operator_id(request)
        result = await self._tool_list_callbacks(request, operator_id)
        try:
            data = json.loads(result)
            if "error" in data:
                await self.send_error(request, response_key, data["error"])
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
            await self.send_text(request, response_key, content=result)

        await self.send_complete(
            request, response_key, complete_request=True,
        )

