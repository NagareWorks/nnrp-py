import asyncio
import contextlib
import ipaddress
import socket
import ssl
import struct
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import nnrp.tools.smoke as smoke_module
from nnrp.adapters import (
    create_quic_client_configuration,
    create_quic_server_configuration,
    create_tcp_client_configuration,
    create_tcp_server_configuration,
    serve_quic,
    serve_tcp,
)
from nnrp.client import (
    SubmitIdentity,
    SubmitObjectReferences,
    SubmitPolicy,
    SubmitRequest,
    TensorSubmitInput,
    TypedPayloadInputFrame,
    TypedPayloadSubmitInput,
    connect_client_control,
    connect_client_control_with_probe,
    probe_client_transport,
)
from nnrp.client.transport import connect_client_session, connect_client_session_with_probe
from nnrp.core import (
    CLIENT_HELLO_TRANSPORT_POLICY_EXTENSION,
    SERVER_HELLO_ACK_TRANSPORT_POLICY_EXTENSION,
    TENSOR_PROFILE_CACHE_OBJECT_BITMAP,
    BudgetPolicy,
    ClientHelloLossToleranceExtension,
    ClientHelloMetadata,
    ClientHelloTransportPolicyExtension,
    FlowUpdateBackpressureLevel,
    FlowUpdateFlags,
    FlowUpdateMetadata,
    FlowUpdateReason,
    FlowUpdateScopeKind,
    FrameSubmitMetadata,
    InputProfile,
    LossTolerance,
    MessageType,
    NnrpPacket,
    PayloadKind,
    ResultClass,
    ResultFlags,
    ResultPushMetadata,
    ServerHelloAckMetadata,
    ServerHelloAckTransportPolicyExtension,
    SessionMigrateAckMetadata,
    SessionMigrateMetadata,
    SubmitMode,
    TensorDType,
    TensorSectionData,
    TileIndexMode,
    TransportId,
    TransportPolicy,
    TransportProbeAckMetadata,
    TransportProbeMetadata,
    WireFormat,
    build_camera_reference_block,
    build_client_hello_loss_tolerance_extension,
    build_flow_update_packet,
    build_result_push_packet,
    build_result_push_typed_payload_packet,
    build_session_migrate_ack_packet,
    build_structured_event_frame,
    build_tile_index_reference_block,
    build_tool_delta_frame,
    build_transport_probe_ack_packet,
    pack_control_extension_block,
    parse_client_hello_transport_policy_extension,
    parse_server_hello_ack_transport_policy_extension,
    unpack_control_extension_block,
    unpack_current_tensor_body,
    validate_frame_submit_body,
)
from nnrp.tools import (
    TransportProbeResult,
    TransportProbeSelection,
    TransportProbeSummary,
    build_smoke_client_hello_packet_with_auth,
    build_smoke_close_packet,
    build_smoke_result_packet,
    build_smoke_server_hello_ack_packet_with_body,
    build_smoke_submit_packet,
    render_smoke_transcript,
    resolve_local_dial_transport_policy,
    run_parallel_transport_probes,
    run_quic_probe_server_once,
    run_quic_smoke_client,
    run_quic_smoke_hello_server_once,
    run_quic_smoke_server_once,
    run_tcp_probe_server_once,
    run_tcp_smoke_hello_server_once,
    run_tcp_smoke_server_once,
)


def test_quic_smoke_client_and_server_round_trip() -> None:
    asyncio.run(_run_quic_smoke_round_trip())


def test_quic_smoke_client_and_server_round_trip_with_verified_cert() -> None:
    asyncio.run(_run_quic_smoke_round_trip_with_verified_cert())


def test_parallel_transport_probes_select_higher_throughput_binding() -> None:
    asyncio.run(_run_parallel_transport_probes_selection())


def test_parallel_transport_probes_fall_back_to_single_binding() -> None:
    asyncio.run(_run_parallel_transport_probes_single_binding_fallback())


def test_probe_client_transport_selects_higher_throughput_binding() -> None:
    asyncio.run(_run_probe_client_transport_selection())


def test_connect_client_control_bootstraps_quic_session() -> None:
    asyncio.run(_run_connect_client_control_quic())


def test_connect_client_control_bootstraps_tcp_session() -> None:
    asyncio.run(_run_connect_client_control_tcp())


def test_connect_client_control_with_probe_selects_tcp_and_bootstraps() -> None:
    asyncio.run(_run_connect_client_control_with_probe_tcp())


def test_connect_client_control_exchanges_flow_update_on_tcp() -> None:
    asyncio.run(_run_connect_client_control_flow_update_tcp())


async def _await_task_with_cleanup(task: asyncio.Task, *, timeout: float = 1.0):
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except TimeoutError:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return None


def test_connect_client_session_submits_and_receives_result() -> None:
    asyncio.run(_run_connect_client_session_quic())


def test_connect_client_session_exposes_typed_current_submit_and_result_flow() -> None:
    asyncio.run(_run_connect_client_session_typed_current_tcp())


def test_connect_client_session_exposes_non_tensor_result_helpers() -> None:
    asyncio.run(_run_connect_client_session_non_tensor_current_tcp())


def test_connect_client_session_routes_results_for_multiple_frames() -> None:
    asyncio.run(_run_connect_client_session_result_router_tcp())


def test_connect_client_session_migrates_quic_to_tcp() -> None:
    asyncio.run(_run_connect_client_session_migrate_quic_to_tcp())


def test_connect_client_session_rejects_mismatched_tensor_coverage() -> None:
    asyncio.run(_run_connect_client_session_rejects_mismatched_tensor_coverage())


def test_connect_client_session_with_probe_submits_and_receives_result() -> None:
    asyncio.run(_run_connect_client_session_with_probe_quic())


def test_connect_client_session_exchanges_flow_update_on_quic() -> None:
    asyncio.run(_run_connect_client_session_flow_update_quic())


def test_connect_client_session_handles_partial_then_final_and_next_frame() -> None:
    asyncio.run(_run_connect_client_session_partial_then_final())


def test_connect_client_session_bootstraps_tcp_session() -> None:
    asyncio.run(_run_connect_client_session_tcp())


def test_connect_client_session_with_probe_selects_tcp_and_submits() -> None:
    asyncio.run(_run_connect_client_session_with_probe_tcp())


def test_smoke_client_hello_packet_with_auth_block_sets_auth_bytes_and_body() -> None:
    packet = build_smoke_client_hello_packet_with_auth(
        requested_session_id=19,
        auth_block=b"conv-lite",
    )

    metadata = ClientHelloMetadata.unpack(packet.metadata)
    assert metadata.requested_session_id == 19
    assert metadata.auth_bytes == 9
    assert packet.body == b"conv-lite"


def test_smoke_client_hello_packet_with_extensions_preserves_trailing_auth_block() -> None:
    extensions = pack_control_extension_block(
        (
            build_client_hello_loss_tolerance_extension(
                ClientHelloLossToleranceExtension(
                    session_loss_tolerance=LossTolerance.LOW_LATENCY,
                )
            ),
        )
    )

    packet = build_smoke_client_hello_packet_with_auth(
        requested_session_id=21,
        auth_block=b"conv-lite",
        control_extensions=extensions,
    )

    metadata = ClientHelloMetadata.unpack(packet.metadata)
    assert metadata.requested_session_id == 21
    assert metadata.auth_bytes == 9
    assert packet.body == extensions + b"conv-lite"


def test_smoke_client_hello_packet_with_transport_policy_helper_extension() -> None:
    packet = build_smoke_client_hello_packet_with_auth(
        requested_session_id=22,
        auth_block=b"conv-lite",
        transport_policy=TransportPolicy.PREFER_TCP,
        preferred_transport_id=TransportId.TCP,
    )

    metadata = ClientHelloMetadata.unpack(packet.metadata)
    extension_payload = packet.body[: -metadata.auth_bytes]
    decoded = unpack_control_extension_block(
        extension_payload,
        known_types={CLIENT_HELLO_TRANSPORT_POLICY_EXTENSION},
    )

    assert metadata.requested_session_id == 22
    assert metadata.auth_bytes == 9
    assert packet.body.endswith(b"conv-lite")
    assert len(decoded) == 1
    assert parse_client_hello_transport_policy_extension(decoded[0]) == ClientHelloTransportPolicyExtension(
        transport_policy=TransportPolicy.PREFER_TCP,
        preferred_transport_id=TransportId.TCP,
    )


def test_resolve_local_dial_transport_policy_prefers_selected_binding() -> None:
    assert resolve_local_dial_transport_policy(
        selected_transport_id=TransportId.TCP,
    ) == ClientHelloTransportPolicyExtension(
        transport_policy=TransportPolicy.PREFER_TCP,
        preferred_transport_id=TransportId.TCP,
    )


def test_resolve_local_dial_transport_policy_forces_binding() -> None:
    assert resolve_local_dial_transport_policy(
        forced_transport_id=TransportId.QUIC,
    ) == ClientHelloTransportPolicyExtension(
        transport_policy=TransportPolicy.FORCE_QUIC,
        preferred_transport_id=TransportId.QUIC,
    )


