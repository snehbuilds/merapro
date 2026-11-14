import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { platformLabel, type AttentionItem } from "@/lib/accounts";

/*
 * dashboard.html's lead notice, from live data: accounts that need
 * reconnecting, then unread critical notifications. Account health comes
 * first because a dead token silently stops publishing. Each links to
 * Connected Accounts with the account selected.
 */
export function AttentionNotices({ items, slug }: { items: readonly AttentionItem[]; slug: string }) {
  if (items.length === 0) return null;
  const accountsHref = (id?: string | null) => `/w/${slug}/accounts${id ? `?account=${encodeURIComponent(id)}` : ""}`;
  return (
    <section aria-label="Needs attention" className="grid gap-2.5">
      {items.map((item) =>
        item.kind === "account" ? (
          <Notice
            key={item.key}
            tone="warn"
            title={`${item.account.nickname} needs reconnecting.`}
            actions={
              <Button asChild size="sm">
                <Link href={accountsHref(item.account.id)}>Reconnect</Link>
              </Button>
            }
          >
            {item.account.status_reason ?? `${platformLabel(item.account.platform)} stopped accepting its token.`} Its
            scheduled posts are on hold; other accounts are unaffected.
          </Notice>
        ) : (
          <Notice
            key={item.key}
            tone="danger"
            title={item.notification.title}
            actions={
              <Button asChild size="sm">
                <Link href={accountsHref(item.notification.social_account_id)}>Open Connected Accounts</Link>
              </Button>
            }
          >
            {item.notification.body}
          </Notice>
        ),
      )}
    </section>
  );
}
