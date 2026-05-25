from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *
from mythic_container.MythicGoRPC.send_mythic_rpc_task_create import MythicRPCTaskCreateMessage as _OrigTaskCreateMessage
import mythic_container
import aiohttp
import json
import os
import uuid


TOOLBOX_URL = "http://127.0.0.1:6789"
MYTHIC_SERVER_HOST = os.environ.get("MYTHIC_SERVER_HOST", "mythic_server")
MYTHIC_SERVER_PORT = os.environ.get("MYTHIC_SERVER_PORT", "17443")
DELEGATE_SERVER_PORT = 6790


class _TaskCreateMessageWithTaskID(_OrigTaskCreateMessage):
    """Extended version that includes task_id for operator resolution."""
    def __init__(self, TaskID: int = None, **kwargs):
        super().__init__(**kwargs)
        self._task_id = TaskID

    def to_json(self):
        j = super().to_json()
        j["task_id"] = self._task_id
        return j


def _parse_config(extra_info):
    """Parse pipe-delimited config from ExtraInfo."""
    config = {}
    if not extra_info or extra_info.strip() == "":
        return config
    for part in extra_info.split('|'):
        if ':' not in part:
            continue
        key, value = part.split(':', 1)
        config[key] = value
    return config


def _render_skill_summary(skill_id: str, output: dict) -> str:
    """Render a human-readable summary of structured skill output."""
    lines = []
    sep = "=" * 60
    lines.append(f"\n{sep}")
    lines.append(f"  SKILL REPORT: {skill_id}")
    lines.append(f"  Target: {output.get('target', 'N/A')}")
    lines.append(f"  Status: {output.get('status', 'N/A').upper()}")
    if output.get("confidence"):
        lines.append(f"  Confidence: {output['confidence']}")
    lines.append(sep)

    findings = output.get("findings", {})

    # Domains / Subdomains
    domains = findings.get("domains", [])
    if domains:
        lines.append(f"\n  DOMAINS ({len(domains)})")
        lines.append(f"  {'Domain':<40} {'Type':<12} {'Source'}")
        lines.append(f"  {'-'*38}  {'-'*10}  {'-'*20}")
        for d in domains:
            lines.append(f"  {d.get('domain',''):<40} {d.get('type',''):<12} {d.get('source','')}")

    # Tech Stack
    tech = findings.get("tech_stack", [])
    if tech:
        lines.append(f"\n  TECH STACK ({len(tech)})")
        lines.append(f"  {'Technology':<30} {'Confidence':<12} Evidence")
        lines.append(f"  {'-'*28}  {'-'*10}  {'-'*40}")
        for t in tech:
            ev = t.get("evidence", "")
            if len(ev) > 60:
                ev = ev[:57] + "..."
            lines.append(f"  {t.get('technology',''):<30} {t.get('confidence',''):<12} {ev}")

    # Cloud Assets
    cloud = findings.get("cloud_assets", [])
    if cloud:
        lines.append(f"\n  CLOUD ASSETS ({len(cloud)})")
        lines.append(f"  {'Provider':<20} {'Asset'}")
        lines.append(f"  {'-'*18}  {'-'*40}")
        for c in cloud:
            lines.append(f"  {c.get('provider',''):<20} {c.get('asset','')}")

    # Credentials Found
    creds = findings.get("credentials_found", [])
    if creds:
        lines.append(f"\n  ⚠ CREDENTIALS FOUND ({len(creds)})")
        lines.append(f"  {'Type':<15} {'Username':<25} {'Source':<30} Value")
        lines.append(f"  {'-'*13}  {'-'*23}  {'-'*28}  {'-'*20}")
        for c in creds:
            val = c.get("value", "")
            if len(val) > 20:
                val = val[:17] + "..."
            lines.append(f"  {c.get('type',''):<15} {c.get('username',''):<25} {c.get('source',''):<30} {val}")

    # Escalation Paths
    esc = findings.get("escalation_paths", [])
    if esc:
        lines.append(f"\n  ESCALATION PATHS ({len(esc)})")
        for e in esc:
            mitre = f" [{e['mitre_id']}]" if e.get("mitre_id") else ""
            lines.append(f"  - {e.get('technique','')}{mitre} ({e.get('confidence','')}) - {e.get('description','')}")

    # Lateral Movement
    lateral = findings.get("lateral_movement_opportunities", [])
    if lateral:
        lines.append(f"\n  LATERAL MOVEMENT ({len(lateral)})")
        for l in lateral:
            lines.append(f"  - {l.get('target_host','')}: {l.get('method','')} (cred: {l.get('credential','')})")

    # Network
    net = findings.get("network", {})
    if net:
        ifaces = net.get("interfaces", [])
        conns = net.get("connections", [])
        if ifaces:
            lines.append(f"\n  NETWORK INTERFACES ({len(ifaces)})")
            for iface in ifaces[:10]:
                if isinstance(iface, dict):
                    lines.append(f"  - {iface.get('name', '')}: {iface.get('ip', iface.get('address', ''))}")
        if conns:
            lines.append(f"\n  ACTIVE CONNECTIONS ({len(conns)})")
            for conn in conns[:15]:
                if isinstance(conn, dict):
                    lines.append(f"  - {conn.get('local', ''):<25} -> {conn.get('remote', ''):<25} {conn.get('state', '')}")

    # Security Products
    sec = findings.get("security_products", [])
    if sec:
        lines.append(f"\n  SECURITY PRODUCTS ({len(sec)})")
        for s in sec:
            lines.append(f"  - {s.get('name', '')} ({s.get('type', '')}) - {s.get('status', '')}")

    # Current User
    user = findings.get("current_user", {})
    if user and user.get("username"):
        lines.append(f"\n  CURRENT USER")
        lines.append(f"  Username: {user.get('username', '')}")
        if user.get("domain"):
            lines.append(f"  Domain: {user['domain']}")
        if user.get("groups"):
            lines.append(f"  Groups: {', '.join(user['groups'][:10])}")
        if user.get("privileges"):
            lines.append(f"  Privileges: {', '.join(user['privileges'][:10])}")
        lines.append(f"  Admin: {user.get('is_admin', False)}")

    # Certificates
    certs = findings.get("certificates", [])
    if certs:
        lines.append(f"\n  CERTIFICATES ({len(certs)})")
        lines.append(f"  {'Subject':<35} {'Issuer':<35} SANs")
        lines.append(f"  {'-'*33}  {'-'*33}  {'-'*30}")
        for c in certs:
            issuer = c.get("issuer", "")
            if len(issuer) > 33:
                issuer = issuer[:30] + "..."
            sans = ", ".join(c.get("san", [])[:3])
            if len(c.get("san", [])) > 3:
                sans += f" (+{len(c['san'])-3})"
            lines.append(f"  {c.get('subject',''):<35} {issuer:<35} {sans}")

    # Email Format
    email_fmt = findings.get("email_format", "")
    if email_fmt:
        lines.append(f"\n  EMAIL FORMAT")
        lines.append(f"  {email_fmt}")

    # Leaked Credentials
    leaked = findings.get("leaked_credentials", [])
    if leaked:
        lines.append(f"\n  ⚠ LEAKED CREDENTIALS ({len(leaked)})")
        for lk in leaked:
            pw = " (with password)" if lk.get("has_password") else ""
            lines.append(f"  - {lk.get('email', '')} [{lk.get('source', '')}]{pw}")

    # Employees
    emps = findings.get("employees", [])
    if emps:
        lines.append(f"\n  EMPLOYEES ({len(emps)})")
        for e in emps:
            lines.append(f"  - {e.get('name', '')} - {e.get('role', '')} [{e.get('source', '')}]")

    # Recommendations
    recs = output.get("recommendations", [])
    if recs:
        lines.append(f"\n  RECOMMENDATIONS")
        for r in recs:
            lines.append(f"  [{r.get('priority','').upper():<6}] {r.get('next_skill','')} - {r.get('rationale','')}")

    lines.append(f"\n{sep}\n")
    return "\n".join(lines)


