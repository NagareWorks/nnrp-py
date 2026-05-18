"""Client-side ordered result buffer for concurrent multi-frame current sessions.

When a client submits multiple frames without waiting for results, the server
may deliver RESULT_PUSH / RESULT_DROP packets in completion order rather than
frame_id order.  ``FrameResultBuffer`` collects incoming packets and exposes
them to the caller in ascending frame_id order so the application layer sees a
stable, sequential result stream.

Typical usage::

    buf = FrameResultBuffer()
    # Feed packets as they arrive (possibly out of order):
    buf.add(frame_id=3, packet=result_3)
    buf.add(frame_id=1, packet=result_1)
    # Consume in frame_id order:
    assert buf.pop_next(expected_frame_id=1).header.frame_id == 1
    assert buf.pop_next(expected_frame_id=3).header.frame_id == 3

For async receive loops use ``add_from_connection`` together with
``wait_for_frame``.
"""

from __future__ import annotations

import asyncio
import heapq
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FrameResultBuffer:
    """Buffer incoming result packets and expose them in frame_id order.

    Thread-safety: this class is *not* thread-safe.  It is designed for use
    within a single ``asyncio`` event loop where all calls happen from coroutines
    running on the same thread.
    """

    _heap: list[tuple[int, Any]] = field(default_factory=list, init=False, repr=False)
    _by_frame_id: dict[int, Any] = field(default_factory=dict, init=False, repr=False)
    _ready: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)

    def __post_init__(self) -> None:
        # Ensure a fresh Event is created inside the running loop when possible.
        self._ready = asyncio.Event()

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, frame_id: int, packet: Any) -> None:
        """Store *packet* keyed by *frame_id*.

        If a packet for *frame_id* already exists it is silently replaced
        (last-write-wins semantics to handle re-transmit/retry scenarios).
        """
        if frame_id not in self._by_frame_id:
            heapq.heappush(self._heap, (frame_id, id(packet)))  # id keeps heap stable
        self._by_frame_id[frame_id] = packet
        self._ready.set()

    # ------------------------------------------------------------------
    # Synchronous access
    # ------------------------------------------------------------------

    def peek_next_frame_id(self) -> int | None:
        """Return the smallest buffered frame_id without consuming it."""
        while self._heap:
            fid, _ = self._heap[0]
            if fid in self._by_frame_id:
                return fid
            heapq.heappop(self._heap)
        return None

    def pop_next(self, *, expected_frame_id: int | None = None) -> Any | None:
        """Remove and return the packet with the smallest frame_id.

        If *expected_frame_id* is given and the smallest buffered frame_id does
        not match, ``None`` is returned without modifying the buffer.
        """
        while self._heap:
            fid, _ = self._heap[0]
            if fid not in self._by_frame_id:
                heapq.heappop(self._heap)
                continue
            if expected_frame_id is not None and fid != expected_frame_id:
                return None
            heapq.heappop(self._heap)
            return self._by_frame_id.pop(fid)
        return None

    def pop_frame(self, frame_id: int) -> Any | None:
        """Remove and return the packet for an exact *frame_id* if present."""
        packet = self._by_frame_id.pop(frame_id, None)
        if packet is not None:
            # Remove stale heap entry lazily (pop_next will skip it).
            pass
        if not self._by_frame_id:
            self._ready.clear()
        return packet

    def __len__(self) -> int:
        return len(self._by_frame_id)

    def __bool__(self) -> bool:
        return bool(self._by_frame_id)

    # ------------------------------------------------------------------
    # Async access
    # ------------------------------------------------------------------

    async def wait_for_frame(
        self,
        frame_id: int,
        *,
        timeout: float | None = None,
    ) -> Any | None:
        """Wait until a packet for *frame_id* is available, then return it.

        Returns ``None`` if *timeout* expires before the frame arrives.
        The packet is removed from the buffer on return.
        """
        deadline = None
        if timeout is not None:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout

        while True:
            packet = self.pop_frame(frame_id)
            if packet is not None:
                return packet

            if deadline is not None:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return None
                try:
                    await asyncio.wait_for(self._wait_ready(), timeout=remaining)
                except TimeoutError:
                    return None
            else:
                await self._wait_ready()

    async def wait_for_next(self, *, timeout: float | None = None) -> Any | None:
        """Wait until any packet is available, then return the one with the
        smallest frame_id.

        Returns ``None`` if *timeout* expires.  The packet is removed.
        """
        deadline = None
        if timeout is not None:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout

        while True:
            packet = self.pop_next()
            if packet is not None:
                return packet

            if deadline is not None:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return None
                try:
                    await asyncio.wait_for(self._wait_ready(), timeout=remaining)
                except TimeoutError:
                    return None
            else:
                await self._wait_ready()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _wait_ready(self) -> None:
        self._ready.clear()
        await self._ready.wait()
