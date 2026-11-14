# Runbook: Use Mera from Claude (Mera's MCP server)

Mera exposes an MCP server so Claude Desktop, Claude Code or any MCP client can read
your workspace, draft posts and send them for approval (P2-M8, docs/05 §1). A client can
**never** approve, publish, resume automation or see a credential. Asking Claude to post
creates an approval request, and the approval happens on your phone or in the web app.

## 1. Deployment settings

Set these in `.env` and restart `api` and `mcp`:

| Variable | Value |
| --- | --- |
| `MCP_SERVICE_SECRET` | 16+ random characters, the same for `api` and `mcp`: `openssl rand -hex 24` |
| `MCP_RESOURCE_URL` | the MCP endpoint as clients reach it, e.g. `https://mera.example.com/mcp` |
| `MERA_API_URL` | the API's public URL (the OAuth issuer; `mcp` also calls it) |
| `MCP_ALLOWED_ORIGINS` | optional JSON list of browser origins allowed to call `/mcp` |
| `MERA_PUBLIC_MCP_URL` | (web) the endpoint shown on the MCP screen |

The reverse proxy sends `/mcp` and `/.well-known/oauth-protected-resource` to the `mcp`
service (port 8002), and `/.well-known/oauth-authorization-server` to `api`.

## 2. Remote clients (Streamable HTTP, OAuth 2.1)

Add the server in the client with the URL from `MCP_RESOURCE_URL`. The client finds the
authorization server on its own (RFC 9728), and you land on Mera's consent page. There
you pick **one workspace** and see in plain words what the client may do. Scopes are
granted as asked; a client that later needs more (say `drafts:write`) gets an
`insufficient_scope` challenge and asks you again.

- Clients identify themselves with a Client ID Metadata Document (an https URL).
  Dynamic Client Registration is not offered.
- Tokens last an hour and refresh for 30 days, rotating on every refresh.
- MCP tokens are refused by the REST API directly. They only work through `mcp`.

## 3. Claude Desktop on your machine (stdio, API key)

1. In Mera: **Settings → API keys**, create a key with the scopes you want (for example
   `drafts:read drafts:write groups:read approvals:read`).
2. In Claude Desktop's config:

```json
{
  "mcpServers": {
    "mera": {
      "command": "python",
      "args": ["-m", "mera.mcp"],
      "env": { "MERA_API_URL": "https://mera.example.com", "MERA_API_KEY": "mera_sk_live_…" }
    }
  }
}
```

The backend package must be installed where Claude Desktop runs (`uv pip install -e
backend`). The key reaches one workspace only.

## 4. What it can do

Read: workspaces, accounts, groups, post sets and drafts, pending approvals, calendar,
analytics, inbox comments (marked as untrusted text), agent runs, MCP integrations.

Write: `create_post` (targets must be named, never guessed), `request_approval`,
`draft_reply` (always as the commented account), `stop_automation` (stop only),
`propose_mcp_server` (lands in review), `delete_comment` (needs a second factor, see
below). Prompts: `setup_wizard`, `group_setup`.

**Second factors never go through the chat.** `delete_comment` returns a link to a Mera
page (a URL elicitation for modern clients, a link in the error for older ones). You
confirm there with your authenticator or PIN, then the client retries and picks up the
result. Never type a PIN into Claude.

## 5. Seeing and revoking clients

**MCP Integrations** lists connected clients (yours; Admins see everyone's) with their
scopes and last use, and **Disconnect** revokes one at once. Calls made through MCP are
audited with surface `mcp` and listed on the same screen.

## 6. Troubleshooting

| Symptom | Check |
| --- | --- |
| 401 with `resource_metadata` on every call | The client did not finish OAuth, or the token expired and refresh failed |
| `insufficient_scope` | Expected: approve the extra scope when the client asks |
| `invalid_target` at consent | The client's `resource` does not equal `MCP_RESOURCE_URL` |
| `invalid_client` | The client's metadata document is unreachable or does not name itself |
| Calls work but are audited as `api` | `MCP_SERVICE_SECRET` differs between `api` and `mcp` |
