# Runbook: kill switch

## Engage (instant, no confirmation)

| Surface | How | Scope |
| --- | --- | --- |
| Web rail | **Stop automation** at the bottom of the rail | workspace |
| Web, Automation | **Stop** on the workspace, a group or an account | any |
| Telegram | `/kill` (active workspace), `/kill Tech News`, `/kill X @main` | any |
| Telegram | `/kill all` (asks once) | every workspace you can reach |
| Telegram | `/pause [scope]` | like `/kill`, but leaves pending approvals open |

Approvers and up may stop what they can reach. `/kill` reports how many accounts and
groups it covered, how many scheduled posts it held and how many pending approvals it
cancelled.

## What it stops

- **Publishing:** every slot for a covered account is held (re-checked every five
  minutes). Nothing is dead-lettered.
- **Approvals:** approving a covered account is refused (`kill_switch_engaged`). `/kill`
  also cancels the pending approvals of the accounts it covers; their siblings on the
  same request stay pending.
- A request records the groups that covered each account at submission, so moving an
  account out of a stopped group does not escape the stop.

## Verify

- Automation shows **Stopped** with who, when and why at that scope.
- Audit Log → *Kill switch* shows `kill_switch.engaged` with the surface (`telegram`,
  `web`).
- Calendar: held posts show "On hold: Automation is stopped for this account."

## Resume (deliberate)

Resuming is web-only and needs an **Admin**: Automation → **Resume** at the scope that
was stopped. Held posts publish at their next check if their approval is still valid;
approvals that were cancelled must be requested again.
