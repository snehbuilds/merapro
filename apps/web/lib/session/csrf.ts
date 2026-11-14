import { errorResponse } from "@/lib/api/errors";
import { CLIENT_HEADER, CLIENT_HEADER_VALUE } from "./constants";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/**
 * CSRF layer one for /api/mera and /api/session: a state-changing request must
 * carry `X-Mera-Client: web`. A cross-site page cannot add a custom header to
 * a request without a CORS preflight, which this app never answers. Returns a
 * 403 envelope to send back, or null when the request may proceed.
 */
export function csrfRejection(request: Pick<Request, "method" | "headers">): Response | null {
  if (SAFE_METHODS.has(request.method.toUpperCase())) return null;
  if (request.headers.get(CLIENT_HEADER) === CLIENT_HEADER_VALUE) return null;
  return errorResponse(403, "csrf_rejected", "This request did not come from the Mera web app.");
}