def test_run_tcp_smoke_client_reports_transcript_with_fake_connection() -> None:
    ack_packet = build_smoke_server_hello_ack_packet_with_body(session_id=17)
    result_packet = build_smoke_result_packet(session_id=17, frame_id=303)

    class FakeConnection:
        def __init__(self) -> None:
            self.control_stream_id = 0
            self.last_result_stream_id = 8
            self.sent_control_packets: list[NnrpPacket] = []
            self.sent_submit_packets: list[NnrpPacket] = []

        async def send_control_packet(self, packet: NnrpPacket) -> None:
            self.sent_control_packets.append(packet)

        async def receive_control_packet(self, *, timeout: float) -> NnrpPacket:
            assert timeout == 5.0
            return ack_packet

        async def send_submit_packet(self, packet: NnrpPacket) -> int:
            self.sent_submit_packets.append(packet)
            return 6

        async def receive_result_packet(self, *, timeout: float) -> NnrpPacket:
            assert timeout == 5.0
            return result_packet

    class FakeConnectionContext:
        def __init__(self, connection: FakeConnection) -> None:
            self._connection = connection

        async def __aenter__(self) -> FakeConnection:
            return self._connection

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    fake_connection = FakeConnection()

    with patch.object(
        smoke_module,
        "connect_tcp",
        return_value=FakeConnectionContext(fake_connection),
    ):
        transcript = asyncio.run(
            smoke_module.run_tcp_smoke_client(
                "127.0.0.1",
                5000,
                requested_session_id=9,
                frame_id=303,
                timeout=5.0,
            )
        )

    assert fake_connection.sent_control_packets[0].header.msg_type is MessageType.CLIENT_HELLO
    assert fake_connection.sent_submit_packets[0].header.msg_type is MessageType.FRAME_SUBMIT
    assert transcript.role == "client"
    assert transcript.negotiated_session_id == 17
    assert transcript.requested_session_id == 9
    assert transcript.frame_id == 303
    assert transcript.control_stream_id == 0
    assert transcript.submit_stream_id == 6
    assert transcript.result_stream_id == 8


def test_smoke_main_client_branch_dispatches_quic_flow() -> None:
    transcript = smoke_module.SmokeTranscript(
        role="client",
        negotiated_session_id=17,
        requested_session_id=9,
        frame_id=303,
        control_stream_id=0,
        submit_stream_id=6,
        result_stream_id=8,
        submit_tile_count=3,
        result_tile_count=3,
    )

    with (
        patch(
            "sys.argv",
            [
                "nnrp-quic-smoke",
                "client",
                "--host",
                "127.0.0.1",
                "--port",
                "5000",
                "--requested-session-id",
                "9",
                "--frame-id",
                "303",
                "--timeout",
                "4.5",
                "--cafile",
                "demo-cert.pem",
                "--verify-peer",
            ],
        ),
        patch.object(
            smoke_module,
            "create_quic_client_configuration",
            return_value="client-config",
        ) as create_config,
        patch.object(
            smoke_module,
            "run_quic_smoke_client",
            return_value=transcript,
        ) as run_client,
        patch("builtins.print") as print_mock,
    ):
        smoke_module.main()

    create_config.assert_called_once_with(
        verify_mode=ssl.CERT_REQUIRED,
        cafile=Path("demo-cert.pem"),
    )
    run_client.assert_called_once_with(
        "127.0.0.1",
        5000,
        configuration="client-config",
        requested_session_id=9,
        frame_id=303,
        timeout=4.5,
    )
    print_mock.assert_called_once_with(render_smoke_transcript(transcript))


def test_smoke_main_server_branch_dispatches_quic_server_once() -> None:
    transcript = smoke_module.SmokeTranscript(
        role="server",
        negotiated_session_id=23,
        requested_session_id=5,
        frame_id=404,
        control_stream_id=0,
        submit_stream_id=6,
        result_stream_id=8,
        submit_tile_count=3,
        result_tile_count=3,
    )

    with (
        patch(
            "sys.argv",
            [
                "nnrp-quic-smoke",
                "server-once",
                "--host",
                "127.0.0.1",
                "--port",
                "5001",
                "--certificate",
                "demo-cert.pem",
                "--private-key",
                "demo-key.pem",
                "--session-id",
                "23",
                "--timeout",
                "6.5",
            ],
        ),
        patch.object(
            smoke_module,
            "create_quic_server_configuration",
            return_value="server-config",
        ) as create_config,
        patch.object(
            smoke_module,
            "run_quic_smoke_server_once",
            return_value=transcript,
        ) as run_server,
        patch("builtins.print") as print_mock,
    ):
        smoke_module.main()

    create_config.assert_called_once_with(Path("demo-cert.pem"), Path("demo-key.pem"))
    run_server.assert_called_once_with(
        "127.0.0.1",
        5001,
        configuration="server-config",
        session_id=23,
        timeout=6.5,
    )
    print_mock.assert_called_once_with(render_smoke_transcript(transcript))


def test_resolve_local_dial_transport_policy_rejects_conflict() -> None:
    try:
        resolve_local_dial_transport_policy(
            selected_transport_id=TransportId.QUIC,
            forced_transport_id=TransportId.TCP,
        )
    except ValueError as exc:
        assert str(exc) == ("selected_transport_id and forced_transport_id must not conflict")
    else:
        raise AssertionError("expected conflicting local dial policy ids to fail")


def test_smoke_client_hello_packet_mirrors_selected_transport_into_extension() -> None:
    packet = build_smoke_client_hello_packet_with_auth(
        requested_session_id=24,
        auth_block=b"conv-lite",
        selected_transport_id=TransportId.TCP,
    )

    metadata = ClientHelloMetadata.unpack(packet.metadata)
    extension_payload = packet.body[: -metadata.auth_bytes]
    decoded = unpack_control_extension_block(
        extension_payload,
        known_types={CLIENT_HELLO_TRANSPORT_POLICY_EXTENSION},
    )

    assert metadata.requested_session_id == 24
    assert packet.body.endswith(b"conv-lite")
    assert len(decoded) == 1
    assert parse_client_hello_transport_policy_extension(decoded[0]) == ClientHelloTransportPolicyExtension(
        transport_policy=TransportPolicy.PREFER_TCP,
        preferred_transport_id=TransportId.TCP,
    )


def test_smoke_client_hello_packet_rejects_conflicting_local_dial_policy() -> None:
    try:
        build_smoke_client_hello_packet_with_auth(
            requested_session_id=25,
            auth_block=b"conv-lite",
            transport_policy=TransportPolicy.PREFER_TCP,
            selected_transport_id=TransportId.TCP,
        )
    except ValueError as exc:
        assert str(exc) == (
            "explicit transport_policy/preferred_transport_id cannot be combined with local dial policy ids"
        )
    else:
        raise AssertionError("expected mixed explicit/local dial policy arguments to fail")


def test_smoke_server_hello_ack_packet_with_body_preserves_payload() -> None:
    packet = build_smoke_server_hello_ack_packet_with_body(
        session_id=23,
        body=b"imdn-x2-tile32",
    )

    metadata = ServerHelloAckMetadata.unpack(packet.metadata)
    assert metadata.session_id == 23
    assert metadata.cache_object_bitmap == TENSOR_PROFILE_CACHE_OBJECT_BITMAP
    assert packet.body == b"imdn-x2-tile32"


def test_smoke_server_hello_ack_packet_with_transport_policy_helper_extension() -> None:
    packet = build_smoke_server_hello_ack_packet_with_body(
        session_id=29,
        body=b"imdn-x2-tile32",
        transport_policy=TransportPolicy.PREFER_TCP,
        accepted_transport_policy=TransportPolicy.FORCE_TCP,
        active_transport_id=TransportId.TCP,
    )

    metadata = ServerHelloAckMetadata.unpack(packet.metadata)
    suffix = b"imdn-x2-tile32"
    extension_payload = packet.body[: -len(suffix)]
    decoded = unpack_control_extension_block(
        extension_payload,
        known_types={SERVER_HELLO_ACK_TRANSPORT_POLICY_EXTENSION},
    )

    assert metadata.session_id == 29
    assert packet.body.endswith(suffix)
    assert len(decoded) == 1
    assert parse_server_hello_ack_transport_policy_extension(decoded[0]) == ServerHelloAckTransportPolicyExtension(
        transport_policy=TransportPolicy.PREFER_TCP,
        accepted_transport_policy=TransportPolicy.FORCE_TCP,
        active_transport_id=TransportId.TCP,
    )


async def _run_quic_smoke_round_trip() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        port = _find_free_udp_port()
        ready_event = asyncio.Event()
        server_task = asyncio.create_task(
            run_quic_smoke_server_once(
                "127.0.0.1",
                port,
                configuration=create_quic_server_configuration(
                    certificate_path,
                    private_key_path,
                ),
                ready_event=ready_event,
                session_id=17,
            )
        )
        await ready_event.wait()

        client_transcript = await run_quic_smoke_client(
            "127.0.0.1",
            port,
            configuration=create_quic_client_configuration(cafile=certificate_path),
            requested_session_id=9,
            frame_id=303,
        )
        server_transcript = await server_task

        assert client_transcript.role == "client"
        assert server_transcript.role == "server"
        assert client_transcript.negotiated_session_id == 17
        assert server_transcript.negotiated_session_id == 17
        assert client_transcript.requested_session_id == 9
        assert server_transcript.requested_session_id == 9
        assert client_transcript.frame_id == 303
        assert server_transcript.frame_id == 303
        assert client_transcript.submit_tile_count == 3
        assert server_transcript.submit_tile_count == 3
        assert client_transcript.result_tile_count == 3
        assert server_transcript.result_tile_count == 3
        assert client_transcript.control_stream_id == server_transcript.control_stream_id == 0
        assert client_transcript.submit_stream_id & 0x02
        assert server_transcript.result_stream_id & 0x02
        assert "negotiated_session_id=17" in render_smoke_transcript(client_transcript)


