import struct

import pytest

from nnrp.core.messages import (
    CACHE_ACK_METADATA_LENGTH,
    CACHE_INVALIDATE_METADATA_LENGTH,
    CACHE_PUT_METADATA_LENGTH,
    CLIENT_HELLO_LOSS_TOLERANCE_EXTENSION,
    CLIENT_HELLO_METADATA_LENGTH,
    CLIENT_HELLO_PAYLOAD_CAPABILITIES_EXTENSION,
    CLIENT_HELLO_TRANSPORT_POLICY_EXTENSION,
    CONTROL_EXTENSION_ALIGNMENT,
    CONTROL_EXTENSION_HEADER_LENGTH,
    ERROR_METADATA_LENGTH,
    FLOW_UPDATE_METADATA_LENGTH,
    RESULT_HINT_METADATA_LENGTH,
    SERVER_HELLO_ACK_LOSS_TOLERANCE_EXTENSION,
    SERVER_HELLO_ACK_METADATA_LENGTH,
    SERVER_HELLO_ACK_PAYLOAD_CAPABILITIES_EXTENSION,
    SERVER_HELLO_ACK_TRANSPORT_POLICY_EXTENSION,
    SESSION_MIGRATE_ACK_METADATA_LENGTH,
    SESSION_MIGRATE_METADATA_LENGTH,
    SESSION_PATCH_ACK_METADATA_LENGTH,
    SESSION_PATCH_METADATA_LENGTH,
    TENSOR_PROFILE_CACHE_OBJECT_BITMAP,
    TRANSPORT_PROBE_ACK_METADATA_LENGTH,
    TRANSPORT_PROBE_METADATA_LENGTH,
    CacheAckMetadata,
    CacheAckStatus,
    CacheInvalidateMetadata,
    CacheInvalidateScope,
    CacheObjectKind,
    CachePutFlags,
    CachePutMetadata,
    ClientHelloLossToleranceExtension,
    ClientHelloMetadata,
    ClientHelloPayloadCapabilitiesExtension,
    ClientHelloTransportPolicyExtension,
    ControlExtensionEntry,
    ControlExtensionFlags,
    ErrorMetadata,
    ErrorScope,
    FlowUpdateBackpressureLevel,
    FlowUpdateFlags,
    FlowUpdateMetadata,
    FlowUpdateReason,
    FlowUpdateScopeKind,
    LossTolerance,
    PayloadKind,
    ResultHintBudgetPolicy,
    ResultHintCongestionState,
    ResultHintMetadata,
    ResultHintReason,
    ServerHelloAckLossToleranceExtension,
    ServerHelloAckMetadata,
    ServerHelloAckPayloadCapabilitiesExtension,
    ServerHelloAckTransportPolicyExtension,
    SessionMigrateAckMetadata,
    SessionMigrateMetadata,
    SessionPatchAckMetadata,
    SessionPatchAckStatus,
    SessionPatchField,
    SessionPatchMetadata,
    SessionPatchRejectReason,
    TensorProfilePatchAckBlock,
    TensorProfilePatchBlock,
    TransportId,
    TransportPolicy,
    TransportProbeAckMetadata,
    TransportProbeMetadata,
    build_cache_object_bitmap,
    build_client_hello_loss_tolerance_extension,
    build_client_hello_payload_capabilities_extension,
    build_client_hello_transport_policy_extension,
    build_server_hello_ack_loss_tolerance_extension,
    build_server_hello_ack_payload_capabilities_extension,
    build_server_hello_ack_transport_policy_extension,
    pack_control_extension_block,
    pack_session_patch_ack_body,
    pack_session_patch_body,
    parse_client_hello_loss_tolerance_extension,
    parse_client_hello_payload_capabilities_extension,
    parse_client_hello_transport_policy_extension,
    parse_server_hello_ack_loss_tolerance_extension,
    parse_server_hello_ack_payload_capabilities_extension,
    parse_server_hello_ack_transport_policy_extension,
    unpack_control_extension_block,
    unpack_session_patch_ack_body,
    unpack_session_patch_body,
)


