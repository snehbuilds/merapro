# ADR-0013: External MCP servers are default-deny, sandboxed and approval-gated, and Mera speaks both MCP eras

**Status:** Accepted
**Date:** 2026-11-08

## Context

The brief asks that you be able to **add your own MCP servers** and let Mera's agents use
them, in addition to Mera exposing its own MCP server. Two facts shape how.

**First, an external MCP server is third-party code and third-party text.** A stdio server
is a package Mera would execute. A remote server returns text that goes straight into an
LLM context, and its tool *descriptions* sit in that context on every step whether or not
the tool is called. A registry that connects a server and hands every agent every tool it
offers sets its blast radius by whatever that server can do. Mera's agents run next to
credentials for many social accounts.

**Second, the protocol changed shape.** MCP revision `2026-07-28` made the protocol
stateless. It removed the `initialize` handshake and `Mcp-Session-Id`, added a mandatory
`server/discover`, replaced server-initiated requests with Multi Round-Trip Requests
(`input_required`), moved change notifications to `subscriptions/listen`, deprecated
Sampling, Roots and Logging, and deprecated Dynamic Client Registration in favour of
Client ID Metadata Documents. Most clients and servers in use still speak `2025-11-25` or
earlier.

## Decision

### 1. Dual-era in both directions

Mera's MCP server answers modern per-request `_meta` and legacy `initialize`. Mera's MCP
client detects each registered server's era once, using the spec's transport-specific
rules, and caches it on the registration. The deprecated HTTP+SSE transport is not
supported either way.

### 2. Registration is not authorization

Every registration, however it is created (form, JSON import, catalog, or Mera's own
`add_mcp_server` tool), lands in `pending_review` with every tool disabled. An agent may
call a tool only if it passes three checks: the tool is enabled in the workspace, the
registration is in scope for the run's account group, and an explicit binding names that
agent. The Publisher and Approval agents can never be bound.

### 3. Admins classify risk; servers' own hints are not trusted

Each tool gets a reviewed `risk_class` (`read`, `write`, `destructive`). Server-supplied
annotations such as `readOnlyHint` count only for servers on a curated list. Otherwise a
tool defaults to `write`. A schema hash is recorded at review, and any change disables
the tool until it is reviewed again.

### 4. Write tools pause for a human

`write` and `destructive` tools default to `approval_policy = always`. Each call becomes an
`approval_request` with `subject_type = mcp_tool_call`, delivered through the existing
bots, showing the exact canonical arguments. The content hash binds the approval to the
server, tool, schema hash and argument bytes. This reuses the post-version invariant
([ADR-0004](0004-immutable-post-versions.md)) rather than inventing a second approval
model.

### 5. stdio servers run in their own sandbox service

`mcp-sandbox` runs one locked-down container per stdio server: gVisor where available,
read-only root, no host mounts, no route to Postgres or the API. All outbound traffic
goes through an `egress` proxy enforcing a per-registration hostname allowlist and the
SSRF deny-list. Packages must be pinned to an exact version or image digest.

### 6. Humans activate; models only propose

On Mera's own MCP surface, `add_mcp_server` proposes and never activates. Enabling a write
tool, setting `auto` on one, or re-enabling a server needs an Admin and a second factor,
delivered as URL-mode elicitation so no secret ever passes through the model.

## Consequences

- Adding a server takes a few more clicks than "paste and go": test, review, bind. That
  friction is the feature, and the JSON importer and catalog keep it short.
- Two more containers in the Compose file, and a host where gVisor is not available falls
  back to weaker isolation (rootless with seccomp). The setup runbook says which one a
  deployment got.
- External write tools are slow by default, because a human is in the loop. Teams that
  trust a specific tool can set `auto` with a second factor, and the bot posts an FYI
  after each call.
- Supporting both protocol eras costs a code path on each side. It is removable once
  legacy usage dies out, which the cached era per registration makes measurable.
- OAuth grants for external servers belong to the workspace registration, not to
  individual users. Simple and auditable, but an agent cannot act "as" the person who
  triggered it.

## Alternatives considered

**Trust the server's annotations to classify tools.** The spec itself says annotations are
untrusted unless the server is trusted. Using them as the default would let any server
label its write tools read-only.

**Run stdio servers inside the worker process or container.** Simpler, but it puts
third-party code next to decrypted social and LLM credentials and on a network that
reaches Postgres. One malicious package would compromise every workspace.

**Modern-only MCP.** Cleaner, but Claude Desktop builds and most published servers still
speak the handshake revisions. Modern-only would mean neither direction works with much of
today's ecosystem.

**A separate approval model for tool calls.** It would duplicate the token ledger, quorum,
expiry and bot rendering that already exist for posts, and a second model is a second
place for the invariant to be wrong.

## Revisit when

- Legacy (`2025-11-25` and earlier) traffic falls below a few percent of MCP requests on
  both sides. Then drop the legacy paths.
- A concrete need appears for per-user OAuth grants on external servers.
- The MCP Registry gains a verification or signing scheme strong enough to extend
  annotation trust beyond the curated list.
- A managed sandbox (a hosted code-execution service, say) becomes cheaper to run than
  `mcp-sandbox` at the required isolation level.