async def _run_quic_smoke_round_trip_with_verified_cert() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        port = _find_free_udp_port()
        ready_event = asyncio.Event()
        server_task = asyncio.create_task(
            run_quic_smoke_server_once(
                "127.0.0.1",
                port,
                configuration=create_quic_server_configuration(
                    certificate_path,
                    private_key_path,
                ),
                ready_event=ready_event,
                session_id=23,
            )
        )
        await ready_event.wait()

        client_transcript = await run_quic_smoke_client(
            "127.0.0.1",
            port,
            configuration=create_quic_client_configuration(
                verify_mode=ssl.CERT_REQUIRED,
                cafile=certificate_path,
            ),
            requested_session_id=5,
            frame_id=404,
        )
        server_transcript = await server_task

        assert client_transcript.negotiated_session_id == 23
        assert server_transcript.requested_session_id == 5
        assert client_transcript.frame_id == 404
        assert client_transcript.control_stream_id == server_transcript.control_stream_id == 0


async def _run_parallel_transport_probes_selection() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        quic_port = _find_free_udp_port()
        tcp_port = _find_free_tcp_port()
        quic_ready = asyncio.Event()
        tcp_ready = asyncio.Event()
        quic_task = asyncio.create_task(
            run_quic_probe_server_once(
                "127.0.0.1",
                quic_port,
                configuration=create_quic_server_configuration(
                    certificate_path,
                    private_key_path,
                    wire_format=WireFormat.CURRENT,
                ),
                ready_event=quic_ready,
                ack_delay_seconds=(0.03, 0.03, 0.03),
                connection_count=3,
            )
        )
        tcp_task = asyncio.create_task(
            run_tcp_probe_server_once(
                "127.0.0.1",
                tcp_port,
                ready_event=tcp_ready,
                connection_count=3,
            )
        )
        await quic_ready.wait()
        await tcp_ready.wait()

        try:
            selection = await run_parallel_transport_probes(
                "127.0.0.1",
                quic_port=quic_port,
                tcp_port=tcp_port,
                quic_configuration=create_quic_client_configuration(
                    wire_format=WireFormat.CURRENT,
                    cafile=certificate_path,
                ),
                probe_payload_bytes=2048,
                sample_count=3,
                timeout=5.0,
            )
        finally:
            await _await_task_with_cleanup(quic_task)
            await _await_task_with_cleanup(tcp_task)

        assert isinstance(selection, TransportProbeSelection)
        assert selection.quic_summary is not None
        assert selection.tcp_summary is not None
        assert selection.quic_result is not None
        assert selection.tcp_result is not None
        assert selection.quic_summary.success_count == 3
        assert selection.tcp_summary.success_count == 3
        assert selection.selected_transport_id is TransportId.TCP
        assert selection.selected_summary is selection.tcp_summary
        assert selection.selected_result is selection.tcp_result
        assert (
            selection.tcp_summary.median_throughput_bytes_per_sec
            > selection.quic_summary.median_throughput_bytes_per_sec
        )


async def _run_probe_client_transport_selection() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        quic_port = _find_free_udp_port()
        tcp_port = _find_free_tcp_port()
        quic_ready = asyncio.Event()
        tcp_ready = asyncio.Event()
        quic_task = asyncio.create_task(
            run_quic_probe_server_once(
                "127.0.0.1",
                quic_port,
                configuration=create_quic_server_configuration(
                    certificate_path,
                    private_key_path,
                    wire_format=WireFormat.CURRENT,
                ),
                ready_event=quic_ready,
                ack_delay_seconds=(0.03, 0.03, 0.03),
                connection_count=3,
            )
        )
        tcp_task = asyncio.create_task(
            run_tcp_probe_server_once(
                "127.0.0.1",
                tcp_port,
                ready_event=tcp_ready,
                connection_count=3,
            )
        )
        await quic_ready.wait()
        await tcp_ready.wait()

        try:
            selection = await probe_client_transport(
                "127.0.0.1",
                quic_port=quic_port,
                tcp_port=tcp_port,
                quic_configuration=create_quic_client_configuration(
                    wire_format=WireFormat.CURRENT,
                    cafile=certificate_path,
                ),
                probe_payload_bytes=2048,
                probe_sample_count=3,
                timeout=5.0,
            )
        finally:
            await _await_task_with_cleanup(quic_task)
            await _await_task_with_cleanup(tcp_task)

        assert selection.selected_transport_id is TransportId.TCP
        assert selection.selected_summary is selection.tcp_summary
        assert selection.tcp_summary is not None
        assert selection.tcp_summary.success_count == 3


async def _run_parallel_transport_probes_single_binding_fallback() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        quic_port = _find_free_udp_port()
        tcp_port = _find_free_tcp_port()
        quic_ready = asyncio.Event()
        quic_task = asyncio.create_task(
            run_quic_probe_server_once(
                "127.0.0.1",
                quic_port,
                configuration=create_quic_server_configuration(
                    certificate_path,
                    private_key_path,
                    wire_format=WireFormat.CURRENT,
                ),
                ready_event=quic_ready,
                connection_count=3,
            )
        )
        await quic_ready.wait()

        try:
            selection = await run_parallel_transport_probes(
                "127.0.0.1",
                quic_port=quic_port,
                tcp_port=tcp_port,
                quic_configuration=create_quic_client_configuration(
                    wire_format=WireFormat.CURRENT,
                    cafile=certificate_path,
                ),
                probe_payload_bytes=1024,
                sample_count=3,
                timeout=5.0,
            )
        finally:
            await _await_task_with_cleanup(quic_task)

        assert selection.quic_summary is not None
        assert selection.tcp_summary is not None
        assert selection.quic_summary.success_count > 0
        assert selection.tcp_summary.success_count == 0
        assert selection.tcp_summary.failure_count == 3
        assert selection.quic_result is not None
        assert selection.tcp_result is None
        assert selection.selected_transport_id is TransportId.QUIC
        assert selection.selected_result is selection.quic_result


async def _run_connect_client_control_quic() -> None:
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            await _run_connect_client_control_quic_once()
            return
        except TimeoutError as error:
            last_error = error

    if last_error is not None:
        raise last_error


async def _run_connect_client_control_quic_once() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        quic_port = _find_free_udp_port()
        ready_event = asyncio.Event()
        server_task = asyncio.create_task(
            run_quic_smoke_hello_server_once(
                "127.0.0.1",
                quic_port,
                configuration=create_quic_server_configuration(
                    certificate_path,
                    private_key_path,
                    wire_format=WireFormat.CURRENT,
                ),
                ready_event=ready_event,
                session_id=61,
                active_transport_id=TransportId.QUIC,
            )
        )
        await ready_event.wait()

        try:
            async with connect_client_control(
                "127.0.0.1",
                quic_port=quic_port,
                quic_configuration=create_quic_client_configuration(
                    wire_format=WireFormat.CURRENT,
                    cafile=certificate_path,
                ),
                requested_session_id=51,
                selected_transport_id=TransportId.QUIC,
                timeout=10.0,
            ) as session:
                assert session.transport_id is TransportId.QUIC
                assert session.ack_metadata.session_id == 61
                assert session.bootstrap.plan.selected_transport_id is TransportId.QUIC
                assert session.bootstrap.hello_packet.header.msg_type is MessageType.CLIENT_HELLO
        finally:
            hello_metadata = await _await_task_with_cleanup(server_task)
        assert hello_metadata.requested_session_id == 51


async def _run_connect_client_control_tcp() -> None:
    tcp_port = _find_free_tcp_port()
    ready_event = asyncio.Event()
    server_task = asyncio.create_task(
        run_tcp_smoke_hello_server_once(
            "127.0.0.1",
            tcp_port,
            ready_event=ready_event,
            session_id=62,
            active_transport_id=TransportId.TCP,
        )
    )
    await ready_event.wait()

    async with connect_client_control(
        "127.0.0.1",
        tcp_port=tcp_port,
        requested_session_id=52,
        forced_transport_id=TransportId.TCP,
        timeout=5.0,
    ) as session:
        assert session.transport_id is TransportId.TCP
        assert session.ack_metadata.session_id == 62
        assert session.bootstrap.plan.selected_transport_id is TransportId.TCP
        assert session.bootstrap.hello_packet.header.msg_type is MessageType.CLIENT_HELLO

    hello_metadata = await server_task
    assert hello_metadata.requested_session_id == 52


