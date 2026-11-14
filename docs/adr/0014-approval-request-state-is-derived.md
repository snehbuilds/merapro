# ADR-0014: Approval request state is derived from its variants; revocation is scoped; caps resolve strictest

**Status:** Accepted
**Date:** 2026-11-08 (product decisions taken at the start of Sprint 8)

## Context

Four documents disagreed about `PARTIALLY_APPROVED` (docs/12 §1, item C1):

- 03 called it terminal;
- 07 §5.2 accepted new decisions in that state;
- 04 §4.9 said a request "stays PENDING until every enabled variant has a decision";
- 02 flow 2 moved `AWAITING_EDIT → PARTIALLY_APPROVED` mid-flow.

Two related questions were also open:

- **C2:** should one decision revoke every token on the request (07 §7.3) or only the tokens for the variants it decided (07 §5.3)?
- **C3:** which status codes mean "the post changed since this button was minted" and "this button was already used"?

Separately, daily posting caps and quiet hours resolved most-specific (ADR-0012). That let a group-scoped Editor raise a group's cap above the Admin's workspace cap. M6 snapshots policy into every request, so the rule had to be fixed before M6 shipped.

## Decision

### 1. The request state is a function of the variant states (C1)

Each `approval_variant_state` runs its own machine:

```
pending → approved | rejected | superseded | expired | cancelled
```

Every outcome is terminal. The request state is **derived**, never set directly, except for three request-wide transitions: `cancelled`, `awaiting_edit` and `superseded`.

- `pending` while any enabled variant is still `pending`.
- Once every enabled variant is terminal:
  - `approved` if all are approved.
  - `rejected` if all are rejected.
  - `expired` if all expired.
  - `cancelled` if all were cancelled.
  - `superseded` if all were superseded.
  - `partially_approved` for any mix that includes at least one approval.
  - With no approval, a mix resolves to whichever terminal outcome is "least final": `rejected` beats `expired` beats `superseded` beats `cancelled`.
- `partially_approved` is **terminal**. It is the normal outcome of "approve LinkedIn, reject X": the approved variants schedule, and nothing is left waiting.
- A request that still has a pending variant accepts decisions for that variant. It is still `pending`, not `partially_approved`.

The function lives in `domain/state_machines/approval.py`. It is pure and property-tested.

### 2. Revocation is scoped (C2)

A decision revokes only the tokens whose `variant_scope` intersects the variants it resolved. A request reaching a terminal state revokes every remaining token in the same transaction. The scheduler always places the approved variants. Nothing about revocation skips it.

### 3. Status codes (C3)

| Failure | Status | Code |
| --- | --- | --- |
| The post changed since the token or page was loaded | 422 | `approval_content_mismatch` |
| Token already used or revoked | 410 | `approval_token_used` |
| Request or variant expired | 410 | `approval_expired` |
| Variant or request already decided | 409 | `approval_already_decided` |
| Approver not scoped to a named account | 403 | `approver_not_authorized` |
| Kill switch engaged at any level | 409 | `kill_switch_engaged` |

### 4. Caps and quiet hours resolve strictest (amends ADR-0012)

`daily_post_cap` takes the **lowest** value set at any level. `quiet_hours` takes the **longest** window set at any level.

A group or account can tighten what the workspace allows, never loosen it. This matches how `required_approvers` and `autopilot_mode` already resolved.

### 5. Who can approve

Only a **person** approves, from the web (or a bot bound to them, M7). An API key, or an agent holding one, can read the queue but never supply an approval (`403 human_approval_required`). The approval is the product's guarantee that a person read each account's text.

A requester may approve their own post. Phase 1 workspaces are often one person, and a "four eyes" rule is expressed by policy: set `required_approver_count: 2` with `all_of` on the accounts that need it.

### 6. No second factor in Phase 1 (P4)

Approval PINs and the second-factor action list move to Phase 2 in full. The `second_factor_actions` policy field is kept so snapshots stay forward-compatible. In Phase 1 it is always empty, and the API refuses to set it.

## Consequences

- **Approvals must be terminal per variant.** There is no "un-approve". Changing an approved variant creates a new version, which supersedes it.
- **Narrow buttons survive their siblings.** Because revocation is scoped, a bot card's "Approve X @main" keeps working after "Reject X @tech". Only the "Approve all" button, which carries the composite hash, dies when any sibling moves.
- **Caps can only get lower down the hierarchy.** Raising a group's cap above the workspace's has no effect. The settings screens show the effective value and the level it came from, so this is visible.
- **A group that carries a policy or a kill switch protects its accounts.** Only an Admin can remove an account from such a group or delete it. A request records the groups that covered each account when it was submitted, and their kill switches keep applying.
- **Expiry is recorded when a request is next touched.** The M6.8 sweeper does it on a clock (Sprint 9). Until then, a decision, a read or a resubmission after `expires_at` marks the pending accounts expired.
- **Company pages need no PIN in Phase 1.** Their only extra protection is a policy requiring more approvers. Phase 2 restores the PIN.
