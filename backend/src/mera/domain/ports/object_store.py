"""Where bytes live (docs/01 §5.5).

The filesystem store writes to a mounted volume; an S3 store can replace it by
configuration. ``presigned_url`` is how a platform that fetches media by URL (Instagram's
container publishing) reads one rendition: short-lived and signed, never a listing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    byte_size: int


class ObjectNotFound(Exception):
    pass


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> StoredObject: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def presigned_url(self, key: str, ttl: timedelta) -> str: ...
