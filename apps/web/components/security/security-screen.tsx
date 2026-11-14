"use client";

import {
  clearPin,
  mfaConfirm,
  mfaDisable,
  mfaRecoveryCodes,
  mfaSetup,
  setMemberAway,
  setPin,
  type SecurityOut,
} from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Topbar } from "@/components/shell/topbar";
import { StepUpPrompt, useStepUpProof } from "@/components/mera/step-up";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";

/*
 * Your security (P2-M2): two-step sign-in, the approval PIN, and out of office.
 * Turning on two-step sign-in signs other sessions out; recovery codes are shown
 * exactly once. Changing either factor needs one you already have: your
 * authenticator (or a recovery code) once two-step is on, else your password.
 */

export interface SecurityScreenProps {
  workspace: { id: string; slug: string; timezone: string };
  status: SecurityOut;
  me: { id: string; awayUntil: string | null; delegateId: string | null };
  /** Members who can approve, for the delegate picker (not you). */
  delegates: { id: string; name: string }[];
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  return (
    <Notice tone="warn" title="Save these recovery codes now." role="status">
      <p>Each works once if you lose your phone. They will not be shown again.</p>
      <pre className="mt-2 grid grid-cols-2 gap-1 font-mono text-sm" aria-label="Recovery codes">
        {codes.join("\n")}
      </pre>
      <Button size="sm" className="mt-2" onClick={onDone}>
        I&apos;ve saved them
      </Button>
    </Notice>
  );
}

