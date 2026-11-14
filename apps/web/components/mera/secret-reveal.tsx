"use client";

import { useState, type ReactNode } from "react";
import { Icon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";

/*
 * A value the API shows exactly once (an API key secret, an invitation link):
 * mono, selectable, with a copy button and the warning that it will not be
 * shown again. Dismissing it is the user's acknowledgement.
 */
export interface SecretRevealProps {
  title: ReactNode;
  value: string;
  /** What the value is, for the copy button's accessible name. */
  label: string;
  children?: ReactNode;
  onDismiss: () => void;
}

export function SecretReveal({ title, value, label, children, onDismiss }: SecretRevealProps) {
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied("copied");
    } catch {
      setCopied("failed");
    }
  }

  return (
    <Notice
      tone="info"
      title={title}
      role="status"
      actions={
        <>
          <Button size="sm" variant="primary" onClick={copy} aria-label={`Copy ${label}`}>
            <Icon name="copy" size="sm" />
            {copied === "copied" ? "Copied" : "Copy"}
          </Button>
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            I&apos;ve saved it
          </Button>
          <span aria-live="polite" className="text-caption text-fg-subtle">
            {copied === "failed" ? "Copy failed — select the text and copy it yourself." : null}
          </span>
        </>
      }
    >
      {children} <strong>You won&apos;t see this again.</strong>
      <code className="mt-2 block rounded-sm border border-border bg-surface px-2 py-1.5 font-mono text-mono-xs break-all text-fg select-all">
        {value}
      </code>
    </Notice>
  );
}
