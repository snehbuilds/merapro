# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

For this design work: **standalone HTML/CSS mockups** under `docs/mockups/`, the user's
explicit choice, because no application scaffold exists yet (the repository is
documentation only).

The product's committed implementation stack, recorded in
[docs/01-architecture.md](docs/01-architecture.md) section 7.6 and
[ADR-0001](docs/adr/0001-python-fastapi-backend.md): Next.js + TypeScript + Tailwind +
shadcn/ui, consuming a generated TypeScript SDK; FastAPI backend. The user asked for a
bespoke premium design system built on Tailwind + shadcn/ui rather than an off-the-shelf
one, so the mockups must be portable into that stack.

## Users

**Primary: the operator.** One person responsible for a professional social presence
across several accounts and several platforms at once. They are not a social media
manager by title; posting is one of many things they do, and the time they can give it is
measured in minutes per day. The defining situation is **approving from a phone** — the
product's whole design assumes decisions get made in ten seconds, in a messaging app,
between other tasks.

**Secondary: the agency or team operator.** The same person running several client
workspaces, where accounts, content, and audit trails must not leak between clients, and
where a client stakeholder may be scoped to only their own brand's queue.

**Roles within a workspace:** Owner, Admin, Editor, Approver, Viewer, ordered
`viewer < approver < editor < admin < owner`. Approver deliberately sits *below* Editor:
a reviewing stakeholder who approves but never drafts. Any role can be narrowed to named
account groups or accounts.

## Product Purpose

Mera closes the loop between "something newsworthy happened in my field" and "a good post
about it went out on my accounts, with my approval, at a sensible time."

It discovers content from configured sources, drafts a **separate post per target
account**, routes every post to a human for approval through a Telegram or WhatsApp bot,
then publishes and schedules across accounts, manages comment engagement, and reports on
performance.

Success is that the operator trusts it enough to leave it running, and can prove
afterwards exactly what was approved, by whom, on which account, against which text.

## Positioning

Two mechanisms a neighbouring product could not truthfully copy without rebuilding its
data model.

**Approval integrity.** Nothing publishes without an approval bound to an immutable
content version *and* its target account, via a content hash that includes
`social_account_id`. Edit the post and the approval is dead. A version approved for one
account is structurally incapable of publishing to another. Expiry is terminal and never
publishes; there is no timeout-to-publish setting, deliberately.

**Per-account authorship.** One story becomes a *post set* with one uniquely written
variant per account — not one body cross-posted. Two X accounts in a group receive
different text, checked against each other by a cross-account duplicate guardrail and
published minutes apart. This is a platform-compliance requirement, not a preference: X
suspends accounts for posting substantially similar content.

## Operating Context

- **The phone is the primary approval surface.** Telegram first, WhatsApp second. The web
  UI is where complex work happens; the bot is where decisions happen.
- **Several accounts per platform is the normal case.** The reference shape is a group
  called "Tech News" holding five accounts: LinkedIn personal, LinkedIn company page,
  X `@main`, X `@tech`, and a Facebook Page.
- **Accounts are organized into groups** that mix platforms. An account may belong to
  several groups. Settings resolve post → account → group → workspace, except approval
  policy and autopilot mode, which resolve to the strictest applicable value.
- **Partial outcomes are normal, not errors.** A post set routinely ends
  `partially_approved` or `partially_published`. The UI must treat these as ordinary
  terminal states.
- **Work is scoped to a workspace at all times**, with a switcher; nothing crosses that
  boundary in any surface.
- Self-hosted, single machine or small VM, Docker Compose. Not a hosted SaaS.

## Capabilities and Constraints

Confirmed, from the committed documentation set:

- **Sources:** RSS/Atom, news APIs, Google News, Reddit, YouTube, Hacker News, email
  ingest, external MCP servers. Scored per account group against that group's pillars.
- **Drafting:** post set → one variant per account → immutable versions. Media uploaded or
  generated (image and video). Hashtags proposed per account with declared evidence basis
  and sample size.
- **Approval:** whole-set or per-account decisions, quorum, expiry, escalation, quiet
  hours, digests. Signed single-use tokens scoped to exact variants.
- **Publishing:** adapter per platform, staggered within a group, per-account retry and
  dead-lettering.
- **Engagement:** unified inbox across accounts; replies always sent as the account that
  owns the post — there is no code path that selects a different one.
- **Analytics:** post → post set → account → group → platform → workspace.
- **Automation:** Manual, Assisted, Full Autopilot, set per workspace, group, or account;
  kill switch at all three levels.
- **Eleven agents** with per-account fan-out, full audit trail, and per-step cost.

Constraints that shape the interface:

