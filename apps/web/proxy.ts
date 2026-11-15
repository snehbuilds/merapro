import type { NextRequest } from "next/server";
import { gateRequest } from "@/lib/session/gate";

/*
 * Next 16 "proxy" (formerly middleware). Gates the signed-in pages; the logic
 * is lib/session/gate.ts. The API routes do their own checks.
 */
export function proxy(request: NextRequest) {
  return gateRequest(request);
}

export const config = {
  matcher: ["/w/:path*", "/welcome", "/invite/:path*"],
};
