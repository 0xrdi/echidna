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
| `exit` | Deactivate callback | `exit` |

> Google only supports `chat` and `model`. Skills and campaigns require Anthropic or OpenAI.

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

| Provider | API | Default Model | Auth |
|----------|-----|---------------|------|
| Anthropic | Messages API | `claude-opus-4-6` | `x-api-key` header |
| OpenAI | Responses API | `gpt-5` | `Bearer` token |
| Google | Gemini generateContent | `gemini-2.5-flash` | API key param |

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

- [Mythic C2](https://github.com/its-a-feature/Mythic) by its-a-feature
- Built with [Claude Code](https://claude.ai/claude-code)
