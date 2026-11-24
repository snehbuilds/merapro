# MCP publishing tools

Mera's publishing tools always act inside the credential's workspace and role. They can
create, edit, schedule, reschedule, or cancel work, but they cannot approve it. A person
must approve the exact immutable post version before a worker can publish it.

## Create with media, a first comment, and a schedule

Upload media from one of three mutually exclusive sources:

```json
{"name":"upload_media","arguments":{"remote_url":"https://cdn.example/chart.png","alt_text":"Read latency by PostgreSQL version"}}
```

```json
{"name":"upload_media","arguments":{"base64_data":"iVBORw0KGgo…","content_type":"image/png","filename":"chart.png"}}
```

For large files, upload to your object store first and pass its short-lived download URL
as `presigned_url`. Mera fetches remote and presigned URLs through its SSRF-safe fetcher,
limits redirects and bytes, and rejects private, loopback, link-local, metadata-service,
and DNS-rebinding targets.

Browse, inspect, or remove workspace-owned assets with the corresponding tools:

```json
{"name":"list_media","arguments":{"limit":50}}
```

```json
{"name":"get_media","arguments":{"media_id":"6f4ac96f-f35b-49ca-a3ed-5ba8f68c18ca"}}
```

```json
{"name":"delete_media","arguments":{"media_id":"6f4ac96f-f35b-49ca-a3ed-5ba8f68c18ca"}}
```

Deletion is rejected when an asset is still referenced by a post. All media operations
enforce workspace ownership and the caller's media permissions.

Then create the post:

```json
{
  "name": "create_post",
  "arguments": {
    "body": "PostgreSQL 18 changes the read-heavy performance curve.",
    "first_comment": "Methodology and raw results are linked below.",
    "link": "https://example.com/benchmark",
    "media_ids": ["6f4ac96f-f35b-49ca-a3ed-5ba8f68c18ca"],
    "media_alt_text": {
      "6f4ac96f-f35b-49ca-a3ed-5ba8f68c18ca": "Read latency falls from PostgreSQL 17 to 18"
    },
    "social_account_ids": ["b40b3d8a-d695-4b0f-861c-10bc9a55b33e"],
    "publish_mode": "scheduled",
    "scheduled_at": "2026-12-02T09:30:00+05:30",
    "timezone": "Asia/Kolkata"
  }
}
```

`draft` only saves the post. `immediate` and `scheduled` submit completed supplied content
for human approval; neither bypasses approval. Timestamps are stored in UTC while the
requested IANA timezone is retained. If approval arrives after a requested time, Mera
places the post after approval and records `approval_after_scheduled_time` in its placement
metadata instead of backdating or silently publishing.

## Editing and approval invalidation

```json
{"name":"update_post","arguments":{"post_set_id":"…","body":"Revised text","first_comment":"Revised comment"}}
```

```json
{"name":"add_first_comment","arguments":{"post_set_id":"…","first_comment":"Full methodology below."}}
```

Text, first-comment, and media changes create a new version and supersede the affected
variant's pending approval. Scheduling-only changes do not alter the approved content.

## Scheduling, cancellation, and status

```json
{"name":"schedule_post","arguments":{"post_set_id":"…","scheduled_at":"2026-12-03T10:00:00Z"}}
```

```json
{"name":"reschedule_post","arguments":{"scheduled_post_id":"…","scheduled_at":"2026-12-04T10:00:00Z"}}
```

```json
{"name":"cancel_post","arguments":{"post_set_id":"…","reason":"Campaign paused"}}
```

```json
{"name":"get_post_status","arguments":{"post_set_id":"…"}}
```

Cancellation affects only this post's draft, approval request, and queued per-account
slots. It never engages the account or workspace kill switch. Status includes independent
first-comment state, platform comment ID, and last error.

## First-comment delivery guarantees

The main post and its first comment are separate deliveries. Mera creates the comment job
only after the post response is durably recorded. The comment has its own content hash,
idempotency key, attempt counter, backoff, audit event, state, and platform ID. Retrying a
comment calls only the platform comment endpoint and can never call the main post endpoint.

LinkedIn post text is limited to 3,000 characters and comments to 1,250 characters. Image
posts support one image or `multiImage`; PDF documents and videos are single-attachment
posts. HTTP 429 responses honor `Retry-After`, and expired tokens hold only the affected
account's work for reconnection.
