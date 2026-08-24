import aiohttp
import json
from mythic_container.logging import logger
from .constants import MAX_TOOL_ROUNDS
from .tools import OPENAI_TOOLS, ANTHROPIC_TOOLS
from .http import retry_post


def _anthropic_root(base_url):
    root = (base_url or "").rstrip("/")
    return root[:-3].rstrip("/") if root.endswith("/v1") else root


class ProviderMixin:

    TOOL_ICONS = {
        "list_callbacks": "T",
        "execute_command": "T",
        "credential_create": "T",
        "create_artifact": "T",
        "event_log": "T",
        "tag_task": "T",
    }

    # ---- shared helpers ----

    def _find_approval_needed(self, tool_calls, require_approval):
        if not require_approval:
            return None
        for tc in tool_calls:
            name = tc.get("name") or tc.get("function", {}).get("name", "")
            if name == "execute_command":
                return tc
        return None

    async def _request_command_approval(self, request, args):
        cmd = args.get("command", "")
        params = args.get("params", "")
        cmd_str = f"{cmd} {params}".strip()
        cb_id = args.get("callback_id", "?")
        await self.send_approval_request(
            request,
            title="Command Execution",
            prompt=f"Execute `{cmd_str}` on callback #{cb_id}?",
            description=(
                "The LLM wants to run a command on a callback. "
                "Approve or deny."
            ),
            data={
                "type": "execute_command_approval",
                "callback_id": args.get("callback_id"),
                "command": cmd,
                "params": params,
            },
        )

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

    # ---- OpenAI streaming ----

    async def _stream_openai(self, session, url, headers, payload):
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        content_parts = []
        tool_calls_by_idx = {}
        finish_reason = ""
        usage = {"input": 0, "output": 0}

        resp = await retry_post(session, url, headers, payload)
        async with resp:
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

                choices = chunk.get("choices", [])
                delta = choices[0].get("delta", {}) if choices else {}
                fr = choices[0].get("finish_reason") if choices else None
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

                u = chunk.get("usage")
                if u:
                    usage["input"] += u.get("prompt_tokens", 0)
                    usage["output"] += u.get("completion_tokens", 0)

        content = "".join(content_parts)
        tool_calls = [tool_calls_by_idx[i] for i in sorted(tool_calls_by_idx)]
        return content, tool_calls, finish_reason, usage

    # ---- OpenAI agentic loop ----

    async def _agentic_openai(self, request, response_key, api_key, model,
                              url, system_prompt, require_approval=False):
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        messages = self.build_chat_messages(
            request, system_prompt=system_prompt,
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

                content, tool_calls, finish, usage = (
                    await self._stream_openai(session, url, headers, payload)
                )
                self._track_tokens(request.ChannelID, usage)
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

                needs_approval = self._find_approval_needed(
                    tool_calls, require_approval,
                )
                if needs_approval:
                    if content:
                        await self.send_text(
                            request, response_key, content=content,
                        )
                        await self.send_complete(request, response_key)
                    try:
                        args = json.loads(
                            needs_approval["function"]["arguments"],
                        )
                    except json.JSONDecodeError:
                        args = {}
                    await self._request_command_approval(request, args)
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

    # ---- Anthropic streaming ----

    async def _stream_anthropic(self, session, url, headers, payload):
        payload["stream"] = True
        text_parts = []
        tool_uses = []
        current_block = None
        usage = {"input": 0, "output": 0}

        resp = await retry_post(session, url, headers, payload)
        async with resp:
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

                elif dtype == "message_start":
                    u = data.get("message", {}).get("usage", {})
                    usage["input"] += u.get("input_tokens", 0)
                    usage["output"] += u.get("output_tokens", 0)
                elif dtype == "message_delta":
                    u = data.get("usage", {})
                    usage["output"] += u.get("output_tokens", 0)

        return text_parts, tool_uses, usage

    # ---- Anthropic agentic loop ----

    async def _agentic_anthropic(self, request, response_key, api_key, model,
                                 base_url, system_prompt,
                                 require_approval=False):
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
            request, system_prompt=system_prompt,
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

                text_parts, tool_uses, usage = (
                    await self._stream_anthropic(
                        session, url, headers, payload,
                    )
                )
                self._track_tokens(request.ChannelID, usage)
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

                needs_approval = self._find_approval_needed(
                    tool_uses, require_approval,
                )
                if needs_approval:
                    if text_parts:
                        thinking_key = f"thinking:{tool_idx}"
                        await self.send_text(
                            request, thinking_key,
                            content="\n".join(text_parts),
                        )
                        await self.send_complete(request, thinking_key)
                    args = needs_approval.get("input", {})
                    await self._request_command_approval(request, args)
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

    # ---- Google ----

    async def _chat_google(self, request, response_key, api_key, model,
                           system_prompt):
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        messages = self.build_chat_messages(
            request, system_prompt=system_prompt,
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
            resp = await retry_post(
                session, url,
                {"Content-Type": "application/json"},
                payload, timeout=60, sock_read=60,
            )
            async with resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(
                        f"Google API {resp.status}: {text[:500]}"
                    )
                data = await resp.json()

        usage_meta = data.get("usageMetadata", {})
        if usage_meta:
            self._track_tokens(request.ChannelID, {
                "input": usage_meta.get("promptTokenCount", 0),
                "output": usage_meta.get("candidatesTokenCount", 0),
            })

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        await self.send_text(request, response_key, content=text)
        await self.send_complete(
            request, response_key, complete_request=True,
        )
