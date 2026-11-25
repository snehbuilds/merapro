"""Publishing capability upgrade contracts that do not need Postgres."""

from __future__ import annotations

import uuid

from mera.domain.policies.content_hash import compute_content_hash
from mera.domain.ports.platform_adapter import PublishPayload
from mera.infrastructure.platforms.adapters.fake import FakePlatformAdapter
from mera.mcp.tools import TOOLS, upload_media


def _hash(first_comment: str | None) -> bytes:
    return compute_content_hash(
        platform="linkedin_member",
        social_account_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        body="A post",
        thread_parts=None,
        hashtags=[],
        link_url=None,
        media=[],
        first_comment=first_comment,
    )


def test_first_comment_changes_approval_hash_but_none_is_backward_compatible() -> None:
    assert _hash("Details below") != _hash("Different details")
    assert _hash(None) == compute_content_hash(
        platform="linkedin_member",
        social_account_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        body="A post",
        thread_parts=None,
        hashtags=[],
        link_url=None,
        media=[],
    )


async def test_comment_idempotency_is_independent_from_post_idempotency() -> None:
    adapter = FakePlatformAdapter("linkedin_member")
    payload = PublishPayload(
        platform="linkedin_member",
        platform_account_id="member-1",
        account_type="personal",
        body="A post",
        thread_parts=None,
        hashtags=[],
        link_url=None,
        link_placement="first_comment",
        media=[],
        idempotency_key="post-key",
        first_comment="Details below",
    )
    post = await adapter.publish({"handle": "ok"}, payload)
    first = await adapter.publish_comment(
        {"handle": "ok"},
        platform_account_id="member-1",
        platform_post_id=post.platform_post_id,
        body="Details below",
        idempotency_key="comment-key",
    )
    second = await adapter.publish_comment(
        {"handle": "ok"},
        platform_account_id="member-1",
        platform_post_id=post.platform_post_id,
        body="Details below",
        idempotency_key="comment-key",
    )
    assert first == second
    assert [call["op"] for call in adapter.calls] == ["publish", "comment"]


def test_mcp_upgrade_tools_are_registered_and_never_approve() -> None:
    names = {tool.name for tool in TOOLS}
    assert {
        "upload_media",
        "list_media",
        "get_media",
        "delete_media",
        "update_post",
        "cancel_post",
        "add_first_comment",
        "schedule_post",
        "reschedule_post",
        "get_post_status",
    } <= names
    assert not {name for name in names if "approve" in name or "publish_now" in name}


async def test_upload_media_accepts_chatgpt_file_params() -> None:
    class Api:
        posted: tuple[str, dict[str, object]] | None = None

        async def post(self, path: str, body: dict[str, object]) -> dict[str, object]:
            self.posted = (path, body)
            return {
                "id": "019b1234-5678-7000-8000-000000000002",
                "content_type": "image/png",
                "original_filename": "incident.png",
            }

    api = Api()
    uploaded = await upload_media(
        api,  # type: ignore[arg-type]
        "workspace",
        {
            "file": {
                "download_url": "https://files.openai.example/generated.png?token=short-lived",
                "file_id": "file_generated_123",
                "mime_type": "image/png",
                "file_name": "incident.png",
            },
            "alt_text": "AI-assisted incident investigation",
        },
    )
    assert uploaded["id"] == "019b1234-5678-7000-8000-000000000002"
    assert api.posted == (
        "/workspaces/workspace/media/ingest",
        {
            "remote_url": "https://files.openai.example/generated.png?token=short-lived",
            "content_type": "image/png",
            "filename": "incident.png",
            "alt_text": "AI-assisted incident investigation",
        },
    )

    tool = next(tool for tool in TOOLS if tool.name == "upload_media").describe()
    assert tool["_meta"]["openai/fileParams"] == ["file"]
    schema = tool["inputSchema"]["$defs"]["OpenAIFile"]
    assert schema["required"] == ["download_url", "file_id"]
    assert set(schema["properties"]) == {
        "download_url",
        "file_id",
        "mime_type",
        "file_name",
    }