async def _run_connect_client_control_with_probe_tcp() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        quic_port = _find_free_udp_port()
        tcp_port = _find_free_tcp_port()
        quic_ready = asyncio.Event()
        tcp_ready = asyncio.Event()
        quic_task = asyncio.create_task(
            _run_quic_probe_then_stop(
                "127.0.0.1",
                quic_port,
                certificate_path=certificate_path,
                private_key_path=private_key_path,
                ready_event=quic_ready,
                ack_delay_seconds=(0.2, 0.2, 0.2),
                probe_connection_count=3,
            )
        )
        tcp_task = asyncio.create_task(
            _run_tcp_probe_then_hello_server(
                "127.0.0.1",
                tcp_port,
                ready_event=tcp_ready,
                ack_delay_seconds=(0.0, 0.0, 0.0),
                probe_connection_count=3,
                session_id=63,
            )
        )
        await quic_ready.wait()
        await tcp_ready.wait()

        async with connect_client_control_with_probe(
            "127.0.0.1",
            quic_port=quic_port,
            tcp_port=tcp_port,
            quic_configuration=create_quic_client_configuration(
                wire_format=WireFormat.CURRENT,
                cafile=certificate_path,
            ),
            probe_payload_bytes=2048,
            probe_sample_count=3,
            requested_session_id=53,
            timeout=5.0,
        ) as session:
            assert session.transport_id is TransportId.TCP
            assert session.ack_metadata.session_id == 63
            assert session.bootstrap.probe_selection is not None
            assert session.bootstrap.probe_selection.selected_transport_id is TransportId.TCP
            assert session.bootstrap.probe_selection.tcp_summary is not None
            assert session.bootstrap.probe_selection.tcp_summary.success_count == 3

        await quic_task
        hello_metadata = await tcp_task
        assert hello_metadata.requested_session_id == 53


async def _run_connect_client_control_flow_update_tcp() -> None:
    tcp_port = _find_free_tcp_port()
    ready_event = asyncio.Event()
    server_task = asyncio.create_task(
        _run_tcp_flow_update_server_once(
            "127.0.0.1",
            tcp_port,
            ready_event=ready_event,
            session_id=64,
        )
    )
    await ready_event.wait()

    async with connect_client_control(
        "127.0.0.1",
        tcp_port=tcp_port,
        requested_session_id=54,
        forced_transport_id=TransportId.TCP,
        timeout=5.0,
    ) as session:
        outbound = FlowUpdateMetadata(
            scope_kind=FlowUpdateScopeKind.SESSION,
            update_reason=FlowUpdateReason.PAUSE,
            backpressure_level=FlowUpdateBackpressureLevel.HARD,
            session_credit=2,
            retry_after_ms=15,
            credit_epoch=10,
            flags=FlowUpdateFlags.CREDIT_VALID | FlowUpdateFlags.RETRY_AFTER_VALID,
        )
        await session.send_flow_update(outbound, trace_id=777)
        inbound_packet, inbound = await session.receive_flow_update(timeout=5.0)

        assert inbound_packet.header.msg_type is MessageType.FLOW_UPDATE
        assert inbound_packet.header.session_id == 64
        assert inbound_packet.header.trace_id == 778
        assert inbound.scope_kind is FlowUpdateScopeKind.SESSION
        assert inbound.session_credit == 4
        assert inbound.credit_epoch == 11
        assert inbound.retry_after_ms == 30

    received_packet, received_metadata = await server_task
    assert received_packet.header.msg_type is MessageType.FLOW_UPDATE
    assert received_packet.header.trace_id == 777
    assert received_metadata == outbound


async def _run_connect_client_session_quic() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        quic_port = _find_free_udp_port()
        ready_event = asyncio.Event()
        server_task = asyncio.create_task(
            run_quic_smoke_server_once(
                "127.0.0.1",
                quic_port,
                configuration=create_quic_server_configuration(
                    certificate_path,
                    private_key_path,
                    wire_format=WireFormat.CURRENT,
                ),
                ready_event=ready_event,
                session_id=71,
            )
        )
        await ready_event.wait()

        async with connect_client_session(
            "127.0.0.1",
            quic_port=quic_port,
            quic_configuration=create_quic_client_configuration(
                wire_format=WireFormat.CURRENT,
                cafile=certificate_path,
            ),
            requested_session_id=61,
            selected_transport_id=TransportId.QUIC,
            timeout=5.0,
        ) as session:
            submit_packet = build_smoke_submit_packet(
                session_id=session.session_id,
                frame_id=808,
            )
            submit_stream_id, result_packet = await session.submit_and_receive_result(
                submit_packet,
                timeout=5.0,
            )

            submit_metadata = FrameSubmitMetadata.unpack(submit_packet.metadata)
            submit_body = unpack_current_tensor_body(
                validate_frame_submit_body(submit_metadata, submit_packet.body),
                section_count=submit_metadata.section_count,
                tile_count=submit_metadata.tile_count,
            )
            assert session.control.ack_metadata.session_id == 71
            assert submit_stream_id & 0x02
            assert submit_metadata.tile_count == 3
            assert submit_body.sections[0].tile_lengths() == (2, 2, 2)
            assert result_packet.header.msg_type is MessageType.RESULT_PUSH
            assert result_packet.header.frame_id == 808

        server_transcript = await server_task
        assert server_transcript.requested_session_id == 61
        assert server_transcript.frame_id == 808


async def _run_connect_client_session_with_probe_quic() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        quic_port = _find_free_udp_port()
        ready_event = asyncio.Event()
        server_task = asyncio.create_task(
            run_quic_smoke_server_once(
                "127.0.0.1",
                quic_port,
                configuration=create_quic_server_configuration(
                    certificate_path,
                    private_key_path,
                    wire_format=WireFormat.CURRENT,
                ),
                ready_event=ready_event,
                session_id=72,
            )
        )
        await ready_event.wait()

        selection = TransportProbeSelection(
            selected_transport_id=TransportId.QUIC,
            quic_summary=TransportProbeSummary(
                transport_id=TransportId.QUIC,
                results=(
                    TransportProbeResult(
                        transport_id=TransportId.QUIC,
                        probe_id=101,
                        probe_payload_bytes=2048,
                        client_send_ts_us=100,
                        server_recv_ts_us=110,
                        ack_recv_ts_us=130,
                    ),
                ),
            ),
        )

        with patch(
            "nnrp.tools.smoke.run_parallel_transport_probes",
            return_value=selection,
        ):
            async with connect_client_session_with_probe(
                "127.0.0.1",
                quic_port=quic_port,
                tcp_port=9999,
                quic_configuration=create_quic_client_configuration(
                    wire_format=WireFormat.CURRENT,
                    cafile=certificate_path,
                ),
                requested_session_id=62,
                timeout=5.0,
            ) as session:
                submit_packet = build_smoke_submit_packet(
                    session_id=session.session_id,
                    frame_id=809,
                )
                (
                    submit_stream_id,
                    result_packet,
                ) = await session.submit_and_receive_result(
                    submit_packet,
                    timeout=5.0,
                )

                assert session.control.bootstrap.probe_selection is selection
                assert submit_stream_id & 0x02
                assert result_packet.header.msg_type is MessageType.RESULT_PUSH
                assert result_packet.header.frame_id == 809

        server_transcript = await server_task
        assert server_transcript.requested_session_id == 62
        assert server_transcript.frame_id == 809


async def _run_connect_client_session_flow_update_quic() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        quic_port = _find_free_udp_port()
        ready_event = asyncio.Event()
        server_task = asyncio.create_task(
            _run_quic_flow_update_server_once(
                "127.0.0.1",
                quic_port,
                certificate_path=certificate_path,
                private_key_path=private_key_path,
                ready_event=ready_event,
                session_id=73,
            )
        )
        await ready_event.wait()

        async with connect_client_session(
            "127.0.0.1",
            quic_port=quic_port,
            quic_configuration=create_quic_client_configuration(
                wire_format=WireFormat.CURRENT,
                cafile=certificate_path,
            ),
            requested_session_id=63,
            selected_transport_id=TransportId.QUIC,
            timeout=5.0,
        ) as session:
            outbound = FlowUpdateMetadata(
                scope_kind=FlowUpdateScopeKind.SESSION,
                update_reason=FlowUpdateReason.CONGESTION,
                backpressure_level=FlowUpdateBackpressureLevel.SOFT,
                session_credit=3,
                retry_after_ms=20,
                credit_epoch=20,
                flags=FlowUpdateFlags.CREDIT_VALID | FlowUpdateFlags.RETRY_AFTER_VALID,
            )
            await session.send_flow_update(outbound, trace_id=880)
            inbound_packet, inbound = await session.receive_flow_update(timeout=5.0)

            assert inbound_packet.header.msg_type is MessageType.FLOW_UPDATE
            assert inbound_packet.header.session_id == 73
            assert inbound_packet.header.trace_id == 881
            assert inbound.scope_kind is FlowUpdateScopeKind.SESSION
            assert inbound.session_credit == 4
            assert inbound.credit_epoch == 21
            assert inbound.retry_after_ms == 25

        received_packet, received_metadata = await server_task
        assert received_packet.header.msg_type is MessageType.FLOW_UPDATE
        assert received_packet.header.trace_id == 880
        assert received_metadata == outbound


