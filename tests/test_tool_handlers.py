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

    async def _tool_task_history(self, args, request):
        return json.dumps({"tasks": []})

    async def _tool_credential_search(self, args, request):
        return json.dumps({"credentials": []})

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

    @pytest.mark.asyncio
    async def test_dispatches_credential_search(self):
        result = await handler._execute_tool(
            "credential_search", {}, None, 0,
        )
        assert "credentials" in json.loads(result)

    @pytest.mark.asyncio
    async def test_dispatches_task_history(self):
        result = await handler._execute_tool(
            "task_history", {}, None, 0,
        )
        assert "tasks" in json.loads(result)


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


# ---- credential_search handler ----

class TestCredentialSearchHandler:
    @pytest.mark.asyncio
    async def test_no_tasks_error(self, monkeypatch):
        h = ToolHandlerMixin()

        async def fake_task_id():
            return 0
        monkeypatch.setattr(h, "_get_any_task_id", fake_task_id)

        result = await h._tool_credential_search({}, None)
        assert "error" in json.loads(result)

    @pytest.mark.asyncio
    async def test_returns_credentials(self, monkeypatch):
        async def fake_cred_search(msg):
            assert msg.TaskID == 5
            # no server-side filter is sent (client-side filtering)
            assert getattr(msg, "Credential", None) is None
            return types.SimpleNamespace(
                Success=True,
                Credentials=[
                    types.SimpleNamespace(
                        Account="Administrator",
                        Credential="Passw0rd!",
                        CredentialType="plaintext",
                        Realm="CORP.LOCAL",
                        Comment="found in unattend.xml",
                    ),
                ],
            )
        monkeypatch.setattr(
            th, "SendMythicRPCCredentialSearch", fake_cred_search,
        )
        h = ToolHandlerMixin()

        async def fake_task_id():
            return 5
        monkeypatch.setattr(h, "_get_any_task_id", fake_task_id)

        result = await h._tool_credential_search({}, None)
        data = json.loads(result)
        assert data["count"] == 1
        cred = data["credentials"][0]
        assert cred["account"] == "Administrator"
        assert cred["credential"] == "Passw0rd!"
        assert cred["type"] == "plaintext"
        assert "truncated" not in data

    @pytest.mark.asyncio
    async def test_filters_client_side(self, monkeypatch):
        def cred(account, realm, ctype):
            return types.SimpleNamespace(
                Account=account, Credential="secret",
                CredentialType=ctype, Realm=realm, Comment="",
            )
        all_creds = [
            cred("Administrator", "CORP.LOCAL", "plaintext"),
            cred("svc_backup", "CORP.LOCAL", "hash"),
            cred("root", "ssh://10.0.0.5", "key"),
        ]

        async def fake_cred_search(msg):
            return types.SimpleNamespace(
                Success=True, Credentials=all_creds,
            )
        monkeypatch.setattr(
            th, "SendMythicRPCCredentialSearch", fake_cred_search,
        )
        h = ToolHandlerMixin()

        async def fake_task_id():
            return 5
        monkeypatch.setattr(h, "_get_any_task_id", fake_task_id)

        # case-insensitive substring match on account
        result = await h._tool_credential_search(
            {"account": "admin"}, None,
        )
        data = json.loads(result)
        assert data["count"] == 1
        assert data["credentials"][0]["account"] == "Administrator"

        # realm + type combined
        result = await h._tool_credential_search(
            {"realm": "corp", "credential_type": "hash"}, None,
        )
        data = json.loads(result)
        assert data["count"] == 1
        assert data["credentials"][0]["account"] == "svc_backup"

        # no match
        result = await h._tool_credential_search(
            {"account": "nonexistent"}, None,
        )
        data = json.loads(result)
        assert data["count"] == 0
        assert data["credentials"] == []


# ---- task_history handler ----