def test_flow_update_metadata_roundtrip() -> None:
    metadata = FlowUpdateMetadata(
        scope_kind=FlowUpdateScopeKind.SESSION,
        update_reason=FlowUpdateReason.CONGESTION,
        backpressure_level=FlowUpdateBackpressureLevel.SOFT,
        session_credit=2,
        retry_after_ms=33,
        credit_epoch=7,
        flags=FlowUpdateFlags.CREDIT_VALID | FlowUpdateFlags.RETRY_AFTER_VALID,
    )

    payload = metadata.pack()

    assert len(payload) == FLOW_UPDATE_METADATA_LENGTH
    assert FlowUpdateMetadata.unpack(payload) == metadata


def test_flow_update_metadata_rejects_invalid_scope_payload() -> None:
    payload = FlowUpdateMetadata.STRUCT.pack(
        int(FlowUpdateScopeKind.SESSION),
        int(FlowUpdateReason.REDUCE),
        int(FlowUpdateBackpressureLevel.HARD),
        0,
        1,
        3,
        0,
        0,
        0,
        0,
        11,
        int(FlowUpdateFlags.CREDIT_VALID),
    )

    with pytest.raises(ValueError, match="session-scope FLOW_UPDATE"):
        FlowUpdateMetadata.unpack(payload)


def test_flow_update_metadata_rejects_unknown_flags() -> None:
    payload = FlowUpdateMetadata.STRUCT.pack(
        int(FlowUpdateScopeKind.CONNECTION),
        int(FlowUpdateReason.GRANT),
        int(FlowUpdateBackpressureLevel.NONE),
        0,
        2,
        0,
        0,
        0,
        0,
        0,
        0,
        0x00000010,
    )

    with pytest.raises(ValueError, match="unknown FLOW_UPDATE flags"):
        FlowUpdateMetadata.unpack(payload)


def test_flow_update_metadata_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError, match="expected 32 bytes, got 31"):
        FlowUpdateMetadata.unpack(b"\x00" * 31)


def test_flow_update_metadata_rejects_retry_after_without_flag() -> None:
    metadata = FlowUpdateMetadata(
        scope_kind=FlowUpdateScopeKind.CONNECTION,
        update_reason=FlowUpdateReason.PAUSE,
        backpressure_level=FlowUpdateBackpressureLevel.HARD,
        connection_credit=0,
        retry_after_ms=12,
    )

    with pytest.raises(ValueError, match="retry_after_ms requires RETRY_AFTER_VALID"):
        metadata.pack()


def test_result_hint_metadata_roundtrip() -> None:
    metadata = ResultHintMetadata(
        applied_budget_policy=ResultHintBudgetPolicy.PARTIAL,
        congestion_state=ResultHintCongestionState.ELEVATED,
        reason=ResultHintReason.SERVER_BUSY,
        retry_after_ms=20,
    )

    payload = metadata.pack()

    assert len(payload) == RESULT_HINT_METADATA_LENGTH
    assert ResultHintMetadata.unpack(payload) == metadata


def test_result_hint_metadata_rejects_unknown_reason() -> None:
    payload = ResultHintMetadata.STRUCT.pack(
        int(ResultHintBudgetPolicy.FULL),
        int(ResultHintCongestionState.STEADY),
        99,
        0,
    )

    with pytest.raises(ValueError):
        ResultHintMetadata.unpack(payload)


def test_result_hint_metadata_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError, match="expected 16 bytes, got 15"):
        ResultHintMetadata.unpack(b"\x00" * 15)