async def _run_connect_client_session_partial_then_final() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        quic_port = _find_free_udp_port()
        ready_event = asyncio.Event()
        server_task = asyncio.create_task(
            _run_quic_partial_result_server_once(
                "127.0.0.1",
                quic_port,
                certificate_path=certificate_path,
                private_key_path=private_key_path,
                ready_event=ready_event,
                session_id=74,
            )
        )
        await ready_event.wait()

        async with connect_client_session(
            "127.0.0.1",
            quic_port=quic_port,
            quic_configuration=create_quic_client_configuration(
                wire_format=WireFormat.CURRENT,
                cafile=certificate_path,
            ),
            requested_session_id=64,
            selected_transport_id=TransportId.QUIC,
            timeout=5.0,
        ) as session:
            submit_packet_1 = build_smoke_submit_packet(
                session_id=session.session_id,
                frame_id=810,
            )
            submit_stream_id_1 = await session.send_submit_packet(submit_packet_1)
            partial_packet = await session.receive_result_packet(timeout=5.0)
            final_packet = await session.receive_result_packet(timeout=5.0)

            partial_metadata = ResultPushMetadata.unpack(partial_packet.metadata)
            final_metadata = ResultPushMetadata.unpack(final_packet.metadata)
            assert submit_stream_id_1 & 0x02
            assert partial_packet.header.frame_id == 810
            assert final_packet.header.frame_id == 810
            assert partial_metadata.result_flags is ResultFlags.PARTIAL
            assert final_metadata.result_flags is ResultFlags.NONE

            submit_packet_2 = build_smoke_submit_packet(
                session_id=session.session_id,
                frame_id=811,
            )
            (
                submit_stream_id_2,
                result_packet_2,
            ) = await session.submit_and_receive_result(
                submit_packet_2,
                timeout=5.0,
            )
            result_metadata_2 = ResultPushMetadata.unpack(result_packet_2.metadata)
            assert submit_stream_id_2 & 0x02
            assert submit_stream_id_2 != submit_stream_id_1
            assert result_packet_2.header.frame_id == 811
            assert result_metadata_2.result_flags is ResultFlags.NONE

        requested_session_id, received_frames = await server_task
        assert requested_session_id == 64
        assert received_frames == [810, 811]


async def _run_connect_client_session_tcp() -> None:
    tcp_port = _find_free_tcp_port()
    ready_event = asyncio.Event()
    server_task = asyncio.create_task(
        run_tcp_smoke_server_once(
            "127.0.0.1",
            tcp_port,
            configuration=create_tcp_server_configuration(),
            ready_event=ready_event,
            session_id=75,
            active_transport_id=TransportId.TCP,
        )
    )
    await ready_event.wait()

    async with connect_client_session(
        "127.0.0.1",
        tcp_port=tcp_port,
        tcp_configuration=create_tcp_client_configuration(),
        requested_session_id=65,
        selected_transport_id=TransportId.TCP,
        timeout=5.0,
    ) as session:
        submit_packet = build_smoke_submit_packet(
            session_id=session.session_id,
            frame_id=812,
        )
        submit_stream_id, result_packet = await session.submit_and_receive_result(
            submit_packet,
            timeout=5.0,
        )

        assert session.control.transport_id is TransportId.TCP
        assert session.control.ack_metadata.session_id == 75
        assert submit_stream_id & 0x02
        assert result_packet.header.msg_type is MessageType.RESULT_PUSH
        assert result_packet.header.frame_id == 812

    server_transcript = await server_task
    assert server_transcript.requested_session_id == 65
    assert server_transcript.frame_id == 812
    assert server_transcript.submit_stream_id & 0x02
    assert server_transcript.result_stream_id & 0x02


async def _run_connect_client_session_typed_current_tcp() -> None:
    tcp_port = _find_free_tcp_port()
    ready_event = asyncio.Event()
    server_task = asyncio.create_task(
        _run_tcp_current_tensor_server_once(
            "127.0.0.1",
            tcp_port,
            ready_event=ready_event,
            session_id=77,
        )
    )
    await ready_event.wait()

    async with connect_client_session(
        "127.0.0.1",
        tcp_port=tcp_port,
        tcp_configuration=create_tcp_client_configuration(),
        requested_session_id=67,
        selected_transport_id=TransportId.TCP,
        timeout=5.0,
    ) as session:
        submit_stream_id = await session.send_submit(
            SubmitRequest.tensor(
                TensorSubmitInput(
                    identity=SubmitIdentity(operation_id=814, frame_id=814),
                    policy=SubmitPolicy(
                        latency_budget_ms=40,
                        target_fps_x100=6000,
                        budget_policy=BudgetPolicy.ALLOW_PARTIAL,
                        dependency_frame_id=812,
                    ),
                    src_width=640,
                    src_height=360,
                    tile_width=32,
                    tile_height=32,
                    tile_ids=(2, 4, 6),
                    sections=(
                        TensorSectionData(
                            role_id=9,
                            default_codec_id=0,
                            dtype_id=TensorDType.UINT8,
                            tile_payloads=(b"abc", b"de", b"f"),
                        ),
                    ),
                    camera_block=b"cam-current",
                    input_profile=InputProfile.DENSE_LUMA_FRAME,
                    tile_index_mode=TileIndexMode.RAW_U16,
                    references=SubmitObjectReferences(
                        camera=build_camera_reference_block(
                            cache_namespace=1,
                            cache_key_hi=2,
                            cache_key_lo=3,
                        ),
                        tile_index=build_tile_index_reference_block(
                            cache_namespace=4,
                            cache_key_hi=5,
                            cache_key_lo=6,
                        ),
                    ),
                )
            )
        )
        result = await session.receive_result(timeout=5.0)

        assert submit_stream_id & 0x02
        assert result.is_push is True
        assert result.is_drop is False
        assert result.metadata is not None
        assert result.metadata.result_class is ResultClass.PARTIAL
        assert result.metadata.applied_budget_policy is BudgetPolicy.ALLOW_PARTIAL
        assert result.metadata.payload_kind_bitmap == PayloadKind.TENSOR
        assert result.metadata.payload_frame_count == 0
        assert result.packet.header.frame_id == 814
        assert result.tile_ids == (2, 4, 6)
        assert len(result.sections) == 1

    requested_session_id, submit_metadata = await server_task
    assert requested_session_id == 67
    assert submit_metadata.submit_mode is SubmitMode.MIXED
    assert submit_metadata.object_ref_mask == 0x00000003
    assert submit_metadata.budget_policy is BudgetPolicy.ALLOW_PARTIAL
    assert submit_metadata.dependency_frame_id == 812
    assert submit_metadata.payload_kind_bitmap == PayloadKind.TENSOR
    assert submit_metadata.payload_frame_count == 0


async def _run_connect_client_session_non_tensor_current_tcp() -> None:
    tcp_port = _find_free_tcp_port()
    ready_event = asyncio.Event()
    server_task = asyncio.create_task(
        _run_tcp_current_non_tensor_server_once(
            "127.0.0.1",
            tcp_port,
            ready_event=ready_event,
            session_id=78,
        )
    )
    await ready_event.wait()

    async with connect_client_session(
        "127.0.0.1",
        tcp_port=tcp_port,
        tcp_configuration=create_tcp_client_configuration(),
        requested_session_id=68,
        selected_transport_id=TransportId.TCP,
        timeout=5.0,
    ) as session:
        submit_stream_id = await session.send_submit(
            SubmitRequest.typed_payload(
                TypedPayloadSubmitInput(
                    identity=SubmitIdentity(operation_id=815, frame_id=815),
                    policy=SubmitPolicy(),
                    frames=(
                        TypedPayloadInputFrame(
                            profile_id=0,
                            payload_kind=PayloadKind.STRUCTURED_EVENT,
                            payload=b'{"event":"ready"}',
                        ),
                        TypedPayloadInputFrame(
                            profile_id=0,
                            payload_kind=PayloadKind.TOOL_DELTA,
                            payload=b'{"tool":"render"}',
                        ),
                    ),
                )
            )
        )
        result = await session.receive_result(timeout=5.0)

        assert submit_stream_id & 0x02
        assert result.is_push is True
        assert result.is_drop is False
        assert result.metadata is not None
        assert result.packet.header.frame_id == 815
        assert result.payload_kinds == (
            PayloadKind.STRUCTURED_EVENT,
            PayloadKind.TOOL_DELTA,
        )
        assert result.has_payload_kind(PayloadKind.STRUCTURED_EVENT) is True
        assert result.has_payload_kind(PayloadKind.TENSOR) is False
        assert result.payload_frame_count == 2
        assert result.has_tensor_coverage is False
        assert result.tensor_covered_tile_count is None
        assert result.tensor_dropped_tile_count is None
        assert result.tile_ids == ()
        assert result.sections == ()

    requested_session_id, submit_metadata = await server_task
    assert requested_session_id == 68
    assert submit_metadata.payload_kind_bitmap == (PayloadKind.STRUCTURED_EVENT | PayloadKind.TOOL_DELTA)
    assert submit_metadata.payload_frame_count == 2


async def _run_tcp_current_tensor_server_once(
    host: str,
    port: int,
    *,
    ready_event: asyncio.Event | None = None,
    session_id: int = 77,
) -> tuple[int, FrameSubmitMetadata]:
    async with serve_tcp(
        host,
        port,
        configuration=create_tcp_server_configuration(),
    ) as listener:
        if ready_event is not None:
            ready_event.set()

        connection = await listener.accept(timeout=5.0)
        hello_packet = await connection.receive_control_packet(timeout=5.0)
        hello_metadata = ClientHelloMetadata.unpack(hello_packet.metadata)
        await connection.send_control_packet(
            build_smoke_server_hello_ack_packet_with_body(
                session_id=session_id,
                active_transport_id=TransportId.TCP,
            )
        )

        submit_packet = await connection.receive_submit_packet(timeout=5.0)
        submit_metadata = FrameSubmitMetadata.unpack(submit_packet.metadata)
        result_packet = build_result_push_packet(
            session_id=session_id,
            frame_id=submit_packet.header.frame_id,
            tile_ids=(2, 4, 6),
            sections=(
                TensorSectionData(
                    role_id=100,
                    default_codec_id=0,
                    dtype_id=TensorDType.UINT8,
                    tile_payloads=(b"ra", b"rb", b"rc"),
                ),
            ),
            result_flags=ResultFlags.PARTIAL,
            active_profile_id=1,
            inference_ms=12,
            queue_ms=2,
            server_total_ms=14,
            result_class=ResultClass.PARTIAL,
            applied_budget_policy=BudgetPolicy.ALLOW_PARTIAL,
            covered_tile_count=2,
            dropped_tile_count=1,
            payload_kind_bitmap=PayloadKind.TENSOR,
            tile_index_mode=submit_metadata.tile_index_mode,
            tile_base_id=submit_metadata.tile_base_id,
        )
        await connection.send_result_packet(result_packet)
        return hello_metadata.requested_session_id, submit_metadata


