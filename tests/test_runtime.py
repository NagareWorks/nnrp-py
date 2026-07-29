from dataclasses import fields

import pytest

from nnrp.client import ClientDialPolicy
from nnrp.core import HeaderFlags, MessageType, TransportId, TransportPolicy
from nnrp.runtime import (
    BudgetMetadata,
    CacheMissMetadata,
    CacheMissReason,
    CacheReferenceMetadata,
    CacheReuseScope,
    CapabilityMetadata,
    ControlRequestMetadata,
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
    declare_runtime_object,
    decode_runtime_control_metadata,
    decode_runtime_object_metadata,
    decode_websocket_binary_frame,
    decode_websocket_binary_frame_batch,
    delta_runtime_object,
    encode_runtime_control_metadata,
    encode_runtime_object_metadata,
    encode_websocket_binary_frame,
    partial_result_runtime_object,
    patch_runtime_object,
    reference_runtime_object,
    release_runtime_object,
)
from nnrp.runtime.types import _FixedRuntimeMetadata


def test_preview4_message_type_values_are_frozen() -> None:
    assert MessageType.CANCEL == 0x30
    assert MessageType.PROGRESS == 0x37
    assert MessageType.OBJECT_DECLARE == 0x41
    assert MessageType.ERROR_RECOVERABLE == 0x48
    assert MessageType.RETRY_AFTER == 0x49


def test_runtime_control_progress_roundtrip_with_body() -> None:
    metadata = ProgressMetadata(
        operation_id=42,
        progress_sequence=7,
        stage_code=3,
        percent_x100=2500,
        object_id=11,
        body_bytes=4,
    )

    payload = encode_runtime_control_metadata(MessageType.PROGRESS, metadata, tail=b"step")
    decoded = decode_runtime_control_metadata(MessageType.PROGRESS, payload)

    assert decoded.metadata == metadata
    assert decoded.tail == b"step"


def test_runtime_control_rejects_wrong_metadata_type() -> None:
    with pytest.raises(TypeError, match="PROGRESS requires ProgressMetadata"):
        encode_runtime_control_metadata(
            MessageType.PROGRESS,
            ControlRequestMetadata(
                operation_id=1,
                control_sequence=1,
                reason_code=1,
                source_role=RuntimeRole.CLIENT,
                flags=0,
                diagnostic_bytes=0,
            ),
        )


def test_runtime_control_rejects_declared_tail_mismatch() -> None:
    metadata = ProgressMetadata(
        operation_id=42,
        progress_sequence=7,
        stage_code=3,
        percent_x100=2500,
        object_id=11,
        body_bytes=5,
    )

    with pytest.raises(ValueError, match="declared tail length 5, got 4"):
        encode_runtime_control_metadata(MessageType.PROGRESS, metadata, tail=b"step")