def test_client_hello_metadata_roundtrip() -> None:
    metadata = ClientHelloMetadata(
        min_version_major=1,
        max_version_major=1,
        supported_wire_format_bitmap=0x0001,
        supported_profile_bitmap=0x0001,
        supported_payload_kind_bitmap=0x0001,
        supported_codec_bitmap=0x0007,
        supported_compression_bitmap=0x0003,
        supported_dtype_bitmap=0x001F,
        supported_layout_bitmap=0x0003,
        cache_digest_bitmap=0x0001,
        cache_object_bitmap=0x0007,
        cache_namespace_count=4,
        max_lane_count=2,
        max_cache_entries=256,
        max_cache_bytes=8 * 1024 * 1024,
        target_cadence_x100=6000,
        latency_budget_ms=100,
        quality_tier=2,
        degrade_policy=2,
        requested_session_id=0,
        auth_bytes=96,
        control_extension_bytes=0,
    )

    payload = metadata.pack()

    assert len(payload) == CLIENT_HELLO_METADATA_LENGTH
    assert ClientHelloMetadata.unpack(payload) == metadata


def test_server_hello_ack_roundtrip() -> None:
    metadata = ServerHelloAckMetadata(
        selected_version_major=1,
        selected_wire_format=0,
        auth_status=0,
        session_id=42,
        accepted_profile_bitmap=0x0001,
        accepted_payload_kind_bitmap=0x0001,
        accepted_codec_bitmap=0x0003,
        accepted_compression_bitmap=0x0003,
        accepted_dtype_bitmap=0x0007,
        accepted_layout_bitmap=0x0001,
        cache_digest_bitmap=0x0001,
        cache_object_bitmap=0x0007,
        max_cache_entries=512,
        max_cache_bytes=16 * 1024 * 1024,
        max_lane_count=2,
        max_concurrent_frames=2,
        target_cadence_x100=6000,
        latency_budget_ms=100,
        quality_tier=2,
        degrade_policy=2,
        max_body_bytes=32 * 1024 * 1024,
        token_ttl_ms=300000,
        retry_after_ms=0,
        control_extension_bytes=0,
        server_flags=0x00000001,
    )

    payload = metadata.pack()

    assert len(payload) == SERVER_HELLO_ACK_METADATA_LENGTH
    assert ServerHelloAckMetadata.unpack(payload) == metadata


def test_transport_probe_metadata_roundtrip() -> None:
    metadata = TransportProbeMetadata(
        probe_id=17,
        probe_payload_bytes=32768,
        client_send_ts_us=123456789,
    )

    payload = metadata.pack()

    assert len(payload) == TRANSPORT_PROBE_METADATA_LENGTH
    assert TransportProbeMetadata.unpack(payload) == metadata


def test_transport_probe_ack_metadata_roundtrip() -> None:
    metadata = TransportProbeAckMetadata(
        probe_id=17,
        reserved=0,
        server_recv_ts_us=223456789,
    )

    payload = metadata.pack()

    assert len(payload) == TRANSPORT_PROBE_ACK_METADATA_LENGTH
    assert TransportProbeAckMetadata.unpack(payload) == metadata


def test_session_migrate_metadata_roundtrip() -> None:
    metadata = SessionMigrateMetadata(
        old_transport_id=TransportId.QUIC,
        new_transport_id=TransportId.TCP,
        last_result_frame_id=44,
        client_migrate_ts_us=3000,
    )

    payload = metadata.pack()

    assert len(payload) == SESSION_MIGRATE_METADATA_LENGTH
    assert SessionMigrateMetadata.unpack(payload) == metadata


def test_session_migrate_ack_metadata_roundtrip() -> None:
    metadata = SessionMigrateAckMetadata(
        accept_code=0,
        resume_from_frame_id=45,
        grace_window_ms=250,
        server_migrate_ts_us=4000,
    )

    payload = metadata.pack()

    assert len(payload) == SESSION_MIGRATE_ACK_METADATA_LENGTH
    assert SessionMigrateAckMetadata.unpack(payload) == metadata


def test_error_metadata_roundtrip() -> None:
    metadata = ErrorMetadata(
        error_code=0x000B,
        error_scope=ErrorScope.SESSION,
        is_fatal=0,
        retry_after_ms=500,
        related_session_id=42,
        related_frame_id=0,
        related_view_id=0,
        diagnostic_bytes=24,
    )

    payload = metadata.pack()

    assert len(payload) == ERROR_METADATA_LENGTH
    decoded = ErrorMetadata.unpack(payload)
    assert decoded == metadata
    assert decoded.error_scope is ErrorScope.SESSION


