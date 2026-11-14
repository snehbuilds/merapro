"""Rendering approval messages for a channel (M7.3-7.5, 7.9-7.10; docs/07 §3). Pure.

The summary card is the two-level layout of docs/07 §3.2: the workspace and group on line
two, the source block once, every account's text (first two lines) with its own time and
confidence and its own flags, shared signals collapsed, then **one primary action** alone on
the top row, numbered per-account buttons, and the corrective actions last.

``fit`` truncates in priority order (passes, then the source summary, then previews) and
never truncates the post body in a detail view: approving a truncated post is exactly the
failure this product exists to prevent.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from mera.domain.policies.scheduling import zone
from mera.domain.ports.approval_channel import Button, ChannelConstraints, OutboundMessage
from mera.domain.value_objects.platform import Platform
from mera.infrastructure.platforms.capabilities import CAPABILITIES

NUMBERS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
RULE = "─" * 24
COMPACT_ABOVE = 8  # above this many accounts, one line each (docs/07 §3.2)


def esc(text: str | None) -> str:
    return html.escape(text or "", quote=False)


def num(i: int) -> str:
    return NUMBERS[i] if i < len(NUMBERS) else f"{i + 1}."


def platform_name(platform: str) -> str:
    try:
        return CAPABILITIES[Platform(platform)].display_name
    except (KeyError, ValueError):
        return platform


def when(iso: str | None, tz: str, *, with_day: bool = True) -> str:
    if not iso:
        return "on approval"
    t = datetime.fromisoformat(iso).astimezone(zone(tz))
    abbr = t.strftime("%Z")
    return (t.strftime("%a %d %b, %H:%M") if with_day else t.strftime("%H:%M")) + (
        f" {abbr}" if abbr else ""
    )


def remaining(expires_iso: str, now: datetime) -> str:
    left = datetime.fromisoformat(expires_iso) - now
    secs = int(left.total_seconds())
    if secs <= 0:
        return "expired"
    h, m = divmod(secs // 60, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def preview_lines(v: Mapping[str, Any], *, lines: int = 2, chars: int = 170) -> str:
    parts = v.get("thread_parts") or []
    text = parts[0] if parts else (v.get("body") or "")
    flat = " ".join(text.split())
    short = flat if len(flat) <= chars else flat[: chars - 1].rstrip() + "…"
    del lines
    return short


def flag_lines(v: Mapping[str, Any]) -> list[str]:
    out = []
    for f in v.get("compliance_flags") or []:
        sev = f.get("severity")
        if sev not in ("warning", "block"):
            continue  # passes collapse into the shared tick line
        icon = "⛔" if sev == "block" else "⚠️"
        text = f.get("explanation") or f.get("excerpt") or f.get("category") or "check"
        out.append(f"{icon} {esc(str(text))[:140]}")
    return out


def _state_badge(state: str) -> str:
    return {
        "approved": "✅ approved",
        "rejected": "❌ rejected",
        "superseded": "✏️ revised, asked again",
        "expired": "⌛ expired",
        "cancelled": "✖️ cancelled",
    }.get(state, "")


def summary_card(
    payload: Mapping[str, Any],
    *,
    now: datetime,
    approve_all: str | None,
    per_account: Sequence[str],
    message_ref: str,
    web_url: str | None,
    revising: set[str] | None = None,
    constraints: ChannelConstraints | None = None,
) -> OutboundMessage:
    """``approve_all`` is the callback for the unscoped token (None hides the button);
    ``per_account`` are the numbered buttons' callbacks (detail views), one per variant."""
    req = payload["request"]
    tz = (payload.get("workspace") or {}).get("timezone") or "UTC"
    variants = req["variants"]
    n = len(variants)
    ws_name = esc((payload.get("workspace") or {}).get("name"))
    grp = payload.get("group")
    open_ = req["state"] in ("pending", "awaiting_edit")
    head = [
        f"📋 <b>Approval needed</b> · expires in {remaining(req['expires_at'], now)}"
        if open_
        else _final_head(req["state"]),
        f"{ws_name}"
        + (f" · {esc(grp['name'])}" if grp else "")
        + f" · {n} account{'s' if n != 1 else ''}",
        RULE,
    ]
    src = payload.get("source")
    source_block: list[str] = []
    if src:
        source_block.append(
            "📰 <b>"
            + esc(src.get("title"))[:120]
            + "</b>"
            + (f" · {esc(src.get('source_name'))}" if src.get("source_name") else "")
        )
        if src.get("summary"):
            source_block.append("   " + esc(src["summary"])[:220])
    media = sum(len(v.get("media") or []) for v in variants)
    if media:
        with_alt = sum(1 for v in variants for m in (v.get("media") or []) if m.get("alt_text"))
        source_block.append(
            f"📎 {media} image{'s' if media != 1 else ''} · "
            f"alt text {'set' if with_alt == media else f'on {with_alt}'}"
        )
    body: list[str] = []
    compact = n > COMPACT_ABOVE
    revising = revising or set()
    for i, v in enumerate(variants):
        sched = payload.get("scheduled_for", {}).get(v["post_variant_id"])
        conf = v.get("confidence_score")
        bits = [
            f"{num(i)} <b>{esc(v['nickname'])}</b> ({esc(platform_name(v['platform']))})",
            when(sched, tz, with_day=False),
        ]
        if conf is not None:
            bits.append(f"{conf:.2f}")
        need = int(v.get("required") or 1)
        if need > 1 and v.get("state") == "pending":
            # Vote progress for multi-approver accounts (P2-M2.8).
            bits.append(f"🗳 {int(v.get('approve_count') or 0)} of {need}")
        if v.get("thread_parts"):
            bits.append(f"thread, {len(v['thread_parts'])}")
        line = " · ".join(bits)
        badge = "✍️ being revised" if v["post_variant_id"] in revising else _state_badge(v["state"])
        if badge:
            line += f" · {badge}"
        body.append(line)
        if compact:
            continue
        body.append("   " + esc(preview_lines(v)))
        if v.get("hashtags"):
            body.append("   " + esc(" ".join("#" + h.lstrip("#") for h in v["hashtags"])))
        body.extend("   " + f for f in flag_lines(v))
        body.append("")
    if compact:
        body.append(f"<i>{n} accounts: one line each. Tap a number for the full text.</i>")
    shared = []
    if payload.get("facts_verified_all"):
        shared.append(f"✅ Facts verified against source (all {n})")
    d = payload.get("distinct") or {}
    if d.get("max_similarity") is not None:
        icon = "✅" if d.get("ok") else "⚠️"
        shared.append(
            f"{icon} Each account's text is distinct (max {float(d['max_similarity']):.2f})"
        )
    span = int(payload.get("stagger_span_seconds") or 0)
    if n > 1:
        shared.append(f"🕒 Staggered over {max(1, span // 60)} min" if span else "🕒 All at once")
    text = fit(
        [head, source_block, body, [RULE, *shared] if shared else []],
        (constraints.max_body_chars if constraints else 4096),
    )
    rows: list[list[Button]] = []
    if open_:
        if approve_all:
            rows.append(
                [Button(f"✅ Approve all {n}" if n > 1 else "✅ Approve & schedule", approve_all)]
            )
        numbered = [Button(num(i), cb) for i, cb in enumerate(per_account)]
        for k in range(0, len(numbered), 5):
            rows.append(numbered[k : k + 5])
        rows.append(
            [Button("✏️ Edit all", f"E:{message_ref}"), Button("🕒 Reschedule", f"t:{message_ref}")]
        )
        tail = [Button("❌ Reject all", f"R:{message_ref}")]
        if web_url:
            tail.append(Button("🌐 Open in web", url=web_url))
        rows.append(tail)
    elif web_url:
        rows.append([Button("🌐 Open in web", url=web_url)])  # buttons removed, not disabled
    return OutboundMessage(text=text, rows=rows)


