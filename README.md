# Echidna

<p align="center">
  <img src="echidna.svg" alt="Echidna Logo" width="150" height="150">
</p>

<p align="center">
  <strong>A virtual LLM agent for Mythic C2</strong>
</p>

---

Echidna is a virtual agent for [Mythic C2](https://github.com/its-a-feature/Mythic) that turns LLMs into specialized red team operators. No binary, no target host — it creates instant callbacks that let operators run AI skill agents through Mythic's interface, each scope-isolated with strict tool policies and the ability to delegate commands to real implants.

## Features

- **13 Skill Agents** — Full kill chain coverage, each with container-level tool sandboxing and structured JSON output
- **Campaign Orchestrator** — Auto-chain skills with operator approval gates before dangerous actions
- **Delegation Bridge** — Skill agents execute commands on real implants (Apollo, Merlin) via Mythic RPC
- **Target Context Injection** — Skills automatically receive host, user, OS, and network info from the callback
- **Cost Tracking** — Real-time token usage and USD cost per skill and across campaigns
- **Multi-Provider** — OpenAI, Anthropic, and Google Gemini

## Installation

```bash
# Install from GitHub
sudo ./mythic-cli install github https://github.com/0xrdi/echidna.git

# Deploy the toolbox (required for skills)
cd InstalledServices/echidna/toolbox
sudo docker compose up -d --build
```

## Commands

| Command | Description | Example |
|---------|-------------|---------|
| `chat <message>` | Chat with the LLM | `chat Explain Kerberoasting` |
| `model [name]` | List or switch models | `model` / `model gpt-5` |
| `skills` | List available skills | `skills` |
| `skill <id> [--callback <id>] <task>` | Run a skill agent | `skill passive-recon Enumerate acme.corp` |
| `campaign [--auto] [--callback <id>] <objective>` | Auto-chain skills with approval gates | `campaign --callback 19 Full assessment of acme.corp` |
| `campaign --resume` / `campaign --skip` | Continue or skip a paused campaign | `campaign --resume` |
| `report [--format md\|html]` | Generate findings report from skill outputs | `report` / `report --format html` |
| `jobs [stop <id>]` | List or stop active jobs | `jobs` / `jobs stop abc123` |
| `bridge [anthropic\|openai]` | Probe this callback's endpoint and print LiteLLM bridge setup | `bridge` / `bridge openai` |
| `exit` | Deactivate callback | `exit` |

> Google only supports `chat` and `model`. Skills and campaigns need an *agent wire*:
> Anthropic, OpenAI, or a `Custom` endpoint that serves the Anthropic `/v1/messages`
> wire — see [Custom endpoints](#custom-endpoints-infreerence).

## Skills

| Skill | Phase | Proxy | Example |
|-------|-------|-------|---------|
| `passive-recon` | Recon | — | `skill passive-recon Enumerate acme.corp` |
| `active-recon` | Recon | Yes | `skill active-recon --callback 5 --port 7001 Scan 10.0.0.0/24` |
| `attack-surface-analyzer` | Analysis | — | `skill attack-surface-analyzer Analyze recon findings` |
| `exploitation-planner` | Planning | — | `skill exploitation-planner Plan attack on web app` |
| `exploitation-executor` | Exploitation | Yes | `skill exploitation-executor --callback 5 Execute approved plan` |
| `post-exploitation` | Post-Exploit | — | `skill post-exploitation --callback 19 Full host enumeration` |
| `privilege-escalation` | Escalation | — | `skill privilege-escalation --callback 19 Escalate using post-exploitation_output.json` |
| `credential-validation` | Validation | Yes | `skill credential-validation --callback 19 Test recovered creds` |
| `cloud-enumeration` | Cloud | Yes | `skill cloud-enumeration Enumerate AWS with recovered keys` |
| `persistence` | Persistence | — | `skill persistence --callback 19 Install persistence on host` |
| `edr-bypass` | Evasion | — | `skill edr-bypass --callback 19 Analyze and bypass EDR` |
| `lateral-movement` | Lateral | — | `skill lateral-movement --callback 19 Pivot to DC01` |
| `data-exfil` | Exfil | — | `skill data-exfil --callback 19 Exfil high-value data` |

Each skill is isolated at three layers: **tool policy** (container sandboxing), **network policy** (proxy/delegation access), and **system prompt** (hard constraints).

### Running Skills

```bash
# Passive recon (standalone)
skill passive-recon Enumerate subdomains and tech stack for acme.corp

# Post-exploitation via delegation to a Merlin implant
skill post-exploitation --callback 19 Full host enumeration

# Active recon through SOCKS proxy
skill active-recon --callback 5 --port 7001 Scan 10.0.0.0/24 for web services

# Campaign: chains skills with approval gates between each step
campaign --callback 19 Full assessment of sentry.security
campaign --resume    # continue after reviewing
campaign --skip      # skip a queued skill

# Auto mode: runs without pausing (still pauses before dangerous skills)
campaign --auto --callback 19 Full assessment of sentry.security
```

### Output

Every skill writes structured JSON (`output.json`) with findings and recommendations. Echidna delivers results three ways:

1. **Formatted report** — Aligned tables rendered in the Mythic task output
2. **JSON file** — Uploaded to Mythic's Files tab as `{skill_id}_output.json`
3. **Artifacts** — Key findings (credentials, escalation paths, domains, cloud assets) registered as Mythic artifacts

### Adding Custom Skills

Create two files and restart the toolbox:

**`toolbox/skills/my_skill.json`** — Tool policy, proxy/delegation flags, output schema

**`toolbox/skills/prompts/my-skill.md`** — Agent identity, constraints, methodology (YAML frontmatter with `name` and `description`)

## Delegation Bridge

```
Skill Agent (toolbox)  →  curl POST :6790/delegate  →  Delegate Server (Echidna)
                                                            ↓
                                                       Mythic RPC → create task on implant
                                                       Mythic RPC → poll for completion
                                                       Mythic RPC → return output
                                                            ↓
Skill Agent  ←  {"success": true, "output": "..."}  ←  Delegate Server
```

- **Generic** — Works with any Mythic agent (Apollo, Merlin, etc.)
- **Synchronous** — Blocks until implant returns (120s timeout)
- **Auto-detection** — Detects payload type (Merlin/Apollo) and adjusts shell parameter format
- **Full access** — Every implant command available: shell, mimikatz, execute_assembly, ldap_query, dcsync, lateral movement, Kerberos, and more

## Architecture

```
echidna/
├── echidna/mythic/agent_functions/
│   ├── builder.py          # PayloadType & callback creation
│   ├── chat.py             # LLM API integration
│   ├── skill.py            # Skill engine (isolation + delegation + reporting)
│   ├── campaign.py         # Campaign orchestrator
│   ├── skills.py           # Skill listing
│   ├── jobs.py             # Job management
│   ├── delegate_server.py  # Delegation bridge (port 6790)
│   ├── model.py            # Model management
│   └── exit.py             # Callback deactivation
└── toolbox/
    ├── server.py            # FastAPI: skill engine, tool policy, streaming
    └── skills/
        ├── *.json           # 13 skill configs (tool policies, schemas)
        └── prompts/*.md     # 13 skill prompts (native SKILL.md format)
```

## Providers

| Provider | Engine | API | Default Model | Build fields |
|----------|--------|-----|---------------|--------------|
| Anthropic | Claude Code | Messages API | `claude-opus-4-6` | `anthropic_key` **or** `anthropic_base_url` |
| OpenAI | Codex | Responses API | `gpt-5` | `openai_key` **or** `openai_base_url` |
| Google | — (chat/model only) | Gemini generateContent | `gemini-2.5-flash` | `google_key` |
| Custom | whichever wire answers | OpenAI-compatible | first model listed | `openai_base_url` (+ optional `openai_key`) |

Per provider there are two ways in, and you need **either one, not both**:

- **`<provider>_key`** — the vendor API. No base URL anywhere.
- **`<provider>_base_url`** — your own endpoint. **No API key at all** (it becomes
  `not-needed` internally, since OpenAI clients demand a non-empty string).

Supplying both is allowed but only useful for a gateway that wants a virtual key —
then the key you gave is used against your endpoint. Fields belonging to the other
providers are ignored, so one form can hold several vendors at once. The only
error is supplying *neither*.

Base URLs are OpenAI-style and **include `/v1`** — exactly what `infreerence
integrations` prints. Echidna strips it to the root where the Anthropic client
needs that.

### Which wire, and what to do when it's missing

Skills and campaigns spawn real coding agents, so they need an **agent wire**, not
plain `/chat/completions`:

| you picked | engine | endpoint must serve |
|---|---|---|
| `provider=Anthropic` + `anthropic_base_url` | Claude Code | `POST /v1/messages` |
| `provider=OpenAI` + `openai_base_url` | Codex | `POST /v1/responses` |
| `provider=Custom` + `openai_base_url` | whichever answers | either of the above |

The payload build **probes the endpoint** and prints the verdict in the
Configuration step before you commit:

```
Provider : OpenAI
Model    : claude-opus-4.5
Endpoint : http://10.0.0.5:4000/v1
Models   : 17 listed
Wire     : openai — skills + campaigns ENABLED (Codex)
```

If the wire is missing, the build step prints the full LiteLLM bridge guide
inline, and the **`bridge` command** reprints it on demand from any callback —
it probes live first, tells you what the endpoint does today, and gives the
setup both **with infreerence** (one command) and **by hand**. LiteLLM serves
both wires, so a single bridge unlocks Claude Code *and* Codex.

### Custom endpoints (infreerence)

`Custom` points Echidna at any OpenAI-compatible endpoint — the kind
[infreerence](https://github.com/0xrdi/infreerence) finds. What it can do depends
on which wire that endpoint speaks, and the payload build **probes and tells you**:

- **A gateway** — LiteLLM, one-api, new-api — serves the Anthropic `/v1/messages`
  wire itself. Everything works: `chat`, `model`, `report`, **`skill` and
  `campaign`**, with no bridge in between. The build step prints
  `Anthropic /v1/messages wire: YES — skills + campaigns enabled`.
- **A raw inference server** — vLLM, Ollama, llama.cpp, LocalAI — speaks only the
  chat wire, so it drives `chat`/`model`/`report`. To get skills, front it with a
  local bridge and point `base_url` at that instead:

  ```bash
  infreerence bridge <scan_id> <ip:port> --run     # serves every wire on 127.0.0.1:4000
  # then build with base_url = http://127.0.0.1:4000/v1
  ```

Getting the parameters for a discovered endpoint:

```bash
infreerence integrations <scan_id> <ip:port>          # base URL + every model it serves
infreerence integrations <scan_id> <ip:port> --tool claude-code   # the same wire Echidna uses
```

`base_url` is the OpenAI-style URL **including `/v1`** (exactly what infreerence
emits); Echidna strips it where the Anthropic client needs a root. Both model
variables are pinned to the model you chose, because the Claude Code CLI otherwise
requests its own `claude-*` ids — including a haiku-class model for background work
— which a discovered endpoint does not serve.

> Point this only at an endpoint you own or are authorized to test. Driving
> someone else's exposed inference server consumes their compute and their
> upstream API credits.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

- [Mythic C2](https://github.com/its-a-feature/Mythic) by its-a-feature
- Built with [Claude Code](https://claude.ai/claude-code)
