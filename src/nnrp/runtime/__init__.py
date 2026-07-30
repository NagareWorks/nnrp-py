"""Preview4 runtime control, object, and WebSocket binary frame helpers."""

from __future__ import annotations

from nnrp.core import HEADER_LENGTH, MessageType, NnrpHeader
from nnrp.runtime.types import (
    BudgetMetadata,
    CacheMissMetadata,
    CacheMissReason,
    CacheReferenceMetadata,
    CacheReuseScope,
    CapabilityMetadata,
    ControlRequestMetadata,
    DecodedRuntimeControlMetadata,
    DecodedRuntimeFrame,
    DecodedRuntimeObjectMetadata,
    InFlightPolicy,
    MemoryLocationHint,
    NativeRuntimeEvent,
    ObjectDeltaMetadata,
    ObjectDescriptorMetadata,
    ObjectReferenceMetadata,
    ObjectReleaseMetadata,
    ObjectReleaseReason,
    OwnershipHint,
    PartialResultMetadata,
    PressureMetadata,
    ProgressMetadata,
    RecoverableErrorMetadata,
    ResultDropReasonCode,
    ResultDropReasonMetadata,
    RetryAfterMetadata,
    RouteHintMetadata,
    RuntimeEventMetadata,
    RuntimeEventMetadataKind,
    RuntimeEventTail,
    RuntimeEventTailKind,
    RuntimeFrameHeader,
    RuntimeObjectKind,
    RuntimeRole,
    SchedulingMetadata,
    SessionCloseMetadata,
    SessionCloseReason,
    SupersedeMetadata,
    TraceContextMetadata,
    _FixedRuntimeMetadata,
)

CONTROL_REQUEST_METADATA_LENGTH = ControlRequestMetadata.STRUCT.size
SCHEDULING_METADATA_LENGTH = SchedulingMetadata.STRUCT.size
SUPERSEDE_METADATA_LENGTH = SupersedeMetadata.STRUCT.size
BUDGET_METADATA_LENGTH = BudgetMetadata.STRUCT.size
PROGRESS_METADATA_LENGTH = ProgressMetadata.STRUCT.size
PARTIAL_RESULT_METADATA_LENGTH = PartialResultMetadata.STRUCT.size
PRESSURE_METADATA_LENGTH = PressureMetadata.STRUCT.size
CAPABILITY_METADATA_LENGTH = CapabilityMetadata.STRUCT.size
ROUTE_HINT_METADATA_LENGTH = RouteHintMetadata.STRUCT.size
TRACE_CONTEXT_METADATA_LENGTH = TraceContextMetadata.STRUCT.size
RESULT_DROP_REASON_METADATA_LENGTH = ResultDropReasonMetadata.STRUCT.size
RECOVERABLE_ERROR_METADATA_LENGTH = RecoverableErrorMetadata.STRUCT.size
RETRY_AFTER_METADATA_LENGTH = RetryAfterMetadata.STRUCT.size
OBJECT_DESCRIPTOR_METADATA_LENGTH = ObjectDescriptorMetadata.STRUCT.size
OBJECT_REFERENCE_METADATA_LENGTH = ObjectReferenceMetadata.STRUCT.size
OBJECT_RELEASE_METADATA_LENGTH = ObjectReleaseMetadata.STRUCT.size
OBJECT_DELTA_METADATA_LENGTH = ObjectDeltaMetadata.STRUCT.size
CACHE_REFERENCE_METADATA_LENGTH = CacheReferenceMetadata.STRUCT.size
CACHE_MISS_METADATA_LENGTH = CacheMissMetadata.STRUCT.size

_RUNTIME_CONTROL_TYPES: dict[MessageType, type[_FixedRuntimeMetadata]] = {
    MessageType.CANCEL: ControlRequestMetadata,
    MessageType.ABORT: ControlRequestMetadata,
    MessageType.PRIORITY_UPDATE: SchedulingMetadata,
    MessageType.DEADLINE: SchedulingMetadata,
    MessageType.EXPIRE_AT: SchedulingMetadata,
    MessageType.SUPERSEDE: SupersedeMetadata,
    MessageType.BUDGET_UPDATE: BudgetMetadata,
    MessageType.PROGRESS: ProgressMetadata,
    MessageType.PARTIAL_RESULT: PartialResultMetadata,
    MessageType.BACKPRESSURE: PressureMetadata,
    MessageType.CREDIT_UPDATE: PressureMetadata,
    MessageType.CAPABILITY_NEGOTIATION: CapabilityMetadata,
    MessageType.DEGRADE_PROFILE: CapabilityMetadata,
    MessageType.ROUTE_HINT: RouteHintMetadata,
    MessageType.EXECUTION_HINT: RouteHintMetadata,
    MessageType.TRACE_CONTEXT: TraceContextMetadata,
    MessageType.RESULT_DROP_REASON: ResultDropReasonMetadata,
    MessageType.ERROR_RECOVERABLE: RecoverableErrorMetadata,
    MessageType.RETRY_AFTER: RetryAfterMetadata,
}

