"""Which agent wire does a configured endpoint speak?

`chat`, `model` and `report` need only plain /chat/completions. Skills and
campaigns are different: they spawn real coding agents in the toolbox — the
Claude Code CLI over the Anthropic /v1/messages wire, or Codex over the OpenAI
/v1/responses wire.

The distinction that matters operationally is *gateway vs. raw server*:

  * A gateway (LiteLLM, one-api, new-api) serves /v1/messages itself, so an
    endpoint found with `infreerence` drives the entire kill chain — skills,
    campaigns, delegation — with nothing in between.
  * A raw inference server (vLLM, Ollama, llama.cpp, LocalAI) implements only
    the OpenAI chat wire and is chat-only until something translates for it:
    `infreerence bridge <scan_id> <ip:port> --run`.

The payload build probes once and records the answer as `Wire:` in the callback
config, so a skill task fails fast with a real reason instead of re-probing every
time. :func:`resolve_engine` reads that, and falls back to probing when the key is
absent — callbacks built before the probe existed still work.
"""
from __future__ import annotations

import aiohttp

# What to tell an operator whose endpoint can't run skills.
BRIDGE_HINT = (
    "This endpoint speaks the OpenAI chat wire only, and skills need an agent "
    "wire (Anthropic /v1/messages). Either point at a gateway that serves it "
    "(LiteLLM, one-api, new-api), or front this one with a local bridge:\n"
    "    infreerence bridge <scan_id> <ip:port> --run\n"
    "then rebuild the payload with base_url=http://127.0.0.1:4000/v1"
)