async def _run_tcp_current_non_tensor_server_once(
    host: str,
    port: int,
    *,
    ready_event: asyncio.Event | None = None,
    session_id: int = 78,
) -> tuple[int, FrameSubmitMetadata]:
    async with serve_tcp(
        host,
        port,
        configuration=create_tcp_server_configuration(),
    ) as listener:
        if ready_event is not None:
            ready_event.set()

        connection = await listener.accept(timeout=5.0)
        hello_packet = await connection.receive_control_packet(timeout=5.0)
        hello_metadata = ClientHelloMetadata.unpack(hello_packet.metadata)
        await connection.send_control_packet(
            build_smoke_server_hello_ack_packet_with_body(
                session_id=session_id,
                active_transport_id=TransportId.TCP,
            )
        )

        submit_packet = await connection.receive_submit_packet(timeout=5.0)
        submit_metadata = FrameSubmitMetadata.unpack(submit_packet.metadata)
        result_packet = build_result_push_typed_payload_packet(
            session_id=session_id,
            frame_id=submit_packet.header.frame_id,
            frames=(
                build_structured_event_frame(b'{"event":"accepted"}'),
                build_tool_delta_frame(b'{"tool":"rendered"}'),
            ),
            result_flags=ResultFlags.NONE,
            active_profile_id=0,
            inference_ms=1,
            queue_ms=0,
            server_total_ms=1,
            result_class=ResultClass.COMPLETE,
        )
        await connection.send_result_packet(result_packet)
        return hello_metadata.requested_session_id, submit_metadata


async def _run_connect_client_session_result_router_tcp() -> None:
    tcp_port = _find_free_tcp_port()
    ready_event = asyncio.Event()
    server_task = asyncio.create_task(
        _run_tcp_current_router_server_once(
            "127.0.0.1",
            tcp_port,
            ready_event=ready_event,
            session_id=78,
        )
    )
    await ready_event.wait()

    async with connect_client_session(
        "127.0.0.1",
        tcp_port=tcp_port,
        tcp_configuration=create_tcp_client_configuration(),
        requested_session_id=68,
        selected_transport_id=TransportId.TCP,
        timeout=5.0,
    ) as session:
        async with session.manage_results() as router:
            submit_stream_id_1 = await router.send_submit(_build_submit_request(frame_id=815))
            submit_stream_id_2 = await router.send_submit(_build_submit_request(frame_id=816))

            frame_816 = await router.receive(816, timeout=5.0)
            frame_815_partial = await router.receive(815, timeout=5.0)
            frame_815_final = await router.receive(815, timeout=5.0)

            assert submit_stream_id_1 & 0x02
            assert submit_stream_id_2 & 0x02
            assert frame_816.packet.header.frame_id == 816
            assert frame_816.metadata is not None
            assert frame_816.metadata.result_flags is ResultFlags.NONE
            assert frame_815_partial.metadata is not None
            assert frame_815_partial.metadata.result_flags is ResultFlags.PARTIAL
            assert frame_815_final.metadata is not None
            assert frame_815_final.metadata.result_flags is ResultFlags.NONE
            await session.connection.send_control_packet(
                build_smoke_close_packet(
                    session_id=session.session_id,
                    reason="current-router-test",
                )
            )

    requested_session_id, received_frames = await server_task
    assert requested_session_id == 68
    assert received_frames == [815, 816]


async def _run_tcp_current_router_server_once(
    host: str,
    port: int,
    *,
    ready_event: asyncio.Event | None = None,
    session_id: int = 78,
) -> tuple[int, list[int]]:
    async with serve_tcp(
        host,
        port,
        configuration=create_tcp_server_configuration(),
    ) as listener:
        if ready_event is not None:
            ready_event.set()

        connection = await listener.accept(timeout=5.0)
        hello_packet = await connection.receive_control_packet(timeout=5.0)
        hello_metadata = ClientHelloMetadata.unpack(hello_packet.metadata)
        await connection.send_control_packet(
            build_smoke_server_hello_ack_packet_with_body(
                session_id=session_id,
                active_transport_id=TransportId.TCP,
            )
        )

        submit_packet_1 = await connection.receive_submit_packet(timeout=5.0)
        submit_packet_2 = await connection.receive_submit_packet(timeout=5.0)
        await connection.send_result_packet(
            build_result_push_packet(
                session_id=session_id,
                frame_id=submit_packet_2.header.frame_id,
                tile_ids=(2, 4, 6),
                sections=(
                    TensorSectionData(
                        role_id=100,
                        default_codec_id=0,
                        dtype_id=TensorDType.UINT8,
                        tile_payloads=(b"r2a", b"r2b", b"r2c"),
                    ),
                ),
                result_flags=ResultFlags.NONE,
                active_profile_id=1,
                inference_ms=8,
                queue_ms=1,
                server_total_ms=9,
                result_class=ResultClass.COMPLETE,
                payload_kind_bitmap=PayloadKind.TENSOR,
                tile_index_mode=TileIndexMode.RAW_U16,
            )
        )
        await connection.send_result_packet(
            build_result_push_packet(
                session_id=session_id,
                frame_id=submit_packet_1.header.frame_id,
                tile_ids=(2, 4, 6),
                sections=(
                    TensorSectionData(
                        role_id=100,
                        default_codec_id=0,
                        dtype_id=TensorDType.UINT8,
                        tile_payloads=(b"p1a", b"p1b", b"p1c"),
                    ),
                ),
                result_flags=ResultFlags.PARTIAL,
                active_profile_id=1,
                inference_ms=10,
                queue_ms=1,
                server_total_ms=11,
                result_class=ResultClass.PARTIAL,
                applied_budget_policy=BudgetPolicy.ALLOW_PARTIAL,
                covered_tile_count=2,
                dropped_tile_count=1,
                payload_kind_bitmap=PayloadKind.TENSOR,
                tile_index_mode=TileIndexMode.RAW_U16,
            )
        )
        await connection.send_result_packet(
            build_result_push_packet(
                session_id=session_id,
                frame_id=submit_packet_1.header.frame_id,
                tile_ids=(2, 4, 6),
                sections=(
                    TensorSectionData(
                        role_id=100,
                        default_codec_id=0,
                        dtype_id=TensorDType.UINT8,
                        tile_payloads=(b"f1a", b"f1b", b"f1c"),
                    ),
                ),
                result_flags=ResultFlags.NONE,
                active_profile_id=1,
                inference_ms=12,
                queue_ms=1,
                server_total_ms=13,
                result_class=ResultClass.COMPLETE,
                payload_kind_bitmap=PayloadKind.TENSOR,
                tile_index_mode=TileIndexMode.RAW_U16,
            )
        )
        close_packet = await connection.receive_control_packet(timeout=5.0)
        assert close_packet.header.msg_type is MessageType.CLOSE
        return hello_metadata.requested_session_id, [
            submit_packet_1.header.frame_id,
            submit_packet_2.header.frame_id,
        ]


async def _run_connect_client_session_migrate_quic_to_tcp() -> None:
    quic_port = _find_free_udp_port()
    tcp_port = _find_free_tcp_port()
    quic_ready = asyncio.Event()
    tcp_ready = asyncio.Event()

    with tempfile.TemporaryDirectory() as temp_dir:
        certificate_path, private_key_path = _write_self_signed_certificate(Path(temp_dir))
        quic_server_task = asyncio.create_task(
            _run_quic_current_origin_server_once(
                "127.0.0.1",
                quic_port,
                certificate_path=certificate_path,
                private_key_path=private_key_path,
                ready_event=quic_ready,
                session_id=93,
            )
        )
        tcp_server_task = asyncio.create_task(
            _run_tcp_current_migration_target_server_once(
                "127.0.0.1",
                tcp_port,
                ready_event=tcp_ready,
                session_id=93,
            )
        )
        await quic_ready.wait()
        await tcp_ready.wait()

        async with connect_client_session(
            "127.0.0.1",
            quic_port=quic_port,
            quic_configuration=create_quic_client_configuration(
                cafile=certificate_path,
                verify_mode=ssl.CERT_REQUIRED,
                wire_format=WireFormat.CURRENT,
            ),
            requested_session_id=71,
            timeout=5.0,
        ) as session:
            submit_stream_id = await session.send_submit(_build_submit_request(frame_id=821))
            result = await session.receive_result(timeout=5.0)

            assert session.control.transport_id is TransportId.QUIC
            assert submit_stream_id & 0x02
            assert result.packet.header.frame_id == 821

            async with session.migrate_session(
                "127.0.0.1",
                tcp_port=tcp_port,
                tcp_configuration=create_tcp_client_configuration(),
                selected_transport_id=TransportId.TCP,
                last_result_frame_id=821,
                timeout=5.0,
            ) as migration:
                try:
                    await migration.session.send_submit(_build_submit_request(frame_id=821))
                except ValueError as exc:
                    assert str(exc) == ("current migration resume_from_frame_id requires frame_id >= 822, got 821")
                else:
                    raise AssertionError("expected migrated session to reject frames below resume_from_frame_id")

                migrated_submit_stream_id = await migration.session.send_submit(_build_submit_request(frame_id=822))
                migrated_result = await migration.session.receive_result(timeout=5.0)

                try:
                    await migration.session.send_submit(_build_submit_request(frame_id=822))
                except ValueError as exc:
                    assert str(exc) == ("current migrated session frame_id must be strictly increasing: 822 <= 822")
                else:
                    raise AssertionError("expected migrated session to reject duplicate frame_id after migration")

                assert migration.accepted is True
                assert migration.previous_transport_id is TransportId.QUIC
                assert migration.current_transport_id is TransportId.TCP
                assert migration.resume_from_frame_id == 822
                assert migration.session.control.transport_id is TransportId.TCP
                assert migrated_submit_stream_id & 0x02
                assert migrated_result.packet.header.frame_id == 822

        requested_session_id, origin_frame_id = await quic_server_task
        migrate_metadata, migrated_frame_id = await tcp_server_task
        assert requested_session_id == 71
        assert origin_frame_id == 821
        assert migrate_metadata.old_transport_id is TransportId.QUIC
        assert migrate_metadata.new_transport_id is TransportId.TCP
        assert migrate_metadata.last_result_frame_id == 821
        assert migrated_frame_id == 822


