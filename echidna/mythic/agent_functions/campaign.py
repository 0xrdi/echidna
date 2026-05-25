from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *
import json
import asyncio


TOOLBOX_URL = "http://127.0.0.1:6789"

# Default skill chain order when no specific starting skill is given
DEFAULT_CHAIN = [
    "passive-recon",
    "active-recon",
    "attack-surface-analyzer",
    "exploitation-planner",
    "post-exploitation",
    "credential-validation",
    "cloud-enumeration",
    "privilege-escalation",
    "lateral-movement",
]

# Skills that require a callback (delegation-based)
DELEGATION_SKILLS = {
    "post-exploitation", "privilege-escalation", "credential-validation",
    "persistence", "edr-bypass", "lateral-movement", "data-exfil",
    "exploitation-executor",
}

# Skills that can run without a callback
STANDALONE_SKILLS = {
    "passive-recon", "active-recon", "attack-surface-analyzer",
    "exploitation-planner", "cloud-enumeration",
}

# Max skills in a single campaign to prevent runaway
MAX_SKILLS_PER_CAMPAIGN = 10

# Skills that always require operator approval before execution
REQUIRES_APPROVAL = {
    "privilege-escalation", "persistence", "lateral-movement",
    "data-exfil", "exploitation-executor",
}

# Module-level campaign state, keyed by callback agent ID, so --resume can pick it up
# Each entry: {
#   "queue": [...],
#   "completed": [...],
#   "context": str,
#   "objective": str,
#   "callback_id": str,
#   "port": str,
#   "auto": bool,
#   "paused_skill": str | None,
#   "task_id": int,
# }
_campaign_state: dict = {}


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


class CampaignArguments(TaskArguments):
    def __init__(self, command_line, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="raw_input",
                cli_name="raw_input",
                display_name="Campaign Command",
                type=ParameterType.String,
                description="Format: [--auto] [--resume] [--skip] [--callback <id>] [--port <port>] [--start <skill_id>] <target and objective>",
                parameter_group_info=[ParameterGroupInfo(required=True)],
            ),
        ]

    async def parse_arguments(self):
        if len(self.command_line.strip()) == 0:
            raise Exception("Usage: campaign [--auto] [--resume] [--skip] [--callback <id>] [--port <port>] [--start <skill_id>] <target and objective>")

        raw = self.command_line.strip()

        if raw[0] == "{":
            import json as _json
            try:
                data = _json.loads(raw)
            except _json.JSONDecodeError:
                raise Exception(f"Invalid JSON: {raw[:200]}")
            if "raw_input" in data:
                raw = data["raw_input"]
            else:
                self.add_arg("raw_input", raw)
                return

        tokens = raw.split()
        callback_id = None
        port = None
        start_skill = None
        auto_mode = False
        resume_mode = False
        skip_mode = False
        task_parts = []

        i = 0
        while i < len(tokens):
            if tokens[i] == "--callback" and i + 1 < len(tokens):
                callback_id = tokens[i + 1]
                i += 2
            elif tokens[i] == "--port" and i + 1 < len(tokens):
                port = tokens[i + 1]
                i += 2
            elif tokens[i] == "--start" and i + 1 < len(tokens):
                start_skill = tokens[i + 1]
                i += 2
            elif tokens[i] == "--auto":
                auto_mode = True
                i += 1
            elif tokens[i] == "--resume":
                resume_mode = True
                i += 1
            elif tokens[i] == "--skip":
                skip_mode = True
                i += 1
            else:
                task_parts.append(tokens[i])
                i += 1

        # --resume and --skip don't require a task description
        if not task_parts and not resume_mode and not skip_mode:
            raise Exception("Target and objective description required")

        packed = {
            "task": " ".join(task_parts),
            "callback": callback_id or "",
            "port": port or "7000",
            "start_skill": start_skill or "",
            "auto": auto_mode,
            "resume": resume_mode,
            "skip": skip_mode,
        }
        self.add_arg("raw_input", json.dumps(packed))


