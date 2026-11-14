import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

/*
 * The web app talks to the API only through the generated SDK (docs/01 §7.6,
 * docs/10 §2). Calling fetch directly is a lint error everywhere in apps/web,
 * with no exemptions: transport lives in packages/sdk/src/gen (generated),
 * and the BFF proxy (lib/session/proxy-core.ts) forwards through the SDK
 * client too.
 */
const FETCH_MESSAGE =
  "Do not call fetch directly. Use @mera/sdk (browser: lib/api/browser.ts, server: lib/api/server.ts) so the API stays complete for every client. See docs/10-repo-structure.md §2.";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      "no-restricted-globals": ["error", { name: "fetch", message: FETCH_MESSAGE }],
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "MemberExpression[object.name=/^(window|globalThis|self)$/][property.name='fetch']",
          message: FETCH_MESSAGE,
        },
      ],
    },
  },
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts", "node_modules/**", "playwright-report/**", "test-results/**"]),
]);
