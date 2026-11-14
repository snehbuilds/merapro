---
name: Mera
description: A dispatch ledger for speaking as several identities at once — warm paper, black ink, one verdigris action colour, an always-dark control rail.
colors:
  bg: "#f5f3ed"
  bg-muted: "#efece4"
  bg-sunken: "#e9e5db"
  bg-raised: "#fffefa"
  bg-hover: "#f1eee6"
  bg-active: "#e7e3d8"
  bg-inverse: "#17181a"
  border: "#e0dbcf"
  border-strong: "#cbc5b7"
  fg: "#17181a"
  fg-muted: "#48473f"
  fg-subtle: "#64625a"
  fg-inverse: "#f5f3ed"
  accent: "#0b6b5e"
  accent-hover: "#08594e"
  accent-fg: "#ffffff"
  accent-soft: "#dcece7"
  focus: "#0b6b5e"
  focus-ring: "rgba(11, 107, 94, 0.28)"
  selection: "rgba(11, 107, 94, 0.18)"
  ok: "#22704a"
  ok-bg: "#e3f0e7"
  warn: "#865600"
  warn-bg: "#f8ecd2"
  danger: "#ad2a1f"
  danger-bg: "#f8e5e1"
  info: "#24549e"
  info-bg: "#e3eaf6"
  neutral: "#5d5b53"
  neutral-bg: "#e9e5db"
  rail-bg: "#15171a"
  rail-fg: "#cfcbc1"
  rail-subtle: "#8f8b81"
  rail-hover: "#1f2226"
  rail-active: "#2a2e33"
  rail-border: "#26292d"
  rail-hi: "#ffffff"
  group-tech: "#3d6596"
  group-corp: "#7b4f93"
  group-launch: "#a2661f"
typography:
  display:
    fontFamily: "IBM Plex Sans, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Arial, sans-serif"
    fontSize: "20px"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.015em"
  topbar-title:
    fontFamily: "IBM Plex Sans, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Arial, sans-serif"
    fontSize: "18px"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.015em"
  headline:
    fontFamily: "IBM Plex Sans, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Arial, sans-serif"
    fontSize: "15px"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "-0.008em"
  title:
    fontFamily: "IBM Plex Sans, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Arial, sans-serif"
    fontSize: "14.5px"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "-0.006em"
  body:
    fontFamily: "IBM Plex Sans, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  small:
    fontFamily: "IBM Plex Sans, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
  micro:
    fontFamily: "IBM Plex Sans, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Arial, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
  control:
    fontFamily: "IBM Plex Sans, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 500
    lineHeight: 1
  field:
    fontFamily: "IBM Plex Sans, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Arial, sans-serif"
    fontSize: "13.5px"
    fontWeight: 400
    lineHeight: 1.5
  label-panel:
    fontFamily: "IBM Plex Sans Condensed, IBM Plex Sans, Arial Narrow, Segoe UI, system-ui, sans-serif"
    fontSize: "12.5px"
    fontWeight: 600
    letterSpacing: "0.09em"
  label-column:
    fontFamily: "IBM Plex Sans Condensed, IBM Plex Sans, Arial Narrow, Segoe UI, system-ui, sans-serif"
    fontSize: "11.5px"
    fontWeight: 600
    letterSpacing: "0.09em"
  label-rail:
    fontFamily: "IBM Plex Sans Condensed, IBM Plex Sans, Arial Narrow, Segoe UI, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    letterSpacing: "0.12em"
  brand:
    fontFamily: "IBM Plex Sans Condensed, IBM Plex Sans, Arial Narrow, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 600
    letterSpacing: "0.14em"
  mono:
    fontFamily: "IBM Plex Mono, ui-monospace, SF Mono, Cascadia Mono, Menlo, Consolas, monospace"
    fontSize: "0.92em"
    fontWeight: 400
    letterSpacing: "-0.01em"
rounded:
  sm: "3px"
  md: "4px"
  lg: "6px"
  chip: "14px"
  count: "9px"
  circle: "50%"
spacing:
  2xs: "4px"
  xs: "6px"
  sm: "8px"
  md: "10px"
  lg: "14px"
  xl: "16px"
  2xl: "22px"
