// SDK drift check (docs/04 §7, M9.2): regenerate from openapi.json into a temp
// directory and fail if it differs from the committed src/gen. Run from the
// repo root with `pnpm sdk:check`.
import { spawnSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const pkgDir = join(dirname(fileURLToPath(import.meta.url)), "..");
const committed = join(pkgDir, "src", "gen");
const fresh = mkdtempSync(join(tmpdir(), "mera-sdk-check-"));

function listFiles(root) {
  const out = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) walk(full);
      else out.push(relative(root, full).split("\\").join("/"));
    }
  };
  try {
    walk(root);
  } catch {
    // missing directory: no files
  }
  return out.sort();
}

let exitCode = 0;
try {
  const bin = join(pkgDir, "node_modules", "@hey-api", "openapi-ts", "bin", "run.js");
  const gen = spawnSync(process.execPath, [bin], {
    cwd: pkgDir,
    env: { ...process.env, MERA_SDK_OUT: fresh },
    stdio: ["ignore", "ignore", "inherit"],
  });
  if (gen.status !== 0) {
    console.error("sdk:check: generation failed");
    process.exit(gen.status ?? 1);
  }

  const a = listFiles(committed);
  const b = listFiles(fresh);
  const all = [...new Set([...a, ...b])].sort();
  const drift = [];
  for (const file of all) {
    if (!a.includes(file)) drift.push(`  + ${file} (missing from src/gen)`);
    else if (!b.includes(file)) drift.push(`  - ${file} (no longer generated)`);
    else if (!readFileSync(join(committed, file)).equals(readFileSync(join(fresh, file)))) drift.push(`  ~ ${file}`);
  }

  if (drift.length > 0) {
    console.error("sdk:check: packages/sdk/src/gen is out of date with packages/sdk/openapi.json:");
    console.error(drift.join("\n"));
    console.error("Run `pnpm sdk` and commit the result.");
    exitCode = 1;
  } else {
    console.log(`sdk:check: src/gen matches openapi.json (${a.length} files)`);
  }
} finally {
  rmSync(fresh, { recursive: true, force: true });
}
process.exit(exitCode);
