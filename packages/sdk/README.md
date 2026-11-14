# @mera/sdk

The TypeScript client for the Mera API, and the only way `apps/web` talks to it: calling
`fetch` directly from app code is a lint error (`docs/01-architecture.md` §7.6,
`docs/10-repo-structure.md` §2).

## Layout

| Path | What | Edit? |
| --- | --- | --- |
| `openapi.json` | The API spec, written by the backend: `cd backend && uv run python scripts/export_openapi.py` | No (backend owns it) |
| `src/gen/` | Generated client: types, one function per operation, the fetch transport | **Never by hand** |
| `src/index.ts` | Re-exports `src/gen` plus `createMeraClient()` | Yes |
| `openapi-ts.config.ts` | Generator config | Yes |
| `scripts/check.mjs` | Drift check | Yes |

## Commands (from the repo root)

```sh
pnpm sdk         # regenerate src/gen from openapi.json
pnpm sdk:check   # regenerate into a temp dir; fail if src/gen differs (CI)
```

Commit `src/gen` with the spec change that produced it. `sdk:check` failing means someone
changed the API without regenerating.

## Use

```ts
import { createMeraClient, listMembers } from "@mera/sdk";

const api = createMeraClient({ baseUrl: "http://api:8000", accessToken: token });
const { data, error, response } = await listMembers({ client: api, path: { workspace_id } });
```

`createMeraClient({ baseUrl, fetch?, headers?, accessToken? })` returns a client; every
operation takes it as `{ client }`. The generated paths already contain `/api/v1`, so
`baseUrl` is the API origin (server-side) or the web app's BFF prefix `/api/mera` (browser).
Errors come back as `error` (the `ErrorEnvelope` of docs/04 §3.1), never thrown, unless you
pass `throwOnError: true`.

Operation names come from FastAPI's operationIds with the default `_api_v1_…_method`
suffix stripped (`list_members_api_v1_workspaces__workspace_id__members_get` →
`listMembers`). Two routes collapsing to the same name fails generation; rename one route.

## Deviation from docs/04 §7

docs/04 §7 names `openapi-typescript-codegen`. That project is unmaintained; its maintained
successor is **`@hey-api/openapi-ts`** (pinned at `0.99.0`, with the bundled
`@hey-api/client-fetch`). Same input, same role, typed responses and errors.
