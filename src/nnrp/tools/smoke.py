"""Minimal QUIC smoke helpers for cross-SDK bring-up."""

from __future__ import annotations

import argparse
import asyncio
import ssl
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from aioquic.quic.configuration import QuicConfiguration

from nnrp.adapters import (
    NnrpTcpClientConfiguration,
    NnrpTcpServerConfiguration,
    connect_quic,
    connect_tcp,
    create_quic_client_configuration,
    create_quic_server_configuration,
    create_tcp_client_configuration,
    create_tcp_server_configuration,
    serve_quic,
    serve_tcp,
)
from nnrp.client.profile import resolve_client_hello_transport_policy
from nnrp.client.transport import (
    TransportProbeResult,
    TransportProbeSelection,
    TransportProbeSummary,
)
from nnrp.client.transport import (
    build_client_hello_packet as build_transport_client_hello_packet,
)
from nnrp.core import (
    TENSOR_PROFILE_CACHE_OBJECT_BITMAP,
    BudgetPolicy,
    ClientHelloMetadata,
    ClientHelloTransportPolicyExtension,
    FrameSubmitMetadata,
    InputProfile,
    MessageType,
    NnrpPacket,
    ResultClass,
    ResultFlags,
    ResultPushMetadata,
    ServerHelloAckMetadata,
    ServerHelloAckTransportPolicyExtension,
    TensorDType,
    TensorSectionData,
    TileIndexMode,
    TransportId,
    TransportPolicy,
    TransportProbeAckMetadata,
    TransportProbeMetadata,
    WireFormat,
    build_frame_submit_packet,
    build_ping_packet,
    build_result_push_packet,
    build_server_hello_ack_transport_policy_extension,
    build_transport_probe_ack_packet,
    build_transport_probe_packet,
    pack_control_extension_block,
    unpack_current_tensor_body,
    validate_frame_submit_body,
    validate_result_push_body,
)


@dataclass(frozen=True, slots=True)
class SmokeTranscript:
    role: str
    negotiated_session_id: int
    requested_session_id: int
    frame_id: int
    control_stream_id: int
    submit_stream_id: int
    result_stream_id: int
    submit_tile_count: int
    result_tile_count: int


def resolve_local_dial_transport_policy(
    *,
    selected_transport_id: TransportId = TransportId.UNSPECIFIED,
    forced_transport_id: TransportId = TransportId.UNSPECIFIED,
) -> ClientHelloTransportPolicyExtension | None:
    return resolve_client_hello_transport_policy(
        selected_transport_id=selected_transport_id,
        forced_transport_id=forced_transport_id,
    )


def build_smoke_client_hello_packet(
    *,
    requested_session_id: int = 1,
    transport_policy: TransportPolicy | None = None,
    preferred_transport_id: TransportId = TransportId.UNSPECIFIED,
    selected_transport_id: TransportId = TransportId.UNSPECIFIED,
    forced_transport_id: TransportId = TransportId.UNSPECIFIED,
    wire_format: WireFormat = WireFormat.CURRENT,
) -> NnrpPacket:
    return build_smoke_client_hello_packet_with_auth(
        requested_session_id=requested_session_id,
        auth_block=b"",
        transport_policy=transport_policy,
        preferred_transport_id=preferred_transport_id,
        selected_transport_id=selected_transport_id,
        forced_transport_id=forced_transport_id,
        wire_format=wire_format,
    )


def build_smoke_client_hello_packet_with_auth(
    *,
    requested_session_id: int = 1,
    auth_block: bytes = b"",
    control_extensions: bytes = b"",
    transport_policy: TransportPolicy | None = None,
    preferred_transport_id: TransportId = TransportId.UNSPECIFIED,
    selected_transport_id: TransportId = TransportId.UNSPECIFIED,
    forced_transport_id: TransportId = TransportId.UNSPECIFIED,
    wire_format: WireFormat = WireFormat.CURRENT,
) -> NnrpPacket:
    return build_transport_client_hello_packet(
        requested_session_id=requested_session_id,
        auth_block=auth_block,
        control_extensions=control_extensions,
        transport_policy=transport_policy,
        preferred_transport_id=preferred_transport_id,
        selected_transport_id=selected_transport_id,
        forced_transport_id=forced_transport_id,
        wire_format=wire_format,
    )


