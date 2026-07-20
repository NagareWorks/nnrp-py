import asyncio
import socket

import pytest

from nnrp.adapters import create_tcp_server_configuration, serve_tcp
from nnrp.client import (
    SubmitRequest,
    TypedPayload,
    build_client_hello_packet,
)
from nnrp.client.transport import connect_client_session
from nnrp.core import (
    SERVER_HELLO_ACK_TRANSPORT_POLICY_EXTENSION,
    HeaderFlags,
    InputProfile,
    MessageType,
    NnrpPacket,
    PayloadKind,
    TensorDType,
    TensorSectionData,
    TileIndexMode,
    TransportId,
    parse_server_hello_ack_transport_policy_extension,
    unpack_control_extension_block,
)
from nnrp.runtime import (
    PartialResultMetadata,
    PressureMetadata,
    ProgressMetadata,
    ResultDropReasonCode,
    ResultDropReasonMetadata,
    RuntimeRole,
    decode_runtime_control_metadata,
)
from nnrp.server import (
    ServerProfile,
    ServerSession,
    ServerSessionAcceptResolution,
    accept_server_connection,
    accept_server_session,
)


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FakeServerConnection:
    def __init__(self) -> None:
        self.control_packets: list[NnrpPacket] = []
        self.closed = False
        self.waited = False

    async def send_control_packet(self, packet: NnrpPacket) -> None:
        self.control_packets.append(packet)

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.waited = True


@pytest.mark.asyncio
async def test_server_session_sends_preview4_runtime_control_packets() -> None:
    connection = FakeServerConnection()
    session = ServerSession(
        connection=connection,
        transport_id=TransportId.TCP,
        hello=object(),
        session_id=77,
    )

    await session.send_progress(
        operation_id=100,
        progress_sequence=1,
        stage_code=2,
        percent_x100=2500,
        object_id=333,
        body=b"stage",
        frame_id=9,
        flags=HeaderFlags.ACK_REQUIRED,
        trace_id=123,
    )
    await session.send_partial_result(
        operation_id=100,
        result_sequence=2,
        object_id=333,
        delta_sequence=4,
        body=b"partial",
        control_flags=0x03,
        trace_id=124,
    )
    await session.send_result_drop_reason(
        operation_id=100,
        result_sequence=3,
        drop_reason_code=ResultDropReasonCode.DEADLINE_EXPIRED,
        source_role=RuntimeRole.SERVER,
        diagnostic=b"late",
        trace_id=125,
    )
    await session.send_backpressure(
        scope_id=77,
        credit_window=4,
        pressure_level=2,
        pressure_reason=5,
        retry_after_ms=20,
        control_flags=0x01,
    )
    await session.send_credit_update(
        scope_id=77,
        credit_window=16,
        pressure_level=1,
    )

    assert [packet.header.msg_type for packet in connection.control_packets] == [
        MessageType.PROGRESS,
        MessageType.PARTIAL_RESULT,
        MessageType.RESULT_DROP_REASON,
        MessageType.BACKPRESSURE,
        MessageType.CREDIT_UPDATE,
    ]
    progress_packet = connection.control_packets[0]
    assert progress_packet.header.session_id == 77
    assert progress_packet.header.frame_id == 9
    assert progress_packet.header.flags == HeaderFlags.ACK_REQUIRED
    assert progress_packet.header.trace_id == 123
    progress = decode_runtime_control_metadata(MessageType.PROGRESS, progress_packet.metadata)
    assert progress.metadata == ProgressMetadata(100, 1, 2, 2500, 333, 5)
    assert progress.tail == b"stage"

    partial = decode_runtime_control_metadata(
        MessageType.PARTIAL_RESULT,
        connection.control_packets[1].metadata,
    )
    assert connection.control_packets[1].header.trace_id == 124
    assert partial.metadata == PartialResultMetadata(100, 2, 333, 4, 7, 0x03)
    assert partial.tail == b"partial"

    drop = decode_runtime_control_metadata(
        MessageType.RESULT_DROP_REASON,
        connection.control_packets[2].metadata,
    )
    assert connection.control_packets[2].header.trace_id == 125
    assert drop.metadata == ResultDropReasonMetadata(
        100,
        3,
        ResultDropReasonCode.DEADLINE_EXPIRED,
        RuntimeRole.SERVER,
        0,
        4,
    )
    assert drop.tail == b"late"

    backpressure = decode_runtime_control_metadata(
        MessageType.BACKPRESSURE,
        connection.control_packets[3].metadata,
    )
    assert backpressure.metadata == PressureMetadata(77, 4, 2, 5, 20, 0x01)
    credit = decode_runtime_control_metadata(MessageType.CREDIT_UPDATE, connection.control_packets[4].metadata)
    assert credit.metadata == PressureMetadata(77, 16, 1, 0, 0, 0)


