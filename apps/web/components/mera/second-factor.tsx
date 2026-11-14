"use client";

import { secondFactor } from "@mera/sdk";
import Link from "next/link";
import { createContext, useCallback, useContext, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { browserApi, settle, type Result } from "@/lib/api/browser";

/*
 * Second factors for high-risk actions (P2-M2.5, docs/09 §5.3). Wrap a call in
 * `withSecondFactor`: if the API answers `second_factor_required`, a dialog asks
 * for an authenticator code, the approval PIN or a recovery code, exchanges it
 * for a single-use grant, and runs the call once more with the grant in
 * `X-Mera-Second-Factor`. Cancelling returns the original refusal.
 */

export const SECOND_FACTOR_HEADER = "x-mera-second-factor";

const ACTION_TEXT: Readonly<Record<string, string>> = {
  kill_switch_resume: "Resume automation",
  account_disconnect: "Disconnect this account",
  company_page_approve: "Approve a post for a company page",
  owner_remove: "Remove an Owner",
  credentials_rotate: "Rotate credentials",
  video_generate: "Generate video or media above the cost ceiling",
  comment_delete: "Delete a comment",
  mcp_server_approve: "Approve an external MCP server",
  mcp_tool_auto: "Let an agent call a write tool without asking",
};

type Method = "totp" | "pin" | "recovery";
type Run<T> = (headers: Record<string, string>) => Promise<Result<T>>;

interface Pending {
  action: string;
  resolve: (token: string | null) => void;
}

interface SecondFactorApi {
  withSecondFactor<T>(run: Run<T>): Promise<Result<T>>;
}

const Ctx = createContext<SecondFactorApi | null>(null);

export function useSecondFactor(): SecondFactorApi {
  const ctx = useContext(Ctx);
  // Outside the provider (tests, the entry pages) a guarded call just runs once.
  return ctx ?? { withSecondFactor: (run) => run({}) };
}

export function SecondFactorProvider({ workspaceId, slug, children }: { workspaceId: string; slug: string; children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const pendingRef = useRef<Pending | null>(null);

  const ask = useCallback(
    (action: string) =>
      new Promise<string | null>((resolve) => {
        const p = { action, resolve };
        pendingRef.current = p;
        setPending(p);
      }),
    [],
  );

  const withSecondFactor = useCallback(
    async <T,>(run: Run<T>): Promise<Result<T>> => {
      const first = await run({});
      if (first.ok || first.error.code !== "second_factor_required") return first;
      const action = Object.keys(first.error.fieldErrors)[0] ?? "";
      const token = await ask(action);
      if (!token) return first;
      return run({ [SECOND_FACTOR_HEADER]: token });
    },
    [ask],
  );

  function close(token: string | null) {
    pendingRef.current?.resolve(token);
    pendingRef.current = null;
    setPending(null);
  }

  return (
    <Ctx.Provider value={{ withSecondFactor }}>
      {children}
      {pending ? (
        <SecondFactorDialog workspaceId={workspaceId} slug={slug} action={pending.action} onDone={close} />
      ) : null}
    </Ctx.Provider>
  );
}

export function SecondFactorDialog({
  workspaceId,
  slug,
  action,
  onDone,
}: {
  workspaceId: string;
  slug: string;
  action: string;
  onDone: (token: string | null) => void;
}) {
  const [method, setMethod] = useState<Method>("totp");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const r = await settle(
      secondFactor({ client: browserApi, path: { workspace_id: workspaceId }, body: { action, method, value: value.trim() } }),
    );
    setBusy(false);
    setValue("");
    if (!r.ok) {
      setError(r.error.status === 429 ? "Too many wrong PINs. Try again in 15 minutes, or use your authenticator." : r.error.message);
      return;
    }
    onDone(r.data.token);
  }

  return (
    <div role="presentation" className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4">
      <Panel role="dialog" aria-modal="true" aria-labelledby="sf-title" className="w-full max-w-sm shadow-popover">
        <PanelHead title={<span id="sf-title">Confirm it&apos;s you</span>} />
        <PanelBody>
          <form className="grid gap-3" onSubmit={submit} aria-label="Second factor">
            <p className="text-sm text-fg-muted">{ACTION_TEXT[action] ?? "This action"} needs a second factor.</p>
            <SegmentedControl
              aria-label="Method"
              value={method}
              onValueChange={(v: string) => setMethod(v as Method)}
              options={[
                { value: "totp", label: "Authenticator" },
                { value: "pin", label: "PIN" },
                { value: "recovery", label: "Recovery code" },
              ]}
            />
            {error ? (
              <Notice tone="danger" role="alert">
                {error}
              </Notice>
            ) : null}
            <Field label={method === "pin" ? "Approval PIN" : method === "totp" ? "6-digit code" : "Recovery code"}>
              <Input
                autoFocus
                type={method === "pin" ? "password" : "text"}
                inputMode={method === "recovery" ? "text" : "numeric"}
                autoComplete="one-time-code"
                value={value}
                onChange={(e) => setValue(e.target.value)}
              />
            </Field>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" variant="primary" disabled={busy || !value.trim()}>
                {busy ? "Checking…" : "Confirm"}
              </Button>
              <Button type="button" variant="ghost" onClick={() => onDone(null)}>
                Cancel
              </Button>
              <Link className="ml-auto text-caption text-accent hover:underline" href={`/w/${slug}/security`}>
                Set up a PIN or app
              </Link>
            </div>
          </form>
        </PanelBody>
      </Panel>
    </div>
  );
}