async def _create_skill_artifacts(taskData, skill_id: str, output: dict):
    """Create Mythic artifacts for key findings in skill output."""
    findings = output.get("findings", {})

    # Artifact for each discovered domain/subdomain
    domains = findings.get("domains", [])
    if domains:
        domain_list = ", ".join(d.get("domain", "") for d in domains)
        try:
            await SendMythicRPCArtifactCreate(MythicRPCArtifactCreateMessage(
                TaskID=taskData.Task.ID,
                ArtifactMessage=f"[{skill_id}] Domains: {domain_list}",
                BaseArtifactType="DNS",
            ))
        except Exception:
            pass

    # Artifact for each credential found
    creds = findings.get("credentials_found", [])
    for c in creds:
        try:
            await SendMythicRPCArtifactCreate(MythicRPCArtifactCreateMessage(
                TaskID=taskData.Task.ID,
                ArtifactMessage=f"[{skill_id}] Credential: {c.get('type','')} - {c.get('username','')} from {c.get('source','')}",
                BaseArtifactType="Credential",
            ))
        except Exception:
            pass

    # Artifact for escalation paths
    esc = findings.get("escalation_paths", [])
    for e in esc:
        try:
            mitre = f" [{e['mitre_id']}]" if e.get("mitre_id") else ""
            await SendMythicRPCArtifactCreate(MythicRPCArtifactCreateMessage(
                TaskID=taskData.Task.ID,
                ArtifactMessage=f"[{skill_id}] Escalation: {e.get('technique','')}{mitre} - {e.get('description','')}",
                BaseArtifactType="PrivEsc",
            ))
        except Exception:
            pass

    # Artifact for lateral movement opportunities
    lateral = findings.get("lateral_movement_opportunities", [])
    for l in lateral:
        try:
            await SendMythicRPCArtifactCreate(MythicRPCArtifactCreateMessage(
                TaskID=taskData.Task.ID,
                ArtifactMessage=f"[{skill_id}] Lateral: {l.get('target_host','')} via {l.get('method','')}",
                BaseArtifactType="LateralMovement",
            ))
        except Exception:
            pass

    # Artifact for cloud assets
    cloud = findings.get("cloud_assets", [])
    if cloud:
        asset_list = "; ".join(f"{c.get('provider','')}: {c.get('asset','')}" for c in cloud)
        try:
            await SendMythicRPCArtifactCreate(MythicRPCArtifactCreateMessage(
                TaskID=taskData.Task.ID,
                ArtifactMessage=f"[{skill_id}] Cloud: {asset_list}",
                BaseArtifactType="Cloud",
            ))
        except Exception:
            pass


