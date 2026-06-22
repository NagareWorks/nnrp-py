import asyncio
import ipaddress
import socket
import ssl
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from aioquic.quic.events import StopSendingReceived
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from nnrp.adapters import (
    NNRP_CURRENT_ALPN,
    NnrpQuicConnectionClosedError,
    NnrpQuicProtocolError,
    alpn_for_wire_format,
    connect_quic,
    create_quic_client_configuration,
    create_quic_server_configuration,
    serve_quic,
)
from nnrp.adapters.quic import (
    NnrpQuicConnection,
    _wait_for_first,
    _wait_for_queue_or_event,
)
from nnrp.core import (
    TENSOR_PROFILE_CACHE_OBJECT_BITMAP,
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
    build_frame_cancel_packet,
    build_frame_submit_packet,
    build_ping_packet,
    build_pong_packet,
    build_result_drop_packet,
    build_result_push_packet,
    build_transport_probe_ack_packet,
    build_transport_probe_packet,
    unpack_tensor_body,
)
from nnrp.runtime import (
    ObjectReferenceMetadata,
    PressureMetadata,
    ProgressMetadata,
    decode_runtime_control_metadata,
    decode_runtime_object_metadata,
    encode_runtime_control_metadata,
)


def test_quic_loopback_control_stream_and_datagram() -> None:
    asyncio.run(_run_quic_loopback())


def test_quic_loopback_submit_and_result_streams() -> None:
    asyncio.run(_run_quic_data_loopback())


def test_quic_receive_raises_on_remote_close() -> None:
    asyncio.run(_run_quic_remote_close())


def test_quic_rejects_truncated_packet_at_stream_end() -> None:
    asyncio.run(_run_quic_truncated_stream())


def test_quic_connect_raises_on_alpn_mismatch() -> None:
    asyncio.run(_run_quic_alpn_mismatch())


def test_quic_control_stream_accepts_multiple_packets() -> None:
    asyncio.run(_run_quic_multiple_control_packets())


def test_quic_control_stream_preserves_client_hello_and_ack_bodies() -> None:
    asyncio.run(_run_quic_control_bodies())


def test_quic_typed_control_and_result_drop_mapping() -> None:
    asyncio.run(_run_quic_typed_control_and_result_drop_mapping())


def test_quic_current_transport_probe_loopback() -> None:
    asyncio.run(_run_quic_transport_probe_loopback())


def test_quic_preview4_runtime_control_and_object_packets_share_control_stream() -> None:
    asyncio.run(_run_quic_preview4_runtime_control_and_object_loopback())


def test_quic_configuration_exposes_explicit_idle_timeout() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))

        client_configuration = create_quic_client_configuration(idle_timeout=135.0)
        server_configuration = create_quic_server_configuration(
            certificate_path,
            private_key_path,
            idle_timeout=135.0,
        )

        assert client_configuration.idle_timeout == pytest.approx(135.0)
        assert server_configuration.idle_timeout == pytest.approx(135.0)


def test_quic_client_configuration_verifies_peers_by_default() -> None:
    client_configuration = create_quic_client_configuration()

    assert client_configuration.verify_mode is ssl.CERT_REQUIRED


def test_quic_client_configuration_requires_explicit_insecure_opt_in() -> None:
    client_configuration = create_quic_client_configuration(insecure_skip_verify=True)

    assert client_configuration.verify_mode is ssl.CERT_NONE


def test_quic_configuration_uses_current_alpn() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))

        client_configuration = create_quic_client_configuration(wire_format=WireFormat.CURRENT)
        server_configuration = create_quic_server_configuration(
            certificate_path,
            private_key_path,
            wire_format=WireFormat.CURRENT,
        )

        assert client_configuration.alpn_protocols == [NNRP_CURRENT_ALPN]
        assert server_configuration.alpn_protocols == [NNRP_CURRENT_ALPN]


def test_alpn_for_wire_format_maps_current() -> None:
    assert alpn_for_wire_format(WireFormat.CURRENT) == NNRP_CURRENT_ALPN


def test_wait_for_queue_or_event_handles_cancellation() -> None:
    asyncio.run(_run_wait_for_queue_or_event_cancellation())


def test_wait_for_first_handles_cancellation() -> None:
    asyncio.run(_run_wait_for_first_cancellation())


def test_quic_ignores_stop_sending_event() -> None:
    protocol = object.__new__(NnrpQuicConnection)
    protocol._stream_buffers = {3: bytearray(b"partial-packet")}
    protocol._terminal_error = None
    protocol._terminated = asyncio.Event()

    protocol.quic_event_received(StopSendingReceived(error_code=0, stream_id=3))

    assert 3 not in protocol._stream_buffers
    assert protocol._terminal_error is None
    assert protocol._terminated.is_set() is False


