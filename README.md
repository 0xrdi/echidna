# Echidna

<p align="center">
  <img src="echidna.svg" alt="Echidna Logo" width="150" height="150">
</p>

<p align="center">
  <strong>A virtual LLM agent for Mythic C2</strong>
</p>

---

> **Legal & Authorized Use Only** — Echidna is provided for authorized security testing, research, and educational purposes only. You may only use it against systems you own or have explicit written authorization to test. The authors assume no liability for misuse or unauthorized activity. Unauthorized access to computer systems is illegal under laws including the CFAA, Computer Misuse Act, and equivalent legislation in your jurisdiction. **The authors do not condone, support, or encourage any illegal activity.**

---

Echidna is a virtual agent for [Mythic C2](https://github.com/its-a-feature/Mythic) that turns LLMs into red team operators. No binary, no target host — it creates instant callbacks that run AI skill agents through Mythic's interface, each scope-isolated with strict tool policies and the ability to delegate commands to real implants.

## Installation

```bash
sudo ./mythic-cli install github https://github.com/0xrdi/echidna.git

cd InstalledServices/echidna/toolbox
sudo docker compose up -d --build
```

## Providers

| Provider | Skills | Default Model | Build field |
|----------|:------:|---------------|-------------|
| Anthropic | Yes (Claude Code) | `claude-opus-4-6` | `anthropic_key` or `anthropic_base_url` |
| OpenAI | Yes (Codex) | `gpt-5` | `openai_key` or `openai_base_url` |
| Kimi | Yes (via Anthropic protocol) | `kimi-k3` | `kimi_key` |
| Google | Chat only | `gemini-2.5-flash` | `google_key` |
| Custom | If endpoint serves an agent protocol | first listed | `openai_base_url` |

Each provider needs **either** an API key **or** a base URL, not both. Kimi skills route through Moonshot's Anthropic-compatible endpoint (`api.moonshot.ai/anthropic`) automatically.

## Commands

| Command | Description |
|---------|-------------|
| `chat <message>` | Chat with the LLM |
| `model [name]` | List or switch models |
| `skill <id> [--callback <id>] <task>` | Run a skill agent |
| `skills` | List available skills |
| `campaign [--auto] [--callback <id>] <objective>` | Auto-chain skills with approval gates |
| `report [--format md\|html]` | Generate findings report |
| `bridge [anthropic\|openai]` | Probe endpoint and print bridge setup |
| `jobs [stop <id>]` | List or stop active jobs |
| `exit` | Deactivate callback |

## Skills

13 skill agents covering the full kill chain. Each is isolated at three layers: **tool policy** (container sandboxing), **network policy** (proxy/delegation access), and **system prompt** (hard constraints).

| Skill | Phase | Proxy |
|-------|-------|:-----:|
| `passive-recon` | Recon | — |
| `active-recon` | Recon | Yes |
| `attack-surface-analyzer` | Analysis | — |
| `exploitation-planner` | Planning | — |
| `exploitation-executor` | Exploitation | Yes |
| `post-exploitation` | Post-Exploit | — |
| `privilege-escalation` | Escalation | — |
| `credential-validation` | Validation | Yes |
| `cloud-enumeration` | Cloud | Yes |
| `persistence` | Persistence | — |
| `edr-bypass` | Evasion | — |
| `lateral-movement` | Lateral | — |
| `data-exfil` | Exfil | — |

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

Create `toolbox/skills/my_skill.json` (tool policy + output schema) and `toolbox/skills/prompts/my-skill.md` (system prompt), then restart the toolbox.

## Delegation Bridge

Skill agents execute commands on real implants (Apollo, Poseidon, Merlin, etc.) via the delegation bridge:

```
Skill Agent (toolbox) → POST :6790/delegate → Delegate Server → Mythic RPC → implant
```

Works with any Mythic agent. Synchronous (120s timeout), auto-detects payload type.

## Architecture

```
echidna/
├── echidna/mythic/agent_functions/
│   ├── builder.py          # PayloadType & callback creation
│   ├── chat.py             # LLM API integration (Anthropic, OpenAI, Google, Kimi)
│   ├── skill.py            # Skill engine (isolation + delegation + reporting)
│   ├── campaign.py         # Campaign orchestrator
│   ├── wire.py             # Agent protocol detection & engine resolution
│   ├── bridge.py           # LiteLLM bridge setup guide
│   ├── delegate_server.py  # Delegation bridge (port 6790)
│   ├── model.py            # Model management
│   ├── rpc.py              # Mythic RPC helpers
│   └── exit.py             # Callback deactivation
└── toolbox/
    ├── server.py            # FastAPI: skill engine, tool policy, streaming
    └── skills/
        ├── *.json           # 13 skill configs
        └── prompts/*.md     # 13 skill prompts

```

## Custom Endpoints

`Custom` points Echidna at any OpenAI-compatible endpoint. Skills work if it also serves an agent protocol (Anthropic `/v1/messages` or OpenAI `/v1/responses`) — the build step probes and tells you. Gateways like LiteLLM serve the protocol natively; raw inference servers (vLLM, Ollama) need a bridge:

```bash
infreerence bridge <scan_id> <ip:port> --run
# then build with openai_base_url = http://127.0.0.1:4000/v1
```

The `bridge` command on any callback reprints the setup guide on demand.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

- [Infreerence](https://github.com/armendgashi-sentry/infreerence) by armendgashi-sentry
- [Mythic C2](https://github.com/its-a-feature/Mythic) by its-a-feature