@pytest.mark.asyncio
async def test_accept_server_session_hides_packet_plumbing() -> None:
    host = "127.0.0.1"
    port = _reserve_port()
    server_done = asyncio.Event()

    async with serve_tcp(host, port, configuration=create_tcp_server_configuration()) as listener:

        async def run_server() -> None:
            session = await accept_server_session(
                listener,
                session_id=41,
                active_model_name="engine-sr",
                server_profile=ServerProfile(max_concurrent_frames=2),
            )
            try:
                assert session.hello.auth_block == b"engine-sr"
                submit = await session.receive_submit(timeout=5.0)
                assert submit.request.frame_id == 303
                assert submit.request.tile_ids == (5, 6)
                assert submit.request.input_profile is InputProfile.DENSE_LUMA_FRAME
                assert len(submit.request.sections) == 1

                await session.send_result(
                    frame_id=submit.request.frame_id,
                    tile_ids=submit.request.tile_ids,
                    sections=submit.request.sections,
                    inference_ms=7,
                    queue_ms=2,
                    server_total_ms=11,
                )
            finally:
                await session.close()
                server_done.set()

        server_task = asyncio.create_task(run_server())
        try:
            async with connect_client_session(
                host,
                tcp_port=port,
                requested_model="engine-sr",
                selected_transport_id=TransportId.TCP,
            ) as session:
                await session.send_submit(
                    SubmitRequest(
                        frame_id=303,
                        operation_id=303,
                        src_width=64,
                        src_height=64,
                        tile_width=32,
                        tile_height=32,
                        tile_ids=(5, 6),
                        sections=(
                            TensorSectionData(
                                role_id=1,
                                default_codec_id=0,
                                dtype_id=TensorDType.FP16,
                                tile_payloads=(b"aa", b"bb"),
                            ),
                        ),
                        input_profile=InputProfile.DENSE_LUMA_FRAME,
                        tile_index_mode=TileIndexMode.RAW_U16,
                    )
                )
                result = await session.receive_result(timeout=5.0)
                assert result.packet.header.frame_id == 303
                assert result.metadata.inference_ms == 7
                assert result.metadata.queue_ms == 2
                assert result.metadata.server_total_ms == 11
                assert result.tensor_body is not None
                assert session.control.ack_metadata.control_extension_bytes == len(session.control.ack_packet.body)
                ack_extensions = unpack_control_extension_block(
                    session.control.ack_packet.body,
                    known_types={SERVER_HELLO_ACK_TRANSPORT_POLICY_EXTENSION},
                )
                assert parse_server_hello_ack_transport_policy_extension(
                    ack_extensions[0]
                ).active_transport_id is TransportId.TCP
        finally:
            await server_task
            await asyncio.wait_for(server_done.wait(), timeout=5.0)


@pytest.mark.asyncio
async def test_accept_server_connection_resolves_session_after_probe_or_prefetch() -> None:
    host = "127.0.0.1"
    port = _reserve_port()
    server_done = asyncio.Event()
    observed: dict[str, object] = {}

    async with serve_tcp(host, port, configuration=create_tcp_server_configuration()) as listener:

        async def run_server() -> None:
            connection = await listener.accept(timeout=5.0)
            first_packet = await connection.receive_control_packet(timeout=5.0)

            def resolve_session(hello):
                observed["auth_block"] = hello.auth_block
                observed["requested_session_id"] = hello.metadata.requested_session_id
                return ServerSessionAcceptResolution(session_id=61, active_model_name="resolved-engine")

            session = await accept_server_connection(
                connection,
                first_packet=first_packet,
                server_profile=ServerProfile(max_concurrent_frames=2),
                session_resolver=resolve_session,
                timeout=5.0,
            )
            try:
                assert session.session_id == 61
                assert session.active_model_name == "resolved-engine"
                submit = await session.receive_submit(timeout=5.0)
                await session.send_result(
                    frame_id=submit.request.frame_id,
                    typed_payloads=(TypedPayload.token_chunk(b"ok"),),
                )
            finally:
                await session.close()
                server_done.set()

        server_task = asyncio.create_task(run_server())
        try:
            async with connect_client_session(
                host,
                tcp_port=port,
                requested_model="engine-sr",
                selected_transport_id=TransportId.TCP,
            ) as session:
                assert session.session_id == 61
                assert session.control.ack_metadata.control_extension_bytes == len(session.control.ack_packet.body)
                await session.send_submit(
                    SubmitRequest(
                        frame_id=404,
                        operation_id=404,
                        typed_payloads=(TypedPayload.opaque_bytes(b"input"),),
                    )
                )
                result = await session.receive_result(timeout=5.0)
                assert result.token_chunks == (b"ok",)
        finally:
            await server_task
            await asyncio.wait_for(server_done.wait(), timeout=5.0)

    assert observed["auth_block"] == b"engine-sr"


def test_build_client_hello_packet_accepts_requested_model_without_manual_auth_block() -> None:
    packet = build_client_hello_packet(requested_model="engine-sr")

    assert packet.body == b"engine-sr"