class SkillArguments(TaskArguments):
    def __init__(self, command_line, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = [
            CommandParameter(
                name="raw_input",
                cli_name="raw_input",
                display_name="Skill Command",
                type=ParameterType.String,
                description="Format: <skill_id> [--callback <id>] [--port <port>] <task description>",
                parameter_group_info=[ParameterGroupInfo(required=True)],
            ),
        ]

    async def parse_arguments(self):
        if len(self.command_line.strip()) == 0:
            raise Exception("Usage: skill <skill_id> [--callback <id>] [--port <port>] <task description>")

        raw = self.command_line.strip()

        # Handle JSON from popup — could be the new single-field format or legacy multi-field
        if raw[0] == "{":
            import json as _json
            try:
                data = _json.loads(raw)
            except _json.JSONDecodeError:
                raise Exception(f"Invalid JSON: {raw[:200]}")

            # Legacy multi-field JSON (from old popup or manual JSON input)
            if "skill_id" in data:
                self.add_arg("raw_input", raw)  # store raw for reference
                return

            # New single-field JSON
            if "raw_input" in data:
                raw = data["raw_input"]
                # Fall through to CLI parsing below
            else:
                raise Exception("JSON must contain 'skill_id' and 'task', or 'raw_input'")

        # CLI parsing: <skill_id> [--callback <id>] [--port <port>] [--context <json>] <task...>
        tokens = raw.split()
        if not tokens:
            raise Exception("Usage: skill <skill_id> [--callback <id>] [--port <port>] <task description>")

        callback_id = None
        port = None
        context = None
        task_parts = []

        # First token is always the skill ID
        skill_id = tokens[0]
        i = 1
        while i < len(tokens):
            if tokens[i] == "--callback" and i + 1 < len(tokens):
                callback_id = tokens[i + 1]
                i += 2
            elif tokens[i] == "--port" and i + 1 < len(tokens):
                port = tokens[i + 1]
                i += 2
            elif tokens[i] == "--context" and i + 1 < len(tokens):
                context = tokens[i + 1]
                i += 2
            else:
                task_parts.append(tokens[i])
                i += 1

        if not task_parts:
            raise Exception("Task description is required after the skill ID")

        # Store as JSON for create_go_tasking to consume uniformly
        import json as _json
        packed = {
            "skill_id": skill_id,
            "task": " ".join(task_parts),
            "callback": callback_id or "",
            "port": port or "7000",
            "context": context or "",
        }
        self.add_arg("raw_input", _json.dumps(packed))


class SkillCommand(CommandBase):
    cmd = "skill"
    needs_admin = False
    help_cmd = "skill <skill_id> [--callback <id>] [--port <port>] <task description>"
    description = "Execute a specialized skill agent with strict scope isolation, structured JSON output, and implant delegation"
    version = 2
    author = "@operator"
    argument_class = SkillArguments
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

        socks_started = False
        target_uuid = None
        socks_port = None
        delegate_session_id = None

        try:
            # Parse Echidna config
            extra_info = taskData.Callback.ExtraInfo
            if not extra_info or extra_info.strip() == "":
                raise Exception("Callback configuration not found. Please rebuild the payload.")

            config = _parse_config(extra_info)
            provider = config.get('Provider')
            api_key = config.get('APIKey')
            model = config.get('Model')
            is_sub_agent = config.get('IsSubAgent') == 'true'

            # Unpack arguments — either from raw_input (single field) or legacy multi-field
            raw_input = taskData.args.get_arg("raw_input") or ""
            if raw_input:
                try:
                    args_data = json.loads(raw_input)
                except (json.JSONDecodeError, TypeError):
                    args_data = {}
            else:
                args_data = {}

            # Support both packed (from raw_input) and legacy (direct field) formats
            skill_id = args_data.get("skill_id") or taskData.args.get_arg("skill_id") or ""
            task = args_data.get("task") or taskData.args.get_arg("task") or ""
            raw_callback = str(args_data.get("callback", "") or taskData.args.get_arg("callback") or "")
            callback_id = int(raw_callback) if raw_callback.strip().isdigit() else None
            raw_port = str(args_data.get("port", "7000") or taskData.args.get_arg("port") or "7000")
            socks_port = int(raw_port) if raw_port.strip().isdigit() else 7000
            campaign_context = args_data.get("context") or taskData.args.get_arg("context") or None
            if campaign_context and not campaign_context.strip():
                campaign_context = None

            if not provider:
                raise Exception("Provider not found in callback config")
            if not api_key:
                raise Exception("API key not found in callback config")
            if not task:
                raise Exception("Task description is required")
            if provider == "Google":
                raise Exception("Skill mode is not supported for Google provider. Use Anthropic or OpenAI.")

            # Fetch skill definition from toolbox to validate
            skill_info = await self._get_skill_info(skill_id)

            if is_sub_agent:
                # === SUB-AGENT MODE: run skill directly in toolbox ===
                delegate_session_id = config.get('DelegateSession')
                await self._run_skill(
                    taskData, provider, api_key, model, skill_id, task,
                    campaign_context=campaign_context,
                    socks_port=config.get('SocksPort'),
                    skill_info=skill_info,
                    delegate_session_id=delegate_session_id,
                )
            else:
                # === PARENT MODE ===
                needs_proxy = skill_info.get("requires_proxy", False)
                needs_delegate = skill_info.get("allow_delegate", False)

                if (needs_proxy or needs_delegate) and not callback_id:
                    raise Exception(
                        f"Skill '{skill_id}' requires a target callback (--callback <id>). "
                        f"needs_proxy={needs_proxy}, needs_delegate={needs_delegate}"
                    )

                # Resolve target callback if needed
                target_info_str = ""
                target_context = {}
                if callback_id and (needs_proxy or needs_delegate):
                    target_uuid, target_info_str, target_context = await self._resolve_target_callback(taskData, callback_id)

                # Start SOCKS proxy if needed
                if needs_proxy and target_uuid:
                    await self._start_socks(taskData, target_uuid, socks_port)
                    socks_started = True
                    await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                        TaskID=taskData.Task.ID,
                        Response=f"[skill:{skill_id}] SOCKS5 proxy started on port {socks_port} via callback #{callback_id}\n".encode()
                    ))

                # Setup delegation if needed
                if needs_delegate and target_uuid:
                    delegate_session_id = str(uuid.uuid4())[:8]
                    target_payload_type = target_context.get("payload_type", "unknown")

                    # Start delegate server (idempotent) and register session
                    from . import delegate_server
                    await delegate_server.start_server(port=DELEGATE_SERVER_PORT)
                    delegate_server.register_session(
                        session_id=delegate_session_id,
                        target_uuid=target_uuid,
                        parent_task_id=taskData.Task.ID,
                        callback_display_id=callback_id,
                        target_payload_type=target_payload_type,
                        target_context=target_context,
                    )
                    await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                        TaskID=taskData.Task.ID,
                        Response=(
                            f"[skill:{skill_id}] Delegation bridge active (session: {delegate_session_id})\n"
                            f"[skill:{skill_id}] Target: {target_info_str} (callback #{callback_id})\n"
                        ).encode()
                    ))

                # Spawn sub-agent with skill config
                child_uuid, child_display_id = await self._spawn_sub_agent(
                    taskData, config, provider, skill_id, task,
                    socks_port=socks_port if socks_started else 0,
                    campaign_context=campaign_context,
                    delegate_session_id=delegate_session_id,
                )

                await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                    TaskID=taskData.Task.ID,
                    Response=(
                        f"[skill:{skill_id}] Sub-agent spawned as callback #{child_display_id}\n"
                        f"[skill:{skill_id}] Skill: {skill_info.get('name', skill_id)} | Provider: {provider}\n"
                        f"[skill:{skill_id}] View output on the sub-agent's callback.\n"
                    ).encode()
                ))

                # Create skill task on child callback
                child_params = json.dumps({
                    "skill_id": skill_id,
                    "task": task,
                    "context": campaign_context or "",
                })
                task_resp = await SendMythicRPCTaskCreate(
                    _TaskCreateMessageWithTaskID(
                        AgentCallbackID=child_uuid,
                        CommandName="skill",
                        Params=child_params,
                        TaskID=taskData.Task.ID,
                    )
                )
                if not task_resp.Success:
                    raise Exception(f"Failed to create task on sub-agent: {task_resp.Error}")

                # Always wait for child to complete so we can capture output
                await self._wait_for_task(taskData, task_resp.TaskID)

                # Propagate [SKILL_RESULT] from child to parent for campaign chaining
                try:
                    child_resp_search = await SendMythicRPCResponseSearch(
                        MythicRPCResponseSearchMessage(TaskID=task_resp.TaskID)
                    )
                    if child_resp_search.Success and child_resp_search.Responses:
                        for r in child_resp_search.Responses:
                            text = r.Response.decode() if isinstance(r.Response, bytes) else str(r.Response)
                            if "[SKILL_RESULT]" in text:
                                for line in text.split("\n"):
                                    if line.strip().startswith("[SKILL_RESULT]"):
                                        await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                                            TaskID=taskData.Task.ID,
                                            Response=f"{line.strip()}\n".encode()
                                        ))
                                        break
                                break
                except Exception:
                    pass

            response.Success = True
            response.TaskStatus = MythicStatus.Completed
            response.Completed = True

        except Exception as e:
            response.Success = False
            response.TaskStatus = MythicStatus.Error
            response.Completed = True
            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=f"[skill] Error: {str(e)}".encode()
            ))

        finally:
            # Cleanup: stop SOCKS and unregister delegation session
            if socks_started and target_uuid and socks_port:
                await self._stop_socks(taskData, target_uuid, socks_port)
            if delegate_session_id:
                try:
                    from . import delegate_server
                    delegate_server.unregister_session(delegate_session_id)
                except Exception:
                    pass

        return response

    async def _get_skill_info(self, skill_id):
        """Fetch skill definition from the toolbox."""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{TOOLBOX_URL}/skills/{skill_id}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 404:
                    async with session.get(f"{TOOLBOX_URL}/skills") as list_resp:
                        skills_data = await list_resp.json()
                        available = [s["id"] for s in skills_data.get("skills", [])]
                    raise Exception(
                        f"Unknown skill '{skill_id}'. Available: {', '.join(available)}"
                    )
                if resp.status != 200:
                    raise Exception(f"Failed to fetch skill info: HTTP {resp.status}")
                return await resp.json()

    async def _run_skill(self, taskData, provider, api_key, model, skill_id, task,
                         campaign_context=None, socks_port=None, skill_info=None,
                         delegate_session_id=None):
        """Sub-agent mode: run skill in toolbox and stream output."""
        await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
            TaskID=taskData.Task.ID,
            Response=f"[skill:{skill_id}] Running in toolbox...\n[skill:{skill_id}] Task: {task}\n\n".encode()
        ))

        payload = {
            "skill_id": skill_id,
            "provider": provider,
            "api_key": api_key,
            "model": model or "",
            "task": task,
            "max_turns": 50,
            "timeout": 300,
        }
        if campaign_context:
            payload["campaign_context"] = campaign_context
        if socks_port:
            payload["socks_port"] = int(socks_port)
        if delegate_session_id:
            payload["delegate_session_id"] = delegate_session_id

        meta_data = None
        skill_output = None

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{TOOLBOX_URL}/run_skill",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=600),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(f"Toolbox error {resp.status}: {error_text}")

                buffer = ""
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    text = chunk.decode("utf-8", errors="replace")
                    buffer += text

                    if "\n" in buffer or len(buffer) > 4096:
                        lines = buffer.split("\n")
                        if buffer.endswith("\n"):
                            buffer = ""
                        else:
                            buffer = lines.pop()

                        output_lines = []
                        for line in lines:
                            stripped = line.strip()
                            if stripped.startswith("[ECHIDNA_META]"):
                                try:
                                    json_str = stripped[len("[ECHIDNA_META]"):].strip()
                                    meta_data = json.loads(json_str)
                                except (json.JSONDecodeError, Exception):
                                    pass
                            elif stripped.startswith("[SKILL_OUTPUT]"):
                                try:
                                    json_str = stripped[len("[SKILL_OUTPUT]"):].strip()
                                    skill_output = json.loads(json_str)
                                except (json.JSONDecodeError, Exception):
                                    pass
                            elif stripped.startswith("[POLICY VIOLATION]"):
                                output_lines.append(line)
                            else:
                                output_lines.append(line)

                        filtered = "\n".join(output_lines)
                        if output_lines:
                            filtered += "\n"
                        if filtered.strip():
                            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                                TaskID=taskData.Task.ID,
                                Response=filtered.encode()
                            ))

                # Flush remaining buffer
                if buffer:
                    stripped = buffer.strip()
                    if stripped.startswith("[ECHIDNA_META]"):
                        try:
                            meta_data = json.loads(stripped[len("[ECHIDNA_META]"):].strip())
                        except Exception:
                            pass
                    elif stripped.startswith("[SKILL_OUTPUT]"):
                        try:
                            skill_output = json.loads(stripped[len("[SKILL_OUTPUT]"):].strip())
                        except Exception:
                            pass
                    elif stripped:
                        await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                            TaskID=taskData.Task.ID,
                            Response=buffer.encode()
                        ))

        # Report structured output
        if skill_output:
            # 1. Upload JSON as downloadable file in Mythic
            try:
                formatted = json.dumps(skill_output, indent=2)
                file_resp = await SendMythicRPCFileCreate(MythicRPCFileCreateMessage(
                    TaskID=taskData.Task.ID,
                    FileContents=formatted.encode(),
                    Filename=f"{skill_id}_output.json",
                    Comment=f"Structured output from skill: {skill_id}",
                    DeleteAfterFetch=False,
                ))
                if file_resp.Success:
                    await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                        TaskID=taskData.Task.ID,
                        Response=f"\n📎 Full JSON saved: {skill_id}_output.json (Files tab)\n".encode()
                    ))
            except Exception:
                pass

            # 2. Write structured output marker for campaign chaining
            # This allows parent tasks and campaign orchestrator to extract the JSON
            compact = json.dumps(skill_output, separators=(',', ':'))
            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=f"[SKILL_RESULT]{compact}\n".encode()
            ))

            # 3. Render human-readable summary
            summary = _render_skill_summary(skill_id, skill_output)
            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=summary.encode()
            ))

            # 4. Create artifacts for key findings
            await _create_skill_artifacts(taskData, skill_id, skill_output)

        # Report usage
        if meta_data and meta_data.get("type") == "usage":
            input_t = meta_data.get("input_tokens", 0)
            output_t = meta_data.get("output_tokens", 0)
            ctx_remaining = meta_data.get("context_remaining", 0)
            cost = meta_data.get("cost_usd", 0)
            turns = meta_data.get("turns", 0)
            model_name = meta_data.get("model", "unknown")

            description = (
                f"[{skill_id}] Tokens: {input_t:,} in / {output_t:,} out | "
                f"Context: {ctx_remaining:,} remaining | "
                f"Cost: ${cost:.2f} | Turns: {turns}"
            )

            try:
                await SendMythicRPCCallbackUpdate(
                    MythicRPCCallbackUpdateMessage(
                        CallbackID=taskData.Callback.ID,
                        Description=description
                    )
                )
            except Exception:
                pass

            usage_line = (
                f"\n---\n"
                f"[skill:{skill_id}] Usage: {input_t:,} input / {output_t:,} output tokens | "
                f"Context remaining: {ctx_remaining:,} | "
                f"Cost: ${cost:.2f} | Turns: {turns} | Model: {model_name}\n"
            )
            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=usage_line.encode()
            ))

    async def _spawn_sub_agent(self, taskData, config, provider, skill_id, task,
                                socks_port=0, campaign_context=None, delegate_session_id=None):
        """Create a child callback for skill execution."""
        parent_search = await SendMythicRPCCallbackSearch(
            MythicRPCCallbackSearchMessage(
                AgentCallbackUUID=taskData.Callback.AgentCallbackID,
                SearchCallbackUUID=taskData.Callback.AgentCallbackID
            )
        )
        if not parent_search.Success or not parent_search.Results:
            raise Exception("Failed to look up parent callback")

        payload_uuid = parent_search.Results[0].RegisteredPayloadUUID
        if not payload_uuid:
            raise Exception("Could not determine PayloadUUID from parent callback")

        parent_display_id = taskData.Callback.DisplayID

        child_extra_info = taskData.Callback.ExtraInfo + "|IsSubAgent:true"
        if socks_port > 0:
            child_extra_info += f"|SocksPort:{socks_port}"
        if delegate_session_id:
            child_extra_info += f"|DelegateSession:{delegate_session_id}"

        child_resp = await SendMythicRPCCallbackCreate(
            MythicRPCCallbackCreateMessage(
                PayloadUUID=payload_uuid,
                C2ProfileName="",
                User="toolbox",
                Host=f"{provider} - {skill_id}",
                Ip="API",
                Description=f"[{skill_id}] {task[:180]}",
                ProcessName=f"skill-{skill_id}",
                Os="Virtual",
                Architecture="x64",
                IntegrityLevel=3,
                ExtraInfo=child_extra_info,
            )
        )
        if not child_resp.Success:
            raise Exception(f"Failed to create sub-agent callback: {child_resp.Error}")

        child_uuid = child_resp.CallbackUUID

        child_search = await SendMythicRPCCallbackSearch(
            MythicRPCCallbackSearchMessage(
                AgentCallbackUUID=taskData.Callback.AgentCallbackID,
                SearchCallbackUUID=child_uuid
            )
        )
        if not child_search.Success or not child_search.Results:
            raise Exception("Failed to look up child callback after creation")

        child_display_id = child_search.Results[0].DisplayID

        # Create graph edge
        token_resp = await SendMythicRPCAPITokenCreate(
            MythicRPCAPITokenCreateMessage(
                AgentTaskID=taskData.Task.AgentTaskID
            )
        )
        if token_resp.Success:
            edge_url = f"http://{MYTHIC_SERVER_HOST}:{MYTHIC_SERVER_PORT}/api/v1.4/callbackgraphedge_add_webhook"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        edge_url,
                        json={
                            "input": {
                                "source_id": parent_display_id,
                                "destination_id": child_display_id,
                                "c2profile": "http"
                            }
                        },
                        headers={"apitoken": token_resp.APIToken},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        pass
            except Exception:
                pass

        return child_uuid, child_display_id

    async def _resolve_target_callback(self, taskData, display_id):
        """Look up a callback by DisplayID and validate it's a real implant."""
        search_resp = await SendMythicRPCCallbackSearch(
            MythicRPCCallbackSearchMessage(
                AgentCallbackUUID=taskData.Callback.AgentCallbackID,
                SearchCallbackDisplayID=display_id
            )
        )
        if not search_resp.Success or not search_resp.Results:
            raise Exception(f"Callback #{display_id} not found")

        target = search_resp.Results[0]
        if not target.Active:
            raise Exception(f"Callback #{display_id} is not active")
        if target.PayloadType == "echidna":
            raise Exception(
                f"Callback #{display_id} is a virtual agent (echidna), not a real implant. "
                f"Choose an Apollo, Poseidon, or other implant callback."
            )

        target_info = f"{target.PayloadType}@{target.Host}"
        target_context = {
            "payload_type": target.PayloadType or "unknown",
            "hostname": target.Host or "unknown",
            "user": target.User or "unknown",
            "ip": target.Ip or "unknown",
            "os": target.Os or "unknown",
            "domain": target.Domain or "",
            "integrity_level": target.IntegrityLevel if target.IntegrityLevel is not None else -1,
            "process_name": target.ProcessName or "unknown",
            "description": target.Description or "",
            "display_id": target.DisplayID if target.DisplayID is not None else 0,
        }
        return target.AgentCallbackID, target_info, target_context

    async def _start_socks(self, taskData, target_uuid, port):
        """Start SOCKS5 proxy on the target callback."""
        import asyncio
        socks_params = json.dumps({"port": port, "action": "start"})

        task_resp = await SendMythicRPCTaskCreate(
            _TaskCreateMessageWithTaskID(
                AgentCallbackID=target_uuid,
                CommandName="socks",
                Params=socks_params,
                TaskID=taskData.Task.ID,
            )
        )
        if not task_resp.Success:
            raise Exception(f"Failed to create socks task on target: {task_resp.Error}")

        for attempt in range(10):
            await asyncio.sleep(1)
            search_resp = await SendMythicRPCTaskSearch(
                MythicRPCTaskSearchMessage(
                    TaskID=taskData.Task.ID,
                    SearchTaskID=task_resp.TaskID
                )
            )
            if not search_resp.Success or not search_resp.Tasks:
                continue
            socks_task = search_resp.Tasks[0]
            if socks_task.Completed:
                if socks_task.Status and "error" in socks_task.Status.lower():
                    raise Exception(f"SOCKS proxy failed to start: {socks_task.Status}")
                return

        raise Exception("SOCKS proxy task did not complete within 10 seconds")

    async def _stop_socks(self, taskData, target_uuid, port):
        """Stop the SOCKS5 proxy. Never raises."""
        try:
            socks_params = json.dumps({"port": port, "action": "stop"})
            await SendMythicRPCTaskCreate(
                _TaskCreateMessageWithTaskID(
                    AgentCallbackID=target_uuid,
                    CommandName="socks",
                    Params=socks_params,
                    TaskID=taskData.Task.ID,
                )
            )
            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=f"\n[skill] SOCKS5 proxy stopped on port {port}\n".encode()
            ))
        except Exception as e:
            await SendMythicRPCResponseCreate(MythicRPCResponseCreateMessage(
                TaskID=taskData.Task.ID,
                Response=f"\n[skill] Warning: failed to stop SOCKS proxy: {e}\n".encode()
            ))

    async def _wait_for_task(self, taskData, child_task_id):
        """Poll until the child task completes (or timeout after 15 min)."""
        import asyncio
        for _ in range(180):  # 180 * 5s = 15 minutes
            await asyncio.sleep(5)
            search_resp = await SendMythicRPCTaskSearch(
                MythicRPCTaskSearchMessage(
                    TaskID=taskData.Task.ID,
                    SearchTaskID=child_task_id
                )
            )
            if search_resp.Success and search_resp.Tasks and search_resp.Tasks[0].Completed:
                return

    async def process_response(self, task: PTTaskMessageAllData, response: any) -> PTTaskProcessResponseMessageResponse:
        resp = PTTaskProcessResponseMessageResponse(TaskID=task.Task.ID, Success=True)
        return resp
