from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *
import aiohttp
import json


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


REPORT_SYSTEM_PROMPT = """You are writing a findings report. Given structured JSON output from skill agents, produce a clear, evidence-backed report in markdown.

## Structure

1. **Summary** — What was targeted, what was found, key risks. Keep it short.

2. **Findings** — Each finding as a subsection:
   - What was found
   - Exact evidence (file paths, hostnames, IPs, credentials, commands, output)
   - MITRE ATT&CK ID if available
   - Severity: Critical / High / Medium / Low / Info

3. **Credentials** — Table of all discovered credentials: type, username, source file, value (redacted if needed).

4. **Attack Paths** — How findings connect. What leads to what.

5. **Recommendations** — What to fix, in priority order.

## Rules
- Cite exact evidence from the data. No generic advice.
- Do NOT invent findings not in the data.
- Do NOT add filler, disclaimers, or marketing language.
- Use tables for structured data.
- Be direct."""


class ReportArguments(TaskArguments):
    def __init__(self, command_line, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="options",
                cli_name="options",
                display_name="Options",
                type=ParameterType.String,
                description="Optional: --format md|html, --title 'Report Title'",
                parameter_group_info=[ParameterGroupInfo(required=False)],
            ),
        ]

    async def parse_arguments(self):
        raw = self.command_line.strip()
        if raw and raw[0] == "{":
            self.load_args_from_json_string(raw)
        else:
            self.add_arg("options", raw)