def _final_head(state: str) -> str:
    return {
        "approved": "✅ <b>Approved</b>",
        "partially_approved": "🟡 <b>Partly approved</b>",
        "rejected": "❌ <b>Rejected</b>",
        "expired": "⌛ <b>Expired</b>: nothing unapproved will go out",
        "cancelled": "✖️ <b>Withdrawn</b>",
        "superseded": "✏️ <b>Replaced by a revised version</b>",
    }.get(state, f"<b>{esc(state)}</b>")


def detail_view(
    payload: Mapping[str, Any],
    index: int,
    *,
    approve: str | None,
    reject: str | None,
    message_ref: str,
    regenerate_cost: str | None = None,
) -> OutboundMessage:
    """``regenerate_cost`` (e.g. "$0.06"): offer "🎨 Regenerate image" with its estimated
    cost, for a pending account that has images (P2-M5.6)."""
    req = payload["request"]
    tz = (payload.get("workspace") or {}).get("timezone") or "UTC"
    v = req["variants"][index]
    sched = payload.get("scheduled_for", {}).get(v["post_variant_id"])
    lines = [
        f"{num(index)} <b>{esc(v['nickname'])}</b> · "
        f"{esc(platform_name(v['platform']))} · {when(sched, tz)}",
        f"<i>@{esc(v.get('handle'))} · version {v.get('version_no')}</i>",
        RULE,
    ]
    parts = v.get("thread_parts") or []
    if parts:
        lines.extend(f"{k}/ {esc(p)}" for k, p in enumerate(parts, start=1))
    else:
        lines.append(esc(v.get("body")))
    if v.get("link_url"):
        lines.append(esc(v["link_url"]))
    if v.get("hashtags"):
        lines.append(esc(" ".join("#" + h.lstrip("#") for h in v["hashtags"])))
    media = v.get("media") or []
    if media:
        alt = sum(1 for m in media if m.get("alt_text"))
        lines.append(f"📎 {len(media)} image{'s' if len(media) != 1 else ''} · alt text on {alt}")
    lines.append(RULE)
    if v.get("confidence_score") is not None:
        lines.append(f"🤖 Confidence {v['confidence_score']:.2f}")
    lines.extend(flag_lines(v) or ["✅ No warnings"])
    if v["state"] != "pending":
        lines.append(_state_badge(v["state"]))
    rows: list[list[Button]] = []
    if approve or reject:
        row = []
        if approve:
            row.append(Button("✅ Approve this", approve))
        if reject:
            row.append(Button("❌ Reject this", reject))
        rows.append(row)
    if v["state"] == "pending" and req["state"] in ("pending", "awaiting_edit"):
        edit = [Button("✏️ Edit", f"e:{message_ref}:{index}")]
        if regenerate_cost and media:
            edit.append(
                Button(f"🎨 Regenerate image · {regenerate_cost}", f"g:{message_ref}:{index}")
            )
        rows.append(edit)
    rows.append([Button(f"↩️ Back to all {len(req['variants'])}", f"b:{message_ref}")])
    # The body is never cut here: a post too long for one message is sent in full by the
    # dispatcher as a follow-up rather than truncated (docs/07 §8.1).
    return OutboundMessage(text="\n".join(lines)[:4096], rows=rows)