def test_session_patch_messages_roundtrip() -> None:
    patch_block = TensorProfilePatchBlock(
        min_width=640,
        min_height=360,
        max_width=1920,
        max_height=1080,
    )
    ack_block = TensorProfilePatchAckBlock(
        min_width=640,
        min_height=360,
        max_width=1280,
        max_height=720,
    )
    patch_metadata = SessionPatchMetadata(
        profile_id=0,
        patch_mask=(
            SessionPatchField.TARGET_CADENCE
            | SessionPatchField.QUALITY_TIER
            | SessionPatchField.ACTIVE_LANE_MASK
            | SessionPatchField.PREFERRED_CODEC
            | SessionPatchField.PROFILE_PATCH
        ),
        target_cadence_x100=9000,
        quality_tier=2,
        degrade_policy=0,
        active_lane_mask=0x0000000000000003,
        preferred_codec_bitmap=0x00000005,
        preferred_compression_bitmap=0,
        profile_patch_bytes=len(pack_session_patch_body(profile_patch_block=patch_block)),
    )
    ack_metadata = SessionPatchAckMetadata(
        ack_status=SessionPatchAckStatus.PARTIALLY_APPLIED,
        reject_reason=SessionPatchRejectReason.UNSUPPORTED_STRATEGY,
        applied_patch_mask=(
            SessionPatchField.TARGET_CADENCE
            | SessionPatchField.QUALITY_TIER
            | SessionPatchField.ACTIVE_LANE_MASK
            | SessionPatchField.PROFILE_PATCH
        ),
        rejected_patch_mask=SessionPatchField.PREFERRED_CODEC,
        retry_after_ms=0,
        effective_profile_id=1,
        effective_target_cadence_x100=9000,
        effective_quality_tier=2,
        effective_degrade_policy=2,
        effective_lane_mask=0x0000000000000003,
        effective_codec_bitmap=0x00000001,
        effective_compression_bitmap=0x00000003,
        profile_patch_ack_bytes=len(pack_session_patch_ack_body(profile_patch_ack_block=ack_block)),
    )

    patch_payload = patch_metadata.pack()
    ack_payload = ack_metadata.pack()
    patch_body = pack_session_patch_body(profile_patch_block=patch_block)
    ack_body = pack_session_patch_ack_body(profile_patch_ack_block=ack_block)

    assert len(patch_payload) == SESSION_PATCH_METADATA_LENGTH
    assert len(ack_payload) == SESSION_PATCH_ACK_METADATA_LENGTH
    decoded_patch = SessionPatchMetadata.unpack(patch_payload)
    decoded_ack = SessionPatchAckMetadata.unpack(ack_payload)
    decoded_patch_body = unpack_session_patch_body(
        patch_body,
        profile_patch_bytes=decoded_patch.profile_patch_bytes,
    )
    decoded_ack_body = unpack_session_patch_ack_body(
        ack_body,
        profile_patch_ack_bytes=decoded_ack.profile_patch_ack_bytes,
    )
    assert decoded_patch == patch_metadata
    assert decoded_ack == ack_metadata
    assert decoded_patch_body == patch_block
    assert decoded_ack_body == ack_block
    assert decoded_patch.patch_mask & SessionPatchField.ACTIVE_LANE_MASK
    assert decoded_patch.patch_mask & SessionPatchField.PROFILE_PATCH
    assert decoded_ack.ack_status is SessionPatchAckStatus.PARTIALLY_APPLIED
    assert decoded_ack.reject_reason is SessionPatchRejectReason.UNSUPPORTED_STRATEGY


