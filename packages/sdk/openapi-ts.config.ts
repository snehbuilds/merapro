import { defineConfig } from "@hey-api/openapi-ts";

/*
 * Generates src/gen from openapi.json (written by the backend:
 * `cd backend && uv run python scripts/export_openapi.py`). Run `pnpm sdk`
 * from the repo root; `pnpm sdk:check` regenerates into a temp dir and fails
 * when the committed output is stale.
 *
 * MERA_SDK_OUT overrides the output directory (used by sdk:check).
 */

const HTTP_METHODS = ["get", "put", "post", "delete", "options", "head", "patch", "trace"] as const;

/**
 * FastAPI's default operationId is `{function}{path}_{method}` with every
 * non-word character replaced by `_` (fastapi.utils.generate_unique_id), e.g.
 * `list_members_api_v1_workspaces__workspace_id__members_get`. Strip the
 * path+method suffix so the SDK exposes `listMembers` rather than
 * `listMembersApiV1WorkspacesWorkspaceIdMembersGet`. An operationId without
 * the default shape (the backend set its own) is left alone. Two operations
 * collapsing to one name is a hard error, never a silent rename.
 */
function shortenFastApiOperationIds(spec: { paths?: Record<string, unknown> }): void {
  const seen = new Map<string, string>();
  for (const [path, item] of Object.entries(spec.paths ?? {})) {
    if (!item || typeof item !== "object") continue;
    for (const method of HTTP_METHODS) {
      const op = (item as Record<string, { operationId?: string } | undefined>)[method];
      if (!op?.operationId) continue;
      const suffix = `${path.replace(/\W/g, "_")}_${method}`;
      if (op.operationId.endsWith(suffix) && op.operationId.length > suffix.length) {
        op.operationId = op.operationId.slice(0, -suffix.length);
      }
      const where = `${method.toUpperCase()} ${path}`;
      const clash = seen.get(op.operationId);
      if (clash) {
        throw new Error(
          `SDK operation name "${op.operationId}" is used by both ${clash} and ${where}. Give one route a distinct name.`,
        );
      }
      seen.set(op.operationId, where);
    }
  }
}

export default defineConfig({
  input: "./openapi.json",
  output: { path: process.env.MERA_SDK_OUT ?? "./src/gen" },
  parser: { patch: (spec) => shortenFastApiOperationIds(spec as { paths?: Record<string, unknown> }) },
  plugins: ["@hey-api/typescript", "@hey-api/sdk", "@hey-api/client-fetch"],
});
