# ADR-0009: Server-Sent Events rather than WebSockets

**Status:** Accepted
**Date:** 2026-11-08

## Context

Several Mera surfaces need live updates: agent run progress in the run viewer, the
approval queue staying in sync with decisions made in a bot, the unified inbox, publish
outcomes, and notifications.

The default reach for "real-time" is WebSockets. It is worth checking whether the traffic
is actually bidirectional before paying for a bidirectional protocol.

It is not. Every one of those flows is **server to client**. Client-to-server actions —
approving, editing, scheduling — are discrete, idempotency-keyed, auditable operations
that want to be HTTP requests with status codes, retries, and an error envelope. Sending
an approval over a socket frame would mean reimplementing all of that inside a custom
message protocol.

## Decision

**Server-Sent Events** for server-to-client updates, ordinary HTTP for everything else.

```
GET /api/v1/workspaces/{id}/events?types=approval.*,agent_run.*
Accept: text/event-stream
Last-Event-ID: 48219
```

Backed by the durable `event` table plus `pg_notify`: the table is the source of truth,
`NOTIFY` is only a wakeup.

## Consequences

**Good**

- **Replay comes free and is genuinely needed.** `EventSource` sends `Last-Event-ID`
  automatically on reconnect, and the server replays from the `event` table. A laptop that
  slept for ten minutes catches up rather than silently missing that a post was approved.
  Building this on WebSockets means designing a resume protocol by hand, and getting
  sequence numbering and gap detection right.
- **Reconnection is the browser's job.** `EventSource` reconnects with backoff with no
  client code.
- **It is HTTP.** Proxies, load balancers, corporate firewalls, HTTP/2 multiplexing,
  `Authorization` headers, and standard observability all work without special
  configuration. WebSocket upgrades are a recurring source of proxy misconfiguration.
- **Per-event authorization is natural.** Each event is evaluated against the current
  principal before being written to the stream, so a role downgrade takes effect
  mid-stream. With a socket this tends to be checked once at connect.
- **One connection per API process holds the `LISTEN`**, fanning out in-process to all
  local SSE clients. This matters under
  [ADR-0002](0002-single-postgres-datastore.md)'s connection budget: 500 connected
  browsers still cost one Postgres connection per API process.
- Trivially testable: an SSE stream is a readable HTTP response.

**Bad**

- **Browser connection limit on HTTP/1.1**: six concurrent connections per origin, and an
  open SSE stream consumes one. Over HTTP/2 the limit effectively disappears, and Mera
  uses a single stream per tab regardless. Documented because it bites people who open one
  stream per widget.
- **No client-to-server channel.** Correct here, and it would be a real constraint if a
  collaborative editing feature ever needed low-latency bidirectional traffic.
- **Text only.** Binary payloads need base64. Mera sends JSON, so this costs nothing.
- **Proxy buffering can delay events.** Mitigated by a heartbeat comment line every 20
  seconds and explicit `X-Accel-Buffering: no` where nginx is in front.
- **Long-lived requests occupy a worker slot.** Fine with async workers, and a reason the
  API runs on an async server rather than a thread-per-request one.

## Alternatives considered

**WebSockets.** The reflexive choice. Would require a hand-built resume protocol to match
what `Last-Event-ID` gives for free, a custom message framing format, its own
authentication story for the upgrade handshake, and proxy configuration. All of that cost
buys a client-to-server channel that nothing in Mera needs.

**Long polling.** Works everywhere, wastes connections and server time, and has worse
latency. SSE is strictly better and equally compatible.

**Polling with `ETag`.** Simplest of all and adequate for the calendar. Inadequate for
agent run progress, where a step every few seconds would mean polling every few seconds
across every open tab. Worth keeping in mind as a fallback for low-frequency screens if
SSE connection counts ever become a problem.

**A hosted real-time service** such as Pusher or Ably. Removes the implementation, adds a
vendor, a cost, and an external dependency to a self-hosted product. Wrong trade.

## Revisit when

- A feature genuinely needs low-latency client-to-server messaging, such as collaborative
  cursors in the Draft Studio. Even then, the right move is a WebSocket **alongside** SSE
  for that one feature rather than migrating everything.
- Mobile clients find SSE awkward. Native `EventSource` support varies, though HTTP
  streaming is universally available and push notifications cover the background case
  better than any persistent connection would.
- Concurrent stream counts grow enough that connection handling becomes a bottleneck, at
  which point the low-frequency screens move to `ETag` polling and SSE is reserved for
  agent runs and approvals.
