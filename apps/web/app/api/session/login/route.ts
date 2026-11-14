import type { NextRequest } from "next/server";
import { handleLogin } from "@/lib/session/session-routes";

export function POST(request: NextRequest): Promise<Response> {
  return handleLogin(request);
}
