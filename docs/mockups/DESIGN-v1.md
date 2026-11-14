---
name: Mera
description: An operations console for speaking as several identities at once — near-monochrome, hairline-ruled, dense enough to decide in seconds.
colors:
  bg: "#ffffff"
  bg-muted: "#fbfbfa"
  bg-sunken: "#f6f6f4"
  bg-raised: "#ffffff"
  bg-hover: "#f4f4f2"
  bg-active: "#eeeeec"
  bg-inverse: "#1a1a18"
  border: "#e7e7e4"
  border-strong: "#d9d9d5"
  fg: "#1a1a18"
  fg-muted: "#4e4e47"
  fg-subtle: "#656560"
  fg-inverse: "#fbfbfa"
  accent: "#1a1a18"
  accent-hover: "#333330"
  accent-fg: "#fbfbfa"
  focus: "#3d7dfc"
  focus-ring: "rgba(61, 125, 252, 0.35)"
  selection: "rgba(61, 125, 252, 0.18)"
  ok: "#2f7a4d"
  ok-bg: "#eef6f1"
  warn: "#87610f"
  warn-bg: "#fbf3e4"
  danger: "#a82c25"
  danger-bg: "#fbeeed"
  info: "#2b5fa8"
  info-bg: "#edf2fb"
  neutral: "#67675f"
  neutral-bg: "#f1f1ee"
  group-tech: "#4a6fa5"
  group-corp: "#7a5ba8"
  group-launch: "#a8733a"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Helvetica Neue, Arial, sans-serif"
    fontSize: "21px"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "-0.018em"
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Helvetica Neue, Arial, sans-serif"
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "-0.012em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Helvetica Neue, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "-0.005em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Helvetica Neue, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  small:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Helvetica Neue, Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
  micro:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Helvetica Neue, Arial, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
  control:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Helvetica Neue, Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 500
    lineHeight: 1
  field:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Helvetica Neue, Arial, sans-serif"
    fontSize: "13.5px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Helvetica Neue, Arial, sans-serif"
    fontSize: "11.5px"
    fontWeight: 600
    letterSpacing: "0.04em"
  label-column:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Helvetica Neue, Arial, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    letterSpacing: "0.03em"
  mono:
    fontFamily: "ui-monospace, SF Mono, JetBrains Mono, Cascadia Mono, Menlo, Consolas, monospace"
    fontSize: "0.95em"
    fontWeight: 400
    letterSpacing: "-0.01em"
rounded:
  sm: "4px"
  md: "6px"
  lg: "8px"
  pill: "9px"
  circle: "50%"
spacing:
  2xs: "4px"
  xs: "6px"
  sm: "8px"
  md: "10px"
  lg: "14px"
  xl: "18px"
components:
  button-secondary:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.fg}"
    typography: "{typography.control}"
    rounded: "{rounded.md}"
    padding: "0 11px"
    height: "30px"
  button-secondary-hover:
    backgroundColor: "{colors.bg-hover}"
    textColor: "{colors.fg}"
  button-secondary-active:
    backgroundColor: "{colors.bg-active}"
    textColor: "{colors.fg}"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-fg}"
    typography: "{typography.control}"
    rounded: "{rounded.md}"
    padding: "0 11px"
    height: "30px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
    textColor: "{colors.accent-fg}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.fg}"
    typography: "{typography.control}"
    rounded: "{rounded.md}"
    padding: "0 11px"
    height: "30px"
  button-danger:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.danger}"
    typography: "{typography.control}"
    rounded: "{rounded.md}"
    padding: "0 11px"
    height: "30px"
  button-sm:
    typography: "{typography.control}"
    rounded: "{rounded.md}"
    padding: "0 9px"
    height: "27px"
  button-icon:
    rounded: "{rounded.md}"
    padding: "0"
    width: "30px"
    height: "30px"
  input:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.fg}"
    typography: "{typography.field}"
    rounded: "{rounded.md}"
    padding: "5px 9px"
    height: "30px"
  panel:
    backgroundColor: "{colors.bg-raised}"
    textColor: "{colors.fg}"
    rounded: "{rounded.lg}"
    padding: "14px"
  panel-footer:
    backgroundColor: "{colors.bg-muted}"
    textColor: "{colors.fg-muted}"
    padding: "10px 14px"
  tag:
    backgroundColor: "{colors.neutral-bg}"
    textColor: "{colors.neutral}"
    typography: "{typography.micro}"
    rounded: "{rounded.sm}"
    padding: "0 8px"
    height: "22px"
  tag-outline:
    backgroundColor: "transparent"
    textColor: "{colors.fg-muted}"
    typography: "{typography.micro}"
    rounded: "{rounded.sm}"
    padding: "0 8px"
    height: "22px"
  chip:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.fg-muted}"
    typography: "{typography.control}"
    rounded: "{rounded.md}"
    padding: "0 10px"
    height: "27px"
  chip-pressed:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.fg}"
  nav-item:
    backgroundColor: "transparent"
    textColor: "{colors.fg-muted}"
    rounded: "{rounded.md}"
    padding: "6px 8px"
  nav-item-active:
    backgroundColor: "{colors.bg-active}"
    textColor: "{colors.fg}"
    rounded: "{rounded.md}"
    padding: "6px 8px"
  killswitch:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.danger}"
    rounded: "{rounded.md}"
    padding: "8px 10px"
  killswitch-hover:
    backgroundColor: "{colors.danger-bg}"
    textColor: "{colors.danger}"
  cmdk-trigger:
    backgroundColor: "{colors.bg-muted}"
    textColor: "{colors.fg-subtle}"
    typography: "{typography.small}"
    rounded: "{rounded.md}"
    padding: "0 8px 0 10px"
    height: "30px"
  kbd:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.fg-subtle}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "3px 5px"
  account-chip-mark:
    backgroundColor: "{colors.bg-sunken}"
    textColor: "{colors.fg-muted}"
    rounded: "{rounded.sm}"
    size: "20px"
  variant-row:
    backgroundColor: "{colors.bg-muted}"
    textColor: "{colors.fg-muted}"
    typography: "{typography.small}"
    padding: "9px 14px 9px 26px"
  proof-block:
    backgroundColor: "{colors.bg-muted}"
    textColor: "{colors.fg}"
    rounded: "{rounded.md}"
    padding: "7px 10px"
  table-header-cell:
    backgroundColor: "{colors.bg-muted}"
    textColor: "{colors.fg-subtle}"
    typography: "{typography.label-column}"
    padding: "8px 12px"
  table-cell:
    backgroundColor: "transparent"
    textColor: "{colors.fg}"
    typography: "{typography.small}"
    padding: "9px 12px"
