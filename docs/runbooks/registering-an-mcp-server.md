# Runbook: Give Mera's agents an external MCP server

Agents can use tools from external MCP servers you choose (P2-M9, docs/05 §2): a Notion
server for research, an image generator, a search server. Nothing an external server
offers is usable until an Admin has reviewed it, and every write waits for a person.

## 1. Register

**MCP Integrations → Add a server**, as an Admin:

- **Remote (https URL)**: the server's Streamable HTTP endpoint. Its address must resolve
  to public IPs only; private, loopback and metadata addresses are refused.
- **Package, run in the sandbox**: a stdio server as a *pinned* package:
  `npm:@scope/server@1.2.3`, `pypi:mcp-server-x==1.2.3` or `oci:image@sha256:…`.
  Unpinned versions and shell commands are refused. List the hostnames it may reach under
  **Hosts it may reach**; everything else is blocked by `mcp-egress`. Each sandboxed server
  runs as its own user with a private home, gets only its own credentials and a proxy
  address that works for its own list, and cannot reach the relay without
  `MCP_SANDBOX_TOKEN` (which it never sees). No hosts listed: no network at all.

Or paste a Claude Desktop `mcpServers` object. Values in `env` and `headers` move to
encrypted credentials and are never shown again.

Agents can also *propose* a server (`propose_mcp_server` over MCP). A proposal is just a
registration waiting for review.

## 2. Test and authorize

**Test connection** detects the protocol era and lists the tools. Every tool arrives
**disabled**. If the server uses OAuth, **Authorize** opens its sign-in. Mera checks the
issuer, binds the token to that server, and never sends it anywhere else.

## 3. Review every tool

For each tool:

- **Risk**: annotations from an uncurated server are hints. A tool calling itself
  read-only is still a *write* until you set it to *read* yourself.
- **Policy**: *ask every time* (default for writes), *no approval* (reads; for a write
  it needs a second factor), or *blocked*.
- **Warnings**: a description that reads like instructions to the model (tool poisoning)
  is flagged. Read it; the tool cannot be enabled until you acknowledge the warning.
- **Agents**: which agents may call it (writer, research, media, …). The Publisher and
  Approval agents never can.

## 4. Approve

**Approve** asks for your authenticator code or PIN. Only then can bound agents see the
enabled tools, and only in runs for the groups the server is scoped to.

## 5. Day to day

- A write call waits on a card (phone and web) showing the **exact arguments**. Allow or
  deny; an unanswered call expires after 30 minutes and is never run.
- If the server changes a tool's definition, that tool is disabled until reviewed again.
- Results reach agents as untrusted text: they can shape a draft, never cause a publish.

## 6. Media generation through MCP

To generate images with an approved MCP tool: enable it, bind it to the **media** agent,
set it to *no approval*, then set the workspace's `media.provider` to `mcp` and
`media.mcp_tool` to its name (e.g. `imagegen__generate_image`).

If anything about a server looks wrong, see [revoking a compromised MCP server](revoking-a-compromised-mcp-server.md).
