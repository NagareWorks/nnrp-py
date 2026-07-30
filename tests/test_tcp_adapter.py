import asyncio
import socket

import pytest

from nnrp.adapters import (
    NnrpTcpUnsupportedOperationError,
    connect_tcp,
    create_tcp_client_configuration,
    create_tcp_server_configuration,
    serve_tcp,
)
from nnrp.core import (
    TENSOR_PROFILE_CACHE_OBJECT_BITMAP,
    CacheObjectKind,
    ClientHelloMetadata,
    FrameSubmitMetadata,
    HeaderFlags,
    InputProfile,
    MessageType,
    NnrpPacket,
    ResultFlags,
    ResultPushMetadata,
    ServerHelloAckMetadata,
    TensorDType,
    TensorSectionData,
    TileIndexMode,
    TransportProbeAckMetadata,
    TransportProbeMetadata,
    WireFormat,
    build_frame_submit_packet,
    build_ping_packet,
    build_pong_packet,
    build_result_push_packet,
    build_transport_probe_ack_packet,
    build_transport_probe_packet,
    unpack_current_tensor_body,
    unpack_inline_object_blocks,
    validate_frame_submit_body,
    validate_result_push_body,
)
from nnrp.runtime import (
    ObjectReferenceMetadata,
    PressureMetadata,
    ProgressMetadata,
    decode_runtime_control_metadata,
    decode_runtime_object_metadata,
    encode_runtime_control_metadata,
)


def test_tcp_loopback_control_stream_preserves_packet_boundaries() -> None:
    asyncio.run(_run_tcp_loopback())


def test_tcp_configuration_exposes_explicit_timeout_and_nodelay_knobs() -> None:
    client_configuration = create_tcp_client_configuration(
        connect_timeout=7.5,
        idle_timeout=135.0,
        no_delay=False,
    )
    server_configuration = create_tcp_server_configuration(
        idle_timeout=140.0,
        no_delay=False,
    )

    assert client_configuration.connect_timeout == pytest.approx(7.5)
    assert client_configuration.idle_timeout == pytest.approx(135.0)
    assert client_configuration.no_delay is False
    assert server_configuration.idle_timeout == pytest.approx(140.0)
    assert server_configuration.no_delay is False


def test_tcp_loopback_supports_submit_and_result_packets() -> None:
    asyncio.run(_run_tcp_current_packet_loopback())


def test_tcp_rejects_quic_only_datagram_api() -> None:
    asyncio.run(_run_tcp_unsupported_operations())


def test_tcp_current_transport_probe_loopback() -> None:
    asyncio.run(_run_tcp_transport_probe_loopback())


def test_tcp_preview4_runtime_control_and_object_packets_share_control_stream() -> None:
    asyncio.run(_run_tcp_preview4_runtime_control_and_object_loopback())


async def _run_tcp_loopback() -> None:
    port = _find_free_tcp_port()

    async with serve_tcp("127.0.0.1", port, configuration=create_tcp_server_configuration()) as listener:
        async with connect_tcp("127.0.0.1", port, configuration=create_tcp_client_configuration()) as client:
            server = await listener.accept(timeout=5.0)

            await client.send_control_packet(
                _build_client_hello_packet(requested_session_id=42, auth_block=b"tcp-auth")
            )
            await client.send_control_packet(build_ping_packet(session_id=42, trace_id=7))

            hello_packet = await server.receive_control_packet(timeout=5.0)
            ping_packet = await server.receive_control_packet(timeout=5.0)

            hello_metadata = ClientHelloMetadata.unpack(hello_packet.metadata)
            assert client.control_stream_id == 0
            assert server.control_stream_id == 0
            assert hello_packet.header.wire_format is WireFormat.CURRENT
            assert hello_packet.header.msg_type is MessageType.CLIENT_HELLO
            assert hello_metadata.requested_session_id == 42
            assert hello_metadata.supported_wire_format_bitmap == 0x0001
            assert hello_packet.body == b"tcp-auth"
            assert ping_packet.header.msg_type is MessageType.PING

            await server.send_control_packet(_build_server_hello_ack_packet(session_id=42, body=b"tcp-ok"))
            await server.send_control_packet(build_pong_packet(session_id=42, trace_id=7))

            ack_packet = await client.receive_control_packet(timeout=5.0)
            pong_packet = await client.receive_control_packet(timeout=5.0)

            ack_metadata = ServerHelloAckMetadata.unpack(ack_packet.metadata)
            assert ack_packet.header.wire_format is WireFormat.CURRENT
            assert ack_packet.header.msg_type is MessageType.SERVER_HELLO_ACK
            assert ack_metadata.session_id == 42
            assert ack_packet.body == b"tcp-ok"
            assert pong_packet.header.msg_type is MessageType.PONG