_RUNTIME_OBJECT_TYPES: dict[MessageType, type[_FixedRuntimeMetadata]] = {
    MessageType.OBJECT_DECLARE: ObjectDescriptorMetadata,
    MessageType.OBJECT_REF: ObjectReferenceMetadata,
    MessageType.OBJECT_RELEASE: ObjectReleaseMetadata,
    MessageType.OBJECT_PATCH: ObjectDeltaMetadata,
    MessageType.OBJECT_DELTA: ObjectDeltaMetadata,
    MessageType.CACHE_REFERENCE: CacheReferenceMetadata,
    MessageType.CACHE_MISS: CacheMissMetadata,
}


def encode_runtime_control_metadata(
    message_type: MessageType,
    metadata: _FixedRuntimeMetadata,
    *,
    tail: bytes | bytearray | memoryview = b"",
) -> bytes:
    fixed_type = _metadata_type(_RUNTIME_CONTROL_TYPES, message_type)
    if not isinstance(metadata, fixed_type):
        raise TypeError(f"{MessageType(message_type).name} requires {fixed_type.__name__}")
    fixed = metadata.pack()
    tail_bytes = _snapshot_payload("tail", tail)
    _validate_declared_tail(message_type, metadata, tail_bytes)
    return fixed + tail_bytes


def decode_runtime_control_metadata(
    message_type: MessageType,
    payload: bytes | bytearray | memoryview,
) -> DecodedRuntimeControlMetadata:
    payload_bytes = _snapshot_payload("payload", payload)
    fixed_type = _metadata_type(_RUNTIME_CONTROL_TYPES, message_type)
    fixed, tail = _split_fixed_tail(payload_bytes, fixed_type)
    metadata = fixed_type.unpack(fixed)
    _validate_declared_tail(message_type, metadata, tail)
    return DecodedRuntimeControlMetadata(metadata=metadata, tail=tail)


def encode_runtime_object_metadata(
    message_type: MessageType,
    metadata: _FixedRuntimeMetadata,
    *,
    tail: bytes | bytearray | memoryview = b"",
) -> bytes:
    fixed_type = _metadata_type(_RUNTIME_OBJECT_TYPES, message_type)
    if not isinstance(metadata, fixed_type):
        raise TypeError(f"{MessageType(message_type).name} requires {fixed_type.__name__}")
    fixed = metadata.pack()
    tail_bytes = _snapshot_payload("tail", tail)
    _validate_declared_tail(message_type, metadata, tail_bytes)
    return fixed + tail_bytes


def declare_runtime_object(
    metadata: ObjectDescriptorMetadata,
    *,
    metadata_tail: bytes | bytearray | memoryview = b"",
) -> bytes:
    return encode_runtime_object_metadata(MessageType.OBJECT_DECLARE, metadata, tail=metadata_tail)


def reference_runtime_object(
    metadata: ObjectReferenceMetadata,
    *,
    metadata_tail: bytes | bytearray | memoryview = b"",
) -> bytes:
    return encode_runtime_object_metadata(MessageType.OBJECT_REF, metadata, tail=metadata_tail)


def release_runtime_object(
    metadata: ObjectReleaseMetadata,
    *,
    diagnostic_tail: bytes | bytearray | memoryview = b"",
) -> bytes:
    return encode_runtime_object_metadata(MessageType.OBJECT_RELEASE, metadata, tail=diagnostic_tail)


def patch_runtime_object(
    metadata: ObjectDeltaMetadata,
    *,
    metadata_tail: bytes | bytearray | memoryview = b"",
    delta: bytes | bytearray | memoryview = b"",
) -> bytes:
    return encode_runtime_object_metadata(
        MessageType.OBJECT_PATCH,
        metadata,
        tail=_snapshot_payload("metadata_tail", metadata_tail) + _snapshot_payload("delta", delta),
    )


def delta_runtime_object(
    metadata: ObjectDeltaMetadata,
    *,
    metadata_tail: bytes | bytearray | memoryview = b"",
    delta: bytes | bytearray | memoryview = b"",
) -> bytes:
    return encode_runtime_object_metadata(
        MessageType.OBJECT_DELTA,
        metadata,
        tail=_snapshot_payload("metadata_tail", metadata_tail) + _snapshot_payload("delta", delta),
    )


