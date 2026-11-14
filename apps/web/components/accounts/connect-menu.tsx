"use client";

import { connectAccount, signInAccount } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { Icon, PlatformMark } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { ACCOUNT_ERROR_TEXT, PLATFORM_CATALOGUE, connectReturnPath, platformInfo, type ApiPlatform } from "@/lib/accounts";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { cn } from "@/lib/utils";

/*
 * The topbar's "Connect account" disclosure. Choosing a platform asks the API
 * for an authorize URL and sends the browser there; the platform returns to the
 * API's callback, which redirects back to /w/<slug>/accounts with
 * ?connect_code= or ?error= (handled by the accounts screen). Mastodon first
 * asks which server; Bluesky takes a handle and an app password here and hands
 * the same one-time code to the accounts screen (P2-M3).
 */
export function ConnectMenu({
  workspaceId,
  slug,
  disabledReason,
}: {
  workspaceId: string;
  slug: string;
  /** Set when the viewer can't connect (role, archived): the button is disabled with this as its title. */
  disabledReason?: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState<ApiPlatform | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<ApiPlatform | null>(null);
  const [identifier, setIdentifier] = useState("");
  const [secret, setSecret] = useState("");
  const [instance, setInstance] = useState("");
  const panelId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const firstRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    firstRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    function onPointer(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [open]);

  async function connect(platform: ApiPlatform) {
    const method = platformInfo(platform).connect;
    if (method !== "oauth" && form !== platform) {
      setForm(platform);
      setError(null);
      return;
    }
    setPending(platform);
    setError(null);
    if (method === "app_password") {
      const signed = await settle(
        signInAccount({
          client: browserApi,
          path: { workspace_id: workspaceId, platform },
          body: { identifier: identifier.trim(), secret: secret.trim() },
        }),
      );
      setSecret("");
      if (!signed.ok) {
        setPending(null);
        setError(ACCOUNT_ERROR_TEXT[signed.error.code] ?? displayMessage(signed.error));
        return;
      }
      setOpen(false);
      setPending(null);
      router.push(`${connectReturnPath(slug)}?connect_code=${encodeURIComponent(signed.data.connect_code)}`);
      return;
    }
    const result = await settle(
      connectAccount({
        client: browserApi,
        path: { workspace_id: workspaceId, platform },
        body: { return_path: connectReturnPath(slug), ...(method === "oauth_instance" ? { instance: instance.trim() } : {}) },
      }),
    );
    if (!result.ok) {
      setPending(null);
      setError(ACCOUNT_ERROR_TEXT[result.error.code] ?? displayMessage(result.error));
      return;
    }
    window.location.assign(result.data.authorize_url);
  }

  return (
    <div ref={rootRef} className="relative shrink-0">
      <Button
        ref={triggerRef}
        variant="primary"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        disabled={disabledReason !== undefined}
        title={disabledReason}
        onClick={() => setOpen((o) => !o)}
        className="max-narrow:w-8 max-narrow:px-0"
      >
        <Icon name="plus" size="sm" />
        <span className="max-narrow:sr-only">Connect account</span>
        <Icon name="chevdown" size="sm" className="max-narrow:hidden" />
      </Button>
      {open ? (
        <div
          id={panelId}
          role="group"
          aria-label="Connect an account"
          className="absolute top-[calc(100%+6px)] right-0 z-30 w-68 rounded-md border border-border bg-surface-raised p-1 shadow-popover"
        >
          <ul>
            {PLATFORM_CATALOGUE.map((p, i) => {
              const isFirst = i === 0;
              return (
                <li key={p.platform}>
                  <button
                    ref={isFirst ? firstRef : undefined}
                    type="button"
                    disabled={pending !== null}
                    onClick={() => connect(p.platform)}
                    className={cn(
                      "flex h-9 w-full items-center gap-2.5 rounded-sm px-2 text-left text-sm text-fg",
                      "hover:bg-surface-hover disabled:cursor-not-allowed disabled:hover:bg-transparent",
                    )}
                  >
                    <span
                      aria-hidden="true"
                      className={cn(
                        "grid size-[22px] shrink-0 place-items-center rounded-full bg-surface-inverse text-fg-inverse [&>svg]:size-[11px]",
                      )}
                    >
                      <PlatformMark platform={p.icon} decorative />
                    </span>
                    <span className="flex-1">{p.label}</span>
                    {pending === p.platform ? <span className="text-caption text-fg-subtle">Opening…</span> : null}
                  </button>
                  {form === p.platform ? (
                    <form
                      className="grid gap-2 px-2 pt-1 pb-2"
                      aria-label={`Connect ${p.label}`}
                      onSubmit={(e) => {
                        e.preventDefault();
                        void connect(p.platform);
                      }}
                    >
                      {p.connect === "app_password" ? (
                        <>
                          <Field label="Handle">
                            <Input value={identifier} placeholder="you.bsky.social" autoComplete="username" onChange={(e) => setIdentifier(e.target.value)} />
                          </Field>
                          <Field label="App password" hint="Create one in Bluesky: Settings, Privacy and security, App passwords. Never your account password.">
                            <Input
                              value={secret}
                              type="password"
                              placeholder="xxxx-xxxx-xxxx-xxxx"
                              autoComplete="off"
                              onChange={(e) => setSecret(e.target.value)}
                            />
                          </Field>
                        </>
                      ) : (
                        <Field label="Server" hint="Where the account lives, like mastodon.social.">
                          <Input value={instance} placeholder="mastodon.social" onChange={(e) => setInstance(e.target.value)} />
                        </Field>
                      )}
                      <Button
                        type="submit"
                        size="sm"
                        variant="primary"
                        disabled={pending !== null || (p.connect === "app_password" ? !identifier.trim() || !secret.trim() : !instance.trim())}
                      >
                        {pending === p.platform ? "Connecting…" : `Connect ${p.label}`}
                      </Button>
                    </form>
                  ) : null}
                </li>
              );
            })}
          </ul>
          {error ? (
            <p role="alert" className="border-t border-border px-2 pt-2 pb-1.5 text-caption text-danger">
              {error}
            </p>
          ) : (
            <p className="border-t border-border px-2 pt-2 pb-1.5 text-caption text-fg-subtle">
              You&apos;ll sign in at the platform, then come back here.
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}
