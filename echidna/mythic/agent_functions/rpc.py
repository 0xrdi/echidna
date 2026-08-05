"""Mythic RPC helpers.

Creating a task over RPC needs an attribution context: Mythic answers
"missing TaskID, EventStepInstanceID, or Operator ID" when it can't work out
which operator the new task belongs to. The Python client's
``MythicRPCTaskCreateMessage`` exposes no field for that, so the parent task's id
is injected into the serialized payload — the server resolves the operator from
it.

Anything that spawns a task on behalf of another task (the `skill` command
spawning sub-agents and SOCKS, the `campaign` orchestrator spawning each skill)
must use this instead of the stock message class.
"""
from mythic_container.MythicGoRPC.send_mythic_rpc_task_create import (
    MythicRPCTaskCreateMessage as _OrigTaskCreateMessage,
)


class TaskCreateMessageWithTaskID(_OrigTaskCreateMessage):
    """``MythicRPCTaskCreateMessage`` that carries the parent task id."""

    def __init__(self, TaskID: int = None, **kwargs):
        super().__init__(**kwargs)
        self._task_id = TaskID

    def to_json(self):
        j = super().to_json()
        j["task_id"] = self._task_id
        return j
