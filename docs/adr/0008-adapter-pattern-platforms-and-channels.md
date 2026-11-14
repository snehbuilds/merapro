# ADR-0008: One adapter pattern for both social platforms and approval channels

**Status:** Accepted
**Date:** 2026-11-08

## Context

Mera integrates with two families of external system that look different but behave the
same way architecturally.

**Social platforms** (nine or more) vary wildly in what they support. Instagram cannot
post text without media. X charges for write access and forbids near-duplicate content.
LinkedIn gives organizations rich analytics and members almost none. Mastodon can edit a
published post; most cannot. Bluesky counts graphemes, not characters.

**Approval channels** (Telegram and WhatsApp now, Slack, Teams, email, and push later)
vary too. Telegram can edit a sent message and has effectively unlimited inline buttons.
WhatsApp can do neither, requires pre-approved templates outside a 24-hour window, and
charges per message.

The naive approach is a lowest-common-denominator interface. That produces a product that
is worse than every platform it integrates with: no Mastodon editing because most
platforms cannot edit, three buttons everywhere because WhatsApp allows three.

The equally naive opposite is platform-specific branching throughout the UI and the
agents, which spreads knowledge of nine platforms across every layer.

## Decision

One pattern, applied twice: **a narrow interface plus a rich, machine-readable capability
descriptor**.

```python
class PlatformAdapter(Protocol):
    platform: Platform
    capabilities: PlatformCapabilities
    async def publish(self, req: PublishRequest) -> PublishResult: ...
    # ...

class ApprovalChannel(Protocol):
    channel_type: ChannelType
    constraints: ChannelConstraints
    async def send_approval_request(self, req, binding) -> ChannelMessageRef: ...
    # ...
```

The rules:

1. **Capabilities are data, not behaviour.** `supports_edit: false` is a field, not a
   method that raises.
2. **Consumers read capabilities.** The UI greys out Edit because the descriptor says so.
   The Writer agent receives per-platform limits as input. The approval renderer lays out
   buttons according to `max_inline_buttons`.
3. **No platform names outside the adapter package.** A `platform == "instagram"` check in
   a React component or an agent prompt is a bug; the missing capability field is the fix.
4. **Every adapter has a `fake.py` sibling** implementing the same Protocol with a
   recorder, which is what sandbox mode and the test suite run against.
5. **Degradation is explicit.** Where a platform cannot do something, the UI says so
   rather than hiding it silently. "LinkedIn does not support editing published posts"
   beats a mysteriously absent button.

## Consequences

**Good**

- Adding a platform is one directory plus a descriptor. No UI changes, no agent changes.
- The UI is honest about limitations by construction, because it renders from the
  descriptor.
- Agents receive real constraints, so the Writer does not produce a 500-character Bluesky
  post or a text-only Instagram post that fails at publish time.
- Sandbox mode is dependency injection, not a parallel code path, so the tested behaviour
  is the shipped behaviour.
- The two families share conventions, so someone who has written a platform adapter can
  write a channel adapter.
- Capability data is served over the API, so future mobile and desktop clients inherit the
  same honesty without reimplementing it.

**Bad**

- **The descriptor grows.** `PlatformCapabilities` already has around 25 fields and will
  grow as platforms add features. Acceptable: it is a data class, and growth is additive.
- **Adding a capability touches every adapter.** A new field needs a value in nine
  descriptors. Mitigated by sensible defaults, so only adapters that differ need editing.
- **Some capabilities resist modelling.** X's edit window is tier-dependent and
  time-limited; LinkedIn's rate limits are not published. Modelled as optional fields with
  conservative defaults, and where it cannot be modelled honestly it is reported as
  unsupported.
- **Descriptors go stale.** Platforms change. Mitigated by a dated verification note in
  each adapter and the checklist in
  [08 - Platform Matrix](../08-platform-matrix.md) section 7.

## Alternatives considered

**Lowest-common-denominator interface.** Simpler and strictly worse. It would mean no
Mastodon edit, no X threads as a first-class concept, and three buttons on Telegram.

**Capability negotiation at call time**, where the adapter raises `NotSupported`. Moves
failure from design time to runtime, which is exactly backwards for a system where the
runtime failure happens after a human already approved the post.

**A third-party aggregator API** such as Ayrshare or Buffer's API. Would remove most of
this work. Rejected because it adds a vendor between Mera and the platforms, obscures
error handling and rate limits, imposes its own costs and limits, and makes the capability
matrix someone else's opinion. It also conflicts with the self-hosted posture.

**Separate patterns for platforms and channels.** They have genuinely different
interfaces, so sharing a pattern rather than a base class is the right level of reuse.
Sharing a base class would have been over-abstraction; sharing nothing would have meant
two conventions for one problem.

## Revisit when

- An adapter needs a capability that cannot be expressed as data, repeatedly.
- The descriptor exceeds roughly 50 fields, which would suggest it should be decomposed by
  concern: composition, lifecycle, engagement, analytics.
- A platform's API is dynamic enough that capabilities must be discovered at runtime
  rather than declared, which would turn the descriptor into a cache.
- A third-party aggregator becomes compelling enough to replace several adapters, at which
  point it would be implemented as one adapter behind the same Protocol rather than as a
  replacement for the pattern.
