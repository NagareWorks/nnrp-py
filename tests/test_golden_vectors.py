from nnrp.core import (
    BodyRegionPrelude,
    BudgetPolicy,
    CacheObjectKind,
    FlowUpdateBackpressureLevel,
    FlowUpdateFlags,
    FlowUpdateMetadata,
    FlowUpdateReason,
    FlowUpdateScopeKind,
    FrameSubmitMetadata,
    HeaderFlags,
    MessageType,
    NnrpHeader,
    ObjectReferenceBlock,
    PayloadKind,
    ResultClass,
    ResultFlags,
    ResultHintBudgetPolicy,
    ResultHintCongestionState,
    ResultHintMetadata,
    ResultHintReason,
    ResultPushMetadata,
    SessionPatchAckMetadata,
    SubmitMode,
    TypedPayloadDescriptor,
    WireFormat,
    build_audio_chunk_frame,
    build_flow_update_packet,
    build_result_hint_packet,
    build_structured_event_frame,
    build_token_chunk_frame,
    build_video_chunk_frame,
    pack_typed_payload_frames,
)
from nnrp.core.messages import (
    ClientHelloMetadata,
    InputProfile,
    SessionPatchAckStatus,
    SessionPatchField,
    SessionPatchRejectReason,
    TileIndexMode,
)
from nnrp.tools import (
    export_cross_language_golden_vectors,
    parse_cross_language_golden_vectors_json,
    render_cross_language_golden_vectors_json,
)

HEADER_GOLDEN_HEX = "4e4e525001001028210000003000000000100000070000000b0000000200000015cd5b0700000000"
CLIENT_HELLO_METADATA_GOLDEN_HEX = (
    "01010100010000000100000007000000030000001f0000000300000001000700"
    "0400020000010000000080007017640002000200000000006000000000000000"
)
SESSION_PATCH_ACK_METADATA_GOLDEN_HEX = (
    "010003000b00000010000000000000000100000028230000020002000300000000000000010000000300000000000000"
)
FLOW_UPDATE_PACKET_GOLDEN_HEX = (
    "4e4e5250010017280000000020000000000000001500000000000000000006000d00000000"
    "0000000104020000000100000000000000000000000000280000000500000003000000"
)
RESULT_HINT_PACKET_GOLDEN_HEX = (
    "4e4e525001001828000000001000000000000000150000002f010000000007000e000000000000000300000003000000030000003c000000"
)
FRAME_SUBMIT_METADATA_GOLDEN_HEX = (
    "80026801200020005400020001020000640070170700000000000000c000000000000000"
    "000000000000000000000000000000000205ff0003000000290000001100000002000000"
)
RESULT_PUSH_METADATA_GOLDEN_HEX = (
    "0000040001005400020000004b0302004e03000000000000100000000000000000000000"
    "0000000000000000010100002900000035001f000300000003000000"
)
BODY_REGION_PRELUDE_GOLDEN_HEX = "1800000010000000100000000e00000010000000050000000000000000000000"
OBJECT_REFERENCE_BLOCK_GOLDEN_HEX = "02000000070000004433221188776655"
TYPED_PAYLOAD_DESCRIPTOR_GOLDEN_HEX = "10000300040000000700000000000000"
TYPED_PAYLOAD_FRAME_DESCRIPTOR_REGION_GOLDEN_HEX = (
    "02000100000000000300000000000000"
    "04000200030000000200000000000000"
    "08000300050000000500000000000000"
    "100004000a0000000300000000000000"
)
TYPED_PAYLOAD_FRAME_REGION_GOLDEN_HEX = "746f6b6175766964656f657674"


def test_header_golden_vector() -> None:
    header = NnrpHeader(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.FRAME_SUBMIT,
        flags=HeaderFlags.ACK_REQUIRED | HeaderFlags.KEYFRAME,
        meta_len=48,
        body_len=4096,
        session_id=7,
        frame_id=11,
        view_id=2,
        route_id=0,
        trace_id=123456789,
    )

    payload = header.pack()

    assert payload.hex() == HEADER_GOLDEN_HEX
    assert NnrpHeader.unpack(bytes.fromhex(HEADER_GOLDEN_HEX)) == header


def test_client_hello_metadata_golden_vector() -> None:
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

    assert payload.hex() == CLIENT_HELLO_METADATA_GOLDEN_HEX
    assert ClientHelloMetadata.unpack(bytes.fromhex(CLIENT_HELLO_METADATA_GOLDEN_HEX)) == metadata