async def endpoint_models(base_url: str, api_key: str):
    """Model ids an OpenAI-compatible endpoint advertises; [] if unreachable."""
    if not base_url:
        return []
    headers = {"Authorization": f"Bearer {api_key or 'not-needed'}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url.rstrip('/')}/models", headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
        return [m['id'] for m in data.get('data', []) if isinstance(m, dict) and m.get('id')]
    except Exception:
        return []


def anthropic_root(base_url: str) -> str:
    """The ROOT of an endpoint carried OpenAI-style (…/v1).

    Anthropic clients append /v1/messages themselves, so the /v1 we carry for the
    OpenAI wire has to come off or requests land on /v1/v1/messages.
    """
    root = (base_url or "").rstrip("/")
    return root[:-3].rstrip("/") if root.endswith("/v1") else root


async def serves_anthropic_wire(base_url: str, api_key: str, model: str = ""):
    """Does this endpoint answer POST /v1/messages itself? -> (ok, detail)."""
    if not base_url:
        return False, "no base_url"
    url = f"{anthropic_root(base_url)}/v1/messages"
    headers = {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
        "x-api-key": api_key or "not-needed",
        "authorization": f"Bearer {api_key or 'not-needed'}",
    }
    payload = {"model": model or "probe", "max_tokens": 16,
               "messages": [{"role": "user", "content": "hi"}]}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    return True, "200"
                return False, f"HTTP {resp.status} from {url}"
    except Exception as e:  # unreachable, TLS, DNS — all mean "can't drive skills"
        return False, f"{type(e).__name__} reaching {url}"


async def serves_openai_responses_wire(base_url: str, api_key: str, model: str = ""):
    """Does this endpoint answer POST /responses (the Codex wire)? -> (ok, detail).

    base_url is carried OpenAI-style *with* /v1, and the Responses path hangs
    directly off it — unlike the Anthropic wire, which needs the root.
    """
    if not base_url:
        return False, "no base_url"
    url = f"{base_url.rstrip('/')}/responses"
    headers = {"content-type": "application/json",
               "authorization": f"Bearer {api_key or 'not-needed'}"}
    payload = {"model": model or "probe", "input": "hi", "max_output_tokens": 16}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    return True, "200"
                return False, f"HTTP {resp.status} from {url}"
    except Exception as e:
        return False, f"{type(e).__name__} reaching {url}"


async def detect_wire(provider: str, base_url: str, api_key: str, model: str = ""):
    """Which agent wire does this endpoint actually serve? -> (wire, detail).

    ``wire`` is "anthropic" (Claude Code can drive it), "openai" (Codex can), or
    "chat" (neither — chat/model/report only). Each provider is probed for the
    wire its own engine needs; Custom is probed for both, because a gateway that
    serves either one can still run skills.
    """
    if not base_url:
        return "chat", "no base_url"
    if provider == "OpenAI":
        ok, detail = await serves_openai_responses_wire(base_url, api_key, model)
        return ("openai" if ok else "chat"), detail
    if provider == "Anthropic":
        ok, detail = await serves_anthropic_wire(base_url, api_key, model)
        return ("anthropic" if ok else "chat"), detail
    # Custom: take whichever wire answers — Anthropic first (LiteLLM, one-api and
    # new-api all serve it, and Claude Code is the better-supported engine here).
    ok, detail = await serves_anthropic_wire(base_url, api_key, model)
    if ok:
        return "anthropic", detail
    ok2, detail2 = await serves_openai_responses_wire(base_url, api_key, model)
    if ok2:
        return "openai", detail2
    return "chat", f"{detail}; {detail2}"


def bridge_guide(want: str = "anthropic", base_url: str = "") -> str:
    """Copy-paste instructions for putting a LiteLLM bridge in front of an
    endpoint that doesn't serve the agent wire ``want``.

    Shared by the payload build step and the `bridge` command so the operator
    reads the same thing wherever they hit the wall.
    """
    upstream = base_url or "http://<ENDPOINT-IP>:<PORT>/v1"
    wire_txt = ("Anthropic /v1/messages (Claude Code skills)" if want == "anthropic"
                else "OpenAI /v1/responses (Codex skills)")
    return f"""
================================================================
 LiteLLM bridge — needed for: {wire_txt}
================================================================
Your endpoint speaks the plain OpenAI chat wire. Skills spawn real coding
agents, which need an agent wire. One local LiteLLM process translates: it
fronts your endpoint and serves the OpenAI, Anthropic AND Gemini wires at
once, on 127.0.0.1:4000.

--- WITH infreerence (one command) ---------------------------------
  infreerence integrations <scan_id>                 # list usable endpoints
  infreerence bridge <scan_id> <ip:port> --run       # write config + launch

  Manual target (no scan):
  infreerence bridge --host <IP> --port <PORT> --engine openai \\
      --model <MODEL> --run

--- WITHOUT infreerence (by hand) ----------------------------------
  pip install 'litellm[proxy]'      # or: uv tool install 'litellm[proxy]'

  cat > bridge.yaml <<'YAML'
  model_list:
    - model_name: "*"
      litellm_params:
        model: "openai/<MODEL>"
        api_base: "{upstream}"
        api_key: "not-needed"
  litellm_settings:
    drop_params: true
  YAML

  litellm --config bridge.yaml --host 127.0.0.1 --port 4000

--- THEN rebuild the payload with -----------------------------------
  {"anthropic_base_url" if want == "anthropic" else "openai_base_url"} = http://127.0.0.1:4000/v1
  (leave the matching *_key empty — the bridge needs no key)

Verify it first:
  curl -sS -o /dev/null -w '%{{http_code}}\\n' \\
    http://127.0.0.1:4000/{"v1/messages" if want == "anthropic" else "v1/responses"} \\
    -H 'content-type: application/json' \\
    {"-H 'anthropic-version: 2023-06-01' " if want == "anthropic" else ""}\\
    -d '{"..."}'
  200 = the wire is live.

NOTE: the bridge listens on loopback. If Mythic runs in Docker, 127.0.0.1
inside the echidna container is NOT your host — use the host's LAN IP (or
run the bridge with --host 0.0.0.0 and firewall it).
================================================================
""".strip()


async def resolve_engine(provider: str, config: dict) -> str:
    """The toolbox engine to run a skill/campaign with, for this callback config.

    Returns "Anthropic" or "OpenAI". Raises with an actionable message when the
    configured endpoint cannot drive an agent at all.
    """
    base_url = (config.get("BaseURL") or "").rstrip("/")

    if provider == "Google":
        raise Exception(
            "Skills and campaigns are not supported for the Google provider. "
            "Use Anthropic or OpenAI — either against the vendor API, or against "
            "your own endpoint via anthropic_base_url / openai_base_url."
        )
    if provider == "Kimi":
        return "Anthropic"
    if provider in ("Anthropic", "OpenAI"):
        # No override: the vendor API always speaks its own agent wire.
        if not base_url:
            return provider
        # Overridden: the endpoint has to actually serve that wire, so fail here
        # with instructions rather than mid-skill with a 404.
        want = "anthropic" if provider == "Anthropic" else "openai"
        wire = (config.get("Wire") or "").strip().lower()
        detail = "recorded at build time"
        if not wire:
            wire, detail = await detect_wire(
                provider, base_url, config.get("APIKey") or "", config.get("Model") or "")
        if wire == want:
            return provider
        raise Exception(
            f"{bridge_guide(want, base_url)}\n\nProbe result: {detail}\n"
            "Run the `bridge` command on this callback for the same guide any time."
        )
    if provider != "Custom":
        raise Exception(f"Unknown provider '{provider}'. Rebuild the payload.")

    # --- Custom: an OpenAI-compatible endpoint, gateway or otherwise ---
    if not base_url:
        raise Exception("BaseURL not found in callback config. Please rebuild the payload.")

    wire = (config.get("Wire") or "").strip().lower()
    detail = "recorded at build time"
    if not wire:
        # Built before the probe existed (or the key was dropped) — ask the endpoint.
        wire, detail = await detect_wire(
            provider, base_url, config.get("APIKey") or "", config.get("Model") or "")
    if wire == "anthropic":
        return "Anthropic"
    if wire in ("openai", "responses"):
        return "OpenAI"
    raise Exception(
        f"{bridge_guide('anthropic', base_url)}\n\nProbe result: {detail}\n"
        "Run the `bridge` command on this callback for the same guide any time."
    )