async def run_quic_transport_probe(
    host: str,
    port: int,
    *,
    configuration: QuicConfiguration | None = None,
    probe_payload_bytes: int = 32 * 1024,
    probe_id: int | None = None,
    timeout: float = 10.0,
) -> TransportProbeResult:
    client_configuration = configuration or create_quic_client_configuration(
        wire_format=WireFormat.CURRENT,
    )
    async with connect_quic(host, port, configuration=client_configuration) as connection:
        return await _run_transport_probe_on_connection(
            connection,
            transport_id=TransportId.QUIC,
            probe_payload_bytes=probe_payload_bytes,
            probe_id=probe_id,
            timeout=timeout,
        )


async def run_tcp_transport_probe(
    host: str,
    port: int,
    *,
    configuration: NnrpTcpClientConfiguration | None = None,
    probe_payload_bytes: int = 32 * 1024,
    probe_id: int | None = None,
    timeout: float = 10.0,
) -> TransportProbeResult:
    client_configuration = configuration or create_tcp_client_configuration(
        connect_timeout=timeout,
        idle_timeout=timeout,
    )
    async with connect_tcp(host, port, configuration=client_configuration) as connection:
        return await _run_transport_probe_on_connection(
            connection,
            transport_id=TransportId.TCP,
            probe_payload_bytes=probe_payload_bytes,
            probe_id=probe_id,
            timeout=timeout,
        )


async def run_parallel_transport_probes(
    host: str,
    *,
    quic_port: int,
    tcp_port: int,
    quic_configuration: QuicConfiguration | None = None,
    tcp_configuration: NnrpTcpClientConfiguration | None = None,
    probe_payload_bytes: int = 32 * 1024,
    sample_count: int = 3,
    include_warmup_probe: bool = False,
    timeout: float = 10.0,
) -> TransportProbeSelection:
    if sample_count <= 0:
        raise ValueError("sample_count must be positive")

    quic_summary, tcp_summary = await asyncio.gather(
        _run_transport_probe_series(
            run_quic_transport_probe,
            host,
            quic_port,
            transport_id=TransportId.QUIC,
            configuration=quic_configuration,
            probe_payload_bytes=probe_payload_bytes,
            sample_count=sample_count,
            include_warmup_probe=include_warmup_probe,
            timeout=timeout,
        ),
        _run_transport_probe_series(
            run_tcp_transport_probe,
            host,
            tcp_port,
            transport_id=TransportId.TCP,
            configuration=tcp_configuration,
            probe_payload_bytes=probe_payload_bytes,
            sample_count=sample_count,
            include_warmup_probe=include_warmup_probe,
            timeout=timeout,
        ),
    )
    selected_summary = _select_transport_probe_summary(quic_summary=quic_summary, tcp_summary=tcp_summary)
    return TransportProbeSelection(
        selected_transport_id=selected_summary.transport_id,
        quic_summary=quic_summary,
        tcp_summary=tcp_summary,
    )


async def run_quic_probe_server_once(
    host: str,
    port: int,
    *,
    configuration: QuicConfiguration,
    ready_event: asyncio.Event | None = None,
    timeout: float = 10.0,
    ack_delay_seconds: float | Sequence[float] = 0.0,
    connection_count: int = 1,
) -> TransportProbeAckMetadata:
    async with serve_quic(host, port, configuration=configuration) as listener:
        if ready_event is not None:
            ready_event.set()

        ack_delays = _normalize_probe_ack_delays(ack_delay_seconds=ack_delay_seconds, connection_count=connection_count)
        last_ack_metadata: TransportProbeAckMetadata | None = None
        for delay in ack_delays:
            connection = await listener.accept(timeout=timeout)
            last_ack_metadata = await _handle_probe_server_connection(
                connection,
                timeout=timeout,
                ack_delay_seconds=delay,
            )

        if last_ack_metadata is None:
            raise ValueError("connection_count must be positive")
        return last_ack_metadata


async def run_tcp_probe_server_once(
    host: str,
    port: int,
    *,
    configuration: NnrpTcpServerConfiguration | None = None,
    ready_event: asyncio.Event | None = None,
    timeout: float = 10.0,
    ack_delay_seconds: float | Sequence[float] = 0.0,
    connection_count: int = 1,
) -> TransportProbeAckMetadata:
    server_configuration = configuration or create_tcp_server_configuration(idle_timeout=timeout)
    async with serve_tcp(host, port, configuration=server_configuration) as listener:
        if ready_event is not None:
            ready_event.set()

        ack_delays = _normalize_probe_ack_delays(ack_delay_seconds=ack_delay_seconds, connection_count=connection_count)
        last_ack_metadata: TransportProbeAckMetadata | None = None
        for delay in ack_delays:
            connection = await listener.accept(timeout=timeout)
            last_ack_metadata = await _handle_probe_server_connection(
                connection,
                timeout=timeout,
                ack_delay_seconds=delay,
            )

        if last_ack_metadata is None:
            raise ValueError("connection_count must be positive")
        return last_ack_metadata