def test_build_client_hello_packet_rejects_requested_model_with_auth_block() -> None:
    with pytest.raises(ValueError, match="requested_model cannot be combined with auth_block"):
        build_client_hello_packet(requested_model="engine-sr", auth_block=b"raw-auth")


def test_typed_payload_rejects_descriptor_flags_on_current_wire() -> None:
    with pytest.raises(ValueError, match="descriptor_flags must be 0"):
        TypedPayload.token_chunk(b"hello", descriptor_flags=1)


def test_typed_payload_to_core_frame_rejects_descriptor_flags_on_current_wire() -> None:
    payload = TypedPayload.token_chunk(b"hello")
    object.__setattr__(payload, "descriptor_flags", 1)

    with pytest.raises(ValueError, match="descriptor_flags must be 0"):
        payload.to_core_frame()


@pytest.mark.asyncio
async def test_current_session_round_trips_typed_and_mixed_payloads_without_core_frames() -> None:
    host = "127.0.0.1"
    port = _reserve_port()
    server_done = asyncio.Event()

    async with serve_tcp(host, port, configuration=create_tcp_server_configuration()) as listener:

        async def run_server() -> None:
            session = await accept_server_session(
                listener,
                session_id=51,
                active_model_name="engine-sr",
                server_profile=ServerProfile(max_concurrent_frames=2),
            )
            try:
                typed_submit = await session.receive_submit(timeout=5.0)
                assert typed_submit.request.frame_id == 304
                assert typed_submit.request.tile_ids == ()
                assert typed_submit.request.sections == ()
                assert typed_submit.request.typed_payloads[0].payload_kind is PayloadKind.TOKEN_CHUNK
                assert typed_submit.request.typed_payloads[0].payload == b"hello"
                assert typed_submit.request.typed_payloads[1].payload_kind is PayloadKind.TOOL_DELTA

                await session.send_result(
                    frame_id=typed_submit.request.frame_id,
                    typed_payloads=(
                        TypedPayload.audio_chunk(b"pcm"),
                        TypedPayload.structured_event(b'{"phase":"typed"}'),
                    ),
                    inference_ms=3,
                )

                mixed_submit = await session.receive_submit(timeout=5.0)
                assert mixed_submit.request.frame_id == 305
                assert mixed_submit.request.tile_ids == (8,)
                assert len(mixed_submit.request.sections) == 1
                assert mixed_submit.request.typed_payloads[0].payload_kind is PayloadKind.TOKEN_CHUNK
                assert mixed_submit.request.typed_payloads[0].payload == b"hint"

                await session.send_result(
                    frame_id=mixed_submit.request.frame_id,
                    tile_ids=mixed_submit.request.tile_ids,
                    sections=mixed_submit.request.sections,
                    typed_payloads=(TypedPayload.opaque_bytes(b"meta"),),
                    inference_ms=5,
                    queue_ms=1,
                    server_total_ms=7,
                )
            finally:
                await session.close()
                server_done.set()

        server_task = asyncio.create_task(run_server())
        try:
            async with connect_client_session(
                host,
                tcp_port=port,
                requested_model="engine-sr",
                selected_transport_id=TransportId.TCP,
            ) as session:
                assert session.control.ack_metadata.accepted_payload_kind_bitmap & int(PayloadKind.TOKEN_CHUNK)

                await session.send_submit(
                    SubmitRequest(
                        frame_id=304,
                        operation_id=304,
                        frame_class=1,
                        latency_budget_ms=12,
                        typed_payloads=(
                            TypedPayload.token_chunk(b"hello"),
                            TypedPayload.tool_delta(b'{"tool":"resize"}'),
                        ),
                    )
                )
                typed_result = await session.receive_result(timeout=5.0)
                assert typed_result.tensor_body is None
                assert typed_result.audio_chunks == (b"pcm",)
                assert typed_result.structured_events == (b'{"phase":"typed"}',)
                assert typed_result.payload_frame_count == 2

                await session.send_submit(
                    SubmitRequest(
                        frame_id=305,
                        operation_id=305,
                        src_width=32,
                        src_height=32,
                        tile_width=32,
                        tile_height=32,
                        tile_ids=(8,),
                        sections=(
                            TensorSectionData(
                                role_id=1,
                                default_codec_id=0,
                                dtype_id=TensorDType.FP16,
                                tile_payloads=(b"xy",),
                            ),
                        ),
                        typed_payloads=(TypedPayload.token_chunk(b"hint"),),
                        input_profile=InputProfile.DENSE_LUMA_FRAME,
                        tile_index_mode=TileIndexMode.RAW_U16,
                    )
                )
                mixed_result = await session.receive_result(timeout=5.0)
                assert mixed_result.tile_ids == (8,)
                assert len(mixed_result.sections) == 1
                assert mixed_result.opaque_bytes_payloads == (b"meta",)
                assert mixed_result.metadata.inference_ms == 5
                assert mixed_result.metadata.queue_ms == 1
                assert mixed_result.metadata.server_total_ms == 7
        finally:
            await server_task
            await asyncio.wait_for(server_done.wait(), timeout=5.0)