function TwoStep({ status }: { status: SecurityOut }) {
  const router = useRouter();
  const [setup, setSetup] = useState<{ secret: string; uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [proof, setProof] = useState("");
  const [password, setPassword] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // An account that signs in only with a provider confirms with it instead (step-up).
  const needsPassword = status.password_set !== false;
  const [stepUp, clearStepUp] = useStepUpProof();

  async function start(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    // Whoever enrols an authenticator controls the next sign-in, so a session alone is not
    // enough: the password is asked for first.
    const r = await settle(mfaSetup({ client: browserApi, body: { proof: needsPassword ? password : (stepUp.proof ?? "") } }));
    setBusy(false);
    setPassword("");
    clearStepUp();
    if (!r.ok) setError(displayMessage(r.error));
    else setSetup({ secret: r.data.secret, uri: r.data.otpauth_uri });
  }

  async function confirm(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const r = await settle(mfaConfirm({ client: browserApi, body: { code: code.trim() } }));
    setBusy(false);
    setCode("");
    if (!r.ok) {
      setError(displayMessage(r.error));
      return;
    }
    setSetup(null);
    setCodes(r.data.recovery_codes);
  }

  async function withProof(kind: "disable" | "codes") {
    setBusy(true);
    setError(null);
    const r =
      kind === "disable"
        ? await settle(mfaDisable({ client: browserApi, body: { proof: proof.trim() } }))
        : await settle(mfaRecoveryCodes({ client: browserApi, body: { proof: proof.trim() } }));
    setBusy(false);
    setProof("");
    if (!r.ok) {
      setError(displayMessage(r.error));
      return;
    }
    if (kind === "codes" && r.data && "recovery_codes" in r.data) setCodes(r.data.recovery_codes);
    else router.refresh();
  }

  return (
    <Panel aria-labelledby="twostep-title">
      <PanelHead
        title={<span id="twostep-title">Two-step sign-in</span>}
        actions={status.mfa_enabled ? <Tag tone="ok">On</Tag> : <Tag tone={status.mfa_required ? "warn" : "neutral"}>Off</Tag>}
      />
      <PanelBody className="grid gap-3">
        {error ? (
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        ) : null}
        {codes ? (
          <RecoveryCodes
            codes={codes}
            onDone={() => {
              setCodes(null);
              // Turning two-step on signed this session out too.
              router.refresh();
            }}
          />
        ) : null}
        {!status.mfa_enabled && status.mfa_required ? (
          <Notice tone="warn" role="status">
            This server requires two-step sign-in for Admins and Owners. Until you turn it on, your access is limited to an Editor&apos;s.
          </Notice>
        ) : null}
        {status.mfa_enabled ? (
          <>
            <p className="text-sm text-fg-muted">
              On since <Time value={status.mfa_enabled_at ?? ""} tz="UTC" format="datetime" /> · {status.recovery_codes_left} recovery codes left
            </p>
            <Field label="Current code" hint="From your authenticator app, or a recovery code.">
              <Input value={proof} autoComplete="one-time-code" onChange={(e) => setProof(e.target.value)} />
            </Field>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={busy || !proof.trim()} onClick={() => void withProof("codes")}>
                New recovery codes
              </Button>
              <Button size="sm" variant="ghost" disabled={busy || !proof.trim()} onClick={() => void withProof("disable")}>
                Turn off
              </Button>
            </div>
          </>
        ) : setup ? (
          <form className="grid gap-3" onSubmit={confirm} aria-label="Confirm authenticator">
            <p className="text-sm">
              Add this key to an authenticator app (1Password, Google Authenticator, Authy…), then enter the code it shows.
            </p>
            <code className="rounded-sm bg-surface-sunken px-2 py-1.5 font-mono text-sm break-all" aria-label="Setup key">
              {setup.secret}
            </code>
            <a className="text-caption text-accent hover:underline" href={setup.uri}>
              Open in an authenticator app on this device
            </a>
            <Field label="Code from the app">
              <Input value={code} inputMode="numeric" autoComplete="one-time-code" onChange={(e) => setCode(e.target.value)} />
            </Field>
            <Button type="submit" variant="primary" disabled={busy || code.trim().length < 6}>
              {busy ? "Checking…" : "Turn on"}
            </Button>
          </form>
        ) : (
          <form className="grid gap-3" onSubmit={start} aria-label="Start two-step sign-in">
            {needsPassword ? (
              <Field label="Your password" hint="Confirms it is you before an authenticator is added.">
                <Input value={password} type="password" autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
              </Field>
            ) : (
              <StepUpPrompt state={stepUp} purpose="add an authenticator" />
            )}
            <Button
              type="submit"
              variant="primary"
              className="justify-self-start"
              disabled={busy || (needsPassword ? !password : !stepUp.proof)}
            >
              Set up two-step sign-in
            </Button>
          </form>
        )}
        {status.codes_locked_until ? (
          <Notice tone="warn" role="status">
            Too many wrong codes. Codes work again at <Time value={status.codes_locked_until} tz="UTC" format="datetime" />.
          </Notice>
        ) : null}
      </PanelBody>
      <PanelFoot>Turning it on signs every session out, this one too: sign in again with your new code.</PanelFoot>
    </Panel>
  );
}

function ApprovalPin({ status }: { status: SecurityOut }) {
  const router = useRouter();
  const [pin, setPinValue] = useState("");
  const [proof, setProof] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const proofLabel = status.mfa_enabled ? "Current authenticator code" : "Your password";
  // No password and no authenticator: the provider confirms instead (step-up).
  const providerOnly = !status.mfa_enabled && status.password_set === false;
  const [stepUp, clearStepUp] = useStepUpProof();
  const proofValue = providerOnly ? (stepUp.proof ?? "") : proof;

  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    const r = await settle(setPin({ client: browserApi, body: { pin: pin.trim(), proof: proofValue } }));
    setPinValue("");
    setProof("");
    clearStepUp();
    if (!r.ok) {
      setError(r.error.code === "invalid_pin" ? "Use 6 to 8 digits, not all the same." : displayMessage(r.error));
      return;
    }
    setSaved(true);
    router.refresh();
  }

  async function clear() {
    setError(null);
    const r = await settle(clearPin({ client: browserApi, body: { proof: proofValue } }));
    setProof("");
    clearStepUp();
    if (!r.ok) setError(displayMessage(r.error));
    else router.refresh();
  }

  return (
    <Panel aria-labelledby="pin-title">
      <PanelHead title={<span id="pin-title">Approval PIN</span>} actions={status.pin_set ? <Tag tone="ok">Set</Tag> : <Tag>Not set</Tag>} />
      <PanelBody>
        <form className="grid gap-3" onSubmit={save} aria-label="Approval PIN">
          <p className="text-sm text-fg-muted">
            Confirms high-risk actions, including approving company pages from Telegram (send /pin and the number).
          </p>
          {status.pin_locked_until ? (
            <Notice tone="warn" role="status">
              Locked after too many wrong tries, until <Time value={status.pin_locked_until} tz="UTC" format="datetime" />.
            </Notice>
          ) : null}
          {error ? (
            <Notice tone="danger" role="alert">
              {error}
            </Notice>
          ) : null}
          {saved ? <p role="status" className="text-caption text-ok">PIN saved.</p> : null}
          <Field label={status.pin_set ? "New PIN" : "PIN"} hint="6 to 8 digits.">
            <Input value={pin} type="password" inputMode="numeric" autoComplete="new-password" onChange={(e) => setPinValue(e.target.value)} />
          </Field>
          {providerOnly ? (
            <StepUpPrompt state={stepUp} purpose="change your PIN" />
          ) : (
            <Field label={proofLabel}>
              <Input
                value={proof}
                type={status.mfa_enabled ? "text" : "password"}
                autoComplete={status.mfa_enabled ? "one-time-code" : "current-password"}
                onChange={(e) => setProof(e.target.value)}
              />
            </Field>
          )}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="primary" disabled={!pin.trim() || !proofValue}>
              {status.pin_set ? "Change PIN" : "Set PIN"}
            </Button>
            {status.pin_set ? (
              <Button type="button" variant="ghost" disabled={!proofValue} onClick={() => void clear()}>
                Remove PIN
              </Button>
            ) : null}
          </div>
        </form>
      </PanelBody>
    </Panel>
  );
}

