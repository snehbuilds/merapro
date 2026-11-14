import type { NextRequest } from "next/server";
import { handleOAuthCallback } from "@/lib/session/oauth";

/* GET: the API redirects the browser here with ?code= or ?error=. */
export function GET(request: NextRequest) {
  return handleOAuthCallback(request);
}