async def _run_tcp_unsupported_operations() -> None:
    port = _find_free_tcp_port()

    async with serve_tcp("127.0.0.1", port) as listener:
        async with connect_tcp("127.0.0.1", port) as client:
            await listener.accept(timeout=5.0)

            with pytest.raises(NnrpTcpUnsupportedOperationError):
                client.send_datagram(b"not-supported")


async def _run_tcp_current_packet_loopback() -> None:
    port = _find_free_tcp_port()

    async with serve_tcp("127.0.0.1", port, configuration=create_tcp_server_configuration()) as listener:
        async with connect_tcp("127.0.0.1", port, configuration=create_tcp_client_configuration()) as client:
            server = await listener.accept(timeout=5.0)

            submit_packet = _build_frame_submit_packet()
            submit_stream_id = await client.send_submit_packet(submit_packet)
            received_submit = await server.receive_submit_packet(timeout=5.0)
            submit_metadata = FrameSubmitMetadata.unpack(received_submit.metadata)

            assert submit_stream_id & 0x02
            assert server.last_submit_stream_id == submit_stream_id
            assert received_submit.header.wire_format is WireFormat.CURRENT
            assert received_submit.header.msg_type is MessageType.FRAME_SUBMIT
            assert submit_metadata == FrameSubmitMetadata.unpack(submit_packet.metadata)
            submit_body = validate_frame_submit_body(submit_metadata, received_submit.body)
            submit_blocks = {
                block.header.object_kind: block
                for block in unpack_inline_object_blocks(submit_body.inline_object_region)
            }
            assert bytes(submit_blocks[CacheObjectKind.CAMERA_BLOCK].payload) == b"camera!!"
            submit_tensor_body = unpack_current_tensor_body(
                submit_body,
                section_count=submit_metadata.section_count,
                tile_count=submit_metadata.tile_count,
            )
            assert received_submit.body == submit_packet.body
            assert submit_tensor_body.sections[0].tile_lengths() == (2, 2, 2)

            result_packet = _build_result_push_packet()
            result_stream_id = await server.send_result_packet(result_packet)
            received_result = await client.receive_result_packet(timeout=5.0)
            result_metadata = ResultPushMetadata.unpack(received_result.metadata)

            assert result_stream_id & 0x02
            assert client.last_result_stream_id == result_stream_id
            assert received_result.header.wire_format is WireFormat.CURRENT
            assert received_result.header.msg_type is MessageType.RESULT_PUSH
            assert result_metadata == ResultPushMetadata.unpack(result_packet.metadata)
            result_tensor_body = unpack_current_tensor_body(
                validate_result_push_body(result_metadata, received_result.body),
                section_count=result_metadata.section_count,
                tile_count=result_metadata.tile_count,
            )
            assert received_result.body == result_packet.body
            assert result_tensor_body.sections[0].tile_lengths() == (2, 2, 2)