async def _run_connect_client_session_rejects_mismatched_tensor_coverage() -> None:
    tcp_port = _find_free_tcp_port()
    ready_event = asyncio.Event()
    server_task = asyncio.create_task(
        _run_tcp_current_invalid_coverage_server_once(
            "127.0.0.1",
            tcp_port,
            ready_event=ready_event,
            session_id=79,
        )
    )
    await ready_event.wait()

    async with connect_client_session(
        "127.0.0.1",
        tcp_port=tcp_port,
        tcp_configuration=create_tcp_client_configuration(),
        requested_session_id=69,
        selected_transport_id=TransportId.TCP,
        timeout=5.0,
    ) as session:
        submit_stream_id = await session.send_submit(_build_submit_request(frame_id=817))
        assert submit_stream_id & 0x02
        with pytest.raises(
            ValueError,
            match=r"covered_tile_count \+ dropped_tile_count must equal tile_count",
        ):
            await session.receive_result(timeout=5.0)

    requested_session_id = await server_task
    assert requested_session_id == 69


async def _run_quic_current_origin_server_once(
    host: str,
    port: int,
    *,
    certificate_path: Path,
    private_key_path: Path,
    ready_event: asyncio.Event | None = None,
    session_id: int = 93,
) -> tuple[int, int]:
    async with serve_quic(
        host,
        port,
        configuration=create_quic_server_configuration(
            certificate_path,
            private_key_path,
            wire_format=WireFormat.CURRENT,
        ),
    ) as listener:
        if ready_event is not None:
            ready_event.set()

        connection = await listener.accept(timeout=5.0)
        hello_packet = await connection.receive_control_packet(timeout=5.0)
        hello_metadata = ClientHelloMetadata.unpack(hello_packet.metadata)
        await connection.send_control_packet(
            build_smoke_server_hello_ack_packet_with_body(
                session_id=session_id,
                active_transport_id=TransportId.QUIC,
            )
        )

        submit_packet = await connection.receive_submit_packet(timeout=5.0)
        await connection.send_result_packet(
            _build_tensor_result_packet(
                session_id=session_id,
                frame_id=int(submit_packet.header.frame_id),
            )
        )
        return hello_metadata.requested_session_id, int(submit_packet.header.frame_id)


async def _run_tcp_current_migration_target_server_once(
    host: str,
    port: int,
    *,
    ready_event: asyncio.Event | None = None,
    session_id: int = 93,
) -> tuple[SessionMigrateMetadata, int]:
    async with serve_tcp(
        host,
        port,
        configuration=create_tcp_server_configuration(idle_timeout=5.0),
    ) as listener:
        if ready_event is not None:
            ready_event.set()

        connection = await listener.accept(timeout=5.0)
        migrate_packet = await connection.receive_control_packet(timeout=5.0)
        assert migrate_packet.header.msg_type is MessageType.SESSION_MIGRATE
        migrate_metadata = SessionMigrateMetadata.unpack(migrate_packet.metadata)
        await connection.send_control_packet(
            build_session_migrate_ack_packet(
                metadata=SessionMigrateAckMetadata(
                    accept_code=0,
                    resume_from_frame_id=822,
                    grace_window_ms=50,
                    server_migrate_ts_us=1_000_100,
                ),
                session_id=session_id,
            )
        )

        submit_packet = await connection.receive_submit_packet(timeout=5.0)
        await connection.send_result_packet(
            _build_tensor_result_packet(
                session_id=session_id,
                frame_id=int(submit_packet.header.frame_id),
            )
        )
        return migrate_metadata, int(submit_packet.header.frame_id)


async def _run_tcp_current_invalid_coverage_server_once(
    host: str,
    port: int,
    *,
    ready_event: asyncio.Event | None = None,
    session_id: int = 79,
) -> int:
    async with serve_tcp(
        host,
        port,
        configuration=create_tcp_server_configuration(),
    ) as listener:
        if ready_event is not None:
            ready_event.set()

        connection = await listener.accept(timeout=5.0)
        hello_packet = await connection.receive_control_packet(timeout=5.0)
        hello_metadata = ClientHelloMetadata.unpack(hello_packet.metadata)
        await connection.send_control_packet(
            build_smoke_server_hello_ack_packet_with_body(
                session_id=session_id,
                active_transport_id=TransportId.TCP,
            )
        )

        submit_packet = await connection.receive_submit_packet(timeout=5.0)
        result_packet = _build_tensor_result_packet(
            session_id=session_id,
            frame_id=int(submit_packet.header.frame_id),
        )
        invalid_metadata = bytearray(result_packet.metadata)
        coverage_marker = struct.pack("<HH", 0xA55A, 0x5AA5)
        marked_metadata = ResultPushMetadata.unpack(result_packet.metadata)
        marked_metadata.covered_tile_count = 0xA55A
        marked_metadata.dropped_tile_count = 0x5AA5
        marked_bytes = marked_metadata.pack()
        coverage_offset = marked_bytes.index(coverage_marker)
        assert marked_bytes.count(coverage_marker) == 1
        struct.pack_into("<HH", invalid_metadata, coverage_offset, 1, 1)
        await connection.send_result_packet(
            NnrpPacket.build(
                version_major=result_packet.header.version_major,
                wire_format=result_packet.header.wire_format,
                msg_type=result_packet.header.msg_type,
                flags=result_packet.header.flags,
                session_id=result_packet.header.session_id,
                frame_id=result_packet.header.frame_id,
                view_id=result_packet.header.view_id,
                route_id=result_packet.header.route_id,
                trace_id=result_packet.header.trace_id,
                metadata=bytes(invalid_metadata),
                body=result_packet.body,
            )
        )
        return hello_metadata.requested_session_id


def _build_submit_request(frame_id: int) -> SubmitRequest:
    return SubmitRequest.tensor(
        TensorSubmitInput(
            identity=SubmitIdentity(operation_id=frame_id, frame_id=frame_id),
            policy=SubmitPolicy(
                latency_budget_ms=40,
                target_fps_x100=6000,
                budget_policy=BudgetPolicy.ALLOW_PARTIAL,
            ),
            src_width=640,
            src_height=360,
            tile_width=32,
            tile_height=32,
            tile_ids=(2, 4, 6),
            sections=(
                TensorSectionData(
                    role_id=9,
                    default_codec_id=0,
                    dtype_id=TensorDType.UINT8,
                    tile_payloads=(b"abc", b"de", b"f"),
                ),
            ),
            camera_block=b"cam-current",
            input_profile=InputProfile.DENSE_LUMA_FRAME,
            tile_index_mode=TileIndexMode.RAW_U16,
        )
    )


def _build_tensor_result_packet(*, session_id: int, frame_id: int) -> NnrpPacket:
    return build_result_push_packet(
        session_id=session_id,
        frame_id=frame_id,
        tile_ids=(2, 4, 6),
        sections=(
            TensorSectionData(
                role_id=100,
                default_codec_id=0,
                dtype_id=TensorDType.UINT8,
                tile_payloads=(b"m2a", b"m2b", b"m2c"),
            ),
        ),
        result_flags=ResultFlags.NONE,
        active_profile_id=1,
        inference_ms=9,
        queue_ms=1,
        server_total_ms=10,
        result_class=ResultClass.COMPLETE,
        payload_kind_bitmap=PayloadKind.TENSOR,
        tile_index_mode=TileIndexMode.RAW_U16,
    )


