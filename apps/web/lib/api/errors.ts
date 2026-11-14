import type { MeraErrorEnvelope, MeraFieldError } from "@mera/sdk";

/*
 * The API's one error shape (docs/04 §3.1): branch on `code`, show `message`.
 * Everything a form needs comes out of `toApiError`.
 */

export interface ApiError {
  status: number;
  code: string;
  /** Safe to show the user. */
  message: string;
  /** field name → message, first error per field wins. */
  fieldErrors: Record<string, string>;
  /** field name → code, for branching. */
  fieldCodes: Record<string, string>;
  requestId?: string;
  /** Seconds from a 429/503 Retry-After header, when the API sent one. */
  retryAfterSeconds?: number;
}

const FALLBACK_MESSAGE: Record<number, string> = {
  0: "Can't reach Mera right now. Check your connection and try again.",
  401: "You're signed out. Sign in again to continue.",
  403: "You don't have permission to do that.",
  404: "Not found.",
  429: "Too many attempts. Wait a moment and try again.",
  502: "Can't reach Mera right now. Try again in a moment.",
  503: "Mera is temporarily unavailable. Try again in a moment.",
};

export function isErrorEnvelope(body: unknown): body is MeraErrorEnvelope {
  if (!body || typeof body !== "object") return false;
  const err = (body as { error?: unknown }).error;
  return !!err && typeof err === "object" && typeof (err as { code?: unknown }).code === "string";
}

/** Map `field_errors` to one message per field. Nested fields ("a.b") keep their dotted path. */
export function fieldErrorMap(fieldErrors: readonly MeraFieldError[] | null | undefined): {
  messages: Record<string, string>;
  codes: Record<string, string>;
} {
  const messages: Record<string, string> = {};
  const codes: Record<string, string> = {};
  for (const fe of fieldErrors ?? []) {
    if (!fe || typeof fe.field !== "string" || fe.field in messages) continue;
    messages[fe.field] = fe.message;
    codes[fe.field] = fe.code;
  }
  return { messages, codes };
}

/**
 * Normalise whatever came back (an envelope, a string, a thrown TypeError) to
 * an ApiError. `status` 0 means the request never got a response.
 */
export function toApiError(body: unknown, status: number, headers?: Headers | null): ApiError {
  const retryAfterSeconds = parseRetryAfter(headers?.get("retry-after"));
  const base = toApiErrorBody(body, status);
  return retryAfterSeconds === undefined ? base : { ...base, retryAfterSeconds };
}

/** Retry-After as seconds: either delta-seconds or an HTTP date. */
export function parseRetryAfter(value: string | null | undefined, now: number = Date.now()): number | undefined {
  if (!value) return undefined;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Number(trimmed);
  const at = Date.parse(trimmed);
  return Number.isNaN(at) ? undefined : Math.max(0, Math.ceil((at - now) / 1000));
}

/** "Too many attempts, try again in 30 seconds." for a 429; the API message otherwise. */
export function displayMessage(error: ApiError): string {
  if (error.status === 429 || error.code === "rate_limited") {
    const n = error.retryAfterSeconds;
    if (n === undefined) return "Too many attempts, try again in a moment.";
    return `Too many attempts, try again in ${n} ${n === 1 ? "second" : "seconds"}.`;
  }
  return error.message;
}

function toApiErrorBody(body: unknown, status: number): ApiError {
  if (isErrorEnvelope(body)) {
    const { messages, codes } = fieldErrorMap(body.error.field_errors);
    return {
      status,
      code: body.error.code,
      message: body.error.message || FALLBACK_MESSAGE[status] || "Something went wrong.",
      fieldErrors: messages,
      fieldCodes: codes,
      requestId: body.error.request_id ?? undefined,
    };
  }
  return {
    status,
    code: status === 0 ? "network_error" : "unknown_error",
    message: FALLBACK_MESSAGE[status] ?? "Something went wrong. Try again.",
    fieldErrors: {},
    fieldCodes: {},
  };
}

/**
 * Split an error for a form: messages for the fields the form shows inline,
 * and everything else (the top-level message, or field errors for fields the
 * form doesn't have) as a list for a form-level notice. Never drops an error.
 */
export function formErrors<F extends string>(
  error: ApiError,
  fields: readonly F[],
): { fields: Partial<Record<F, string>>; form: string[] } {
  const inline: Partial<Record<F, string>> = {};
  const form: string[] = [];
  for (const [field, message] of Object.entries(error.fieldErrors)) {
    if ((fields as readonly string[]).includes(field)) inline[field as F] = message;
    else form.push(message);
  }
  if (Object.keys(inline).length === 0 && form.length === 0) form.push(displayMessage(error));
  return { fields: inline, form };
}

/** Build an envelope for errors the BFF itself raises. */
export function errorEnvelope(code: string, message: string): MeraErrorEnvelope {
  return { error: { code, message, field_errors: [] } };
}

export function errorResponse(status: number, code: string, message: string): Response {
  return Response.json(errorEnvelope(code, message), { status });
}
