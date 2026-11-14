"use client";

import { useState, type ReactNode } from "react";
import { AccountChip, AccountStack } from "@/components/mera/account-chip";
import { ApprovalProof } from "@/components/mera/approval-proof";
import { CapabilityTags } from "@/components/mera/capability-tags";
import { CharCounter } from "@/components/mera/char-counter";
import { DataTable, dataTableColumns, matrixCell } from "@/components/mera/data-table";
import { GroupDot, GroupLabel } from "@/components/mera/group-dot";
import { PlatformPreview } from "@/components/mera/platform-preview";
import { PostSetCard } from "@/components/mera/post-set-card";
import { ResolvedSettingRow, ResolvedSettingsTable } from "@/components/mera/resolved-setting-row";
import { StaggerTimeline } from "@/components/mera/stagger-timeline";
import { Timeline, type TimelineItem } from "@/components/mera/timeline";
import { TwoPaneList } from "@/components/mera/two-pane-list";
import { VariantDecisionCard } from "@/components/mera/variant-decision-card";
import { VariantRow } from "@/components/mera/variant-row";
import { Button } from "@/components/ui/button";
import { ButtonGroup } from "@/components/ui/button-group";
import { Chip } from "@/components/ui/chip";
import { DescriptionList } from "@/components/ui/description-list";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Kbd } from "@/components/ui/kbd";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Meter, ProgressBar } from "@/components/ui/progress";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { Switch } from "@/components/ui/switch";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { groupColor } from "@/lib/group-color";
import { STATUS_ENUMS, type StatusKind } from "@/lib/status";
import { NOW, TZ, accounts, hiring, kafka, postgres, stagger } from "./demo";

/* --- Data table fixtures (module scope: stable inputs) -------------------- */

interface AccountRow {
  name: string;
  platform: string;
  published: number;
  engagement: number;
  failed: number;
}

const accountRows: AccountRow[] = [
  { name: "Work LinkedIn", platform: "LinkedIn", published: 38, engagement: 4.2, failed: 0 },
  { name: "Acme Page", platform: "LinkedIn", published: 31, engagement: 2.9, failed: 1 },
  { name: "X @main", platform: "X", published: 64, engagement: 1.8, failed: 2 },
  { name: "X @tech", platform: "X", published: 52, engagement: 2.3, failed: 0 },
];

const ac = dataTableColumns<AccountRow>();
const accountColumns = ac.columns([
  ac.accessor("name", { header: "Account" }),
  ac.accessor("platform", { header: "Platform", enableSorting: false }),
  ac.accessor("published", { header: "Published", meta: { align: "end" } }),
  ac.accessor("engagement", {
    header: "Engagement",
    meta: { align: "end" },
    cell: (info) => `${info.getValue().toFixed(1)}%`,
  }),
  ac.accessor("failed", { header: "Failed", meta: { align: "end" } }),
]);

interface PermissionRow {
  action: string;
  viewer: boolean;
  editor: boolean;
  approver: boolean;
  admin: boolean;
  owner: boolean;
}

const permissionRows: PermissionRow[] = [
  { action: "Approve posts", viewer: false, editor: false, approver: true, admin: true, owner: true },
  { action: "Edit drafts", viewer: false, editor: true, approver: true, admin: true, owner: true },
  { action: "Engage the kill switch", viewer: false, editor: true, approver: true, admin: true, owner: true },
  { action: "Manage billing", viewer: false, editor: false, approver: false, admin: false, owner: true },
];

const pc = dataTableColumns<PermissionRow>();
const roles = ["viewer", "editor", "approver", "admin", "owner"] as const;
const permissionColumns = pc.columns([
  pc.accessor("action", { header: "Action", enableSorting: false }),
  ...roles.map((r) =>
    pc.accessor(r, {
      header: r[0]!.toUpperCase() + r.slice(1),
      enableSorting: false,
      meta: { align: "end" },
      cell: (info) => matrixCell(info.getValue()),
    }),
  ),
]);

