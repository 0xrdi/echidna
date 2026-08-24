import json
from mythic_container.MythicRPC import *


class ReportMixin:

    async def _generate_report(self, request, response_key):
        sections = ["# Operation Report\n"]

        all_callbacks = []
        try:
            cb_search = await SendMythicRPCCallbackSearch(
                MythicRPCCallbackSearchMessage()
            )
            if cb_search.Success:
                all_callbacks = cb_search.Results
                active = [c for c in all_callbacks if c.Active]
                dead = [c for c in all_callbacks if not c.Active]
                sections.append(
                    f"## Callbacks ({len(active)} active, "
                    f"{len(dead)} dead)\n"
                )
                for cb in active:
                    sections.append(
                        f"- **#{cb.DisplayID}** `{cb.PayloadType}` "
                        f"— {cb.User}@{cb.Host} ({cb.Ip}) "
                        f"pid {cb.PID} `{cb.ProcessName}` "
                        f"integrity={cb.IntegrityLevel}"
                    )
                sections.append("")
        except Exception as e:
            sections.append(f"Callbacks: error — {e}\n")

        task_id = await self._get_any_task_id()

        if task_id:
            try:
                cred_resp = await SendMythicRPCCredentialSearch(
                    MythicRPCCredentialSearchMessage(TaskID=task_id)
                )
                if cred_resp.Success:
                    creds = cred_resp.Credentials
                    sections.append(
                        f"## Credentials ({len(creds)})\n"
                    )
                    if creds:
                        for c in creds:
                            realm = f" ({c.Realm})" if c.Realm else ""
                            cred_val = c.Credential or ""
                            truncated = (
                                cred_val[:40] + "..."
                                if len(cred_val) > 40
                                else cred_val
                            )
                            sections.append(
                                f"- `{c.Account}`{realm} — "
                                f"{c.CredentialType}: `{truncated}`"
                            )
                    else:
                        sections.append("No credentials stored.")
                    sections.append("")
            except Exception as e:
                sections.append(f"Credentials: error — {e}\n")

            try:
                art_resp = await SendMythicRPCArtifactSearch(
                    MythicRPCArtifactSearchMessage(
                        TaskID=task_id,
                        SearchArtifacts=MythicRPCArtifactSearchArtifactData(),
                    )
                )
                if art_resp.Success:
                    arts = art_resp.Artifacts
                    sections.append(f"## Artifacts ({len(arts)})\n")
                    if arts:
                        for a in arts:
                            cleanup = (
                                " [needs cleanup]"
                                if a.NeedsCleanup else ""
                            )
                            sections.append(
                                f"- `{a.ArtifactType or 'Unknown'}` "
                                f"{a.Host or ''} — "
                                f"{a.ArtifactMessage or ''}"
                                f"{cleanup}"
                            )
                    else:
                        sections.append("No artifacts logged.")
                    sections.append("")
            except Exception as e:
                sections.append(f"Artifacts: error — {e}\n")

            try:
                all_tasks = []
                for cb in all_callbacks:
                    t_resp = await SendMythicRPCTaskSearch(
                        MythicRPCTaskSearchMessage(
                            TaskID=0,
                            SearchCallbackID=cb.DisplayID,
                        )
                    )
                    if t_resp.Success and t_resp.Tasks:
                        all_tasks.extend(t_resp.Tasks)
                completed = sorted(
                    [t for t in all_tasks if t.Completed],
                    key=lambda t: t.DisplayID,
                )
                sections.append(
                    f"## Tasks ({len(completed)} completed, "
                    f"{len(all_tasks)} total)\n"
                )
                if completed:
                    for t in completed:
                        status = t.Status or "done"
                        sections.append(
                            f"- Task #{t.DisplayID} "
                            f"cb#{t.CallbackDisplayID} "
                            f"`{t.CommandName} "
                            f"{t.DisplayParams or ''}` "
                            f"— {status}"
                        )
                else:
                    sections.append("No completed tasks.")
                sections.append("")
            except Exception as e:
                sections.append(f"Tasks: error — {e}\n")
        else:
            sections.append(
                "## Credentials / Artifacts / Tasks\n"
                "No tasks found — cannot query these sections "
                "without at least one executed task.\n"
            )

        usage = self._token_usage.get(
            request.ChannelID, {"input": 0, "output": 0},
        )
        total = usage["input"] + usage["output"]
        sections.append("## Token Usage\n")
        sections.append(
            f"- Input: **{usage['input']:,}**\n"
            f"- Output: **{usage['output']:,}**\n"
            f"- Total: **{total:,}**"
        )
        sections.append("")

        await self.send_text(
            request, response_key,
            content="\n".join(sections),
        )
        await self.send_complete(
            request, response_key, complete_request=True,
        )
        self._pending_resets.add(request.ChannelID)

    async def _export_chat(self, request, response_key):
        lines = [
            f"# Chat Export — {request.ChannelName or 'channel'}\n",
        ]
        for msg in request.Context:
            ts = msg.CreatedAt or ""
            sender = msg.SenderDisplayName or msg.AuthorType or "unknown"
            if msg.AuthorType == "ai":
                header = "### Echidna"
            else:
                header = f"### {sender}"
            if ts:
                header += f" ({ts})"
            lines.append(header)
            lines.append("")
            lines.append(msg.Message or "*(empty)*")
            lines.append("")
            lines.append("---")
            lines.append("")

        usage = self._token_usage.get(
            request.ChannelID, {"input": 0, "output": 0},
        )
        total = usage["input"] + usage["output"]
        lines.append(
            f"*Exported {len(request.Context)} messages. "
            f"Tokens: {total:,} "
            f"({usage['input']:,} in / {usage['output']:,} out)*"
        )

        await self.send_text(
            request, response_key,
            content="\n".join(lines),
        )
        await self.send_complete(
            request, response_key, complete_request=True,
        )
        self._pending_resets.add(request.ChannelID)