async def _run_transport_probe_on_connection(
    connection,
    *,
    transport_id: TransportId,
    probe_payload_bytes: int,
    probe_id: int | None,
    timeout: float,
) -> TransportProbeResult:
    if probe_payload_bytes <= 0:
        raise ValueError("probe_payload_bytes must be positive")

    actual_probe_id = _default_probe_id() if probe_id is None else probe_id
    probe_body = b"P" * probe_payload_bytes
    client_send_ts_us = _now_us()
    metadata = TransportProbeMetadata(
        probe_id=actual_probe_id,
        probe_payload_bytes=probe_payload_bytes,
        client_send_ts_us=client_send_ts_us,
    )
    await connection.send_control_packet(
        build_transport_probe_packet(
            metadata=metadata,
            body=probe_body,
        )
    )
    ack_packet = await connection.receive_control_packet(timeout=timeout)
    ack_recv_ts_us = _now_us()
    if ack_packet.header.msg_type is not MessageType.TRANSPORT_PROBE_ACK:
        raise ValueError(f"expected TRANSPORT_PROBE_ACK, got {ack_packet.header.msg_type.name}")

    ack_metadata = TransportProbeAckMetadata.unpack(ack_packet.metadata)
    if ack_metadata.probe_id != actual_probe_id:
        raise ValueError(f"transport probe ack id mismatch: expected {actual_probe_id}, got {ack_metadata.probe_id}")

    return TransportProbeResult(
        transport_id=transport_id,
        probe_id=actual_probe_id,
        probe_payload_bytes=probe_payload_bytes,
        client_send_ts_us=client_send_ts_us,
        server_recv_ts_us=ack_metadata.server_recv_ts_us,
        ack_recv_ts_us=ack_recv_ts_us,
    )


async def _handle_probe_server_connection(
    connection,
    *,
    timeout: float,
    ack_delay_seconds: float,
) -> TransportProbeAckMetadata:
    probe_packet = await connection.receive_control_packet(timeout=timeout)
    if probe_packet.header.msg_type is not MessageType.TRANSPORT_PROBE:
        raise ValueError(f"expected TRANSPORT_PROBE, got {probe_packet.header.msg_type.name}")

    probe_metadata = TransportProbeMetadata.unpack(probe_packet.metadata)
    if probe_metadata.probe_payload_bytes != len(probe_packet.body):
        raise ValueError("transport probe body length did not match probe_payload_bytes metadata")

    ack_metadata = TransportProbeAckMetadata(
        probe_id=probe_metadata.probe_id,
        reserved=0,
        server_recv_ts_us=_now_us(),
    )
    if ack_delay_seconds > 0:
        await asyncio.sleep(ack_delay_seconds)
    await connection.send_control_packet(build_transport_probe_ack_packet(metadata=ack_metadata))
    return ack_metadata


async def run_quic_smoke_hello_server_once(
    host: str,
    port: int,
    *,
    configuration: QuicConfiguration,
    ready_event: asyncio.Event | None = None,
    session_id: int = 7,
    transport_policy: TransportPolicy | None = None,
    accepted_transport_policy: TransportPolicy | None = None,
    active_transport_id: TransportId = TransportId.UNSPECIFIED,
    timeout: float = 10.0,
) -> ClientHelloMetadata:
    async with serve_quic(host, port, configuration=configuration) as listener:
        if ready_event is not None:
            ready_event.set()

        connection = await listener.accept(timeout=timeout)
        return await _handle_smoke_hello_server_connection(
            connection,
            session_id=session_id,
            transport_policy=transport_policy,
            accepted_transport_policy=accepted_transport_policy,
            active_transport_id=active_transport_id,
            timeout=timeout,
        )


async def run_tcp_smoke_hello_server_once(
    host: str,
    port: int,
    *,
    configuration: NnrpTcpServerConfiguration | None = None,
    ready_event: asyncio.Event | None = None,
    session_id: int = 7,
    transport_policy: TransportPolicy | None = None,
    accepted_transport_policy: TransportPolicy | None = None,
    active_transport_id: TransportId = TransportId.UNSPECIFIED,
    timeout: float = 10.0,
) -> ClientHelloMetadata:
    server_configuration = configuration or create_tcp_server_configuration(idle_timeout=timeout)
    async with serve_tcp(host, port, configuration=server_configuration) as listener:
        if ready_event is not None:
            ready_event.set()

        connection = await listener.accept(timeout=timeout)
        return await _handle_smoke_hello_server_connection(
            connection,
            session_id=session_id,
            transport_policy=transport_policy,
            accepted_transport_policy=accepted_transport_policy,
            active_transport_id=active_transport_id,
            timeout=timeout,
        )