def test_session_patch_ack_metadata_golden_vector() -> None:
    metadata = SessionPatchAckMetadata(
        ack_status=SessionPatchAckStatus.PARTIALLY_APPLIED,
        reject_reason=SessionPatchRejectReason.UNSUPPORTED_STRATEGY,
        applied_patch_mask=(
            SessionPatchField.TARGET_CADENCE | SessionPatchField.QUALITY_TIER | SessionPatchField.ACTIVE_LANE_MASK
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
        profile_patch_ack_bytes=0,
    )

    payload = metadata.pack()

    assert payload.hex() == SESSION_PATCH_ACK_METADATA_GOLDEN_HEX
    assert SessionPatchAckMetadata.unpack(bytes.fromhex(SESSION_PATCH_ACK_METADATA_GOLDEN_HEX)) == metadata


def test_flow_update_packet_golden_vector() -> None:
    metadata = FlowUpdateMetadata(
        scope_kind=FlowUpdateScopeKind.SESSION,
        update_reason=FlowUpdateReason.CONGESTION,
        backpressure_level=FlowUpdateBackpressureLevel.HARD,
        session_credit=1,
        retry_after_ms=40,
        credit_epoch=5,
        flags=FlowUpdateFlags.CREDIT_VALID | FlowUpdateFlags.RETRY_AFTER_VALID,
    )
    packet = build_flow_update_packet(
        metadata=metadata,
        session_id=21,
        route_id=6,
        trace_id=13,
    )

    payload = packet.pack()
    decoded = packet.unpack(bytes.fromhex(FLOW_UPDATE_PACKET_GOLDEN_HEX))

    assert payload.hex() == FLOW_UPDATE_PACKET_GOLDEN_HEX
    assert decoded == packet
    assert FlowUpdateMetadata.unpack(decoded.metadata) == metadata


def test_result_hint_packet_golden_vector() -> None:
    metadata = ResultHintMetadata(
        applied_budget_policy=ResultHintBudgetPolicy.STALE_REUSE,
        congestion_state=ResultHintCongestionState.SATURATED,
        reason=ResultHintReason.BUDGET_EXCEEDED,
        retry_after_ms=60,
    )
    packet = build_result_hint_packet(
        metadata=metadata,
        session_id=21,
        frame_id=303,
        route_id=7,
        trace_id=14,
    )

    payload = packet.pack()
    decoded = packet.unpack(bytes.fromhex(RESULT_HINT_PACKET_GOLDEN_HEX))

    assert payload.hex() == RESULT_HINT_PACKET_GOLDEN_HEX
    assert decoded == packet
    assert ResultHintMetadata.unpack(decoded.metadata) == metadata


def test_current_frame_submit_metadata_golden_vector() -> None:
    metadata = FrameSubmitMetadata(
        src_width=640,
        src_height=360,
        tile_width=32,
        tile_height=32,
        tile_count=84,
        section_count=2,
        frame_class=1,
        input_profile=InputProfile.DENSE_LUMA_FRAME,
        tile_index_mode=TileIndexMode.DENSE_RANGE,
        reserved0=0,
        latency_budget_ms=100,
        target_fps_x100=6000,
        retry_of_frame=7,
        tile_base_id=0,
        camera_bytes=192,
        tile_index_bytes=0,
        submit_mode=SubmitMode.MIXED,
        budget_policy=BudgetPolicy.ALLOW_PARTIAL | BudgetPolicy.ALLOW_DEGRADED,
        loss_tolerance_policy=0xFF,
        object_ref_mask=0x00000003,
        dependency_frame_id=41,
        payload_kind_bitmap=PayloadKind.TENSOR | PayloadKind.STRUCTURED_EVENT,
        payload_frame_count=2,
    )

    payload = metadata.pack()

    assert payload.hex() == FRAME_SUBMIT_METADATA_GOLDEN_HEX
    assert FrameSubmitMetadata.unpack(bytes.fromhex(FRAME_SUBMIT_METADATA_GOLDEN_HEX)) == metadata


def test_current_result_push_metadata_golden_vector() -> None:
    metadata = ResultPushMetadata(
        status_code=0,
        result_flags=ResultFlags.PARTIAL,
        section_count=1,
        tile_count=84,
        active_profile_id=2,
        reserved0=0,
        inference_ms=843,
        queue_ms=2,
        server_total_ms=846,
        reserved1=0,
        tile_base_id=0,
        tile_index_bytes=16,
        result_class=ResultClass.PARTIAL,
        applied_budget_policy=BudgetPolicy.ALLOW_PARTIAL,
        reused_frame_id=41,
        covered_tile_count=53,
        dropped_tile_count=31,
        payload_kind_bitmap=PayloadKind.TENSOR | PayloadKind.TOKEN_CHUNK,
        payload_frame_count=3,
    )

    payload = metadata.pack()

    assert payload.hex() == RESULT_PUSH_METADATA_GOLDEN_HEX
    assert ResultPushMetadata.unpack(bytes.fromhex(RESULT_PUSH_METADATA_GOLDEN_HEX)) == metadata


def test_body_region_prelude_golden_vector() -> None:
    prelude = BodyRegionPrelude(
        inline_object_bytes=24,
        object_reference_bytes=16,
        typed_payload_descriptor_bytes=16,
        typed_payload_frame_bytes=14,
        extension_descriptor_bytes=16,
        extension_payload_bytes=5,
    )

    payload = prelude.pack()

    assert payload.hex() == BODY_REGION_PRELUDE_GOLDEN_HEX
    assert BodyRegionPrelude.unpack(bytes.fromhex(BODY_REGION_PRELUDE_GOLDEN_HEX)) == prelude


def test_object_reference_block_golden_vector() -> None:
    block = ObjectReferenceBlock(
        object_kind=CacheObjectKind.TILE_INDEX_BLOCK,
        ref_flags=0,
        cache_namespace=7,
        cache_key_hi=0x11223344,
        cache_key_lo=0x55667788,
    )

    payload = block.pack()

    assert payload.hex() == OBJECT_REFERENCE_BLOCK_GOLDEN_HEX
    assert ObjectReferenceBlock.unpack(bytes.fromhex(OBJECT_REFERENCE_BLOCK_GOLDEN_HEX)) == block


def test_typed_payload_descriptor_golden_vector() -> None:
    descriptor = TypedPayloadDescriptor(
        payload_kind=PayloadKind.STRUCTURED_EVENT,
        descriptor_flags=0,
        profile_id=3,
        payload_offset=4,
        payload_length=7,
    )

    payload = descriptor.pack()

    assert payload.hex() == TYPED_PAYLOAD_DESCRIPTOR_GOLDEN_HEX
    assert TypedPayloadDescriptor.unpack(bytes.fromhex(TYPED_PAYLOAD_DESCRIPTOR_GOLDEN_HEX)) == descriptor


def test_typed_payload_frame_regions_golden_vector() -> None:
    descriptor_region, payload_region = pack_typed_payload_frames(
        (
            build_token_chunk_frame(b"tok", profile_id=1),
            build_audio_chunk_frame(b"au", profile_id=2),
            build_video_chunk_frame(b"video", profile_id=3),
            build_structured_event_frame(b"evt", profile_id=4),
        )
    )

    assert descriptor_region.hex() == TYPED_PAYLOAD_FRAME_DESCRIPTOR_REGION_GOLDEN_HEX
    assert payload_region.hex() == TYPED_PAYLOAD_FRAME_REGION_GOLDEN_HEX


def test_cross_language_golden_vector_manifest_round_trips() -> None:
    exported_vectors = export_cross_language_golden_vectors()
    manifest = render_cross_language_golden_vectors_json(exported_vectors)
    parsed_vectors = parse_cross_language_golden_vectors_json(manifest)

    assert parsed_vectors == exported_vectors


def test_cross_language_golden_vector_manifest_rejects_duplicate_names() -> None:
    manifest = """
    {
      \"schema\": \"nnrp.cross-language-golden-vectors.v1\",
      \"vectors\": [
        {\"name\": \"dup\", \"kind\": \"packet\", \"hex\": \"00\", \"bytes\": 1},
        {\"name\": \"dup\", \"kind\": \"packet\", \"hex\": \"01\", \"bytes\": 1}
      ]
    }
    """

    try:
        parse_cross_language_golden_vectors_json(manifest)
    except ValueError as exc:
        assert str(exc) == "duplicate golden vector name: dup"
    else:
        raise AssertionError("expected duplicate names to be rejected")
