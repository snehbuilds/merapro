# Surface brief — Mera Phase 1 screen set (HTML mockups)

**Scope:** `docs/mockups/` — the full Phase 1 screen inventory as standalone HTML,
light and dark, portable into Next.js + Tailwind + shadcn/ui later.
**Visitor mode:** Operate. The visitor completes a task; scanability and familiar
affordances outrank expression.
**Audience and job:** the operator, mid-task, scanning dense multi-account queues and
deciding fast. Secondary: an agency operator switching between client workspaces.
**Constraints:** WCAG 2.1 AA; status never carried by colour alone; terminology fixed by
the docs glossary; demonstration data only, no invented commercial claims.

## Direction contract

**THESIS.** Mera is an operations console for speaking as several identities at once,
where every post is bound to one exact account and one exact version. The surface owns
*per-account legibility*: at any density, you can always see which account a thing belongs
to, what state it is in, and which version was approved. It refuses the dashboard reflex
of collapsing five accounts into one summary row, and it refuses the decorated-card
scaffold — same-size icon-and-heading tiles standing in for structure.

**OWN-WORLD.** The category standard, played straight, at the craft level of Notion,
Raycast, and Resend — the user's named bar. Near-monochrome neutral palette, warm-leaning
greys, one near-black primary action that inverts to near-white in dark; a single blue
reserved for focus and selection; muted semantic status colours that never form a rainbow.
**Hairline 1px borders are the structure** — panels and tables are divided by rules, not
floated on shadows. Shadow appears only on popovers and menus, with real offset and blur.
System UI stack for interface, system monospace for machine data: hashes, versions, IDs,
handles, timestamps, costs. Radii 4–8px, never pill-soft. Density is Raycast-compact in
rows, Notion-calm in reading areas. No gradients, no glass, no decorative shadows, no
emoji; icons are authored SVG on one 16px grid at a single stroke weight.

**STORY.** The operator understands within one screen that Mera manages *accounts*, not
just posts; that nothing has published without a named human approving a named version;
and that partial outcomes — two of four approved, three of four published — are ordinary
states the system reports precisely rather than failures it apologizes for. They act by
approving, correcting one account, or stopping everything.

**FIRST VIEWPORT.** A fixed left rail (240px) carrying the workspace switcher at top, then
navigation grouped Work / Accounts / System, with a kill switch pinned to the bottom —
reachable without scrolling, because stopping is the one action that must never be hunted
for. A top bar with the screen title, a scope filter (group and account), and a command
affordance showing its shortcut. The work area leads with what the screen is for: on the
Dashboard, the approval queue itself, as rows of post sets each exposing its per-account
variants; not a metric strip. Primary action sits top-right of the work area; destructive
actions never sit adjacent to it.

**FORM.** The category standard, chosen by the user over three distinctive worlds
(press proof, playout schedule, dispatch release) at the direction round; convention is
therefore the commitment and is executed at full fidelity, without irony or smuggled
quirk. Ranked first on the user's own instruction, not on my ordering. The concept-seed
script was blocked by the harness's auto-review, so no roll occurred and the candidate
set was authored rather than dealt; the substitution was disclosed to the user in the
reply that opened the round. seed: none (unavailable)

**FINISH.** unreviewed and undocumented is unfinished; this build ends with the finish
review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Unresolved

- Content pillars and brand voice examples (PRODUCT.md open question Q4) — mockup copy
  uses the PostgreSQL 18 worked example from the docs as demonstration data.
- No logo or wordmark exists; the rail uses a plain typographic lockup rather than an
  invented mark.