async def _run_quic_loopback() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        port = _find_free_udp_port()
        server_configuration = create_quic_server_configuration(certificate_path, private_key_path)
        client_configuration = _create_test_client_configuration(certificate_path)

        async with serve_quic("127.0.0.1", port, configuration=server_configuration) as listener:
            async with connect_quic("127.0.0.1", port, configuration=client_configuration) as client:
                server = await listener.accept(timeout=5.0)

                await client.send_control_packet(_build_client_hello_packet())
                hello_packet = await server.receive_control_packet(timeout=5.0)
                hello_metadata = ClientHelloMetadata.unpack(hello_packet.metadata)
                assert hello_packet.header.wire_format is WireFormat.CURRENT
                assert hello_packet.header.msg_type is MessageType.CLIENT_HELLO
                assert hello_metadata.max_lane_count == 2
                assert hello_metadata.supported_wire_format_bitmap == 0x0001

                await server.send_control_packet(_build_server_hello_ack_packet(session_id=42))
                ack_packet = await client.receive_control_packet(timeout=5.0)
                ack_metadata = ServerHelloAckMetadata.unpack(ack_packet.metadata)
                assert ack_packet.header.wire_format is WireFormat.CURRENT
                assert ack_packet.header.msg_type is MessageType.SERVER_HELLO_ACK
                assert ack_metadata.session_id == 42

                client.send_datagram(b"nnrp-datagram")
                assert await server.receive_datagram(timeout=5.0) == b"nnrp-datagram"


async def _run_quic_data_loopback() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        port = _find_free_udp_port()
        server_configuration = create_quic_server_configuration(certificate_path, private_key_path)
        client_configuration = _create_test_client_configuration(certificate_path)

        async with serve_quic("127.0.0.1", port, configuration=server_configuration) as listener:
            async with connect_quic("127.0.0.1", port, configuration=client_configuration) as client:
                server = await listener.accept(timeout=5.0)

                submit_packet = _build_frame_submit_packet()
                submit_stream_id = await client.send_submit_packet(submit_packet)
                received_submit = await server.receive_submit_packet(timeout=5.0)
                submit_metadata = FrameSubmitMetadata.unpack(received_submit.metadata)

                assert submit_stream_id & 0x02
                assert received_submit.header.msg_type is MessageType.FRAME_SUBMIT
                assert received_submit.body[: submit_metadata.camera_bytes] == b"camera!!"
                submit_tensor_body = unpack_tensor_body(
                    received_submit.body[submit_metadata.camera_bytes :],
                    tile_index_bytes=submit_metadata.tile_index_bytes,
                    section_count=submit_metadata.section_count,
                    tile_count=submit_metadata.tile_count,
                )
                assert received_submit.body == submit_packet.body
                assert submit_tensor_body.sections[0].tile_lengths() == (2, 2, 2)

                result_packet = _build_result_push_packet(
                    session_id=received_submit.header.session_id,
                    frame_id=received_submit.header.frame_id,
                    view_id=received_submit.header.view_id,
                )
                result_stream_id = await server.send_result_packet(result_packet)
                received_result = await client.receive_result_packet(timeout=5.0)
                result_metadata = ResultPushMetadata.unpack(received_result.metadata)

                assert result_stream_id & 0x02
                assert received_result.header.msg_type is MessageType.RESULT_PUSH
                result_tensor_body = unpack_tensor_body(
                    received_result.body,
                    tile_index_bytes=result_metadata.tile_index_bytes,
                    section_count=result_metadata.section_count,
                    tile_count=result_metadata.tile_count,
                )
                assert received_result.body == result_packet.body
                assert result_tensor_body.sections[0].tile_lengths() == (2, 2, 2)


async def _run_quic_remote_close() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        port = _find_free_udp_port()
        server_configuration = create_quic_server_configuration(certificate_path, private_key_path)
        client_configuration = _create_test_client_configuration(certificate_path)

        async with serve_quic("127.0.0.1", port, configuration=server_configuration) as listener:
            async with connect_quic("127.0.0.1", port, configuration=client_configuration) as client:
                server = await listener.accept(timeout=5.0)
                server.close(error_code=1, reason_phrase="test shutdown")

                with pytest.raises(NnrpQuicConnectionClosedError, match="connection terminated"):
                    await client.receive_control_packet(timeout=5.0)