def test_session_patch_body_length_validation() -> None:
    with pytest.raises(ValueError, match="expected empty SESSION_PATCH body"):
        unpack_session_patch_body(b"\x00", profile_patch_bytes=0)

    with pytest.raises(ValueError, match="must be 0 or 16"):
        unpack_session_patch_body(b"", profile_patch_bytes=4)

    with pytest.raises(ValueError, match="expected 16 SESSION_PATCH_ACK body bytes"):
        unpack_session_patch_ack_body(b"\x00" * 8, profile_patch_ack_bytes=16)


def test_cache_messages_roundtrip() -> None:
    put_metadata = CachePutMetadata(
        cache_namespace=1,
        cache_key_hi=0x01020304,
        cache_key_lo=0x05060708,
        object_kind=CacheObjectKind.TILE_INDEX_BLOCK,
        ttl_ms=15000,
        object_bytes=2048,
        codec_bitmap=0x0003,
        flags=CachePutFlags.PINNED | CachePutFlags.REUSABLE,
    )
    ack_metadata = CacheAckMetadata(
        cache_namespace=1,
        cache_key_hi=0x01020304,
        cache_key_lo=0x05060708,
        status=CacheAckStatus.ACCEPTED,
        accepted_ttl_ms=15000,
        max_object_bytes=8192,
        detail_code=0,
    )
    invalidate_metadata = CacheInvalidateMetadata(
        invalidate_scope=CacheInvalidateScope.OBJECT_KEY,
        cache_namespace=1,
        cache_key_hi=0x01020304,
        cache_key_lo=0x05060708,
        reason_code=2,
    )

    put_payload = put_metadata.pack()
    ack_payload = ack_metadata.pack()
    invalidate_payload = invalidate_metadata.pack()

    assert len(put_payload) == CACHE_PUT_METADATA_LENGTH
    assert len(ack_payload) == CACHE_ACK_METADATA_LENGTH
    assert len(invalidate_payload) == CACHE_INVALIDATE_METADATA_LENGTH
    assert CachePutMetadata.unpack(put_payload) == put_metadata
    assert CacheAckMetadata.unpack(ack_payload) == ack_metadata
    assert CacheInvalidateMetadata.unpack(invalidate_payload) == invalidate_metadata


def test_cache_put_metadata_rejects_unknown_object_kind() -> None:
    with pytest.raises(ValueError, match="65535 is not a valid CacheObjectKind"):
        CachePutMetadata(
            cache_namespace=1,
            cache_key_hi=0,
            cache_key_lo=0,
            object_kind=0xFFFF,
            ttl_ms=1000,
            object_bytes=16,
            codec_bitmap=0,
        )


def test_cache_invalidate_metadata_rejects_unknown_scope() -> None:
    with pytest.raises(ValueError, match="255 is not a valid CacheInvalidateScope"):
        CacheInvalidateMetadata(
            invalidate_scope=0xFF,
            cache_namespace=1,
            cache_key_hi=0,
            cache_key_lo=0,
            reason_code=0,
        )


def test_tensor_profile_cache_object_bitmap_stays_aligned() -> None:
    assert TENSOR_PROFILE_CACHE_OBJECT_BITMAP == 0x0007
    assert TENSOR_PROFILE_CACHE_OBJECT_BITMAP == build_cache_object_bitmap(
        CacheObjectKind.CAMERA_BLOCK,
        CacheObjectKind.TILE_INDEX_BLOCK,
        CacheObjectKind.TENSOR_SECTION_TABLE,
    )


def test_control_extension_block_roundtrip_and_alignment() -> None:
    entries = (
        ControlExtensionEntry(ext_type=0x0001, payload=b"abc"),
        ControlExtensionEntry(
            ext_type=0x4001,
            ext_flags=ControlExtensionFlags.CRITICAL,
            payload=b"current",
        ),
    )

    payload = pack_control_extension_block(entries)
    decoded = unpack_control_extension_block(payload)

    assert decoded == entries
    assert len(payload) == (CONTROL_EXTENSION_HEADER_LENGTH + 3 + 5) + (CONTROL_EXTENSION_HEADER_LENGTH + 8)
    assert len(payload) % CONTROL_EXTENSION_ALIGNMENT == 0


