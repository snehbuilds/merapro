# Runbook: Revoke a compromised MCP server or client

Use this when an external MCP server behaves unexpectedly (odd tool descriptions, calls
you did not expect, data showing up where it should not), or when an MCP client's
credentials may have leaked.

## An external server agents use

1. **MCP Integrations → the server → Disable.** No second factor: stopping is always
   allowed. This, at once:
   - makes every tool of the server invisible to agents,
   - cancels every pending tool call (they are never run),
   - stops its sandboxed process and revokes its egress grant (stdio servers).
2. Review **Recent calls** and the audit log (`mcp_tool_call.*`, `mcp_server.*`) for what
   it was asked and what it returned. Results were stored as untrusted text.
3. If it used OAuth, revoke Mera's grant at the provider as well. Mera's stored token is
   kept encrypted and sent nowhere else; re-registering starts a fresh consent.
4. If a post set may have been influenced by its results, check the agent runs that
   called it (Agents screen) and reject or edit any affected drafts before approval.

To bring it back: register it again (it returns to review) rather than re-enabling, so
every tool is reviewed again.

## An MCP client (Claude, another app)

1. **MCP Integrations → Connected clients → Disconnect.** The token stops working at the
   next call. API keys used over stdio are revoked under **Settings → API keys**.
2. To cut every MCP client at once, rotate `MCP_SERVICE_SECRET` on `api` and `mcp`:
   MCP tokens then fail at the API until the services share the new secret.
3. Check audit entries with surface `mcp` for what the client did. It could not approve,
   publish, resume automation or read credentials; drafts and approval requests it made
   are visible and can be rejected.

## If automation itself is at risk

Engage the kill switch (see [incident-kill-switch.md](incident-kill-switch.md)). Resuming
needs an Admin and a second factor.
