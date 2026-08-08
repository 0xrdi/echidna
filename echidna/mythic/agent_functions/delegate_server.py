"""
Lightweight HTTP server running inside the Echidna container.
Handles delegation requests from the toolbox on behalf of skill agents.

The toolbox calls POST /delegate with a command to execute on a target
implant. This server uses Mythic RPC to create the task, polls for
completion, and returns the result.

Runs on port 6790 in a background thread started by skill.py.
"""

from aiohttp import web
from mythic_container.MythicRPC import *
from mythic_container.MythicGoRPC.send_mythic_rpc_task_create import MythicRPCTaskCreateMessage as _OrigTaskCreateMessage
import asyncio
import json
import logging

logger = logging.getLogger("delegate_server")

# Singleton state
_app = None
_runner = None
_site = None
_parent_task_id = None  # The parent task ID for RPC calls


class _TaskCreateMessageWithTaskID(_OrigTaskCreateMessage):
    def __init__(self, TaskID: int = None, **kwargs):
        super().__init__(**kwargs)
        self._task_id = TaskID

    def to_json(self):
        j = super().to_json()
        j["task_id"] = self._task_id
        return j


# Store active sessions: session_id -> {target_uuid, parent_task_id, callback_display_id}
_sessions: dict = {}


async def handle_delegate(request: web.Request) -> web.Response:
    """Execute a command on a target implant via Mythic RPC.

    Request JSON:
    {
        "session_id": "abc123",        -- links to a registered delegation session
        "command": "shell",            -- Apollo command name
        "params": "whoami /priv"       -- command parameters (string or JSON string)
    }

    Response JSON:
    {
        "success": true,
        "task_id": 123,
        "output": "...",               -- combined task output
        "status": "completed",
        "error": null
    }
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON body"}, status=400)

    session_id = body.get("session_id")
    command = body.get("command")
    params = body.get("params", "")

    if not session_id or session_id not in _sessions:
        return web.json_response(
            {"success": False, "error": f"Unknown session_id '{session_id}'. Active sessions: {list(_sessions.keys())}"},
            status=400
        )
    if not command:
        return web.json_response({"success": False, "error": "Missing 'command' field"}, status=400)

    session = _sessions[session_id]
    target_uuid = session["target_uuid"]
    parent_task_id = session["parent_task_id"]

    # Normalize params: if dict/list, serialize to JSON string
    if isinstance(params, (dict, list)):
        params = json.dumps(params)

    try:
        # Create task on the target implant
        task_resp = await SendMythicRPCTaskCreate(
            _TaskCreateMessageWithTaskID(
                AgentCallbackID=target_uuid,
                CommandName=command,
                Params=str(params),
                TaskID=parent_task_id,
            )
        )
        if not task_resp.Success:
            return web.json_response(
                {"success": False, "error": f"Failed to create task: {task_resp.Error}"},
                status=500
            )

        created_task_id = task_resp.TaskID

        # Poll for task completion (up to 120 seconds)
        output_text = ""
        task_status = "unknown"
        for _ in range(60):
            await asyncio.sleep(2)

            search_resp = await SendMythicRPCTaskSearch(
                MythicRPCTaskSearchMessage(
                    TaskID=parent_task_id,
                    SearchTaskID=created_task_id
                )
            )
            if not search_resp.Success or not search_resp.Tasks:
                continue

            task = search_resp.Tasks[0]
            if task.Completed:
                task_status = task.Status or "completed"

                # Fetch task output via response search
                resp_search = await SendMythicRPCResponseSearch(
                    MythicRPCResponseSearchMessage(TaskID=created_task_id)
                )
                if resp_search.Success and resp_search.Responses:
                    output_parts = []
                    for r in resp_search.Responses:
                        if hasattr(r, 'Response') and r.Response:
                            if isinstance(r.Response, bytes):
                                output_parts.append(r.Response.decode("utf-8", errors="replace"))
                            else:
                                output_parts.append(str(r.Response))
                    output_text = "\n".join(output_parts)

                return web.json_response({
                    "success": True,
                    "task_id": created_task_id,
                    "output": output_text,
                    "status": task_status,
                    "error": None,
                })

        # Timed out
        return web.json_response({
            "success": False,
            "task_id": created_task_id,
            "output": "",
            "status": "timeout",
            "error": "Task did not complete within 120 seconds",
        })

    except Exception as e:
        return web.json_response(
            {"success": False, "error": f"Delegation error: {str(e)}"},
            status=500
        )


async def handle_sessions(request: web.Request) -> web.Response:
    """List active delegation sessions."""
    result = {}
    for sid, info in _sessions.items():
        result[sid] = {
            "callback_display_id": info.get("callback_display_id"),
            "target_payload_type": info.get("target_payload_type", "unknown"),
            "target_context": info.get("target_context", {}),
        }
    return web.json_response({"sessions": result})


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "active_sessions": len(_sessions)})


def register_session(session_id: str, target_uuid: str, parent_task_id: int,
                     callback_display_id: int = 0, target_payload_type: str = "unknown",
                     target_context: dict = None):
    """Register a delegation session for a skill run."""
    _sessions[session_id] = {
        "target_uuid": target_uuid,
        "parent_task_id": parent_task_id,
        "callback_display_id": callback_display_id,
        "target_payload_type": target_payload_type,
        "target_context": target_context or {},
    }


def unregister_session(session_id: str):
    """Remove a delegation session."""
    _sessions.pop(session_id, None)


async def start_server(port: int = 6790):
    """Start the delegation HTTP server (call once)."""
    global _app, _runner, _site

    if _site is not None:
        return  # Already running

    _app = web.Application()
    _app.router.add_post("/delegate", handle_delegate)
    _app.router.add_get("/sessions", handle_sessions)
    _app.router.add_get("/health", handle_health)

    _runner = web.AppRunner(_app)
    await _runner.setup()
    _site = web.TCPSite(_runner, "0.0.0.0", port)
    await _site.start()
    logger.info(f"[delegate_server] Listening on port {port}")


async def stop_server():
    """Stop the delegation HTTP server."""
    global _runner, _site
    if _runner:
        await _runner.cleanup()
        _runner = None
        _site = None
