# ADR-0012: Account groups, and settings that resolve most-specific-first except where they resolve strictest-first

**Status:** Accepted
**Date:** 2026-11-08

## Context

A workspace holds many accounts, several per platform. Posting to each individually is
tedious, so the brief introduces **account groups**: named sets mixing platforms, such as
"Tech News" = 2 LinkedIn + 2 X + 1 Facebook Page, where an account may belong to several
groups and a post can target a whole group at once.

Groups are not only a targeting convenience. They are a natural home for settings — brand
voice, posting schedule, content pillars, approval policy, autopilot mode, stagger
configuration — which creates the real design problem. The same setting can now exist at
four levels (post override, account, group, workspace), an account can be reached through
different groups on different days, and "which brand voice applies" must have one
defensible answer every time.

Getting this wrong is not a cosmetic failure. If approval policy resolves the wrong way,
adding a company page to a convenient group silently removes its requirement for two
approvers, and nobody finds out until something goes out unapproved.

## Decision

### 1. Group membership is a join table

`account_group_member` with `UNIQUE (account_group_id, social_account_id)`. An account
belongs to zero or more groups. A column on `social_account` would force one group per
account and collapse the case that motivates the feature — the company page genuinely
belongs in both "Tech News" and "Company Official".

Membership carries `position`, giving the group a deterministic publish order, which
staggering depends on.

### 2. The targeting group is recorded on the variant

`post_variant.via_account_group_id` names the group that routed this account into this
post set. When an account belongs to several groups, this is what makes group-level
resolution deterministic rather than an arbitrary pick. It is also a historical fact:
moving an account between groups next month must not rewrite which group last month's post
went out through.

### 3. Most settings resolve most-specific-first

```
post override  →  social_account  →  account_group  →  workspace  →  system default
```

Implemented once, in `domain/policies/settings_resolution.py`, used by the API, the
agents, and the bots. It returns the value **and the level it came from**, which the UI
renders as "Brand voice: Technical, inherited from group Tech News".

### 4. Approval policy and autopilot mode resolve strictest-first

These two do **not** follow the chain above. They resolve to the most restrictive value
among all applicable levels:

| Account setting | Group setting | Effective |
| --- | --- | --- |
| Manual | Full | **Manual** |
| Full | Manual | **Manual** |
| 2 approvers | 1 approver | **2 approvers** |
| unset | Assisted | Assisted |

The kill switch follows the same principle in the obvious direction: engaged at any level
above an account stops that account.

This asymmetry is the central decision in this ADR, and it is deliberate. Under
most-specific-first, adding the company page to a group running Full Autopilot would
override its Manual setting, quietly weakening a protection someone configured on purpose.
Under strictest-first, **permissiveness never inherits downward; only restriction does**,
which makes "the company page always requires approval" a property of the system rather
than a configuration everyone must remember not to break.

The cost is a rule people have to learn, and one place where the UI must explain itself:
`GET /automation/resolved` exists so a user can see *why* their post is waiting rather
than discovering the rule by surprise.

### 5. Staggering belongs to the group

`stagger_mode`, `stagger_min_seconds`, and `stagger_max_seconds` are group-level, because
staggering is a property of publishing to several accounts together. Offsets are computed
once at scheduling time and **stored** on `scheduled_post`, so the calendar, the approval
card, and any retry all agree.

`stagger_mode: none` is a blocking guardrail when a group holds two or more accounts on
one platform. See [08 - Platform Matrix](../08-platform-matrix.md) section 3.3.

## Consequences

**Good**

- Posting to five accounts is one action, and the composer still shows and permits editing
  each account individually.
- Settings live where they are conceptually owned: a group's voice on the group, an
  account's quirks on the account.
- Resolution is one function with one set of tests, so the API, the agents, and the bots
  cannot disagree about which voice applies.
- Provenance in the response means settings are explainable, which is what stops
  inheritance from feeling like magic.
- Strictest-first makes the most dangerous misconfiguration — accidentally relaxing an
  approval requirement — structurally impossible.
- Groups give analytics, the calendar, the queue, and the digest a natural grouping key
  without inventing a second one.

**Bad**

- **Two resolution rules instead of one.** Most settings resolve most-specific-first, two
  resolve strictest-first. This is a genuine inconsistency, chosen because the alternative
  is a safety hole, but it has to be documented, taught, and surfaced in the UI.
- **Overlapping membership needs a tiebreak.** `via_account_group_id` solves it, at the
  cost of a column that must be set correctly at target expansion and never recomputed
  afterwards.
- **Group settings can surprise.** Adding an account to a group changes its effective
  voice and schedule. Mitigated by showing provenance everywhere, but it is a real class
  of "why did this post sound different".
- **Deleting a group is not nothing.** Accounts survive, but anything that inherited from
  the group falls back a level. The UI must show what will change before confirming.
- **Four levels is a lot of surface to test.** Resolution is property-tested over every
  combination rather than by example, because the combinations outnumber what anyone would
  enumerate by hand.

## Alternatives considered

**No groups; always select accounts individually.** Simplest, and it makes the routine
case — post this to my usual five accounts — a five-step chore. It also leaves settings
with nowhere to live between account and workspace, so every account repeats the same
voice and schedule.

**Groups as a pure targeting shortcut, carrying no settings.** Tempting for the simplicity:
one resolution rule, no inheritance questions. Rejected because the settings a group would
carry are exactly the ones that differ between "my tech accounts" and "my company
accounts", and without the level they have to be duplicated onto every account, where they
immediately drift.

**One group per account, modelled as a column.** Halves the complexity and fails the
stated requirement that an account belong to several groups.

**Most-specific-first for everything, including approval policy.** Consistent, teachable,
one rule. Rejected because it makes weakening a company page's approval requirement a side
effect of adding it to a group, which is both easy to do and invisible when done. A
consistent rule that produces an unsafe default is worse than an inconsistent rule that
has to be explained once.

**Strictest-first for everything.** Also consistent. Produces nonsense for non-safety
settings: there is no "strictest" brand voice, and the strictest posting schedule is no
posting at all.

## Revisit when

- Users routinely need nested groups, or groups of groups, which would mean the single
  level is not expressive enough for how they actually organize.
- The strictest-first rule produces repeated surprise in practice, which would suggest
  surfacing it more loudly rather than removing it.
- An account's membership in several groups starts needing *different settings per group*
  beyond what `via_account_group_id` resolves, which would point toward settings on the
  membership row rather than on the group.