def _default_probe_id() -> int:
    return int(time.monotonic_ns() & 0xFFFFFFFF)


def _unpack_inline_submit_tensor_body(
    metadata: FrameSubmitMetadata,
    body: bytes,
):
    return unpack_current_tensor_body(
        validate_frame_submit_body(metadata, body),
        section_count=metadata.section_count,
        tile_count=metadata.tile_count,
    )


def _unpack_inline_result_tensor_body(
    metadata: ResultPushMetadata,
    body: bytes,
):
    return unpack_current_tensor_body(
        validate_result_push_body(metadata, body),
        section_count=metadata.section_count,
        tile_count=metadata.tile_count,
    )


def _now_us() -> int:
    return time.monotonic_ns() // 1_000


def _select_transport_probe_result(
    *,
    quic_result: TransportProbeResult | None,
    tcp_result: TransportProbeResult | None,
) -> TransportProbeResult:
    candidates = [result for result in (quic_result, tcp_result) if result is not None]
    if not candidates:
        raise RuntimeError("transport probing failed on both QUIC and TCP bindings")
    return max(candidates, key=lambda result: result.effective_throughput_bytes_per_sec)


async def _run_transport_probe_series(
    probe_runner,
    host: str,
    port: int,
    *,
    transport_id: TransportId,
    configuration,
    probe_payload_bytes: int,
    sample_count: int,
    include_warmup_probe: bool,
    timeout: float,
) -> TransportProbeSummary:
    scored_results: list[TransportProbeResult] = []
    failure_count = 0
    total_attempts = sample_count + int(include_warmup_probe)

    for attempt_index in range(total_attempts):
        try:
            result = await probe_runner(
                host,
                port,
                configuration=configuration,
                probe_payload_bytes=probe_payload_bytes,
                probe_id=_default_probe_id(),
                timeout=timeout,
            )
        except Exception:
            if attempt_index >= int(include_warmup_probe):
                failure_count += 1
            continue

        if attempt_index >= int(include_warmup_probe):
            scored_results.append(result)

    return TransportProbeSummary(
        transport_id=transport_id,
        results=tuple(scored_results),
        failure_count=failure_count,
    )


def _select_transport_probe_summary(
    *,
    quic_summary: TransportProbeSummary,
    tcp_summary: TransportProbeSummary,
) -> TransportProbeSummary:
    candidates = [summary for summary in (quic_summary, tcp_summary) if summary.success_count > 0]
    if not candidates:
        raise RuntimeError("transport probing failed on both QUIC and TCP bindings")
    return max(
        candidates,
        key=lambda summary: (
            summary.success_count,
            summary.median_throughput_bytes_per_sec,
            -summary.median_round_trip_us,
        ),
    )


def _normalize_probe_ack_delays(
    *,
    ack_delay_seconds: float | Sequence[float],
    connection_count: int,
) -> tuple[float, ...]:
    if connection_count <= 0:
        raise ValueError("connection_count must be positive")
    if isinstance(ack_delay_seconds, Sequence) and not isinstance(ack_delay_seconds, (bytes, bytearray, str)):
        delays = tuple(float(delay) for delay in ack_delay_seconds)
        if len(delays) != connection_count:
            raise ValueError("ack_delay_seconds sequence length must match connection_count")
        return delays
    return tuple(float(ack_delay_seconds) for _ in range(connection_count))


async def _handle_smoke_hello_server_connection(
    connection,
    *,
    session_id: int,
    transport_policy: TransportPolicy | None,
    accepted_transport_policy: TransportPolicy | None,
    active_transport_id: TransportId,
    timeout: float,
) -> ClientHelloMetadata:
    hello_packet = await connection.receive_control_packet(timeout=timeout)
    if hello_packet.header.msg_type is not MessageType.CLIENT_HELLO:
        raise ValueError(f"expected CLIENT_HELLO, got {hello_packet.header.msg_type.name}")

    hello_metadata = ClientHelloMetadata.unpack(hello_packet.metadata)
    await connection.send_control_packet(
        build_smoke_server_hello_ack_packet_with_body(
            session_id=session_id,
            transport_policy=transport_policy,
            accepted_transport_policy=accepted_transport_policy,
            active_transport_id=active_transport_id,
        )
    )
    return hello_metadata


