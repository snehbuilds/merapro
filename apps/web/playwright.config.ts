import { defineConfig } from "@playwright/test";

/*
 * Browser end-to-end tests (P2-M0.7) against a running stack: web, api and
 * worker in sandbox mode with the demo seed (`make up`, or the verify-* preview
 * servers). They do not start servers themselves.
 *
 *   E2E_BASE_URL   web app            (default http://localhost:3000)
 *   E2E_API_URL    api, for setup     (default http://localhost:8000)
 *   E2E_EMAIL / E2E_PASSWORD          (default: the demo seed user)
 *   E2E_BROWSER_CHANNEL               (default "chrome": the installed browser,
 *                                      so no browser download is needed behind
 *                                      a corporate proxy; "msedge" also works)
 */
export default defineConfig({
  testDir: "./e2e",
  // Staggered publishing runs on the worker's one-minute tick, and a repeat run can wait
  // out the ten-minute per-account spacing behind the previous run's posts.
  timeout: 16 * 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  outputDir: "test-results",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    channel: process.env.E2E_BROWSER_CHANNEL ?? "chrome",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
