import type { NextRequest } from "next/server";
import { handleMfa } from "@/lib/session/mfa";

export function POST(request: NextRequest): Promise<Response> {
  return handleMfa(request);
}