def test_control_extension_block_rejects_truncated_payload() -> None:
    payload = pack_control_extension_block((ControlExtensionEntry(ext_type=0x0002, payload=b"abcd"),))

    try:
        unpack_control_extension_block(payload[:-1])
    except ValueError as exc:
        assert "truncated control extension" in str(exc)
    else:
        raise AssertionError("expected truncated control extension payload to fail")


def test_control_extension_block_rejects_non_zero_padding() -> None:
    payload = bytearray(pack_control_extension_block((ControlExtensionEntry(ext_type=0x0003, payload=b"abc"),)))
    payload[-1] = 0x7F

    try:
        unpack_control_extension_block(bytes(payload))
    except ValueError as exc:
        assert str(exc) == "control extension padding must be zero"
    else:
        raise AssertionError("expected non-zero control extension padding to fail")


def test_control_extension_block_ignores_unknown_optional_extensions() -> None:
    payload = pack_control_extension_block(
        (
            ControlExtensionEntry(ext_type=0x0001, payload=b"known"),
            ControlExtensionEntry(ext_type=0x8001, payload=b"ignored"),
        )
    )

    decoded = unpack_control_extension_block(payload, known_types={0x0001})

    assert decoded == (ControlExtensionEntry(ext_type=0x0001, payload=b"known"),)


def test_control_extension_block_rejects_unknown_critical_extensions() -> None:
    payload = pack_control_extension_block(
        (
            ControlExtensionEntry(
                ext_type=0x8002,
                ext_flags=ControlExtensionFlags.CRITICAL,
                payload=b"fatal",
            ),
        )
    )

    try:
        unpack_control_extension_block(payload, known_types={0x0001})
    except ValueError as exc:
        assert str(exc) == "unknown critical control extension type: 0x8002"
    else:
        raise AssertionError("expected unknown critical control extension to fail")


def test_client_hello_transport_policy_extension_roundtrip() -> None:
    extension = ClientHelloTransportPolicyExtension(
        transport_policy=TransportPolicy.PREFER_TCP,
        preferred_transport_id=TransportId.TCP,
    )

    entry = build_client_hello_transport_policy_extension(extension)

    assert entry.ext_type == CLIENT_HELLO_TRANSPORT_POLICY_EXTENSION
    assert parse_client_hello_transport_policy_extension(entry) == extension


def test_server_hello_ack_transport_policy_extension_roundtrip() -> None:
    extension = ServerHelloAckTransportPolicyExtension(
        transport_policy=TransportPolicy.PREFER_QUIC,
        accepted_transport_policy=TransportPolicy.FORCE_TCP,
        active_transport_id=TransportId.TCP,
    )

    entry = build_server_hello_ack_transport_policy_extension(extension)

    assert entry.ext_type == SERVER_HELLO_ACK_TRANSPORT_POLICY_EXTENSION
    assert parse_server_hello_ack_transport_policy_extension(entry) == extension


def test_client_hello_loss_tolerance_extension_roundtrip() -> None:
    extension = ClientHelloLossToleranceExtension(
        session_loss_tolerance=LossTolerance.LOW_LATENCY,
    )

    entry = build_client_hello_loss_tolerance_extension(extension)

    assert entry.ext_type == CLIENT_HELLO_LOSS_TOLERANCE_EXTENSION
    assert parse_client_hello_loss_tolerance_extension(entry) == extension


def test_server_hello_ack_loss_tolerance_extension_roundtrip() -> None:
    extension = ServerHelloAckLossToleranceExtension(
        accepted_loss_tolerance=LossTolerance.BEST_EFFORT,
    )

    entry = build_server_hello_ack_loss_tolerance_extension(extension)

    assert entry.ext_type == SERVER_HELLO_ACK_LOSS_TOLERANCE_EXTENSION
    assert parse_server_hello_ack_loss_tolerance_extension(entry) == extension


