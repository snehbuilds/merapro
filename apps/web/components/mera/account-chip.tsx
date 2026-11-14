import type { ComponentProps, ReactNode } from "react";
import { PlatformMark, PLATFORM_LABELS, type Platform } from "@/components/icons";
import { cn } from "@/lib/utils";

/*
 * mera.css .acct: the recurring atom. A round ink mark carrying the platform
 * glyph, the account's nickname, and (lg) a mono handle / meta line under it.
 * The platform is announced through the mark's sr text, since the glyph is the
 * only visible cue for it.
 */
export interface AccountRef {
  id: string;
  platform: Platform;
  /** The workspace's nickname for the account ("Work LinkedIn"). */
  name: string;
  /** Platform handle, e.g. "@alexmercer". */
  handle?: string;
  avatar_url?: string | null;
}

export function AccountMark({
  platform,
  avatar_url,
  size = "md",
  className,
}: {
  platform: Platform;
  avatar_url?: string | null;
  size?: "md" | "lg";
  className?: string;
}) {
  if (avatar_url) {
    return (
      <span className={cn("relative shrink-0 inline-flex items-center justify-center", className)}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={avatar_url}
          alt=""
          referrerPolicy="no-referrer"
          className={cn(
            "rounded-full object-cover border border-border-subtle bg-surface-muted",
            size === "lg" ? "size-[30px]" : "size-[22px]",
          )}
          onError={(e) => {
            (e.currentTarget as HTMLElement).style.display = "none";
          }}
        />
        <span
          data-slot="account-mark"
          className={cn(
            "absolute -bottom-0.5 -right-0.5 grid place-items-center rounded-full bg-surface-inverse text-fg-inverse shadow-sm",
            size === "lg" ? "size-3.5 [&>svg]:size-2" : "size-2.5 [&>svg]:size-1.5",
          )}
        >
          <PlatformMark platform={platform} decorative />
          <span className="sr-only">{PLATFORM_LABELS[platform]}: </span>
        </span>
      </span>
    );
  }

  return (
    <span
      data-slot="account-mark"
      className={cn(
        "grid shrink-0 place-items-center rounded-full bg-surface-inverse text-fg-inverse",
        size === "lg" ? "size-[30px] [&>svg]:size-3.5" : "size-[22px] [&>svg]:size-[11px]",
        className,
      )}
    >
      <PlatformMark platform={platform} decorative />
      <span className="sr-only">{PLATFORM_LABELS[platform]}: </span>
    </span>
  );
}

export interface AccountChipProps extends Omit<ComponentProps<"span">, "children"> {
  account: Pick<AccountRef, "platform" | "name" | "handle"> & { avatar_url?: string | null };
  size?: "md" | "lg";
  /** lg only: the line under the name. Defaults to the handle. */
  meta?: ReactNode;
}

export function AccountChip({ account, size = "md", meta, className, ...props }: AccountChipProps) {
  const line = meta ?? account.handle;
  return (
    <span
      data-slot="account-chip"
      data-size={size}
      className={cn("inline-flex min-w-0 items-center gap-2", className)}
      {...props}
    >
      <AccountMark platform={account.platform} avatar_url={account.avatar_url} size={size} />
      {size === "lg" ? (
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium">{account.name}</span>
          {line ? <span className="block truncate text-xs text-fg-subtle">{line}</span> : null}
        </span>
      ) : (
        <span className="min-w-0 truncate text-sm font-medium">{account.name}</span>
      )}
    </span>
  );
}

/** mera.css .acctstack: overlapping marks for "these accounts". */
export function AccountStack({ accounts, className }: { accounts: readonly Pick<AccountRef, "platform" | "name">[]; className?: string }) {
  return (
    <span data-slot="account-stack" className={cn("inline-flex items-center", className)}>
      {accounts.map((a, i) => (
        <AccountMark
          key={`${a.name}-${i}`}
          platform={a.platform}
          className={cn(i > 0 && "-ml-1.5", "shadow-[0_0_0_2px_var(--bg-raised)]")}
        />
      ))}
      <span className="sr-only">{accounts.map((a) => a.name).join(", ")}</span>
    </span>
  );
}
