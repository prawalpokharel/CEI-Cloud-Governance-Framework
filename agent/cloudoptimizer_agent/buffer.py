"""
Offline snapshot buffer.

When the ingest endpoint is unreachable -- a deploy, a network partition, an
outage -- snapshots would otherwise be discarded, leaving a hole in the
history exactly when something interesting was probably happening.

Held in memory rather than on disk. The pod runs with a read-only root
filesystem and only an emptyDir at /tmp, which does not survive a restart
either, so writing to disk would add IO and complexity for no durability
gain. Buffering covers server-side outages, which is the common case; agent
restarts lose the buffer, which is acceptable because the next collection is
at most one interval away.

Bounded on two axes because a memory-limited pod that OOMs while buffering
has converted a recoverable outage into a crash loop.
"""

from __future__ import annotations

import logging
from collections import deque

log = logging.getLogger(__name__)

# ~30 minutes at the default 60s interval.
DEFAULT_MAX_ITEMS = 30

# A 1000-pod cluster gzips to a few hundred KiB; 32 MiB leaves room for the
# item cap to bind first on normal clusters, and protects the pod's memory
# limit on very large ones.
DEFAULT_MAX_BYTES = 32 * 1024 * 1024


class SnapshotBuffer:
    def __init__(
        self,
        max_items: int = DEFAULT_MAX_ITEMS,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ):
        self.max_items = max_items
        self.max_bytes = max_bytes
        self._items: deque[bytes] = deque()
        self._bytes = 0
        self.dropped = 0

    def __len__(self) -> int:
        return len(self._items)

    @property
    def nbytes(self) -> int:
        return self._bytes

    def add(self, payload: bytes) -> None:
        """
        Buffer a snapshot, evicting the OLDEST if the buffer is full.

        Oldest-first eviction is deliberate: when only some observations can
        be kept, recent ones describe the cluster as it is now. A buffer that
        dropped new arrivals would preserve a stale picture and discard the
        current one.
        """
        self._items.append(payload)
        self._bytes += len(payload)

        while self._items and (
            len(self._items) > self.max_items or self._bytes > self.max_bytes
        ):
            evicted = self._items.popleft()
            self._bytes -= len(evicted)
            self.dropped += 1

        if self.dropped and self.dropped % 10 == 0:
            log.warning(
                "Snapshot buffer full; %d observation(s) dropped so far. "
                "The ingest endpoint has been unreachable for a while.",
                self.dropped,
            )

    def drain(self, send) -> int:
        """
        Replay buffered snapshots oldest-first via ``send``.

        Stops at the first failure and keeps the remainder, so a still-broken
        endpoint does not cause the whole backlog to be discarded in one pass.
        Returns the number successfully delivered.
        """
        delivered = 0
        while self._items:
            payload = self._items[0]
            try:
                send(payload)
            except Exception as exc:
                log.warning(
                    "Buffer replay stopped after %d snapshot(s): %s",
                    delivered, exc,
                )
                break
            self._items.popleft()
            self._bytes -= len(payload)
            delivered += 1

        if delivered:
            log.info(
                "Replayed %d buffered snapshot(s); %d still queued",
                delivered, len(self._items),
            )
        return delivered
