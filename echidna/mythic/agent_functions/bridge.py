"""`bridge` — show how to put a LiteLLM bridge in front of an endpoint.

Skills and campaigns spawn real coding agents, which speak an *agent protocol*:
Claude Code wants Anthropic /v1/messages, Codex wants OpenAI /v1/responses. A
raw inference server offers neither. This command prints the setup guide — with
infreerence (one command) and without it (hand-rolled config) — and, when run on
a callback that already has an endpoint, probes it first so the operator is told
what they actually need rather than the generic case.

It is a suggested command, so it shows up in the callback's command list as the
obvious thing to click when a skill refuses to run.
"""
from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *

from .wire import bridge_guide, detect_wire


def _parse_config(extra_info):
    config = {}
    if not extra_info or extra_info.strip() == "":
        return config
    try:
        return json.loads(extra_info)
    except (json.JSONDecodeError, TypeError):
        pass
    for part in extra_info.split('|'):
        if ':' not in part:
            continue
        key, value = part.split(':', 1)
        config[key] = value
    return config


class BridgeArguments(TaskArguments):
    def __init__(self, command_line, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="wire",
                cli_name="wire",
                display_name="Wire",
                type=ParameterType.String,
                description="Which agent protocol you need: 'anthropic' (Claude Code skills) "
                            "or 'openai' (Codex skills). Omit to detect from this callback.",
                parameter_group_info=[ParameterGroupInfo(required=False)],
            ),
        ]

    async def parse_arguments(self):
        raw = self.command_line.strip()
        if raw and raw[0] == "{":
            self.load_args_from_json_string(raw)
        else:
            self.add_arg("wire", raw)


class BridgeCommand(CommandBase):
    cmd = "bridge"
    needs_admin = False
    help_cmd = "bridge [anthropic|openai]"
    description = ("Show how to set up a LiteLLM bridge so skills/campaigns can drive "
                   "an endpoint that only speaks the plain OpenAI chat protocol.")
    version = 1
    author = "@operator"
    argument_class = BridgeArguments
    attackmapping = []
    attributes = CommandAttributes(
        builtin=True,
        suggested_command=True,
    )

    async def create_go_tasking(self, taskData: PTTaskMessageAllData) -> PTTaskCreateTaskingMessageResponse:
        response = PTTaskCreateTaskingMessageResponse(
            TaskID=taskData.Task.ID,
            Success=False,
        )
        try:
            config = _parse_config(taskData.Callback.ExtraInfo)
            provider = config.get('Provider', '')
            base_url = (config.get('BaseURL') or "").rstrip('/')
            api_key = config.get('APIKey') or ""
            model = config.get('Model') or ""

            asked = (taskData.args.get_arg("wire") or "").strip().lower()
            if asked in ("anthropic", "claude", "claude-code"):
                want = "anthropic"
            elif asked in ("openai", "codex", "responses"):
                want = "openai"
            else:
                # Default to the protocol THIS callback's engine needs.
                want = "openai" if provider == "OpenAI" else "anthropic"

            out = []
            if base_url:
                # Say what this endpoint does today before saying how to fix it.
                wire, detail = await detect_wire(provider, base_url, api_key, model)
                recorded = (config.get('Wire') or '').lower()
                out.append(f"Endpoint : {base_url}")
                out.append(f"Provider : {provider}"
                           + (f"   (recorded protocol: {recorded})" if recorded else ""))
                if wire == "anthropic":
                    out.append("Live probe: serves the Anthropic /v1/messages protocol — "
                               "Claude Code skills work against it as-is.")
                elif wire == "openai":
                    out.append("Live probe: serves the OpenAI /v1/responses protocol — "
                               "Codex skills work against it as-is.")
                else:
                    out.append(f"Live probe: chat protocol only ({detail})")
                if wire == want:
                    out.append("")
                    out.append("No bridge needed for this callback. The guide below is "
                               "kept for reference.")
                out.append("")
            else:
                out.append("This callback uses the vendor API directly (no endpoint "
                           "override), so no bridge is needed. Guide below is for "
                           "pointing a payload at your own endpoint.")
                out.append("")

            out.append(bridge_guide(want, base_url))

            response.Success = True
            response.TaskStatus = MythicStatus.Completed
            response.Completed = True
            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response="\n".join(out).encode()
            ))
        except Exception as e:
            response.Success = False
            response.TaskStatus = MythicStatus.Error
            response.Completed = True
            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=f"Error: {str(e)}".encode()
            ))
        return response

    async def process_response(self, task: PTTaskMessageAllData, response: any) -> PTTaskProcessResponseMessageResponse:
        return PTTaskProcessResponseMessageResponse(TaskID=task.Task.ID, Success=True)
