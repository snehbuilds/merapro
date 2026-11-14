"use client";

import { completeConnect } from "@mera/sdk";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { ConnectResult, PageChoice } from "@/lib/accounts";
import { browserApi, settle } from "@/lib/api/browser";
import { connectErrorCode } from "@/lib/accounts";

/*
 * P2-M1.5: a Facebook or Instagram sign-in that manages several Pages. The connect
 * code is still unredeemed; picking a Page redeems it for that one only. The other
 * Pages' tokens are discarded by the API.
 */
export function PagePicker({
  code,
  choices,
  onDone,
}: {
  code: string;
  choices: PageChoice[];
  onDone: (result: ConnectResult) => void;
}) {
  const [pending, setPending] = useState<string | null>(null);

  async function pick(choice: PageChoice) {
    setPending(choice.platform_account_id);
    const r = await settle(
      completeConnect({ client: browserApi, body: { connect_code: code, platform_account_id: choice.platform_account_id } }),
    );
    setPending(null);
    if (r.ok) {
      onDone({ kind: r.data.outcome === "reconnected" ? "reconnected" : "connected", accountId: r.data.account_id });
    } else {
      onDone({ kind: "error", code: connectErrorCode(r.error.code), message: r.error.message });
    }
  }

  return (
    <ul className="mt-2 grid gap-1.5" aria-label="Pages this sign-in manages">
      {choices.map((c) => (
        <li key={c.platform_account_id} className="flex flex-wrap items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-sm">
            <span className="font-medium">{c.display_name ?? c.handle}</span>
            <span className="text-fg-subtle"> · @{c.handle}</span>
          </span>
          <Button size="sm" variant="primary" disabled={pending !== null} onClick={() => void pick(c)}>
            {pending === c.platform_account_id ? "Connecting…" : `Connect ${c.display_name ?? c.handle}`}
          </Button>
        </li>
      ))}
    </ul>
  );
}
