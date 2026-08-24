OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_callbacks",
            "description": (
                "List active Mythic callbacks (implants) in the current "
                "operation. Returns callback ID, host, user, payload type, "
                "IP, OS, and process info."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": (
                "Execute a command on a Mythic callback and return the "
                "output. Use the callback's payload type commands "
                "(e.g. 'shell' for Poseidon/Apollo to run shell commands, "
                "'ls' to list files, 'download' to download a file). "
                "Waits for the task to complete and returns the output."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "callback_id": {
                        "type": "integer",
                        "description": "Callback display ID (the # number)",
                    },
                    "command": {
                        "type": "string",
                        "description": "Command name (e.g. shell, ls, pwd, whoami, download)",
                    },
                    "params": {
                        "type": "string",
                        "description": "Command parameters (e.g. 'hostname' for shell)",
                    },
                },
                "required": ["callback_id", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "credential_create",
            "description": (
                "Store a credential in the Mythic credential store. Use "
                "this whenever you discover credentials during operations "
                "(passwords, hashes, tokens, API keys, SSH keys). The "
                "credential is stored in the current operation and visible "
                "to all operators."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "credential_type": {
                        "type": "string",
                        "description": (
                            "Type: plaintext, hash, ticket, certificate, "
                            "token, key, or other"
                        ),
                    },
                    "account": {
                        "type": "string",
                        "description": "Username or account name",
                    },
                    "credential": {
                        "type": "string",
                        "description": "The credential value (password, hash, etc.)",
                    },
                    "realm": {
                        "type": "string",
                        "description": "Domain, host, or service (e.g. CORP.LOCAL, ssh://10.0.0.1)",
                    },
                    "comment": {
                        "type": "string",
                        "description": "How/where the credential was found",
                    },
                },
                "required": ["credential_type", "account", "credential"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_artifact",
            "description": (
                "Log an OPSEC artifact in Mythic. Use this to track files "
                "dropped, registry keys modified, services created, or any "
                "other forensic footprint left on a target. Artifacts appear "
                "in the Mythic artifacts view for OPSEC review and cleanup."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact": {
                        "type": "string",
                        "description": "What was created/modified (e.g. /tmp/.payload, HKLM\\...\\Run\\backdoor)",
                    },
                    "artifact_type": {
                        "type": "string",
                        "description": "Type: File, Registry, Service, Scheduled Task, User Account, Process, Network, Other",
                    },
                    "host": {
                        "type": "string",
                        "description": "Host where the artifact exists",
                    },
                    "needs_cleanup": {
                        "type": "boolean",
                        "description": "Whether this artifact should be cleaned up before exiting",
                    },
                },
                "required": ["artifact", "artifact_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "event_log",
            "description": (
                "Write an entry to the Mythic operation event log. Use "
                "this to record significant events: access gained, "
                "credentials found, persistence installed, lateral "
                "movement completed, or any milestone worth logging "
                "in the operation timeline."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Event description for the operation log",
                    },
                    "level": {
                        "type": "string",
                        "description": "Level: info, warning. Default info.",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tag_task",
            "description": (
                "Tag a Mythic task with a MITRE ATT&CK technique ID. "
                "Use this after executing a command to tag the task with "
                "the relevant technique. Tags are searchable in Mythic "
                "and appear in reporting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "Mythic task ID to tag (from execute_command output)",
                    },
                    "technique_id": {
                        "type": "string",
                        "description": "MITRE ATT&CK technique ID (e.g. T1548.003, T1059.004)",
                    },
                },
                "required": ["task_id", "technique_id"],
            },
        },
    },
]

TOOL_SUMMARIES = {
    "list_callbacks": "see active implants",
    "execute_command": "run a command on an implant callback",
    "credential_create": "store found credentials in Mythic",
    "create_artifact": "log OPSEC artifacts (files dropped, services created)",
    "event_log": "write to the operation timeline",
    "tag_task": "tag a task with a MITRE ATT&CK technique ID",
}


def tool_prompt_section():
    lines = ["You have tools to interact with Mythic directly:"]
    for t in OPENAI_TOOLS:
        name = t["function"]["name"]
        summary = TOOL_SUMMARIES.get(name, t["function"]["description"])
        lines.append(f"- {name}: {summary}")
    return "\n".join(lines)


ANTHROPIC_TOOLS = [
    {
        "name": t["function"]["name"],
        "description": t["function"]["description"],
        "input_schema": t["function"]["parameters"],
    }
    for t in OPENAI_TOOLS
]
