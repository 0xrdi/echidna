from mythic_container.PayloadBuilder import *
from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *
import asyncio
import pathlib


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
Virtual agent for interfacing with LLM APIs (OpenAI, Anthropic, Google).
No binary is generated - creates instant callback for chat operations.
    """
    supports_dynamic_loading = False

    # Build parameters for LLM configuration
    build_parameters = [
        BuildParameter(
            name="provider",
            parameter_type=BuildParameterType.ChooseOne,
            choices=["Anthropic", "OpenAI", "Google"],
            default_value="Anthropic",
            description="LLM provider to use for chat operations",
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
            description="Model name (leave empty for provider defaults: claude-opus-4-6, gpt-5, gemini-2.5-flash)",
            required=False,
        ),
        BuildParameter(
            name="api_key",
            parameter_type=BuildParameterType.String,
            default_value="",
            description="API key for the selected provider (sensitive - stored securely)",
            required=True,
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

        try:
            # Step 1: Validate configuration
            provider = self.get_parameter('provider')
            alias = self.get_parameter('alias')
            api_key = self.get_parameter('api_key')
            model = self.get_parameter('model')

            # Validate required parameters
            if not api_key or api_key.strip() == "":
                await SendMythicRPCPayloadUpdatebuildStep(
                    MythicRPCPayloadUpdateBuildStepMessage(
                        PayloadUUID=self.uuid,
                        StepName="Configuration",
                        StepStdout="API key is required",
                        StepSuccess=False
                    )
                )
                resp.build_message = "API key is required"
                return resp

            # Set default models if not specified
            if not model or model.strip() == "":
                model_defaults = {
                    "Anthropic": "claude-opus-4-6",
                    "OpenAI": "gpt-5",
                    "Google": "gemini-2.5-flash"
                }
                model = model_defaults.get(provider, "default")

            await SendMythicRPCPayloadUpdatebuildStep(
                MythicRPCPayloadUpdateBuildStepMessage(
                    PayloadUUID=self.uuid,
                    StepName="Configuration",
                    StepStdout=f"Configured {provider} with model {model}",
                    StepSuccess=True
                )
            )

            # Step 2: Create virtual callback with configuration in ExtraInfo
            # Store config in ExtraInfo since Description gets overwritten by Mythic
            callback_config = f"Provider:{provider}|Model:{model}|APIKey:{api_key}"

            callback_resp = await SendMythicRPCCallbackCreate(
                MythicRPCCallbackCreateMessage(
                    PayloadUUID=self.uuid,
                    C2ProfileName="",  # No C2 needed
                    EncryptionKey=None,
                    DecryptionKey=None,
                    Ip="API",  # Show callback type
                    Host=provider,  # Show provider name (Anthropic, OpenAI, Google)
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