class ReportCommand(CommandBase):
    cmd = "report"
    needs_admin = False
    help_cmd = "report [--title 'title'] [--format md|html]"
    description = "Generate a findings report from all skill outputs on this callback"
    version = 1
    author = "@operator"
    argument_class = ReportArguments
    attackmapping = []
    attributes = CommandAttributes(
        builtin=True,
        suggested_command=False
    )

    async def create_go_tasking(self, taskData: PTTaskMessageAllData) -> PTTaskCreateTaskingMessageResponse:
        response = PTTaskCreateTaskingMessageResponse(
            TaskID=taskData.Task.ID,
            Success=False,
        )

        try:
            extra_info = taskData.Callback.ExtraInfo
            if not extra_info or extra_info.strip() == "":
                raise Exception("Callback configuration not found.")

            config = _parse_config(extra_info)
            provider = config.get('Provider')
            api_key = config.get('APIKey')
            model = config.get('Model')
            base_url = (config.get('BaseURL') or "").rstrip('/')

            if not provider or not (api_key or base_url):
                raise Exception("Provider and API key required")

            # Parse options
            options = (taskData.args.get_arg("options") or "").strip()
            report_format = "md"
            title = "Findings Report"

            tokens = options.split() if options else []
            i = 0
            while i < len(tokens):
                if tokens[i] == "--format" and i + 1 < len(tokens):
                    report_format = tokens[i + 1].lower()
                    i += 2
                elif tokens[i] == "--title" and i + 1 < len(tokens):
                    # Collect quoted title
                    title_parts = []
                    i += 1
                    while i < len(tokens):
                        title_parts.append(tokens[i].strip("'\""))
                        if tokens[i].endswith("'") or tokens[i].endswith('"'):
                            break
                        i += 1
                    title = " ".join(title_parts)
                    i += 1
                else:
                    i += 1

            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=f"[report] Collecting skill findings from this callback...\n".encode()
            ))

            # Search for all completed skill tasks on this callback
            skill_outputs = await self._collect_skill_outputs(taskData)

            if not skill_outputs:
                raise Exception("No skill outputs found on this callback. Run some skills first.")

            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=f"[report] Found {len(skill_outputs)} skill outputs. Generating report...\n".encode()
            ))

            # Build the LLM prompt
            findings_json = json.dumps(skill_outputs, indent=2)
            prompt = (
                f"{REPORT_SYSTEM_PROMPT}\n\n"
                f"## Report Title: {title}\n\n"
                f"## Format: {report_format}\n\n"
            )
            if report_format == "html":
                prompt += "Wrap the output in proper HTML with inline CSS styling for a clean, printable report.\n\n"
            prompt += (
                f"## Skill Findings Data\n\n"
                f"```json\n{findings_json}\n```\n\n"
                f"Generate the full report now."
            )

            # Call the LLM
            if provider in ("OpenAI", "Custom"):
                # Custom has no Responses protocol — report over plain chat/completions.
                report_text = (await self._call_openai_compatible(base_url, api_key, model, prompt)
                               if provider == "Custom"
                               else await self._call_openai(api_key, model, prompt, base_url))
            elif provider == "Anthropic":
                report_text = await self._call_anthropic(api_key, model, prompt, base_url)
            elif provider == "Google":
                report_text = await self._call_google(api_key, model, prompt)
            else:
                raise Exception(f"Unknown provider: {provider}")

            # Upload as file
            ext = "html" if report_format == "html" else "md"
            filename = f"pentest_report.{ext}"
            file_resp = await SendMythicRPCFileCreate(MythicRPCFileCreateMessage(
                TaskID=taskData.Task.ID,
                FileContents=report_text.encode(),
                Filename=filename,
                Comment=f"Findings report from {len(skill_outputs)} skill outputs",
                DeleteAfterFetch=False,
            ))

            # Also render in task output
            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=f"\n{report_text}\n".encode()
            ))

            if file_resp.Success:
                await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                    TaskID=taskData.Task.ID,
                    Response=f"\n📎 Report saved: {filename} (Files tab)\n".encode()
                ))

            response.Success = True
            response.TaskStatus = MythicStatus.Completed
            response.Completed = True

        except Exception as e:
            response.Success = False
            response.TaskStatus = MythicStatus.Error
            response.Completed = True
            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=f"[report] Error: {str(e)}".encode()
            ))

        return response

    async def _collect_skill_outputs(self, taskData):
        """Collect skill outputs using two methods: uploaded files and [SKILL_RESULT] markers."""
        outputs = []
        seen_skills = set()

        # Method 1: Search for *_output.json files uploaded by skills
        # This works for ALL skill runs, including those before [SKILL_RESULT] was added
        try:
            file_search = await SendMythicRPCFileSearch(
                MythicRPCFileSearchMessage(
                    TaskID=taskData.Task.ID,
                    Filename="_output.json",
                    LimitByCallback=False,
                )
            )
            if file_search.Success and file_search.Files:
                for f in file_search.Files:
                    if f.Filename and f.Filename.endswith("_output.json"):
                        try:
                            content_resp = await SendMythicRPCFileGetContent(
                                MythicRPCFileGetContentMessage(AgentFileId=f.AgentFileId)
                            )
                            if content_resp.Success and content_resp.Content:
                                raw = content_resp.Content
                                text = raw.decode() if isinstance(raw, bytes) else str(raw)
                                output = json.loads(text)
                                skill_id = output.get("skill", f.Filename.replace("_output.json", ""))
                                if skill_id not in seen_skills:
                                    seen_skills.add(skill_id)
                                    outputs.append(output)
                        except Exception:
                            continue
        except Exception:
            pass

        # Method 2: Fall back to [SKILL_RESULT] markers in task responses
        try:
            search_resp = await SendMythicRPCTaskSearch(
                MythicRPCTaskSearchMessage(
                    TaskID=taskData.Task.ID,
                    SearchCallbackID=taskData.Callback.ID,
                    SearchCommandNames=["skill"],
                    SearchCompleted=True,
                )
            )
            if search_resp.Success and search_resp.Tasks:
                for task in search_resp.Tasks:
                    try:
                        resp_search = await SendMythicRPCResponseSearch(
                            MythicRPCResponseSearchMessage(TaskID=task.ID)
                        )
                        if not resp_search.Success or not resp_search.Responses:
                            continue
                        for r in resp_search.Responses:
                            text = r.Response.decode() if isinstance(r.Response, bytes) else str(r.Response)
                            if "[SKILL_RESULT]" in text:
                                for line in text.split("\n"):
                                    line = line.strip()
                                    if line.startswith("[SKILL_RESULT]"):
                                        try:
                                            output = json.loads(line[len("[SKILL_RESULT]"):])
                                            skill_id = output.get("skill", "")
                                            if skill_id and skill_id not in seen_skills:
                                                seen_skills.add(skill_id)
                                                outputs.append(output)
                                        except (json.JSONDecodeError, IndexError):
                                            pass
                                        break
                                break
                    except Exception:
                        continue
        except Exception:
            pass

        return outputs

    async def _call_openai(self, api_key, model, prompt, base_url=""):
        url = (f"{base_url.rstrip('/')}/responses" if base_url
               else "https://api.openai.com/v1/responses")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"model": model or "gpt-5", "input": prompt}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    raise Exception(f"OpenAI API error {resp.status}: {await resp.text()}")
                data = await resp.json()
                if 'output' in data:
                    for item in data['output']:
                        if item.get('type') == 'message':
                            for content in item.get('content', []):
                                if content.get('type') == 'output_text':
                                    return content.get('text', '')
                    return str(data['output'])
                return str(data)

    async def _call_openai_compatible(self, base_url, api_key, model, prompt):
        """Plain /chat/completions — for Custom endpoints with no Responses protocol."""
        base = (base_url or "").rstrip('/')
        headers = {"Authorization": f"Bearer {api_key or 'not-needed'}",
                   "Content-Type": "application/json"}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            if not model:
                async with session.get(f"{base}/models", headers=headers) as resp:
                    if resp.status != 200:
                        raise Exception(f"No model set and {base}/models returned {resp.status}")
                    ids = [m.get('id') for m in (await resp.json()).get('data', []) if m.get('id')]
                    if not ids:
                        raise Exception(f"No model set and {base}/models listed none")
                    model = ids[0]
            payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
            async with session.post(f"{base}/chat/completions", headers=headers, json=payload) as resp:
                if resp.status != 200:
                    raise Exception(f"Endpoint error {resp.status}: {(await resp.text())[:500]}")
                data = await resp.json()
        try:
            return data['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError):
            return str(data)

    async def _call_anthropic(self, api_key, model, prompt, base_url=""):
        root = (base_url or "").rstrip('/')
        if root.endswith('/v1'):
            root = root[:-3].rstrip('/')
        url = f"{root}/v1/messages" if root else "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        payload = {
            "model": model or "claude-sonnet-4-5-20250929",
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt}]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    raise Exception(f"Anthropic API error {resp.status}: {await resp.text()}")
                data = await resp.json()
                return data.get("content", [{}])[0].get("text", str(data))

    async def _call_google(self, api_key, model, prompt):
        model = model or "gemini-2.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    raise Exception(f"Google API error {resp.status}: {await resp.text()}")
                data = await resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", str(data))
                return str(data)

    async def process_response(self, task: PTTaskMessageAllData, response: any) -> PTTaskProcessResponseMessageResponse:
        return PTTaskProcessResponseMessageResponse(TaskID=task.Task.ID, Success=True)
