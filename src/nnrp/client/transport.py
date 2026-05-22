"""Client-facing current transport helpers."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from statistics import median

from aioquic.quic.configuration import QuicConfiguration

from nnrp.adapters import (
    NnrpQuicConnection,
    NnrpTcpClientConfiguration,
    NnrpTcpConnection,
    connect_quic,
    connect_tcp,
    create_quic_client_configuration,
    create_tcp_client_configuration,
)
from nnrp.client.profile import ClientProfile, resolve_client_hello_transport_policy
from nnrp.core import (
    TENSOR_PROFILE_CACHE_OBJECT_BITMAP,
    BudgetPolicy,
    CacheObjectKind,
    ClientHelloMetadata,
    ClientHelloTransportPolicyExtension,
    ControlExtensionEntry,
    FlowUpdateMetadata,
    HeaderFlags,
    InputProfile,
    MessageType,
    NnrpPacket,
    ObjectReferenceBlock,
    PayloadKind,
    ResultPushMetadata,
    ServerHelloAckMetadata,
    SessionMigrateAckMetadata,
    SessionMigrateMetadata,
    SubmitMode,
    TensorBodyView,
    TensorSectionData,
    TileIndexMode,
    TransportId,
    TransportPolicy,
    TypedPayloadFrame,
    WireFormat,
    build_audio_chunk_frame,
    build_client_hello_transport_policy_extension,
    build_flow_update_packet,
    build_frame_submit_mixed_packet,
    build_frame_submit_packet,
    build_frame_submit_typed_payload_packet,
    build_opaque_bytes_frame,
    build_session_migrate_packet,
    build_structured_event_frame,
    build_token_chunk_frame,
    build_tool_delta_frame,
    build_typed_payload_frame,
    build_video_chunk_frame,
    pack_control_extension_block,
    unpack_inline_object_blocks,
    unpack_tensor_body,
    unpack_tile_index_block,
    unpack_typed_payload_frames,
    validate_result_push_body,
    validate_result_push_tensor_coverage,
)

_PAYLOAD_KIND_ORDER = (
    PayloadKind.TENSOR,
    PayloadKind.TOKEN_CHUNK,
    PayloadKind.AUDIO_CHUNK,
    PayloadKind.VIDEO_CHUNK,
    PayloadKind.STRUCTURED_EVENT,
    PayloadKind.TOOL_DELTA,
    PayloadKind.OPAQUE_BYTES,
)


@dataclass(frozen=True, slots=True)
class TransportProbeResult:
    transport_id: TransportId
    probe_id: int
    probe_payload_bytes: int
    client_send_ts_us: int
    server_recv_ts_us: int
    ack_recv_ts_us: int

    @property
    def round_trip_us(self) -> int:
        return max(self.ack_recv_ts_us - self.client_send_ts_us, 1)

    @property
    def effective_throughput_bytes_per_sec(self) -> float:
        return self.probe_payload_bytes * 1_000_000.0 / self.round_trip_us


@dataclass(frozen=True, slots=True)
class TransportProbeSummary:
    transport_id: TransportId
    results: tuple[TransportProbeResult, ...] = ()
    failure_count: int = 0

    @property
    def success_count(self) -> int:
        return len(self.results)

    @property
    def median_throughput_bytes_per_sec(self) -> float:
        if not self.results:
            raise ValueError("transport probe summary does not contain successful samples")
        return float(median(result.effective_throughput_bytes_per_sec for result in self.results))

    @property
    def median_round_trip_us(self) -> int:
        if not self.results:
            raise ValueError("transport probe summary does not contain successful samples")
        return int(median(result.round_trip_us for result in self.results))

    @property
    def representative_result(self) -> TransportProbeResult | None:
        if not self.results:
            return None
        target_throughput = self.median_throughput_bytes_per_sec
        return min(
            self.results,
            key=lambda result: (
                abs(result.effective_throughput_bytes_per_sec - target_throughput),
                result.round_trip_us,
                result.probe_id,
            ),
        )


@dataclass(frozen=True, slots=True)
class TransportProbeSelection:
    selected_transport_id: TransportId
    quic_summary: TransportProbeSummary | None = None
    tcp_summary: TransportProbeSummary | None = None
    quic_result: TransportProbeResult | None = None
    tcp_result: TransportProbeResult | None = None

    def __post_init__(self) -> None:
        if self.quic_summary is None and self.quic_result is not None:
            object.__setattr__(
                self,
                "quic_summary",
                TransportProbeSummary(
                    transport_id=TransportId.QUIC,
                    results=(self.quic_result,),
                ),
            )
        elif self.quic_summary is not None:
            if self.quic_summary.transport_id is not TransportId.QUIC:
                raise ValueError("quic_summary must carry TransportId.QUIC")
            if self.quic_result is None:
                object.__setattr__(self, "quic_result", self.quic_summary.representative_result)

        if self.tcp_summary is None and self.tcp_result is not None:
            object.__setattr__(
                self,
                "tcp_summary",
                TransportProbeSummary(
                    transport_id=TransportId.TCP,
                    results=(self.tcp_result,),
                ),
            )
        elif self.tcp_summary is not None:
            if self.tcp_summary.transport_id is not TransportId.TCP:
                raise ValueError("tcp_summary must carry TransportId.TCP")
            if self.tcp_result is None:
                object.__setattr__(self, "tcp_result", self.tcp_summary.representative_result)

    @property
    def selected_summary(self) -> TransportProbeSummary:
        if self.selected_transport_id is TransportId.QUIC and self.quic_summary is not None:
            return self.quic_summary
        if self.selected_transport_id is TransportId.TCP and self.tcp_summary is not None:
            return self.tcp_summary
        raise ValueError("selected transport summary is not available")

    @property
    def selected_result(self) -> TransportProbeResult:
        if self.selected_transport_id is TransportId.QUIC and self.quic_result is not None:
            return self.quic_result
        if self.selected_transport_id is TransportId.TCP and self.tcp_result is not None:
            return self.tcp_result
        raise ValueError("selected transport result is not available")


@dataclass(frozen=True, slots=True)
class ClientTransportPlan:
    selected_transport_id: TransportId = TransportId.UNSPECIFIED
    transport_policy_extension: ClientHelloTransportPolicyExtension | None = None

    def build_client_hello_packet(
        self,
        *,
        requested_session_id: int = 1,
        auth_block: bytes = b"",
        requested_model: str | None = None,
        control_extensions: bytes = b"",
        client_profile: ClientProfile | None = None,
        wire_format: WireFormat = WireFormat.CURRENT,
    ) -> NnrpPacket:
        return build_client_hello_packet(
            requested_session_id=requested_session_id,
            auth_block=auth_block,
            requested_model=requested_model,
            control_extensions=control_extensions,
            client_profile=client_profile,
            transport_policy_extension=self.transport_policy_extension,
            wire_format=wire_format,
        )


@dataclass(frozen=True, slots=True)
class ClientTransportBootstrap:
    plan: ClientTransportPlan
    hello_packet: NnrpPacket
    probe_selection: TransportProbeSelection | None = None


CurrentControlConnection = NnrpQuicConnection | NnrpTcpConnection


@dataclass(slots=True)
class ClientControlBootstrapSession:
    transport_id: TransportId
    bootstrap: ClientTransportBootstrap
    connection: CurrentControlConnection
    ack_packet: NnrpPacket
    ack_metadata: ServerHelloAckMetadata

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
                session_id=self.ack_metadata.session_id,
                trace_id=trace_id,
                flags=flags,
            )
        )

    async def receive_flow_update(
        self,
        timeout: float | None = None,
    ) -> tuple[NnrpPacket, FlowUpdateMetadata]:
        packet = await self.connection.receive_control_packet(timeout=timeout)
        if packet.header.msg_type is not MessageType.FLOW_UPDATE:
            raise ValueError(f"expected FLOW_UPDATE, got {packet.header.msg_type.name}")
        return packet, FlowUpdateMetadata.unpack(packet.metadata)

    async def send_session_migrate(
        self,
        metadata: SessionMigrateMetadata,
        *,
        route_id: int = 0,
        trace_id: int = 0,
        flags: HeaderFlags = HeaderFlags.NONE,
        body: bytes = b"",
    ) -> None:
        await self.connection.send_control_packet(
            build_session_migrate_packet(
                metadata=metadata,
                session_id=self.ack_metadata.session_id,
                route_id=route_id,
                trace_id=trace_id,
                flags=flags,
                body=body,
            )
        )

    async def receive_session_migrate_ack(
        self,
        timeout: float | None = None,
    ) -> tuple[NnrpPacket, SessionMigrateAckMetadata]:
        packet = await self.connection.receive_control_packet(timeout=timeout)
        if packet.header.msg_type is not MessageType.SESSION_MIGRATE_ACK:
            raise ValueError(f"expected SESSION_MIGRATE_ACK, got {packet.header.msg_type.name}")
        return packet, SessionMigrateAckMetadata.unpack(packet.metadata)


@dataclass(frozen=True, slots=True)
class TypedPayload:
    payload_kind: PayloadKind
    payload: bytes
    profile_id: int = 0
    descriptor_flags: int = 0

    def __post_init__(self) -> None:
        frame = build_typed_payload_frame(
            self.payload_kind,
            self.payload,
            profile_id=self.profile_id,
            descriptor_flags=self.descriptor_flags,
        )
        object.__setattr__(self, "payload_kind", frame.payload_kind)
        object.__setattr__(self, "payload", frame.payload)
        object.__setattr__(self, "profile_id", frame.profile_id)
        object.__setattr__(self, "descriptor_flags", frame.descriptor_flags)

    @classmethod
    def from_core_frame(cls, frame: TypedPayloadFrame) -> TypedPayload:
        return cls(
            payload_kind=frame.payload_kind,
            payload=frame.payload,
            profile_id=frame.profile_id,
            descriptor_flags=frame.descriptor_flags,
        )

    @classmethod
    def token_chunk(cls, payload: bytes, *, profile_id: int = 0, descriptor_flags: int = 0) -> TypedPayload:
        return cls(
            payload_kind=PayloadKind.TOKEN_CHUNK,
            payload=payload,
            profile_id=profile_id,
            descriptor_flags=descriptor_flags,
        )

    @classmethod
    def audio_chunk(cls, payload: bytes, *, profile_id: int = 0, descriptor_flags: int = 0) -> TypedPayload:
        return cls(
            payload_kind=PayloadKind.AUDIO_CHUNK,
            payload=payload,
            profile_id=profile_id,
            descriptor_flags=descriptor_flags,
        )

    @classmethod
    def video_chunk(cls, payload: bytes, *, profile_id: int = 0, descriptor_flags: int = 0) -> TypedPayload:
        return cls(
            payload_kind=PayloadKind.VIDEO_CHUNK,
            payload=payload,
            profile_id=profile_id,
            descriptor_flags=descriptor_flags,
        )

    @classmethod
    def structured_event(
        cls,
        payload: bytes,
        *,
        profile_id: int = 0,
        descriptor_flags: int = 0,
    ) -> TypedPayload:
        return cls(
            payload_kind=PayloadKind.STRUCTURED_EVENT,
            payload=payload,
            profile_id=profile_id,
            descriptor_flags=descriptor_flags,
        )

    @classmethod
    def tool_delta(cls, payload: bytes, *, profile_id: int = 0, descriptor_flags: int = 0) -> TypedPayload:
        return cls(
            payload_kind=PayloadKind.TOOL_DELTA,
            payload=payload,
            profile_id=profile_id,
            descriptor_flags=descriptor_flags,
        )

    @classmethod
    def opaque_bytes(
        cls,
        payload: bytes,
        *,
        profile_id: int = 0,
        descriptor_flags: int = 0,
    ) -> TypedPayload:
        return cls(
            payload_kind=PayloadKind.OPAQUE_BYTES,
            payload=payload,
            profile_id=profile_id,
            descriptor_flags=descriptor_flags,
        )

    def to_core_frame(self) -> TypedPayloadFrame:
        if self.descriptor_flags != 0:
            raise ValueError("descriptor_flags must be 0 in current typed payload descriptors")
        builders = {
            PayloadKind.TOKEN_CHUNK: build_token_chunk_frame,
            PayloadKind.AUDIO_CHUNK: build_audio_chunk_frame,
            PayloadKind.VIDEO_CHUNK: build_video_chunk_frame,
            PayloadKind.STRUCTURED_EVENT: build_structured_event_frame,
            PayloadKind.TOOL_DELTA: build_tool_delta_frame,
            PayloadKind.OPAQUE_BYTES: build_opaque_bytes_frame,
        }
        builder = builders.get(self.payload_kind)
        if builder is None:
            return build_typed_payload_frame(
                self.payload_kind,
                self.payload,
                profile_id=self.profile_id,
                descriptor_flags=self.descriptor_flags,
            )
        return builder(self.payload, profile_id=self.profile_id)


@dataclass(frozen=True, slots=True)
class SubmitRequest:
    frame_id: int
    src_width: int = 0
    src_height: int = 0
    tile_width: int = 0
    tile_height: int = 0
    tile_ids: tuple[int, ...] = ()
    sections: tuple[TensorSectionData, ...] = ()
    camera_block: bytes = b""
    frame_class: int = 0
    input_profile: InputProfile = InputProfile.UNSPECIFIED
    tile_index_mode: TileIndexMode = TileIndexMode.RAW_U16
    latency_budget_ms: int = 0
    target_fps_x100: int = 0
    retry_of_frame: int = 0
    tile_base_id: int = 0
    submit_mode: SubmitMode = SubmitMode.INLINE
    object_ref_mask: int = 0
    camera_reference: ObjectReferenceBlock | None = None
    tile_index_reference: ObjectReferenceBlock | None = None
    tensor_section_table_reference: ObjectReferenceBlock | None = None
    budget_policy: BudgetPolicy = BudgetPolicy.NONE
    dependency_frame_id: int = 0
    loss_tolerance_policy: int = 0xFF
    payload_kind_bitmap: PayloadKind = PayloadKind.TENSOR
    payload_frame_count: int = 0
    typed_payloads: tuple[TypedPayload, ...] = ()
    view_id: int = 0
    route_id: int = 0
    trace_id: int = 0
    flags: HeaderFlags = HeaderFlags.NONE


@dataclass(frozen=True, slots=True)
class Result:
    packet: NnrpPacket
    metadata: ResultPushMetadata | None = None
    tensor_body: TensorBodyView | None = None
    typed_payloads: tuple[TypedPayload, ...] = ()

    @property
    def is_drop(self) -> bool:
        return self.packet.header.msg_type is MessageType.RESULT_DROP

    @property
    def is_push(self) -> bool:
        return self.packet.header.msg_type is MessageType.RESULT_PUSH

    @property
    def payload_kinds(self) -> tuple[PayloadKind, ...]:
        if self.metadata is None:
            return ()
        return tuple(
            payload_kind for payload_kind in _PAYLOAD_KIND_ORDER if self.metadata.payload_kind_bitmap & payload_kind
        )

    def has_payload_kind(self, payload_kind: PayloadKind | int) -> bool:
        if self.metadata is None:
            return False
        normalized_payload_kind = PayloadKind(payload_kind)
        if normalized_payload_kind is PayloadKind.NONE:
            return False
        return bool(self.metadata.payload_kind_bitmap & normalized_payload_kind)

    @property
    def payload_frame_count(self) -> int:
        if self.metadata is None:
            return 0
        return int(self.metadata.payload_frame_count)

    def typed_payloads_of_kind(self, payload_kind: PayloadKind | int) -> tuple[TypedPayload, ...]:
        normalized_payload_kind = PayloadKind(payload_kind)
        if normalized_payload_kind in {PayloadKind.NONE, PayloadKind.TENSOR}:
            return ()
        return tuple(payload for payload in self.typed_payloads if payload.payload_kind is normalized_payload_kind)

    @property
    def token_chunks(self) -> tuple[bytes, ...]:
        return tuple(payload.payload for payload in self.typed_payloads_of_kind(PayloadKind.TOKEN_CHUNK))

    @property
    def audio_chunks(self) -> tuple[bytes, ...]:
        return tuple(payload.payload for payload in self.typed_payloads_of_kind(PayloadKind.AUDIO_CHUNK))

    @property
    def video_chunks(self) -> tuple[bytes, ...]:
        return tuple(payload.payload for payload in self.typed_payloads_of_kind(PayloadKind.VIDEO_CHUNK))

    @property
    def structured_events(self) -> tuple[bytes, ...]:
        return tuple(payload.payload for payload in self.typed_payloads_of_kind(PayloadKind.STRUCTURED_EVENT))

    @property
    def tool_deltas(self) -> tuple[bytes, ...]:
        return tuple(payload.payload for payload in self.typed_payloads_of_kind(PayloadKind.TOOL_DELTA))

    @property
    def opaque_bytes_payloads(self) -> tuple[bytes, ...]:
        return tuple(payload.payload for payload in self.typed_payloads_of_kind(PayloadKind.OPAQUE_BYTES))

    @property
    def has_tensor_coverage(self) -> bool:
        return self.has_payload_kind(PayloadKind.TENSOR)

    @property
    def tensor_covered_tile_count(self) -> int | None:
        if self.metadata is None or not self.has_tensor_coverage:
            return None
        return int(self.metadata.covered_tile_count)

    @property
    def tensor_dropped_tile_count(self) -> int | None:
        if self.metadata is None or not self.has_tensor_coverage:
            return None
        return int(self.metadata.dropped_tile_count)

    @property
    def tile_ids(self) -> tuple[int, ...]:
        if self.metadata is None or self.tensor_body is None:
            return ()
        return unpack_tile_index_block(
            self.tensor_body.tile_index_block,
            mode=TileIndexMode.RAW_U16 if self.metadata.tile_index_bytes else TileIndexMode.DENSE_RANGE,
            tile_count=self.metadata.tile_count,
            tile_base_id=self.metadata.tile_base_id,
        )

    @property
    def sections(self):
        if self.tensor_body is None:
            return ()
        return self.tensor_body.sections


@dataclass(frozen=True, slots=True)
class MigrationOutcome:
    previous_transport_id: TransportId
    current_transport_id: TransportId
    session: ClientSession
    ack_packet: NnrpPacket
    ack_metadata: SessionMigrateAckMetadata

    @property
    def accepted(self) -> bool:
        return int(self.ack_metadata.accept_code) == 0

    @property
    def resume_from_frame_id(self) -> int:
        return int(self.ack_metadata.resume_from_frame_id)


@dataclass(frozen=True, slots=True)
class PathHealthSample:
    round_trip_us: int = 0
    effective_throughput_bytes_per_sec: float = 0.0
    timed_out: bool = False

    def __post_init__(self) -> None:
        if self.round_trip_us < 0:
            raise ValueError("round_trip_us must be non-negative")
        if self.effective_throughput_bytes_per_sec < 0:
            raise ValueError("effective_throughput_bytes_per_sec must be non-negative")

    @classmethod
    def from_probe_result(cls, result: TransportProbeResult) -> PathHealthSample:
        return cls(
            round_trip_us=result.round_trip_us,
            effective_throughput_bytes_per_sec=result.effective_throughput_bytes_per_sec,
            timed_out=False,
        )


@dataclass(frozen=True, slots=True)
class MigrationTriggerPolicy:
    window_size: int = 4
    consecutive_degraded_windows: int = 2
    max_timeout_rate: float = 0.25
    min_median_throughput_bytes_per_sec: float | None = None
    max_median_round_trip_us: int | None = None
    max_jitter_us: int | None = None

    def __post_init__(self) -> None:
        if self.window_size <= 0:
            raise ValueError("window_size must be greater than zero")
        if self.consecutive_degraded_windows <= 0:
            raise ValueError("consecutive_degraded_windows must be greater than zero")
        if not 0.0 <= self.max_timeout_rate <= 1.0:
            raise ValueError("max_timeout_rate must be between 0.0 and 1.0")
        if self.min_median_throughput_bytes_per_sec is not None and self.min_median_throughput_bytes_per_sec < 0:
            raise ValueError("min_median_throughput_bytes_per_sec must be non-negative")
        if self.max_median_round_trip_us is not None and self.max_median_round_trip_us < 0:
            raise ValueError("max_median_round_trip_us must be non-negative")
        if self.max_jitter_us is not None and self.max_jitter_us < 0:
            raise ValueError("max_jitter_us must be non-negative")


@dataclass(frozen=True, slots=True)
class MigrationTriggerSnapshot:
    sample_count: int
    median_round_trip_us: int | None
    median_effective_throughput_bytes_per_sec: float | None
    jitter_us: int | None
    timeout_rate: float
    degraded_reasons: tuple[str, ...]
    consecutive_degraded_windows: int
    should_trigger: bool

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded_reasons)


@dataclass(slots=True)
class MigrationTriggerMonitor:
    active_transport_id: TransportId
    policy: MigrationTriggerPolicy = field(default_factory=MigrationTriggerPolicy)
    _samples: deque[PathHealthSample] = field(default_factory=deque, init=False, repr=False)
    _consecutive_degraded_windows: int = field(default=0, init=False, repr=False)

    def observe(self, sample: PathHealthSample) -> MigrationTriggerSnapshot:
        self._samples.append(sample)
        if len(self._samples) > self.policy.window_size:
            self._samples.popleft()
        return self._snapshot()

    def observe_probe_result(self, result: TransportProbeResult) -> MigrationTriggerSnapshot:
        return self.observe(PathHealthSample.from_probe_result(result))

    def _snapshot(self) -> MigrationTriggerSnapshot:
        samples = tuple(self._samples)
        if len(samples) < self.policy.window_size:
            return MigrationTriggerSnapshot(
                sample_count=len(samples),
                median_round_trip_us=None,
                median_effective_throughput_bytes_per_sec=None,
                jitter_us=None,
                timeout_rate=0.0,
                degraded_reasons=(),
                consecutive_degraded_windows=self._consecutive_degraded_windows,
                should_trigger=False,
            )

        timeout_count = sum(1 for sample in samples if sample.timed_out)
        timeout_rate = timeout_count / len(samples)
        round_trip_values = [sample.round_trip_us for sample in samples if not sample.timed_out]
        throughput_values = [sample.effective_throughput_bytes_per_sec for sample in samples if not sample.timed_out]
        median_round_trip_us = int(median(round_trip_values)) if round_trip_values else None
        median_throughput = float(median(throughput_values)) if throughput_values else None
        jitter_us = None
        if len(round_trip_values) >= 2:
            jitter_us = int(
                median(
                    abs(current - previous)
                    for previous, current in zip(round_trip_values, round_trip_values[1:], strict=False)
                )
            )

        degraded_reasons: list[str] = []
        if timeout_rate > self.policy.max_timeout_rate:
            degraded_reasons.append("timeout_rate")
        if (
            self.policy.min_median_throughput_bytes_per_sec is not None
            and median_throughput is not None
            and median_throughput < self.policy.min_median_throughput_bytes_per_sec
        ):
            degraded_reasons.append("throughput")
        if (
            self.policy.max_median_round_trip_us is not None
            and median_round_trip_us is not None
            and median_round_trip_us > self.policy.max_median_round_trip_us
        ):
            degraded_reasons.append("round_trip")
        if self.policy.max_jitter_us is not None and jitter_us is not None and jitter_us > self.policy.max_jitter_us:
            degraded_reasons.append("jitter")

        if degraded_reasons:
            self._consecutive_degraded_windows += 1
        else:
            self._consecutive_degraded_windows = 0

        return MigrationTriggerSnapshot(
            sample_count=len(samples),
            median_round_trip_us=median_round_trip_us,
            median_effective_throughput_bytes_per_sec=median_throughput,
            jitter_us=jitter_us,
            timeout_rate=timeout_rate,
            degraded_reasons=tuple(degraded_reasons),
            consecutive_degraded_windows=self._consecutive_degraded_windows,
            should_trigger=self._consecutive_degraded_windows >= self.policy.consecutive_degraded_windows,
        )


_RESULT_ROUTER_CLOSED = object()


@dataclass(slots=True)
class ResultRouter:
    session: ClientSession
    _queues: dict[tuple[int, int], asyncio.Queue[object]] = field(default_factory=dict, init=False, repr=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _error: BaseException | None = field(default=None, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    async def __aenter__(self) -> ResultRouter:
        if self._task is not None:
            raise RuntimeError("current result router is already running")
        self._task = asyncio.create_task(self._run(), name="nnrp-current-result-router")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def send_submit(self, request: SubmitRequest) -> int:
        return await self.session.send_submit(request)

    async def receive(self, frame_id: int, *, view_id: int = 0, timeout: float | None = None) -> Result:
        queue = self._queue_for(frame_id, view_id)
        if self._error is not None and queue.empty():
            raise RuntimeError("current result router stopped after a receive failure") from self._error
        if self._closed and queue.empty():
            raise RuntimeError("current result router is closed")

        waitable = queue.get()
        item = await asyncio.wait_for(waitable, timeout=timeout) if timeout is not None else await waitable
        if item is _RESULT_ROUTER_CLOSED:
            if self._error is not None:
                raise RuntimeError("current result router stopped after a receive failure") from self._error
            raise RuntimeError("current result router is closed")
        return item  # type: ignore[return-value]

    async def close(self) -> None:
        task = self._task
        if task is None:
            self._closed = True
            return
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        else:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None

    def _queue_for(self, frame_id: int, view_id: int) -> asyncio.Queue[object]:
        key = (int(frame_id), int(view_id))
        queue = self._queues.get(key)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[key] = queue
        return queue

    async def _run(self) -> None:
        try:
            while True:
                result = await self.session.receive_result()
                queue = self._queue_for(result.packet.header.frame_id, result.packet.header.view_id)
                await queue.put(result)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._error = exc
        finally:
            self._closed = True
            for queue in self._queues.values():
                queue.put_nowait(_RESULT_ROUTER_CLOSED)


@dataclass(slots=True)
class ClientSession:
    """Client session helpers.

    Typed helpers are the primary business-facing API. Raw packet
    methods remain available as low-level escape hatches for tests, adapters,
    and callers that need direct wire control.
    """

    control: ClientControlBootstrapSession
    connection: CurrentControlConnection
    _current_min_frame_id: int = 0
    _current_last_submitted_frame_id: int | None = None

    @property
    def session_id(self) -> int:
        return self.control.ack_metadata.session_id

    async def send_submit_packet(self, packet: NnrpPacket) -> int:
        """Low-level escape hatch for sending a prebuilt submit packet."""
        return await self.connection.send_submit_packet(packet)

    async def receive_result_packet(self, timeout: float | None = None) -> NnrpPacket:
        """Low-level escape hatch for receiving a raw result packet."""
        return await self.connection.receive_result_packet(timeout=timeout)

    async def submit_and_receive_result(
        self,
        packet: NnrpPacket,
        *,
        timeout: float | None = None,
    ) -> tuple[int, NnrpPacket]:
        """Convenience helper for smoke/tests around raw submit/result exchange."""
        submit_stream_id = await self.send_submit_packet(packet)
        result_packet = await self.receive_result_packet(timeout=timeout)
        return submit_stream_id, result_packet

    async def send_submit(self, request: SubmitRequest) -> int:
        frame_id = int(request.frame_id)
        if self._current_min_frame_id > 0 or self._current_last_submitted_frame_id is not None:
            self._validate_migrated_current_frame_id(frame_id)
        typed_payloads = tuple(payload for payload in request.typed_payloads)
        if typed_payloads:
            _validate_typed_payload_request(request)
            typed_frames = tuple(payload.to_core_frame() for payload in typed_payloads)
            if _current_request_has_tensor_body(request):
                packet = build_frame_submit_mixed_packet(
                    session_id=self.session_id,
                    frame_id=frame_id,
                    src_width=request.src_width,
                    src_height=request.src_height,
                    tile_width=request.tile_width,
                    tile_height=request.tile_height,
                    tile_ids=request.tile_ids,
                    sections=request.sections,
                    frames=typed_frames,
                    camera_block=request.camera_block,
                    frame_class=request.frame_class,
                    input_profile=request.input_profile,
                    tile_index_mode=request.tile_index_mode,
                    latency_budget_ms=request.latency_budget_ms,
                    target_fps_x100=request.target_fps_x100,
                    retry_of_frame=request.retry_of_frame,
                    tile_base_id=request.tile_base_id,
                    budget_policy=request.budget_policy,
                    dependency_frame_id=request.dependency_frame_id,
                    loss_tolerance_policy=request.loss_tolerance_policy,
                    wire_format=WireFormat.CURRENT,
                    flags=request.flags,
                    view_id=request.view_id,
                    route_id=request.route_id,
                    trace_id=request.trace_id,
                )
            else:
                packet = build_frame_submit_typed_payload_packet(
                    session_id=self.session_id,
                    frame_id=frame_id,
                    frames=typed_frames,
                    frame_class=request.frame_class,
                    latency_budget_ms=request.latency_budget_ms,
                    target_fps_x100=request.target_fps_x100,
                    retry_of_frame=request.retry_of_frame,
                    budget_policy=request.budget_policy,
                    dependency_frame_id=request.dependency_frame_id,
                    loss_tolerance_policy=request.loss_tolerance_policy,
                    wire_format=WireFormat.CURRENT,
                    flags=request.flags,
                    view_id=request.view_id,
                    route_id=request.route_id,
                    trace_id=request.trace_id,
                )
        else:
            packet = build_frame_submit_packet(
                session_id=self.session_id,
                frame_id=frame_id,
                src_width=request.src_width,
                src_height=request.src_height,
                tile_width=request.tile_width,
                tile_height=request.tile_height,
                tile_ids=request.tile_ids,
                sections=request.sections,
                camera_block=request.camera_block,
                frame_class=request.frame_class,
                input_profile=request.input_profile,
                tile_index_mode=request.tile_index_mode,
                latency_budget_ms=request.latency_budget_ms,
                target_fps_x100=request.target_fps_x100,
                retry_of_frame=request.retry_of_frame,
                tile_base_id=request.tile_base_id,
                submit_mode=request.submit_mode,
                object_ref_mask=request.object_ref_mask,
                camera_reference=request.camera_reference,
                tile_index_reference=request.tile_index_reference,
                tensor_section_table_reference=request.tensor_section_table_reference,
                budget_policy=request.budget_policy,
                dependency_frame_id=request.dependency_frame_id,
                loss_tolerance_policy=request.loss_tolerance_policy,
                payload_kind_bitmap=request.payload_kind_bitmap,
                payload_frame_count=request.payload_frame_count,
                wire_format=WireFormat.CURRENT,
                flags=request.flags,
                view_id=request.view_id,
                route_id=request.route_id,
                trace_id=request.trace_id,
            )
        submit_stream_id = await self.send_submit_packet(packet)
        if self._current_min_frame_id > 0 or self._current_last_submitted_frame_id is not None:
            self._current_last_submitted_frame_id = frame_id
        return submit_stream_id

    async def receive_result(self, timeout: float | None = None) -> Result:
        packet = await self.receive_result_packet(timeout=timeout)
        if packet.header.msg_type is MessageType.RESULT_DROP:
            return Result(packet=packet)
        if packet.header.msg_type is not MessageType.RESULT_PUSH:
            raise ValueError(f"expected RESULT_PUSH or RESULT_DROP, got {packet.header.msg_type.name}")

        metadata = ResultPushMetadata.unpack(packet.metadata)
        tensor_body = None
        typed_payloads: tuple[TypedPayload, ...] = ()
        if (
            not packet.body
            and metadata.payload_frame_count == 0
            and not (metadata.payload_kind_bitmap & PayloadKind.TENSOR)
        ):
            return Result(
                packet=packet,
                metadata=metadata,
                tensor_body=None,
                typed_payloads=(),
            )
        if _current_metadata_uses_composed_body(
            payload_kind_bitmap=metadata.payload_kind_bitmap,
            payload_frame_count=metadata.payload_frame_count,
        ):
            body_view = validate_result_push_body(metadata, packet.body)
            typed_payloads = tuple(
                TypedPayload.from_core_frame(frame)
                for frame in unpack_typed_payload_frames(
                    body_view.typed_payload_descriptor_region,
                    body_view.typed_payload_frame_region,
                )
            )
            if metadata.payload_kind_bitmap & PayloadKind.TENSOR:
                inline_blocks = {
                    int(block.header.object_kind): block
                    for block in unpack_inline_object_blocks(body_view.inline_object_region)
                }
                tile_index_inline = inline_blocks.get(int(CacheObjectKind.TILE_INDEX_BLOCK))
                section_inline = inline_blocks.get(int(CacheObjectKind.TENSOR_SECTION_TABLE))
                section_views = ()
                if section_inline is not None:
                    section_views = unpack_tensor_body(
                        section_inline.payload,
                        tile_index_bytes=0,
                        section_count=metadata.section_count,
                        tile_count=metadata.tile_count,
                    ).sections
                tensor_body = TensorBodyView(
                    tile_index_block=(tile_index_inline.payload if tile_index_inline is not None else memoryview(b"")),
                    sections=section_views,
                )
        elif metadata.payload_kind_bitmap & PayloadKind.TENSOR:
            tensor_body = unpack_tensor_body(
                packet.body,
                tile_index_bytes=metadata.tile_index_bytes,
                section_count=metadata.section_count,
                tile_count=metadata.tile_count,
            )
            unpack_tile_index_block(
                tensor_body.tile_index_block,
                mode=TileIndexMode.RAW_U16 if metadata.tile_index_bytes else TileIndexMode.DENSE_RANGE,
                tile_count=metadata.tile_count,
                tile_base_id=metadata.tile_base_id,
            )
            validate_result_push_tensor_coverage(metadata)
        return Result(
            packet=packet,
            metadata=metadata,
            tensor_body=tensor_body,
            typed_payloads=typed_payloads,
        )

    def manage_results(self) -> ResultRouter:
        return ResultRouter(session=self)

    def monitor_migration(
        self,
        policy: MigrationTriggerPolicy | None = None,
    ) -> MigrationTriggerMonitor:
        return MigrationTriggerMonitor(
            active_transport_id=self.control.transport_id,
            policy=policy or MigrationTriggerPolicy(),
        )

    @asynccontextmanager
    async def migrate_session(
        self,
        host: str,
        *,
        quic_port: int | None = None,
        tcp_port: int | None = None,
        quic_configuration: QuicConfiguration | None = None,
        tcp_configuration: NnrpTcpClientConfiguration | None = None,
        selected_transport_id: TransportId = TransportId.UNSPECIFIED,
        forced_transport_id: TransportId = TransportId.UNSPECIFIED,
        last_result_frame_id: int = 0,
        client_migrate_ts_us: int | None = None,
        route_id: int = 0,
        trace_id: int = 0,
        flags: HeaderFlags = HeaderFlags.NONE,
        body: bytes = b"",
        timeout: float = 10.0,
    ) -> AsyncIterator[MigrationOutcome]:
        plan = plan_client_transport(
            selected_transport_id=selected_transport_id,
            forced_transport_id=forced_transport_id,
        )
        new_transport_id, port = _resolve_selected_transport_endpoint(
            selected_transport_id=plan.selected_transport_id,
            quic_port=quic_port,
            tcp_port=tcp_port,
        )
        if new_transport_id is self.control.transport_id:
            raise ValueError("current migration requires a different transport from the active session")

        async with _open_current_control_connection(
            host,
            transport_id=new_transport_id,
            port=port,
            wire_format=WireFormat.CURRENT,
            quic_configuration=quic_configuration,
            tcp_configuration=tcp_configuration,
            timeout=timeout,
        ) as connection:
            migrated_control = ClientControlBootstrapSession(
                transport_id=new_transport_id,
                bootstrap=ClientTransportBootstrap(
                    plan=ClientTransportPlan(selected_transport_id=new_transport_id),
                    hello_packet=self.control.bootstrap.hello_packet,
                    probe_selection=self.control.bootstrap.probe_selection,
                ),
                connection=connection,
                ack_packet=self.control.ack_packet,
                ack_metadata=self.control.ack_metadata,
            )
            await migrated_control.send_session_migrate(
                SessionMigrateMetadata(
                    old_transport_id=self.control.transport_id,
                    new_transport_id=new_transport_id,
                    last_result_frame_id=last_result_frame_id,
                    client_migrate_ts_us=_now_us() if client_migrate_ts_us is None else client_migrate_ts_us,
                ),
                route_id=route_id,
                trace_id=trace_id,
                flags=flags,
                body=body,
            )
            (
                ack_packet,
                ack_metadata,
            ) = await migrated_control.receive_session_migrate_ack(timeout=timeout)
            if int(ack_packet.header.session_id) != int(self.session_id):
                raise ValueError(
                    f"expected migrated session_id {self.session_id}, got {int(ack_packet.header.session_id)}"
                )
            yield MigrationOutcome(
                previous_transport_id=self.control.transport_id,
                current_transport_id=new_transport_id,
                session=ClientSession(
                    control=migrated_control,
                    connection=connection,
                    _current_min_frame_id=int(ack_metadata.resume_from_frame_id),
                    _current_last_submitted_frame_id=(
                        int(ack_metadata.resume_from_frame_id) - 1
                        if int(ack_metadata.resume_from_frame_id) > 0
                        else None
                    ),
                ),
                ack_packet=ack_packet,
                ack_metadata=ack_metadata,
            )

    async def send_flow_update(
        self,
        metadata: FlowUpdateMetadata,
        *,
        trace_id: int = 0,
        flags: HeaderFlags = HeaderFlags.NONE,
    ) -> None:
        await self.control.send_flow_update(metadata, trace_id=trace_id, flags=flags)

    async def receive_flow_update(
        self,
        timeout: float | None = None,
    ) -> tuple[NnrpPacket, FlowUpdateMetadata]:
        return await self.control.receive_flow_update(timeout=timeout)

    def _validate_migrated_current_frame_id(self, frame_id: int) -> None:
        if self._current_min_frame_id > 0 and frame_id < self._current_min_frame_id:
            raise ValueError(
                "current migration resume_from_frame_id requires frame_id >= "
                f"{self._current_min_frame_id}, got {frame_id}"
            )
        if self._current_last_submitted_frame_id is not None and frame_id <= self._current_last_submitted_frame_id:
            raise ValueError(
                "current migrated session frame_id must be strictly increasing: "
                f"{frame_id} <= {self._current_last_submitted_frame_id}"
            )


def plan_client_transport(
    *,
    selected_transport_id: TransportId = TransportId.UNSPECIFIED,
    forced_transport_id: TransportId = TransportId.UNSPECIFIED,
) -> ClientTransportPlan:
    policy_extension = resolve_client_hello_transport_policy(
        selected_transport_id=selected_transport_id,
        forced_transport_id=forced_transport_id,
    )
    resolved_transport_id = TransportId(forced_transport_id)
    if resolved_transport_id is TransportId.UNSPECIFIED:
        resolved_transport_id = TransportId(selected_transport_id)
    return ClientTransportPlan(
        selected_transport_id=resolved_transport_id,
        transport_policy_extension=policy_extension,
    )


def build_client_hello_packet(
    *,
    requested_session_id: int = 1,
    auth_block: bytes = b"",
    requested_model: str | None = None,
    control_extensions: bytes = b"",
    client_profile: ClientProfile | None = None,
    transport_policy_extension: ClientHelloTransportPolicyExtension | None = None,
    transport_policy: TransportPolicy | None = None,
    preferred_transport_id: TransportId = TransportId.UNSPECIFIED,
    selected_transport_id: TransportId = TransportId.UNSPECIFIED,
    forced_transport_id: TransportId = TransportId.UNSPECIFIED,
    wire_format: WireFormat = WireFormat.CURRENT,
) -> NnrpPacket:
    if transport_policy_extension is not None:
        if (
            transport_policy is not None
            or preferred_transport_id is not TransportId.UNSPECIFIED
            or selected_transport_id is not TransportId.UNSPECIFIED
            or forced_transport_id is not TransportId.UNSPECIFIED
        ):
            raise ValueError("transport_policy_extension cannot be combined with explicit transport policy arguments")
        transport_policy = transport_policy_extension.transport_policy
        preferred_transport_id = transport_policy_extension.preferred_transport_id

    plan = plan_client_transport(
        selected_transport_id=selected_transport_id,
        forced_transport_id=forced_transport_id,
    )
    if plan.transport_policy_extension is not None:
        if transport_policy is not None or preferred_transport_id is not TransportId.UNSPECIFIED:
            raise ValueError(
                "explicit transport_policy/preferred_transport_id cannot be combined with local dial policy ids"
            )
        transport_policy = plan.transport_policy_extension.transport_policy
        preferred_transport_id = plan.transport_policy_extension.preferred_transport_id

    profile = client_profile or ClientProfile()
    auth_block = _resolve_client_auth_block(auth_block=auth_block, requested_model=requested_model)
    helper_extensions = _build_client_hello_helper_extensions(
        transport_policy=transport_policy,
        preferred_transport_id=preferred_transport_id,
    )
    body = helper_extensions + bytes(control_extensions) + bytes(auth_block)
    cache_namespace_count = 1 if profile.enable_cache else 0
    max_cache_entries = profile.max_cache_entries if profile.enable_cache else 0
    max_cache_bytes = profile.max_cache_bytes if profile.enable_cache else 0
    metadata = ClientHelloMetadata(
        min_version_major=1,
        max_version_major=1,
        supported_wire_format_bitmap=_client_hello_wire_format_bitmap(wire_format),
        supported_profile_bitmap=0x0001,
        supported_payload_kind_bitmap=0x0001,
        supported_codec_bitmap=0x0003,
        supported_compression_bitmap=0x0003,
        supported_dtype_bitmap=0x001F,
        supported_layout_bitmap=0x0003,
        cache_digest_bitmap=0x0001,
        cache_object_bitmap=TENSOR_PROFILE_CACHE_OBJECT_BITMAP if profile.enable_cache else 0,
        cache_namespace_count=cache_namespace_count,
        max_lane_count=profile.max_views,
        max_cache_entries=max_cache_entries,
        max_cache_bytes=max_cache_bytes,
        target_cadence_x100=6000,
        latency_budget_ms=50,
        quality_tier=2,
        degrade_policy=0,
        requested_session_id=requested_session_id,
        auth_bytes=len(auth_block),
        control_extension_bytes=len(helper_extensions) + len(control_extensions),
    ).pack()
    return NnrpPacket.build(
        version_major=1,
        wire_format=wire_format,
        msg_type=MessageType.CLIENT_HELLO,
        flags=HeaderFlags.ACK_REQUIRED,
        metadata=metadata,
        body=body,
    )


def bootstrap_client_transport(
    *,
    requested_session_id: int = 1,
    auth_block: bytes = b"",
    requested_model: str | None = None,
    control_extensions: bytes = b"",
    client_profile: ClientProfile | None = None,
    probe_selection: TransportProbeSelection | None = None,
    selected_transport_id: TransportId = TransportId.UNSPECIFIED,
    forced_transport_id: TransportId = TransportId.UNSPECIFIED,
) -> ClientTransportBootstrap:
    resolved_selected_transport_id = selected_transport_id
    if (
        resolved_selected_transport_id is TransportId.UNSPECIFIED
        and forced_transport_id is TransportId.UNSPECIFIED
        and probe_selection is not None
    ):
        resolved_selected_transport_id = probe_selection.selected_transport_id

    plan = plan_client_transport(
        selected_transport_id=resolved_selected_transport_id,
        forced_transport_id=forced_transport_id,
    )
    return ClientTransportBootstrap(
        plan=plan,
        hello_packet=plan.build_client_hello_packet(
            requested_session_id=requested_session_id,
            auth_block=auth_block,
            requested_model=requested_model,
            control_extensions=control_extensions,
            client_profile=client_profile,
        ),
        probe_selection=probe_selection,
    )


@asynccontextmanager
async def connect_client_control(
    host: str,
    *,
    quic_port: int | None = None,
    tcp_port: int | None = None,
    quic_configuration: QuicConfiguration | None = None,
    tcp_configuration: NnrpTcpClientConfiguration | None = None,
    requested_session_id: int = 1,
    auth_block: bytes = b"",
    requested_model: str | None = None,
    control_extensions: bytes = b"",
    client_profile: ClientProfile | None = None,
    probe_selection: TransportProbeSelection | None = None,
    selected_transport_id: TransportId = TransportId.UNSPECIFIED,
    forced_transport_id: TransportId = TransportId.UNSPECIFIED,
    timeout: float = 10.0,
) -> AsyncIterator[ClientControlBootstrapSession]:
    bootstrap = bootstrap_client_transport(
        requested_session_id=requested_session_id,
        auth_block=auth_block,
        requested_model=requested_model,
        control_extensions=control_extensions,
        client_profile=client_profile,
        probe_selection=probe_selection,
        selected_transport_id=selected_transport_id,
        forced_transport_id=forced_transport_id,
    )
    async with _connect_client_control_from_bootstrap(
        host,
        bootstrap=bootstrap,
        wire_format=WireFormat.CURRENT,
        quic_port=quic_port,
        tcp_port=tcp_port,
        quic_configuration=quic_configuration,
        tcp_configuration=tcp_configuration,
        timeout=timeout,
    ) as session:
        yield session


@asynccontextmanager
async def connect_client_control_with_probe(
    host: str,
    *,
    quic_port: int,
    tcp_port: int,
    quic_configuration: QuicConfiguration | None = None,
    tcp_configuration: NnrpTcpClientConfiguration | None = None,
    requested_session_id: int = 1,
    auth_block: bytes = b"",
    requested_model: str | None = None,
    control_extensions: bytes = b"",
    client_profile: ClientProfile | None = None,
    probe_payload_bytes: int = 32 * 1024,
    probe_sample_count: int = 3,
    include_warmup_probe: bool = False,
    timeout: float = 10.0,
) -> AsyncIterator[ClientControlBootstrapSession]:
    from nnrp.tools.smoke import run_parallel_transport_probes

    probe_selection = await run_parallel_transport_probes(
        host,
        quic_port=quic_port,
        tcp_port=tcp_port,
        quic_configuration=quic_configuration,
        tcp_configuration=tcp_configuration,
        probe_payload_bytes=probe_payload_bytes,
        sample_count=probe_sample_count,
        include_warmup_probe=include_warmup_probe,
        timeout=timeout,
    )

    async with connect_client_control(
        host,
        quic_port=quic_port,
        tcp_port=tcp_port,
        quic_configuration=quic_configuration,
        tcp_configuration=tcp_configuration,
        requested_session_id=requested_session_id,
        auth_block=auth_block,
        requested_model=requested_model,
        control_extensions=control_extensions,
        client_profile=client_profile,
        probe_selection=probe_selection,
        timeout=timeout,
    ) as session:
        yield session


async def probe_client_transport(
    host: str,
    *,
    quic_port: int,
    tcp_port: int,
    quic_configuration: QuicConfiguration | None = None,
    tcp_configuration: NnrpTcpClientConfiguration | None = None,
    probe_payload_bytes: int = 32 * 1024,
    probe_sample_count: int = 3,
    include_warmup_probe: bool = False,
    timeout: float = 10.0,
) -> TransportProbeSelection:
    from nnrp.tools.smoke import run_parallel_transport_probes

    return await run_parallel_transport_probes(
        host,
        quic_port=quic_port,
        tcp_port=tcp_port,
        quic_configuration=quic_configuration,
        tcp_configuration=tcp_configuration,
        probe_payload_bytes=probe_payload_bytes,
        sample_count=probe_sample_count,
        include_warmup_probe=include_warmup_probe,
        timeout=timeout,
    )


@asynccontextmanager
async def connect_client_session_with_probe(
    host: str,
    *,
    quic_port: int,
    tcp_port: int,
    quic_configuration: QuicConfiguration | None = None,
    tcp_configuration: NnrpTcpClientConfiguration | None = None,
    requested_session_id: int = 1,
    auth_block: bytes = b"",
    requested_model: str | None = None,
    control_extensions: bytes = b"",
    client_profile: ClientProfile | None = None,
    probe_payload_bytes: int = 32 * 1024,
    probe_sample_count: int = 3,
    include_warmup_probe: bool = False,
    timeout: float = 10.0,
) -> AsyncIterator[ClientSession]:
    from nnrp.tools.smoke import run_parallel_transport_probes

    probe_selection = await run_parallel_transport_probes(
        host,
        quic_port=quic_port,
        tcp_port=tcp_port,
        quic_configuration=quic_configuration,
        tcp_configuration=tcp_configuration,
        probe_payload_bytes=probe_payload_bytes,
        sample_count=probe_sample_count,
        include_warmup_probe=include_warmup_probe,
        timeout=timeout,
    )
    async with connect_client_session(
        host,
        quic_port=quic_port,
        tcp_port=tcp_port,
        quic_configuration=quic_configuration,
        tcp_configuration=tcp_configuration,
        requested_session_id=requested_session_id,
        auth_block=auth_block,
        requested_model=requested_model,
        control_extensions=control_extensions,
        client_profile=client_profile,
        probe_selection=probe_selection,
        timeout=timeout,
    ) as session:
        yield session


@asynccontextmanager
async def connect_client_session(
    host: str,
    *,
    quic_port: int | None = None,
    tcp_port: int | None = None,
    quic_configuration: QuicConfiguration | None = None,
    tcp_configuration: NnrpTcpClientConfiguration | None = None,
    requested_session_id: int = 1,
    auth_block: bytes = b"",
    requested_model: str | None = None,
    control_extensions: bytes = b"",
    client_profile: ClientProfile | None = None,
    probe_selection: TransportProbeSelection | None = None,
    selected_transport_id: TransportId = TransportId.UNSPECIFIED,
    forced_transport_id: TransportId = TransportId.UNSPECIFIED,
    timeout: float = 10.0,
) -> AsyncIterator[ClientSession]:
    bootstrap = bootstrap_client_transport(
        requested_session_id=requested_session_id,
        auth_block=auth_block,
        requested_model=requested_model,
        control_extensions=control_extensions,
        client_profile=client_profile,
        probe_selection=probe_selection,
        selected_transport_id=selected_transport_id,
        forced_transport_id=forced_transport_id,
    )
    transport_id, _ = _resolve_transport_endpoint(
        bootstrap=bootstrap,
        quic_port=quic_port,
        tcp_port=tcp_port,
    )

    async with connect_client_control(
        host,
        quic_port=quic_port,
        tcp_port=tcp_port,
        quic_configuration=quic_configuration,
        tcp_configuration=tcp_configuration,
        requested_session_id=requested_session_id,
        auth_block=auth_block,
        requested_model=requested_model,
        control_extensions=control_extensions,
        client_profile=client_profile,
        probe_selection=probe_selection,
        selected_transport_id=selected_transport_id,
        forced_transport_id=forced_transport_id,
        timeout=timeout,
    ) as control_session:
        yield ClientSession(
            control=control_session,
            connection=control_session.connection,
        )


def _resolve_client_auth_block(*, auth_block: bytes, requested_model: str | None) -> bytes:
    if requested_model is None:
        return bytes(auth_block)
    if auth_block:
        raise ValueError("requested_model cannot be combined with auth_block")
    return requested_model.encode("utf-8")


def _validate_typed_payload_request(request: SubmitRequest) -> None:
    if request.submit_mode is not SubmitMode.INLINE:
        raise ValueError("typed payload submit helper currently supports SubmitMode.INLINE only")
    if request.object_ref_mask:
        raise ValueError("typed payload submit helper does not support object references")
    if request.camera_reference is not None:
        raise ValueError("typed payload submit helper does not support camera_reference")
    if request.tile_index_reference is not None:
        raise ValueError("typed payload submit helper does not support tile_index_reference")
    if request.tensor_section_table_reference is not None:
        raise ValueError("typed payload submit helper does not support tensor_section_table_reference")


def _current_request_has_tensor_body(request: SubmitRequest) -> bool:
    return bool(request.camera_block or request.tile_ids or request.sections)


def _current_metadata_uses_composed_body(*, payload_kind_bitmap: PayloadKind, payload_frame_count: int) -> bool:
    return bool(payload_frame_count) or payload_kind_bitmap not in {
        PayloadKind.NONE,
        PayloadKind.TENSOR,
    }


def _build_client_hello_helper_extensions(
    *,
    transport_policy: TransportPolicy | None,
    preferred_transport_id: TransportId,
) -> bytes:
    entries: list[ControlExtensionEntry] = []
    if transport_policy is not None or preferred_transport_id is not TransportId.UNSPECIFIED:
        entries.append(
            build_client_hello_transport_policy_extension(
                ClientHelloTransportPolicyExtension(
                    transport_policy=transport_policy or TransportPolicy.AUTO,
                    preferred_transport_id=preferred_transport_id,
                )
            )
        )
    if not entries:
        return b""
    return pack_control_extension_block(entries)


def _client_hello_wire_format_bitmap(wire_format: WireFormat) -> int:
    if wire_format is WireFormat.CURRENT:
        return 0x0001
    raise ValueError(f"unsupported client hello wire_format: {wire_format}")


@asynccontextmanager
async def _connect_client_control_from_bootstrap(
    host: str,
    *,
    bootstrap: ClientTransportBootstrap,
    wire_format: WireFormat,
    quic_port: int | None,
    tcp_port: int | None,
    quic_configuration: QuicConfiguration | None,
    tcp_configuration: NnrpTcpClientConfiguration | None,
    timeout: float,
) -> AsyncIterator[ClientControlBootstrapSession]:
    transport_id, port = _resolve_transport_endpoint(
        bootstrap=bootstrap,
        quic_port=quic_port,
        tcp_port=tcp_port,
    )

    async with _open_current_control_connection(
        host,
        transport_id=transport_id,
        port=port,
        wire_format=wire_format,
        quic_configuration=quic_configuration,
        tcp_configuration=tcp_configuration,
        timeout=timeout,
    ) as connection:
        yield await _bootstrap_connected_client(
            connection,
            transport_id=transport_id,
            bootstrap=bootstrap,
            timeout=timeout,
        )


@asynccontextmanager
async def _open_current_control_connection(
    host: str,
    *,
    transport_id: TransportId,
    port: int,
    wire_format: WireFormat,
    quic_configuration: QuicConfiguration | None,
    tcp_configuration: NnrpTcpClientConfiguration | None,
    timeout: float,
) -> AsyncIterator[CurrentControlConnection]:
    if transport_id is TransportId.QUIC:
        client_configuration = quic_configuration or create_quic_client_configuration(
            wire_format=wire_format,
        )
        async with connect_quic(host, port, configuration=client_configuration) as connection:
            yield connection
        return

    if transport_id is TransportId.TCP:
        client_configuration = tcp_configuration or create_tcp_client_configuration(
            connect_timeout=timeout,
            idle_timeout=timeout,
        )
        async with connect_tcp(host, port, configuration=client_configuration) as connection:
            yield connection
        return

    raise ValueError(f"unsupported transport id for connection: {transport_id}")


async def _bootstrap_connected_client(
    connection: CurrentControlConnection,
    *,
    transport_id: TransportId,
    bootstrap: ClientTransportBootstrap,
    timeout: float,
) -> ClientControlBootstrapSession:
    await connection.send_control_packet(bootstrap.hello_packet)
    ack_packet = await connection.receive_control_packet(timeout=timeout)
    if ack_packet.header.msg_type is not MessageType.SERVER_HELLO_ACK:
        raise ValueError(f"expected SERVER_HELLO_ACK, got {ack_packet.header.msg_type.name}")
    return ClientControlBootstrapSession(
        transport_id=transport_id,
        bootstrap=bootstrap,
        connection=connection,
        ack_packet=ack_packet,
        ack_metadata=ServerHelloAckMetadata.unpack(ack_packet.metadata),
    )


def _resolve_transport_endpoint(
    *,
    bootstrap: ClientTransportBootstrap,
    quic_port: int | None,
    tcp_port: int | None,
) -> tuple[TransportId, int]:
    return _resolve_selected_transport_endpoint(
        selected_transport_id=bootstrap.plan.selected_transport_id,
        quic_port=quic_port,
        tcp_port=tcp_port,
    )


def _resolve_selected_transport_endpoint(
    *,
    selected_transport_id: TransportId,
    quic_port: int | None,
    tcp_port: int | None,
) -> tuple[TransportId, int]:
    resolved_transport_id = selected_transport_id
    if resolved_transport_id is TransportId.UNSPECIFIED:
        if quic_port is not None and tcp_port is None:
            resolved_transport_id = TransportId.QUIC
        elif tcp_port is not None and quic_port is None:
            resolved_transport_id = TransportId.TCP
        else:
            raise ValueError(
                "selected transport id is unspecified; provide probe selection, dial policy, or a single endpoint"
            )

    if resolved_transport_id is TransportId.QUIC:
        if quic_port is None:
            raise ValueError("quic_port is required when selected transport is QUIC")
        return resolved_transport_id, quic_port
    if resolved_transport_id is TransportId.TCP:
        if tcp_port is None:
            raise ValueError("tcp_port is required when selected transport is TCP")
        return resolved_transport_id, tcp_port
    raise ValueError(f"unsupported transport id for endpoint resolution: {resolved_transport_id}")


def _now_us() -> int:
    return time.monotonic_ns() // 1_000