@pytest.mark.parametrize(
    ("message_type", "metadata", "tail"),
    [
        (
            MessageType.CANCEL,
            ControlRequestMetadata(
                operation_id=1,
                control_sequence=2,
                reason_code=3,
                source_role=99,
                flags=0x01,
                diagnostic_bytes=2,
            ),
            b"no",
        ),
        (
            MessageType.PRIORITY_UPDATE,
            SchedulingMetadata(
                operation_id=2,
                control_sequence=3,
                priority_class=4,
                priority_delta=-2,
                deadline_unix_ms=123,
                flags=0x01,
            ),
            b"",
        ),
        (
            MessageType.SUPERSEDE,
            SupersedeMetadata(
                old_operation_id=2,
                new_operation_id=3,
                control_sequence=4,
                drop_reason_code=5,
                flags=0x01,
                diagnostic_bytes=0,
            ),
            b"",
        ),
        (
            MessageType.BUDGET_UPDATE,
            BudgetMetadata(
                operation_id=3,
                compute_budget_units=4,
                memory_budget_bytes=5,
                bandwidth_budget_bytes=6,
                token_budget=7,
                flags=0x02,
            ),
            b"",
        ),
        (
            MessageType.PARTIAL_RESULT,
            PartialResultMetadata(
                operation_id=4,
                result_sequence=5,
                object_id=6,
                delta_sequence=7,
                body_bytes=3,
                flags=0x03,
            ),
            b"abc",
        ),
        (
            MessageType.BACKPRESSURE,
            PressureMetadata(
                scope_id=5,
                credit_window=6,
                pressure_level=7,
                pressure_reason=8,
                retry_after_ms=9,
                flags=0x01,
            ),
            b"",
        ),
        (
            MessageType.CAPABILITY_NEGOTIATION,
            CapabilityMetadata(
                profile_id=1,
                capability_count=2,
                cost_model_id=3,
                preference_rank=4,
                limit_bytes=5,
                limit_units=6,
                body_bytes=2,
                flags=0x02,
            ),
            b"{}",
        ),
        (
            MessageType.ROUTE_HINT,
            RouteHintMetadata(
                operation_id=8,
                route_id=9,
                executor_class=10,
                affinity_class=11,
                deadline_unix_ms=12,
                body_bytes=2,
                flags=0x01,
            ),
            b"rt",
        ),
        (
            MessageType.TRACE_CONTEXT,
            TraceContextMetadata(
                trace_id=13,
                span_id=14,
                parent_span_id=15,
                stage_code=16,
                flags=0x03,
                body_bytes=2,
            ),
            b"tr",
        ),
        (
            MessageType.RESULT_DROP_REASON,
            ResultDropReasonMetadata(
                operation_id=17,
                result_sequence=18,
                drop_reason_code=ResultDropReasonCode.PEER_CANCELLED,
                source_role=99,
                flags=0x01,
                diagnostic_bytes=2,
            ),
            b"rd",
        ),
        (
            MessageType.ERROR_RECOVERABLE,
            RecoverableErrorMetadata(
                error_code=20,
                error_scope=21,
                recovery_action=22,
                source_role=99,
                flags=0x02,
                retry_after_ms=23,
                related_session_id=24,
                related_frame_id=25,
                related_view_id=26,
                diagnostic_bytes=2,
            ),
            b"er",
        ),
        (
            MessageType.RETRY_AFTER,
            RetryAfterMetadata(
                scope_id=27,
                control_sequence=28,
                retry_after_ms=29,
                jitter_ms=30,
                reason_code=31,
                source_role=99,
                flags=0x01,
                diagnostic_bytes=2,
            ),
            b"ra",
        ),
    ],
)
def test_runtime_control_metadata_roundtrips_all_preview4_shapes(
    message_type: MessageType,
    metadata: _FixedRuntimeMetadata,
    tail: bytes,
) -> None:
    payload = encode_runtime_control_metadata(message_type, metadata, tail=tail)

    decoded = decode_runtime_control_metadata(message_type, payload)

    assert decoded.metadata == metadata
    assert decoded.tail == tail


def test_runtime_object_declare_roundtrip_with_extension() -> None:
    metadata = ObjectDescriptorMetadata(
        object_id=9,
        object_kind=RuntimeObjectKind.TENSOR,
        producer_role=RuntimeRole.RUNTIME,
        consumer_role=RuntimeRole.CLIENT,
        session_id=3,
        byte_size=4096,
        compute_cost_units=12,
        memory_location_hint=MemoryLocationHint.HOST_MEMORY,
        ownership_hint=OwnershipHint.CONSUMER_OWNED,
        lifetime_hint_ms=1000,
        metadata_bytes=3,
    )

    payload = encode_runtime_object_metadata(MessageType.OBJECT_DECLARE, metadata, tail=b"abc")
    decoded = decode_runtime_object_metadata(MessageType.OBJECT_DECLARE, payload)

    assert decoded.metadata == metadata
    assert decoded.tail == b"abc"