async def _run_quic_truncated_stream() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        port = _find_free_udp_port()
        server_configuration = create_quic_server_configuration(certificate_path, private_key_path)
        client_configuration = _create_test_client_configuration(certificate_path)

        async with serve_quic("127.0.0.1", port, configuration=server_configuration) as listener:
            async with connect_quic("127.0.0.1", port, configuration=client_configuration) as client:
                server = await listener.accept(timeout=5.0)

                stream_id = await client.ensure_control_stream()
                packet_bytes = _build_client_hello_packet().pack()
                client._quic.send_stream_data(stream_id, packet_bytes[:-3], end_stream=True)
                client.transmit()

                with pytest.raises(NnrpQuicProtocolError, match="incomplete packet"):
                    await server.receive_control_packet(timeout=5.0)


async def _run_quic_alpn_mismatch() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        port = _find_free_udp_port()
        server_configuration = create_quic_server_configuration(certificate_path, private_key_path)
        client_configuration = _create_test_client_configuration(certificate_path, alpn_protocols=["nnrp/0-test"])

        async with serve_quic("127.0.0.1", port, configuration=server_configuration):
            with pytest.raises(NnrpQuicConnectionClosedError, match="connection terminated"):
                async with connect_quic("127.0.0.1", port, configuration=client_configuration):
                    pass


async def _run_quic_multiple_control_packets() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        port = _find_free_udp_port()
        server_configuration = create_quic_server_configuration(certificate_path, private_key_path)
        client_configuration = _create_test_client_configuration(certificate_path)

        async with serve_quic("127.0.0.1", port, configuration=server_configuration) as listener:
            async with connect_quic("127.0.0.1", port, configuration=client_configuration) as client:
                server = await listener.accept(timeout=5.0)

                await client.send_control_packet(_build_client_hello_packet(requested_session_id=11))
                first_stream_id = client.control_stream_id
                await client.send_control_packet(_build_client_hello_packet(requested_session_id=29))

                first_packet = await server.receive_control_packet(timeout=5.0)
                second_packet = await server.receive_control_packet(timeout=5.0)

                assert first_stream_id is not None
                assert client.control_stream_id == first_stream_id
                assert ClientHelloMetadata.unpack(first_packet.metadata).requested_session_id == 11
                assert ClientHelloMetadata.unpack(second_packet.metadata).requested_session_id == 29


async def _run_quic_control_bodies() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        port = _find_free_udp_port()
        server_configuration = create_quic_server_configuration(certificate_path, private_key_path)
        client_configuration = _create_test_client_configuration(certificate_path)

        async with serve_quic("127.0.0.1", port, configuration=server_configuration) as listener:
            async with connect_quic("127.0.0.1", port, configuration=client_configuration) as client:
                server = await listener.accept(timeout=5.0)

                hello_packet = _build_client_hello_packet(requested_session_id=31, auth_block=b"conv-lite")
                await client.send_control_packet(hello_packet)
                received_hello = await server.receive_control_packet(timeout=5.0)
                hello_metadata = ClientHelloMetadata.unpack(received_hello.metadata)
                assert hello_metadata.requested_session_id == 31
                assert hello_metadata.auth_bytes == len(b"conv-lite")
                assert received_hello.body == b"conv-lite"

                ack_packet = _build_server_hello_ack_packet(session_id=52, body=b"imdn-x2-tile32")
                await server.send_control_packet(ack_packet)
                received_ack = await client.receive_control_packet(timeout=5.0)
                ack_metadata = ServerHelloAckMetadata.unpack(received_ack.metadata)
                assert ack_metadata.session_id == 52
                assert received_ack.body == b"imdn-x2-tile32"


async def _run_quic_typed_control_and_result_drop_mapping() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        port = _find_free_udp_port()
        server_configuration = create_quic_server_configuration(certificate_path, private_key_path)
        client_configuration = _create_test_client_configuration(certificate_path)

        async with serve_quic("127.0.0.1", port, configuration=server_configuration) as listener:
            async with connect_quic("127.0.0.1", port, configuration=client_configuration) as client:
                server = await listener.accept(timeout=5.0)

                await client.send_control_packet(build_ping_packet(session_id=11, trace_id=101))
                control_stream_id = client.control_stream_id
                await client.send_control_packet(
                    build_frame_cancel_packet(
                        session_id=11,
                        frame_id=77,
                        view_id=2,
                        trace_id=102,
                        flags=HeaderFlags.CAN_DROP,
                    )
                )

                received_ping = await server.receive_control_packet(timeout=5.0)
                received_cancel = await server.receive_control_packet(timeout=5.0)
                assert control_stream_id is not None
                assert client.control_stream_id == control_stream_id
                assert received_ping.header.msg_type is MessageType.PING
                assert received_cancel.header.msg_type is MessageType.FRAME_CANCEL
                assert received_cancel.header.frame_id == 77
                assert received_cancel.header.view_id == 2

                await server.send_control_packet(build_pong_packet(session_id=11, trace_id=101))
                received_pong = await client.receive_control_packet(timeout=5.0)
                assert received_pong.header.msg_type is MessageType.PONG

                result_drop_stream_id = await server.send_result_packet(
                    build_result_drop_packet(
                        session_id=11,
                        frame_id=77,
                        view_id=2,
                        trace_id=103,
                        flags=HeaderFlags.CAN_DROP,
                    )
                )
                received_drop = await client.receive_result_packet(timeout=5.0)

                assert result_drop_stream_id & 0x02
                assert result_drop_stream_id != control_stream_id
                assert client.last_result_stream_id == result_drop_stream_id
                assert received_drop.header.msg_type is MessageType.RESULT_DROP
                assert received_drop.header.frame_id == 77
                assert received_drop.header.meta_len == 0
                assert received_drop.header.body_len == 0