---

# Design System: Mera

> **Archived: v1, "The Hairline Ledger".** This describes the original mockups in
> `docs/mockups/`. The current design system is v2, "The Dispatch Ledger", documented in
> the root `DESIGN.md` and built in `docs/mockups-v2/`.

## Overview

**Creative North Star: "The Hairline Ledger"**

Mera looks like a ledger ruled in hairlines, not a dashboard built from cards. Every
structural division — panel from page, row from row, account from account, column from
column — is a single 1px line in `border` (#e7e7e4). Nothing floats. Nothing is lifted
off the page to signal importance; importance is signalled by position, weight, and
label. The palette is warm-leaning near-monochrome, and the one near-black accent
(#1a1a18) is the same value as the primary text colour, so the primary button reads as
*ink*, not as a brand colour. Colour that is not a neutral appears only where it carries
meaning: a status, a semantic tint behind a tag, a group identity dot, a focus ring.

The density target is someone scanning, not studying. Rows are Raycast-compact — a 30px
control height, 9px table cells, a 13px row type size — while reading areas stay calm,
capped at 70–72ch with 1.5 line-height. The type ramp is deliberately short: 21px down to
11.5px across the whole product, with weight and colour doing the hierarchy work that
size would do in a marketing surface. A generic design detector reads this as
flat-type-hierarchy; it is an accepted, intentional trade for an operations console where
a 36px heading would push the queue below the fold.

What the system refuses is as load-bearing as what it does. It refuses the decorated-card
scaffold of same-size icon-and-heading tiles standing in for structure. It refuses the
dashboard reflex of collapsing five accounts into one summary row — the per-account
variant row is the signature structure, and the Dashboard leads with the approval queue
itself rather than a metric strip. It refuses gradients, glass, decorative shadow, emoji,
and multi-hue status rainbows. Light and dark are two complete token blocks (`:root` and
`[data-theme="dark"]`), neither derived from the other.

**Key Characteristics:**
- Hairline 1px rules as the only structural device; shadow exists for popovers alone.
- Near-monochrome warm greys; the accent is ink-black in light, paper-white in dark.
- Status is always colour **plus** dot shape **plus** text label — never colour alone.
- Monospace strictly reserved for machine data: hashes, versions, IDs, step numbers.
- A short type ramp (21/16/14/13/12/11.5px) with weight 500/550/600 carrying hierarchy.
- Every foreground/surface pair tested to WCAG AA 4.5:1 in both themes.
- Authored SVG icons on one 16px grid at 1.5px stroke; no icon font, no emoji.

## Colors

A warm-leaning near-monochrome field in which every non-neutral hue has a job.

### Primary

- **Ink** (`accent`, #1a1a18 light / #f2f2ef dark): The primary action colour, and
  simultaneously the primary text colour. The primary button is a filled block of body
  text ink with `accent-fg` (#fbfbfa) type on it. In dark the pair inverts exactly, so
  "primary" always means maximum contrast against the page rather than a hue. Hover
  lifts to `accent-hover` (#333330 light / #ffffff dark) — a value step, never a hue step.

### Secondary

- **Signal Blue** (`focus`, #3d7dfc light / #5b94ff dark): The only blue in the interface
  chrome, and it is reserved. It appears as the 2px `:focus-visible` outline, as the
  input border on focus with a 3px `focus-ring` halo, and as the text selection ground
  (`selection`). It is never used for emphasis, links, or branding. Hashtags in the
  composer preview borrow `info`, not `focus`, precisely to keep this reservation intact.

### Tertiary

- **Group Identity Trio** (`group-tech` #4a6fa5, `group-corp` #7a5ba8, `group-launch`
  #a8733a; lightened in dark to #7fa3d8 / #a98fd4 / #d4a06a): Three desaturated hues that
  identify an account group, carried **only** as an 8px rounded square `gdot` beside a
  text label, or as a 7px dot before a calendar event title. These are identity, not
  status, and they are the only hues in the system that do not map to a state.

### Neutral

- **Paper / Raised** (`bg`, `bg-raised`, #ffffff): The page ground and the panel ground.
  In light they are the same value, so a panel is distinguished from the page by its
  hairline border alone. In dark they diverge (#0e0e0d page, #1a1a18 panel) because a
  border alone is too weak to separate two near-blacks.
- **Chrome Grey** (`bg-muted`, #fbfbfa): The ground of the left rail, panel footers,
  filter bars, table headers, the command trigger, the proof block, and the per-account
  variant strip. It is the "this is secondary structure" ground.
- **Well** (`bg-sunken`, #f6f6f4): Recessed areas — media placeholders, the account chip
  mark, skeleton base.
- **Hover / Active** (`bg-hover` #f4f4f2, `bg-active` #eeeeec): The two interaction
  grounds. `bg-active` is also how the current nav item and the selected inbox message
  are marked — a ground change plus a weight change, never a coloured edge.
- **Rule** (`border` #e7e7e4) and **Strong Rule** (`border-strong` #d9d9d5): `border` is
  structure (panel edges, row dividers, table cell rules, calendar grid). `border-strong`
  is an affordance edge — it outlines anything you can click or type into (buttons,
  inputs, chips, the kill switch) and draws the scrollbar thumb.
- **Ink / Muted / Subtle** (`fg` #1a1a18, `fg-muted` #4e4e47, `fg-subtle` #656560): The
  three-step text ramp. `fg` is primary content, `fg-muted` is supporting prose and
  secondary rows, `fg-subtle` is metadata, column labels, placeholders, and timestamps.
  All three clear 4.5:1 on every surface token in both themes. Worst pairs: light
  `fg-subtle` on `bg-active` at 5.04:1; dark `fg-subtle` (#93938a) on `bg-active`
  (#262623) at 4.9:1. Dark `fg-subtle` was raised from #88887f, which measured 4.24:1 on
  `bg-active`. It failed on the selected inbox and composer rows, which is exactly the
  case The 4.5 Floor Rule exists to catch.

### Semantic status

- **Ok** (`ok` #2f7a4d / #6cbd8b), **Warn** (`warn` #87610f / #dcab57), **Danger**
  (`danger` #a82c25 / #e88077), **Info** (`info` #2b5fa8 / #86adea), **Neutral**
  (`neutral` #67675f / #a8a89f). Each has a paired tint (`*-bg`) used only as a tag or
  notice ground. The set is deliberately desaturated so that four statuses in one row
  read as a muted sequence rather than a rainbow. Every semantic foreground clears 4.5:1
  on its own tint (lowest: light `ok` on `ok-bg` at 4.76:1) and on every surface token.

### Named Rules

**The Meaning-Only Colour Rule.** Hue appears only where it carries information: status,
group identity, focus, or selection. There is no decorative colour, no brand gradient,
and no hue used purely to make a surface look less plain.

**The Two-Block Theme Rule.** Light and dark are two complete, hand-tuned token blocks,
not a filter and its inversion. Every new token must be authored in both `:root` and
`[data-theme="dark"]` before it ships. A token that exists in only one block is a bug.

**The 4.5 Floor Rule.** Any foreground token must clear 4.5:1 against every surface token
it can land on — not just against `bg`. Test the pair against `bg-active`, which is the
worst case in both themes, before adding a colour.

## Typography

**Interface Font:** the system UI stack (`-apple-system`, `BlinkMacSystemFont`,
`Segoe UI`, `system-ui`, `Helvetica Neue`, `Arial`, sans-serif).
**Machine-Data Font:** the system monospace stack (`ui-monospace`, `SF Mono`,
`JetBrains Mono`, `Cascadia Mono`, `Menlo`, `Consolas`, monospace).

**Character:** Deliberately unbranded. The interface face is whatever the operator's OS
already renders best, so the product reads as a native tool rather than a web page. The
monospace face is not a stylistic choice — it is a semantic one, marking the strings a
machine produced. There is no display or editorial face anywhere in the system.

### Hierarchy

- **Display** (600, 21px, 1.5, -0.018em): The page title in the top bar and page head.
  The largest type anywhere in the product.
- **Headline** (600, 16px, 1.5, -0.012em): Panel and section titles.
- **Title** (600, 14px, 1.5, -0.005em): Block headings inside panels and empty states;
  the same size as body, separated by weight alone.
- **Body** (400, 14px, 1.5): Default. Reading areas cap at 70ch (`.prose`) and page
  descriptions at 72ch.
- **Small** (400, 13px, 1.5): The true workhorse — table cells, variant rows, nav items,
  buttons, chips, inbox previews. Appears more than any other role in the built screens.
- **Micro** (400, 12px): Tags, captions, panel footers, chart axis text, timestamps.
- **Label** (600, 11.5px, 0.04em, uppercase): Rail group labels and proof-block keys.
- **Label-column** (600, 12px, 0.03em, uppercase): Table column headers.
- **Mono** (0.95em of its context, -0.01em): Hashes, version numbers, account IDs, step
  numbers, cost figures.

Weight is the primary hierarchy instrument: 400 body, 500 controls and account names,
550 for "slightly more than a row, less than a heading" (set titles, nav current item,
workspace name, field labels), 600 headings, 650 for the workspace mark monogram.

### Named Rules

**The Machine-Data Monospace Rule.** Monospace is reserved for strings a machine
generated or that a human must compare character by character: content hashes, version
numbers (`v1`), account and run IDs, step numbers, raw timestamps, costs. Prose, labels,
headings, and handles rendered as display names stay in the UI face. Monospace is never
used for emphasis or texture.

**The Tabular Figures Rule.** Any number compared down a column carries
`font-variant-numeric: tabular-nums` — applied globally to `.num`, `.mono`, table cells,
definition-list values, nav counts, calendar times, and character counters.

**The Tight Ramp Rule.** The whole product spans 21px to 11.5px. Hierarchy comes from
weight (400/500/550/600) and colour (`fg` / `fg-muted` / `fg-subtle`), not from size
jumps. A heading larger than 21px does not exist here; adding one would push the thing
the operator came to decide about below the fold. Automated heuristics flag this as a
flat type hierarchy — that finding is accepted and closed, not a defect to fix.

## Layout

A fixed two-column application shell: a 244px (`--rail-w`) sticky left rail and a
`minmax(0, 1fr)` main column, set with CSS Grid over a `100vh` minimum. The main column
carries a 52px (`--topbar-h`) sticky top bar at `z-index: 20` and a `18px`-padded page
body below it.

The rail is a three-part flex column: a bordered head holding the workspace switcher, a
scrolling nav body grouped Work / Accounts / System with uppercase group labels, and a
bordered foot pinning the kill switch. The top bar runs title → spacer → scope filters →
command trigger → theme toggle. Page heads put the h1 and its description left, and push
primary actions right with `margin-left: auto`; destructive actions never sit in that
cluster.

Inside the page, content composes from four grid primitives: `.stack` (14px vertical
rhythm), `.grid--2` / `.grid--3` (equal columns, 14px gutter), and `.split`
(content + a 320px aside; 380px in the wide variant, or a 300px leading column in
`--left`). Panel internals use 14px padding, 11px/14px for heads, 10px/14px for feet.

The spacing rhythm is a six-step scale — 4, 6, 8, 10, 14, 18px — with 4/6 for intra-
component gaps, 8 for row gaps, 10 for rail and compact padding, 14 for panel padding and
grid gutters, and 18 for the page frame. Off-scale values appear only as optical
corrections (the 26px left indent on a variant row that reads as an indent under its set
header; 9px/12px table cells; 13px set-head padding).

Three breakpoints, all content-driven:

- **1180px:** the splits collapse to one column, three-up grids become two-up, and the
  inbox list narrows from 350px to 290px.
- **900px:**
  - **Rail:** becomes a fixed off-canvas drawer (`translateX(-100%)`, revealed by
    `[data-open]` with the popover shadow and a hamburger toggle).
  - **Top bar:** compacts. The subtitle hides, and the command trigger collapses to a
    32px icon button.
  - **Rows and grids:** variant rows collapse from three columns to a stack. The inbox
    and left-split layouts go single-column. Panel heads, set heads and action rows
    wrap instead of overflowing.
  - **Tables:** cells stop wrapping and each table scrolls horizontally inside its own
    panel.
  - **Page and calendar:** page padding drops to 14px. The calendar switches to a
    horizontally scrollable 620px-minimum grid rather than compressing its columns.
- **560px:** the top bar's primary action keeps its accessible label but shows only its
  icon, the page title drops to 17px, and a post set's actions take a full row under its
  title.

### Named Rules

**The No Sideways Page Rule.** The page itself never scrolls horizontally, at any width
down to 390px. Wide content scrolls inside its own container instead.

- **Tables:** every `.table` is wrapped at runtime in a `.tablewrap`
  (`position: relative; overflow-x: auto`). The `position` matters: without it, an
  absolutely positioned `.sr` label inside a cell escapes the scroller and widens the
  page anyway.
- **Grid and flex children:** they get `min-width: 0`, because their default
  `min-width: auto` lets one unbroken line push a whole column past the viewport.
- **Testing:** every screen is checked at 1440, 1280, 1024, 768 and 390px.

**The Kill Switch Reachability Rule.** Stopping everything is pinned to the bottom of the
rail, inside the viewport without scrolling, on every screen. It is never placed in a
menu, a settings page, or adjacent to a primary action.

**The One Row Per Account Rule.** A post set renders one `.variant` row per target
account — account chip, its own text, its own status. Five accounts never collapse into
one summary row. Any aggregate count ("4 accounts", "Approve all 4") must sit beside the
expandable rows that prove it, not replace them. A bulk action counts only what it will
act on: when one of four accounts is already approved, the button reads "Approve
remaining 3", not "Approve all 4".

**The One Number Rule.** A count that appears on more than one screen is the same number
on every screen. Group membership, account totals and character counts come from one
source of demonstration data. The Tech News group is five accounts everywhere: Work
LinkedIn, Acme Page, X @main, X @tech and Acme FB. A post set that disables one of them
says "4 accounts" for that set, with the fifth shown as Off. A character count matches
the text it sits under, using the platform's own counting rule: on X a link counts as
23 characters.

## Elevation & Depth

This system is flat by construction. Depth is expressed by hairline rules and by a
four-step tonal ground ladder (`bg-sunken` → `bg` → `bg-muted` → `bg-raised`), not by
shadow. A panel sits on the page because it has a border and a radius, not because it
casts anything. In light, panel and page grounds are identical white and the border does
all the separating; in dark the grounds step apart because two near-blacks need tonal
help the border cannot give.

Exactly one shadow token exists, and it is reserved for things that float above the
document: the command palette box, the mobile rail drawer, and the calendar's reschedule
popover. The focus halo is a ring, not an elevation.

### Shadow Vocabulary

- **Popover** (`--shadow-popover`: `0 1px 2px rgba(26,26,24,.06), 0 8px 24px -6px
  rgba(26,26,24,.14)` light / `0 1px 2px rgba(0,0,0,.4), 0 8px 24px -6px rgba(0,0,0,.6)`
  dark): Real contact shadow plus real offset blur, for the command palette, the mobile
  rail drawer and the reschedule popover only.
- **Focus halo** (`0 0 0 3px var(--focus-ring)`): A ring on focused inputs, paired with a
  `focus`-coloured border. Not elevation — it never implies the field lifted.

### Named Rules

**The Hairline-Not-Shadow Rule.** Structure is drawn with 1px `border`. If a new
component needs separation from its neighbour, it gets a rule or a ground change. A
shadow is only permitted on something that genuinely overlays the page and can be
dismissed.

## Shapes

A tight, consistently squared-off radius family: 4px (`sm`) for small inlaid elements —
tags, kbd keys, account marks, segmented buttons, calendar events, group dots; 6px (`md`)
for interactive controls — buttons, inputs, chips, nav items, the proof block, notices;
8px (`lg`) for container surfaces — panels, the filter bar's top corners, the command
palette, the index grid. Nothing is pill-soft; the two full-round shapes in the system
are functional, not stylistic: the 7px status dot and the 18px-tall toggle switch with
its 14px knob.

Borders are the form language. Panels, inputs, buttons, chips, tags-outline, notices, and
calendar events are all defined by a 1px stroke — `border` where the job is structure,
`border-strong` where the job is "this is operable". Button groups negate the shared edge
with `margin-left: -1px` and square the interior corners so a group reads as one object.
The index grid achieves its hairline cell matrix with a 1px grid gap over a `border`
background rather than per-cell borders.

Icons are authored SVG on a single 16×16 grid, rendered at 16px default, 14px (`--sm`),
20px (`--lg`), and 26px (`--xl`), stroked at 1.5px (reduced to 1.25px at `--xl` to hold
optical weight), with round caps and joins and no fill. Platform marks are the one
exception: they are filled paths, because a brand mark traced in strokes stops reading as
itself. The sprite carries 56 symbols — 49 UI icons prefixed `i-`, 7 platform marks
prefixed `p-` — injected once per page and referenced by `<use href="#i-name">`.

### Named Rules

**The 16-Grid Icon Rule.** Every icon is authored on the 16px grid at 1.5px stroke and
added to the sprite. No icon fonts, no `<img>` icons, no emoji, no pasted icon-set SVGs
at a foreign stroke weight.

## Components

### Buttons

- **Shape:** Softly squared (6px), 30px tall, 11px horizontal padding, 6px icon-to-label
  gap; the small variant is 27px tall with 9px padding, and the icon-only variant is a
  30px square (26px when small).
- **Secondary (default `.btn`):** Page ground with a `border-strong` edge and `fg` text.
  This is the ordinary button; most actions in the product are this.
- **Primary:** Filled `accent` with `accent-fg` text and a matching border, so its height
  matches the secondary button exactly. One per action cluster, top-right of the work
  area.
- **Ghost:** Transparent border and ground; used for row-level affordances like the
  variant expand chevron.
- **Danger:** `danger` text on the page ground; on hover the ground fills `danger-bg` and
  the border takes `danger`. It is never filled at rest — a destructive action should not
  be the loudest thing on screen.
- **Hover / Active:** `bg-hover` then `bg-active` for neutral variants; `accent-hover` for
  primary. Transitions are 90ms ease-out on background and border-color only.
- **Disabled:** 0.55 opacity with `pointer-events: none`. This is the one sanctioned use
  of opacity, and it applies to a control that has been removed from the tab order — not
  to information.
- **Groups:** `.btngroup` joins buttons with a −1px margin, squaring the interior corners
  and rounding only the outer pair.

### Chips

- **Filter chip (`.chip`):** 27px tall, 6px radius, `border-strong` edge, `fg-muted`
  text on the page ground. Pressed state swaps the border to `fg`, the text to `fg`, and
  the weight to 550 — no fill, no coloured edge.
- **Tag (`.tag`):** 22px tall, 4px radius, filled with a semantic tint
  (`neutral-bg` by default, or `ok-bg` / `warn-bg` / `danger-bg` / `info-bg`) and its
  matching foreground. The outline variant is transparent with a `border-strong` edge
  and `fg-muted` text — used for machine scores like a 0.84 confidence value.

### Cards / Containers

- **Corner Style:** 8px.
- **Background:** `bg-raised`.
- **Shadow Strategy:** None. See Elevation & Depth.
- **Border:** 1px `border`, with internal 1px rules dividing head from body from foot.
- **Internal Padding:** 14px body, 11px/14px head, 10px/14px foot. The foot takes the
  `bg-muted` ground and inherits the container's bottom radius.

### Inputs / Fields

- **Style:** 1px `border-strong` edge, 6px radius, page ground, 13.5px text, 30px minimum
  height, 5px/9px padding. The textarea is 90px minimum, vertically resizable, 1.55
  line-height. The select suppresses the native appearance and draws its own chevron from
  two hard-stop gradient triangles.
- **Focus:** Border becomes `focus`, plus a 3px `focus-ring` halo. Native outline is
  suppressed only here, because the halo replaces it; everywhere else `:focus-visible`
  draws a 2px `focus` outline at 2px offset.
- **Field wrapper:** A 5px-gap grid of label (13px/550), control, and `fg-subtle` hint.
- **Toggle (`.switch`):** 30×18px, knob 14px, off state `border-strong`, on state `ok`,
  with a 120ms knob translation.
- **Segmented control (`.seg`):** A `bg-muted` tray with a 3px inset; the pressed segment
  lifts to `bg-raised` with its own border.

### Navigation

- **Rail items:** 13.5px, 6px radius, `fg-muted` text with `fg-subtle` icons, 6px/8px
  padding. Hover fills `bg-hover` and promotes text to `fg`. The current page
  (`aria-current="page"`) fills `bg-active`, promotes text and icon to `fg`, and raises
  weight to 550. Counts sit right-aligned in 12px `fg-subtle` tabular figures.
- **Group labels:** 11.5px uppercase 600 at 0.04em in `fg-subtle`.
- **Workspace switcher:** A full-width 6px-radius button with a 24px inverted-ground
  monogram mark, then two stacked lines: a 13px/550 name and a 12px `fg-subtle` meta line
  ("9 accounts · 3 groups"). Both are `display: block`. Left inline, they ran together
  as "Acme9 accounts". The meta line truncates with an ellipsis rather than wrapping.
- **Command trigger (`.cmdk`):** A 30px `bg-muted` field-shaped button carrying a search
  icon, a label, and a `kbd` chip showing the shortcut. Above 900px the shortcut is always
  visible, never hidden behind discovery. Below 900px, where there is no keyboard to
  advertise, it collapses to a 32px icon button.
- **Top bar title:** The h1 and its subtitle never wrap. They truncate with an ellipsis,
  so a long title can't push the search and action buttons off the bar.
- **Mobile:** The rail becomes an off-canvas drawer behind a hamburger toggle.
- **Selected item in a list of buttons:** Inbox messages, brand voices, groups, and the
  composer's account list mark the selected item with `aria-current="true"`. It is shown
  as the `bg-active` ground plus weight 600 on the name. `aria-selected` is reserved for
  elements with `role="option"` inside a listbox, as in the command palette. On a plain
  button it is invalid ARIA, and the accessibility scan flags it as critical.

### Status (signature)

`.status` is the system's most-used atom after the button. It is an inline flex of a 7px
dot and a text label, 12.5px/500, coloured by variant (`ok`, `warn`, `danger`, `info`,
`neutral` — the neutral variant deliberately uses `fg-subtle` rather than the `neutral`
token so an inert state recedes). The dot carries a second, non-colour channel through
its shape:

- **Solid** — a settled, complete state (Approved, Published, Connected).
- **Ring** (`--ring`, transparent with a 1.5px stroke) — an empty or not-yet-started
  state.
- **Half** (`--half`, a hard-stop 50% fill inside a 1.5px ring) — a partial or in-flight
  state (Pending, Expiring, 2 of 4 approved).

### Account chip (signature)

`.acct` is the recurring product atom: a 20px rounded-square mark carrying the platform
glyph on a `bg-sunken` ground with a `border` edge, followed by a 13px/500 display name
and optionally a 12.5px `fg-subtle` handle. The `--lg` variant scales the mark to 28px.
`.acctstack` overlaps marks at −5px with the raised ground as a separator, for the one
case where accounts legitimately compress into a glance.

### Variant row (signature structure)

`.variant` is the structure the whole product is organised around: a three-column grid of
a 186px account chip, a single-line truncated variant text, and a right-justified aside
holding the confidence tag, the stagger time, and the status. Rows sit on the `bg-muted`
strip inside a post set, divided by 1px rules, indented 26px from the set header, and
collapse to a stack below 900px. A disabled account (`--off`) stays fully readable:
`fg-subtle` text with a strikethrough and an explicit "Off" label, never dimmed.

### Approval proof block (signature)

`.proof` is a wrapping key/value strip on the `bg-muted` ground with a 6px radius, where
keys are 11.5px uppercase `fg-subtle` labels and values that identify bytes — version,
hash — are monospace. It exists to make "which version, which account, approved by whom"
legible in one glance, and it is the visual expression of the product's approval-integrity
guarantee.

### Tool-call approval block (signature)

This is the proof block's counterpart for an external MCP tool call that is waiting for a
human. It appears in the Approval Queue aside ("Tool call waiting") and as the Telegram
card preview on Approval Channels.

- **Who and what:** a one-line statement of the request ("**Analytics agent** wants to
  call `notion__create_page`"). Below it, a `fg-subtle` micro line gives the server,
  risk level, run and request time.
- **The arguments:** they sit in a `.preview` block under a `fg-subtle` head reading
  "Arguments, exactly as they will be sent". The body is monospace, one `key: value` per
  line, with no column alignment padding, because padding breaks badly when it wraps in
  a 320px aside. Long values are cut with "…" and a "N chars · show all" line. They are
  never summarized, since the approval binds to these exact bytes.
- **Actions:** "Allow once" (primary) and "Deny" sit together. "Block this tool"
  (danger) is pushed to the far end by a spacer, so it never sits next to the primary
  action.
- **Caption:** states what invalidates the approval (changed arguments, a redefined
  tool) and that expiry never makes the call.
- **Bot previews are pictures:** in a Telegram card preview the buttons are `span.btn`,
  not `<button>`. They must not be focusable or announced as controls that do nothing.

### MCP risk and review markers

- **Risk tags:** `Read` uses `tag--outline`. `Write` uses `tag--warn`. `Destructive`
  uses `tag--danger`. The approval policy beside each is plain text ("Runs without
  asking", "Ask in Telegram every time", "Blocked"), not another tag. Two tags in a row
  would compete.
- **Registration status:** this reuses `.status`. `Active` is a solid `ok` dot.
  `1 tool changed` is a half `warn` dot. `Pending review` is a ring `neutral` dot, empty
  because nothing is enabled yet.
- **Machine strings:** the protocol version (`2026-07-28`, `2025-11-25 · legacy`) and
  the name agents see (`notion__search`) are monospace under the human label. Both are
  machine strings an operator compares character by character.

### Reschedule popover

This is the keyboard equivalent of dragging on the calendar, and the reason drag is never
the only way to move a post.

- **Opening:** selecting a calendar item (click or Enter) opens a 280px `bg-raised`
  popover with the popover shadow, anchored under the item. It flips above the item when
  there isn't room below.
- **Contents:** the item's name and current time, a Day select, a Time input, a caption
  saying that moving keeps the approval, and Cancel / Move.
- **Behaviour:** nothing changes until Move is pressed. Escape, Cancel, or a click
  outside closes it and returns focus to the item that opened it. Focus starts on the Day
  select. It is `role="dialog"`, labelled by its title, and not modal: the calendar
  behind it stays readable.

### States

- **Empty:** A centred grid with a `fg-subtle` icon, a 14px title, and a 13px `fg-muted`
  line capped at 46ch, in 44px/24px padding.
- **Loading:** `.skel` bars — a 10px-tall 4px-radius sheen animating 1.4s between
  `bg-sunken` and `bg-hover`. Disabled entirely under `prefers-reduced-motion`.
- **Notice:** A 6px-radius bordered block on `bg-muted`; the warn/danger/info variants
  take the semantic tint as ground and a `color-mix` 28% semantic border.

### Data display

- **Tables:** 13px body, uppercase 12px `fg-subtle` headers on a `bg-muted` ground, 9px/
  12px cells, 1px `border` row rules with the last row's rule suppressed, hover on
  `bg-hover`, and 14px outer padding on the first and last columns. Permission ticks are
  `::after` pseudo-content with the accessible name carried in an `.sr` span, because a
  bare glyph announces as nothing.
  - **Width:** every table lives in a `.tablewrap` (added by `mera.js`, never hand-written).
    Above 900px cells wrap normally and the table fits its panel. Below 900px cells hold
    one line (`white-space: nowrap`) and the table scrolls sideways inside the panel.
  - **Column budget:** six columns is the practical maximum inside a `.split` main
    column. Past that, fold a secondary value under its primary as a `fg-subtle` micro
    line, rather than letting the table scroll at desktop width. The MCP server table
    does this: the protocol version sits under the transport.
- **Charts:** Authored inline SVG using the text colours as series colours — `fg` solid
  for series A, `fg-subtle` dashed for series B, both at 1.75px — with `border-strong`
  axes, dashed `border` grid lines, a 12px `fg-subtle` type size, an explicit legend, and
  a caption. Charts are monochrome by default; they do not introduce a palette.
- **Timeline (`.tl`):** A four-column row grid with a monospace 11.5px right-aligned step
  number, a 132px timestamp column, content, and an aside.

### Motion

Transitions are short and functional: 90ms ease-out for button grounds and borders, 120ms
for the switch, 180ms `cubic-bezier(.2,.8,.3,1)` for the rail drawer. The only looping
animation is the skeleton shimmer. A global `prefers-reduced-motion` block collapses every
animation and transition to 0.01ms.

### Browser surfaces

Scrollbars are 10px with a `border-strong` thumb inset 3px against the page ground,
darkening to `fg-subtle` on hover; `::selection` uses the `selection` tint with `fg` text;
`color-scheme` is declared per theme so native form controls and scrollbars match. The
parts the system did not draw still carry the design.

## Do's and Don'ts

### Do:

- **Do** build structure from 1px `border` rules and ground changes. A new container gets
  a hairline and an 8px radius, not a shadow.
- **Do** pair every status colour with a dot shape (solid / ring / half) **and** a text
  label. The three-signal rule is non-negotiable; it is how the AA commitment survives
  a colour-blind operator.
- **Do** render one row per target account whenever a post set, an outcome, or a metric
  spans accounts.
- **Do** use monospace only for machine data — hashes, versions, IDs, step numbers, costs
  — and tabular figures for any number compared down a column.
- **Do** author both theme blocks for any new token, and verify the foreground clears
  4.5:1 against `bg-active`, the worst-case surface, in both.
- **Do** keep the type ramp inside 21px–11.5px and express hierarchy with weight
  (500/550/600) and the three-step text colour ramp.
- **Do** keep the kill switch pinned to the rail foot, reachable without scrolling.
- **Do** draw new icons on the 16px grid at 1.5px stroke and add them to the sprite.
- **Do** state a limitation in words — "not reported", "LinkedIn does not support
  editing" — rather than hiding the affordance.
- **Do** give every drag interaction a keyboard path that does the same job: the
  calendar's reschedule popover is the pattern. A shortcut hint in copy must be wired
  before it ships.
- **Do** let wide content scroll inside its own container. Check every screen at 1440,
  1024 and 390px for a sideways-scrolling page before calling it done.
- **Do** show external tool arguments verbatim wherever a human is asked to approve
  them.

### Don't:

- **Don't** mark identity or selection with a coloured border-left above 1px. A tinted
  edge above a hairline reads as decoration and fails at a glance anyway. Group identity
  rides on the `gdot` or the calendar event's leading dot; selection is a ground change
  plus a weight change.
- **Don't** dim information with opacity. Disabled accounts, expired items, and inactive
  rows use `fg-subtle`, a strikethrough, and an explicit label — opacity would drop text
  under the contrast floor. The only sanctioned opacity is on a control already removed
  from the tab order (`[disabled]` at 0.55) and on a decorative platform mark (0.6).
- **Don't** add a second accent, a brand hue, or a gradient. The accent is ink; blue is
  reserved for focus and selection.
- **Don't** put a shadow on anything that does not overlay the page and cannot be
  dismissed.
- **Don't** collapse several accounts into one summary row, and don't lead an operational
  screen with a metric strip when there is a queue to act on.
- **Don't** build a wall of same-size icon-and-heading tiles standing in for structure.
- **Don't** use pill radii on containers or controls; the family stops at 8px.
- **Don't** use emoji, icon fonts, or imported icons at a foreign stroke weight.
- **Don't** place a destructive action adjacent to the primary action in a page-head
  cluster.
- **Don't** put `aria-selected` on a `<button>`. A selected item in a list of buttons is
  `aria-current="true"`; `aria-selected` belongs to `role="option"`.
- **Don't** let two screens disagree about a count, or show a character count that does
  not match the text beside it.
- **Don't** render a picture of a control (a bot card preview) with real `<button>`
  elements.
