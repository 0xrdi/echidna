"""Tests for core/tool_handlers.py — dispatch and technique validation."""
import json
import pytest
from echidna.mythic.agent_functions.core.tool_handlers import (
    ToolHandlerMixin,
    _TECHNIQUE_RE,
)


class TestTechniqueRegex:
    def test_valid_base(self):
        assert _TECHNIQUE_RE.match("T1059")

    def test_valid_subtechnique(self):
        assert _TECHNIQUE_RE.match("T1059.004")

    def test_invalid_no_t(self):
        assert not _TECHNIQUE_RE.match("1059")

    def test_invalid_too_short(self):
        assert not _TECHNIQUE_RE.match("T105")

    def test_invalid_too_long_base(self):
        assert not _TECHNIQUE_RE.match("T10590")

    def test_invalid_subtechnique_too_short(self):
        assert not _TECHNIQUE_RE.match("T1059.04")

    def test_invalid_subtechnique_too_long(self):
        assert not _TECHNIQUE_RE.match("T1059.0040")

    def test_invalid_random(self):
        assert not _TECHNIQUE_RE.match("hello")

    def test_invalid_empty(self):
        assert not _TECHNIQUE_RE.match("")


class FakeHandler(ToolHandlerMixin):
    async def _tool_list_callbacks(self, args, request, operator_id):
        return json.dumps({"callbacks": []})

    async def _tool_execute_command(self, args, request, operator_id):
        return json.dumps({"status": "ok"})

    async def _tool_credential_create(self, args, request):
        return json.dumps({"status": "stored"})

    async def _tool_create_artifact(self, args, request):
        return json.dumps({"status": "logged"})

    async def _tool_event_log(self, args, request):
        return json.dumps({"status": "logged"})

    async def _tool_tag_task(self, args, request):
        return json.dumps({"status": "tagged"})


handler = FakeHandler()


class TestExecuteToolDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_list_callbacks(self):
        result = await handler._execute_tool(
            "list_callbacks", {}, None, 0,
        )
        assert "callbacks" in json.loads(result)

    @pytest.mark.asyncio
    async def test_dispatches_execute_command(self):
        result = await handler._execute_tool(
            "execute_command", {"callback_id": 1, "command": "shell"}, None, 0,
        )
        assert json.loads(result)["status"] == "ok"

    @pytest.mark.asyncio
    async def test_dispatches_credential_create(self):
        result = await handler._execute_tool(
            "credential_create", {}, None, 0,
        )
        assert json.loads(result)["status"] == "stored"

    @pytest.mark.asyncio
    async def test_dispatches_event_log(self):
        result = await handler._execute_tool(
            "event_log", {"message": "test"}, None, 0,
        )
        assert json.loads(result)["status"] == "logged"

    @pytest.mark.asyncio
    async def test_dispatches_tag_task(self):
        result = await handler._execute_tool(
            "tag_task", {"task_id": 1, "technique_id": "T1059"}, None, 0,
        )
        assert json.loads(result)["status"] == "tagged"

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        result = await handler._execute_tool(
            "nonexistent_tool", {}, None, 0,
        )
        data = json.loads(result)
        assert "error" in data
        assert "Unknown tool" in data["error"]
