"""Mera's MCP server (P2-M8, docs/05 §1): a thin, stateless wrapper over the REST API.

It holds no business logic and no database connection. Every tool call is an
authenticated request to ``/api/v1``, so MCP inherits RBAC, guardrails, rate limits,
idempotency and the approval invariant: asking Claude to post creates an approval
request, it does not publish.
"""