def test_client_hello_payload_capabilities_extension_roundtrip() -> None:
    extension = ClientHelloPayloadCapabilitiesExtension(
        payload_kind_bitmap=PayloadKind.TENSOR | PayloadKind.TOOL_DELTA,
        critical_extension_frame_bitmap=0,
    )

    entry = build_client_hello_payload_capabilities_extension(extension)

    assert entry.ext_type == CLIENT_HELLO_PAYLOAD_CAPABILITIES_EXTENSION
    assert parse_client_hello_payload_capabilities_extension(entry) == extension


def test_server_hello_ack_payload_capabilities_extension_roundtrip() -> None:
    extension = ServerHelloAckPayloadCapabilitiesExtension(
        accepted_payload_kind_bitmap=PayloadKind.TENSOR | PayloadKind.STRUCTURED_EVENT,
        accepted_critical_extension_frame_bitmap=0,
    )

    entry = build_server_hello_ack_payload_capabilities_extension(extension)

    assert entry.ext_type == SERVER_HELLO_ACK_PAYLOAD_CAPABILITIES_EXTENSION
    assert parse_server_hello_ack_payload_capabilities_extension(entry) == extension


def test_transport_policy_extensions_roundtrip_through_control_block() -> None:
    entries = (
        build_client_hello_transport_policy_extension(
            ClientHelloTransportPolicyExtension(
                transport_policy=TransportPolicy.AUTO,
                preferred_transport_id=TransportId.QUIC,
            )
        ),
        build_server_hello_ack_transport_policy_extension(
            ServerHelloAckTransportPolicyExtension(
                transport_policy=TransportPolicy.PREFER_QUIC,
                accepted_transport_policy=TransportPolicy.PREFER_TCP,
                active_transport_id=TransportId.TCP,
            )
        ),
    )

    payload = pack_control_extension_block(entries)
    decoded = unpack_control_extension_block(
        payload,
        known_types={
            CLIENT_HELLO_TRANSPORT_POLICY_EXTENSION,
            SERVER_HELLO_ACK_TRANSPORT_POLICY_EXTENSION,
        },
    )

    assert decoded == entries
    assert parse_client_hello_transport_policy_extension(decoded[0]).preferred_transport_id is TransportId.QUIC
    assert parse_server_hello_ack_transport_policy_extension(decoded[1]).active_transport_id is TransportId.TCP


def test_loss_tolerance_extensions_roundtrip_through_control_block() -> None:
    entries = (
        build_client_hello_loss_tolerance_extension(
            ClientHelloLossToleranceExtension(
                session_loss_tolerance=LossTolerance.FIRE_AND_FORGET,
            )
        ),
        build_server_hello_ack_loss_tolerance_extension(
            ServerHelloAckLossToleranceExtension(
                accepted_loss_tolerance=LossTolerance.LOW_LATENCY,
            )
        ),
    )

    payload = pack_control_extension_block(entries)
    decoded = unpack_control_extension_block(
        payload,
        known_types={
            CLIENT_HELLO_LOSS_TOLERANCE_EXTENSION,
            SERVER_HELLO_ACK_LOSS_TOLERANCE_EXTENSION,
        },
    )

    assert decoded == entries
    assert (
        parse_client_hello_loss_tolerance_extension(decoded[0]).session_loss_tolerance is LossTolerance.FIRE_AND_FORGET
    )
    assert (
        parse_server_hello_ack_loss_tolerance_extension(decoded[1]).accepted_loss_tolerance is LossTolerance.LOW_LATENCY
    )