components:
  button-secondary:
    backgroundColor: "{colors.bg-raised}"
    textColor: "{colors.fg}"
    typography: "{typography.control}"
    rounded: "{rounded.md}"
    padding: "0 12px"
    height: "32px"
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
    padding: "0 12px"
    height: "32px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
    textColor: "{colors.accent-fg}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.fg}"
    typography: "{typography.control}"
    rounded: "{rounded.md}"
    padding: "0 12px"
    height: "32px"
  button-danger:
    backgroundColor: "{colors.bg-raised}"
    textColor: "{colors.danger}"
    typography: "{typography.control}"
    rounded: "{rounded.md}"
    padding: "0 12px"
    height: "32px"
  button-sm:
    typography: "{typography.control}"
    rounded: "{rounded.md}"
    padding: "0 10px"
    height: "28px"
  button-icon:
    rounded: "{rounded.md}"
    padding: "0"
    width: "32px"
    height: "32px"
  input:
    backgroundColor: "{colors.bg-raised}"
    textColor: "{colors.fg}"
    typography: "{typography.field}"
    rounded: "{rounded.md}"
    padding: "5px 10px"
    height: "32px"
  panel:
    backgroundColor: "{colors.bg-raised}"
    textColor: "{colors.fg}"
    rounded: "{rounded.lg}"
    padding: "14px"
  panel-footer:
    backgroundColor: "transparent"
    textColor: "{colors.fg-muted}"
    padding: "10px 14px"
  tag:
    backgroundColor: "{colors.neutral-bg}"
    textColor: "{colors.neutral}"
    typography: "{typography.micro}"
    rounded: "{rounded.sm}"
    padding: "0 7px"
    height: "22px"
  tag-outline:
    backgroundColor: "transparent"
    textColor: "{colors.fg-muted}"
    typography: "{typography.mono}"
    rounded: "{rounded.sm}"
    padding: "0 7px"
    height: "22px"
  chip:
    backgroundColor: "{colors.bg-raised}"
    textColor: "{colors.fg-muted}"
    typography: "{typography.control}"
    rounded: "{rounded.chip}"
    padding: "0 10px"
    height: "28px"
  chip-pressed:
    backgroundColor: "{colors.bg-inverse}"
    textColor: "{colors.fg-inverse}"
  nav-item:
    backgroundColor: "transparent"
    textColor: "{colors.rail-fg}"
    rounded: "{rounded.md}"
    padding: "0 8px"
    height: "29px"
  nav-item-active:
    backgroundColor: "{colors.rail-active}"
    textColor: "{colors.rail-hi}"
    rounded: "{rounded.md}"
    padding: "0 8px"
    height: "29px"
  killswitch:
    backgroundColor: "#2a1311"
    textColor: "#ffb4a8"
    typography: "{typography.label-panel}"
    rounded: "{rounded.md}"
    padding: "0 11px"
    height: "36px"
  killswitch-hover:
    backgroundColor: "#3a1814"
    textColor: "#ffd2ca"
  cmdk-trigger:
    backgroundColor: "{colors.bg-raised}"
    textColor: "{colors.fg-subtle}"
    typography: "{typography.small}"
    rounded: "{rounded.md}"
    padding: "0 7px 0 10px"
    height: "32px"
    width: "240px"
  kbd:
    backgroundColor: "{colors.bg-raised}"
    textColor: "{colors.fg-muted}"
    typography: "{typography.mono}"
    rounded: "{rounded.sm}"
    padding: "3px 4px"
  account-chip-mark:
    backgroundColor: "{colors.bg-inverse}"
    textColor: "{colors.fg-inverse}"
    rounded: "{rounded.circle}"
    size: "22px"
  variant-row:
    backgroundColor: "transparent"
    textColor: "{colors.fg-muted}"
    typography: "{typography.small}"
    rounded: "{rounded.md}"
    padding: "8px 10px 8px 22px"
  proof-block:
    backgroundColor: "{colors.bg-muted}"
    textColor: "{colors.fg}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  table-header-cell:
    backgroundColor: "transparent"
    textColor: "{colors.fg-subtle}"
    typography: "{typography.label-column}"
    padding: "9px 12px"
  table-cell:
    backgroundColor: "transparent"
    textColor: "{colors.fg}"
    typography: "{typography.small}"
    padding: "10px 12px"
---

# Design System: Mera

> **Current: v2, "The Dispatch Ledger".** Built in `docs/mockups-v2/`, and implemented
> by `docs/mockups-v2/assets/mera.css`, which is the source of truth when this document
> and the code disagree. The previous system, v1 "The Hairline Ledger", is archived at
> `docs/mockups/DESIGN-v1.md` with the original mockups. Both mockup sets carry identical
> screen content. Only the design system differs.

## Overview

**Creative North Star: "The Dispatch Ledger"**

