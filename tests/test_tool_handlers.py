"""Tests for core/tool_handlers.py — dispatch and technique validation."""
import json
import types
import pytest
import echidna.mythic.agent_functions.core.tool_handlers as th
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

    async def _tool_list_commands(self, args, request):
        return json.dumps({"commands": []})

    async def _tool_execute_command(self, args, request, operator_id):
        return json.dumps({"status": "ok"})

    async def _tool_process_search(self, args, request):
        return json.dumps({"processes": []})

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

    @pytest.mark.asyncio
    async def test_dispatches_list_commands(self):
        result = await handler._execute_tool(
            "list_commands", {"callback_id": 1}, None, 0,
        )
        assert "commands" in json.loads(result)

    @pytest.mark.asyncio
    async def test_dispatches_process_search(self):
        result = await handler._execute_tool(
            "process_search", {}, None, 0,
        )
        assert "processes" in json.loads(result)


# ---- list_commands handler ----

class TestListCommandsHandler:
    @pytest.mark.asyncio
    async def test_requires_callback_id(self):
        h = ToolHandlerMixin()
        result = await h._tool_list_commands({}, None)
        assert "error" in json.loads(result)

    @pytest.mark.asyncio
    async def test_callback_not_found(self, monkeypatch):
        async def fake_cb_search(msg):
            return types.SimpleNamespace(Success=True, Results=[])
        monkeypatch.setattr(
            th, "SendMythicRPCCallbackSearch", fake_cb_search,
        )
        h = ToolHandlerMixin()
        result = await h._tool_list_commands({"callback_id": 9}, None)
        assert "not found" in json.loads(result)["error"]

    @pytest.mark.asyncio
    async def test_returns_commands(self, monkeypatch):
        async def fake_cb_search(msg):
            return types.SimpleNamespace(
                Success=True,
                Results=[types.SimpleNamespace(ID=7)],
            )

        async def fake_cmd_search(msg):
            # display ID resolved to the real numeric callback ID
            assert msg.CallbackID == 7
            return types.SimpleNamespace(
                Success=True,
                Commands=[
                    types.SimpleNamespace(
                        Name="shell",
                        Description="run a shell command",
                        HelpCmd="shell -h",
                        NeedsAdmin=False,
                    ),
                ],
            )
        monkeypatch.setattr(
            th, "SendMythicRPCCallbackSearch", fake_cb_search,
        )
        monkeypatch.setattr(
            th, "SendMythicRPCCallbackSearchCommand", fake_cmd_search,
        )
        h = ToolHandlerMixin()

        async def fake_task_id():
            return 5
        monkeypatch.setattr(h, "_get_any_task_id", fake_task_id)

        result = await h._tool_list_commands({"callback_id": 1}, None)
        data = json.loads(result)
        assert data["callback_id"] == 1
        assert data["count"] == 1
        assert data["commands"][0]["name"] == "shell"


# ---- process_search handler ----

class TestProcessSearchHandler:
    @pytest.mark.asyncio
    async def test_no_tasks_error(self, monkeypatch):
        h = ToolHandlerMixin()

        async def fake_task_id():
            return 0
        monkeypatch.setattr(h, "_get_any_task_id", fake_task_id)

        result = await h._tool_process_search({}, None)
        assert "error" in json.loads(result)

    @pytest.mark.asyncio
    async def test_returns_processes(self, monkeypatch):
        async def fake_proc_search(msg):
            assert msg.TaskID == 5
            # filters are applied client-side — nothing sent to the RPC
            assert getattr(msg.Process, "Name", None) is None
            assert getattr(msg.Process, "Host", None) is None
            return types.SimpleNamespace(
                Success=True,
                Processes=[
                    types.SimpleNamespace(
                        Host="DC01", ProcessID=4100,
                        ParentProcessID=640, Name="MsMpEng.exe",
                        User="SYSTEM", Architecture="x64",
                        BinPath="C:\\Program Files\\...",
                        CommandLine=None, Signer="Microsoft",
                    ),
                ],
            )
        monkeypatch.setattr(
            th, "SendMythicRPCProcessSearch", fake_proc_search,
        )
        h = ToolHandlerMixin()

        async def fake_task_id():
            return 5
        monkeypatch.setattr(h, "_get_any_task_id", fake_task_id)

        result = await h._tool_process_search({}, None)
        data = json.loads(result)
        assert data["count"] == 1
        proc = data["processes"][0]
        assert proc["name"] == "MsMpEng.exe"
        assert proc["host"] == "DC01"
        assert proc["command_line"] == ""
        assert "truncated" not in data

    @pytest.mark.asyncio
    async def test_filters_client_side(self, monkeypatch):
        def proc(host, pid, name, user):
            return types.SimpleNamespace(
                Host=host, ProcessID=pid, ParentProcessID=0,
                Name=name, User=user, Architecture=None,
                BinPath=None, CommandLine=None, Signer=None,
            )
        all_procs = [
            proc("DC01", 4, "MsMpEng.exe", "SYSTEM"),
            proc("DC01", 100, "chrome.exe", "CORP\\alice"),
            proc("WS02", 200, "msedge.exe", "CORP\\bob"),
        ]

        async def fake_proc_search(msg):
            return types.SimpleNamespace(
                Success=True, Processes=all_procs,
            )
        monkeypatch.setattr(
            th, "SendMythicRPCProcessSearch", fake_proc_search,
        )
        h = ToolHandlerMixin()

        async def fake_task_id():
            return 5
        monkeypatch.setattr(h, "_get_any_task_id", fake_task_id)

        # case-insensitive substring match on name
        result = await h._tool_process_search({"name": "msmpeng"}, None)
        data = json.loads(result)
        assert data["count"] == 1
        assert data["processes"][0]["pid"] == 4

        # host + user combined
        result = await h._tool_process_search(
            {"host": "dc01", "user": "alice"}, None,
        )
        data = json.loads(result)
        assert data["count"] == 1
        assert data["processes"][0]["name"] == "chrome.exe"

        # no match
        result = await h._tool_process_search({"name": "nonexistent"}, None)
        data = json.loads(result)
        assert data["count"] == 0
        assert data["processes"] == []

    @pytest.mark.asyncio
    async def test_truncates_large_result_sets(self, monkeypatch):
        procs = [
            types.SimpleNamespace(
                Host="h", ProcessID=i, ParentProcessID=0,
                Name=f"proc{i}", User="u", Architecture=None,
                BinPath=None, CommandLine="x" * 300, Signer=None,
            )
            for i in range(th.MAX_PROCESS_RESULTS + 50)
        ]

        async def fake_proc_search(msg):
            return types.SimpleNamespace(Success=True, Processes=procs)
        monkeypatch.setattr(
            th, "SendMythicRPCProcessSearch", fake_proc_search,
        )
        h = ToolHandlerMixin()

        async def fake_task_id():
            return 5
        monkeypatch.setattr(h, "_get_any_task_id", fake_task_id)

        result = await h._tool_process_search({}, None)
        data = json.loads(result)
        assert data["count"] == th.MAX_PROCESS_RESULTS
        assert "truncated" in data
        assert len(data["processes"][0]["command_line"]) == 200
