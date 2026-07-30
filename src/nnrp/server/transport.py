"""Server-facing current transport helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from inspect import isawaitable

from nnrp.adapters import (
    NnrpQuicConnection,
    NnrpQuicListener,
    NnrpTcpConnection,
    NnrpTcpListener,
)
from nnrp.client.transport import SubmitHeaderContext, SubmitRequest, TypedPayload
from nnrp.core import (
    CLIENT_HELLO_TRANSPORT_POLICY_EXTENSION,
    BudgetPolicy,
    ClientHelloMetadata,
    ControlExtensionEntry,
    FlowUpdateMetadata,
    FrameSubmitMetadata,
    HeaderFlags,
    MessageType,
    NnrpPacket,
    PayloadKind,
    ResultClass,
    ResultFlags,
    ServerHelloAckMetadata,
    ServerHelloAckTransportPolicyExtension,
    TensorBodyView,
    TensorSectionData,
    TileIndexMode,
    TransportId,
    TransportPolicy,
    WireFormat,
    build_flow_update_packet,
    build_result_drop_packet,
    build_result_push_mixed_packet,
    build_result_push_packet,
    build_result_push_typed_payload_packet,
    build_server_hello_ack_transport_policy_extension,
    pack_control_extension_block,
    parse_client_hello_transport_policy_extension,
    unpack_control_extension_block,
    unpack_current_tensor_body,
    validate_frame_submit_body,
)
from nnrp.runtime import (
    PartialResultMetadata,
    PressureMetadata,
    ProgressMetadata,
    ResultDropReasonCode,
    ResultDropReasonMetadata,
    RuntimeRole,
    encode_runtime_control_metadata,
)
from nnrp.runtime.types import _FixedRuntimeMetadata
from nnrp.server.profile import ServerProfile

ServerListener = NnrpQuicListener | NnrpTcpListener
ServerConnection = NnrpQuicConnection | NnrpTcpConnection
ServerSessionResolver = Callable[
    ["ClientHelloContext"],
    "ServerSessionAcceptResolution | Awaitable[ServerSessionAcceptResolution]",
]
_SUPPORTED_PAYLOAD_KINDS = (
    PayloadKind.TENSOR
    | PayloadKind.TOKEN_CHUNK
    | PayloadKind.AUDIO_CHUNK
    | PayloadKind.VIDEO_CHUNK
    | PayloadKind.STRUCTURED_EVENT
    | PayloadKind.TOOL_DELTA
    | PayloadKind.OPAQUE_BYTES
)


@dataclass(frozen=True, slots=True)
class ClientHelloContext:
    packet: NnrpPacket
    metadata: ClientHelloMetadata
    auth_block: bytes
    control_extensions: tuple[ControlExtensionEntry, ...]


@dataclass(frozen=True, slots=True)
class ServerSessionAcceptResolution:
    session_id: int
    active_model_name: str = ""


@dataclass(frozen=True, slots=True)
class ReceivedSubmit:
    packet: NnrpPacket
    metadata: FrameSubmitMetadata
    request: SubmitRequest
    tensor_body: TensorBodyView | None = None


@dataclass(slots=True)
class ServerSession:
    connection: ServerConnection
    transport_id: TransportId
    hello: ClientHelloContext
    session_id: int
    active_model_name: str = ""
    server_profile: ServerProfile = field(default_factory=ServerProfile)

    async def receive_submit(self, timeout: float | None = None) -> ReceivedSubmit:
        packet = await self.connection.receive_submit_packet(timeout=timeout)
        if packet.header.msg_type is not MessageType.FRAME_SUBMIT:
            raise ValueError(f"expected FRAME_SUBMIT, got {packet.header.msg_type.name}")
        if packet.header.wire_format is not WireFormat.CURRENT:
            raise ValueError(f"expected current FRAME_SUBMIT, got {packet.header.wire_format.name}")
        if int(packet.header.session_id) != int(self.session_id):
            raise ValueError(f"expected session_id {self.session_id}, got {int(packet.header.session_id)}")

        metadata = FrameSubmitMetadata.unpack(packet.metadata)
        request, tensor_body = _decode_submit_request(packet, metadata)
        return ReceivedSubmit(
            packet=packet,
            metadata=metadata,
            request=request,
            tensor_body=tensor_body,
        )

    async def send_result(
        self,
        *,
        frame_id: int,
        tile_ids: tuple[int, ...] = (),
        sections: tuple[TensorSectionData, ...] = (),
        typed_payloads: tuple[TypedPayload, ...] = (),
        result_flags: ResultFlags = ResultFlags.NONE,
        active_profile_id: int = 0,
        inference_ms: int = 0,
        queue_ms: int = 0,
        server_total_ms: int = 0,
        status_code: int = 0,
        tile_index_mode: TileIndexMode = TileIndexMode.RAW_U16,
        tile_base_id: int = 0,
        result_class: ResultClass = ResultClass.COMPLETE,
        applied_budget_policy: BudgetPolicy = BudgetPolicy.NONE,
        reused_frame_id: int = 0,
        covered_tile_count: int | None = None,
        dropped_tile_count: int = 0,
        payload_kind_bitmap: PayloadKind = PayloadKind.TENSOR,
        payload_frame_count: int = 0,
        flags: HeaderFlags = HeaderFlags.NONE,
        view_id: int = 0,
        route_id: int = 0,
        trace_id: int = 0,
    ) -> int:
        normalized_typed_payloads = tuple(payload for payload in typed_payloads)
        if normalized_typed_payloads:
            typed_frames = tuple(payload.to_core_frame() for payload in normalized_typed_payloads)
            if tile_ids or sections:
                packet = build_result_push_mixed_packet(
                    session_id=self.session_id,
                    frame_id=frame_id,
                    tile_ids=tile_ids,
                    sections=sections,
                    frames=typed_frames,
                    result_flags=result_flags,
                    active_profile_id=active_profile_id,
                    inference_ms=inference_ms,
                    queue_ms=queue_ms,
                    server_total_ms=server_total_ms,
                    status_code=status_code,
                    tile_index_mode=tile_index_mode,
                    tile_base_id=tile_base_id,
                    result_class=result_class,
                    applied_budget_policy=applied_budget_policy,
                    reused_frame_id=reused_frame_id,
                    covered_tile_count=covered_tile_count,
                    dropped_tile_count=dropped_tile_count,
                    wire_format=WireFormat.CURRENT,
                    flags=flags,
                    view_id=view_id,
                    route_id=route_id,
                    trace_id=trace_id,
                )
            else:
                packet = build_result_push_typed_payload_packet(
                    session_id=self.session_id,
                    frame_id=frame_id,
                    frames=typed_frames,
                    result_flags=result_flags,
                    active_profile_id=active_profile_id,
                    inference_ms=inference_ms,
                    queue_ms=queue_ms,
                    server_total_ms=server_total_ms,
                    status_code=status_code,
                    result_class=result_class,
                    applied_budget_policy=applied_budget_policy,
                    reused_frame_id=reused_frame_id,
                    wire_format=WireFormat.CURRENT,
                    flags=flags,
                    view_id=view_id,
                    route_id=route_id,
                    trace_id=trace_id,
                )
        else:
            packet = build_result_push_packet(
                session_id=self.session_id,
                frame_id=frame_id,
                tile_ids=tile_ids,
                sections=sections,
                result_flags=result_flags,
                active_profile_id=active_profile_id,
                inference_ms=inference_ms,
                queue_ms=queue_ms,
                server_total_ms=server_total_ms,
                status_code=status_code,
                tile_index_mode=tile_index_mode,
                tile_base_id=tile_base_id,
                result_class=result_class,
                applied_budget_policy=applied_budget_policy,
                reused_frame_id=reused_frame_id,
                covered_tile_count=covered_tile_count,
                dropped_tile_count=dropped_tile_count,
                payload_kind_bitmap=payload_kind_bitmap,
                payload_frame_count=payload_frame_count,
                wire_format=WireFormat.CURRENT,
                flags=flags,
                view_id=view_id,
                route_id=route_id,
                trace_id=trace_id,
            )
        return await self.connection.send_result_packet(packet)

    async def send_result_drop(
        self,
        *,
        frame_id: int,
        flags: HeaderFlags = HeaderFlags.NONE,
        view_id: int = 0,
        route_id: int = 0,
        trace_id: int = 0,
    ) -> int:
        packet = build_result_drop_packet(
            session_id=self.session_id,
            frame_id=frame_id,
            wire_format=WireFormat.CURRENT,
            flags=flags,
            view_id=view_id,
            route_id=route_id,
            trace_id=trace_id,
        )
        return await self.connection.send_result_packet(packet)

    async def send_flow_update(
        self,
        metadata: FlowUpdateMetadata,
        *,
        trace_id: int = 0,
        flags: HeaderFlags = HeaderFlags.NONE,
    ) -> None:
        await self.connection.send_control_packet(
            build_flow_update_packet(
                metadata=metadata,
                session_id=self.session_id,
                trace_id=trace_id,
                flags=flags,
            )
        )

    async def send_progress(
        self,
        *,
        operation_id: int,
        progress_sequence: int,
        stage_code: int,
        percent_x100: int = 0xFFFF,
        object_id: int = 0,
        body: bytes | bytearray | memoryview = b"",
        frame_id: int = 0,
        flags: HeaderFlags = HeaderFlags.NONE,
        view_id: int = 0,
        route_id: int = 0,
        trace_id: int = 0,
    ) -> None:
        metadata = ProgressMetadata(
            operation_id=operation_id,
            progress_sequence=progress_sequence,
            stage_code=stage_code,
            percent_x100=percent_x100,
            object_id=object_id,
            body_bytes=memoryview(body).nbytes,
        )
        await self._send_runtime_control_packet(
            MessageType.PROGRESS,
            metadata,
            body=body,
            frame_id=frame_id,
            flags=flags,
            view_id=view_id,
            route_id=route_id,
            trace_id=trace_id,
        )

    async def send_partial_result(
        self,
        *,
        operation_id: int,
        result_sequence: int,
        object_id: int = 0,
        delta_sequence: int = 0,
        body: bytes | bytearray | memoryview = b"",
        control_flags: int = 0,
        frame_id: int = 0,
        flags: HeaderFlags = HeaderFlags.NONE,
        view_id: int = 0,
        route_id: int = 0,
        trace_id: int = 0,
    ) -> None:
        metadata = PartialResultMetadata(
            operation_id=operation_id,
            result_sequence=result_sequence,
            object_id=object_id,
            delta_sequence=delta_sequence,
            body_bytes=memoryview(body).nbytes,
            flags=control_flags,
        )
        await self._send_runtime_control_packet(
            MessageType.PARTIAL_RESULT,
            metadata,
            body=body,
            frame_id=frame_id,
            flags=flags,
            view_id=view_id,
            route_id=route_id,
            trace_id=trace_id,
        )

    async def send_result_drop_reason(
        self,
        *,
        operation_id: int,
        result_sequence: int,
        drop_reason_code: ResultDropReasonCode | int,
        source_role: RuntimeRole | int = RuntimeRole.SERVER,
        diagnostic: bytes | bytearray | memoryview = b"",
        control_flags: int = 0,
        frame_id: int = 0,
        flags: HeaderFlags = HeaderFlags.NONE,
        view_id: int = 0,
        route_id: int = 0,
        trace_id: int = 0,
    ) -> None:
        metadata = ResultDropReasonMetadata(
            operation_id=operation_id,
            result_sequence=result_sequence,
            drop_reason_code=drop_reason_code,
            source_role=source_role,
            flags=control_flags,
            diagnostic_bytes=memoryview(diagnostic).nbytes,
        )
        await self._send_runtime_control_packet(
            MessageType.RESULT_DROP_REASON,
            metadata,
            body=diagnostic,
            frame_id=frame_id,
            flags=flags,
            view_id=view_id,
            route_id=route_id,
            trace_id=trace_id,
        )

    async def send_backpressure(
        self,
        *,
        scope_id: int,
        credit_window: int,
        pressure_level: int,
        pressure_reason: int,
        retry_after_ms: int = 0,
        control_flags: int = 0,
        frame_id: int = 0,
        flags: HeaderFlags = HeaderFlags.NONE,
        view_id: int = 0,
        route_id: int = 0,
        trace_id: int = 0,
    ) -> None:
        await self._send_pressure_control(
            MessageType.BACKPRESSURE,
            scope_id=scope_id,
            credit_window=credit_window,
            pressure_level=pressure_level,
            pressure_reason=pressure_reason,
            retry_after_ms=retry_after_ms,
            control_flags=control_flags,
            frame_id=frame_id,
            flags=flags,
            view_id=view_id,
            route_id=route_id,
            trace_id=trace_id,
        )

    async def send_credit_update(
        self,
        *,
        scope_id: int,
        credit_window: int,
        pressure_level: int = 0,
        pressure_reason: int = 0,
        retry_after_ms: int = 0,
        control_flags: int = 0,
        frame_id: int = 0,
        flags: HeaderFlags = HeaderFlags.NONE,
        view_id: int = 0,
        route_id: int = 0,
        trace_id: int = 0,
    ) -> None:
        await self._send_pressure_control(
            MessageType.CREDIT_UPDATE,
            scope_id=scope_id,
            credit_window=credit_window,
            pressure_level=pressure_level,
            pressure_reason=pressure_reason,
            retry_after_ms=retry_after_ms,
            control_flags=control_flags,
            frame_id=frame_id,
            flags=flags,
            view_id=view_id,
            route_id=route_id,
            trace_id=trace_id,
        )

    async def close(self) -> None:
        self.connection.close()
        wait_closed = getattr(self.connection, "wait_closed", None)
        if callable(wait_closed):
            await wait_closed()

    async def _send_pressure_control(
        self,
        message_type: MessageType,
        *,
        scope_id: int,
        credit_window: int,
        pressure_level: int,
        pressure_reason: int,
        retry_after_ms: int,
        control_flags: int,
        frame_id: int,
        flags: HeaderFlags,
        view_id: int,
        route_id: int,
        trace_id: int,
    ) -> None:
        metadata = PressureMetadata(
            scope_id=scope_id,
            credit_window=credit_window,
            pressure_level=pressure_level,
            pressure_reason=pressure_reason,
            retry_after_ms=retry_after_ms,
            flags=control_flags,
        )
        await self._send_runtime_control_packet(
            message_type,
            metadata,
            frame_id=frame_id,
            flags=flags,
            view_id=view_id,
            route_id=route_id,
            trace_id=trace_id,
        )

    async def _send_runtime_control_packet(
        self,
        message_type: MessageType,
        metadata: _FixedRuntimeMetadata,
        *,
        body: bytes | bytearray | memoryview = b"",
        frame_id: int,
        flags: HeaderFlags,
        view_id: int,
        route_id: int,
        trace_id: int,
    ) -> None:
        packet = NnrpPacket.build(
            version_major=1,
            wire_format=WireFormat.CURRENT,
            msg_type=message_type,
            flags=flags,
            session_id=self.session_id,
            frame_id=frame_id,
            view_id=view_id,
            route_id=route_id,
            trace_id=trace_id,
            metadata=encode_runtime_control_metadata(message_type, metadata, tail=body),
        )
        await self.connection.send_control_packet(packet)


async def accept_server_session(
    listener: ServerListener,
    *,
    session_id: int | None = None,
    active_model_name: str = "",
    server_profile: ServerProfile | None = None,
    timeout: float = 10.0,
    session_resolver: ServerSessionResolver | None = None,
) -> ServerSession:
    connection = await listener.accept(timeout=timeout)
    return await accept_server_connection(
        connection,
        session_id=session_id,
        active_model_name=active_model_name,
        server_profile=server_profile,
        timeout=timeout,
        session_resolver=session_resolver,
    )


async def accept_server_connection(
    connection: ServerConnection,
    *,
    first_packet: NnrpPacket | None = None,
    session_id: int | None = None,
    active_model_name: str = "",
    server_profile: ServerProfile | None = None,
    timeout: float = 10.0,
    session_resolver: ServerSessionResolver | None = None,
) -> ServerSession:
    try:
        hello_packet = first_packet
        if hello_packet is None:
            hello_packet = await connection.receive_control_packet(timeout=timeout)
        if hello_packet.header.msg_type is not MessageType.CLIENT_HELLO:
            raise ValueError(f"expected CLIENT_HELLO, got {hello_packet.header.msg_type.name}")
        if hello_packet.header.wire_format is not WireFormat.CURRENT:
            raise ValueError(f"expected current CLIENT_HELLO, got {hello_packet.header.wire_format.name}")

        metadata = ClientHelloMetadata.unpack(hello_packet.metadata)
        resolved_profile = server_profile or ServerProfile()
        hello = ClientHelloContext(
            packet=hello_packet,
            metadata=metadata,
            auth_block=_parse_hello_auth_block(hello_packet.body, metadata.auth_bytes),
            control_extensions=_parse_hello_control_extensions(
                hello_packet.body,
                auth_bytes=metadata.auth_bytes,
                control_extension_bytes=metadata.control_extension_bytes,
            ),
        )
        resolved_session_id = int(metadata.requested_session_id) if session_id is None else int(session_id)
        resolved_active_model_name = active_model_name
        if session_resolver is not None:
            resolution = session_resolver(hello)
            if isawaitable(resolution):
                resolution = await resolution
            resolved_session_id = int(resolution.session_id)
            resolved_active_model_name = resolution.active_model_name
        await connection.send_control_packet(
            _build_server_hello_ack_packet(
                session_id=resolved_session_id,
                server_profile=resolved_profile,
                active_transport_id=_transport_id_for_connection(connection),
                requested_transport_policy=_requested_transport_policy(hello.control_extensions),
            )
        )
        return ServerSession(
            connection=connection,
            transport_id=_transport_id_for_connection(connection),
            hello=hello,
            session_id=resolved_session_id,
            active_model_name=resolved_active_model_name,
            server_profile=resolved_profile,
        )
    except Exception:
        connection.close()
        wait_closed = getattr(connection, "wait_closed", None)
        if callable(wait_closed):
            await wait_closed()
        raise


def _transport_id_for_connection(connection: ServerConnection) -> TransportId:
    if isinstance(connection, NnrpQuicConnection):
        return TransportId.QUIC
    return TransportId.TCP


def _parse_hello_auth_block(body: bytes, auth_bytes: int) -> bytes:
    if auth_bytes < 0 or auth_bytes > len(body):
        raise ValueError(f"CLIENT_HELLO auth_bytes/body mismatch: auth_bytes={auth_bytes}, body={len(body)}")
    return body[-auth_bytes:] if auth_bytes else b""


def _parse_hello_control_extensions(
    body: bytes,
    *,
    auth_bytes: int,
    control_extension_bytes: int,
) -> tuple[ControlExtensionEntry, ...]:
    if auth_bytes < 0 or auth_bytes > len(body):
        raise ValueError(f"CLIENT_HELLO auth_bytes/body mismatch: auth_bytes={auth_bytes}, body={len(body)}")
    extension_payload = body[:-auth_bytes] if auth_bytes else body
    if control_extension_bytes != len(extension_payload):
        raise ValueError(
            "CLIENT_HELLO control_extension_bytes/body mismatch: "
            f"control_extension_bytes={control_extension_bytes}, body={len(extension_payload)}"
        )
    if not extension_payload:
        return ()
    return unpack_control_extension_block(extension_payload)


def _build_server_hello_ack_packet(
    *,
    session_id: int,
    server_profile: ServerProfile,
    active_transport_id: TransportId,
    requested_transport_policy: TransportPolicy,
) -> NnrpPacket:
    body = pack_control_extension_block(
        (
            build_server_hello_ack_transport_policy_extension(
                ServerHelloAckTransportPolicyExtension(
                    transport_policy=requested_transport_policy,
                    accepted_transport_policy=requested_transport_policy,
                    active_transport_id=active_transport_id,
                )
            ),
        )
    )
    metadata = ServerHelloAckMetadata(
        selected_version_major=1,
        selected_wire_format=int(WireFormat.CURRENT),
        auth_status=0,
        session_id=session_id,
        accepted_profile_bitmap=0x0001,
        accepted_payload_kind_bitmap=int(_SUPPORTED_PAYLOAD_KINDS),
        accepted_codec_bitmap=0x0003,
        accepted_compression_bitmap=0x0003,
        accepted_dtype_bitmap=0x001F,
        accepted_layout_bitmap=0x0003,
        cache_digest_bitmap=0,
        cache_object_bitmap=0,
        max_cache_entries=0,
        max_cache_bytes=0,
        max_lane_count=1,
        target_cadence_x100=0,
        latency_budget_ms=0,
        quality_tier=0,
        degrade_policy=0,
        max_concurrent_frames=server_profile.max_concurrent_frames,
        max_body_bytes=server_profile.max_body_bytes,
        token_ttl_ms=0,
        retry_after_ms=0,
        control_extension_bytes=len(body),
        server_flags=0,
    )
    return NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.SERVER_HELLO_ACK,
        flags=HeaderFlags.ACK_REQUIRED,
        session_id=session_id,
        metadata=metadata.pack(),
        body=body,
    )


def _requested_transport_policy(control_extensions: tuple[ControlExtensionEntry, ...]) -> TransportPolicy:
    for entry in control_extensions:
        if entry.ext_type == CLIENT_HELLO_TRANSPORT_POLICY_EXTENSION:
            return parse_client_hello_transport_policy_extension(entry).transport_policy
    return TransportPolicy.AUTO


def _decode_submit_request(
    packet: NnrpPacket,
    metadata: FrameSubmitMetadata,
) -> tuple[SubmitRequest, TensorBodyView | None]:
    body_view = validate_frame_submit_body(metadata, packet.body)
    tensor_body: TensorBodyView | None = None
    if metadata.payload_kind_bitmap & PayloadKind.TENSOR:
        tensor_body = unpack_current_tensor_body(
            body_view,
            section_count=metadata.section_count,
            tile_count=metadata.tile_count,
        )

    request = SubmitRequest(
        operation_id=metadata.operation_id,
        frame_id=int(packet.header.frame_id),
        header=SubmitHeaderContext(
            flags=packet.header.flags,
            view_id=int(packet.header.view_id),
            route_id=int(packet.header.route_id),
            trace_id=int(packet.header.trace_id),
        ),
        metadata=metadata,
        body=bytes(packet.body),
    )
    return request, tensor_body