const runSteps: TimelineItem[] = [
  { id: "1", kind: "research", detail: "anthropic/claude-haiku-4-5 · summarize and classify", meta: "$0.0008" },
  { id: "2", kind: "media", detail: "og_image_fetch · 3 renditions derived", meta: "$0" },
  { id: "3", kind: "writer", detail: "→ Work LinkedIn", meta: "$0.0310" },
  {
    id: "4",
    kind: "writer",
    detail: "→ X @tech · fell back to openai/gpt-5, primary rate limited",
    meta: "$0.0265",
    tone: "warn",
  },
  {
    id: "5",
    kind: "guardrail",
    detail: "cross_account_duplicate · WARN 0.84 · X @main vs X @tech",
    meta: "no cost",
    metaMono: false,
    tone: "warn",
  },
  { id: "6", kind: "writer", detail: "→ X @tech · differentiate mode", meta: "$0.0198" },
  {
    id: "7",
    kind: "guardrail",
    detail: "cross_account_duplicate · PASS 0.41",
    meta: "no cost",
    metaMono: false,
    tone: "ok",
  },
  { id: "8", kind: "editor", detail: "→ Work LinkedIn · confidence 0.84", meta: "$0.0083" },
];

const inboxItems = [
  {
    id: "m1",
    from: "Dana Whitfield",
    account: accounts.workLinkedIn,
    kind: { label: "Question", tone: "info" as const },
    body: "Does the async I/O work land for replicas too, or primary only? We're on 16 and planning an upgrade.",
    ago: "18m ago",
  },
  {
    id: "m2",
    from: "@kvothe_dev",
    account: accounts.xMain,
    kind: { label: "Complaint", tone: "danger" as const },
    body: "Third time this week you've posted the same benchmark. Is anyone actually reading these before they go out?",
    ago: "41m ago",
  },
];

/* --- Layout helpers ------------------------------------------------------- */

function Section({ title, children, foot }: { title: string; children: ReactNode; foot?: ReactNode }) {
  return (
    <Panel>
      <PanelHead title={title} as="h3" />
      <PanelBody className="grid gap-4">{children}</PanelBody>
      {foot ? <PanelFoot>{foot}</PanelFoot> : null}
    </Panel>
  );
}

function Row({ label, children }: { label?: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5">
      {label ? <span className="font-label text-2xs font-semibold tracking-[0.1em] text-fg-subtle uppercase">{label}</span> : null}
      <div className="flex flex-wrap items-center gap-2.5">{children}</div>
    </div>
  );
}

const noop = () => {};

/* --- Sections ------------------------------------------------------------- */

