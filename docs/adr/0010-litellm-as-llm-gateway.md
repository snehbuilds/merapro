# ADR-0010: LiteLLM as the single LLM gateway

**Status:** Accepted
**Date:** 2026-11-08

## Context

Mera's brief requires the agent layer to be provider-agnostic, configurable per agent, and
able to run against Claude, OpenAI, or a local model. The original design satisfied that
with a hand-written adapter per provider behind an `LLMProvider` port.

Working through what those adapters actually contain made the cost visible. Each one needs
request and response mapping, its own structured-output mechanism (JSON schema mode for
some, tool calling for others, prompted JSON for the rest), its own usage field names, its
own error taxonomy for rate limits and context-length failures, its own streaming shape,
and its own retry semantics. Three adapters is a few hundred lines of fiddly,
easy-to-get-subtly-wrong code whose entire purpose is to arrive at an abstraction that
already exists as a library.

The operator requirement that settled it: **"I should be able to use any key."** Not three
providers chosen at design time, but whichever one happens to be available, including
whatever appears next year.

## Decision

Every LLM call routes through [LiteLLM](https://docs.litellm.ai), used **as a library**,
behind Mera's own thin `LLMProvider` port.

1. One implementation, `LiteLLMProvider`. There is no second real implementation and no
   per-vendor adapter directory.
2. A provider is a `model` string on `agent_definition`: `anthropic/claude-sonnet-4-5`,
   `openai/gpt-5`, `gemini/...`, `groq/...`, `ollama/llama3.1`, and so on. Changing
   provider is configuration, not code.
3. Keys come from the environment, one per provider in use. A per-workspace override
   exists for the agency case and is encrypted like any other credential.
4. **The port stays.** Agents never call `litellm.acompletion` directly.
5. Fallback chains are configured per agent and handled by LiteLLM. Any substitution is
   recorded as `agent_step.fallback_from`.
6. Cost is computed by Mera from a pinned, date-effective price table, not taken from the
   library's estimate.
7. Structured output is bridged in `infrastructure/llm/structured.py`: native schema mode
   where available, tool calling where not, validate-and-retry as the floor.

### Why the port survives

Deleting the port and calling the library from agent code would be simpler by one layer
and wrong. Four things happen in the wrapper and none of them is optional:

- The `agent_step` row is written, with prompt, output, model, usage, and target account,
  **before the result is used**.
- The daily cost cap is checked against today's spend before the call is made.
- `Usage` is normalized and persisted; cost accounting is part of the return type rather
  than instrumentation someone might forget.
- Untrusted content is rendered into the prompt inside its delimiters by the wrapper, so
  no caller can concatenate hostile text into a system message by accident.

The port is also what keeps the escape hatch open. If one agent ever needs a
provider-specific feature LiteLLM does not expose, a second implementation can serve that
agent without touching the other ten.

## Consequences

**Good**

- Any provider LiteLLM supports works with no Mera change. That is the requirement, met
  directly rather than approximated by three adapters.
- One request and response shape across vendors, so per-agent model mixing is trivial and
  the cost rollups in [06 - Agents](../06-agents.md) section 8 work identically everywhere.
- Retries, timeouts, and fallbacks come from a library that has already met the failure
  modes.
- A local Ollama path needs no separate adapter, so "run it offline" is a config edit.
- Several hundred lines of adapter code never written, and never needing maintenance as
  providers change their APIs.
- Provider outage handling becomes a per-agent list rather than custom logic.

**Bad**

- **A fast-moving dependency sits in the hottest part of the agent layer.** LiteLLM ships
  frequently and supports a very large surface. Mitigated by pinning the version and
  upgrading deliberately, with the eval suite as the gate.
- **A routing bug looks like a model bug.** The symptom of wrong parameter mapping is
  degraded output, not an exception. Mitigated by contract tests per configured provider
  and by `fallback_from` being visible in the run viewer.
- **Lowest-common-denominator surface.** Provider-specific features (extended thinking
  controls, provider-native caching knobs, unusual modalities) arrive late or not at all.
  Accepted, with the documented escape hatch.
- **`drop_params` hides incompatibilities.** Setting it true means a parameter a provider
  rejects is silently dropped rather than erroring, so an agent can quietly run at a
  different temperature than configured. Accepted because the alternative — a hard failure
  when switching provider — defeats the purpose, but it is logged at debug level and the
  evals are what catch a meaningful drift.
- **Cost estimates can disagree with invoices.** Mera computes cost from its own pinned
  table precisely so that a library update does not restate history, which means the table
  must be maintained as prices change.
- **Switching provider becomes too easy.** With adapters, changing vendor was a visible
  piece of work. Now it is one string, which raises the risk of an unevaluated change
  reaching production. This is why model pinning and the eval gate are stated as
  requirements rather than good practice.

## Alternatives considered

**Hand-written adapters per provider.** The original plan. Full control, no dependency in
a sensitive place, and provider-specific features available immediately. Rejected on
effort-to-value: the work is substantial, ongoing, and entirely in service of reaching an
abstraction that exists. The one real argument for it — avoiding a dependency in the agent
path — is largely addressed by the thin port, which keeps the substitution point.

**The LiteLLM proxy server.** The same project ships as a standalone gateway with key
management, spend dashboards, and per-team budgets. Correct for an organization sharing
keys across many applications. Rejected here: a seventh container duplicating cost
accounting Mera already does per `agent_step`, against the single-datastore,
few-containers posture of [ADR-0002](0002-single-postgres-datastore.md). Documented as the
upgrade path if key management outgrows environment variables.

**OpenRouter or a similar hosted router.** One key, hundreds of models, no library. Adds a
third party between Mera and every model call, with its own uptime, its own margin, and
its own data-handling terms. Also conflicts with the self-hosted posture and with using a
local model. Worth noting that it remains available *through* LiteLLM as one more provider
string, which is the better arrangement.

**A framework's model abstraction** (LangChain, the Claude Agent SDK). Taking a model
router by way of an agent framework means taking the framework. Rejected for the reasons
in [ADR-0005](0005-in-house-agent-orchestrator.md).

**Two providers, hardcoded.** Anthropic and OpenAI only, accepting the limitation.
Rejected because it fails the stated requirement, and because the second adapter is where
most of the abstraction cost lands anyway — having paid it, supporting the tenth provider
for free is strictly better.

## Revisit when

- A provider feature that materially improves output quality is unavailable through
  LiteLLM for more than a release cycle. The response is a second `LLMProvider`
  implementation for the affected agent, not abandoning the gateway.
- LiteLLM upgrades repeatedly break the agent layer, which would mean the dependency is
  costing more than the adapters would have.
- Key management outgrows environment variables — several teams, rotation policies,
  per-team budgets — at which point the proxy server becomes the proportionate answer and
  the library call changes to a base URL.
- Spend reaches a level where provider-native batch or caching APIs materially matter and
  are not exposed through the library.
