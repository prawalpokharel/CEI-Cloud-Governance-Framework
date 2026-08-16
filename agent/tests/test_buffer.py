"""
Offline buffer behaviour.

The buffer exists so a server outage does not silently punch a hole in a
cluster's history. Its two failure modes are losing the wrong data and
exhausting the pod's memory limit, so both bounds are tested explicitly.
"""

from __future__ import annotations

import pytest

from cloudoptimizer_agent.buffer import SnapshotBuffer


def test_drains_in_observation_order():
    """History must replay oldest-first or the time series is scrambled."""
    buffer = SnapshotBuffer()
    for i in range(3):
        buffer.add(f"snap{i}".encode())

    sent = []
    delivered = buffer.drain(sent.append)

    assert delivered == 3
    assert sent == [b"snap0", b"snap1", b"snap2"]
    assert len(buffer) == 0
    assert buffer.nbytes == 0


def test_evicts_oldest_when_item_bound_is_reached():
    """
    When only some observations fit, the recent ones describe the cluster as
    it is now. Dropping new arrivals would preserve a stale picture.
    """
    buffer = SnapshotBuffer(max_items=3)
    for i in range(5):
        buffer.add(f"snap{i}".encode())

    sent = []
    buffer.drain(sent.append)

    assert sent == [b"snap2", b"snap3", b"snap4"]
    assert buffer.dropped == 2


def test_evicts_on_byte_bound_even_when_item_count_is_fine():
    """A pod that OOMs while buffering turns an outage into a crash loop."""
    buffer = SnapshotBuffer(max_items=100, max_bytes=25)
    for i in range(5):
        buffer.add(b"x" * 10)

    assert buffer.nbytes <= 25
    assert len(buffer) == 2
    assert buffer.dropped == 3


def test_partial_drain_keeps_the_remainder():
    """
    A still-broken endpoint must not cause the whole backlog to be discarded
    in one pass.
    """
    buffer = SnapshotBuffer()
    for i in range(4):
        buffer.add(f"snap{i}".encode())

    sent = []

    def send(payload):
        if len(sent) == 2:
            raise RuntimeError("still down")
        sent.append(payload)

    delivered = buffer.drain(send)

    assert delivered == 2
    assert len(buffer) == 2

    # Recovery replays exactly what was left, in order.
    rest = []
    assert buffer.drain(rest.append) == 2
    assert rest == [b"snap2", b"snap3"]


def test_empty_drain_is_a_noop():
    buffer = SnapshotBuffer()
    assert buffer.drain(lambda _: pytest.fail("should not send")) == 0