def test_payload_capabilities_extensions_roundtrip_through_control_block() -> None:
    entries = (
        build_client_hello_payload_capabilities_extension(
            ClientHelloPayloadCapabilitiesExtension(
                payload_kind_bitmap=PayloadKind.TENSOR | PayloadKind.AUDIO_CHUNK,
            )
        ),
        build_server_hello_ack_payload_capabilities_extension(
            ServerHelloAckPayloadCapabilitiesExtension(
                accepted_payload_kind_bitmap=PayloadKind.TENSOR | PayloadKind.VIDEO_CHUNK,
            )
        ),
    )

    payload = pack_control_extension_block(entries)
    decoded = unpack_control_extension_block(
        payload,
        known_types={
            CLIENT_HELLO_PAYLOAD_CAPABILITIES_EXTENSION,
            SERVER_HELLO_ACK_PAYLOAD_CAPABILITIES_EXTENSION,
        },
    )

    assert decoded == entries
    assert parse_client_hello_payload_capabilities_extension(decoded[0]).payload_kind_bitmap == (
        PayloadKind.TENSOR | PayloadKind.AUDIO_CHUNK
    )
    assert parse_server_hello_ack_payload_capabilities_extension(decoded[1]).accepted_payload_kind_bitmap == (
        PayloadKind.TENSOR | PayloadKind.VIDEO_CHUNK
    )


def test_loss_tolerance_extension_rejects_non_zero_reserved_fields() -> None:
    payload = bytes([int(LossTolerance.STRICT), 1, 0, 0, 0, 0, 0, 0])

    with pytest.raises(ValueError, match="reserved fields must be zero"):
        ClientHelloLossToleranceExtension.unpack(payload)


def test_loss_tolerance_extension_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError, match="expected 8 bytes, got 7"):
        ClientHelloLossToleranceExtension.unpack(b"\x00" * 7)


def test_payload_capabilities_extension_rejects_unknown_payload_kind_bits() -> None:
    payload = struct.pack("<II", 0x00000080, 0)

    with pytest.raises(ValueError, match="unknown payload kind bits"):
        ClientHelloPayloadCapabilitiesExtension.unpack(payload)


def test_payload_capabilities_extension_rejects_non_zero_critical_bitmap() -> None:
    payload = struct.pack("<II", int(PayloadKind.TENSOR), 1)

    with pytest.raises(ValueError, match="critical extension frame bitmap must be zero"):
        ClientHelloPayloadCapabilitiesExtension.unpack(payload)


def test_payload_capabilities_extension_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError, match="expected 8 bytes, got 7"):
        ClientHelloPayloadCapabilitiesExtension.unpack(b"\x00" * 7)


def test_transport_policy_extension_rejects_wrong_entry_type() -> None:
    entry = build_client_hello_transport_policy_extension(
        ClientHelloTransportPolicyExtension(
            transport_policy=TransportPolicy.FORCE_QUIC,
            preferred_transport_id=TransportId.QUIC,
        )
    )

    try:
        parse_server_hello_ack_transport_policy_extension(entry)
    except ValueError as exc:
        assert "unexpected server hello ack transport extension type" in str(exc)
    else:
        raise AssertionError("expected mismatched transport extension type to fail")


def test_transport_policy_extension_rejects_unknown_transport_id() -> None:
    with pytest.raises(ValueError, match="3 is not a valid TransportId"):
        ClientHelloTransportPolicyExtension(
            transport_policy=TransportPolicy.PREFER_QUIC,
            preferred_transport_id=3,
        ).pack()


def test_session_migrate_metadata_rejects_unspecified_transport_id() -> None:
    with pytest.raises(ValueError, match="transport id must not be unspecified"):
        SessionMigrateMetadata(
            old_transport_id=TransportId.UNSPECIFIED,
            new_transport_id=TransportId.TCP,
            last_result_frame_id=1,
            client_migrate_ts_us=2,
        ).pack()


def test_payload_capabilities_extension_rejects_wrong_entry_type() -> None:
    entry = build_client_hello_payload_capabilities_extension(
        ClientHelloPayloadCapabilitiesExtension(
            payload_kind_bitmap=PayloadKind.TENSOR,
        )
    )

    try:
        parse_server_hello_ack_payload_capabilities_extension(entry)
    except ValueError as exc:
        assert "unexpected server hello ack payload capabilities extension type" in str(exc)
    else:
        raise AssertionError("expected mismatched payload capabilities extension type to fail")