def test_runtime_object_lifecycle_helpers_encode_frozen_message_types() -> None:
    descriptor = ObjectDescriptorMetadata(
        object_id=9,
        object_kind=RuntimeObjectKind.IMAGE_TILE,
        producer_role=RuntimeRole.RUNTIME,
        consumer_role=RuntimeRole.CLIENT,
        session_id=3,
        byte_size=4096,
        compute_cost_units=12,
        memory_location_hint=MemoryLocationHint.HOST_MEMORY,
        ownership_hint=OwnershipHint.CONSUMER_OWNED,
        lifetime_hint_ms=1000,
        metadata_bytes=3,
    )
    object_ref = ObjectReferenceMetadata(
        object_id=9,
        operation_id=42,
        object_version=2,
        offset=128,
        length=256,
        flags=0x01,
        metadata_bytes=2,
    )
    release = ObjectReleaseMetadata(
        object_id=9,
        operation_id=42,
        release_reason=ObjectReleaseReason.COMPLETED,
        source_role=RuntimeRole.CLIENT,
        flags=0x01,
        diagnostic_bytes=2,
    )

    assert decode_runtime_object_metadata(
        MessageType.OBJECT_DECLARE,
        declare_runtime_object(descriptor, metadata_tail=b"abc"),
    ).metadata == descriptor
    assert decode_runtime_object_metadata(
        MessageType.OBJECT_REF,
        reference_runtime_object(object_ref, metadata_tail=b"md"),
    ).metadata == object_ref
    assert decode_runtime_object_metadata(
        MessageType.OBJECT_RELEASE,
        release_runtime_object(release, diagnostic_tail=b"ok"),
    ).metadata == release


def test_runtime_object_delta_and_partial_result_helpers_preserve_tail_boundaries() -> None:
    delta = ObjectDeltaMetadata(
        object_id=9,
        delta_sequence=2,
        region_offset=128,
        region_bytes=64,
        delta_bytes=4,
        flags=0x03,
        metadata_bytes=2,
    )
    partial = PartialResultMetadata(
        operation_id=4,
        result_sequence=5,
        object_id=9,
        delta_sequence=2,
        body_bytes=6,
        flags=0x03,
    )

    patch = decode_runtime_object_metadata(
        MessageType.OBJECT_PATCH,
        patch_runtime_object(delta, metadata_tail=b"md", delta=b"xxxx"),
    )
    object_delta = decode_runtime_object_metadata(
        MessageType.OBJECT_DELTA,
        delta_runtime_object(delta, metadata_tail=b"md", delta=b"xxxx"),
    )
    partial_result = decode_runtime_control_metadata(
        MessageType.PARTIAL_RESULT,
        partial_result_runtime_object(partial, body=b"output"),
    )

    assert patch.metadata == delta
    assert patch.tail == b"mdxxxx"
    assert object_delta.metadata == delta
    assert object_delta.tail == b"mdxxxx"
    assert partial_result.metadata == partial
    assert partial_result.tail == b"output"


def test_runtime_metadata_decoders_snapshot_mutable_payloads() -> None:
    progress = ProgressMetadata(
        operation_id=42,
        progress_sequence=7,
        stage_code=3,
        percent_x100=2500,
        object_id=11,
        body_bytes=4,
    )
    object_ref = ObjectReferenceMetadata(
        object_id=9,
        operation_id=42,
        object_version=2,
        offset=128,
        length=256,
        flags=0x01,
        metadata_bytes=2,
    )

    control_payload = bytearray(encode_runtime_control_metadata(MessageType.PROGRESS, progress, tail=b"step"))
    object_payload = bytearray(reference_runtime_object(object_ref, metadata_tail=b"md"))
    decoded_control = decode_runtime_control_metadata(MessageType.PROGRESS, control_payload)
    decoded_object = decode_runtime_object_metadata(MessageType.OBJECT_REF, object_payload)

    control_payload[-4:] = b"xxxx"
    object_payload[-2:] = b"xx"

    assert decoded_control.tail == b"step"
    assert decoded_object.tail == b"md"


