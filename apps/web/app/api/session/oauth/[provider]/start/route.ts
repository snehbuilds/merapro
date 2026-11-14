import type { NextRequest } from "next/server";
import { handleOAuthStart } from "@/lib/session/oauth";

/* GET: the browser navigates here from the sign-in buttons on /login. */
export async function GET(request: NextRequest, ctx: { params: Promise<{ provider: string }> }) {
  const { provider } = await ctx.params;
  return handleOAuthStart(request, provider);
}