def partial_result_runtime_object(
    metadata: PartialResultMetadata,
    *,
    body: bytes | bytearray | memoryview = b"",
) -> bytes:
    return encode_runtime_control_metadata(MessageType.PARTIAL_RESULT, metadata, tail=body)


def decode_runtime_object_metadata(
    message_type: MessageType,
    payload: bytes | bytearray | memoryview,
) -> DecodedRuntimeObjectMetadata:
    payload_bytes = _snapshot_payload("payload", payload)
    fixed_type = _metadata_type(_RUNTIME_OBJECT_TYPES, message_type)
    fixed, tail = _split_fixed_tail(payload_bytes, fixed_type)
    metadata = fixed_type.unpack(fixed)
    _validate_declared_tail(message_type, metadata, tail)
    return DecodedRuntimeObjectMetadata(metadata=metadata, tail=tail)


def encode_websocket_binary_frame(
    header: RuntimeFrameHeader,
    metadata: bytes | bytearray | memoryview = b"",
    body: bytes | bytearray | memoryview = b"",
) -> bytes:
    metadata_bytes = _require_websocket_binary_payload("metadata", metadata)
    body_bytes = _require_websocket_binary_payload("body", body)
    packet_header = NnrpHeader(
        version_major=header.version_major,
        wire_format=header.wire_format,
        msg_type=header.message_type,
        flags=header.flags,
        meta_len=len(metadata_bytes),
        body_len=len(body_bytes),
        session_id=header.session_id,
        frame_id=header.frame_id,
        view_id=header.view_id,
        route_id=header.route_id,
        trace_id=header.trace_id,
    )
    return packet_header.pack() + metadata_bytes + body_bytes


def decode_websocket_binary_frame(frame: bytes | bytearray | memoryview) -> DecodedRuntimeFrame:
    frame_bytes = _require_websocket_binary_payload("frame", frame)
    if len(frame_bytes) < HEADER_LENGTH:
        raise ValueError("incomplete WebSocket binary frame header")
    header = NnrpHeader.unpack(frame_bytes[:HEADER_LENGTH])
    total_len = header.header_len + header.meta_len + header.body_len
    if len(frame_bytes) != total_len:
        raise ValueError(f"expected {total_len} bytes, got {len(frame_bytes)}")
    metadata_start = header.header_len
    body_start = metadata_start + header.meta_len
    return DecodedRuntimeFrame(
        header=_runtime_frame_header_from_header(header),
        metadata=frame_bytes[metadata_start:body_start],
        body=frame_bytes[body_start:],
    )


def decode_websocket_binary_frame_batch(
    batch: bytes | bytearray | memoryview,
    *,
    limit: int = 0,
) -> list[DecodedRuntimeFrame]:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    batch_bytes = _require_websocket_binary_payload("batch", batch)
    frames: list[DecodedRuntimeFrame] = []
    cursor = 0
    while cursor < len(batch_bytes):
        if limit != 0 and len(frames) >= limit:
            raise ValueError(f"WebSocket batch contains more than {limit} frames")
        if len(batch_bytes) - cursor < HEADER_LENGTH:
            raise ValueError("incomplete WebSocket binary frame in batch")
        header = NnrpHeader.unpack(batch_bytes[cursor : cursor + HEADER_LENGTH])
        frame_len = header.header_len + header.meta_len + header.body_len
        if len(batch_bytes) - cursor < frame_len:
            raise ValueError("incomplete WebSocket binary frame in batch")
        frames.append(decode_websocket_binary_frame(batch_bytes[cursor : cursor + frame_len]))
        cursor += frame_len
    return frames


def _metadata_type(
    registry: dict[MessageType, type[_FixedRuntimeMetadata]],
    message_type: MessageType,
) -> type[_FixedRuntimeMetadata]:
    normalized = MessageType(message_type)
    try:
        return registry[normalized]
    except KeyError as exc:
        raise ValueError(f"{normalized.name} does not carry preview4 runtime metadata") from exc


def _split_fixed_tail(payload: bytes, fixed_type: type[_FixedRuntimeMetadata]) -> tuple[bytes, bytes]:
    fixed_len = fixed_type.STRUCT.size
    if len(payload) < fixed_len:
        raise ValueError(f"expected at least {fixed_len} bytes, got {len(payload)}")
    return payload[:fixed_len], payload[fixed_len:]


def _snapshot_payload(name: str, value: bytes | bytearray | memoryview) -> bytes:
    if isinstance(value, str):
        raise TypeError(f"runtime {name} must be bytes-like")
    try:
        return memoryview(value).tobytes()
    except TypeError as error:
        raise TypeError(f"runtime {name} must be bytes-like") from error