def build_smoke_server_hello_ack_packet(
    *,
    session_id: int,
    wire_format: WireFormat = WireFormat.CURRENT,
) -> NnrpPacket:
    return build_smoke_server_hello_ack_packet_with_body(
        session_id=session_id,
        body=b"",
        wire_format=wire_format,
    )


def build_smoke_server_hello_ack_packet_with_body(
    *,
    session_id: int,
    body: bytes = b"",
    control_extensions: bytes = b"",
    transport_policy: TransportPolicy | None = None,
    accepted_transport_policy: TransportPolicy | None = None,
    active_transport_id: TransportId = TransportId.UNSPECIFIED,
    wire_format: WireFormat = WireFormat.CURRENT,
) -> NnrpPacket:
    helper_extensions = _build_server_hello_ack_helper_extensions(
        transport_policy=transport_policy,
        accepted_transport_policy=accepted_transport_policy,
        active_transport_id=active_transport_id,
    )
    packet_body = helper_extensions + bytes(control_extensions) + bytes(body)
    metadata = ServerHelloAckMetadata(
        selected_version_major=1,
        selected_wire_format=int(wire_format),
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
        max_lane_count=1,
        max_concurrent_frames=2,
        target_cadence_x100=6000,
        latency_budget_ms=50,
        quality_tier=2,
        degrade_policy=0,
        max_body_bytes=4 * 1024 * 1024,
        token_ttl_ms=300000,
        retry_after_ms=0,
        control_extension_bytes=len(helper_extensions) + len(control_extensions),
        server_flags=0x00000001,
    ).pack()
    return NnrpPacket.build(
        version_major=1,
        wire_format=wire_format,
        msg_type=MessageType.SERVER_HELLO_ACK,
        metadata=metadata,
        body=packet_body,
    )


def _build_server_hello_ack_helper_extensions(
    *,
    transport_policy: TransportPolicy | None,
    accepted_transport_policy: TransportPolicy | None,
    active_transport_id: TransportId,
) -> bytes:
    entries = []

    if (
        transport_policy is not None
        or accepted_transport_policy is not None
        or active_transport_id is not TransportId.UNSPECIFIED
    ):
        entries.append(
            build_server_hello_ack_transport_policy_extension(
                ServerHelloAckTransportPolicyExtension(
                    transport_policy=transport_policy or TransportPolicy.AUTO,
                    accepted_transport_policy=accepted_transport_policy or TransportPolicy.AUTO,
                    active_transport_id=active_transport_id,
                )
            )
        )

    if not entries:
        return b""
    return pack_control_extension_block(entries)


def build_smoke_ping_packet(*, session_id: int, trace_id: int = 0) -> NnrpPacket:
    return build_ping_packet(session_id=session_id, trace_id=trace_id)


def build_smoke_close_packet(*, session_id: int, reason: str = "smoke-close") -> NnrpPacket:
    return NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.CLOSE,
        session_id=session_id,
        metadata=b"",
        body=reason.encode("utf-8"),
    )


def build_smoke_submit_packet(
    *,
    session_id: int,
    frame_id: int,
    view_id: int = 0,
    wire_format: WireFormat = WireFormat.CURRENT,
) -> NnrpPacket:
    if wire_format is not WireFormat.CURRENT:
        raise ValueError("smoke submit packets support current only")
    return build_frame_submit_packet(
        session_id=session_id,
        frame_id=frame_id,
        operation_id=frame_id,
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
        view_id=view_id,
    )


def build_smoke_result_packet(
    *,
    session_id: int,
    frame_id: int,
    view_id: int = 0,
    result_flags: ResultFlags = ResultFlags.NONE,
    wire_format: WireFormat = WireFormat.CURRENT,
) -> NnrpPacket:
    if wire_format is not WireFormat.CURRENT:
        raise ValueError("smoke result packets support current only")
    is_partial = bool(result_flags & ResultFlags.PARTIAL)
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
        result_flags=result_flags,
        active_profile_id=1,
        inference_ms=12,
        queue_ms=2,
        server_total_ms=14,
        result_class=ResultClass.PARTIAL if is_partial else ResultClass.COMPLETE,
        applied_budget_policy=BudgetPolicy.ALLOW_PARTIAL if is_partial else BudgetPolicy.NONE,
        covered_tile_count=2 if is_partial else 3,
        dropped_tile_count=1 if is_partial else 0,
        tile_index_mode=TileIndexMode.RAW_U16,
        view_id=view_id,
    )


