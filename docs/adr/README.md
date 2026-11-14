# Architecture Decision Records

Each ADR records one decision that was expensive to make and would be expensive to
reverse. They exist so that six months from now, when something here looks wrong, the
reasoning is available and the reversal can be judged rather than guessed at.

Format per record: Status, Context, Decision, Consequences, Alternatives considered, and
**Revisit when** — the concrete signal that should trigger reopening the question.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-python-fastapi-backend.md) | Python and FastAPI for the backend, not Node and NestJS | Accepted |
| [0002](0002-single-postgres-datastore.md) | PostgreSQL as the only datastore: no Redis, no object-storage service | Accepted |
| [0003](0003-procrastinate-over-celery-and-temporal.md) | procrastinate for jobs, with approval state as an explicit state machine | Accepted |
| [0004](0004-immutable-post-versions.md) | Immutable post versions anchored by a content hash | Accepted, amended by 0011 |
| [0005](0005-in-house-agent-orchestrator.md) | An in-house orchestrator rather than LangGraph | Accepted |
| [0006](0006-rls-plus-repository-tenancy.md) | Two independent tenancy layers: RLS and repository scoping | Accepted |
| [0007](0007-pgvector-over-dedicated-vector-store.md) | pgvector rather than a dedicated vector database | Accepted |
| [0008](0008-adapter-pattern-platforms-and-channels.md) | One adapter pattern for both social platforms and approval channels | Accepted |
| [0009](0009-sse-over-websockets.md) | Server-Sent Events rather than WebSockets | Accepted |
| [0010](0010-litellm-as-llm-gateway.md) | LiteLLM as the single LLM gateway, so any provider key works | Accepted |
| [0011](0011-post-sets-and-per-account-variants.md) | Post sets with per-account variants, and approval at the variant level | Accepted |
| [0012](0012-account-groups-and-settings-resolution.md) | Account groups, and two settings-resolution rules | Accepted |
| [0013](0013-external-mcp-default-deny-and-dual-era.md) | External MCP servers are default-deny, sandboxed and approval-gated; Mera speaks both MCP eras | Accepted |
| [0014](0014-approval-request-state-is-derived.md) | Approval request state is derived from its variants; scoped revocation; caps resolve strictest; no Phase 1 second factor | Accepted |
| [0015](0015-phase1-delivery-decisions.md) | How Phase 1 delivered approvals, publishing and the bot: placement on approval, held vs failed, disjoint requests, one bot per deployment | Accepted |
| [0016](0016-phase2-delivery-decisions.md) | How Phase 2 was delivered (P2-M1 to P2-M9): approval validity, second factors, fediverse connects, WhatsApp, media budgets, reply identity, scheduler proposals, MCP server auth and tools, MCP client registry and sandbox | Accepted |
| [0017](0017-phase3-delivery-decisions.md) | How Phase 3 is delivered: evidence minimums, hashtag stats, calibration, insights and weights, A/B, earned autopilot, digests and webhooks, cloning and evergreen, MCP catalog/budgets/breaker/source, alerts, data rights and step-up | Accepted |

## Relative importance

Not all of these matter equally. If you only read three, read:

- [0004](0004-immutable-post-versions.md) together with
  [0011](0011-post-sets-and-per-account-variants.md), which are the decisions the
  product's central promise rests on. 0004 establishes that an approval names exact
  content; 0011 extends it so an approval also names an exact account.
- [0012](0012-account-groups-and-settings-resolution.md), which contains the one
  deliberate inconsistency in the design — most settings inherit most-specific-first,
  approval policy and autopilot mode inherit strictest-first — and explains why the
  inconsistency is safer than the consistency.
- [0002](0002-single-postgres-datastore.md), which is the one most likely to be revisited
  as usage grows, and which multi-account posting pushes on hardest since job volume
  scales with account count.