def test_runtime_metadata_helpers_snapshot_mutable_tail_inputs() -> None:
    delta_tail = bytearray(b"md")
    delta_payload = bytearray(b"xxxx")
    body = bytearray(b"output")
    delta = ObjectDeltaMetadata(
        object_id=9,
        delta_sequence=2,
        region_offset=128,
        region_bytes=64,
        delta_bytes=4,
        flags=0x03,
        metadata_bytes=2,
    )
    partial = PartialResultMetadata(
        operation_id=4,
        result_sequence=5,
        object_id=9,
        delta_sequence=2,
        body_bytes=6,
        flags=0x03,
    )

    encoded_delta = delta_runtime_object(delta, metadata_tail=delta_tail, delta=delta_payload)
    encoded_partial = partial_result_runtime_object(partial, body=body)
    delta_tail[:] = b"xx"
    delta_payload[:] = b"yyyy"
    body[:] = b"mutate"

    assert decode_runtime_object_metadata(MessageType.OBJECT_DELTA, encoded_delta).tail == b"mdxxxx"
    assert decode_runtime_control_metadata(MessageType.PARTIAL_RESULT, encoded_partial).tail == b"output"


def test_runtime_metadata_helpers_reject_non_binary_snapshots() -> None:
    progress = ProgressMetadata(
        operation_id=42,
        progress_sequence=7,
        stage_code=3,
        percent_x100=2500,
        object_id=11,
        body_bytes=4,
    )

    with pytest.raises(TypeError, match="runtime tail must be bytes-like"):
        encode_runtime_control_metadata(MessageType.PROGRESS, progress, tail="text")
    with pytest.raises(TypeError, match="runtime payload must be bytes-like"):
        decode_runtime_control_metadata(MessageType.PROGRESS, object())


@pytest.mark.parametrize(
    ("message_type", "metadata", "tail"),
    [
        (
            MessageType.OBJECT_RELEASE,
            ObjectReleaseMetadata(
                object_id=9,
                operation_id=42,
                release_reason=ObjectReleaseReason.COMPLETED,
                source_role=RuntimeRole.CLIENT,
                flags=0x01,
                diagnostic_bytes=2,
            ),
            b"or",
        ),
        (
            MessageType.OBJECT_DELTA,
            ObjectDeltaMetadata(
                object_id=9,
                delta_sequence=2,
                region_offset=128,
                region_bytes=64,
                delta_bytes=4,
                flags=0x03,
                metadata_bytes=2,
            ),
            b"mdxxxx",
        ),
    ],
)
def test_runtime_object_metadata_roundtrips_remaining_preview4_shapes(
    message_type: MessageType,
    metadata: _FixedRuntimeMetadata,
    tail: bytes,
) -> None:
    payload = encode_runtime_object_metadata(message_type, metadata, tail=tail)

    decoded = decode_runtime_object_metadata(message_type, payload)

    assert decoded.metadata == metadata
    assert decoded.tail == tail


def test_runtime_object_reference_and_cache_reference_roundtrip() -> None:
    object_ref = ObjectReferenceMetadata(
        object_id=9,
        operation_id=42,
        object_version=2,
        offset=128,
        length=256,
        flags=0x01,
        metadata_bytes=0,
    )
    cache_ref = CacheReferenceMetadata(
        cache_namespace=7,
        cache_key_hi=0x11,
        cache_key_lo=0x22,
        profile_id=7,
        reuse_scope=CacheReuseScope.SESSION,
        lease_id=123,
        producer_trace_id=456,
        expiration_hint_ms=10_000,
        metadata_bytes=0,
        flags=0x01,
    )

    assert decode_runtime_object_metadata(
        MessageType.OBJECT_REF,
        encode_runtime_object_metadata(MessageType.OBJECT_REF, object_ref),
    ).metadata == object_ref
    assert decode_runtime_object_metadata(
        MessageType.CACHE_REFERENCE,
        encode_runtime_object_metadata(MessageType.CACHE_REFERENCE, cache_ref),
    ).metadata == cache_ref
    assert CacheReferenceMetadata.STRUCT.size == 56
    packed = cache_ref.pack()
    assert int.from_bytes(packed[0:4], "little") == 7
    assert int.from_bytes(packed[8:16], "little") == 0x11
    assert int.from_bytes(packed[16:24], "little") == 0x22
    assert packed[52:56] == b"\0" * 4


