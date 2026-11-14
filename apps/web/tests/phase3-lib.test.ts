import { NextRequest } from "next/server";
import { describe, expect, it, vi } from "vitest";
import { readStepUpFragment, stepUpHref } from "@/components/mera/step-up";
import { parseThreshold } from "@/lib/autopilot";
import { formatAbRate, OUTCOME_COPY } from "@/lib/experiments";
import { bandLabel, formatRate, formatRatio, separatesCopy } from "@/lib/insights";
import { formatBytes, isHHMM, WEBHOOK_EVENTS } from "@/lib/integrations";
import { handleOAuthCallback, handleOAuthStart, handleStepUp } from "@/lib/session/oauth";
import { SOURCE_TYPES } from "@/lib/sources";

const ORIGIN = "http://localhost:3000";

describe("Phase 3 helpers", () => {
  it("autopilot thresholds follow the API's 0.50 to 0.99 rule", () => {
    expect(parseThreshold("0.85")).toBe(0.85);
    expect(parseThreshold(".9")).toBe(0.9);
    expect(parseThreshold("0.49")).toBeNull();
    expect(parseThreshold("1")).toBeNull();
    expect(parseThreshold("abc")).toBeNull();
  });

  it("insights never show a misleading zero", () => {
    expect(formatRate(null, 0, 10)).toBe("Not enough data");
    expect(formatRate(0.8, 4, 10)).toBe("Not enough data");
    expect(formatRate(0.8, 12, 10)).toBe("80%");
    expect(formatRatio(1.25)).toBe("+25% vs usual");
    expect(formatRatio(0.9)).toBe("-10% vs usual");
    expect(formatRatio(1)).toBe("As usual");
    expect(formatRatio(null)).toBe("Not enough data");
    expect(bandLabel({ low: 0.6, high: 0.8 })).toBe("60–80%");
    expect(separatesCopy(null)).toMatch(/Not enough/);
    expect(separatesCopy(true)).toMatch(/meaningful/);
  });

  it("A/B results wait for enough impressions", () => {
    expect(formatAbRate(0.05, 120, 500)).toBe("120 of 500 impressions");
    expect(formatAbRate(0.052, 800, 500)).toBe("5.2%");
    expect(formatAbRate(null, null, 500)).toBe("not reported");
    expect(OUTCOME_COPY.b).toBe("B did better");
  });

  it("digest times, sizes and webhook events", () => {
    expect(isHHMM("08:30")).toBe(true);
    expect(isHHMM("24:00")).toBe(false);
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(WEBHOOK_EVENTS).toContain("post.published");
  });

  it("MCP tools are an available source type", () => {
    expect(SOURCE_TYPES.find((t) => t.type === "mcp")?.available).toBe(true);
  });
});

describe("step-up for provider-only accounts", () => {
  it("reads a proof from the fragment and nothing else", () => {
    expect(readStepUpFragment("#step_up=mera_ra_abc")).toEqual({ proof: "mera_ra_abc", failed: false });
    expect(readStepUpFragment("#step_up=evil")).toEqual({ proof: null, failed: false });
    expect(readStepUpFragment("#step_up_error=1")).toEqual({ proof: null, failed: true });
  });

  it("starts the provider flow and returns to this page", () => {
    expect(stepUpHref("google", "/w/acme/security")).toBe("/api/session/oauth/google/start?step_up=1&next=%2Fw%2Facme%2Fsecurity");
  });

  it("start marks the flow as step-up; the callback hops same-origin instead of signing in", async () => {
    vi.stubEnv("MERA_API_URL", "http://api.test");
    try {
      const res = await handleOAuthStart(new NextRequest(`${ORIGIN}/api/session/oauth/google/start?step_up=1&next=/w/acme/security`), "google");
      const cookie = res.headers.getSetCookie().find((c) => c.startsWith("mera_oauth="))!;
      const stored = JSON.parse(decodeURIComponent(cookie.split(";")[0]!.slice("mera_oauth=".length))) as { s?: number };
      expect(stored.s).toBe(1);
    } finally {
      vi.unstubAllEnvs();
    }
    const verifier = JSON.stringify({ v: "v".repeat(43), next: "/w/acme/security", s: 1 });
    const upstream = vi.fn();
    const hop = await handleOAuthCallback(
      new NextRequest(`${ORIGIN}/api/session/oauth/callback?code=code-1234567890`, {
        headers: { cookie: `mera_oauth=${encodeURIComponent(verifier)}` },
      }),
      { fetch: upstream as unknown as typeof fetch },
    );
    expect(await hop.text()).toContain("/api/session/oauth/step-up?code=code-1234567890");
    expect(upstream).not.toHaveBeenCalled(); // no session was created
  });

  it("the hop trades the code for a proof with the session and returns it in the fragment", async () => {
    const verifier = JSON.stringify({ v: "v".repeat(43), next: "/w/acme/security", s: 1 });
    const upstream = vi.fn(async (req: Request) => {
      expect(new URL(req.url).pathname).toBe("/api/v1/auth/step-up/exchange");
      expect(req.headers.get("authorization")).toBe("Bearer at");
      expect(await req.json()).toEqual({ code: "code-1234567890", code_verifier: "v".repeat(43) });
      return Response.json({ proof: "mera_ra_xyz", expires_at: "2026-11-13T18:00:00Z" });
    });
    const res = await handleStepUp(
      new NextRequest(`${ORIGIN}/api/session/oauth/step-up?code=code-1234567890`, {
        headers: { cookie: `mera_oauth=${encodeURIComponent(verifier)}; mera_at=at` },
      }),
      { apiBaseUrl: "http://api.test", fetch: upstream as unknown as typeof fetch },
    );
    expect(res.headers.get("location")).toBe(`${ORIGIN}/w/acme/security#step_up=mera_ra_xyz`);
    expect(res.headers.getSetCookie().find((c) => c.startsWith("mera_oauth="))).toMatch(/Max-Age=0/i);
  });

  it("without a session the hop reports failure and calls nothing", async () => {
    const verifier = JSON.stringify({ v: "v".repeat(43), next: "/w/acme/security", s: 1 });
    const upstream = vi.fn();
    const res = await handleStepUp(
      new NextRequest(`${ORIGIN}/api/session/oauth/step-up?code=code-1234567890`, {
        headers: { cookie: `mera_oauth=${encodeURIComponent(verifier)}` },
      }),
      { fetch: upstream as unknown as typeof fetch },
    );
    expect(res.headers.get("location")).toBe(`${ORIGIN}/w/acme/security#step_up_error=1`);
    expect(upstream).not.toHaveBeenCalled();
  });
});