async def _run_tcp_transport_probe_loopback() -> None:
    port = _find_free_tcp_port()
    probe_body = b"t" * 80
    probe_metadata = TransportProbeMetadata(
        probe_id=201,
        probe_payload_bytes=len(probe_body),
        client_send_ts_us=222000,
    )
    ack_metadata = TransportProbeAckMetadata(
        probe_id=201,
        reserved=0,
        server_recv_ts_us=222080,
    )

    async with serve_tcp("127.0.0.1", port, configuration=create_tcp_server_configuration()) as listener:
        async with connect_tcp("127.0.0.1", port, configuration=create_tcp_client_configuration()) as client:
            server = await listener.accept(timeout=5.0)

            await client.send_control_packet(
                build_transport_probe_packet(metadata=probe_metadata, body=probe_body, trace_id=91)
            )
            received_probe = await server.receive_control_packet(timeout=5.0)

            assert received_probe.header.msg_type is MessageType.TRANSPORT_PROBE
            assert TransportProbeMetadata.unpack(received_probe.metadata) == probe_metadata
            assert received_probe.body == probe_body

            await server.send_control_packet(build_transport_probe_ack_packet(metadata=ack_metadata, trace_id=92))
            received_ack = await client.receive_control_packet(timeout=5.0)

            assert received_ack.header.msg_type is MessageType.TRANSPORT_PROBE_ACK
            assert received_ack.header.body_len == 0
            assert TransportProbeAckMetadata.unpack(received_ack.metadata) == ack_metadata


async def _run_tcp_preview4_runtime_control_and_object_loopback() -> None:
    port = _find_free_tcp_port()
    packets = _build_preview4_runtime_control_packets(session_id=313)

    async with serve_tcp("127.0.0.1", port, configuration=create_tcp_server_configuration()) as listener:
        async with connect_tcp("127.0.0.1", port, configuration=create_tcp_client_configuration()) as client:
            server = await listener.accept(timeout=5.0)

            for packet in packets:
                await client.send_control_packet(packet)

            received_progress = await server.receive_control_packet(timeout=5.0)
            received_pressure = await server.receive_control_packet(timeout=5.0)
            received_object_ref = await server.receive_control_packet(timeout=5.0)

            assert received_progress.header.msg_type is MessageType.PROGRESS
            assert decode_runtime_control_metadata(
                MessageType.PROGRESS,
                received_progress.metadata + received_progress.body,
            ).metadata == ProgressMetadata(41, 1, 7, 2500, 9001, 4)
            assert received_progress.body == b"step"

            assert received_pressure.header.msg_type is MessageType.BACKPRESSURE
            assert decode_runtime_control_metadata(
                MessageType.BACKPRESSURE,
                received_pressure.metadata,
            ).metadata == PressureMetadata(41, 16, 2, 3, 5, 0x03)

            assert received_object_ref.header.msg_type is MessageType.OBJECT_REF
            assert decode_runtime_object_metadata(
                MessageType.OBJECT_REF,
                received_object_ref.metadata + received_object_ref.body,
            ).metadata == ObjectReferenceMetadata(9001, 41, 2, 128, 256, 0x01, 2)
            assert received_object_ref.body == b"md"
            assert client.last_submit_stream_id is None
            assert client.last_result_stream_id is None
            assert server.last_submit_stream_id is None
            assert server.last_result_stream_id is None


def _build_client_hello_packet(*, requested_session_id: int = 0, auth_block: bytes = b"") -> NnrpPacket:
    metadata = ClientHelloMetadata(
        min_version_major=1,
        max_version_major=1,
        supported_wire_format_bitmap=0x0001,
        supported_profile_bitmap=0x0001,
        supported_payload_kind_bitmap=0x0001,
        supported_codec_bitmap=0x0003,
        supported_compression_bitmap=0x0003,
        supported_dtype_bitmap=0x001F,
        supported_layout_bitmap=0x0003,
        cache_digest_bitmap=0x0001,
        cache_object_bitmap=TENSOR_PROFILE_CACHE_OBJECT_BITMAP,
        cache_namespace_count=1,
        max_lane_count=2,
        max_cache_entries=16,
        max_cache_bytes=1024 * 1024,
        target_cadence_x100=6000,
        latency_budget_ms=100,
        quality_tier=2,
        degrade_policy=0,
        requested_session_id=requested_session_id,
        auth_bytes=len(auth_block),
        control_extension_bytes=0,
    ).pack()
    return NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.CLIENT_HELLO,
        flags=HeaderFlags.ACK_REQUIRED,
        metadata=metadata,
        body=auth_block,
    )


