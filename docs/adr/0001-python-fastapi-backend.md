# ADR-0001: Python and FastAPI for the backend

**Status:** Accepted
**Date:** 2026-11-08

## Context

Mera needs a backend serving a REST API, a worker pool, a bot service, and an MCP server.
The frontend is Next.js and TypeScript regardless. The realistic candidates were an
all-TypeScript monorepo (NestJS or Fastify, BullMQ) and a Python backend (FastAPI, with a
separate frontend consuming a generated SDK).

The all-TypeScript option has a genuine advantage: one language, one toolchain, and types
shared end to end from Prisma through to React with no generation step.

The workload pulls the other way. Mera's distinctive work is content ingestion and an
agent layer: article extraction, language detection, embeddings, deduplication,
classification, and LLM orchestration. Those libraries are Python-first, often
Python-only, and the TypeScript equivalents are generally ports that lag.

## Decision

Python 3.11 with FastAPI, SQLAlchemy 2.0, Alembic, and Pydantic. The frontend stays
Next.js and consumes a TypeScript SDK generated from the OpenAPI spec.

## Consequences

**Good**

- `trafilatura`, `feedparser`, `langdetect`, and the embedding and LLM ecosystems are
  available without workarounds.
- Pydantic models are simultaneously the validation layer, the serialization layer, the
  OpenAPI source, and the agent input and output contracts. That is four jobs from one
  definition, which is unusually good leverage.
- FastAPI generates OpenAPI 3.1 from the implementation, so the published contract cannot
  drift from the code.
- `mypy --strict` on the domain and application layers gives the type safety that was the
  main argument for TypeScript, where it matters most.

**Bad**

- The SDK generation step is real machinery that must be enforced. Mitigated by a CI stage
  that regenerates and fails on drift, plus a lint rule banning raw `fetch` in the web app.
- Two toolchains to install and keep current.
- Python async requires discipline. A synchronous call in an async handler blocks the
  event loop silently. Mitigated by an async-only policy in the application layer and by
  `ruff` rules that flag blocking calls.
- No shared type definitions between backend and frontend except through generation.

## Alternatives considered

**Node with NestJS.** One language, shared types, strong DI, mature ecosystem. Rejected on
the ingestion and AI library gap. Calling out to Python services for that work would
reintroduce the polyglot problem with an RPC boundary in the middle of the agent layer.

**Hybrid: TypeScript API with a Python AI service.** Both deployment stories plus a network
boundary in the hottest part of the system. Only sensible if the API layer had a reason to
be TypeScript, and it does not.

**Go.** Excellent for the worker and publishing layers, poor for the agent layer, and the
development velocity cost is not justified at this scale.

## Revisit when

- The agent layer is extracted into a separate service for independent scaling, at which
  point the API's language is no longer coupled to it.
- The TypeScript ecosystem's article-extraction and embedding libraries reach parity,
  which would remove the main justification.
- SDK drift causes repeated production incidents, which would indicate the generation
  boundary is not being respected and the single-language argument has become stronger
  in practice than in theory.
