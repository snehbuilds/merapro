# ADR-0005: An in-house agent orchestrator rather than LangGraph

**Status:** Accepted
**Date:** 2026-11-08

## Context

Mera runs ten agents in multi-step workflows with handoffs, retries, guardrail gates, and
human-in-the-loop pauses that can last hours. The obvious move is to adopt an agent
framework. LangGraph is the leading candidate; the Claude Agent SDK, CrewAI, and AutoGen
are others.

Mera's requirements of that layer are unusual in a specific way. The hard parts are not
reasoning or tool-use orchestration, which frameworks do well. They are:

1. A **complete audit trail**: every prompt, output, tool call, and decision persisted and
   queryable, because this is a system that publishes in someone's name.
2. **Per-step cost attribution**, rolling up to per-published-post cost.
3. **Human pauses measured in hours or days**, surviving process restarts and deployments.
4. **Resumability** from an arbitrary step after a failure.
5. **Guardrails evaluated outside the agent**, so an agent cannot influence its own gate.

Every one of those wants state in our own tables.

## Decision

A small in-house orchestrator:

- Agents are typed classes: `Agent[TIn, TOut]` with Pydantic input and output models and
  an explicit tool allowlist.
- Workflows are **static step templates** for the four built-in cases, not LLM-planned.
  The orchestrator model is invoked only for custom workflows and failure recovery, so
  most runs spend zero orchestration tokens.
- A handoff is a **queued job** carrying a typed payload, which makes it durable,
  retryable, and visible.
- State lives in `agent_run`, `agent_step`, and `guardrail_result`.
- The `Agent` base class records the step, including prompt, output, usage, and cost,
  around every LLM call. It is not optional instrumentation that can be forgotten.

## Consequences

**Good**

- The audit trail is the execution model, not a layer bolted beside it. There is exactly
  one answer to "where is this run".
- Cost accounting is a return type. `LLMProvider` returns `Usage`, the base class writes
  it, and the rollup is a `SUM`.
- A human pause is a row with a state, so a week-long wait costs nothing and survives any
  number of deployments.
- Resuming from step 7 is a query plus a deferred job.
- Guardrails run in the orchestrator, structurally outside the agent.
- Agent unit tests need no framework harness: construct the input model, fake the
  `LLMProvider`, assert the output model.
- Zero framework version churn in the most safety-sensitive part of the system.

**Bad**

- **We maintain it.** Retry semantics, step sequencing, and failure propagation are ours.
  Bounded by keeping workflows static and shallow, but real.
- **No graph-shaped reasoning.** Cyclic plans, dynamic replanning, and agent-chooses-agent
  patterns are not supported. Mera's workflows are linear with branches, so this costs
  nothing today and would cost real work if requirements changed.
- **Reinventing some wheels**, notably structured-output retry on validation failure and
  tool-call loop handling.
- **Less community knowledge.** A new contributor who knows LangGraph has to learn our
  conventions instead.

## Alternatives considered

**LangGraph.** Mature, good at graph-shaped reasoning, has checkpointing. Rejected because
adopting it would mean maintaining our audit and cost layer *and* its checkpoint state,
with two sources of truth about where a run is. Its checkpointing is designed for
conversational resumption rather than for "resume this step after a human approves,
possibly next Tuesday, possibly after a redeploy". We would end up writing the same tables
anyway and then reconciling them with the framework's.

**Claude Agent SDK.** Excellent for conversational agents with tool use. Mera's agents are
non-conversational typed transformations, which is a different shape. It would also couple
the agent layer to one provider, against the explicit requirement of provider agnosticism.

**CrewAI or AutoGen.** Oriented toward multi-agent conversation and role-play delegation.
Mera's agents do not converse; they hand off typed payloads. The abstraction does not fit.

**Temporal for agent workflows.** Would solve durability and resumability properly. Same
operational objection as in [ADR-0003](0003-procrastinate-over-celery-and-temporal.md),
and the same migration path remains open because state is explicit.

## Revisit when

- Custom workflows become genuinely graph-shaped, with cycles or dynamic replanning.
- The orchestrator exceeds roughly 1,000 lines, which would indicate it is growing into a
  framework and should be replaced by one.
- A framework appears whose checkpointing is built for multi-day human-in-the-loop pauses
  rather than conversational resumption.
- Hiring becomes a consideration and framework familiarity has real onboarding value.

The migration path is reasonable: agents are already typed functions with explicit
contracts, so wrapping them in a framework's node interface is mechanical. The audit and
cost tables would stay regardless, because no framework will produce them in the shape the
product needs.