def fit(blocks: Sequence[Sequence[str]], limit: int) -> str:
    """Join blocks; if too long, drop shared passes, then the source summary, then shorten
    previews. Order of ``blocks``: head, source, body, shared."""

    def join(bs: Sequence[Sequence[str]]) -> str:
        return "\n".join(line for b in bs for line in b).strip()

    text = join(blocks)
    if len(text) <= limit:
        return text
    head, source, body, shared = (list(b) for b in blocks)
    shared = [s for s in shared if not s.startswith("✅")]
    text = join([head, source, body, shared])
    if len(text) <= limit:
        return text
    source = source[:1]
    text = join([head, source, body, shared])
    if len(text) <= limit:
        return text
    body = [b for b in body if not b.startswith("   ") or b.strip().startswith(("⚠️", "⛔"))]
    body.append("<i>Previews shortened to fit; tap a number for the full text.</i>")
    return join([head, source, body, shared])[:limit]


def reminder(
    payload: Mapping[str, Any], *, now: datetime, approve_all: str | None, web_url: str | None
) -> OutboundMessage:
    req = payload["request"]
    title = esc(req.get("title") or "this post")
    pending = sum(1 for v in req["variants"] if v["state"] == "pending")
    rows = []
    if approve_all:
        rows.append([Button(f"✅ Approve {pending}", approve_all)])
    if web_url:
        rows.append([Button("🌐 Open in web", url=web_url)])
    return OutboundMessage(
        text=(
            f"⏰ Still waiting on this one.\n{title} · {pending} account(s) · "
            f"expires in {remaining(req['expires_at'], now)}"
        ),
        rows=rows,
    )


def escalation(payload: Mapping[str, Any], *, level: int, now: datetime) -> OutboundMessage:
    req = payload["request"]
    return OutboundMessage(
        text=(
            f"↗️ Escalated (level {level}): <b>{esc(req.get('title'))}</b> still needs a decision.\n"
            f"Expires in {remaining(req['expires_at'], now)}. The original buttons remain valid."
        )
    )