class CampaignCommand(CommandBase):
    cmd = "campaign"
    needs_admin = False
    help_cmd = "campaign [--auto] [--resume] [--skip] [--callback <id>] [--port <port>] [--start <skill_id>] <target and objective>"
    description = "Run an automated skill chain against a target, following recommendations from each skill"
    version = 1
    author = "@operator"
    argument_class = CampaignArguments
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
                raise Exception("Callback configuration not found. Please rebuild the payload.")

            config = _parse_config(extra_info)
            provider = config.get('Provider')
            api_key = config.get('APIKey')
            model = config.get('Model')

            if not provider or not api_key:
                raise Exception("Provider and API key required")

            if provider == "Google":
                raise Exception("Campaign is not supported for Google provider.")

            # Parse campaign args
            raw_input = taskData.args.get_arg("raw_input")
            try:
                params = json.loads(raw_input)
            except json.JSONDecodeError:
                params = {"task": raw_input}

            objective = params.get("task", "")
            callback_id = params.get("callback", "")
            port = params.get("port", "7000")
            start_skill = params.get("start_skill", "")
            auto_mode = params.get("auto", False)
            resume_mode = params.get("resume", False)
            skip_mode = params.get("skip", False)

            agent_callback_id = taskData.Callback.AgentCallbackID

            # ── Handle --resume: pick up a paused campaign ──
            if resume_mode:
                state = _campaign_state.get(agent_callback_id)
                if not state or not state.get("paused_skill"):
                    raise Exception("No paused campaign to resume on this callback.")
                # Restore state and continue with the paused skill
                objective = state["objective"]
                callback_id = state["callback_id"]
                port = state["port"]
                auto_mode = state["auto"]
                skill_queue = [state["paused_skill"]] + state["queue"]
                completed_skills = state["completed"]
                campaign_context = state["context"]
                skill_count = len(completed_skills)
                state["paused_skill"] = None

                await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                    TaskID=taskData.Task.ID,
                    Response=f"\n[campaign] Resuming — next skill: {skill_queue[0]}\n".encode()
                ))

            # ── Handle --skip: skip the paused skill and continue ──
            elif skip_mode:
                state = _campaign_state.get(agent_callback_id)
                if not state or not state.get("paused_skill"):
                    raise Exception("No paused campaign to skip on this callback.")
                skipped = state["paused_skill"]
                objective = state["objective"]
                callback_id = state["callback_id"]
                port = state["port"]
                auto_mode = state["auto"]
                skill_queue = list(state["queue"])
                completed_skills = state["completed"]
                campaign_context = state["context"]
                skill_count = len(completed_skills)
                state["paused_skill"] = None

                await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                    TaskID=taskData.Task.ID,
                    Response=f"\n[campaign] Skipping '{skipped}' — continuing with queue: {', '.join(skill_queue) or '(empty)'}\n".encode()
                ))

            # ── Normal start ──
            else:
                if not objective:
                    raise Exception("Target and objective required")

                # Determine starting skill
                if start_skill:
                    skill_queue = [start_skill]
                elif callback_id:
                    skill_queue = ["post-exploitation"]
                else:
                    skill_queue = ["passive-recon"]

                completed_skills = []
                campaign_context = ""
                skill_count = 0

                # Header
                mode_label = "AUTO" if auto_mode else "INTERACTIVE (use --resume / --skip to continue)"
                await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                    TaskID=taskData.Task.ID,
                    Response=(
                        f"{'='*60}\n"
                        f"  CAMPAIGN STARTED\n"
                        f"  Objective: {objective[:200]}\n"
                        f"  Callback: {'#' + callback_id if callback_id else 'None (standalone skills only)'}\n"
                        f"  Starting skill: {skill_queue[0]}\n"
                        f"  Mode: {mode_label}\n"
                        f"{'='*60}\n\n"
                    ).encode()
                ))

            # Campaign loop
            while skill_queue and skill_count < MAX_SKILLS_PER_CAMPAIGN:
                next_skill = skill_queue.pop(0)

                # Skip if already run
                if next_skill in completed_skills:
                    continue

                # Check if skill needs callback and we don't have one
                if next_skill in DELEGATION_SKILLS and not callback_id:
                    await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                        TaskID=taskData.Task.ID,
                        Response=f"\n[campaign] Skipping '{next_skill}' — requires --callback\n".encode()
                    ))
                    continue

                # ── Approval gate ──
                # In interactive mode (no --auto): pause before EVERY skill.
                # In auto mode: pause only before dangerous skills.
                needs_pause = False
                if not auto_mode:
                    needs_pause = True
                elif next_skill in REQUIRES_APPROVAL:
                    needs_pause = True

                if needs_pause:
                    # Save state so --resume / --skip can pick it up
                    _campaign_state[agent_callback_id] = {
                        "queue": list(skill_queue),
                        "completed": list(completed_skills),
                        "context": campaign_context,
                        "objective": objective,
                        "callback_id": callback_id,
                        "port": port,
                        "auto": auto_mode,
                        "paused_skill": next_skill,
                        "task_id": taskData.Task.ID,
                    }

                    danger_note = " (DANGEROUS — requires approval)" if next_skill in REQUIRES_APPROVAL else ""
                    await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                        TaskID=taskData.Task.ID,
                        Response=(
                            f"\n[campaign] PAUSED — Next skill: {next_skill}{danger_note}\n"
                            f"[campaign] Queue after: {', '.join(skill_queue) or '(none)'}\n"
                            f"[campaign] Run 'campaign --resume' to continue or 'campaign --skip' to skip.\n"
                        ).encode()
                    ))

                    response.Success = True
                    response.TaskStatus = MythicStatus.Completed
                    response.Completed = True
                    return response

                skill_count += 1

                await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                    TaskID=taskData.Task.ID,
                    Response=(
                        f"\n{'─'*60}\n"
                        f"  SKILL {skill_count}: {next_skill}\n"
                        f"{'─'*60}\n"
                    ).encode()
                ))

                # Build skill command
                skill_cmd_parts = [next_skill]
                if callback_id and next_skill in DELEGATION_SKILLS:
                    skill_cmd_parts.extend(["--callback", callback_id, "--port", port])
                if campaign_context:
                    skill_cmd_parts.extend(["--context", campaign_context])
                skill_cmd_parts.append(objective)

                skill_params = json.dumps({
                    "skill_id": next_skill,
                    "task": objective,
                    "callback": callback_id if next_skill in DELEGATION_SKILLS else "",
                    "port": port,
                    "context": campaign_context,
                })

                # Create skill task on THIS callback (not a child)
                task_resp = await SendMythicRPCTaskCreate(
                    MythicRPCTaskCreateMessage(
                        AgentCallbackID=taskData.Callback.AgentCallbackID,
                        CommandName="skill",
                        Params=skill_params,
                    )
                )

                if not task_resp.Success:
                    await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                        TaskID=taskData.Task.ID,
                        Response=f"\n[campaign] Failed to start '{next_skill}': {task_resp.Error}\n".encode()
                    ))
                    continue

                child_task_id = task_resp.TaskID

                # Wait for skill to complete (up to 20 min per skill)
                skill_output = await self._wait_and_collect(taskData, child_task_id)

                completed_skills.append(next_skill)

                if skill_output:
                    # Update campaign context with this skill's output
                    campaign_context = json.dumps(skill_output)

                    # Extract recommendations for next skills
                    recommendations = skill_output.get("recommendations", [])
                    if recommendations:
                        rec_summary = []
                        for rec in recommendations:
                            rec_skill = rec.get("next_skill", "")
                            priority = rec.get("priority", "medium")
                            rationale = rec.get("rationale", "")
                            if rec_skill and rec_skill not in completed_skills and rec_skill not in skill_queue:
                                skill_queue.append(rec_skill)
                                rec_summary.append(f"  [{priority.upper():6s}] {rec_skill} — {rationale[:100]}")

                        if rec_summary:
                            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                                TaskID=taskData.Task.ID,
                                Response=(
                                    f"\n[campaign] Recommendations from {next_skill}:\n"
                                    + "\n".join(rec_summary) + "\n"
                                    f"[campaign] Queue: {', '.join(skill_queue)}\n"
                                ).encode()
                            ))
                    else:
                        await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                            TaskID=taskData.Task.ID,
                            Response=f"\n[campaign] No recommendations from {next_skill}. Chain complete.\n".encode()
                        ))
                else:
                    await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                        TaskID=taskData.Task.ID,
                        Response=f"\n[campaign] No structured output from {next_skill}.\n".encode()
                    ))

            # Clean up campaign state on completion
            _campaign_state.pop(agent_callback_id, None)

            # Summary
            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=(
                    f"\n{'='*60}\n"
                    f"  CAMPAIGN COMPLETE\n"
                    f"  Skills executed: {', '.join(completed_skills)}\n"
                    f"  Total: {len(completed_skills)} skills\n"
                    f"{'='*60}\n"
                ).encode()
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
                Response=f"[campaign] Error: {str(e)}".encode()
            ))

        return response

    async def _wait_and_collect(self, taskData, child_task_id):
        """Wait for skill task to complete and try to extract its structured output."""
        # Wait up to 20 minutes
        for _ in range(240):  # 240 * 5s = 20 min
            await asyncio.sleep(5)
            search_resp = await SendMythicRPCTaskSearch(
                MythicRPCTaskSearchMessage(
                    TaskID=taskData.Task.ID,
                    SearchTaskID=child_task_id
                )
            )
            if search_resp.Success and search_resp.Tasks and search_resp.Tasks[0].Completed:
                break
        else:
            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=f"\n[campaign] Warning: skill task timed out after 20 minutes\n".encode()
            ))
            return None

        # Search task responses for [SKILL_RESULT] marker
        # The skill command writes this in both sub-agent mode and propagates it in parent mode
        try:
            resp_search = await SendMythicRPCResponseSearch(
                MythicRPCResponseSearchMessage(
                    TaskID=child_task_id
                )
            )
            if resp_search.Success and resp_search.Responses:
                for r in resp_search.Responses:
                    text = r.Response.decode() if isinstance(r.Response, bytes) else str(r.Response)
                    if "[SKILL_RESULT]" in text:
                        for line in text.split("\n"):
                            line = line.strip()
                            if line.startswith("[SKILL_RESULT]"):
                                try:
                                    json_str = line[len("[SKILL_RESULT]"):]
                                    return json.loads(json_str)
                                except (json.JSONDecodeError, IndexError):
                                    pass
        except Exception:
            pass

        return None

    async def process_response(self, task: PTTaskMessageAllData, response: any) -> PTTaskProcessResponseMessageResponse:
        resp = PTTaskProcessResponseMessageResponse(TaskID=task.Task.ID, Success=True)
        return resp