def _align_current_camera_bytes(camera_bytes: int) -> int:
    return ((camera_bytes + 7) // 8) * 8


async def _run_connect_client_session_with_probe_tcp() -> None:
    tcp_port = _find_free_tcp_port()
    ready_event = asyncio.Event()
    server_task = asyncio.create_task(
        run_tcp_smoke_server_once(
            "127.0.0.1",
            tcp_port,
            configuration=create_tcp_server_configuration(),
            ready_event=ready_event,
            session_id=76,
            active_transport_id=TransportId.TCP,
        )
    )
    await ready_event.wait()

    selection = TransportProbeSelection(
        selected_transport_id=TransportId.TCP,
        tcp_summary=TransportProbeSummary(
            transport_id=TransportId.TCP,
            results=(
                TransportProbeResult(
                    transport_id=TransportId.TCP,
                    probe_id=202,
                    probe_payload_bytes=2048,
                    client_send_ts_us=200,
                    server_recv_ts_us=210,
                    ack_recv_ts_us=220,
                ),
            ),
        ),
    )

    with patch(
        "nnrp.tools.smoke.run_parallel_transport_probes",
        return_value=selection,
    ):
        async with connect_client_session_with_probe(
            "127.0.0.1",
            quic_port=9998,
            tcp_port=tcp_port,
            tcp_configuration=create_tcp_client_configuration(),
            requested_session_id=66,
            timeout=5.0,
        ) as session:
            submit_packet = build_smoke_submit_packet(
                session_id=session.session_id,
                frame_id=813,
            )
            (
                submit_stream_id,
                result_packet,
            ) = await session.submit_and_receive_result(
                submit_packet,
                timeout=5.0,
            )

            assert session.control.transport_id is TransportId.TCP
            assert session.control.bootstrap.probe_selection is selection
            assert submit_stream_id & 0x02
            assert result_packet.header.msg_type is MessageType.RESULT_PUSH
            assert result_packet.header.frame_id == 813

    server_transcript = await server_task
    assert server_transcript.requested_session_id == 66
    assert server_transcript.frame_id == 813


async def _run_quic_probe_then_stop(
    host: str,
    port: int,
    *,
    certificate_path: Path,
    private_key_path: Path,
    ready_event: asyncio.Event,
    ack_delay_seconds: tuple[float, ...],
    probe_connection_count: int,
) -> None:
    async with serve_quic(
        host,
        port,
        configuration=create_quic_server_configuration(
            certificate_path,
            private_key_path,
            wire_format=WireFormat.CURRENT,
        ),
    ) as listener:
        ready_event.set()
        for delay in ack_delay_seconds[:probe_connection_count]:
            connection = await listener.accept(timeout=5.0)
            probe_packet = await connection.receive_control_packet(timeout=5.0)
            probe_metadata = TransportProbeMetadata.unpack(probe_packet.metadata)
            ack_metadata = TransportProbeAckMetadata(
                probe_id=probe_metadata.probe_id,
                reserved=0,
                server_recv_ts_us=probe_metadata.client_send_ts_us + 1,
            )
            if delay > 0:
                await asyncio.sleep(delay)
            await connection.send_control_packet(build_transport_probe_ack_packet(metadata=ack_metadata))


async def _run_tcp_probe_then_hello_server(
    host: str,
    port: int,
    *,
    ready_event: asyncio.Event,
    ack_delay_seconds: tuple[float, ...],
    probe_connection_count: int,
    session_id: int,
) -> ClientHelloMetadata:
    async with serve_tcp(
        host,
        port,
        configuration=create_tcp_server_configuration(idle_timeout=5.0),
    ) as listener:
        ready_event.set()
        for delay in ack_delay_seconds[:probe_connection_count]:
            connection = await listener.accept(timeout=5.0)
            probe_packet = await connection.receive_control_packet(timeout=5.0)
            probe_metadata = TransportProbeMetadata.unpack(probe_packet.metadata)
            ack_metadata = TransportProbeAckMetadata(
                probe_id=probe_metadata.probe_id,
                reserved=0,
                server_recv_ts_us=probe_metadata.client_send_ts_us + 1,
            )
            if delay > 0:
                await asyncio.sleep(delay)
            await connection.send_control_packet(build_transport_probe_ack_packet(metadata=ack_metadata))

        hello_connection = await listener.accept(timeout=5.0)
        hello_packet = await hello_connection.receive_control_packet(timeout=5.0)
        hello_metadata = ClientHelloMetadata.unpack(hello_packet.metadata)
        await hello_connection.send_control_packet(
            build_smoke_server_hello_ack_packet_with_body(
                session_id=session_id,
                active_transport_id=TransportId.TCP,
            )
        )
        return hello_metadata


async def _run_quic_flow_update_server_once(
    host: str,
    port: int,
    *,
    certificate_path: Path,
    private_key_path: Path,
    ready_event: asyncio.Event,
    session_id: int,
) -> tuple[NnrpPacket, FlowUpdateMetadata]:
    async with serve_quic(
        host,
        port,
        configuration=create_quic_server_configuration(
            certificate_path,
            private_key_path,
            wire_format=WireFormat.CURRENT,
        ),
    ) as listener:
        ready_event.set()
        connection = await listener.accept(timeout=5.0)
        hello_packet = await connection.receive_control_packet(timeout=5.0)
        hello_metadata = ClientHelloMetadata.unpack(hello_packet.metadata)
        await connection.send_control_packet(
            build_smoke_server_hello_ack_packet_with_body(
                session_id=session_id,
                active_transport_id=TransportId.QUIC,
            )
        )
        assert hello_metadata.requested_session_id == 63

        flow_packet = await connection.receive_control_packet(timeout=5.0)
        flow_metadata = FlowUpdateMetadata.unpack(flow_packet.metadata)
        await connection.send_control_packet(
            build_flow_update_packet(
                metadata=FlowUpdateMetadata(
                    scope_kind=FlowUpdateScopeKind.SESSION,
                    update_reason=FlowUpdateReason.CONGESTION,
                    backpressure_level=FlowUpdateBackpressureLevel.SOFT,
                    session_credit=4,
                    retry_after_ms=25,
                    credit_epoch=21,
                    flags=(FlowUpdateFlags.CREDIT_VALID | FlowUpdateFlags.RETRY_AFTER_VALID),
                ),
                session_id=session_id,
                trace_id=881,
            )
        )
        await asyncio.sleep(0)
        return flow_packet, flow_metadata


async def _run_tcp_flow_update_server_once(
    host: str,
    port: int,
    *,
    ready_event: asyncio.Event,
    session_id: int,
) -> tuple[NnrpPacket, FlowUpdateMetadata]:
    async with serve_tcp(
        host,
        port,
        configuration=create_tcp_server_configuration(idle_timeout=5.0),
    ) as listener:
        ready_event.set()
        connection = await listener.accept(timeout=5.0)
        hello_packet = await connection.receive_control_packet(timeout=5.0)
        hello_metadata = ClientHelloMetadata.unpack(hello_packet.metadata)
        await connection.send_control_packet(
            build_smoke_server_hello_ack_packet_with_body(
                session_id=session_id,
                active_transport_id=TransportId.TCP,
            )
        )
        assert hello_metadata.requested_session_id == 54

        flow_packet = await connection.receive_control_packet(timeout=5.0)
        flow_metadata = FlowUpdateMetadata.unpack(flow_packet.metadata)
        await connection.send_control_packet(
            build_flow_update_packet(
                metadata=FlowUpdateMetadata(
                    scope_kind=FlowUpdateScopeKind.SESSION,
                    update_reason=FlowUpdateReason.PAUSE,
                    backpressure_level=FlowUpdateBackpressureLevel.HARD,
                    session_credit=4,
                    retry_after_ms=30,
                    credit_epoch=11,
                    flags=(FlowUpdateFlags.CREDIT_VALID | FlowUpdateFlags.RETRY_AFTER_VALID),
                ),
                session_id=session_id,
                trace_id=778,
            )
        )
        return flow_packet, flow_metadata


async def _run_quic_partial_result_server_once(
    host: str,
    port: int,
    *,
    certificate_path: Path,
    private_key_path: Path,
    ready_event: asyncio.Event,
    session_id: int,
) -> tuple[int, list[int]]:
    async with serve_quic(
        host,
        port,
        configuration=create_quic_server_configuration(
            certificate_path,
            private_key_path,
            wire_format=WireFormat.CURRENT,
        ),
    ) as listener:
        ready_event.set()
        connection = await listener.accept(timeout=5.0)
        hello_packet = await connection.receive_control_packet(timeout=5.0)
        hello_metadata = ClientHelloMetadata.unpack(hello_packet.metadata)
        await connection.send_control_packet(
            build_smoke_server_hello_ack_packet_with_body(
                session_id=session_id,
                active_transport_id=TransportId.QUIC,
            )
        )

        received_frames: list[int] = []

        submit_packet_1 = await connection.receive_submit_packet(timeout=5.0)
        received_frames.append(int(submit_packet_1.header.frame_id))
        await connection.send_result_packet(
            build_smoke_result_packet(
                session_id=session_id,
                frame_id=int(submit_packet_1.header.frame_id),
                result_flags=ResultFlags.PARTIAL,
            )
        )
        await connection.send_result_packet(
            build_smoke_result_packet(
                session_id=session_id,
                frame_id=int(submit_packet_1.header.frame_id),
            )
        )

        submit_packet_2 = await connection.receive_submit_packet(timeout=5.0)
        received_frames.append(int(submit_packet_2.header.frame_id))
        await connection.send_result_packet(
            build_smoke_result_packet(
                session_id=session_id,
                frame_id=int(submit_packet_2.header.frame_id),
            )
        )

        return hello_metadata.requested_session_id, received_frames


def _find_free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _find_free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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