def test_cache_miss_remains_typed_metadata() -> None:
    metadata = CacheMissMetadata(
        cache_namespace=7,
        cache_key_hi=0x33,
        cache_key_lo=0x44,
        miss_reason=CacheMissReason.SCHEMA_MISMATCH,
        profile_id=5,
        diagnostic_bytes=4,
    )

    payload = encode_runtime_object_metadata(MessageType.CACHE_MISS, metadata, tail=b"diag")
    decoded = decode_runtime_object_metadata(MessageType.CACHE_MISS, payload)

    assert decoded.metadata == metadata
    assert decoded.tail == b"diag"
    assert CacheMissMetadata.STRUCT.size == 32
    assert int.from_bytes(payload[0:4], "little") == 7
    assert int.from_bytes(payload[8:16], "little") == 0x33
    assert int.from_bytes(payload[16:24], "little") == 0x44


def test_runtime_metadata_helpers_reject_invalid_payloads_and_reserved_bits() -> None:
    with pytest.raises(ValueError, match="PING does not carry preview4 runtime metadata"):
        decode_runtime_control_metadata(MessageType.PING, b"")
    with pytest.raises(ValueError, match="expected at least"):
        decode_runtime_object_metadata(MessageType.OBJECT_REF, b"\0")
    with pytest.raises(TypeError, match="OBJECT_REF requires ObjectReferenceMetadata"):
        encode_runtime_object_metadata(
            MessageType.OBJECT_REF,
            CacheMissMetadata(
                cache_namespace=0,
                cache_key_hi=0,
                cache_key_lo=0,
                miss_reason=CacheMissReason.UNKNOWN,
                profile_id=0,
                diagnostic_bytes=0,
            ),
        )
    with pytest.raises(ValueError, match="reserved bits"):
        PressureMetadata(
            scope_id=1,
            credit_window=1,
            pressure_level=1,
            pressure_reason=1,
            retry_after_ms=1,
            flags=0x04,
        ).pack()
    with pytest.raises(ValueError, match="must be zero"):
        CacheMissMetadata.unpack(CacheMissMetadata.STRUCT.pack(1, 2, int(CacheMissReason.UNKNOWN), 3, 4, 5, 1))
    with pytest.raises(ValueError, match="must be zero"):
        CacheReferenceMetadata.unpack(
            CacheReferenceMetadata.STRUCT.pack(1, 2, int(CacheReuseScope.SESSION), 3, 4, 5, 6, 7, 8, 0, 1)
        )
    with pytest.raises(ValueError, match="progress.percent_x100"):
        ProgressMetadata(
            operation_id=1,
            progress_sequence=1,
            stage_code=1,
            percent_x100=10_001,
            object_id=1,
            body_bytes=0,
        ).pack()
    with pytest.raises(ValueError, match="progress.percent_x100"):
        ProgressMetadata(
            operation_id=1,
            progress_sequence=1,
            stage_code=1,
            percent_x100=-1,
            object_id=1,
            body_bytes=0,
        ).pack()
    with pytest.raises(NotImplementedError):
        _FixedRuntimeMetadata().pack()
    with pytest.raises(NotImplementedError):
        _FixedRuntimeMetadata._from_tuple(())


def test_result_drop_reason_code_preserves_private_values() -> None:
    metadata = ResultDropReasonMetadata(
        operation_id=1,
        result_sequence=2,
        drop_reason_code=0x8000,
        source_role=RuntimeRole.SERVER,
        flags=0,
        diagnostic_bytes=0,
    )

    decoded = decode_runtime_control_metadata(
        MessageType.RESULT_DROP_REASON,
        encode_runtime_control_metadata(MessageType.RESULT_DROP_REASON, metadata),
    )

    assert decoded.metadata.drop_reason_code == 0x8000


def test_websocket_binary_frame_roundtrip_and_batch_decode() -> None:
    header = RuntimeFrameHeader(
        message_type=MessageType.PROGRESS,
        flags=HeaderFlags.ACK_REQUIRED,
        session_id=3,
        frame_id=9,
        view_id=1,
        route_id=2,
        trace_id=99,
    )

    first = encode_websocket_binary_frame(header, metadata=b"meta", body=b"body")
    second = encode_websocket_binary_frame(
        RuntimeFrameHeader(message_type=MessageType.CACHE_MISS, session_id=3),
        metadata=b"cache",
    )
    decoded = decode_websocket_binary_frame(first)
    batch = decode_websocket_binary_frame_batch(first + second)

    assert decoded.header == header
    assert first[:40].hex() == "4e4e5250010037280100000004000000040000000300000009000000010002006300000000000000"
    assert decoded.metadata == b"meta"
    assert decoded.body == b"body"
    assert [entry.header.message_type for entry in batch] == [MessageType.PROGRESS, MessageType.CACHE_MISS]