async def _run_quic_transport_probe_loopback() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        port = _find_free_udp_port()
        server_configuration = create_quic_server_configuration(certificate_path, private_key_path)
        client_configuration = _create_test_client_configuration(certificate_path, wire_format=WireFormat.CURRENT)
        probe_body = b"q" * 96
        probe_metadata = TransportProbeMetadata(
            probe_id=101,
            probe_payload_bytes=len(probe_body),
            client_send_ts_us=123456,
        )
        ack_metadata = TransportProbeAckMetadata(
            probe_id=101,
            reserved=0,
            server_recv_ts_us=123556,
        )

        async with serve_quic("127.0.0.1", port, configuration=server_configuration) as listener:
            async with connect_quic("127.0.0.1", port, configuration=client_configuration) as client:
                server = await listener.accept(timeout=5.0)

                await client.send_control_packet(
                    build_transport_probe_packet(metadata=probe_metadata, body=probe_body, trace_id=88)
                )
                received_probe = await server.receive_control_packet(timeout=5.0)

                assert received_probe.header.msg_type is MessageType.TRANSPORT_PROBE
                assert TransportProbeMetadata.unpack(received_probe.metadata) == probe_metadata
                assert received_probe.body == probe_body

                await server.send_control_packet(build_transport_probe_ack_packet(metadata=ack_metadata, trace_id=89))
                received_ack = await client.receive_control_packet(timeout=5.0)

                assert received_ack.header.msg_type is MessageType.TRANSPORT_PROBE_ACK
                assert received_ack.header.body_len == 0
                assert TransportProbeAckMetadata.unpack(received_ack.metadata) == ack_metadata


async def _run_quic_preview4_runtime_control_and_object_loopback() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        port = _find_free_udp_port()
        server_configuration = create_quic_server_configuration(certificate_path, private_key_path)
        client_configuration = _create_test_client_configuration(certificate_path, wire_format=WireFormat.CURRENT)
        packets = _build_preview4_runtime_control_packets(session_id=314)

        async with serve_quic("127.0.0.1", port, configuration=server_configuration) as listener:
            async with connect_quic("127.0.0.1", port, configuration=client_configuration) as client:
                server = await listener.accept(timeout=5.0)

                for packet in packets:
                    await client.send_control_packet(packet)

                control_stream_id = client.control_stream_id
                received_progress = await server.receive_control_packet(timeout=5.0)
                received_pressure = await server.receive_control_packet(timeout=5.0)
                received_object_ref = await server.receive_control_packet(timeout=5.0)

                assert control_stream_id is not None
                assert client.control_stream_id == control_stream_id
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


async def _run_wait_for_queue_or_event_cancellation() -> None:
    queue: asyncio.Queue[str] = asyncio.Queue()
    event = asyncio.Event()
    task = asyncio.create_task(_wait_for_queue_or_event(queue, event, None))

    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def _run_wait_for_first_cancellation() -> None:
    primary = asyncio.Event()
    secondary = asyncio.Event()
    task = asyncio.create_task(_wait_for_first(primary.wait(), secondary.wait(), None))

    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


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


def _build_result_push_packet(*, session_id: int, frame_id: int, view_id: int) -> NnrpPacket:
    return build_result_push_packet(
        session_id=session_id,
        frame_id=frame_id,
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
        active_profile_id=2,
        inference_ms=17,
        queue_ms=2,
        server_total_ms=19,
        tile_index_mode=TileIndexMode.RAW_U16,
        view_id=view_id,
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


def _find_free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _create_test_client_configuration(certificate_path: Path, **kwargs) -> object:
    return create_quic_client_configuration(cafile=certificate_path, **kwargs)


def _write_self_signed_certificate(target_dir: Path) -> tuple[Path, Path]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "NNRP"),
            x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1"),
        ]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=7))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    certificate_path = target_dir / "quic-cert.pem"
    private_key_path = target_dir / "quic-key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return certificate_path, private_key_path
