import type { NextRequest } from "next/server";
import { handleLogout } from "@/lib/session/session-routes";

export function POST(request: NextRequest): Promise<Response> {
  return handleLogout(request);
}
