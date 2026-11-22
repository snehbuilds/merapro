"use client";

import { listOauthApps, saveOauthApp, type OAuthAppOut } from "@mera/sdk";
import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { SkeletonRows } from "@/components/ui/skeleton";
import { browserApi, settle } from "@/lib/api/browser";

const PROVIDERS = {
  linkedin: "LinkedIn",
  meta: "Facebook and Instagram",
  threads: "Threads",
  x: "X",
};

const PROVIDER_HELP = {
  linkedin: { url: "https://www.linkedin.com/developers/apps", steps: "Create an app, open Auth, and copy its Client ID and Client Secret. Add the redirect URL shown below under authorized redirect URLs and request the products and permissions your account needs." },
  meta: { url: "https://developers.facebook.com/apps/", steps: "Create a Meta developer app and add the products needed for Facebook and Instagram. Copy the App ID and App Secret from app settings, then add the redirect URL below to Facebook Login's valid OAuth redirect URIs." },
  threads: { url: "https://developers.facebook.com/apps/", steps: "Create a Meta developer app with the Threads use case. Copy the Threads App ID and App Secret, and add the redirect URL below in the Threads API settings." },
  x: { url: "https://developer.x.com/en/portal/dashboard", steps: "Create a project and app in the developer portal. Enable OAuth 2.0 user authentication, add the redirect URL below, and copy the OAuth 2.0 Client ID and Client Secret." },
};

export function SocialOAuthPanel({ workspaceId, archived }: { workspaceId: string; archived: boolean }) {
  const [apps, setApps] = useState<OAuthAppOut[]>();
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      const result = await settle(listOauthApps({
        client: browserApi,
        path: { workspace_id: workspaceId },
        signal: controller.signal,
      }));
      if (controller.signal.aborted) return;
      if (result.ok) setApps(result.data.data);
      else setLoadFailed(true);
    }
    void load();
    return () => controller.abort();
  }, [workspaceId]);

  return (
    <Panel>
      <PanelHead title="Social OAuth apps" />
      <PanelBody className="grid gap-4">
        <p className="text-sm text-fg-muted">
          Add the OAuth credentials from your developer app, then connect accounts from Social Accounts.
          Add the redirect URLs below to the provider’s developer settings.
        </p>
        {loadFailed ? <Notice tone="danger">Could not load social app settings. Refresh to retry.</Notice> : null}
        {!apps && !loadFailed ? <SkeletonRows rows={4} label="Loading social app settings" /> : null}
        {apps?.map((app) => (
          <OAuthAppForm key={app.provider} app={app} workspaceId={workspaceId} archived={archived} />
        ))}
      </PanelBody>
    </Panel>
  );
}

function OAuthAppForm({ app, workspaceId, archived }: { app: OAuthAppOut; workspaceId: string; archived: boolean }) {
  const [source, setSource] = useState(app.source);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<"saved" | "error">();
  const isMetaApp = app.provider === "meta" || app.provider === "threads";

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || archived) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setFeedback(undefined);
    const result = await settle(saveOauthApp({
      client: browserApi,
      path: { workspace_id: workspaceId, provider: app.provider },
      body: {
        client_id: String(values.get("client_id") ?? "").trim(),
        client_secret: String(values.get("client_secret") ?? "").trim(),
      },
    }));
    if (result.ok) {
      form.reset();
      setSource("workspace");
      setFeedback("saved");
    } else {
      setFeedback("error");
    }
    setBusy(false);
  }

  return (
    <form method="post" onSubmit={(event) => void save(event)} className="grid gap-3 border-t border-border pt-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold">{PROVIDERS[app.provider]}</h3>
        <span className="text-caption text-fg-muted">
          {source === "workspace" ? "Workspace credentials saved" : source === "server" ? "Using server default" : "Setup required"}
        </span>
      </div>
      <ul className="grid gap-1">
        {app.redirect_uris.map((uri) => <li key={uri}><code className="break-all text-caption">{uri}</code></li>)}
      </ul>
      <details className="text-sm text-fg-muted">
        <summary className="cursor-pointer font-semibold">How to find these credentials</summary>
        <p className="mt-2">{PROVIDER_HELP[app.provider].steps}</p>
        <a className="underline" href={PROVIDER_HELP[app.provider].url} target="_blank" rel="noreferrer">Open {PROVIDERS[app.provider]} developer portal</a>
      </details>
      {archived ? null : (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={isMetaApp ? "App ID" : "Client ID"}>
              <Input name="client_id" required maxLength={512} autoComplete="off" disabled={busy} />
            </Field>
            <Field label={isMetaApp ? "App secret" : "Client secret"}>
              <Input type="password" name="client_secret" required maxLength={4096} autoComplete="new-password" disabled={busy} />
            </Field>
          </div>
          <Button type="submit" className="justify-self-start" disabled={busy}>
            {busy ? "Saving…" : source === "workspace" ? "Replace credentials" : "Save credentials"}
          </Button>
          {feedback === "saved" ? <Notice role="status">Saved. Reconnect accounts you want to move to this app.</Notice> : null}
          {feedback === "error" ? <Notice tone="danger" role="alert">Could not save credentials. Check your access and try again.</Notice> : null}
        </>
      )}
    </form>
  );
}