def _build_server_hello_ack_packet(*, session_id: int, body: bytes = b"") -> NnrpPacket:
    metadata = ServerHelloAckMetadata(
        selected_version_major=1,
        selected_wire_format=0,
        auth_status=0,
        session_id=session_id,
        accepted_profile_bitmap=0x0001,
        accepted_payload_kind_bitmap=0x0001,
        accepted_codec_bitmap=0x0003,
        accepted_compression_bitmap=0x0003,
        accepted_dtype_bitmap=0x0007,
        accepted_layout_bitmap=0x0001,
        cache_digest_bitmap=0x0001,
        cache_object_bitmap=TENSOR_PROFILE_CACHE_OBJECT_BITMAP,
        max_cache_entries=16,
        max_cache_bytes=1024 * 1024,
        max_lane_count=2,
        max_concurrent_frames=2,
        target_cadence_x100=6000,
        latency_budget_ms=100,
        quality_tier=2,
        degrade_policy=0,
        max_body_bytes=4 * 1024 * 1024,
        token_ttl_ms=300000,
        retry_after_ms=0,
        control_extension_bytes=0,
        server_flags=0x00000001,
    ).pack()
    return NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.SERVER_HELLO_ACK,
        metadata=metadata,
        body=body,
    )


def _build_frame_submit_packet() -> NnrpPacket:
    return build_frame_submit_packet(
        session_id=7,
        frame_id=101,
        operation_id=1001,
        src_width=640,
        src_height=360,
        tile_width=32,
        tile_height=32,
        tile_ids=(0, 1, 2),
        sections=(
            TensorSectionData(
                role_id=1,
                default_codec_id=0,
                dtype_id=TensorDType.UINT8,
                tile_payloads=(b"aa", b"bb", b"cc"),
            ),
        ),
        camera_block=b"camera!!",
        frame_class=0,
        input_profile=InputProfile.DENSE_LUMA_FRAME,
        tile_index_mode=TileIndexMode.RAW_U16,
        latency_budget_ms=50,
        target_fps_x100=6000,
        view_id=1,
    )


def _build_result_push_packet() -> NnrpPacket:
    return build_result_push_packet(
        session_id=7,
        frame_id=101,
        tile_ids=(0, 1, 2),
        sections=(
            TensorSectionData(
                role_id=100,
                default_codec_id=0,
                dtype_id=TensorDType.UINT8,
                tile_payloads=(b"ra", b"rb", b"rc"),
            ),
        ),
        result_flags=ResultFlags.NONE,
        active_profile_id=1,
        inference_ms=11,
        queue_ms=2,
        server_total_ms=13,
        tile_index_mode=TileIndexMode.RAW_U16,
        view_id=1,
    )


def _build_preview4_runtime_control_packets(*, session_id: int) -> tuple[NnrpPacket, NnrpPacket, NnrpPacket]:
    progress = ProgressMetadata(41, 1, 7, 2500, 9001, 4)
    pressure = PressureMetadata(41, 16, 2, 3, 5, 0x03)
    object_ref = ObjectReferenceMetadata(9001, 41, 2, 128, 256, 0x01, 2)
    return (
        NnrpPacket.build(
            version_major=1,
            wire_format=WireFormat.CURRENT,
            msg_type=MessageType.PROGRESS,
            session_id=session_id,
            metadata=progress.pack(),
            body=b"step",
        ),
        NnrpPacket.build(
            version_major=1,
            wire_format=WireFormat.CURRENT,
            msg_type=MessageType.BACKPRESSURE,
            session_id=session_id,
            metadata=encode_runtime_control_metadata(MessageType.BACKPRESSURE, pressure),
        ),
        NnrpPacket.build(
            version_major=1,
            wire_format=WireFormat.CURRENT,
            msg_type=MessageType.OBJECT_REF,
            session_id=session_id,
            metadata=object_ref.pack(),
            body=b"md",
        ),
    )


def _find_free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