async def run_quic_smoke_server_once(
    host: str,
    port: int,
    *,
    configuration: QuicConfiguration,
    ready_event: asyncio.Event | None = None,
    session_id: int = 7,
    transport_policy: TransportPolicy | None = None,
    accepted_transport_policy: TransportPolicy | None = None,
    active_transport_id: TransportId = TransportId.UNSPECIFIED,
    wire_format: WireFormat = WireFormat.CURRENT,
    timeout: float = 10.0,
) -> SmokeTranscript:
    async with serve_quic(host, port, configuration=configuration) as listener:
        if ready_event is not None:
            ready_event.set()

        connection = await listener.accept(timeout=timeout)
        hello_packet = await connection.receive_control_packet(timeout=timeout)
        if hello_packet.header.msg_type is not MessageType.CLIENT_HELLO:
            raise ValueError(f"expected CLIENT_HELLO, got {hello_packet.header.msg_type.name}")

        hello_metadata = ClientHelloMetadata.unpack(hello_packet.metadata)
        await connection.send_control_packet(
            build_smoke_server_hello_ack_packet_with_body(
                session_id=session_id,
                transport_policy=transport_policy,
                accepted_transport_policy=accepted_transport_policy,
                active_transport_id=active_transport_id,
                wire_format=wire_format,
            )
        )

        submit_packet = await connection.receive_submit_packet(timeout=timeout)
        if submit_packet.header.msg_type is not MessageType.FRAME_SUBMIT:
            raise ValueError(f"expected FRAME_SUBMIT, got {submit_packet.header.msg_type.name}")

        submit_metadata = FrameSubmitMetadata.unpack(submit_packet.metadata)
        _unpack_inline_submit_tensor_body(submit_metadata, submit_packet.body)
        result_packet = build_smoke_result_packet(
            session_id=session_id,
            frame_id=submit_packet.header.frame_id,
            view_id=submit_packet.header.view_id,
            wire_format=wire_format,
        )
        result_stream_id = await connection.send_result_packet(result_packet)
        await asyncio.sleep(0)
        result_metadata = ResultPushMetadata.unpack(result_packet.metadata)
        _unpack_inline_result_tensor_body(result_metadata, result_packet.body)
        control_stream_id = connection.control_stream_id
        submit_stream_id = connection.last_submit_stream_id
        if control_stream_id is None:
            raise ValueError("control stream was not established")
        if submit_stream_id is None:
            raise ValueError("submit stream id was not captured")

        return SmokeTranscript(
            role="server",
            negotiated_session_id=session_id,
            requested_session_id=hello_metadata.requested_session_id,
            frame_id=submit_packet.header.frame_id,
            control_stream_id=control_stream_id,
            submit_stream_id=submit_stream_id,
            result_stream_id=result_stream_id,
            submit_tile_count=submit_metadata.tile_count,
            result_tile_count=result_metadata.tile_count,
        )


async def run_tcp_smoke_server_once(
    host: str,
    port: int,
    *,
    configuration: NnrpTcpServerConfiguration | None = None,
    ready_event: asyncio.Event | None = None,
    session_id: int = 7,
    transport_policy: TransportPolicy | None = None,
    accepted_transport_policy: TransportPolicy | None = None,
    active_transport_id: TransportId = TransportId.UNSPECIFIED,
    wire_format: WireFormat = WireFormat.CURRENT,
    timeout: float = 10.0,
) -> SmokeTranscript:
    server_configuration = configuration or create_tcp_server_configuration(idle_timeout=timeout)
    async with serve_tcp(host, port, configuration=server_configuration) as listener:
        if ready_event is not None:
            ready_event.set()

        connection = await listener.accept(timeout=timeout)
        hello_packet = await connection.receive_control_packet(timeout=timeout)
        if hello_packet.header.msg_type is not MessageType.CLIENT_HELLO:
            raise ValueError(f"expected CLIENT_HELLO, got {hello_packet.header.msg_type.name}")

        hello_metadata = ClientHelloMetadata.unpack(hello_packet.metadata)
        await connection.send_control_packet(
            build_smoke_server_hello_ack_packet_with_body(
                session_id=session_id,
                transport_policy=transport_policy,
                accepted_transport_policy=accepted_transport_policy,
                active_transport_id=active_transport_id,
                wire_format=wire_format,
            )
        )

        submit_packet = await connection.receive_submit_packet(timeout=timeout)
        if submit_packet.header.msg_type is not MessageType.FRAME_SUBMIT:
            raise ValueError(f"expected FRAME_SUBMIT, got {submit_packet.header.msg_type.name}")

        submit_metadata = FrameSubmitMetadata.unpack(submit_packet.metadata)
        _unpack_inline_submit_tensor_body(submit_metadata, submit_packet.body)
        result_packet = build_smoke_result_packet(
            session_id=session_id,
            frame_id=submit_packet.header.frame_id,
            view_id=submit_packet.header.view_id,
            wire_format=wire_format,
        )
        result_stream_id = await connection.send_result_packet(result_packet)
        result_metadata = ResultPushMetadata.unpack(result_packet.metadata)
        _unpack_inline_result_tensor_body(result_metadata, result_packet.body)

        return SmokeTranscript(
            role="server",
            negotiated_session_id=session_id,
            requested_session_id=hello_metadata.requested_session_id,
            frame_id=submit_packet.header.frame_id,
            control_stream_id=connection.control_stream_id,
            submit_stream_id=connection.last_submit_stream_id or 0,
            result_stream_id=result_stream_id,
            submit_tile_count=submit_metadata.tile_count,
            result_tile_count=result_metadata.tile_count,
        )


