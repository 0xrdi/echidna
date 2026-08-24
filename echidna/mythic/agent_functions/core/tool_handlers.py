import re
import json
import asyncio
import aiohttp
from mythic_container.MythicRPC import *
from .constants import TASK_POLL_TIMEOUT


_TECHNIQUE_RE = re.compile(r"^T\d{4}(\.\d{3})?$")


class ToolHandlerMixin:

    async def _execute_tool(self, name, args, request, operator_id):
        dispatch = {
            "list_callbacks": self._tool_list_callbacks,
            "execute_command": self._tool_execute_command,
            "credential_create": self._tool_credential_create,
            "create_artifact": self._tool_create_artifact,
            "event_log": self._tool_event_log,
            "tag_task": self._tool_tag_task,
        }
        handler = dispatch.get(name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {name}"})
        if name in ("list_callbacks", "execute_command"):
            return await handler(args, request, operator_id)
        return await handler(args, request)

    async def _tool_list_callbacks(self, _args, request, operator_id):
        try:
            search = await SendMythicRPCCallbackSearch(
                MythicRPCCallbackSearchMessage()
            )
            if not search.Success:
                return json.dumps(
                    {"error": f"Search failed: {search.Error}"}
                )

            callbacks = []
            for cb in search.Results:
                if not cb.Active:
                    continue
                callbacks.append({
                    "id": cb.DisplayID,
                    "payload_type": cb.PayloadType,
                    "host": cb.Host,
                    "user": cb.User,
                    "ip": cb.Ip,
                    "os": cb.Os,
                    "pid": cb.PID,
                    "process": cb.ProcessName,
                    "integrity": cb.IntegrityLevel,
                    "description": cb.Description,
                })
            return json.dumps({"callbacks": callbacks})
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _tool_execute_command(self, args, request, operator_id):
        display_id = args.get("callback_id")
        command = args.get("command", "")
        params = args.get("params", "")

        if not display_id or not command:
            return json.dumps({
                "error": "callback_id and command are required",
            })

        try:
            cb_search = await SendMythicRPCCallbackSearch(
                MythicRPCCallbackSearchMessage(
                    SearchCallbackDisplayID=int(display_id),
                )
            )
            if not cb_search.Success or not cb_search.Results:
                return json.dumps({
                    "error": f"Callback #{display_id} not found",
                })
            agent_callback_id = cb_search.Results[0].AgentCallbackID
            if not operator_id:
                operator_id = cb_search.Results[0].OperatorID

            task_resp = await SendMythicRPCTaskCreate(
                MythicRPCTaskCreateMessage(
                    AgentCallbackID=agent_callback_id,
                    CommandName=command,
                    Params=str(params),
                    OperatorID=operator_id,
                )
            )
            if not task_resp.Success:
                return json.dumps({
                    "error": f"Task creation failed: {task_resp.Error}",
                })

            task_id = task_resp.TaskID
            task_display_id = task_resp.TaskDisplayID or task_id

            for _ in range(TASK_POLL_TIMEOUT // 2):
                await asyncio.sleep(2)
                search = await SendMythicRPCTaskSearch(
                    MythicRPCTaskSearchMessage(
                        TaskID=task_id,
                        SearchTaskID=task_id,
                    )
                )
                if (search.Success and search.Tasks
                        and search.Tasks[0].Completed):
                    break
            else:
                return json.dumps({
                    "task_id": task_display_id,
                    "status": "timeout",
                    "error": "Task did not complete within timeout",
                })

            resp_search = await SendMythicRPCResponseSearch(
                MythicRPCResponseSearchMessage(TaskID=task_id)
            )
            output_parts = []
            if resp_search.Success and resp_search.Responses:
                for r in resp_search.Responses:
                    if hasattr(r, "Response") and r.Response:
                        output_parts.append(str(r.Response))

            return json.dumps({
                "task_id": task_display_id,
                "status": "completed",
                "output": "\n".join(output_parts),
            })

        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _get_any_task_id(self):
        try:
            cb_search = await SendMythicRPCCallbackSearch(
                MythicRPCCallbackSearchMessage()
            )
            if cb_search.Success and cb_search.Results:
                for cb in cb_search.Results:
                    task_search = await SendMythicRPCTaskSearch(
                        MythicRPCTaskSearchMessage(
                            TaskID=0,
                            SearchCallbackID=cb.DisplayID,
                        )
                    )
                    if task_search.Success and task_search.Tasks:
                        return task_search.Tasks[0].TaskID
        except Exception:
            pass
        return 0

    async def _tool_credential_create(self, args, request):
        try:
            from mythic_container.MythicGoRPC.send_mythic_rpc_credential_create import (
                MythicRPCCredentialData,
            )
            task_id = await self._get_any_task_id()
            cred = MythicRPCCredentialData(
                CredentialType=args.get("credential_type", "plaintext"),
                Account=args.get("account", ""),
                Credential=args.get("credential", ""),
                Realm=args.get("realm", ""),
                Comment=args.get("comment", ""),
            )
            resp = await SendMythicRPCCredentialCreate(
                MythicRPCCredentialCreateMessage(
                    TaskID=task_id,
                    Credentials=[cred],
                )
            )
            if resp.Success:
                return json.dumps({
                    "status": "stored",
                    "account": args.get("account", ""),
                    "type": args.get("credential_type", ""),
                    "realm": args.get("realm", ""),
                })
            return json.dumps({"error": resp.Error})
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _tool_create_artifact(self, args, request):
        try:
            task_id = await self._get_any_task_id()
            resp = await SendMythicRPCArtifactCreate(
                MythicRPCArtifactCreateMessage(
                    TaskID=task_id,
                    ArtifactMessage=args.get("artifact", ""),
                    BaseArtifactType=args.get("artifact_type", "Other"),
                    ArtifactHost=args.get("host", ""),
                    NeedsCleanup=args.get("needs_cleanup", False),
                )
            )
            if resp.Success:
                return json.dumps({
                    "status": "logged",
                    "artifact": args.get("artifact", ""),
                    "type": args.get("artifact_type", ""),
                    "needs_cleanup": args.get("needs_cleanup", False),
                })
            return json.dumps({"error": resp.Error})
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _tool_event_log(self, args, request):
        try:
            level = args.get("level", "info")
            resp = await SendMythicRPCOperationEventLogCreate(
                MythicRPCOperationEventLogCreateMessage(
                    OperationID=request.OperationID,
                    Message=f"[Echidna] {args.get('message', '')}",
                    MessageLevel=level,
                    Warning=(level == "warning"),
                )
            )
            if resp.Success:
                return json.dumps({
                    "status": "logged",
                    "message": args.get("message", ""),
                })
            return json.dumps({"error": resp.Error})
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _tool_tag_task(self, args, request):
        try:
            task_id = args.get("task_id", 0)
            technique_id = args.get("technique_id", "")
            if not task_id or not technique_id:
                return json.dumps({
                    "error": "task_id and technique_id are required",
                })
            if not _TECHNIQUE_RE.match(technique_id):
                return json.dumps({
                    "error": (
                        f"Invalid technique_id format: {technique_id}. "
                        "Expected Tnnnn or Tnnnn.nnn"
                    ),
                })
            token_resp = await SendMythicRPCAPITokenCreate(
                MythicRPCAPITokenCreateMessage(
                    OperationID=request.OperationID,
                    APITokenID=request.APITokenID,
                    ChatChannelID=request.ChannelID,
                )
            )
            if not token_resp.Success:
                return json.dumps({
                    "error": f"API token: {token_resp.Error}",
                })
            gql = (
                "mutation { addAttackToTask("
                f't_num: "{technique_id}", '
                f"task_display_id: {int(task_id)}"
                ") { status error } }"
            )
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://mythic_nginx:7443/graphql/",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token_resp.APIToken}",
                    },
                    json={"query": gql},
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
            result = data.get("data", {}).get("addAttackToTask", {})
            if result.get("status") == "success":
                return json.dumps({
                    "status": "tagged",
                    "task_id": task_id,
                    "technique": technique_id,
                })
            err = result.get("error") or data.get("errors", "unknown")
            return json.dumps({"error": str(err)})
        except Exception as e:
            return json.dumps({"error": str(e)})
