"""Preview4 runtime control and object metadata models."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any, ClassVar, TypeVar

from nnrp.core import HeaderFlags, MessageType, WireFormat


class OperationState(IntEnum):
    ACCEPTED = 0
    RUNNING = 1
    PARTIAL = 2
    WAITING_TOOL = 3
    SUPERSEDED = 4
    CANCELLED = 5
    FAILED = 6
    COMPLETED = 7


class ResultTerminalState(IntEnum):
    SUCCESS = 0
    CANCELLED = 1
    DROPPED = 2
    ERROR = 3


class RuntimeObjectKind(IntEnum):
    UNSPECIFIED = 0
    TENSOR = 1
    TOKEN_BLOCK = 2
    IMAGE_TILE = 3
    FEATURE_MAP = 4
    TOOL_RESULT = 5
    TRACE_SEGMENT = 6
    OPAQUE_BYTES = 7
    DOCUMENT_CHUNK = 8
    AUDIO_CHUNK = 9
    VIDEO_CHUNK = 10
    ROUTE_PLAN = 11
    CACHE_MANIFEST = 12


class RuntimeRole(IntEnum):
    UNSPECIFIED = 0
    CLIENT = 1
    SERVER = 2
    RUNTIME = 3
    SUBAGENT = 4
    TOOL = 5
    SCHEDULER = 6
    CONFORMANCE_RUNNER = 7


class MemoryLocationHint(IntEnum):
    UNSPECIFIED = 0
    HOST_MEMORY = 1
    DEVICE_MEMORY = 2
    SHARED_MEMORY = 3
    REMOTE_MEMORY = 4
    MMAP_FILE = 5
    OBJECT_STORE = 6


class OwnershipHint(IntEnum):
    UNSPECIFIED = 0
    PRODUCER_OWNED = 1
    CONSUMER_OWNED = 2
    SESSION_OWNED = 3
    BORROWED = 4
    TRANSFER_ON_REF = 5
    RELEASE_ON_DROP = 6


class ObjectReleaseReason(IntEnum):
    COMPLETED = 0
    CANCELLED = 1
    EXPIRED = 2
    REPLACED = 3
    INVALIDATED = 4
    OWNER_CLOSED = 5
    LEASE_EXPIRED = 6
    CONFORMANCE_INJECTION = 7


class ResultDropReasonCode(IntEnum):
    NONE = 0
    DEADLINE_EXPIRED = 1
    SUPERSEDED = 2
    PEER_CANCELLED = 3
    BACKPRESSURE = 4
    CAPABILITY_MISMATCH = 5
    BUDGET_EXCEEDED = 6
    OBJECT_INVALIDATED = 7
    TRANSPORT_CLOSED = 8
    CONFORMANCE_INJECTION = 9


class CacheReuseScope(IntEnum):
    OPERATION = 0
    SESSION = 1
    CONNECTION = 2
    GLOBAL = 3
    TENANT = 4
    PROFILE = 5


class CacheMissReason(IntEnum):
    UNKNOWN = 0
    NOT_FOUND = 1
    EXPIRED = 2
    INVALIDATED = 3
    SCHEMA_MISMATCH = 4
    PRODUCER_UNAVAILABLE = 5
    LEASE_REQUIRED = 6
    PERMISSION_DENIED = 7


class SessionCloseReason(IntEnum):
    NORMAL = 0
    CLIENT_SHUTDOWN = 1
    SERVER_SHUTDOWN = 2
    IDLE_TIMEOUT = 3
    PROTOCOL_ERROR = 4
    AUTH_REVOKED = 5


class InFlightPolicy(IntEnum):
    DRAIN = 0
    ABORT = 1


class RuntimeEventMetadataKind(StrEnum):
    NONE = "none"
    FRAME_SUBMIT = "frame_submit"
    RESULT_PUSH = "result_push"
    RESULT_HINT = "result_hint"
    CONTROL_REQUEST = "control_request"
    SCHEDULING = "scheduling"
    SUPERSEDE = "supersede"
    BUDGET = "budget"
    PROGRESS = "progress"
    PARTIAL_RESULT = "partial_result"
    PRESSURE = "pressure"
    CAPABILITY = "capability"
    ROUTE_HINT = "route_hint"
    TRACE_CONTEXT = "trace_context"
    RESULT_DROP_REASON = "result_drop_reason"
    RECOVERABLE_ERROR = "recoverable_error"
    RETRY_AFTER = "retry_after"
    FLOW_UPDATE = "flow_update"
    OBJECT_DESCRIPTOR = "object_descriptor"
    OBJECT_REFERENCE = "object_reference"
    OBJECT_RELEASE = "object_release"
    OBJECT_DELTA = "object_delta"
    CACHE_REFERENCE = "cache_reference"
    CACHE_MISS = "cache_miss"
    CACHE_INVALIDATE = "cache_invalidate"
    SESSION_CLOSE = "session_close"


class RuntimeEventTailKind(StrEnum):
    NONE = "none"
    BODY = "body"
    DIAGNOSTIC = "diagnostic"
    METADATA_BODY_AND_DELTA = "metadata_body_and_delta"


@dataclass(frozen=True, slots=True)
class RuntimeEventMetadata:
    kind: RuntimeEventMetadataKind
    value: Any = None

    def __post_init__(self) -> None:
        normalized = RuntimeEventMetadataKind(self.kind)
        object.__setattr__(self, "kind", normalized)
        if normalized is RuntimeEventMetadataKind.NONE and self.value is not None:
            raise ValueError("none runtime event metadata cannot carry a value")
        if normalized is not RuntimeEventMetadataKind.NONE and self.value is None:
            raise ValueError(f"{normalized.value} runtime event metadata requires a value")


@dataclass(frozen=True, slots=True)
class RuntimeEventTail:
    kind: RuntimeEventTailKind
    body: bytes = b""
    diagnostic: bytes = b""
    metadata_body: bytes = b""
    delta: bytes = b""

    def __post_init__(self) -> None:
        normalized = RuntimeEventTailKind(self.kind)
        object.__setattr__(self, "kind", normalized)
        for name in ("body", "diagnostic", "metadata_body", "delta"):
            value = getattr(self, name)
            if not isinstance(value, bytes):
                raise TypeError(f"runtime event tail {name} must be owned bytes")
        populated = {
            RuntimeEventTailKind.NONE: not any((self.body, self.diagnostic, self.metadata_body, self.delta)),
            RuntimeEventTailKind.BODY: not any((self.diagnostic, self.metadata_body, self.delta)),
            RuntimeEventTailKind.DIAGNOSTIC: not any((self.body, self.metadata_body, self.delta)),
            RuntimeEventTailKind.METADATA_BODY_AND_DELTA: not any((self.body, self.diagnostic)),
        }
        if not populated[normalized]:
            raise ValueError(f"runtime event tail fields do not match {normalized.value}")

    @classmethod
    def none(cls) -> RuntimeEventTail:
        return cls(RuntimeEventTailKind.NONE)

    @classmethod
    def with_body(cls, body: bytes) -> RuntimeEventTail:
        return cls(RuntimeEventTailKind.BODY, body=bytes(body))

    @classmethod
    def with_diagnostic(cls, diagnostic: bytes) -> RuntimeEventTail:
        return cls(RuntimeEventTailKind.DIAGNOSTIC, diagnostic=bytes(diagnostic))

    @classmethod
    def with_metadata_body_and_delta(
        cls,
        metadata_body: bytes,
        delta: bytes,
    ) -> RuntimeEventTail:
        return cls(
            RuntimeEventTailKind.METADATA_BODY_AND_DELTA,
            metadata_body=bytes(metadata_body),
            delta=bytes(delta),
        )


@dataclass(frozen=True, slots=True)
class NativeRuntimeEvent:
    header: RuntimeFrameHeader
    metadata: RuntimeEventMetadata
    tail: RuntimeEventTail
    _native_context: object | None = None


@dataclass(frozen=True, slots=True)
class OperationLifecycleEvent:
    operation_id: int
    state: OperationState

    def __post_init__(self) -> None:
        if type(self.operation_id) is not int or not 1 <= self.operation_id <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("operation_id must be a non-zero unsigned 64-bit integer")
        object.__setattr__(self, "state", OperationState(self.state))


class NativeTerminalEventKind(StrEnum):
    RUNTIME = "runtime"
    LIFECYCLE = "lifecycle"


@dataclass(frozen=True, slots=True)
class NativeTerminalEvent:
    kind: NativeTerminalEventKind
    value: NativeRuntimeEvent | OperationLifecycleEvent

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", NativeTerminalEventKind(self.kind))
        expected_type = NativeRuntimeEvent if self.kind is NativeTerminalEventKind.RUNTIME else OperationLifecycleEvent
        if not isinstance(self.value, expected_type):
            raise TypeError(f"{self.kind.value} terminal event requires {expected_type.__name__}")

    @classmethod
    def runtime(cls, event: NativeRuntimeEvent) -> NativeTerminalEvent:
        return cls(NativeTerminalEventKind.RUNTIME, event)

    @classmethod
    def lifecycle(cls, event: OperationLifecycleEvent) -> NativeTerminalEvent:
        return cls(NativeTerminalEventKind.LIFECYCLE, event)

    def as_runtime(self) -> NativeRuntimeEvent | None:
        return self.value if isinstance(self.value, NativeRuntimeEvent) else None

    def as_lifecycle(self) -> OperationLifecycleEvent | None:
        return self.value if isinstance(self.value, OperationLifecycleEvent) else None


RuntimeMetadataT = TypeVar("RuntimeMetadataT", bound="_FixedRuntimeMetadata")


class _FixedRuntimeMetadata:
    STRUCT: ClassVar[struct.Struct]

    def pack(self) -> bytes:
        raise NotImplementedError

    @classmethod
    def unpack(cls: type[RuntimeMetadataT], payload: bytes) -> RuntimeMetadataT:
        if len(payload) != cls.STRUCT.size:
            raise ValueError(f"expected {cls.STRUCT.size} bytes, got {len(payload)}")
        return cls._from_tuple(cls.STRUCT.unpack(payload))

    @classmethod
    def _from_tuple(cls: type[RuntimeMetadataT], values: tuple[int, ...]) -> RuntimeMetadataT:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class SessionCloseMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<HBBIQII")

    close_reason: SessionCloseReason
    in_flight_policy: InFlightPolicy
    drain_timeout_ms: int
    last_operation_id: int
    session_error_code: int
    session_close_tag: int

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            int(self.close_reason),
            int(self.in_flight_policy),
            0,
            self.drain_timeout_ms,
            self.last_operation_id,
            self.session_error_code,
            self.session_close_tag,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> SessionCloseMetadata:
        _require_zero(values[2], "session_close.reserved0")
        return cls(
            SessionCloseReason(values[0]),
            InFlightPolicy(values[1]),
            values[3],
            values[4],
            values[5],
            values[6],
        )


@dataclass(frozen=True, slots=True)
class DecodedRuntimeControlMetadata:
    metadata: _FixedRuntimeMetadata
    tail: bytes = b""


@dataclass(frozen=True, slots=True)
class DecodedRuntimeObjectMetadata:
    metadata: _FixedRuntimeMetadata
    tail: bytes = b""


@dataclass(frozen=True, slots=True)
class RuntimeFrameHeader:
    message_type: MessageType
    flags: HeaderFlags = HeaderFlags.NONE
    session_id: int = 0
    frame_id: int = 0
    view_id: int = 0
    route_id: int = 0
    trace_id: int = 0
    version_major: int = 1
    wire_format: WireFormat = WireFormat.CURRENT


@dataclass(frozen=True, slots=True)
class DecodedRuntimeFrame:
    header: RuntimeFrameHeader
    metadata: bytes
    body: bytes


@dataclass(frozen=True, slots=True)
class ControlRequestMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<QQHBBIQ")

    operation_id: int
    control_sequence: int
    reason_code: int
    source_role: RuntimeRole | int
    flags: int
    diagnostic_bytes: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x03, "control_request.flags")
        return self.STRUCT.pack(
            self.operation_id,
            self.control_sequence,
            self.reason_code,
            int(self.source_role),
            self.flags,
            self.diagnostic_bytes,
            0,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> ControlRequestMetadata:
        _require_zero(values[6], "control_request.reserved")
        return cls(values[0], values[1], values[2], _runtime_role_or_int(values[3]), values[4], values[5])


@dataclass(frozen=True, slots=True)
class SchedulingMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<QQHhQI")

    operation_id: int
    control_sequence: int
    priority_class: int
    priority_delta: int
    deadline_unix_ms: int
    flags: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x00000003, "scheduling.flags")
        return self.STRUCT.pack(
            self.operation_id,
            self.control_sequence,
            self.priority_class,
            self.priority_delta,
            self.deadline_unix_ms,
            self.flags,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> SchedulingMetadata:
        _validate_mask(values[5], 0x00000003, "scheduling.flags")
        return cls(*values)


@dataclass(frozen=True, slots=True)
class SupersedeMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<QQQHHI")

    old_operation_id: int
    new_operation_id: int
    control_sequence: int
    drop_reason_code: int
    flags: int
    diagnostic_bytes: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x0001, "supersede.flags")
        return self.STRUCT.pack(
            self.old_operation_id,
            self.new_operation_id,
            self.control_sequence,
            self.drop_reason_code,
            self.flags,
            self.diagnostic_bytes,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> SupersedeMetadata:
        _validate_mask(values[4], 0x0001, "supersede.flags")
        return cls(*values)


@dataclass(frozen=True, slots=True)
class BudgetMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<QQQQII")

    operation_id: int
    compute_budget_units: int
    memory_budget_bytes: int
    bandwidth_budget_bytes: int
    token_budget: int
    flags: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x00000003, "budget.flags")
        return self.STRUCT.pack(
            self.operation_id,
            self.compute_budget_units,
            self.memory_budget_bytes,
            self.bandwidth_budget_bytes,
            self.token_budget,
            self.flags,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> BudgetMetadata:
        _validate_mask(values[5], 0x00000003, "budget.flags")
        return cls(*values)


@dataclass(frozen=True, slots=True)
class ProgressMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<QQHHQI")

    operation_id: int
    progress_sequence: int
    stage_code: int
    percent_x100: int
    object_id: int
    body_bytes: int

    def pack(self) -> bytes:
        _validate_percent_x100(self.percent_x100)
        return self.STRUCT.pack(
            self.operation_id,
            self.progress_sequence,
            self.stage_code,
            self.percent_x100,
            self.object_id,
            self.body_bytes,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> ProgressMetadata:
        _validate_percent_x100(values[3])
        return cls(*values)


@dataclass(frozen=True, slots=True)
class PartialResultMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<QQQQII")

    operation_id: int
    result_sequence: int
    object_id: int
    delta_sequence: int
    body_bytes: int
    flags: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x00000003, "partial_result.flags")
        return self.STRUCT.pack(
            self.operation_id,
            self.result_sequence,
            self.object_id,
            self.delta_sequence,
            self.body_bytes,
            self.flags,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> PartialResultMetadata:
        _validate_mask(values[5], 0x00000003, "partial_result.flags")
        return cls(*values)


@dataclass(frozen=True, slots=True)
class PressureMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<QQHHIII")

    scope_id: int
    credit_window: int
    pressure_level: int
    pressure_reason: int
    retry_after_ms: int
    flags: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x00000003, "pressure.flags")
        return self.STRUCT.pack(
            self.scope_id,
            self.credit_window,
            self.pressure_level,
            self.pressure_reason,
            self.retry_after_ms,
            self.flags,
            0,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> PressureMetadata:
        _validate_mask(values[5], 0x00000003, "pressure.flags")
        _require_zero(values[6], "pressure.reserved")
        return cls(*values[:6])


@dataclass(frozen=True, slots=True)
class CapabilityMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<HHHHQQII")

    profile_id: int
    capability_count: int
    cost_model_id: int
    preference_rank: int
    limit_bytes: int
    limit_units: int
    body_bytes: int
    flags: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x00000003, "capability.flags")
        return self.STRUCT.pack(
            self.profile_id,
            self.capability_count,
            self.cost_model_id,
            self.preference_rank,
            self.limit_bytes,
            self.limit_units,
            self.body_bytes,
            self.flags,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> CapabilityMetadata:
        _validate_mask(values[7], 0x00000003, "capability.flags")
        return cls(*values)


@dataclass(frozen=True, slots=True)
class RouteHintMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<QIHHQII")

    operation_id: int
    route_id: int
    executor_class: int
    affinity_class: int
    deadline_unix_ms: int
    body_bytes: int
    flags: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x00000003, "route_hint.flags")
        return self.STRUCT.pack(
            self.operation_id,
            self.route_id,
            self.executor_class,
            self.affinity_class,
            self.deadline_unix_ms,
            self.body_bytes,
            self.flags,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> RouteHintMetadata:
        _validate_mask(values[6], 0x00000003, "route_hint.flags")
        return cls(*values)


@dataclass(frozen=True, slots=True)
class TraceContextMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<QQQHHI")

    trace_id: int
    span_id: int
    parent_span_id: int
    stage_code: int
    flags: int
    body_bytes: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x0003, "trace_context.flags")
        return self.STRUCT.pack(
            self.trace_id,
            self.span_id,
            self.parent_span_id,
            self.stage_code,
            self.flags,
            self.body_bytes,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> TraceContextMetadata:
        _validate_mask(values[4], 0x0003, "trace_context.flags")
        return cls(*values)


@dataclass(frozen=True, slots=True)
class ResultDropReasonMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<QQHBBIQ")

    operation_id: int
    result_sequence: int
    drop_reason_code: ResultDropReasonCode | int
    source_role: RuntimeRole | int
    flags: int
    diagnostic_bytes: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x03, "result_drop_reason.flags")
        return self.STRUCT.pack(
            self.operation_id,
            self.result_sequence,
            int(self.drop_reason_code),
            int(self.source_role),
            self.flags,
            self.diagnostic_bytes,
            0,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> ResultDropReasonMetadata:
        _validate_mask(values[4], 0x03, "result_drop_reason.flags")
        _require_zero(values[6], "result_drop_reason.reserved")
        return cls(
            values[0],
            values[1],
            _result_drop_reason_or_int(values[2]),
            _runtime_role_or_int(values[3]),
            values[4],
            values[5],
        )


@dataclass(frozen=True, slots=True)
class RecoverableErrorMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<IIHBBIIIII")

    error_code: int
    error_scope: int
    recovery_action: int
    source_role: RuntimeRole | int
    flags: int
    retry_after_ms: int
    related_session_id: int
    related_frame_id: int
    related_view_id: int
    diagnostic_bytes: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x03, "recoverable_error.flags")
        return self.STRUCT.pack(
            self.error_code,
            self.error_scope,
            self.recovery_action,
            int(self.source_role),
            self.flags,
            self.retry_after_ms,
            self.related_session_id,
            self.related_frame_id,
            self.related_view_id,
            self.diagnostic_bytes,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> RecoverableErrorMetadata:
        _validate_mask(values[4], 0x03, "recoverable_error.flags")
        return cls(values[0], values[1], values[2], _runtime_role_or_int(values[3]), *values[4:])


@dataclass(frozen=True, slots=True)
class RetryAfterMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<QQIIHBBI")

    scope_id: int
    control_sequence: int
    retry_after_ms: int
    jitter_ms: int
    reason_code: int
    source_role: RuntimeRole | int
    flags: int
    diagnostic_bytes: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x03, "retry_after.flags")
        return self.STRUCT.pack(
            self.scope_id,
            self.control_sequence,
            self.retry_after_ms,
            self.jitter_ms,
            self.reason_code,
            int(self.source_role),
            self.flags,
            self.diagnostic_bytes,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> RetryAfterMetadata:
        _validate_mask(values[6], 0x03, "retry_after.flags")
        return cls(
            values[0],
            values[1],
            values[2],
            values[3],
            values[4],
            _runtime_role_or_int(values[5]),
            values[6],
            values[7],
        )


@dataclass(frozen=True, slots=True)
class ObjectDescriptorMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<QHBBIQIHHIIQ")

    object_id: int
    object_kind: RuntimeObjectKind | int
    producer_role: RuntimeRole | int
    consumer_role: RuntimeRole | int
    session_id: int
    byte_size: int
    compute_cost_units: int
    memory_location_hint: MemoryLocationHint | int
    ownership_hint: OwnershipHint | int
    lifetime_hint_ms: int
    metadata_bytes: int

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.object_id,
            int(self.object_kind),
            int(self.producer_role),
            int(self.consumer_role),
            self.session_id,
            self.byte_size,
            self.compute_cost_units,
            int(self.memory_location_hint),
            int(self.ownership_hint),
            self.lifetime_hint_ms,
            self.metadata_bytes,
            0,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> ObjectDescriptorMetadata:
        _require_zero(values[11], "object_descriptor.reserved")
        return cls(
            values[0],
            RuntimeObjectKind(values[1]),
            RuntimeRole(values[2]),
            RuntimeRole(values[3]),
            values[4],
            values[5],
            values[6],
            MemoryLocationHint(values[7]),
            OwnershipHint(values[8]),
            values[9],
            values[10],
        )


@dataclass(frozen=True, slots=True)
class ObjectReferenceMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<QQQQQII")

    object_id: int
    operation_id: int
    object_version: int
    offset: int
    length: int
    flags: int
    metadata_bytes: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x00000007, "object_reference.flags")
        return self.STRUCT.pack(
            self.object_id,
            self.operation_id,
            self.object_version,
            self.offset,
            self.length,
            self.flags,
            self.metadata_bytes,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> ObjectReferenceMetadata:
        _validate_mask(values[5], 0x00000007, "object_reference.flags")
        return cls(*values)


@dataclass(frozen=True, slots=True)
class ObjectReleaseMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<QQHBBIQ")

    object_id: int
    operation_id: int
    release_reason: ObjectReleaseReason | int
    source_role: RuntimeRole | int
    flags: int
    diagnostic_bytes: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x03, "object_release.flags")
        return self.STRUCT.pack(
            self.object_id,
            self.operation_id,
            int(self.release_reason),
            int(self.source_role),
            self.flags,
            self.diagnostic_bytes,
            0,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> ObjectReleaseMetadata:
        _validate_mask(values[4], 0x03, "object_release.flags")
        _require_zero(values[6], "object_release.reserved")
        return cls(values[0], values[1], ObjectReleaseReason(values[2]), RuntimeRole(values[3]), values[4], values[5])


@dataclass(frozen=True, slots=True)
class ObjectDeltaMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<QQQIIII")

    object_id: int
    delta_sequence: int
    region_offset: int
    region_bytes: int
    delta_bytes: int
    flags: int
    metadata_bytes: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x00000007, "object_delta.flags")
        return self.STRUCT.pack(
            self.object_id,
            self.delta_sequence,
            self.region_offset,
            self.region_bytes,
            self.delta_bytes,
            self.flags,
            self.metadata_bytes,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> ObjectDeltaMetadata:
        _validate_mask(values[5], 0x00000007, "object_delta.flags")
        return cls(*values)


@dataclass(frozen=True, slots=True)
class CacheReferenceMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<IHHQQQQIIII")

    cache_namespace: int
    cache_key_hi: int
    cache_key_lo: int
    profile_id: int
    reuse_scope: CacheReuseScope | int
    lease_id: int
    producer_trace_id: int
    expiration_hint_ms: int
    metadata_bytes: int
    flags: int

    def pack(self) -> bytes:
        _validate_mask(self.flags, 0x00000003, "cache_reference.flags")
        return self.STRUCT.pack(
            self.cache_namespace,
            self.profile_id,
            int(self.reuse_scope),
            self.cache_key_hi,
            self.cache_key_lo,
            self.lease_id,
            self.producer_trace_id,
            self.expiration_hint_ms,
            self.metadata_bytes,
            self.flags,
            0,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> CacheReferenceMetadata:
        _validate_mask(values[9], 0x00000003, "cache_reference.flags")
        _require_zero(values[10], "cache_reference.reserved")
        return cls(values[0], values[3], values[4], values[1], CacheReuseScope(values[2]), *values[5:10])


@dataclass(frozen=True, slots=True)
class CacheMissMetadata(_FixedRuntimeMetadata):
    STRUCT: ClassVar[struct.Struct] = struct.Struct("<IHHQQII")

    cache_namespace: int
    cache_key_hi: int
    cache_key_lo: int
    miss_reason: CacheMissReason | int
    profile_id: int
    diagnostic_bytes: int

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.cache_namespace,
            self.profile_id,
            int(self.miss_reason),
            self.cache_key_hi,
            self.cache_key_lo,
            self.diagnostic_bytes,
            0,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> CacheMissMetadata:
        _require_zero(values[6], "cache_miss.reserved")
        return cls(values[0], values[3], values[4], CacheMissReason(values[2]), values[1], values[5])


def _validate_mask(value: int, allowed: int, field: str) -> None:
    if value & ~allowed:
        raise ValueError(f"{field} has reserved bits set: 0x{value:x}")


def _require_zero(value: int, field: str) -> None:
    if value != 0:
        raise ValueError(f"{field} must be zero")


def _validate_percent_x100(value: int) -> None:
    if 0 <= value <= 10_000 or value == 0xFFFF:
        return
    raise ValueError("progress.percent_x100 must be 0..10000 or 0xffff")


def _runtime_role_or_int(value: int) -> RuntimeRole | int:
    try:
        return RuntimeRole(value)
    except ValueError:
        return value


def _result_drop_reason_or_int(value: int) -> ResultDropReasonCode | int:
    try:
        return ResultDropReasonCode(value)
    except ValueError:
        return value
