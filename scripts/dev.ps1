# Windows equivalent of the Makefile targets: .\scripts\dev.ps1 <target> [-Name "msg"] [-Service api] [-Flags "--live"]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("setup", "up", "down", "test", "test-unit", "test-integration", "lint", "typecheck", "migrate", "logs", "web-dev", "preflight", "e2e")]
  [string]$Target,
  [string]$Name = "",
  [string]$Service = "",
  [string]$Flags = "--live"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Backend([scriptblock]$cmd) { Push-Location backend; try { & $cmd } finally { Pop-Location } }

switch ($Target) {
  "setup" {
    if (-not (Test-Path .env)) { Copy-Item .env.example .env }
    Backend { uv sync }
    corepack enable pnpm; pnpm install
  }
  "up" { docker compose up -d --build --wait }
  "down" { docker compose down }
  "test" { Backend { uv run pytest } }
  "test-unit" { Backend { uv run pytest tests/unit } }
  "test-integration" { Backend { uv run pytest -m integration } }
  "lint" { Backend { uv run ruff check .; uv run ruff format --check . }; pnpm -r lint }
  "typecheck" { Backend { uv run mypy src }; pnpm -r typecheck }
  "migrate" { Backend { uv run alembic revision --autogenerate -m $Name } }
  "logs" { docker compose logs -f $Service }
  "web-dev" { pnpm --filter web dev }
  # Go-live checks inside the running api container (P2-M0); secrets are never printed.
  "e2e" { pnpm --filter web e2e }
  "preflight" { docker compose exec api python -m mera.preflight @($Flags -split ' ' | Where-Object { $_ }) }
}