- **Official platform APIs only.** Where a platform cannot do something, the UI says so
  rather than hiding it. Capability descriptors drive affordances; a greyed Edit button is
  an adapter reporting `supports_edit: false`, never a hardcoded check.
- **Platform honesty about metrics.** LinkedIn personal profiles expose almost no
  analytics. Absent metrics render as "not reported", never as zero.
- **Terminology is fixed** by the glossary in [docs/00-overview.md](docs/00-overview.md)
  section 4: workspace, account group, post set, post variant, post version, composite
  hash, stagger, approval request, kill switch.

**Explicitly undecided:** the operator's content pillars and brand voice examples (open
question Q4); how many accounts share a platform within one group (Q8); image and video
generation provider (Q9); which external MCP servers will be registered (Q10).

## Brand Commitments

- **Name:** Mera. No logo, wordmark, or typeface asset exists in the repository.
- **Voice**, evidenced consistently across the documentation set: direct, specific, and
  willing to state costs and limitations plainly. It names what will not work and why.
  Interface copy should inherit this — no cheerful filler, no hiding a limitation behind a
  friendly phrase.
- **Visual direction, standing: the category standard, played straight.** Offered a
  choice between three distinctive worlds and the conventional path, the user chose the
  convention. Convention is therefore the commitment: execute it at full fidelity, with no
  irony and no smuggled quirk. Distinctiveness comes from precision and restraint, not
  from a borrowed world.
- **Craft bar, named by the user: Notion, Raycast, Resend.** Their shared discipline is
  the specification:
  - near-monochrome neutral palette, one restrained accent, colour reserved for meaning;
  - **hairline borders as the primary structure**, not drop-shadowed cards everywhere;
  - typographic calm and generous rhythm (Notion) held against compact, scannable rows
    where data is dense (Raycast);
  - **monospace for machine data** — IDs, content hashes, version numbers, timestamps,
    handles (Resend);
  - visible keyboard affordances and a command surface (Raycast);
  - light and dark both first-class, neither an afterthought;
  - no gradients, no glassmorphism, no decorative shadows, no multi-hue status rainbows.

## Evidence on Hand

- A complete architecture documentation set: `docs/00-overview.md` through
  `docs/11-phase1-plan.md`, `docs/api/openapi-v1-outline.yaml`, and twelve ADRs. These are
  the authority for entities, states, flows, and screen inventory.
- **Screen inventory** is fixed by [docs/00-overview.md](docs/00-overview.md) and the
  brief's section 7: Dashboard, Sources, Content Discovery/Trending, Composer/Draft
  Studio, Approval Queue, Content Calendar, Unified Inbox, Analytics, Agents and run
  viewer, MCP Integrations, Connected Accounts, Account Groups, Approval Channels, Brand
  Voice, Automation/Autopilot, Team & Roles, Audit Log, Settings.
- **Realistic demonstration content exists in the docs** and should be reused rather than
  invented: the PostgreSQL 18 release as the worked example story, the Tech News group and
  its five accounts, confidence scores of 0.84 / 0.91 / 0.77 / 0.73, a stagger spanning
  09:00 to 09:13, the approval card layouts in
  [docs/07-approval-bots.md](docs/07-approval-bots.md) section 3.
- **No real brand assets, no real customers, no pricing, no benchmarks exist.** Any such
  content in a mockup is synthetic and must be labelled or avoided. Account handles,
  metrics, and post text in mockups are demonstration data.
- **Accessibility target is committed:** WCAG 2.1 AA, with an axe scan passing with no
  serious or critical violations as a Phase 1 acceptance criterion.

## Product Principles

1. **The target account is the unit of everything.** Drafting, approval, scheduling,
   publishing, failure, and measurement all happen per account. Any screen that collapses
   five accounts into one row hides the thing the operator is deciding about.
2. **Approval means specific bytes on a specific account.** Every surface that shows an
   approval must make the version and the destination unambiguous.
3. **Partial is normal.** Two of four approved, three of four published, one retrying —
   these are states to render clearly, never errors to apologize for.
4. **State limitations rather than hiding them.** "Not reported" beats a zero; "LinkedIn
   does not support editing published posts" beats a missing button.
5. **Decisions happen in seconds.** The density target is set by someone scanning, not
   studying. Scanability outranks expression on every operational screen.

## Accessibility & Inclusion

WCAG 2.1 AA, committed in the brief and in
[docs/11-phase1-plan.md](docs/11-phase1-plan.md) milestone M9. Specific consequences for
this design work:

- Status must never be carried by colour alone — account health, approval state, and
  publish outcome all need a shape, glyph, or label alongside any colour.
- Full keyboard operation including the composer's per-account controls and the calendar's
  drag-and-drop, which needs a keyboard-reachable equivalent.
- Visible focus states throughout; contrast holding in both light and dark themes, both of
  which are committed.
