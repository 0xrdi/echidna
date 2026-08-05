from mythic_container.PayloadBuilder import *
from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *
import asyncio
import pathlib

# One source of truth for endpoint capability detection — the skill and campaign
# commands resolve the same way at task time, and the `bridge` command prints the
# same guide this build step does.
from .wire import bridge_guide as _bridge_guide
from .wire import detect_wire as _detect_wire
from .wire import endpoint_models as _endpoint_models


class Echidna(PayloadType):
    name = "echidna"
    file_extension = ""  # Virtual agent, no file
    author = "@0xrdi"
    mythic_encrypts = False  # No real communication
    supported_os = [SupportedOS("LLM")]  # Custom OS for virtual LLM agent
    semver = "1.0.0"
    wrapper = False
    wrapped_payloads = []
    agent_path = pathlib.Path(".") / "echidna" / "mythic"
    agent_code_path = pathlib.Path(".") / "echidna" / "agent_code"
    agent_icon_path = agent_path / "agent_functions" / "echidna.svg"
    note = """
Virtual agent for interfacing with LLM APIs (OpenAI, Anthropic, Google, Kimi).
No binary is generated - creates instant callback for chat operations.
    """
    supports_dynamic_loading = False

    # Build parameters for LLM configuration.
    #
    # Per provider there are two ways in, and you need EITHER one:
    #   <provider>_key       -> the vendor API
    #   <provider>_base_url  -> your own endpoint, no key at all
    # Supplying both is allowed but only meaningful for a gateway that wants a
    # virtual key. Fields belonging to the other providers are ignored entirely.
    build_parameters = [
        BuildParameter(
            name="provider",
            parameter_type=BuildParameterType.ChooseOne,
            choices=["Anthropic", "OpenAI", "Google", "Kimi", "Custom"],
            default_value="Anthropic",
            description=(
                "Which engine drives this callback. Anthropic = Claude Code (skills + "
                "campaigns). OpenAI = Codex (skills + campaigns). Google = chat/model only. "
                "Kimi = Moonshot AI (chat/model only). Custom = any OpenAI-compatible "
                "endpoint; skills work if it also serves an agent protocol — the build probes "
                "it and tells you."
            ),
            required=True,
        ),
        BuildParameter(
            name="alias",
            parameter_type=BuildParameterType.String,
            default_value="LLM-Assistant",
            description="Friendly name for this LLM connection (appears in callback)",
            required=True,
        ),
        BuildParameter(
            name="model",
            parameter_type=BuildParameterType.String,
            default_value="",
            description=(
                "Model name. Empty = the vendor default (claude-opus-4-6 / gpt-5 / "
                "gemini-2.5-flash) or, when a base URL is set, the first model that "
                "endpoint lists in /v1/models."
            ),
            required=False,
        ),
        # ---- keys: one per vendor, so several can live in the form at once ----
        BuildParameter(
            name="anthropic_key",
            parameter_type=BuildParameterType.String,
            default_value="",
            description=("Anthropic API key — for the vendor API. EITHER this OR "
                         "anthropic_base_url; leave empty if you set a base URL. "
                         "(Set both only if your gateway requires a virtual key.)"),
            required=False,
        ),
        BuildParameter(
            name="openai_key",
            parameter_type=BuildParameterType.String,
            default_value="",
            description=("OpenAI API key — for the vendor API. EITHER this OR "
                         "openai_base_url; leave empty if you set a base URL. "
                         "(Set both only if your gateway requires a virtual key.)"),
            required=False,
        ),
        BuildParameter(
            name="google_key",
            parameter_type=BuildParameterType.String,
            default_value="",
            description="Google AI Studio API key. Google has no base-URL override.",
            required=False,
        ),
        BuildParameter(
            name="kimi_key",
            parameter_type=BuildParameterType.String,
            default_value="",
            description="Moonshot AI (Kimi) API key.",
            required=False,
        ),
        # ---- endpoints: one per protocol, no API key required ----
        BuildParameter(
            name="anthropic_base_url",
            parameter_type=BuildParameterType.String,
            default_value="",
            description=(
                "Your own endpoint serving the Anthropic /v1/messages protocol — LiteLLM, "
                "one-api, new-api. Include /v1, e.g. http://10.0.0.5:4000/v1 (exactly what "
                "`infreerence integrations` emits). No API key needed. Used when "
                "provider=Anthropic: drives chat, model, report, skills and campaigns."
            ),
            required=False,
        ),
        BuildParameter(
            name="openai_base_url",
            parameter_type=BuildParameterType.String,
            default_value="",
            description=(
                "Your own endpoint serving the OpenAI /v1 protocol. Include /v1. Used when "
                "provider=OpenAI (Codex skills need /v1/responses — LiteLLM serves it) or "
                "provider=Custom (plain /chat/completions is enough for chat). No API key "
                "needed. Missing the protocol? Run the `bridge` command on any callback for "
                "setup instructions, with or without infreerence. "
                "Point ONLY at an endpoint you own or are authorized to use."
            ),
            required=False,
        ),
    ]

    # No C2 profiles needed for virtual agent
    c2_profiles = []

    build_steps = [
        BuildStep(step_name="Configuration", step_description="Validating LLM configuration"),
        BuildStep(step_name="Callback Creation", step_description="Creating virtual callback"),
    ]

    async def build(self) -> BuildResponse:
        """Build the virtual agent - creates instant callback"""
        resp = BuildResponse(status=BuildStatus.Error)

        async def _fail(message: str):
            """Report a configuration failure on the build step and stop."""
            await SendMythicRPCPayloadUpdatebuildStep(
                MythicRPCPayloadUpdateBuildStepMessage(
                    PayloadUUID=self.uuid,
                    StepName="Configuration",
                    StepStdout=message,
                    StepSuccess=False,
                )
            )
            resp.build_message = message.splitlines()[0]
            return resp

        try:
            # Step 1: Resolve the provider's own key/endpoint pair
            provider = self.get_parameter('provider')
            alias = self.get_parameter('alias')

            def _p(name):
                return (self.get_parameter(name) or "").strip()

            model = _p('model')
            # Each provider reads only its own two fields; the rest are ignored.
            # Custom rides the OpenAI protocol, so it shares that pair.
            key_of = {"Anthropic": 'anthropic_key', "OpenAI": 'openai_key',
                      "Google": 'google_key', "Kimi": 'kimi_key',
                      "Custom": 'openai_key'}
            url_of = {"Anthropic": 'anthropic_base_url', "OpenAI": 'openai_base_url',
                      "Custom": 'openai_base_url'}          # Google, Kimi: no override
            key_field = key_of.get(provider, 'anthropic_key')
            url_field = url_of.get(provider)
            api_key = _p(key_field)
            base_url = _p(url_field).rstrip('/') if url_field else ""

            # An endpoint replaces the key; without one, the vendor key is required.
            if provider == "Custom" and not base_url:
                return await _fail(
                    "openai_base_url is required for the Custom provider "
                    "(e.g. http://10.0.0.5:4000/v1).")
            if not base_url and not api_key:
                hint = (f"Set {key_field}, or point {url_field} at your own endpoint "
                        f"(no key needed).") if url_field else f"Set {key_field}."
                return await _fail(f"No credentials for provider {provider}. {hint}")
            if base_url and not api_key:
                api_key = "not-needed"     # open endpoint; clients still need a string

            # Resolve a REAL model id when an endpoint is in play: skills spawn a
            # coding-agent CLI, which otherwise asks for its own vendor model ids —
            # names your endpoint doesn't serve, so every call 404s.
            listed = await _endpoint_models(base_url, api_key) if base_url else []
            if not model:
                vendor_default = {"Anthropic": "claude-opus-4-6", "OpenAI": "gpt-5",
                                  "Google": "gemini-2.5-flash",
                                  "Kimi": "kimi-k3"}.get(provider, "")
                model = listed[0] if (base_url and listed) else vendor_default

            lines = [f"Provider : {provider}",
                     f"Model    : {model or '<unresolved>'}",
                     f"Endpoint : {base_url or '(vendor API)'}",
                     f"Auth     : {key_field}" if not base_url else
                     f"Auth     : {'key supplied' if api_key != 'not-needed' else 'none (open endpoint)'}"]

            # Which agent protocol does this endpoint actually serve? Anthropic needs
            # /v1/messages (Claude Code), OpenAI needs /v1/responses (Codex).
            # Recording it now means a skill fails fast with a real reason.
            wire = "chat"
            if base_url:
                lines.append(f"Models   : {len(listed)} listed" if listed else
                             f"Models   : NONE — {base_url}/models returned nothing "
                             f"(unreachable from the Mythic container?)")
                wire, detail = await _detect_wire(provider, base_url, api_key, model)
                engine = {"anthropic": "Claude Code", "openai": "Codex"}.get(wire)
                if engine:
                    lines.append(f"Protocol : {wire} — skills + campaigns ENABLED ({engine})")
                else:
                    want = "openai" if provider == "OpenAI" else "anthropic"
                    lines.append(f"Protocol : chat only ({detail})")
                    lines.append("           chat / model / report work; skills and "
                                 "campaigns do NOT.")
                    lines.append("")
                    lines.append(_bridge_guide(want, base_url))
            else:
                wire = "vendor"
                lines.append("Protocol : vendor API — skills + campaigns ENABLED")

            await SendMythicRPCPayloadUpdatebuildStep(
                MythicRPCPayloadUpdateBuildStepMessage(
                    PayloadUUID=self.uuid,
                    StepName="Configuration",
                    StepStdout="\n".join(lines),
                    StepSuccess=True
                )
            )

            # Step 2: Create virtual callback with configuration in ExtraInfo
            # Store config in ExtraInfo since Description gets overwritten by Mythic
            # Keys are split on the FIRST ':' only, so a URL's own colons survive intact.
            # Downstream commands (chat/model/report/skill/campaign) all read this
            # same shape, so the new per-provider form resolves into it unchanged.
            callback_config = f"Provider:{provider}|Model:{model}|APIKey:{api_key}"
            if base_url:
                callback_config += f"|BaseURL:{base_url}|Wire:{wire}"

            # For Custom, surface the endpoint itself in the callback rather than
            # the literal word "Custom" — the operator needs to see what they hit.
            host_label = provider
            if base_url:
                host_label = base_url.split('//')[-1].split('/')[0] or provider

            callback_resp = await SendMythicRPCCallbackCreate(
                MythicRPCCallbackCreateMessage(
                    PayloadUUID=self.uuid,
                    C2ProfileName="",  # No C2 needed
                    EncryptionKey=None,
                    DecryptionKey=None,
                    Ip="API",  # Show callback type
                    Host=host_label,  # Provider name, or the custom endpoint's host:port
                    User=alias,  # Show alias as user
                    IntegrityLevel=3,
                    Os="Virtual",
                    Architecture="x64",
                    Domain="",  # Leave empty
                    ExternalIp="0.0.0.0",
                    ProcessName="echidna-virtual",
                    Pid=1,
                    ExtraInfo=callback_config,  # Store config here instead
                )
            )

            if callback_resp.Success:
                callback_id = getattr(callback_resp, 'CallbackID', None) or getattr(callback_resp, 'CallbackUUID', 'unknown')
                await SendMythicRPCPayloadUpdatebuildStep(
                    MythicRPCPayloadUpdateBuildStepMessage(
                        PayloadUUID=self.uuid,
                        StepName="Callback Creation",
                        StepStdout=f"Virtual callback created: {alias} (ID: {callback_id})",
                        StepSuccess=True
                    )
                )

                # Return empty payload (virtual agent)
                resp.payload = b""
                resp.build_message = f"Virtual {provider} agent '{alias}' ready. Callback created automatically."
                resp.status = BuildStatus.Success
            else:
                await SendMythicRPCPayloadUpdatebuildStep(
                    MythicRPCPayloadUpdateBuildStepMessage(
                        PayloadUUID=self.uuid,
                        StepName="Callback Creation",
                        StepStdout=f"Failed to create callback: {callback_resp.Error}",
                        StepSuccess=False
                    )
                )
                resp.build_message = f"Failed to create callback: {callback_resp.Error}"

        except Exception as e:
            resp.build_message = f"Error building virtual agent: {str(e)}"
            resp.payload = b""

        return resp
