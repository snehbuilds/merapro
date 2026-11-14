"""UUIDv7 generation (docs/03 §1): time-sortable keys without exposing a sequence.

Python 3.11 has no ``uuid.uuid7``; this follows RFC 9562 §5.7 with a monotonic counter in
``rand_a`` so IDs minted in the same millisecond still sort in creation order.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_counter = 0
_COUNTER_MAX = 0xFFF


def uuid7() -> uuid.UUID:
    global _last_ms, _counter
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms > _last_ms:
            _last_ms = ms
            _counter = int.from_bytes(os.urandom(2), "big") & 0x7FF  # leave headroom
        else:
            # Same millisecond (or clock went backwards): advance the counter.
            _counter += 1
            if _counter > _COUNTER_MAX:
                _last_ms += 1
                _counter = 0
            ms = _last_ms
        rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
        value = (ms & ((1 << 48) - 1)) << 80
        value |= 0x7 << 76  # version
        value |= _counter << 64
        value |= 0b10 << 62  # variant
        value |= rand_b
        return uuid.UUID(int=value)
