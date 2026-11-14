"""Token buckets in Postgres (M2.12, docs/04 §3.5, ADR-0002: no Redis).

One row per bucket key. A take is one short transaction: ensure the row, lock it, refill by
elapsed time (measured with the database clock, so API replicas never disagree), then
spend. ``cost=0`` peeks without spending.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class Limit:
    capacity: int
    per_seconds: float

    @property
    def refill_per_second(self) -> float:
        return self.capacity / self.per_seconds


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    remaining: int
    retry_after: int  # whole seconds until one token is available; 0 when allowed
    reset_after: int  # whole seconds until the bucket is full again


async def take(
    sessionmaker: async_sessionmaker[AsyncSession], key: str, limit: Limit, *, cost: float = 1.0
) -> Decision:
    async with sessionmaker() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO rate_limit_bucket (key, tokens, refilled_at) "
                "VALUES (:key, :cap, now()) ON CONFLICT (key) DO NOTHING"
            ),
            {"key": key, "cap": float(limit.capacity)},
        )
        tokens, elapsed = (
            await s.execute(
                text(
                    "SELECT tokens, EXTRACT(EPOCH FROM (now() - refilled_at)) "
                    "FROM rate_limit_bucket WHERE key = :key FOR UPDATE"
                ),
                {"key": key},
            )
        ).one()
        rate = limit.refill_per_second
        available = min(float(limit.capacity), float(tokens) + max(float(elapsed), 0.0) * rate)
        allowed = available >= cost
        left = available - cost if allowed else available
        await s.execute(
            text("UPDATE rate_limit_bucket SET tokens = :t, refilled_at = now() WHERE key = :key"),
            {"t": left, "key": key},
        )
    return Decision(
        allowed=allowed,
        remaining=max(int(left), 0),
        retry_after=0 if allowed else max(1, math.ceil((cost - left) / rate)),
        reset_after=max(0, math.ceil((limit.capacity - left) / rate)),
    )


async def refund(
    sessionmaker: async_sessionmaker[AsyncSession], key: str, limit: Limit, *, amount: float = 1.0
) -> None:
    """Give back tokens taken for an attempt that turned out to be legitimate."""
    async with sessionmaker() as s, s.begin():
        await s.execute(
            text(
                "UPDATE rate_limit_bucket SET tokens = LEAST(:cap, tokens + :amount) "
                "WHERE key = :key"
            ),
            {"cap": float(limit.capacity), "amount": amount, "key": key},
        )