def publish_report(
    *,
    title: str,
    group: str | None,
    rows: Sequence[Mapping[str, Any]],
    tz: str,
    web_url: str | None,
) -> OutboundMessage:
    done = sum(1 for r in rows if r["state"] == "published")
    lines = [f"🚀 <b>Published</b> · {esc(group or title)} · {done} of {len(rows)}"]
    for r in rows:
        name = esc(r["nickname"])
        if r["state"] == "published":
            lines.append(
                f"✅ {name} {when(r.get('published_at'), tz, with_day=False)} · "
                f"{esc(r.get('permalink') or '')}"
            )
        elif r["state"] == "dead_lettered":
            lines.append(
                f"❌ {name} · failed, will not retry\n   “{esc((r.get('error') or '')[:160])}”"
            )
        elif r["state"] == "failed":
            lines.append(
                f"❌ {name} · failed, retrying "
                f"(attempt {r.get('attempts')} of {r.get('max_attempts')})\n"
                f"   “{esc((r.get('error') or '')[:160])}”"
            )
        else:
            lines.append(f"🕒 {name} · {esc(r['state'])}")
    buttons = [[Button("🌐 Open in web", url=web_url)]] if web_url else []
    return OutboundMessage(text="\n".join(lines), rows=buttons)


def health_alert(
    *, nickname: str, reason: str | None, held: int, web_url: str | None
) -> OutboundMessage:
    rows = [[Button("🔗 Reconnect", url=web_url)]] if web_url else []
    return OutboundMessage(
        text=(
            f"⚠️ <b>{esc(nickname)} needs reconnecting</b>\n"
            f"{esc(reason or 'The platform revoked the grant.')}\n"
            f"{held} scheduled post{'s' if held != 1 else ''} for this account "
            f"{'are' if held != 1 else 'is'} on hold. "
            "Other accounts are unaffected."
        ),
        rows=rows,
    )


REPLY_SHOWN = 1500  # a longer reply is not shown in full on a card, so it gets no Send
ARGS_SHOWN = 1800  # likewise the arguments of a tool call: no Allow when they are cut
TOO_LONG = "⚠️ <i>Too long to show in full here: open it in the web to approve.</i>"


def reply_fits(reply: str) -> bool:
    """Whether a card shows the whole reply. Approving text you could not read is exactly
    what the product exists to prevent, so a card that cuts it offers no Send."""
    return len(reply) <= REPLY_SHOWN


def reply_card(
    *,
    workspace: str,
    platform: str,
    account: str,
    handle: str,
    author: str | None,
    comment: str,
    reply: str,
) -> str:
    """A reply waiting for approval (P2-M6.6). The comment is quoted as untrusted text."""
    lines = [
        f"💬 <b>Reply waiting</b> · {esc(workspace)}",
        f"{esc(platform_name(platform))} · {esc(account)} <i>@{esc(handle)}</i>",
        RULE,
        f"<b>@{esc(author or 'someone')}</b> commented:",
        f"<i>{esc(comment[:900])}</i>",
        RULE,
        f"<b>Reply as {esc(account)}:</b>",
        esc(reply[:REPLY_SHOWN]),
    ]
    if not reply_fits(reply):
        lines += ["…", TOO_LONG]
    return "\n".join(lines)[:4000]


def tool_call_card(
    *,
    workspace: str,
    server: str,
    tool: str,
    risk: str,
    agent: str | None,
    arguments: dict[str, object],
) -> str:
    """An external MCP write waiting for a person (P2-M9.7): the exact arguments, as JSON."""
    import json

    args = json.dumps(arguments, indent=2, ensure_ascii=False, default=str)
    lines = [
        f"🔧 <b>Tool call waiting</b> · {esc(workspace)}",
        f"{esc(server)} · <code>{esc(tool)}</code> · {esc(risk)}",
        f"Asked by the {esc(agent or 'unknown')} agent",
        RULE,
        f"<pre>{esc(args[:ARGS_SHOWN])}</pre>",
    ]
    if not tool_args_fit(arguments):
        lines.append(TOO_LONG)
    return "\n".join(lines)[:4000]


def tool_args_fit(arguments: dict[str, object]) -> bool:
    """Whether a card shows every argument; if not, it offers no Allow (Deny is safe)."""
    import json

    return len(json.dumps(arguments, indent=2, ensure_ascii=False, default=str)) <= ARGS_SHOWN
