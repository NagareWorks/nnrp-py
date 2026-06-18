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
    MemoryLocationHint,
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
    RuntimeFrameHeader,
    RuntimeObjectKind,
    RuntimeRole,
    SchedulingMetadata,
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
    tail: bytes = b"",
) -> bytes:
    fixed_type = _metadata_type(_RUNTIME_CONTROL_TYPES, message_type)
    if not isinstance(metadata, fixed_type):
        raise TypeError(f"{MessageType(message_type).name} requires {fixed_type.__name__}")
    fixed = metadata.pack()
    _validate_declared_tail(message_type, metadata, tail)
    return fixed + bytes(tail)


def decode_runtime_control_metadata(message_type: MessageType, payload: bytes) -> DecodedRuntimeControlMetadata:
    fixed_type = _metadata_type(_RUNTIME_CONTROL_TYPES, message_type)
    fixed, tail = _split_fixed_tail(payload, fixed_type)
    metadata = fixed_type.unpack(fixed)
    _validate_declared_tail(message_type, metadata, tail)
    return DecodedRuntimeControlMetadata(metadata=metadata, tail=tail)


def encode_runtime_object_metadata(
    message_type: MessageType,
    metadata: _FixedRuntimeMetadata,
    *,
    tail: bytes = b"",
) -> bytes:
    fixed_type = _metadata_type(_RUNTIME_OBJECT_TYPES, message_type)
    if not isinstance(metadata, fixed_type):
        raise TypeError(f"{MessageType(message_type).name} requires {fixed_type.__name__}")
    fixed = metadata.pack()
    _validate_declared_tail(message_type, metadata, tail)
    return fixed + bytes(tail)


def decode_runtime_object_metadata(message_type: MessageType, payload: bytes) -> DecodedRuntimeObjectMetadata:
    fixed_type = _metadata_type(_RUNTIME_OBJECT_TYPES, message_type)
    fixed, tail = _split_fixed_tail(payload, fixed_type)
    metadata = fixed_type.unpack(fixed)
    _validate_declared_tail(message_type, metadata, tail)
    return DecodedRuntimeObjectMetadata(metadata=metadata, tail=tail)


def encode_websocket_binary_frame(
    header: RuntimeFrameHeader,
    metadata: bytes = b"",
    body: bytes = b"",
) -> bytes:
    packet_header = NnrpHeader(
        version_major=header.version_major,
        wire_format=header.wire_format,
        msg_type=header.message_type,
        flags=header.flags,
        meta_len=len(metadata),
        body_len=len(body),
        session_id=header.session_id,
        frame_id=header.frame_id,
        view_id=header.view_id,
        route_id=header.route_id,
        trace_id=header.trace_id,
    )
    return packet_header.pack() + bytes(metadata) + bytes(body)


def decode_websocket_binary_frame(frame: bytes) -> DecodedRuntimeFrame:
    if len(frame) < HEADER_LENGTH:
        raise ValueError("incomplete WebSocket binary frame header")
    header = NnrpHeader.unpack(frame[:HEADER_LENGTH])
    total_len = header.header_len + header.meta_len + header.body_len
    if len(frame) != total_len:
        raise ValueError(f"expected {total_len} bytes, got {len(frame)}")
    metadata_start = header.header_len
    body_start = metadata_start + header.meta_len
    return DecodedRuntimeFrame(
        header=_runtime_frame_header_from_header(header),
        metadata=frame[metadata_start:body_start],
        body=frame[body_start:],
    )


def decode_websocket_binary_frame_batch(batch: bytes, *, limit: int = 0) -> list[DecodedRuntimeFrame]:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    frames: list[DecodedRuntimeFrame] = []
    cursor = 0
    while cursor < len(batch) and (limit == 0 or len(frames) < limit):
        if len(batch) - cursor < HEADER_LENGTH:
            raise ValueError("incomplete WebSocket binary frame in batch")
        header = NnrpHeader.unpack(batch[cursor : cursor + HEADER_LENGTH])
        frame_len = header.header_len + header.meta_len + header.body_len
        if len(batch) - cursor < frame_len:
            raise ValueError("incomplete WebSocket binary frame in batch")
        frames.append(decode_websocket_binary_frame(batch[cursor : cursor + frame_len]))
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


def _runtime_frame_header_from_header(header: NnrpHeader) -> RuntimeFrameHeader:
    return RuntimeFrameHeader(
        message_type=header.msg_type,
        flags=header.flags,
        session_id=header.session_id,
        generation=0,
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
    "RuntimeFrameHeader",
    "RuntimeObjectKind",
    "RuntimeRole",
    "SchedulingMetadata",
    "SupersedeMetadata",
    "TraceContextMetadata",
    "decode_runtime_control_metadata",
    "decode_runtime_object_metadata",
    "decode_websocket_binary_frame",
    "decode_websocket_binary_frame_batch",
    "encode_runtime_control_metadata",
    "encode_runtime_object_metadata",
    "encode_websocket_binary_frame",
]
