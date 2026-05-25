from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *
import aiohttp
import json


class ModelArguments(TaskArguments):
    def __init__(self, command_line, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="action",
                cli_name="action",
                display_name="Action",
                type=ParameterType.String,
                description="No args = list models. Provide a model name to switch to it.",
                parameter_group_info=[
                    ParameterGroupInfo(required=False),
                ],
            ),
        ]

    async def parse_arguments(self):
        raw = self.command_line.strip()
        if raw and raw[0] == "{":
            self.load_args_from_json_string(raw)
        else:
            self.add_arg("action", raw)


def _parse_config(extra_info):
    config = {}
    if not extra_info or extra_info.strip() == "":
        return config
    for part in extra_info.split('|'):
        if ':' not in part:
            continue
        key, value = part.split(':', 1)
        config[key] = value
    return config


class ModelCommand(CommandBase):
    cmd = "model"
    needs_admin = False
    help_cmd = "model [model_name]"
    description = "List available models or switch to a different model"
    version = 2
    author = "@operator"
    argument_class = ModelArguments
    attackmapping = []
    attributes = CommandAttributes(
        builtin=True,
        suggested_command=True
    )

    async def create_go_tasking(self, taskData: PTTaskMessageAllData) -> PTTaskCreateTaskingMessageResponse:
        response = PTTaskCreateTaskingMessageResponse(
            TaskID=taskData.Task.ID,
            Success=False,
        )

        try:
            extra_info = taskData.Callback.ExtraInfo
            if not extra_info or extra_info.strip() == "":
                raise Exception("Callback configuration not found. Please rebuild the payload.")

            config = _parse_config(extra_info)
            provider = config.get('Provider')
            api_key = config.get('APIKey')
            current_model = config.get('Model', 'default')

            if not provider or not api_key:
                raise Exception("Provider or API key not found in callback config")

            action = (taskData.args.get_arg("action") or "").strip()

            # Strip "use " prefix if present for backwards compat
            if action.lower().startswith("use "):
                action = action[4:].strip()

            if not action:
                # LIST MODE — no args, list available models
                output = await self._list_models(provider, api_key)
                output += f"\n\nCurrently using: {current_model}"
                output += f"\nTo switch: model <model_name>"

                response.Success = True
                response.TaskStatus = MythicStatus.Completed
                response.Completed = True
                await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                    TaskID=taskData.Task.ID,
                    Response=output.encode()
                ))
            else:
                # SWITCH MODE — validate model exists then switch
                new_model = action
                available = await self._get_model_list(provider, api_key)
                if available and new_model not in available:
                    raise Exception(
                        f"Model '{new_model}' not found for {provider}.\n"
                        f"Run 'model' to see available models."
                    )

                new_config = f"Provider:{provider}|Model:{new_model}|APIKey:{api_key}"

                update_resp = await SendMythicRPCCallbackUpdate(
                    MythicRPCCallbackUpdateMessage(
                        CallbackID=taskData.Callback.ID,
                        ExtraInfo=new_config
                    )
                )

                if not update_resp.Success:
                    raise Exception(f"Failed to update model: {update_resp.Error}")

                output = (
                    f"Model switched: {current_model} -> {new_model}\n"
                    f"All future commands will use: {new_model}"
                )

                response.Success = True
                response.TaskStatus = MythicStatus.Completed
                response.Completed = True
                await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                    TaskID=taskData.Task.ID,
                    Response=output.encode()
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

    async def _get_model_list(self, provider, api_key):
        """Return a set of valid model IDs for the provider, or None if validation not possible."""
        try:
            if provider == "OpenAI":
                url = "https://api.openai.com/v1/models"
                headers = {"Authorization": f"Bearer {api_key}"}
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return {m['id'] for m in data.get('data', [])}
            elif provider == "Anthropic":
                return {
                    "claude-opus-4-6", "claude-sonnet-4-6",
                    "claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001",
                    "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022",
                    "claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307",
                }
            elif provider == "Google":
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return {m['name'].split('/')[-1] for m in data.get('models', [])}
        except Exception:
            pass
        return None  # Validation not possible, allow the switch

    async def _list_models(self, provider, api_key):
        if provider == "OpenAI":
            return await self._list_openai(api_key)
        elif provider == "Anthropic":
            return self._list_anthropic()
        elif provider == "Google":
            return await self._list_google(api_key)
        else:
            return f"Unknown provider: {provider}"

    async def _list_openai(self, api_key):
        url = "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    raise Exception(f"OpenAI API error {resp.status}: {await resp.text()}")
                data = await resp.json()
                models = sorted([m['id'] for m in data.get('data', [])
                                 if any(x in m['id'] for x in ['gpt', 'o1', 'o3', 'o4', 'codex'])])
                lines = [f"  {m}" for m in models]
                return f"OpenAI Models ({len(models)}):\n" + "\n".join(lines)

    def _list_anthropic(self):
        models = [
            "claude-opus-4-6",
            "claude-sonnet-4-5-20250929",
            "claude-haiku-4-5-20251001",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ]
        lines = [f"  {m}" for m in models]
        return f"Anthropic Models ({len(models)}):\n" + "\n".join(lines)

    async def _list_google(self, api_key):
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    raise Exception(f"Google API error {resp.status}: {await resp.text()}")
                data = await resp.json()
                models = sorted([m['name'].split('/')[-1] for m in data.get('models', [])
                                 if 'generateContent' in m.get('supportedGenerationMethods', [])])
                lines = [f"  {m}" for m in models]
                return f"Google Models ({len(models)}):\n" + "\n".join(lines)

    async def process_response(self, task: PTTaskMessageAllData, response: any) -> PTTaskProcessResponseMessageResponse:
        return PTTaskProcessResponseMessageResponse(TaskID=task.Task.ID, Success=True)