async def run_quic_smoke_client(
    host: str,
    port: int,
    *,
    configuration: QuicConfiguration | None = None,
    requested_session_id: int = 1,
    frame_id: int = 101,
    transport_policy: TransportPolicy | None = None,
    preferred_transport_id: TransportId = TransportId.UNSPECIFIED,
    selected_transport_id: TransportId = TransportId.UNSPECIFIED,
    forced_transport_id: TransportId = TransportId.UNSPECIFIED,
    timeout: float = 10.0,
) -> SmokeTranscript:
    async with connect_quic(host, port, configuration=configuration) as connection:
        await connection.send_control_packet(
            build_smoke_client_hello_packet_with_auth(
                requested_session_id=requested_session_id,
                transport_policy=transport_policy,
                preferred_transport_id=preferred_transport_id,
                selected_transport_id=selected_transport_id,
                forced_transport_id=forced_transport_id,
            )
        )
        ack_packet = await connection.receive_control_packet(timeout=timeout)
        if ack_packet.header.msg_type is not MessageType.SERVER_HELLO_ACK:
            raise ValueError(f"expected SERVER_HELLO_ACK, got {ack_packet.header.msg_type.name}")

        ack_metadata = ServerHelloAckMetadata.unpack(ack_packet.metadata)
        submit_packet = build_smoke_submit_packet(session_id=ack_metadata.session_id, frame_id=frame_id)
        submit_stream_id = await connection.send_submit_packet(submit_packet)
        submit_metadata = FrameSubmitMetadata.unpack(submit_packet.metadata)
        _unpack_inline_submit_tensor_body(submit_metadata, submit_packet.body)
        result_packet = await connection.receive_result_packet(timeout=timeout)
        if result_packet.header.msg_type is not MessageType.RESULT_PUSH:
            raise ValueError(f"expected RESULT_PUSH, got {result_packet.header.msg_type.name}")

        result_metadata = ResultPushMetadata.unpack(result_packet.metadata)
        _unpack_inline_result_tensor_body(result_metadata, result_packet.body)
        control_stream_id = connection.control_stream_id
        result_stream_id = connection.last_result_stream_id
        if control_stream_id is None:
            raise ValueError("control stream was not established")
        if result_stream_id is None:
            raise ValueError("result stream id was not captured")

        return SmokeTranscript(
            role="client",
            negotiated_session_id=ack_metadata.session_id,
            requested_session_id=requested_session_id,
            frame_id=frame_id,
            control_stream_id=control_stream_id,
            submit_stream_id=submit_stream_id,
            result_stream_id=result_stream_id,
            submit_tile_count=submit_metadata.tile_count,
            result_tile_count=result_metadata.tile_count,
        )