def test_runtime_frame_header_contains_exactly_the_caller_controlled_wire_fields() -> None:
    assert [field.name for field in fields(RuntimeFrameHeader)] == [
        "message_type",
        "flags",
        "session_id",
        "frame_id",
        "view_id",
        "route_id",
        "trace_id",
        "version_major",
        "wire_format",
    ]


def test_websocket_binary_frame_rejects_incomplete_inputs() -> None:
    header = RuntimeFrameHeader(message_type=MessageType.PROGRESS, session_id=3)
    frame = encode_websocket_binary_frame(header, metadata=b"m")

    with pytest.raises(ValueError, match="incomplete WebSocket binary frame header"):
        decode_websocket_binary_frame(b"\0")
    with pytest.raises(ValueError, match="expected"):
        decode_websocket_binary_frame(frame + b"x")
    with pytest.raises(ValueError, match="limit must be non-negative"):
        decode_websocket_binary_frame_batch(frame, limit=-1)
    with pytest.raises(ValueError, match="incomplete WebSocket binary frame in batch"):
        decode_websocket_binary_frame_batch(frame[:1])
    with pytest.raises(ValueError, match="incomplete WebSocket binary frame in batch"):
        decode_websocket_binary_frame_batch(frame[:-1])

    assert decode_websocket_binary_frame_batch(frame, limit=1) == [decode_websocket_binary_frame(frame)]
    with pytest.raises(ValueError, match="contains more than 1 frames"):
        decode_websocket_binary_frame_batch(frame + frame, limit=1)


def test_websocket_binary_frame_rejects_text_frames_and_non_contiguous_views() -> None:
    header = RuntimeFrameHeader(message_type=MessageType.PROGRESS, session_id=3)
    frame = encode_websocket_binary_frame(header, metadata=bytearray(b"m"), body=memoryview(b"body"))
    non_contiguous = memoryview(bytearray(b"abcdef"))[::2]

    assert decode_websocket_binary_frame(memoryview(frame)).body == b"body"
    assert decode_websocket_binary_frame_batch(bytearray(frame))[0].metadata == b"m"
    with pytest.raises(TypeError, match="not text"):
        encode_websocket_binary_frame(header, metadata="text")
    with pytest.raises(TypeError, match="not text"):
        decode_websocket_binary_frame("text")
    with pytest.raises(TypeError, match="bytes-like"):
        decode_websocket_binary_frame(object())
    with pytest.raises(ValueError, match="must be contiguous"):
        encode_websocket_binary_frame(header, metadata=non_contiguous)
    with pytest.raises(ValueError, match="must be contiguous"):
        decode_websocket_binary_frame_batch(non_contiguous)


def test_preview4_client_dial_policy_maps_ipc_and_websocket() -> None:
    assert ClientDialPolicy(selected_transport_id=TransportId.IPC).to_client_hello_transport_policy() is not None
    assert ClientDialPolicy(
        selected_transport_id=TransportId.IPC,
    ).to_client_hello_transport_policy().transport_policy is TransportPolicy.PREFER_IPC
    assert ClientDialPolicy(
        selected_transport_id=TransportId.WEBSOCKET,
    ).to_client_hello_transport_policy().transport_policy is TransportPolicy.PREFER_WEBSOCKET
    assert ClientDialPolicy(
        forced_transport_id=TransportId.IPC,
    ).to_client_hello_transport_policy().transport_policy is TransportPolicy.FORCE_IPC
    assert ClientDialPolicy(
        forced_transport_id=TransportId.WEBSOCKET,
    ).to_client_hello_transport_policy().transport_policy is TransportPolicy.FORCE_WEBSOCKET
