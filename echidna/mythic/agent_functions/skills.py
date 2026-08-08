from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *
import aiohttp
import json


TOOLBOX_URL = "http://127.0.0.1:6789"


class SkillsArguments(TaskArguments):
    def __init__(self, command_line, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = []

    async def parse_arguments(self):
        pass


class SkillsCommand(CommandBase):
    cmd = "skills"
    needs_admin = False
    help_cmd = "skills"
    description = "List all available skill agents with their capabilities and requirements"
    version = 1
    author = "@operator"
    argument_class = SkillsArguments
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
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{TOOLBOX_URL}/skills",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        raise Exception(f"Toolbox error: HTTP {resp.status}")
                    data = await resp.json()

            skills = data.get("skills", [])

            if not skills:
                output = "No skills registered in the toolbox.\n"
                output += "Ensure the toolbox container is running with skill definitions in /app/skills/\n"
            else:
                output = f"Available Skills ({len(skills)}):\n"
                output += "=" * 70 + "\n\n"

                for s in skills:
                    flags = []
                    if s.get("requires_proxy"):
                        flags.append("PROXY")
                    if s.get("requires_callback_access"):
                        flags.append("CALLBACK")
                    if s.get("allow_delegate"):
                        flags.append("DELEGATE")
                    flag_str = f" [{', '.join(flags)}]" if flags else ""

                    mitre = ", ".join(s.get("mitre_techniques", [])[:5])
                    mitre_str = f"\n    MITRE: {mitre}" if mitre else ""

                    output += f"  {s['id']}{flag_str}\n"
                    output += f"    {s['description']}{mitre_str}\n\n"

                output += "-" * 70 + "\n"
                output += "Flags: PROXY = needs --callback for SOCKS proxy\n"
                output += "       CALLBACK = needs --callback for command delegation\n"
                output += "       DELEGATE = can execute commands on other implants\n"
                output += "\nUsage: skill --skill <id> [--callback <id>] [--port <port>] <task>\n"

            response.Success = True
            response.TaskStatus = MythicStatus.Completed
            response.Completed = True
            await SendMythicRPCResponseCreate(
                MythicRPCResponseCreateMessage(
                    TaskID=taskData.Task.ID,
                    Response=output.encode()
                )
            )

        except Exception as e:
            response.Success = False
            response.TaskStatus = MythicStatus.Error
            response.Completed = True
            await SendMythicRPCResponseCreate(
                MythicRPCResponseCreateMessage(
                    TaskID=taskData.Task.ID,
                    Response=f"Error listing skills: {str(e)}\nEnsure the toolbox container is running.".encode()
                )
            )

        return response

    async def process_response(self, task: PTTaskMessageAllData, response: any) -> PTTaskProcessResponseMessageResponse:
        resp = PTTaskProcessResponseMessageResponse(TaskID=task.Task.ID, Success=True)
        return resp
