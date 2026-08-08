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
    if not extra_info or extra_info.strip() == "":
        return {}
    try:
        return json.loads(extra_info)
    except (json.JSONDecodeError, TypeError):
        pass
    config = {}
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
            base_url = (config.get('BaseURL') or "").rstrip('/')
            current_model = config.get('Model', 'default')

            if not provider:
                raise Exception("Provider not found in callback config")
            if provider == "Custom":
                if not base_url:
                    raise Exception("BaseURL not found in callback config")
            elif not api_key:
                raise Exception("API key not found in callback config")

            action = (taskData.args.get_arg("action") or "").strip()

            # Strip "use " prefix if present for backwards compat
            if action.lower().startswith("use "):
                action = action[4:].strip()

            if not action:
                # LIST MODE — no args, list available models
                output = await self._list_models(provider, api_key, base_url)
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
                available = await self._get_model_list(provider, api_key, base_url)
                if available and new_model not in available:
                    raise Exception(
                        f"Model '{new_model}' not found for {provider}.\n"
                        f"Run 'model' to see available models."
                    )

                # Rewrite only Model and keep every other key. Rebuilding the
                # string from scratch used to drop BaseURL's companions — the
                # protocol key (which gates skills), plus IsSubAgent / DelegateSession /
                # SocksPort on a sub-agent callback.
                config['Model'] = new_model
                new_config = json.dumps(config)

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

    async def _get_model_list(self, provider, api_key, base_url=""):
        """Return a set of valid model IDs for the provider, or None if validation not possible."""
        try:
            if provider == "Custom":
                return await self._custom_model_ids(base_url, api_key)
            if provider == "OpenAI":
                url = (f"{base_url.rstrip('/')}/models" if base_url
                       else "https://api.openai.com/v1/models")
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
            elif provider == "Kimi":
                url = "https://api.moonshot.ai/v1/models"
                headers = {"Authorization": f"Bearer {api_key}"}
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return {m['id'] for m in data.get('data', [])}
        except Exception:
            pass
        return None  # Validation not possible, allow the switch

    async def _list_models(self, provider, api_key, base_url=""):
        if provider == "OpenAI":
            return await self._list_openai(api_key, base_url)
        elif provider == "Anthropic":
            return self._list_anthropic()
        elif provider == "Google":
            return await self._list_google(api_key)
        elif provider == "Kimi":
            return await self._list_kimi(api_key)
        elif provider == "Custom":
            return await self._list_custom(base_url, api_key)
        else:
            return f"Unknown provider: {provider}"

    async def _custom_model_ids(self, base_url, api_key):
        """Model IDs advertised by an OpenAI-compatible endpoint, or None.

        Never raises: an unreachable or non-conforming endpoint yields None, so the
        caller falls back to allowing the switch rather than blocking on a server
        that simply has no /models route.
        """
        if not base_url:
            return None
        headers = {"Authorization": f"Bearer {api_key or 'not-needed'}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{base_url}/models", headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    ids = {m['id'] for m in data.get('data', []) if m.get('id')}
                    return ids or None
        except Exception:
            return None

    async def _list_custom(self, base_url, api_key):
        headers = {"Authorization": f"Bearer {api_key or 'not-needed'}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/models", headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    raise Exception(f"Endpoint error {resp.status}: {(await resp.text())[:300]}")
                data = await resp.json()
        models = sorted(m['id'] for m in data.get('data', []) if m.get('id'))
        if not models:
            return f"{base_url} listed no models"
        lines = [f"  {m}" for m in models]
        return f"Models at {base_url} ({len(models)}):\n" + "\n".join(lines)

    async def _list_openai(self, api_key, base_url=""):
        url = (f"{base_url.rstrip('/')}/models" if base_url
               else "https://api.openai.com/v1/models")
        headers = {"Authorization": f"Bearer {api_key}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    raise Exception(f"OpenAI API error {resp.status}: {await resp.text()}")
                data = await resp.json()
                ids = [m['id'] for m in data.get('data', []) if m.get('id')]
                # The OpenAI-name filter only makes sense against OpenAI proper; a
                # bridge fronts arbitrary names (claude-*, deepseek-*, ...).
                if not base_url:
                    ids = [i for i in ids if any(x in i for x in ['gpt', 'o1', 'o3', 'o4', 'codex'])]
                models = sorted(ids)
                lines = [f"  {m}" for m in models]
                label = f"Models at {base_url}" if base_url else "OpenAI Models"
                return f"{label} ({len(models)}):\n" + "\n".join(lines)

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

    async def _list_kimi(self, api_key):
        url = "https://api.moonshot.ai/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    raise Exception(f"Kimi API error {resp.status}: {await resp.text()}")
                data = await resp.json()
                models = sorted(m['id'] for m in data.get('data', []) if m.get('id'))
                lines = [f"  {m}" for m in models]
                return f"Kimi Models ({len(models)}):\n" + "\n".join(lines)

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