def _validate_declared_tail(message_type: MessageType, metadata: _FixedRuntimeMetadata, tail: bytes) -> None:
    declared = _declared_tail_length(metadata)
    if declared != len(tail):
        raise ValueError(f"{MessageType(message_type).name} declared tail length {declared}, got {len(tail)}")


def _declared_tail_length(metadata: _FixedRuntimeMetadata) -> int:
    if isinstance(metadata, ObjectDeltaMetadata):
        return metadata.metadata_bytes + metadata.delta_bytes
    for field_name in ("diagnostic_bytes", "body_bytes", "metadata_bytes"):
        if hasattr(metadata, field_name):
            return int(getattr(metadata, field_name))
    return 0


def _require_websocket_binary_payload(name: str, value: bytes | bytearray | memoryview) -> bytes:
    if isinstance(value, str):
        raise TypeError(f"WebSocket {name} must be a binary frame payload, not text")
    try:
        view = memoryview(value)
    except TypeError as error:
        raise TypeError(f"WebSocket {name} must be bytes-like") from error
    if not view.contiguous:
        raise ValueError(f"WebSocket {name} memoryview must be contiguous")
    return view.tobytes()


def _runtime_frame_header_from_header(header: NnrpHeader) -> RuntimeFrameHeader:
    return RuntimeFrameHeader(
        message_type=header.msg_type,
        flags=header.flags,
        session_id=header.session_id,
        frame_id=header.frame_id,
        view_id=header.view_id,
        route_id=header.route_id,
        trace_id=header.trace_id,
        version_major=header.version_major,
        wire_format=header.wire_format,
    )


__all__ = [
    "BUDGET_METADATA_LENGTH",
    "CACHE_MISS_METADATA_LENGTH",
    "CACHE_REFERENCE_METADATA_LENGTH",
    "CAPABILITY_METADATA_LENGTH",
    "CONTROL_REQUEST_METADATA_LENGTH",
    "OBJECT_DELTA_METADATA_LENGTH",
    "OBJECT_DESCRIPTOR_METADATA_LENGTH",
    "OBJECT_REFERENCE_METADATA_LENGTH",
    "OBJECT_RELEASE_METADATA_LENGTH",
    "PARTIAL_RESULT_METADATA_LENGTH",
    "PRESSURE_METADATA_LENGTH",
    "PROGRESS_METADATA_LENGTH",
    "RECOVERABLE_ERROR_METADATA_LENGTH",
    "RESULT_DROP_REASON_METADATA_LENGTH",
    "RETRY_AFTER_METADATA_LENGTH",
    "ROUTE_HINT_METADATA_LENGTH",
    "SCHEDULING_METADATA_LENGTH",
    "SUPERSEDE_METADATA_LENGTH",
    "TRACE_CONTEXT_METADATA_LENGTH",
    "BudgetMetadata",
    "CacheMissMetadata",
    "CacheMissReason",
    "CacheReferenceMetadata",
    "CacheReuseScope",
    "CapabilityMetadata",
    "ControlRequestMetadata",
    "DecodedRuntimeControlMetadata",
    "DecodedRuntimeFrame",
    "DecodedRuntimeObjectMetadata",
    "MemoryLocationHint",
    "InFlightPolicy",
    "ObjectDeltaMetadata",
    "ObjectDescriptorMetadata",
    "ObjectReferenceMetadata",
    "ObjectReleaseMetadata",
    "ObjectReleaseReason",
    "OwnershipHint",
    "PartialResultMetadata",
    "PressureMetadata",
    "ProgressMetadata",
    "RecoverableErrorMetadata",
    "ResultDropReasonCode",
    "ResultDropReasonMetadata",
    "RetryAfterMetadata",
    "RouteHintMetadata",
    "RuntimeEventMetadata",
    "RuntimeEventMetadataKind",
    "RuntimeEventTail",
    "RuntimeEventTailKind",
    "RuntimeFrameHeader",
    "NativeRuntimeEvent",
    "RuntimeObjectKind",
    "RuntimeRole",
    "SessionCloseMetadata",
    "SessionCloseReason",
    "SchedulingMetadata",
    "SupersedeMetadata",
    "TraceContextMetadata",
    "declare_runtime_object",
    "decode_runtime_control_metadata",
    "decode_runtime_object_metadata",
    "decode_websocket_binary_frame",
    "decode_websocket_binary_frame_batch",
    "delta_runtime_object",
    "encode_runtime_control_metadata",
    "encode_runtime_object_metadata",
    "encode_websocket_binary_frame",
    "partial_result_runtime_object",
    "patch_runtime_object",
    "reference_runtime_object",
    "release_runtime_object",
]
