# One image, one target per service (docs/10 §3). Digests should be pinned before release.
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    UV_SYSTEM_CERTS=1
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
WORKDIR /app

# Optional: behind a TLS-intercepting proxy, pass your organisation's CA bundle as a build
# secret (CORP_CA_BUNDLE=/path/to/ca.pem in the environment; see docker-compose.yml).
# Without it the secret is empty and this step is a no-op.
RUN --mount=type=secret,id=corp_ca,required=false \
    if [ -s /run/secrets/corp_ca ]; then \
      cp /run/secrets/corp_ca /usr/local/share/ca-certificates/corp-ca.crt && update-ca-certificates; \
    fi

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd --system --uid 10001 mera && mkdir -p /var/lib/mera/media && chown mera /var/lib/mera/media
USER mera

FROM base AS migrate
CMD ["alembic", "upgrade", "head"]

FROM base AS api
EXPOSE 8000
CMD ["uvicorn", "mera.api.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS worker
CMD ["python", "-m", "mera.workers.main"]

FROM base AS bots
EXPOSE 8001
CMD ["uvicorn", "mera.bots.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8001"]

FROM base AS mcp
EXPOSE 8002
# Mera's MCP server, Streamable HTTP (P2-M8). Local stdio use: python -m mera.mcp
CMD ["uvicorn", "mera.mcp.http:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8002"]

FROM base AS mcp-egress
EXPOSE 3128 3129
# The proxy listens on 3128; the grant admin API (sandbox network only) on 3129.
CMD ["uvicorn", "mera.sandbox.egress:app_factory", "--factory", "--host", "0.0.0.0", "--port", "3129"]

FROM base AS mcp-sandbox
USER root
# Package runners for pinned stdio servers: npx (Node) and uvx (Python).
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm     && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir uv
USER mera
EXPOSE 8010
CMD ["uvicorn", "mera.sandbox.runner:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8010"]