function OutOfOffice({ workspace, me, delegates }: Omit<SecurityScreenProps, "status">) {
  const router = useRouter();
  const [until, setUntil] = useState(me.awayUntil ? me.awayUntil.slice(0, 10) : "");
  const [delegate, setDelegate] = useState(me.delegateId ?? "");
  const [error, setError] = useState<string | null>(null);

  async function save(away: boolean) {
    setError(null);
    const r = await settle(
      setMemberAway({
        client: browserApi,
        path: { workspace_id: workspace.id, user_id: me.id },
        body: away ? { away_until: new Date(`${until}T23:59:00Z`).toISOString(), delegate_user_id: delegate || null } : { away_until: null },
      }),
    );
    if (!r.ok) setError(displayMessage(r.error));
    else router.refresh();
  }

  return (
    <Panel aria-labelledby="away-title">
      <PanelHead title={<span id="away-title">Out of office</span>} actions={me.awayUntil ? <Tag tone="warn">Away</Tag> : null} />
      <PanelBody className="grid gap-3">
        <p className="text-sm text-fg-muted">
          While you&apos;re away, no new approval cards reach you, and where a policy names you as an approver your delegate decides instead.
        </p>
        {error ? (
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        ) : null}
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Back on">
            <Input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
          </Field>
          <Field label="Delegate">
            <Select value={delegate} onChange={(e) => setDelegate(e.target.value)}>
              <option value="">Nobody</option>
              {delegates.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </Select>
          </Field>
          <Button variant="primary" disabled={!until} onClick={() => void save(true)}>
            {me.awayUntil ? "Update" : "Set away"}
          </Button>
          {me.awayUntil ? (
            <Button variant="ghost" onClick={() => void save(false)}>
              I&apos;m back
            </Button>
          ) : null}
        </div>
      </PanelBody>
    </Panel>
  );
}

export function SecurityScreen(props: SecurityScreenProps) {
  return (
    <>
      <Topbar title="Your security" sub="Two-step sign-in, approval PIN, out of office" />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        <TwoStep status={props.status} />
        <ApprovalPin status={props.status} />
        <OutOfOffice workspace={props.workspace} me={props.me} delegates={props.delegates} />
      </main>
    </>
  );
}
