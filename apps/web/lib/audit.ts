/* Audit Log filters (M11). Plain data: shared by the server page and the client screen. */
export const AUDIT_FILTERS = [
  { value: "", label: "Everything" },
  { value: "approval", label: "Approvals" },
  { value: "post.", label: "Publishing" },
  { value: "kill_switch", label: "Kill switch" },
  { value: "account.", label: "Accounts" },
  { value: "approver_binding", label: "Pairing" },
  { value: "security.", label: "Security" },
] as const;

export function auditAction(raw: string | undefined): string {
  return AUDIT_FILTERS.some((f) => f.value === raw) ? (raw as string) : "";
}
