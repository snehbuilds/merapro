import type { NextRequest } from "next/server";
import { handleStepUp } from "@/lib/session/oauth";

/* GET: the same-origin hop from the callback during step-up (P3-M7.3). */
export function GET(request: NextRequest) {
  return handleStepUp(request);
}
