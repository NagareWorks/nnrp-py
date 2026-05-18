"""Unit tests for FrameResultBuffer."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from nnrp.tools.ordered_buffer import FrameResultBuffer


def _packet(frame_id: int) -> SimpleNamespace:
    return SimpleNamespace(frame_id=frame_id)


class TestFrameResultBufferSync:
    def test_add_and_pop_single(self) -> None:
        buf = FrameResultBuffer()
        p = _packet(1)
        buf.add(frame_id=1, packet=p)
        assert len(buf) == 1
        assert buf.pop_next() is p
        assert len(buf) == 0

    def test_pop_next_returns_smallest_frame_id(self) -> None:
        buf = FrameResultBuffer()
        p3 = _packet(3)
        p1 = _packet(1)
        p2 = _packet(2)
        buf.add(frame_id=3, packet=p3)
        buf.add(frame_id=1, packet=p1)
        buf.add(frame_id=2, packet=p2)

        assert buf.pop_next() is p1
        assert buf.pop_next() is p2
        assert buf.pop_next() is p3
        assert buf.pop_next() is None

    def test_pop_next_expected_frame_id_match(self) -> None:
        buf = FrameResultBuffer()
        p1 = _packet(1)
        p2 = _packet(2)
        buf.add(frame_id=1, packet=p1)
        buf.add(frame_id=2, packet=p2)

        assert buf.pop_next(expected_frame_id=1) is p1
        assert buf.pop_next(expected_frame_id=2) is p2

    def test_pop_next_expected_frame_id_mismatch_returns_none(self) -> None:
        buf = FrameResultBuffer()
        p3 = _packet(3)
        buf.add(frame_id=3, packet=p3)

        assert buf.pop_next(expected_frame_id=1) is None
        # Packet is still in buffer.
        assert len(buf) == 1
        assert buf.pop_next() is p3

    def test_pop_frame_exact(self) -> None:
        buf = FrameResultBuffer()
        p5 = _packet(5)
        p7 = _packet(7)
        buf.add(frame_id=5, packet=p5)
        buf.add(frame_id=7, packet=p7)

        assert buf.pop_frame(5) is p5
        assert buf.pop_frame(5) is None
        assert len(buf) == 1

    def test_add_replaces_existing_frame_id(self) -> None:
        buf = FrameResultBuffer()
        p_old = _packet(10)
        p_new = _packet(10)
        buf.add(frame_id=10, packet=p_old)
        buf.add(frame_id=10, packet=p_new)

        assert len(buf) == 1
        assert buf.pop_next() is p_new

    def test_peek_next_frame_id(self) -> None:
        buf = FrameResultBuffer()
        assert buf.peek_next_frame_id() is None
        buf.add(frame_id=7, packet=_packet(7))
        buf.add(frame_id=3, packet=_packet(3))
        assert buf.peek_next_frame_id() == 3

    def test_bool_and_len(self) -> None:
        buf = FrameResultBuffer()
        assert not buf
        assert len(buf) == 0
        buf.add(frame_id=1, packet=_packet(1))
        assert buf
        assert len(buf) == 1
        buf.pop_next()
        assert not buf


class TestFrameResultBufferAsync:
    @pytest.mark.asyncio
    async def test_wait_for_frame_already_present(self) -> None:
        buf = FrameResultBuffer()
        p = _packet(5)
        buf.add(frame_id=5, packet=p)
        result = await buf.wait_for_frame(5, timeout=1.0)
        assert result is p

    @pytest.mark.asyncio
    async def test_wait_for_frame_arrives_later(self) -> None:
        buf = FrameResultBuffer()
        p = _packet(42)

        async def _producer() -> None:
            await asyncio.sleep(0.05)
            buf.add(frame_id=42, packet=p)

        asyncio.create_task(_producer())
        result = await buf.wait_for_frame(42, timeout=2.0)
        assert result is p

    @pytest.mark.asyncio
    async def test_wait_for_frame_timeout(self) -> None:
        buf = FrameResultBuffer()
        result = await buf.wait_for_frame(99, timeout=0.05)
        assert result is None

    @pytest.mark.asyncio
    async def test_wait_for_next_ordered_delivery(self) -> None:
        buf = FrameResultBuffer()
        p3 = _packet(3)
        p1 = _packet(1)
        p2 = _packet(2)

        # Feed out-of-order; consume in order.
        buf.add(frame_id=3, packet=p3)
        buf.add(frame_id=1, packet=p1)
        buf.add(frame_id=2, packet=p2)

        results = []
        for _ in range(3):
            r = await buf.wait_for_next(timeout=1.0)
            assert r is not None
            results.append(r.frame_id)
        assert results == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_wait_for_next_arrives_later(self) -> None:
        buf = FrameResultBuffer()
        p = _packet(7)

        async def _producer() -> None:
            await asyncio.sleep(0.05)
            buf.add(frame_id=7, packet=p)

        asyncio.create_task(_producer())
        result = await buf.wait_for_next(timeout=2.0)
        assert result is p

    @pytest.mark.asyncio
    async def test_wait_for_next_timeout(self) -> None:
        buf = FrameResultBuffer()
        result = await buf.wait_for_next(timeout=0.05)
        assert result is None