async def run_tcp_smoke_client(
    host: str,
    port: int,
    *,
    configuration: NnrpTcpClientConfiguration | None = None,
    requested_session_id: int = 1,
    frame_id: int = 101,
    transport_policy: TransportPolicy | None = None,
    preferred_transport_id: TransportId = TransportId.UNSPECIFIED,
    selected_transport_id: TransportId = TransportId.UNSPECIFIED,
    forced_transport_id: TransportId = TransportId.UNSPECIFIED,
    timeout: float = 10.0,
) -> SmokeTranscript:
    client_configuration = configuration or create_tcp_client_configuration(idle_timeout=timeout)
    async with connect_tcp(
        host,
        port,
        configuration=client_configuration,
    ) as connection:
        await connection.send_control_packet(
            build_smoke_client_hello_packet_with_auth(
                requested_session_id=requested_session_id,
                transport_policy=transport_policy,
                preferred_transport_id=preferred_transport_id,
                selected_transport_id=selected_transport_id,
                forced_transport_id=forced_transport_id,
            )
        )
        ack_packet = await connection.receive_control_packet(timeout=timeout)
        if ack_packet.header.msg_type is not MessageType.SERVER_HELLO_ACK:
            raise ValueError(f"expected SERVER_HELLO_ACK, got {ack_packet.header.msg_type.name}")

        ack_metadata = ServerHelloAckMetadata.unpack(ack_packet.metadata)
        submit_packet = build_smoke_submit_packet(session_id=ack_metadata.session_id, frame_id=frame_id)
        submit_stream_id = await connection.send_submit_packet(submit_packet)
        submit_metadata = FrameSubmitMetadata.unpack(submit_packet.metadata)
        _unpack_inline_submit_tensor_body(submit_metadata, submit_packet.body)
        result_packet = await connection.receive_result_packet(timeout=timeout)
        if result_packet.header.msg_type is not MessageType.RESULT_PUSH:
            raise ValueError(f"expected RESULT_PUSH, got {result_packet.header.msg_type.name}")

        result_metadata = ResultPushMetadata.unpack(result_packet.metadata)
        _unpack_inline_result_tensor_body(result_metadata, result_packet.body)
        result_stream_id = connection.last_result_stream_id
        if result_stream_id is None:
            raise ValueError("result stream id was not captured")

        return SmokeTranscript(
            role="client",
            negotiated_session_id=ack_metadata.session_id,
            requested_session_id=requested_session_id,
            frame_id=frame_id,
            control_stream_id=connection.control_stream_id,
            submit_stream_id=submit_stream_id,
            result_stream_id=result_stream_id,
            submit_tile_count=submit_metadata.tile_count,
            result_tile_count=result_metadata.tile_count,
        )


def render_smoke_transcript(transcript: SmokeTranscript) -> str:
    return "\n".join(
        [
            f"role={transcript.role}",
            f"requested_session_id={transcript.requested_session_id}",
            f"negotiated_session_id={transcript.negotiated_session_id}",
            f"frame_id={transcript.frame_id}",
            f"control_stream_id={transcript.control_stream_id}",
            f"submit_stream_id={transcript.submit_stream_id}",
            f"result_stream_id={transcript.result_stream_id}",
            f"submit_tile_count={transcript.submit_tile_count}",
            f"result_tile_count={transcript.result_tile_count}",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run minimal NNRP current QUIC smoke flows")
    subparsers = parser.add_subparsers(dest="command", required=True)

    client_parser = subparsers.add_parser("client", help="Connect to a remote QUIC smoke server")
    client_parser.add_argument("--host", default="127.0.0.1")
    client_parser.add_argument("--port", type=int, required=True)
    client_parser.add_argument("--requested-session-id", type=int, default=1)
    client_parser.add_argument("--frame-id", type=int, default=101)
    client_parser.add_argument("--timeout", type=float, default=10.0)
    client_parser.add_argument("--cafile", type=Path)
    client_parser.add_argument(
        "--verify-peer",
        action="store_true",
        help="Enable certificate verification for the remote QUIC peer",
    )

    server_parser = subparsers.add_parser("server-once", help="Serve one QUIC smoke session and exit")
    server_parser.add_argument("--host", default="127.0.0.1")
    server_parser.add_argument("--port", type=int, required=True)
    server_parser.add_argument("--certificate", type=Path, required=True)
    server_parser.add_argument("--private-key", type=Path, required=True)
    server_parser.add_argument("--session-id", type=int, default=7)
    server_parser.add_argument("--timeout", type=float, default=10.0)

    args = parser.parse_args()
    if args.command == "client":
        verify_mode = ssl.CERT_REQUIRED if args.verify_peer else ssl.CERT_NONE
        transcript = asyncio.run(
            run_quic_smoke_client(
                args.host,
                args.port,
                configuration=create_quic_client_configuration(
                    verify_mode=verify_mode,
                    cafile=args.cafile,
                ),
                requested_session_id=args.requested_session_id,
                frame_id=args.frame_id,
                timeout=args.timeout,
            )
        )
    else:
        transcript = asyncio.run(
            run_quic_smoke_server_once(
                args.host,
                args.port,
                configuration=create_quic_server_configuration(args.certificate, args.private_key),
                session_id=args.session_id,
                timeout=args.timeout,
            )
        )

    print(render_smoke_transcript(transcript))


if __name__ == "__main__":
    main()