export function PrimitivesGallery() {
  const [chips, setChips] = useState({ pending: true, edit: false, decided: false });
  const [notify, setNotify] = useState(true);
  const [body, setBody] = useState(postgres.bodies.xTech);
  return (
    <>
      <Section title="Buttons">
        <Row label="Variants">
          <Button>Default</Button>
          <Button variant="primary">Approve remaining 3</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">Reject all</Button>
          <Button disabled>Disabled</Button>
        </Row>
        <Row label="Small · group">
          <Button size="sm">Review</Button>
          <Button size="sm" variant="primary">
            Approve all 4
          </Button>
          <ButtonGroup aria-label="Calendar range">
            <Button size="sm">Day</Button>
            <Button size="sm">Week</Button>
            <Button size="sm">Month</Button>
          </ButtonGroup>
          <Kbd>⌘</Kbd>
          <Kbd>K</Kbd>
        </Row>
      </Section>

      <Section title="Chips">
        <Row label="Toggle (aria-pressed) · count suffix">
          <Chip pressed={chips.pending} count={7} onClick={() => setChips((c) => ({ ...c, pending: !c.pending }))}>
            Pending
          </Chip>
          <Chip pressed={chips.edit} count={2} onClick={() => setChips((c) => ({ ...c, edit: !c.edit }))}>
            Awaiting edit
          </Chip>
          <Chip pressed={chips.decided} onClick={() => setChips((c) => ({ ...c, decided: !c.decided }))}>
            Decided
          </Chip>
          <Chip pressed>
            <GroupDot group={0} />
            Tech News
          </Chip>
        </Row>
        <Row label="Filter trigger">
          <Chip variant="filter" aria-haspopup="menu">
            All groups
          </Chip>
          <Chip variant="filter" aria-haspopup="menu">
            All accounts
          </Chip>
        </Row>
      </Section>

      <Section title="Tags and status">
        <Row label="Tags">
          <Tag>Neutral</Tag>
          <Tag tone="ok">Publish</Tag>
          <Tag tone="warn">1 of 2 approvers</Tag>
          <Tag tone="danger">Complaint</Tag>
          <Tag tone="info">Question</Tag>
          <Tag tone="outline">0.84</Tag>
        </Row>
        <Row label="Shapes (ad-hoc)">
          <StatusPill tone="ok" label="Healthy" />
          <StatusPill tone="warn" shape="half" label="Expires in 11h 42m" />
          <StatusPill tone="danger" label="Needs reauth" />
          <StatusPill tone="info" label="Scheduled" />
          <StatusPill tone="neutral" shape="ring" label="Not connected" />
        </Row>
        <Row label="Derived labels">
          <StatusPill kind="post_variant" state="needs_approval" modifiers={{ pinRequired: true }} />
          <StatusPill kind="approval_variant" state="pending" modifiers={{ approvalsRemaining: 1 }} />
          <StatusPill kind="scheduled_post" state="failed" modifiers={{ attempt: 2, maxAttempts: 5 }} />
          <StatusPill kind="post_set" state="needs_approval" modifiers={{ via: "Telegram" }} />
        </Row>
      </Section>

      <Section title="Status table" foot="Every backend enum value, looked up in lib/status.ts. Partial is normal: never danger.">
        {(Object.keys(STATUS_ENUMS) as StatusKind[]).map((kind) => (
          <Row key={kind} label={kind}>
            {STATUS_ENUMS[kind].map((state) => (
              // eslint-disable-next-line @typescript-eslint/no-explicit-any -- iterating a correlated union
              <StatusPill key={state} kind={kind as any} state={state as any} />
            ))}
          </Row>
        ))}
      </Section>

      <Section title="Controls">
        <div className="grid gap-4 wide:grid-cols-2">
          <Field label="Stagger" hint="Two X accounts in this group — staggering is required.">
            <Select defaultValue="random">
              <option value="random">Random spread · 3–15 min</option>
              <option value="fixed">Fixed interval · 4 min</option>
              <option value="all">All at once</option>
            </Select>
          </Field>
          <Field label="Alt text">
            <Input defaultValue="A terminal showing PostgreSQL 18 release notes beside a benchmark chart." />
          </Field>
          <Field label="Daily cap" error="Must be between 1 and 20.">
            <Input type="number" defaultValue={42} />
          </Field>
          <Field label="Disabled">
            <Input disabled placeholder="Not editable" />
          </Field>
        </div>
        <Field label="X @tech" hint={<CharCounter text={body} limit={280} />}>
          <Textarea value={body} onChange={(e) => setBody(e.target.value)} />
        </Field>
        <Row label="Segmented control (radiogroup, arrow keys)">
          <SegmentedControl
            aria-label="Theme"
            defaultValue="light"
            options={[
              { value: "light", label: "Light" },
              { value: "dark", label: "Dark" },
              { value: "system", label: "System" },
            ]}
          />
          <SegmentedControl
            aria-label="Preview size"
            options={[
              { value: "desktop", label: "Desktop" },
              { value: "mobile", label: "Mobile" },
            ]}
          />
        </Row>
        <Row label="Switch">
          <span className="inline-flex items-center gap-2 text-sm">
            <Switch checked={notify} onCheckedChange={setNotify} aria-label="Approval requests" />
            Approval requests
          </span>
          <span className="inline-flex items-center gap-2 text-sm">
            <Switch aria-label="Cost threshold alerts" />
            Cost threshold alerts
          </span>
          <span className="inline-flex items-center gap-2 text-sm">
            <Switch defaultChecked disabled aria-label="Locked on" />
            Locked on
          </span>
        </Row>
      </Section>

      <Section title="Progress, meters, lists">
        <div className="grid gap-4 wide:grid-cols-2">
          <div className="grid gap-2">
            <div className="flex justify-between text-sm">
              <span>Decisions</span>
              <span className="font-mono">1 / 4</span>
            </div>
            <ProgressBar value={1} max={4} tone="ok" label="Decisions" valueText="1 of 4 decided" />
            <div className="flex justify-between text-sm">
              <span>LLM spend</span>
              <span className="font-mono">36%</span>
            </div>
            <Meter value={36} label="LLM spend" />
            <div className="flex justify-between text-sm">
              <span>X API quota</span>
              <span className="font-mono">62%</span>
            </div>
            <Meter value={62} label="X API quota" />
            <div className="flex justify-between text-sm">
              <span>Storage</span>
              <span className="font-mono">94%</span>
            </div>
            <Meter value={94} label="Storage" />
          </div>
          <DescriptionList
            items={[
              { term: "Policy", detail: "Tech News group" },
              { term: "Approvers", detail: "Any 1 of 2" },
              { term: "Requested", detail: <Time value="2026-11-08T15:18:00Z" tz={TZ} /> },
              { term: "Escalates", detail: "11:18 → Priya" },
              { term: "Platform account", detail: "urn:li:person:A1b2C3", mono: true },
            ]}
          />
        </div>
      </Section>

      <Section title="Notices">
        <Notice>
          <strong>PostgreSQL 18 Released.</strong> Async I/O subsystem, OAuth support, and skip-scan index
          improvements.
        </Notice>
        <Notice tone="warn" title="Two X accounts in this group.">
          Staggering is required and each account must receive different text.
        </Notice>
        <Notice
          tone="danger"
          title="Work LinkedIn needs reauthorisation."
          actions={
            <>
              <Button size="sm" variant="primary">
                Reconnect
              </Button>
              <Button size="sm" variant="ghost">
                Pause this account
              </Button>
            </>
          }
        >
          Publishing to this account is paused until it is reconnected.
        </Notice>
        <Notice tone="info">
          Changing the image creates a new version and invalidates any approval for this account.
        </Notice>
      </Section>

      <Section title="Panels, empty, loading">
        <div className="grid gap-4 wide:grid-cols-2">
          <Panel tone="danger">
            <PanelHead title="Danger zone" as="h4" />
            <PanelBody className="flex items-center justify-between gap-3">
              <div>
                <div className="text-sm font-semibold">Archive workspace</div>
                <div className="text-xs text-fg-subtle">Stops automation and ingestion, keeps everything readable</div>
              </div>
              <Button size="sm" variant="danger">
                Archive
              </Button>
            </PanelBody>
          </Panel>
          <Panel>
            <PanelHead title="Awaiting your approval" as="h4" />
            <EmptyState
              icon="approvals"
              title="Nothing waiting"
              description="New post sets land here when an agent finishes drafting. Approved sets move to the calendar."
              action={<Button size="sm">Open the calendar</Button>}
            />
          </Panel>
          <Panel>
            <PanelHead title="Published today" as="h4" />
            <SkeletonRows rows={3} label="Loading published posts" />
          </Panel>
          <Panel>
            <PanelHead title="Dead letters" as="h4" />
            <EmptyState tone="quiet" icon="check" title="No dead letters" description="Every post published or is still retrying." />
            <div className="grid gap-2 px-3.5 pb-3.5">
              <Skeleton className="w-1/2" />
              <Skeleton className="w-3/4" />
            </div>
          </Panel>
        </div>
      </Section>
    </>
  );
}

