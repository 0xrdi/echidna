# Echidna

<p align="center">
  <img src="echidna/mythic/agent_functions/echidna.svg" alt="Echidna Logo" width="150" height="150">
</p>

<p align="center">
  <strong>A virtual LLM agent for Mythic C2</strong>
</p>

---

> **Authorized Use Only** — Echidna is provided for authorized security testing, research, and educational purposes only. You may only use it against systems you own or have explicit written authorization to test. The authors assume no liability for misuse or unauthorized activity. Unauthorized access to computer systems is illegal under laws including the CFAA, Computer Misuse Act, and equivalent legislation in your jurisdiction.

---

Echidna is a Chat container for [Mythic C2](https://github.com/its-a-feature/Mythic) (v4.0+) that turns LLMs into red team operators. It provides an agentic chat interface with native Mythic tool use — the LLM can execute commands on implants, store credentials, log OPSEC artifacts, write to the operation timeline, and tag tasks with MITRE ATT&CK techniques. No binary, no payload. The entire agent is a single Chat container that registers with Mythic and operates through its chat framework.

## Requirements

- **Mythic C2 v4.0+** with Chat container support
- **mythic-container** Python library `>=0.7.0rc9`
- An API key for at least one supported LLM provider (or a self-hosted endpoint)

## Installation

```bash
sudo ./mythic-cli install github https://github.com/0xrdi/echidna.git
```

After installation, Echidna appears in Mythic's Chat sidebar. Create a new channel, select the `echidna` model, and configure your provider and API key in the channel settings.

## Providers

| Provider | Tool Use | Default Model | Config |
|----------|:--------:|---------------|--------|
| Anthropic | Yes | `claude-sonnet-4-20250514` | API key or `anthropic_base_url` |
| OpenAI | Yes | `gpt-4o` | API key or `openai_base_url` |
| Kimi | Yes | `kimi-k3` | API key |
| Google | Coming soon | `gemini-2.5-flash` | API key |
| Custom | Yes | auto-detected | `openai_base_url` (required) |

Each provider needs **either** an API key **or** a base URL, not both:

- **API key** — uses the vendor's hosted API directly.
- **Base URL** — points at a self-hosted OpenAI-compatible endpoint (LiteLLM, one-api, new-api, vLLM, Ollama). No API key required. The URL must include `/v1` (e.g. `http://10.0.0.5:4000/v1`).

Google tool use support is coming soon — currently chat-only (no function calling).

### API Key Resolution

Echidna resolves the API key in this order:

1. **Channel config** — the `api_key` field in the channel settings
2. **User secrets** — Mythic's per-user secret store (`anthropic_api_key`, `openai_api_key`, `google_api_key`, `kimi_api_key`)
3. **Keyless** — if a `base_url` is set and no key is found, requests are sent without authentication

### AI Chat API Token

Mythic v4 supports scoped API tokens for Chat containers. Configure the channel's AI Chat API Token to allow Echidna to mint scoped Mythic API tokens at runtime (used by `tag_task` to call the GraphQL API). The token must include `apitoken.write` and `chat-ai.write` permissions.

## Chat Interface

Echidna operates through Mythic's Chat container framework. Configure a channel with your provider, model, and API key, then chat naturally:

- *"list callbacks"* — queries Mythic for active implants
- *"run whoami on callback #1"* — executes a command via the implant
- *"check /etc/shadow on callback #3"* — reads and analyzes command output
- *"what lateral movement options do I have?"* — general offensive security discussion (no tools needed)

The LLM decides when to call tools based on the conversation. It will not fabricate data — if it hasn't called a tool, it says so. Commands are executed one at a time, and only confirmed output is reported.

### Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show version, available tools, and usage examples |
| `/callbacks` | List active callbacks directly (bypasses the LLM) |

## Mythic Tools

Six tools are registered with the LLM as function definitions. The LLM calls them automatically based on conversation context. Each tool call is rendered as a collapsible card in the chat with input parameters and output.

| Tool | Description |
|------|-------------|
| `list_callbacks` | List active implants (callback ID, host, user, payload type, IP, OS, process) |
| `execute_command` | Run a command on a callback by display ID. Waits for completion (120s timeout) and returns output. Returns the `task_display_id` for use with `tag_task`. |
| `credential_create` | Store a credential in Mythic (plaintext, hash, ticket, certificate, token, key). Requires `account` and `credential` fields. |
| `create_artifact` | Log an OPSEC artifact (file, registry key, service, scheduled task, etc.) with optional cleanup flag. |
| `event_log` | Write an entry to the operation event log. Supports `info` and `warning` levels. |
| `tag_task` | Tag a completed task with a MITRE ATT&CK technique ID (e.g. `T1059.004`). Uses the native `addAttackToTask` GraphQL mutation — tagged tasks appear on Mythic's MITRE ATT&CK dashboard. |

### Agentic Loop

The LLM runs in an agentic loop with up to **15 tool rounds** per message. In each round:

1. The full conversation (including prior tool results) is sent to the LLM
2. If the LLM returns tool calls, each tool is executed via Mythic RPC
3. Tool results are appended to the conversation and the next round begins
4. If the LLM returns text without tool calls, the response is sent to the operator and the loop ends

Both Anthropic and OpenAI providers use **streaming SSE** — responses appear incrementally. Google uses a single request/response cycle (no streaming, no tools).

### ATT&CK Tagging

After executing a command, the LLM automatically tags the task with the relevant ATT&CK technique. `tag_task` works by:

1. Minting a scoped API token via `SendMythicRPCAPITokenCreate` (uses the channel's configured API token — no hardcoded credentials)
2. Calling the `addAttackToTask(t_num, task_display_id)` GraphQL mutation on `mythic_nginx:7443`
3. The technique ID is validated against the pattern `Tnnnn` or `Tnnnn.nnn` before the request

Tagged tasks appear on Mythic's MITRE ATT&CK dashboard, providing automatic coverage mapping during an operation.

## Architecture

```
echidna/
├── main.py                              # Entry point
├── Dockerfile                           # Container image
├── config.json                          # Mythic container config
└── echidna/mythic/agent_functions/
    └── echidna_chat.py                  # Chat container — agentic loop, tools, streaming
```

The entire agent is a single Python file. `echidna_chat.py` defines the `EchidnaChat` class, which subclasses `Chat` from `mythic_container.ChatBase`. On startup, `mythic_container.mythic_service.start_and_run_forever()` discovers and registers it with Mythic's RabbitMQ message bus.

### Key Components

- **`EchidnaChat.chat()`** — entry point for every message. Resolves provider config, dispatches to the provider-specific agentic loop.
- **`_agentic_anthropic()`** — streams Anthropic API responses (SSE `content_block_start/delta/stop` events), handles tool use blocks, feeds results back as `tool_result` messages.
- **`_agentic_openai()`** — streams OpenAI-compatible responses (SSE `data:` lines), accumulates `tool_calls` deltas by index, feeds results back as `tool` role messages.
- **`_chat_google()`** — single-shot request to Google's `generateContent` API. Tool support coming soon.
- **`_execute_tool()`** — dispatcher that routes tool calls to the corresponding `_tool_*` method.
- **`_send_tool_card()`** — renders tool calls as collapsible subagent-style cards in the Mythic chat UI.

### Internal Networking

Echidna runs as a Docker container in Mythic's internal network. Tool calls that need Mythic data use RPC over RabbitMQ (`SendMythicRPC*` functions). The `tag_task` tool is the only one that makes an HTTP request — to `https://mythic_nginx:7443/graphql/` (the internal Nginx reverse proxy), because no RPC exists for ATT&CK task mapping.

## Custom Endpoints

Select the **Custom** provider and set `openai_base_url` to point at any OpenAI-compatible endpoint. Works with:

- **Gateways**: LiteLLM, one-api, new-api (serve multiple protocols, handle model routing)
- **Inference servers**: vLLM, Ollama, llama.cpp, LocalAI (serve `/v1/chat/completions` directly)

The endpoint must support **function calling** (tool use) for Echidna's tools to work. Without it, the LLM can still chat but cannot interact with Mythic.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

- [Mythic C2](https://github.com/its-a-feature/Mythic) by its-a-feature
- [infreerence](https://github.com/aremndgashi-infreerence) — endpoint discovery and gateway bridging for self-hosted LLMs