class TestTaskHistoryHandler:
    def _task(self, display_id, command, params, cb=1, completed=True):
        return types.SimpleNamespace(
            TaskID=1000 + display_id, DisplayID=display_id,
            CallbackDisplayID=cb, CommandName=command,
            DisplayParams=params, Status="completed",
            Completed=completed, OperatorUsername="admin",
        )

    def _patch_task_id(self, monkeypatch, h):
        async def fake_task_id():
            return 5
        monkeypatch.setattr(h, "_get_any_task_id", fake_task_id)

    @pytest.mark.asyncio
    async def test_list_recent_first(self, monkeypatch):
        tasks = [
            self._task(1, "shell", "whoami"),
            self._task(3, "ls", "/tmp"),
            self._task(2, "shell", "hostname"),
        ]

        async def fake_search(msg):
            assert msg.TaskID == 0
            assert msg.SearchCallbackID is None
            return types.SimpleNamespace(Success=True, Tasks=tasks)
        monkeypatch.setattr(th, "SendMythicRPCTaskSearch", fake_search)
        h = ToolHandlerMixin()
        self._patch_task_id(monkeypatch, h)

        result = await h._tool_task_history({}, None)
        data = json.loads(result)
        assert data["count"] == 3
        assert [t["task_id"] for t in data["tasks"]] == [3, 2, 1]
        assert "truncated" not in data

    @pytest.mark.asyncio
    async def test_list_filters(self, monkeypatch):
        tasks = [
            self._task(1, "shell", "whoami"),
            self._task(2, "shell", "cat /etc/shadow"),
            self._task(3, "download", "/etc/passwd"),
        ]

        async def fake_search(msg):
            return types.SimpleNamespace(Success=True, Tasks=tasks)
        monkeypatch.setattr(th, "SendMythicRPCTaskSearch", fake_search)
        h = ToolHandlerMixin()
        self._patch_task_id(monkeypatch, h)

        # command filter, case-insensitive substring
        result = await h._tool_task_history({"command": "SHELL"}, None)
        data = json.loads(result)
        assert [t["task_id"] for t in data["tasks"]] == [2, 1]

        # params filter
        result = await h._tool_task_history({"params": "shadow"}, None)
        data = json.loads(result)
        assert [t["task_id"] for t in data["tasks"]] == [2]

        # no match
        result = await h._tool_task_history({"command": "nope"}, None)
        assert json.loads(result)["count"] == 0

    @pytest.mark.asyncio
    async def test_list_callback_filter_server_side(self, monkeypatch):
        async def fake_search(msg):
            assert msg.SearchCallbackID == 3
            return types.SimpleNamespace(Success=True, Tasks=[])
        monkeypatch.setattr(th, "SendMythicRPCTaskSearch", fake_search)
        h = ToolHandlerMixin()
        self._patch_task_id(monkeypatch, h)

        result = await h._tool_task_history({"callback_id": 3}, None)
        assert json.loads(result)["count"] == 0

    @pytest.mark.asyncio
    async def test_list_truncation(self, monkeypatch):
        tasks = [
            self._task(i, "shell", f"cmd{i}")
            for i in range(th.MAX_TASK_RESULTS + 10)
        ]

        async def fake_search(msg):
            return types.SimpleNamespace(Success=True, Tasks=tasks)
        monkeypatch.setattr(th, "SendMythicRPCTaskSearch", fake_search)
        h = ToolHandlerMixin()
        self._patch_task_id(monkeypatch, h)

        result = await h._tool_task_history({}, None)
        data = json.loads(result)
        assert data["count"] == th.MAX_TASK_RESULTS
        assert "truncated" in data
        # most recent first after the cap
        assert data["tasks"][0]["task_id"] == th.MAX_TASK_RESULTS + 9

    @pytest.mark.asyncio
    async def test_detail_returns_output(self, monkeypatch):
        task = self._task(7, "shell", "whoami")

        async def fake_search(msg):
            assert msg.SearchTaskDisplayID == 7
            return types.SimpleNamespace(Success=True, Tasks=[task])

        async def fake_resp_search(msg):
            assert msg.TaskID == 1007  # real TaskID, not display ID
            return types.SimpleNamespace(
                Success=True,
                Responses=[
                    types.SimpleNamespace(Response="corp\\admin"),
                    types.SimpleNamespace(Response="uid=0"),
                ],
            )
        monkeypatch.setattr(th, "SendMythicRPCTaskSearch", fake_search)
        monkeypatch.setattr(
            th, "SendMythicRPCResponseSearch", fake_resp_search,
        )
        h = ToolHandlerMixin()
        self._patch_task_id(monkeypatch, h)

        result = await h._tool_task_history({"task_id": 7}, None)
        data = json.loads(result)
        assert data["command"] == "shell"
        assert data["output"] == "corp\\admin\nuid=0"
        assert "truncated" not in data

    @pytest.mark.asyncio
    async def test_detail_not_found(self, monkeypatch):
        async def fake_search(msg):
            return types.SimpleNamespace(Success=True, Tasks=[])
        monkeypatch.setattr(th, "SendMythicRPCTaskSearch", fake_search)
        h = ToolHandlerMixin()
        self._patch_task_id(monkeypatch, h)

        result = await h._tool_task_history({"task_id": 99}, None)
        assert "not found" in json.loads(result)["error"]

    @pytest.mark.asyncio
    async def test_detail_output_truncation(self, monkeypatch):
        task = self._task(7, "shell", "cat bigfile")

        async def fake_search(msg):
            return types.SimpleNamespace(Success=True, Tasks=[task])

        async def fake_resp_search(msg):
            return types.SimpleNamespace(
                Success=True,
                Responses=[
                    types.SimpleNamespace(
                        Response="x" * (th.MAX_TASK_OUTPUT_CHARS + 500)
                    ),
                ],
            )
        monkeypatch.setattr(th, "SendMythicRPCTaskSearch", fake_search)
        monkeypatch.setattr(
            th, "SendMythicRPCResponseSearch", fake_resp_search,
        )
        h = ToolHandlerMixin()
        self._patch_task_id(monkeypatch, h)

        result = await h._tool_task_history({"task_id": 7}, None)
        data = json.loads(result)
        assert len(data["output"]) == th.MAX_TASK_OUTPUT_CHARS
        assert "truncated" in data
