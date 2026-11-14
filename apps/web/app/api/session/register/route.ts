import type { NextRequest } from "next/server";
import { handleRegister } from "@/lib/session/session-routes";

export function POST(request: NextRequest): Promise<Response> {
  return handleRegister(request);
}