Mera looks like a dispatch office's ledger. Warm paper, black ink, and a single verdigris
stamp for the action that moves things along. The work sits on sheets laid on paper:
raised panels (`bg-raised`, #fffefa) on a warm page (`bg`, #f5f3ed). Panel titles, column
heads and rail labels are set in condensed, tracked capitals, like the printed headings
on a form. The person filling the form writes in a plain sans, and the machine stamps its
data in monospace: hashes, versions, scores, handles, times and costs.

Two things make it recognizable at a glance:

- **Every post set draws a branch.** A thin vertical rule hangs from the set's title, and
  each per-account variant ticks off it. "One story, one post per account" is visible as
  structure before a word is read.
- **Approval proof is a perforated receipt.** It sits on a dashed border in monospace.
  Panel footers use the same dashed rule, as if each sheet can be torn off at the bottom.

The shell is split by surface. The **control rail** is always dark, in both themes. It
holds navigation, the workspace switcher, the signed-in person and the kill switch. The
work happens on paper to its right. Stopping everything lives on a different surface from
doing anything, so it can't be mistaken for an ordinary action.

The density target is an operator scanning, not studying: 32px controls, 10px table
cells, 13px row text. Reading areas stay calm at a 70ch measure.

**Key Characteristics:**
- Warm paper and black ink; one verdigris action colour (#0b6b5e light / #4cc4ae dark).
- An always-dark control rail, with the kill switch alone at its foot.
- Three typefaces with three jobs: IBM Plex Sans for reading and writing, IBM Plex Sans
  Condensed in tracked capitals for headings that name a field, IBM Plex Mono for
  anything a machine produced.
- Post sets drawn as a branch to their per-account variants.
- Dashed rules for things that can be torn off: proof receipts, panel footers, media
  drop areas. Dotted rules for definition lists.
- Status is a coloured dot shape **plus** a label. Danger also changes the dot to a
  diamond.
- Every text pair tested to WCAG AA 4.5:1 in both themes.

## Colors

Warm neutrals do the structural work; a single cool green does the acting.

### Primary

- **Verdigris** (`accent`, #0b6b5e light / #4cc4ae dark): The one action colour. It fills
  the primary button, the "on" state of a switch, chart series A and bars, the progress
  bar, link underlines on hover, and the brand diamond. It is also the focus colour
  (`focus`) and the selection tint, so "this is where you are" and "this is what will
  happen" share one hue. It is a blue-leaning green chosen to read as "go" without
  borrowing status green (`ok`, #22704a). The two must never be swapped. Hover steps
  darker in light (#08594e) and lighter in dark (#6fd3c0).
- **Verdigris Soft** (`accent-soft`, #dcece7 / #13302b): The selected-row ground in the
  inbox and the hover ground on index cards. A selection, not a status.

### Neutral: paper and ink

- **Paper** (`bg`, #f5f3ed / #111315): The page.
- **Sheet** (`bg-raised`, #fffefa / #181b1e): Panels, inputs, buttons, the command
  trigger. A sheet is lighter than the paper in light and lighter than the ink page in
  dark, so a panel always reads as laid on top.
- **Muted / Sunken** (`bg-muted` #efece4 / #15181a, `bg-sunken` #e9e5db / #0c0e10):
  The proof receipt ground; the segmented-control tray, media drop areas, skeletons.
- **Hover / Active** (`bg-hover` #f1eee6 / #1e2125, `bg-active` #e7e3d8 / #272b30):
  Interaction grounds. `bg-active` is also the empty track of a progress bar.
- **Inverse** (`bg-inverse` #17181a / #ebe8e1): The account-chip mark, the pressed filter
  chip, the workspace monogram outside the rail.
- **Rules** (`border` #e0dbcf / #262a2e, `border-strong` #cbc5b7 / #363b41): `border` divides
  rows and panels. `border-strong` outlines anything operable, draws table head
  underlines, the variant branch, and every dashed or dotted rule.
- **Ink ramp** (`fg` #17181a / #ebe8e1, `fg-muted` #48473f / #b7b3a9, `fg-subtle` #64625a
  / #9d998f): Primary content, supporting text, metadata. Worst measured pairs, both on
  `bg-active`: light `fg-subtle` 4.77:1, dark `fg-subtle` 5.01:1.

### Rail

A separate dark palette that does not change with the theme, except to get darker:

- `rail-bg` #15171a (dark theme #0b0d0f), `rail-hover` #1f2226 (#16191c), `rail-active`
  #2a2e33 (#1f2327), `rail-border` #26292d (#1d2023).
- `rail-fg` #cfcbc1 (11.1:1), `rail-subtle` #8f8b81 (5.3:1), `rail-hi` #ffffff.
- The rail sets its own `--focus: #6fd3c0`, because verdigris #0b6b5e would vanish on
  near-black.
- `rail-subtle` is for labels and icons on `rail-bg` only. On `rail-active` it measures
  4.0:1, so the current nav item promotes its icon to `rail-hi`.

### Semantic status

**Ok** (#22704a / #6cc391), **Warn** (#865600 / #e0ad55), **Danger** (#ad2a1f / #ee8678),
**Info** (#24549e / #8db2ec), **Neutral** (#5d5b53 / #aaa69c), each with a `*-bg` tint for
tags and notices. Every foreground clears 4.5:1 on its own tint and on every surface (lowest:
light `ok` on `bg-active`, 4.70:1).

### Kill switch palette

The kill switch uses fixed values, not tokens, because it lives on the always-dark rail in
both themes: ground #2a1311, border #7a2a22, text #ffb4a8 (10.3:1); hover ground #3a1814,
border #ee8678, text #ffd2ca.

### Group identity

`group-tech` #3d6596, `group-corp` #7b4f93, `group-launch` #a2661f (dark: #7fa6d9 /
#b593cf / #d9a05c). Identity, not status. Carried only as an 8px square mark with 1px
corners (`gdot`), or as the 7px square before a calendar item.

### Named Rules

**The One Stamp Rule.** Verdigris means "act here" or "you are here". It is never used for
a status, a group, or decoration. Status green is `ok`, and the two sit side by side
without confusion only because neither borrows the other's job.

**The Two-Block Theme Rule.** Light and dark are two complete, hand-tuned token blocks
(`:root` and `[data-theme="dark"]`). A new token is authored in both before it ships.
Rail tokens are the deliberate exception: they are dark in both blocks.

**The 4.5 Floor Rule.** Every foreground must clear 4.5:1 on every surface it can land on,
not just on `bg`. Test against `bg-active`, the worst case, and against `bg-inverse` for
anything that can appear inside a pressed chip.

## Typography

**Interface:** IBM Plex Sans (400, 500, 600), falling back to the system UI stack.
**Labels:** IBM Plex Sans Condensed (500, 600), falling back to Plex Sans, then Arial
Narrow.
**Machine data:** IBM Plex Mono (400, 500), falling back to the system monospace stack.

**Character:** One family, three registers. Plex is engineered and slightly typewritten,
which suits a ledger. The condensed cut, set in tracked capitals, is the form's printed
headings. The mono is the stamp. Because all three come from one family, they share
x-height and rhythm, so a mono score beside a sans name doesn't jump.

**Loading:** The mockups load the three families from Google Fonts with an `@import` at the
top of `mera.css`. Opened offline, they fall back to system fonts and the layout holds. The
product should self-host the same files (for example through `next/font`) rather than
depend on a third-party request at runtime.

### Hierarchy

- **Display** (Plex Sans 600, 20px, 1.25, -0.015em): Page heads (`h1`). In the top bar the
  title is 18px, and 16px below 560px. The index page alone uses 34px.
- **Headline** (600, 15px, -0.008em): `h2` outside panel heads.
- **Title** (600, 14.5px): Post set titles.
- **Body** (400, 14px, 1.5): Default; reading areas cap at 70ch.
- **Small** (400, 13px): Table cells, variant rows, inbox previews, buttons.
- **Micro** (400, 12–12.5px): Tags, captions, panel footers, field hints, status labels.
- **Panel label** (Condensed 600, 12.5px, 0.09em, uppercase): Panel titles, read as
  column heads on a form.
- **Column label** (Condensed 600, 11.5px, 0.09em, uppercase): Table headers, over a
  `border-strong` underline.
- **Rail label** (Condensed 600, 11px, 0.12em, uppercase): Rail group names, each followed
  by a hairline that runs to the edge.
- **Other condensed labels:** top-bar crumb (500, 12px, 0.08em), calendar day heads
  (600, 12px, 0.06em), proof keys (600, 11px, 0.1em), the kill switch (600, 12.5px,
  0.08em), the wordmark (600, 15px, 0.14em).
- **Mono** (Plex Mono, 0.92em, -0.01em): Hashes, versions, IDs, handles, scores in
  outline tags, the top-bar subtitle, nav counts, `kbd`, character counters, chart and
  calendar times, timeline step numbers.

### Named Rules

**The Three Registers Rule.** Sans is for what a person reads or writes. Condensed
capitals are only for a heading that names a field, column or section; never for a
sentence, a button label, or emphasis. Mono is only for strings a machine produced or a
human compares character by character. A string in the wrong register is a bug.

**The Tabular Figures Rule.** Any number compared down a column uses tabular figures
(`.num`, `.mono`, table cells, definition-list values, outline tags, nav counts).

## Layout

A two-column shell: a 232px always-dark rail and a `minmax(0, 1fr)` main column. The main
column has a 54px sticky top bar and a 22px-padded page.

- **Rail, top to bottom:** the wordmark (a verdigris diamond, MERA in tracked capitals, and
  the phase in mono), the workspace switcher, grouped navigation (Work / Content /
  Accounts / System), then the foot: the signed-in person and role, and the kill switch.
- **Top bar, left to right:** the workspace crumb ("ACME /"), the page title and its mono
  subtitle, a spacer, the 240px command trigger, the theme toggle, the primary action. The
  bar is 92% opaque paper with a 6px backdrop blur, so content scrolling beneath stays
  faintly legible without competing.
- **Page primitives:** `.stack` (16px rhythm), `.grid--2` / `.grid--3` (16px gutter),
  `.split` (content + 320px aside; 380px `--wide`; 300px leading column in `--left`).
  Panels pad 14px; heads are at least 42px tall with 8px/14px padding.

Spacing runs 4, 6, 8, 10, 14, 16, 22px: 4–6 inside components, 8 between row items, 10
for compact padding, 14 inside panels, 16 between panels, 22 for the page frame and top
bar.

### Breakpoints

- **1180px:** splits go single-column, three-up grids go two-up, the inbox list narrows
  to 290px, the command trigger narrows to 180px.
- **900px:**
  - **Rail:** becomes an off-canvas drawer behind a hamburger, with the popover shadow.
  - **Top bar:** the crumb and subtitle hide; the command trigger becomes a 32px icon.
  - **Post sets:** variants stack, and the branch moves in to 18px.
  - **Rows and tables:** panel heads, set heads and action rows wrap. Tables hold one
    line per row and scroll inside their panel.
  - **Page:** padding drops to 16px. The calendar scrolls within a 620px minimum rather
    than squeezing.
- **560px:** the top bar's primary action keeps its accessible label but shows only its
  icon, the title drops to 16px, and a post set's actions take a full row.

### Named Rules

**The Two Surfaces Rule.** Navigation and stopping live on the dark rail; work lives on
paper. Nothing operational (no approve button, no draft) ever sits on the rail, and the
kill switch never appears on paper.

**The Kill Switch Reachability Rule.** "Stop all automation" is pinned to the rail foot,
visible without scrolling on every screen, with nothing below it.

**The One Row Per Account Rule.** A post set renders one variant row per target account.
Aggregate counts ("4 accounts") sit beside the rows that prove them. A bulk action counts
only what it will act on: "Approve remaining 3", not "Approve all 4", when one is already
approved.

**The No Sideways Page Rule.** The page never scrolls horizontally down to 390px. Every
`.table` is wrapped at runtime in a `.tablewrap` (`position: relative; overflow-x: auto`).
Grid and flex children carry `min-width: 0`. Screens are checked at 1440, 1024 and 390px.

## Elevation & Depth

Depth is paper on paper. A panel is a lighter sheet with a hairline edge on a warmer page.
Elevation comes from that tonal step and the rule, not from shadow.

Small, deliberate exceptions, all physical:

- **Button lip:** secondary buttons carry a 1px bottom shadow at 45% of `border-strong`,
  like the bevel on a key. It disappears when the button is pressed, and the primary,
  ghost and grouped buttons don't have it.
- **Popover** (`--shadow-popover`: `0 1px 0 rgba(23,24,26,.04), 0 12px 32px -10px
  rgba(23,24,26,.28)` light; `0 1px 0 rgba(0,0,0,.4), 0 14px 36px -10px rgba(0,0,0,.75)`
  dark): the command palette, the mobile rail drawer, the calendar reschedule popover.
- **Rings drawn as shadows:** `box-shadow: 0 0 0 1px` outlines the pressed segment, and a
  2px ring separates overlapping account marks. Both are borders that don't take layout
  space, not elevation.
- **Focus halo:** `0 0 0 3px var(--focus-ring)` on focused inputs.

### Named Rules

**The Sheet-Not-Shadow Rule.** Separation comes from a tonal step plus a rule. A shadow is
allowed only on something that overlays the page and can be dismissed, or as the one
physical key lip on a secondary button.

## Shapes and rules

A squared-off radius family: 3px (`sm`) for tags, kbd, segments and calendar items, 4px
(`md`) for buttons, inputs, nav items, notices and the proof receipt, 6px (`lg`) for
panels and previews.

Shapes that break the family on purpose:

- **Rounded:** filter chips are rounded (14px radius) and nav counts are pills (9px), so a
  filter never looks like a button. Account marks are circles, so an identity never looks
  like a control.
- **Square:** the switch is square (3px track, 2px knob), so on/off reads like a lever.
  Group marks are near-square (1px corners).
- **Diamond:** the brand mark is a rotated square, and a solid danger status dot becomes
  one too.

Line styles carry meaning:

- **Solid 1px** (`border`): structure. Panel edges, row rules.
- **Solid 1px** (`border-strong`): something you operate, a table's head underline, the
  variant branch.
- **Dashed** (`border-strong`): something separable or provisional. Proof receipts,
  panel footers, media drop areas.
- **Dotted** (`border-strong`): definition-list rows, read like a ruled form.

Icons are authored SVG on a 16px grid at 1.5px stroke (1.25px at 26px), round caps and
joins, no fill. Platform glyphs are filled, and render inside the inverted account-mark
circle at 11px. The sprite holds 56 symbols: 49 UI icons (`i-`) and 7 platform marks
(`p-`).

### Named Rules

**The Line Means Something Rule.** Solid, dashed and dotted are not interchangeable. Use
dashed only for things that are separable or provisional, and dotted only for ruled
key/value lists.

**The 16-Grid Icon Rule.** Every icon is drawn on the 16px grid at 1.5px stroke and added
to the sprite. No icon fonts, no `<img>` icons, no emoji.

## Components

### Buttons

- **Shape:** 32px tall (28px small), 12px padding (10px small), 4px radius, 6px gap
  between icon and label.
- **Secondary (`.btn`):** `bg-raised` sheet, `border-strong` edge, `fg` text, plus the key
  lip. Hover darkens the edge to `fg-subtle`. Active drops the lip and fills `bg-active`.
- **Primary:** verdigris fill and border, `accent-fg` text at 600, no lip. One per action
  cluster.
- **Ghost:** transparent; hover fills `bg-active`.
- **Danger:** `danger` text on the sheet; hover fills `danger-bg` and takes a `danger`
  edge. Never filled at rest.
- **Disabled:** 0.55 opacity and `pointer-events: none`. The only sanctioned opacity on a
  control.
- **Groups:** `.btngroup` overlaps edges by 1px and squares the inner corners.

### Chips and tags

- **Filter chip (`.chip`):** 28px rounded pill on the sheet, `border` edge, `fg-muted`
  text. Pressed inverts completely: `bg-inverse` fill and `fg-inverse` text. A group
  mark inside gains a 1.5px inverse ring. Any `.subtle` or `.muted` text inside a pressed
  chip inherits the inverse ink, because `fg-subtle` on `bg-inverse` measures only
  2.9:1.
- **Tag (`.tag`):** 22px, 3px radius, a semantic tint with its matching ink.
- **Outline tag (`.tag--outline`):** transparent with a `border-strong` edge and Plex Mono
  11.5px. Used for scores and counts (0.84, "3 tools", "Manual"), which are machine
  values.

### Panels

- **Sheet:** `bg-raised`, 1px `border`, 6px radius, no shadow.
- **Head:** a condensed uppercase title (12.5px, 0.09em), a spacer, then actions; 42px
  minimum height, solid bottom rule.
- **Foot:** a dashed `border-strong` top rule on the same sheet (no separate ground),
  12.5px `fg-muted`. Links in it are verdigris at 500.

### Inputs and fields

- **Control:** 32px, `bg-raised`, `border-strong` edge, 4px radius, 13.5px. Focus takes a
  verdigris border and a 3px verdigris halo.
- **Field:** a 600 13px label, the control, a 12.5px `fg-subtle` hint, 6px apart.
- **Textarea:** 96px minimum, 1.6 line-height.
- **Select:** a hand-drawn chevron from two gradient triangles.
- **Switch:** 32×18 square track; off `border-strong`, on verdigris with an `accent-fg`
  knob.
- **Segmented control (`.seg`):** a `bg-sunken` tray with a `border-strong` edge and 2px
  inset. The pressed segment is a raised sheet with a 1px ring and 600 weight.

### Navigation (rail)

- **Wordmark:** 54px tall to align with the top bar. A 9px verdigris diamond, MERA in
  condensed capitals (0.14em), and "phase 1" in 11px mono at the right.
- **Workspace switcher:** a bordered button holding a 26px light monogram, then two
  stacked lines: name (13.5px/600, `rail-hi`) and meta ("9 accounts · 3 groups", 12px,
  `rail-subtle`, truncating).
- **Group labels:** condensed capitals with a hairline running to the edge.
- **Nav items:** 29px, 13.5px, `rail-fg` with `rail-subtle` icons.
  - **Hover:** fills `rail-hover` and promotes the item to `rail-hi`.
  - **Current page (`aria-current="page"`):** fills `rail-active`, weight 500, and adds a
    3px verdigris bar flush against the rail's left edge.
  - **Counts:** mono 11px pills on `rail-active`.
- **Foot:** the person (a 22px round monogram, name, role in condensed capitals), then
  the kill switch.

### Top bar

- **Crumb:** the workspace in condensed capitals, followed by a `border-strong` slash.
- **Title:** 18px, truncating with an ellipsis. The subtitle is mono 12px.
- **Command trigger (`.cmdk`):** a 240px raised sheet with a search icon, a label that
  fills the width, and `kbd` keys. Below 900px it becomes an icon button.
- **`kbd`:** mono 11px on the sheet, with a 2px bottom border like a keycap.

### Status (signature)

`.status` is an 8px dot plus a 12.5px/500 label, coloured by variant. The dot's shape is
a second channel:

- **Solid:** a settled state (Approved, Published, Active).
- **Ring:** an empty or not-started state (Pending review, On escalation).
- **Half:** in flight or partial (Pending, Expiring, 1 tool changed).
- **Diamond:** a solid danger dot is drawn as a rotated square (Reauth, Retrying,
  Expires in 1h). It is a third cue beyond hue and label for the states that need action.

### Account chip (signature)

`.acct`: a 22px **inverted circle** carrying the 11px platform glyph, then a 13px/500
name and an optional mono 12px handle. The `--lg` variant is 30px. Marks overlap by 6px
in `.acctstack`, separated by a 2px ring in the sheet colour.

### Post set and variant branch (signature)

- **Set head:** a 14.5px/600 title, a 12.5px `fg-muted` meta line (group mark, account
  count, source, stagger window, expiry), and actions to the right.
- **Branch:** `.variants` draws a 1px `border-strong` vertical rule 28px from the set's
  edge. Each `.variant` draws a 14px horizontal tick to it.
- **Variant row:** a three-column grid (180px account chip, truncated text, aside with an
  outline confidence tag, the stagger time and a status). It has 4px radius and fills
  `bg-hover` on hover.
- **Below 900px:** rows stack and the branch moves in to 18px.
- **Disabled account (`--off`):** text stays readable in `fg-subtle` with a
  strikethrough and an explicit "Off" label. Only the platform mark dims (0.55).

### Approval proof receipt (signature)

`.proof`: a wrapping key/value strip on `bg-muted` with a **dashed** `border-strong` edge
and 4px radius. Keys are condensed 11px capitals in `fg-subtle`; values that identify
bytes (version, hash) are mono 12px. The dashed edge makes it read as a receipt torn from
the approval, which is what it is: proof of which bytes, on which account, approved by
whom.

### Tool-call approval block

An external MCP tool call waiting for a human (Approval Queue aside, and the Telegram
preview on Approval Channels):

- **Header:** a sentence ("**Analytics agent** wants to call `notion__create_page`")
  with a micro meta line below (server, risk, run, time).
- **Arguments:** shown in a `.preview` headed "Arguments, exactly as they will be
  sent", in mono, one `key: value` per line, no alignment padding. Long values are cut
  with "…" and a "show all" line; they are never summarized.
- **Actions:** "Allow once" (primary) and "Deny" together. "Block this tool" (danger) is
  pushed to the far end.
- **Caption:** what voids the approval, and that expiry never makes the call.
- **Bot previews:** the buttons in a Telegram card preview are `span.btn`, not
  `<button>`.

### MCP markers

- **Risk tags:** `Read` (outline), `Write` (warn), `Destructive` (danger). Approval
  policy beside each is plain text, not another tag.
- **Server status:** Active (solid ok), 1 tool changed (half warn), Pending review (ring
  neutral).
- **Machine strings:** the protocol version and the agent-facing tool name
  (`notion__search`) are mono, under the human label.

### Inbox and selection

The inbox list is 350px (290px below 1180px). A selected message is marked with
`aria-current="true"` and an `accent-soft` ground with its name at 600. `aria-selected`
is used only on `role="option"` items, as in the command palette.

### Reschedule popover

The keyboard path for dragging a calendar item:

- **Opening:** click or Enter opens a 280px raised popover with the popover shadow,
  anchored under the item and flipping above it if needed.
- **Contents:** Day select, Time input, a note that moving keeps the approval, and
  Cancel / Move.
- **Behaviour:** nothing moves until Move. Escape, Cancel or an outside click closes it
  and returns focus to the item. It is a non-modal `role="dialog"`.

### Data display

- **Tables:** condensed 11.5px uppercase heads over a `border-strong` rule, 10px/12px
  cells, `border` row rules, hover on `bg-hover`. Permission ticks are pseudo-content
  with an `.sr` label. About six columns fit a `.split` main column; past that, fold a
  secondary value under its primary as a micro line.
- **Definition lists (`.dl`):** `fg-muted` keys and right-aligned values, each row on a
  dotted rule.
- **Progress bar:** 6px, 1px corners, `bg-active` track, verdigris fill (or ok/warn/danger
  where the bar is a status).
- **Charts:** series A verdigris 2px solid, series B `fg-subtle` 1.5px dashed, bars
  verdigris over `border-strong`, mono 11.5px axis text, `border-strong` axes, dotted
  `border` grid lines, and a legend.
- **Timeline (`.tl`):** a 28px mono step number, a 132px time, content, an aside.
- **Calendar:** condensed day heads, mono times, items on the paper ground with a 7px
  group square; `cursor: grab`, with the reschedule popover as the keyboard equivalent.

### States

- **Empty:** a centred `fg-subtle` icon, a title, and a 46ch `fg-muted` line.
- **Loading:** `.skel` shimmer between `bg-sunken` and `bg-hover`, 1.4s.
- **Notices:** a 4px-radius block; warn/danger/info take their tint as ground and a 30%
  semantic border, with the icon in the semantic ink and `strong` in `fg`.

### Motion

Buttons transition background, border and colour over 90ms ease-out. The switch track
takes 120ms and its knob 140ms `cubic-bezier(.2,.8,.3,1)`. The rail drawer takes 180ms
with the same curve. The only loop is the skeleton shimmer. `prefers-reduced-motion`
collapses everything to 0.01ms.

### Browser surfaces

Thin scrollbars (10px) with a `border-strong` thumb, inset 3px against the page; the rail
uses `rail-active` for its own thumb. `::selection` is the verdigris tint. Focus is a 2px
outline at 2px offset in `focus`: verdigris on paper, #6fd3c0 on the rail. Link hover is a
1px verdigris underline at 3px offset.

## Do's and Don'ts

### Do:

- **Do** keep verdigris for action and location only: primary buttons, switches on,
  focus, selection, chart series A, the current-page bar, the brand diamond.
- **Do** set panel titles, column heads and rail labels in condensed tracked capitals,
  and everything a machine produced in Plex Mono.
- **Do** draw the branch for every post set, one ticked row per target account.
- **Do** pair every status colour with a dot shape and a label, and use the diamond for
  solid danger.
- **Do** use dashed rules only for separable or provisional things, and dotted rules only
  for definition lists.
- **Do** author new tokens in both theme blocks and test them against `bg-active` and,
  where they can appear in a pressed chip, `bg-inverse`.
- **Do** keep the kill switch alone at the rail foot, on every screen.
- **Do** give every drag a keyboard path; the reschedule popover is the pattern.
- **Do** let wide content scroll inside its container, never the page.
- **Do** show external tool arguments verbatim wherever a human approves them.
- **Do** self-host the Plex families in the product build.

### Don't:

- **Don't** use verdigris for success, or `ok` green for an action. They are different
  jobs.
- **Don't** put anything operational on the rail, or the kill switch anywhere else.
- **Don't** set sentences, buttons or emphasis in condensed capitals.
- **Don't** add a coloured edge to mark selection or identity. The 3px verdigris bar on
  the current nav item is the one sanctioned edge, and it exists only on the dark rail.
- **Don't** dim information with opacity. Use `fg-subtle`, a strikethrough and a label.
  The only sanctioned opacity is a disabled control (0.55) and a disabled account's
  platform mark (0.55).
- **Don't** add shadows beyond the key lip, the popover shadow and ring outlines.
- **Don't** add gradients, glass panels or a second accent. The top bar's light blur is
  the one translucency, and it exists so scrolled content stays faintly visible.
- **Don't** collapse several accounts into one row.
- **Don't** put `aria-selected` on a `<button>`; use `aria-current="true"`.
- **Don't** render a picture of a control (a bot card preview) with real `<button>`
  elements.
- **Don't** let two screens disagree about a count, or a character count disagree with
  its text.
