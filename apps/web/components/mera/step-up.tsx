"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";

/*
 * Step-up for an account that signs in only with Google or Microsoft (P3-M7.3). Where
 * someone with a password would type it, they sign in with their provider again; the
 * page comes back with a single-use proof in the URL fragment, good for a few minutes.
 */

export type StepUpState = { proof: string | null; failed: boolean };

/** Reads (and removes) ``#step_up=`` once on load. */
export function readStepUpFragment(hash: string): StepUpState {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  const proof = params.get("step_up");
  return { proof: proof && proof.startsWith("mera_ra_") ? proof : null, failed: params.has("step_up_error") };
}

// Every panel on the page sees the same result; the fragment is removed after the first read.
let landed: StepUpState | null = null;

export function useStepUpProof(): [StepUpState, () => void] {
  const [state, setState] = useState<StepUpState>({ proof: null, failed: false });
  useEffect(() => {
    if (window.location.hash) {
      const found = readStepUpFragment(window.location.hash);
      if (found.proof || found.failed) {
        landed = found;
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
      }
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- browser-only data, read once after mount
    if (landed) setState(landed);
  }, []);
  return [
    state,
    () => {
      landed = null; // single use: spent by whichever action sent it
      setState({ proof: null, failed: false });
    },
  ];
}

export function stepUpHref(provider: "google" | "microsoft", next: string): string {
  const q = new URLSearchParams({ step_up: "1", next });
  return `/api/session/oauth/${provider}/start?${q.toString()}`;
}

export function StepUpPrompt({ state, purpose }: { state: StepUpState; purpose: string }) {
  const [next, setNext] = useState("/");
  // eslint-disable-next-line react-hooks/set-state-in-effect -- browser-only data, read once after mount
  useEffect(() => setNext(window.location.pathname + window.location.search), []);
  if (state.proof) {
    return (
      <Notice tone="info" icon="check">
        Confirmed. You have a few minutes to {purpose}.
      </Notice>
    );
  }
  return (
    <div className="grid gap-2">
      {state.failed ? (
        <Notice tone="danger" role="alert">
          That confirmation did not work. Sign in again with the provider you use for Mera.
        </Notice>
      ) : null}
      <p className="text-sm text-fg-muted">You sign in with a provider, so confirm it is you there to {purpose}.</p>
      <div className="flex flex-wrap gap-2">
        <Button asChild size="sm">
          <a href={stepUpHref("google", next)}>Confirm with Google</a>
        </Button>
        <Button asChild size="sm">
          <a href={stepUpHref("microsoft", next)}>Confirm with Microsoft</a>
        </Button>
      </div>
    </div>
  );
}
