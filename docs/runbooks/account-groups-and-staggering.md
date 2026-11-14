# Runbook: account groups, staggering and publishing

## Designing groups

A group is a set of accounts that usually share a story (*Tech News*), with its own stagger
mode, posting schedule and settings. An account can be in several groups. A post set can
target groups, accounts or both; the targets are deduplicated, and each account gets
**its own** post, written for it.

## Which setting wins

Most settings resolve **most specific first**: account, then group, then workspace.
Safety settings resolve **strictest** (ADR-0012, ADR-0014 §4):

| Setting | Rule |
| --- | --- |
| Autopilot mode | strictest (Manual beats Assisted beats Full) |
| Required approvers, quorum | strictest: the most approvers, `all_of` over `any_of` |
| Daily post cap | the **lowest** set at any level |
| Quiet hours | a window covering **every** level's window |
| Kill switch | engaged at any level above an account stops that account |

The account and group screens show each effective value and where it came from.

## Why two accounts on one platform must stagger

Platforms flag coordinated posting. Two accounts on the same platform posting the same
story at the same moment is the pattern that gets accounts limited, and on X near-duplicate
content across accounts is enforced by suspension (docs/08 §3.3). So:

- The Writer drafts **different** text per account. The cross-account check blocks a set
  at 0.90 similarity and warns at 0.80; two rewrite attempts, then a person decides.
- Stagger offsets are fixed when the set is created, from the group's stagger mode
  (`fixed` or `random_spread`, between the group's min and max seconds), and stored.
- A set where two same-platform accounts would post at the same moment
  (`stagger_mode: none`) is refused at **Send for approval** with the setting named.

## From approval to published

1. When an account is approved, it is **placed** immediately: requested time (or now)
   plus its stagger offset, moved forward past the account's quiet hours, daily cap and
   a 10-minute minimum spacing from its other posts. The slot is stored once, so the
   calendar shows exactly what the approver saw; a move says which rule moved it.
2. A dispatcher runs every minute and queues one publish job per due slot. Jobs for one
   account run one at a time; different accounts never wait on each other.
3. Before every platform call, `assert_publishable` re-checks the approval, the exact
   version and hash, the validity window, the quorum, the kill switch at all three levels
   and the account's health. Editing an approved account cancels its slot.
4. Each account succeeds or fails on its own. A set lands in `published` or
   `partially_published`; the Telegram report names each account and any error.

## Failures

| What happened | What Mera does |
| --- | --- |
| 5xx, timeout | retry with exponential backoff (1, 2, 4, 8 minutes), five attempts |
| 429 | waits the platform's `Retry-After`, then retries |
| Response lost after the platform accepted | the retry first looks for the post it already made; it never posts twice |
| Content refused (4xx) | dead-lettered at once: retrying cannot fix it |
| Token revoked | the account moves to `needs_reauth`; its posts are **held**, not failed |
| Kill switch | held and re-checked every five minutes; nothing fails |

Dead letters show on the Dashboard and the Calendar with the platform's message.
**Retry** re-runs every check from scratch; **Dismiss** records that a person looked.