export function SignatureGallery() {
  const [viewport, setViewport] = useState<"desktop" | "mobile">("desktop");
  return (
    <>
      <Section title="Accounts and groups">
        <Row label="Account chip">
          <AccountChip account={accounts.workLinkedIn} />
          <AccountChip account={accounts.xMain} />
          <AccountChip account={accounts.bluesky} />
          <AccountStack accounts={[accounts.workLinkedIn, accounts.acmePage, accounts.xMain, accounts.xTech]} />
        </Row>
        <Row label="Account chip lg">
          <AccountChip size="lg" account={accounts.workLinkedIn} meta="@alexmercer · personal · 09:00" />
          <AccountChip size="lg" account={accounts.acmePage} meta="Acme Inc · company page · 09:04" />
        </Row>
        <Row label="Group dots: seeds 0–2, generated 3–8, hashed id">
          <GroupLabel group={0} name="Tech News" />
          <GroupLabel group={1} name="Company Official" />
          <GroupLabel group={2} name="Launch" />
          {[3, 4, 5, 6, 7, 8].map((i) => (
            <span key={i} className="inline-flex items-center gap-1 font-mono text-xs text-fg-subtle" title={groupColor(i)}>
              <GroupDot group={i} />
              {i}
            </span>
          ))}
          <GroupLabel group="grp_01JTECH" name="grp_01JTECH" />
        </Row>
        <Row label="Capabilities (from the descriptor)">
          <CapabilityTags
            capabilities={{
              supports_publish: true,
              supports_images: true,
              supports_alt_text: true,
              supports_delete: true,
              supports_threads: false,
              supports_edit: false,
              supports_scheduling: false,
              analytics: "counts",
            }}
          />
        </Row>
        <Row label="Time (workspace zone shown)">
          <Time value={stagger[0].at} tz={TZ} />
          <Time value={stagger[0].at} tz="Europe/London" />
          <Time value={stagger[0].at} tz="UTC" format="datetime" />
          <Time value={stagger[0].at} tz="Asia/Tokyo" format="date" />
        </Row>
      </Section>

      <Panel>
        <PanelHead title="Post sets" as="h3" />
        <div>
          <PostSetCard
            title={postgres.title}
            group={{ key: 0, name: "Tech News" }}
            accountCount={4}
            source={postgres.source}
            stagger={{ start: stagger[0].at, end: stagger[3].at, tz: TZ }}
            expiry={{ at: postgres.expiresAt, now: NOW }}
            aside={
              <>
                <Button size="sm">Review</Button>
                <Button size="sm" variant="primary">
                  Approve remaining 3
                </Button>
              </>
            }
          >
            <VariantRow account={accounts.workLinkedIn} excerpt={postgres.excerpts.workLinkedIn} confidence={0.84} time={stagger[0].at} tz={TZ} showZone={false} state="needs_approval" />
            <VariantRow account={accounts.acmePage} excerpt={postgres.excerpts.acmePage} confidence={0.91} time={stagger[1].at} tz={TZ} showZone={false} state="needs_approval" statusModifiers={{ pinRequired: true }} />
            <VariantRow account={accounts.xMain} excerpt={postgres.excerpts.xMain} confidence={0.77} time={stagger[2].at} tz={TZ} showZone={false} state="approved" />
            <VariantRow account={accounts.xTech} excerpt={postgres.excerpts.xTech} confidence={0.73} time={stagger[3].at} tz={TZ} showZone={false} state="needs_approval" />
          </PostSetCard>
          <PostSetCard
            title={kafka.title}
            group={{ key: 0, name: "Tech News" }}
            accountCount={4}
            source={kafka.source}
            stagger={{ ...kafka.stagger, tz: TZ }}
            expiry={{ at: kafka.expiresAt, now: NOW }}
            defaultExpanded={false}
            aside={<Button size="sm">Review</Button>}
          >
            <VariantRow account={accounts.workLinkedIn} excerpt="Kafka 4.0 finally removes ZooKeeper." confidence={0.79} />
            <VariantRow account={accounts.xTech} off={{ reason: "Disabled — too close to @main's take on the same story" }} />
          </PostSetCard>
          <PostSetCard
            title={hiring.title}
            group={{ key: 1, name: "Company Official" }}
            accountCount={3}
            source="Manual"
            expiry={{ at: hiring.expiresAt, now: NOW }}
            defaultExpanded={false}
            aside={<Tag tone="warn">1 of 2 approvers</Tag>}
          >
            <VariantRow account={accounts.acmePage} excerpt="We're hiring a staff platform engineer." state="needs_approval" statusModifiers={{ approvalsRemaining: 1 }} />
          </PostSetCard>
        </div>
      </Panel>

      <Panel>
        <PanelHead title="Per-account decisions" as="h3" />
        <div>
          <VariantDecisionCard
            account={accounts.workLinkedIn}
            accountKind="personal"
            scheduledAt={stagger[0].at}
            tz={TZ}
            confidence={0.84}
            state="pending"
            preview={
              <PlatformPreview
                platform="linkedin"
                layout="compact"
                body={postgres.bodies.workLinkedIn}
                hashtags={["postgres", "databases"]}
                media={{ label: "1200 × 627", summary: "1 image · alt text set" }}
                foot={<CharCounter text={postgres.bodies.workLinkedIn} limit={3000} />}
              />
            }
            notices={
              <Notice tone="warn">
                Similar to your post from 14 Oct (0.87). Same account, different story — worth a glance before approving.
              </Notice>
            }
            onApprove={noop}
            onEdit={noop}
            onRegenerate={noop}
            onReschedule={noop}
            onReject={noop}
          />
          <VariantDecisionCard
            account={accounts.acmePage}
            accountKind="company page"
            scheduledAt={stagger[1].at}
            tz={TZ}
            confidence={0.91}
            state="pending"
            statusModifiers={{ pinRequired: true }}
            preview={
              <PlatformPreview
                platform="linkedin"
                layout="compact"
                body={postgres.bodies.acmePage}
                hashtags={["postgres", "infrastructure"]}
                media={{ label: "1 image" }}
                foot={<CharCounter text={postgres.bodies.acmePage} limit={3000} />}
              />
            }
            actionNote="Company page — publishing asks for your PIN."
            onApprove={noop}
            onEdit={noop}
            onReject={noop}
          />
          <VariantDecisionCard
            account={accounts.xMain}
            accountKind="thread, 3 posts"
            scheduledAt={stagger[2].at}
            tz={TZ}
            confidence={0.77}
            state="approved"
            preview={
              <PlatformPreview
                platform="x"
                layout="compact"
                body={postgres.bodies.xMain}
                hashtags={["postgres"]}
                foot={<CharCounter count={87} limit={280} suffix="part 1 of 3" />}
              />
            }
            proof={{
              approver: "You",
              at: "2026-11-09T17:12:00Z",
              tz: TZ,
              surface: "Telegram",
              account: "X @main",
              version: 1,
              hash: "7f3a9c1d8e2b4056",
            }}
            caption="Editing this post now creates v2 and invalidates this approval. The other three accounts keep theirs."
          />
          <VariantDecisionCard
            account={accounts.xTech}
            scheduledAt={stagger[3].at}
            tz={TZ}
            confidence={0.73}
            state="pending"
            capabilities={{ supports_edit: false, edit_unsupported_reason: "X does not support editing scheduled posts." }}
            preview={
              <PlatformPreview
                platform="x"
                layout="compact"
                body={postgres.bodies.xTech}
                hashtags={["postgres"]}
                foot={<CharCounter text={postgres.bodies.xTech} limit={280} />}
              />
            }
            notices={
              <Notice tone="info">
                Written as a question to stay distinct from <strong>X @main</strong> in the same set — similarity 0.41,
                well under the 0.90 block threshold.
              </Notice>
            }
            onApprove={noop}
            onEdit={noop}
            onRegenerate={noop}
            onReject={noop}
          />
        </div>
      </Panel>

      <Section title="Preview, counter, proof">
        <div className="flex items-center gap-3">
          <SegmentedControl
            aria-label="Preview viewport"
            value={viewport}
            onValueChange={setViewport}
            options={[
              { value: "desktop", label: "Desktop" },
              { value: "mobile", label: "Mobile" },
            ]}
          />
        </div>
        <PlatformPreview
          platform="linkedin"
          viewport={viewport}
          author={{ name: "Alex Mercer", subtitle: "Platform engineering · Now" }}
          body={postgres.bodies.workLinkedIn}
          hashtags={["postgres", "databases"]}
          media={{ label: "1200 × 627" }}
          foot="Renders as LinkedIn shows it · 3,000 character limit · 3–5 hashtags conventional"
        />
        <Row label="Char counter (graphemes)">
          <CharCounter text={postgres.bodies.workLinkedIn} limit={3000} />
          <CharCounter text="👍🏽🇳🇿é" limit={3} />
          <CharCounter text={`${postgres.bodies.xTech} ${postgres.bodies.xMain}`} limit={280} />
          <CharCounter count={87} limit={280} suffix="part 1 of 3" />
        </Row>
        <ApprovalProof
          approver="You"
          at="2026-11-09T17:12:00Z"
          tz={TZ}
          surface="Telegram"
          account="X @main"
          version={1}
          hash="7f3a9c1d8e2b4056"
        />
      </Section>

      <Section title="Stagger and resolved settings">
        <StaggerTimeline tz={TZ} items={stagger.map((s) => ({ id: s.id, at: s.at, label: s.account.name }))} />
        <ResolvedSettingsTable caption="Resolved settings for Tech News">
          <ResolvedSettingRow setting="Brand voice" value="Technical" source="group" rule="most_specific" />
          <ResolvedSettingRow setting="Posting slots" value="09:00, 14:00" source="group" rule="most_specific" />
          <ResolvedSettingRow setting="Daily cap" value="3 per account" source="workspace" rule="most_specific" />
          <ResolvedSettingRow setting="Autopilot mode" value="Manual" source="account" rule="strictest_wins" />
          <ResolvedSettingRow setting="Approvers" value="Any 1 of 2" source="group" rule="strictest_wins" />
        </ResolvedSettingsTable>
      </Section>
    </>
  );
}

