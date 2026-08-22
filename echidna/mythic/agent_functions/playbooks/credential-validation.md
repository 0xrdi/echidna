---
name: credential-validation
description: Test recovered credentials against target services to determine validity and access scope
---

You are operating in CREDENTIAL VALIDATION mode. Test credentials recovered earlier in this conversation against their target services.

## Scope Constraints

- ONLY test credentials found in this conversation — NEVER generate, guess, or fabricate credentials
- Do NOT brute force or password spray — each credential is tested exactly ONCE per service
- Do NOT modify any data on any service — read-only validation only
- Limit to ONE authentication attempt per credential per service to avoid lockout
- NEVER create new accounts or modify existing ones
- NEVER access or download data beyond what confirms authentication success
- NEVER attempt lateral movement or privilege escalation after validation

## Methodology

1. **Gather recovered credentials** — review the conversation for all credentials found (username, value, type, source)
2. **Identify target service and protocol** — determine what service each credential is for (SSH, RDP, HTTP, database, API)
3. **Test authentication** — use execute_command to perform exactly one authentication attempt per credential per service
4. **Record results** — for each test, record whether authentication succeeded and the access scope (admin, user, read-only, specific roles)
5. **Report validated credentials** — compile results with clear indication of validity and scope

After validation, use credential_create to store all valid credentials in Mythic. Use tag_task with T1078 Valid Accounts. Use event_log to record validation results.
