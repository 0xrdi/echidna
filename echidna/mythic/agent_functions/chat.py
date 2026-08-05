from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *
import aiohttp
import json


def _anthropic_root(base_url: str) -> str:
    """Strip a trailing /v1 so the Anthropic path can be appended cleanly.

    base_url is carried OpenAI-style (…/v1) because that is what
    `infreerence integrations` emits, but the Anthropic wire is /v1/messages off
    the ROOT — without this you get /v1/v1/messages.
    """
    root = (base_url or "").rstrip('/')
    if root.endswith('/v1'):
        root = root[:-3].rstrip('/')
    return root


class ChatArguments(TaskArguments):
    def __init__(self, command_line, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="message",
                cli_name="message",
                display_name="Message",
                type=ParameterType.String,
                description="Message to send to the LLM",
                parameter_group_info=[
                    ParameterGroupInfo(
                        required=True,
                    ),
                ],
            ),
        ]

    async def parse_arguments(self):
        if len(self.command_line.strip()) == 0:
            raise Exception("Chat requires a message parameter")

        # Support both JSON and plain text
        if self.command_line[0] == "{":
            self.load_args_from_json_string(self.command_line)
        else:
            # Plain text - treat entire command line as message
            self.add_arg("message", self.command_line)


class ChatCommand(CommandBase):
    cmd = "chat"
    needs_admin = False
    help_cmd = "chat <message>"
    description = "Send a message to the configured LLM provider and receive response"
    version = 1
    author = "@operator"
    argument_class = ChatArguments
    attackmapping = []
    attributes = CommandAttributes(
        builtin=True,
        suggested_command=True
    )

    async def create_go_tasking(self, taskData: PTTaskMessageAllData) -> PTTaskCreateTaskingMessageResponse:
        """Execute LLM API call and return response"""
        response = PTTaskCreateTaskingMessageResponse(
            TaskID=taskData.Task.ID,
            Success=False,
        )

        try:
            # Extract configuration from callback ExtraInfo (not Description, which Mythic overwrites)
            extra_info = taskData.Callback.ExtraInfo

            # Debug: log what we received
            if not extra_info or extra_info.strip() == "":
                raise Exception("Callback configuration not found. Please rebuild the payload.")

            config_parts = extra_info.split('|')
            config = {}
            for part in config_parts:
                if ':' not in part:
                    continue
                key, value = part.split(':', 1)
                config[key] = value

            provider = config.get('Provider')
            model = config.get('Model')
            api_key = config.get('APIKey')
            base_url = (config.get('BaseURL') or "").rstrip('/')
            message = taskData.args.get_arg("message")

            # Validate we got all required config. Custom endpoints need a base
            # URL instead of a key — most self-hosted servers are unauthenticated.
            if not provider:
                raise Exception(f"Provider not found in callback config. Got: {extra_info[:100]}")
            if provider == "Custom":
                if not base_url:
                    raise Exception("BaseURL not found in callback config. Please rebuild the payload.")
            elif not api_key:
                raise Exception("API key not found in callback config")
            if not message:
                raise Exception("Message is required")

            # Route to appropriate provider
            if provider == "OpenAI":
                llm_response = await self._call_openai(api_key, model, message, base_url)
            elif provider == "Anthropic":
                llm_response = await self._call_anthropic(api_key, model, message, base_url)
            elif provider == "Google":
                llm_response = await self._call_google(api_key, model, message)
            elif provider == "Kimi":
                llm_response = await self._call_kimi(api_key, model, message)
            elif provider == "Custom":
                llm_response = await self._call_openai_compatible(base_url, api_key, model, message)
            else:
                raise Exception(f"Unknown provider: {provider}")

            # Return response immediately (virtual agent completes instantly)
            response.Success = True
            response.TaskStatus = MythicStatus.Completed
            response.Completed = True
            await SendMythicRPCResponseCreate(
                MythicRPCResponseCreateMessage(
                    TaskID=taskData.Task.ID,
                    Response=llm_response.encode()
                )
            )

        except Exception as e:
            response.Success = False
            response.TaskStatus = MythicStatus.Error
            response.Completed = True
            error_msg = f"Error calling LLM: {str(e)}"
            await SendMythicRPCResponseCreate(
                MythicRPCResponseCreateMessage(
                    TaskID=taskData.Task.ID,
                    Response=error_msg.encode()
                )
            )

        return response

    async def _call_openai(self, api_key: str, model: str, message: str, base_url: str = "") -> str:
        """Call the OpenAI Responses API, or a bridge serving that wire.

        base_url carries /v1, so /responses is appended directly.
        """
        url = (f"{base_url.rstrip('/')}/responses" if base_url
               else "https://api.openai.com/v1/responses")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "input": message
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(f"OpenAI API error {resp.status}: {error_text}")

                data = await resp.json()
                # Extract response from Responses API format
                if 'output' in data and 'response' in data['output']:
                    return data['output']['response']
                elif 'output' in data:
                    return str(data['output'])
                else:
                    return str(data)

    async def _call_anthropic(self, api_key: str, model: str, message: str, base_url: str = "") -> str:
        """Call the Anthropic Messages API, or a bridge serving that wire."""
        url = (f"{_anthropic_root(base_url)}/v1/messages" if base_url
               else "https://api.anthropic.com/v1/messages")
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": [
                {"role": "user", "content": message}
            ]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(f"Anthropic API error {resp.status}: {error_text}")

                data = await resp.json()
                # Extract text from first content block
                return data['content'][0]['text']

    async def _call_google(self, api_key: str, model: str, message: str) -> str:
        """Call Google Gemini generateContent API"""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        headers = {
            "Content-Type": "application/json"
        }
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": message}]
                }
            ]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(f"Google API error {resp.status}: {error_text}")

                data = await resp.json()
                # Extract text from first candidate
                return data['candidates'][0]['content']['parts'][0]['text']

    async def _call_kimi(self, api_key: str, model: str, message: str) -> str:
        """Call Moonshot AI (Kimi) via their OpenAI-compatible endpoint."""
        return await self._call_openai_compatible(
            "https://api.moonshot.ai/v1", api_key, model, message)

    async def _call_openai_compatible(self, base_url: str, api_key: str, model: str, message: str) -> str:
        """Call any OpenAI-compatible /chat/completions endpoint.

        This is the surface `infreerence integrations` emits (vLLM, Ollama, LiteLLM,
        LocalAI, LM Studio, ...). Deliberately NOT the Responses API used by
        _call_openai: /v1/responses is OpenAI-proper and virtually no self-hosted
        server implements it, whereas /chat/completions is universal — so no
        translating proxy is needed for this path. Self-hosted servers usually
        ignore the key, and when no model is pinned the first one advertised wins.
        """
        headers = {
            "Authorization": f"Bearer {api_key or 'not-needed'}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            if not model or model.strip() == "":
                async with session.get(f"{base_url}/models", headers=headers) as resp:
                    if resp.status != 200:
                        raise Exception(
                            f"No model configured and {base_url}/models returned "
                            f"{resp.status}: {(await resp.text())[:200]}"
                        )
                    listing = await resp.json()
                    ids = [m.get('id') for m in listing.get('data', []) if m.get('id')]
                    if not ids:
                        raise Exception(f"No model configured and {base_url}/models listed none")
                    model = ids[0]

            payload = {"model": model, "messages": [{"role": "user", "content": message}]}
            async with session.post(f"{base_url}/chat/completions", headers=headers, json=payload) as resp:
                if resp.status != 200:
                    raise Exception(f"Endpoint error {resp.status}: {(await resp.text())[:500]}")
                data = await resp.json()

        try:
            return data['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError):
            return str(data)

    async def process_response(self, task: PTTaskMessageAllData, response: any) -> PTTaskProcessResponseMessageResponse:
        """Not needed for virtual agent - all processing in create_go_tasking"""
        resp = PTTaskProcessResponseMessageResponse(TaskID=task.Task.ID, Success=True)
        return resp
