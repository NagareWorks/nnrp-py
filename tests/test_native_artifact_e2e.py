from __future__ import annotations

import asyncio
import os

import pytest

from nnrp.core import build_ping_packet, build_pong_packet
from nnrp.native import NativeArtifactError, load_native_transport_binding

pytestmark = pytest.mark.skipif(
    os.environ.get("NNRP_NATIVE_E2E") != "1",
    reason="requires prepared preview4 native transport artifacts",
)


@pytest.mark.asyncio
async def test_foreign_artifact_cannot_take_carrier_or_listener_ownership() -> None:
    tcp = load_native_transport_binding("tcp")
    quic = load_native_transport_binding("quic")
    listener = await tcp.listen("tcp://127.0.0.1:0", timeout_ms=10_000)
    accept_task = asyncio.create_task(listener.accept(timeout_ms=10_000))
    client = await tcp.connect(listener.endpoint, timeout_ms=10_000)
    server = await accept_task
    try:
        with pytest.raises(NativeArtifactError, match="owning transport artifact"):
            quic.adopt_client(client, connection_id=1, generation=1)

        ping = build_ping_packet(session_id=1, trace_id=1).pack()
        await client.send(ping)
        assert await server.receive(max_packets=1, timeout_ms=10_000) == (ping,)
    finally:
        await client.close()
        await client.close()
        await server.close()
        await server.close()
        await listener.close()
        await listener.close()

    second_listener = await tcp.listen("tcp://127.0.0.1:0", timeout_ms=10_000)
    try:
        with pytest.raises(NativeArtifactError, match="owning transport artifact"):
            quic.adopt_server(second_listener, server_id=1, generation=1)

        second_accept = asyncio.create_task(second_listener.accept(timeout_ms=10_000))
        second_client = await tcp.connect(second_listener.endpoint, timeout_ms=10_000)
        second_server = await second_accept
        try:
            pong = build_pong_packet(session_id=2, trace_id=2).pack()
            await second_server.send(pong)
            assert await second_client.receive(max_packets=1, timeout_ms=10_000) == (pong,)
        finally:
            await second_client.close()
            await second_server.close()
    finally:
        await second_listener.close()