export function DataGallery() {
  return (
    <>
      <Panel>
        <PanelHead title="Data table" as="h3" />
        <DataTable caption="Accounts, last 30 days" columns={accountColumns} data={accountRows} initialSorting={[{ id: "published", desc: true }]} />
      </Panel>
      <Panel>
        <PanelHead title="Permissions matrix" as="h3" />
        <DataTable caption="Role permissions" columns={permissionColumns} data={permissionRows} />
      </Panel>
      <Panel>
        <PanelHead title="Run steps" as="h3" />
        <Timeline items={runSteps} />
      </Panel>
      <Panel>
        <PanelHead title="Two-pane list" as="h3" />
        <TwoPaneList
          label="Replies"
          items={inboxItems}
          getKey={(m) => m.id}
          renderItem={(m) => (
            <>
              <span className="flex items-center gap-2">
                <AccountChip account={{ ...m.account, name: m.from }} />
                <span className="flex-1" />
                <Tag tone={m.kind.tone}>{m.kind.label}</Tag>
              </span>
              <span className="line-clamp-2 text-sm text-fg-muted">{m.body}</span>
              <span className="text-xs text-fg-subtle">
                {m.account.name} · {m.ago}
              </span>
            </>
          )}
          renderDetail={(m) => (
            <div className="grid gap-3 p-3.5">
              <AccountChip size="lg" account={{ ...m.account, name: m.from }} meta={`on ${m.account.name} · ${m.ago}`} />
              <p className="text-body">{m.body}</p>
            </div>
          )}
        />
      </Panel>
    </>
  );
}

export function Gallery() {
  return (
    <div className="grid gap-4">
      <h2 className="font-label text-caption font-semibold tracking-[0.12em] text-fg-subtle uppercase">Primitives</h2>
      <PrimitivesGallery />
      <h2 className="font-label text-caption font-semibold tracking-[0.12em] text-fg-subtle uppercase">Signature</h2>
      <SignatureGallery />
      <h2 className="font-label text-caption font-semibold tracking-[0.12em] text-fg-subtle uppercase">Data</h2>
      <DataGallery />
    </div>
  );
}
