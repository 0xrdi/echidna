from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *
import aiohttp
import json


TOOLBOX_URL = "http://127.0.0.1:6789"


class JobsArguments(TaskArguments):
    def __init__(self, command_line, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="action",
                cli_name="action",
                display_name="Action",
                type=ParameterType.String,
                description="No args = list jobs. 'kill <job_id>' to stop a running job.",
                parameter_group_info=[ParameterGroupInfo(required=False)],
            ),
        ]

    async def parse_arguments(self):
        raw = self.command_line.strip()
        if raw and raw[0] == "{":
            self.load_args_from_json_string(raw)
        else:
            self.add_arg("action", raw)


class JobsCommand(CommandBase):
    cmd = "jobs"
    needs_admin = False
    help_cmd = "jobs [stop <job_id>]"
    description = "List active skill jobs or stop a running one"
    version = 1
    author = "@operator"
    argument_class = JobsArguments
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
            action = (taskData.args.get_arg("action") or "").strip()

            # Parse: "stop <job_id>" or empty
            job_id = None
            if action.lower().startswith("stop "):
                job_id = action[5:].strip()

            async with aiohttp.ClientSession() as session:
                if not job_id:
                    # LIST MODE
                    async with session.get(
                        f"{TOOLBOX_URL}/jobs",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status != 200:
                            raise Exception(f"Toolbox error: {await resp.text()}")
                        data = await resp.json()

                    jobs = data.get("jobs", [])
                    if not jobs:
                        output = "No active jobs."
                    else:
                        output = f"Active Jobs ({len(jobs)}):\n"
                        output += f"  {'Job ID':<20} {'Skill':<25} {'Elapsed':<10}\n"
                        output += f"  {'-'*20} {'-'*25} {'-'*10}\n"
                        for j in jobs:
                            elapsed = j.get("elapsed_seconds", 0)
                            mins, secs = divmod(elapsed, 60)
                            output += f"  {j['job_id']:<20} {j['skill_id']:<25} {mins}m{secs:02d}s\n"
                        output += f"\nTo stop a job: jobs stop <job_id>"

                else:
                    # KILL MODE
                    async with session.post(
                        f"{TOOLBOX_URL}/stop",
                        json={"job_id": job_id},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        data = await resp.json()
                        if resp.status == 404:
                            raise Exception(data.get("error", f"Job '{job_id}' not found"))
                        if not data.get("success"):
                            raise Exception(data.get("error", "Unknown error"))

                    output = f"Stopped job {job_id} (skill: {data.get('skill_id', 'unknown')})"

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

    async def process_response(self, task: PTTaskMessageAllData, response: any) -> PTTaskProcessResponseMessageResponse:
        return PTTaskProcessResponseMessageResponse(TaskID=task.Task.ID, Success=True)
