import pytest

from nnrp.core import (
    BudgetPolicy,
    CacheObjectKind,
    ExtensionFrameFlags,
    FlowUpdateBackpressureLevel,
    FlowUpdateFlags,
    FlowUpdateMetadata,
    FlowUpdateReason,
    FlowUpdateScopeKind,
    FrameSubmitMetadata,
    HeaderFlags,
    InlineObjectBlockHeader,
    InputProfile,
    MessageType,
    NnrpPacket,
    PayloadKind,
    ResultClass,
    ResultFlags,
    ResultPushMetadata,
    SectionFlags,
    TensorDType,
    TensorLayout,
    TensorSectionData,
    TileIndexMode,
    build_audio_chunk_frame,
    build_camera_inline_object_block,
    build_camera_reference_block,
    build_degraded_result_push_packet,
    build_extension_frame,
    build_flow_update_packet,
    build_frame_cancel_packet,
    build_frame_submit_mixed_packet,
    build_frame_submit_packet,
    build_frame_submit_typed_payload_packet,
    build_header_only_packet,
    build_opaque_bytes_frame,
    build_partial_result_push_packet,
    build_ping_packet,
    build_pong_packet,
    build_result_drop_packet,
    build_result_push_mixed_packet,
    build_result_push_packet,
    build_result_push_typed_payload_packet,
    build_stale_reuse_result_push_packet,
    build_structured_event_frame,
    build_tensor_section_table_inline_object_block,
    build_tensor_section_table_reference_block,
    build_tile_index_inline_object_block,
    build_tile_index_reference_block,
    build_token_chunk_frame,
    build_tool_delta_frame,
    build_typed_payload_frame,
    build_video_chunk_frame,
    pack_extension_frames,
    pack_inline_object_block,
    pack_tensor_section_data,
    pack_tile_index_block,
    pack_typed_payload_frames,
    parse_audio_chunk_frame,
    parse_camera_inline_object_block,
    parse_camera_reference_block,
    parse_opaque_bytes_frame,
    parse_structured_event_frame,
    parse_tensor_section_table_inline_object_block,
    parse_tensor_section_table_reference_block,
    parse_tile_index_inline_object_block,
    parse_tile_index_reference_block,
    parse_token_chunk_frame,
    parse_tool_delta_frame,
    parse_video_chunk_frame,
    unpack_extension_frames,
    unpack_inline_object_block,
    unpack_inline_object_blocks,
    unpack_object_reference_blocks,
    unpack_tensor_body,
    unpack_tile_index_block,
    unpack_typed_payload_frames,
    validate_frame_submit_body,
    validate_result_push_body,
)
from nnrp.enums import WireFormat


def test_packet_roundtrip() -> None:
    packet = NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.FRAME_SUBMIT,
        flags=HeaderFlags.ACK_REQUIRED,
        session_id=7,
        frame_id=42,
        view_id=1,
        route_id=9,
        trace_id=99,
        metadata=b"meta",
        body=b"body",
    )

    encoded = packet.pack()
    decoded = NnrpPacket.unpack(encoded)

    assert decoded == packet
    assert decoded.header.meta_len == 4
    assert decoded.header.body_len == 4


def test_packet_rejects_length_mismatch() -> None:
    packet = NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.PING,
        metadata=b"abc",
        body=b"xyz",
    )
    packet.header.meta_len = 2

    with pytest.raises(ValueError, match="metadata length mismatch"):
        packet.pack()


def test_packet_unpack_rejects_truncated_payload() -> None:
    packet = NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.PING,
        metadata=b"abc",
        body=b"xyz",
    )
    encoded = packet.pack()

    with pytest.raises(ValueError, match="expected 46 bytes, got 45"):
        NnrpPacket.unpack(encoded[:-1])


def test_packet_unpack_rejects_trailing_payload() -> None:
    packet = NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.PING,
        metadata=b"abc",
        body=b"xyz",
    )
    encoded = packet.pack() + b"!"

    with pytest.raises(ValueError, match="expected 46 bytes, got 47"):
        NnrpPacket.unpack(encoded)


def test_header_only_packet_helpers_keep_zero_lengths() -> None:
    ping_packet = build_ping_packet(session_id=7, trace_id=41)
    pong_packet = build_pong_packet(session_id=7, trace_id=41)
    cancel_packet = build_frame_cancel_packet(session_id=7, frame_id=99, view_id=2, trace_id=41)

    assert ping_packet.header.wire_format is WireFormat.CURRENT
    assert pong_packet.header.wire_format is WireFormat.CURRENT
    assert cancel_packet.header.wire_format is WireFormat.CURRENT
    assert ping_packet.header.msg_type is MessageType.PING
    assert pong_packet.header.msg_type is MessageType.PONG
    assert cancel_packet.header.msg_type is MessageType.FRAME_CANCEL
    assert ping_packet.header.meta_len == 0
    assert ping_packet.header.body_len == 0
    assert cancel_packet.header.frame_id == 99
    assert cancel_packet.header.view_id == 2


def test_build_header_only_packet_rejects_non_header_only_type() -> None:
    with pytest.raises(ValueError, match="header-only control packet type"):
        build_header_only_packet(msg_type=MessageType.CLIENT_HELLO)


def test_result_drop_packet_roundtrip_keeps_zero_lengths() -> None:
    packet = build_result_drop_packet(session_id=13, frame_id=144, view_id=1, trace_id=55)

    encoded = packet.pack()
    decoded = NnrpPacket.unpack(encoded)

    assert decoded.header.wire_format is WireFormat.CURRENT
    assert decoded.header.msg_type is MessageType.RESULT_DROP
    assert decoded.header.meta_len == 0
    assert decoded.header.body_len == 0


def test_flow_update_packet_roundtrip() -> None:
    metadata = FlowUpdateMetadata(
        scope_kind=FlowUpdateScopeKind.SESSION,
        update_reason=FlowUpdateReason.CONGESTION,
        backpressure_level=FlowUpdateBackpressureLevel.SOFT,
        session_credit=1,
        retry_after_ms=40,
        credit_epoch=5,
        flags=FlowUpdateFlags.CREDIT_VALID | FlowUpdateFlags.RETRY_AFTER_VALID,
    )
    packet = build_flow_update_packet(metadata=metadata, session_id=21, route_id=6, trace_id=13)

    decoded = NnrpPacket.unpack(packet.pack())

    assert decoded == packet
    assert FlowUpdateMetadata.unpack(decoded.metadata) == metadata


def test_build_frame_submit_packet_assembles_metadata_and_body() -> None:
    build_frame_submit_packet(
        session_id=7,
        frame_id=101,
        operation_id=1001,
        src_width=640,
        src_height=360,
        tile_width=32,
        tile_height=32,
        tile_ids=(0, 1),
        sections=(
            TensorSectionData(
                role_id=1,
                default_codec_id=0,
                dtype_id=TensorDType.UINT8,
                tile_payloads=(b"aa", b"bb"),
            ),
        ),
        camera_block=b"camera!!",
        frame_class=0,
        input_profile=InputProfile.DENSE_LUMA_FRAME,
        tile_index_mode=TileIndexMode.RAW_U16,
        latency_budget_ms=50,
        target_fps_x100=6000,
        view_id=1,
    )


def test_tile_index_block_roundtrip_supports_all_current_modes() -> None:
    dense_payload = pack_tile_index_block((3, 4, 5), mode=TileIndexMode.DENSE_RANGE, tile_base_id=3)
    raw_payload = pack_tile_index_block((9, 17, 33), mode=TileIndexMode.RAW_U16)
    delta_payload = pack_tile_index_block((9, 17, 33), mode=TileIndexMode.DELTA_U16)
    bitset_payload = pack_tile_index_block((1, 4, 9), mode=TileIndexMode.BITSET)

    assert dense_payload == b""
    assert unpack_tile_index_block(dense_payload, mode=TileIndexMode.DENSE_RANGE, tile_count=3, tile_base_id=3) == (
        3,
        4,
        5,
    )
    assert unpack_tile_index_block(raw_payload, mode=TileIndexMode.RAW_U16, tile_count=3) == (9, 17, 33)
    assert unpack_tile_index_block(delta_payload, mode=TileIndexMode.DELTA_U16, tile_count=3) == (9, 17, 33)
    assert unpack_tile_index_block(bitset_payload, mode=TileIndexMode.BITSET, tile_count=3) == (1, 4, 9)


def test_tile_index_block_rejects_invalid_dense_and_bitset_shapes() -> None:
    with pytest.raises(ValueError, match="dense_range tile ids must be contiguous"):
        pack_tile_index_block((1, 3), mode=TileIndexMode.DENSE_RANGE, tile_base_id=1)

    with pytest.raises(ValueError, match="bitset decoded 1 tile ids, expected 2"):
        unpack_tile_index_block(b"\x01", mode=TileIndexMode.BITSET, tile_count=2)


def test_inline_object_block_roundtrip_and_padding_validation() -> None:
    header = InlineObjectBlockHeader(
        object_kind=CacheObjectKind.CAMERA_BLOCK,
        object_flags=0,
        profile_id=7,
        reserved0=0,
        object_bytes=3,
    )
    payload = pack_inline_object_block(header, b"cam")
    block = unpack_inline_object_block(payload)

    assert block.header == header
    assert bytes(block.payload) == b"cam"

    invalid_payload = bytearray(payload)
    invalid_payload[-1] = 1
    with pytest.raises(ValueError, match="zero"):
        unpack_inline_object_block(bytes(invalid_payload))


def test_typed_payload_frame_helpers_roundtrip_and_expected_kind_validation() -> None:
    frames = (
        build_token_chunk_frame(b"tok"),
        build_audio_chunk_frame(b"aud", profile_id=0x104),
        build_video_chunk_frame(b"vid", profile_id=0x105),
        build_structured_event_frame(b'{"phase":"run"}', profile_id=0x106),
        build_tool_delta_frame(b'{"delta":1}', profile_id=0x107),
        build_opaque_bytes_frame(b"bin", profile_id=0x108),
    )

    descriptors, payload_region = pack_typed_payload_frames(frames)
    decoded = unpack_typed_payload_frames(
        descriptors,
        payload_region,
        payload_kind_bitmap=(
            PayloadKind.TOKEN_CHUNK
            | PayloadKind.AUDIO_CHUNK
            | PayloadKind.VIDEO_CHUNK
            | PayloadKind.STRUCTURED_EVENT
            | PayloadKind.TOOL_DELTA
            | PayloadKind.OPAQUE_BYTES
        ),
    )

    assert parse_token_chunk_frame(decoded[0]) == frames[0]
    assert parse_audio_chunk_frame(decoded[1]) == frames[1]
    assert parse_video_chunk_frame(decoded[2]) == frames[2]
    assert parse_structured_event_frame(decoded[3]) == frames[3]
    assert parse_tool_delta_frame(decoded[4]) == frames[4]
    assert parse_opaque_bytes_frame(decoded[5]) == frames[5]

    with pytest.raises(ValueError, match="expected AUDIO_CHUNK"):
        parse_audio_chunk_frame(decoded[0])

    with pytest.raises(ValueError, match="tensor payloads must be encoded"):
        build_typed_payload_frame(PayloadKind.TENSOR, b"bad")


def test_extension_frames_roundtrip_skips_unknown_non_critical_and_rejects_unknown_critical() -> None:
    descriptors, payload_region = pack_extension_frames(
        (
            build_extension_frame(100, b"alpha", profile_id=1),
            build_extension_frame(101, b"beta", profile_id=2, extension_flags=ExtensionFrameFlags.CRITICAL),
        )
    )

    decoded = unpack_extension_frames(descriptors, payload_region, known_extension_kinds={100, 101})
    assert [frame.extension_kind for frame in decoded] == [100, 101]
    assert decoded[0].payload == b"alpha"
    assert decoded[1].payload == b"beta"

    skipped = unpack_extension_frames(descriptors, payload_region, known_extension_kinds={101})
    assert [frame.extension_kind for frame in skipped] == [101]

    critical_descriptors, critical_payload_region = pack_extension_frames(
        (build_extension_frame(102, b"gamma", extension_flags=ExtensionFrameFlags.CRITICAL),)
    )
    with pytest.raises(ValueError, match="unknown critical extension frame kind"):
        unpack_extension_frames(critical_descriptors, critical_payload_region, known_extension_kinds={101})


def test_frame_submit_typed_payload_packet_validates_current_body_contract() -> None:
    packet = build_frame_submit_typed_payload_packet(
        session_id=7,
        frame_id=701,
        operation_id=1701,
        frames=(build_token_chunk_frame(b"tok"), build_audio_chunk_frame(b"aud")),
        target_fps_x100=3000,
    )

    metadata = FrameSubmitMetadata.unpack(packet.metadata)
    body_view = validate_frame_submit_body(metadata, packet.body)
    typed_frames = unpack_typed_payload_frames(
        body_view.typed_payload_descriptor_region,
        body_view.typed_payload_frame_region,
        payload_kind_bitmap=metadata.payload_kind_bitmap,
    )

    assert metadata.tile_count == 0
    assert metadata.section_count == 0
    assert metadata.payload_frame_count == 2
    assert metadata.payload_kind_bitmap == (PayloadKind.TOKEN_CHUNK | PayloadKind.AUDIO_CHUNK)
    assert [frame.payload_kind for frame in typed_frames] == [PayloadKind.TOKEN_CHUNK, PayloadKind.AUDIO_CHUNK]


def test_frame_submit_mixed_packet_validates_tensor_and_typed_regions() -> None:
    packet = build_frame_submit_mixed_packet(
        session_id=7,
        frame_id=702,
        operation_id=1702,
        src_width=640,
        src_height=360,
        tile_width=32,
        tile_height=32,
        tile_ids=(0, 1),
        sections=(
            TensorSectionData(
                role_id=1,
                default_codec_id=0,
                dtype_id=TensorDType.UINT8,
                tile_payloads=(b"aa", b"bb"),
            ),
        ),
        frames=(build_token_chunk_frame(b"tok"),),
        camera_block=b"camera!!",
    )

    metadata = FrameSubmitMetadata.unpack(packet.metadata)
    body_view = validate_frame_submit_body(metadata, packet.body)
    typed_frames = unpack_typed_payload_frames(
        body_view.typed_payload_descriptor_region,
        body_view.typed_payload_frame_region,
        payload_kind_bitmap=metadata.payload_kind_bitmap,
    )

    assert metadata.camera_bytes == len(b"camera!!")
    assert metadata.tile_count == 2
    assert metadata.section_count == 1
    assert metadata.payload_frame_count == 1
    assert typed_frames[0].payload_kind is PayloadKind.TOKEN_CHUNK


def test_result_push_typed_payload_packet_validates_non_tensor_current_body_contract() -> None:
    packet = build_result_push_typed_payload_packet(
        session_id=7,
        frame_id=703,
        frames=(
            build_typed_payload_frame(
                PayloadKind.STRUCTURED_EVENT,
                b'{"state":"ok"}',
                profile_id=0x0103,
                schema_id=0x2001,
                schema_version=1,
            ),
            build_typed_payload_frame(
                PayloadKind.TOOL_DELTA,
                b'{"step":2}',
                profile_id=0x0103,
                schema_id=0x2002,
                schema_version=1,
            ),
        ),
    )

    metadata = ResultPushMetadata.unpack(packet.metadata)
    body_view = validate_result_push_body(metadata, packet.body)
    typed_frames = unpack_typed_payload_frames(
        body_view.typed_payload_descriptor_region,
        body_view.typed_payload_frame_region,
        payload_kind_bitmap=metadata.payload_kind_bitmap,
    )

    assert metadata.tile_count == 0
    assert metadata.section_count == 0
    assert metadata.payload_frame_count == 2
    assert metadata.covered_tile_count == 0
    assert metadata.dropped_tile_count == 0
    assert [frame.payload_kind for frame in typed_frames] == [PayloadKind.STRUCTURED_EVENT, PayloadKind.TOOL_DELTA]


def test_result_push_mixed_packet_validates_tensor_and_typed_regions() -> None:
    packet = build_result_push_mixed_packet(
        session_id=7,
        frame_id=704,
        tile_ids=(0, 1),
        sections=(
            TensorSectionData(
                role_id=4,
                default_codec_id=0,
                dtype_id=TensorDType.UINT8,
                tile_payloads=(b"ra", b"rb"),
            ),
        ),
        frames=(build_opaque_bytes_frame(b"bin"),),
    )

    metadata = ResultPushMetadata.unpack(packet.metadata)
    body_view = validate_result_push_body(metadata, packet.body)
    typed_frames = unpack_typed_payload_frames(
        body_view.typed_payload_descriptor_region,
        body_view.typed_payload_frame_region,
        payload_kind_bitmap=metadata.payload_kind_bitmap,
    )

    assert metadata.tile_count == 2
    assert metadata.section_count == 1
    assert metadata.payload_frame_count == 1
    assert metadata.covered_tile_count == 2
    assert typed_frames[0].payload_kind is PayloadKind.OPAQUE_BYTES


def test_result_push_packet_rejects_non_tensor_specific_tensor_fields() -> None:
    with pytest.raises(ValueError, match="non-tensor RESULT_PUSH current builder does not accept tile_ids"):
        build_result_push_packet(
            session_id=7,
            frame_id=705,
            tile_ids=(0,),
            sections=(),
            payload_kind_bitmap=PayloadKind.TOKEN_CHUNK,
        )

    with pytest.raises(ValueError, match="non-tensor RESULT_PUSH current builder does not accept covered_tile_count"):
        build_result_push_packet(
            session_id=7,
            frame_id=706,
            tile_ids=(),
            sections=(),
            payload_kind_bitmap=PayloadKind.TOKEN_CHUNK,
            covered_tile_count=1,
        )


def test_reference_block_builders_roundtrip_and_expected_kind_validation() -> None:
    camera_reference = build_camera_reference_block(cache_namespace=3, cache_key_hi=11, cache_key_lo=12)
    tile_index_reference = build_tile_index_reference_block(cache_namespace=4, cache_key_hi=21, cache_key_lo=22)
    section_table_reference = build_tensor_section_table_reference_block(
        cache_namespace=5,
        cache_key_hi=31,
        cache_key_lo=32,
    )

    assert parse_camera_reference_block(camera_reference) == camera_reference
    assert parse_tile_index_reference_block(tile_index_reference) == tile_index_reference
    assert parse_tensor_section_table_reference_block(section_table_reference) == section_table_reference

    with pytest.raises(ValueError, match="expected CAMERA_BLOCK"):
        parse_camera_reference_block(tile_index_reference)

    with pytest.raises(ValueError, match="multiple"):
        unpack_object_reference_blocks(b"\x00")


def test_standard_inline_object_builders_roundtrip_and_expected_kind_validation() -> None:
    camera_block = build_camera_inline_object_block(b"cam")
    tile_index_block = build_tile_index_inline_object_block((4, 5), mode=TileIndexMode.RAW_U16)
    section_table_block = build_tensor_section_table_inline_object_block(b"section-table")

    assert bytes(parse_camera_inline_object_block(camera_block).payload) == b"cam"
    assert parse_tile_index_inline_object_block(tile_index_block).header.object_kind is CacheObjectKind.TILE_INDEX_BLOCK
    assert (
        parse_tensor_section_table_inline_object_block(section_table_block).header.object_kind
        is CacheObjectKind.TENSOR_SECTION_TABLE
    )

    with pytest.raises(ValueError, match="expected CAMERA_BLOCK"):
        parse_camera_inline_object_block(tile_index_block)

    with pytest.raises(ValueError, match="expected TILE_INDEX_BLOCK"):
        parse_tile_index_inline_object_block(camera_block)


def test_result_push_wrapper_helpers_set_expected_metadata_flags() -> None:
    sections = (
        TensorSectionData(
            role_id=1,
            default_codec_id=0,
            dtype_id=TensorDType.UINT8,
            tile_payloads=(b"aa", b"bb"),
        ),
    )

    partial_packet = build_partial_result_push_packet(
        session_id=7,
        frame_id=801,
        tile_ids=(0, 1),
        sections=sections,
        covered_tile_count=1,
        dropped_tile_count=1,
    )
    stale_packet = build_stale_reuse_result_push_packet(
        session_id=7,
        frame_id=802,
        tile_ids=(0, 1),
        sections=sections,
        reused_frame_id=700,
    )
    degraded_packet = build_degraded_result_push_packet(
        session_id=7,
        frame_id=803,
        tile_ids=(0, 1),
        sections=sections,
    )

    partial_metadata = ResultPushMetadata.unpack(partial_packet.metadata)
    stale_metadata = ResultPushMetadata.unpack(stale_packet.metadata)
    degraded_metadata = ResultPushMetadata.unpack(degraded_packet.metadata)

    assert partial_metadata.result_class is ResultClass.PARTIAL
    assert partial_metadata.applied_budget_policy is BudgetPolicy.ALLOW_PARTIAL
    assert bool(partial_metadata.result_flags & ResultFlags.PARTIAL)
    assert partial_metadata.dropped_tile_count == 1

    assert stale_metadata.result_class is ResultClass.STALE_REUSE
    assert stale_metadata.applied_budget_policy is BudgetPolicy.ALLOW_STALE_REUSE
    assert bool(stale_metadata.result_flags & ResultFlags.STALE)
    assert stale_metadata.reused_frame_id == 700

    assert degraded_metadata.result_class is ResultClass.DEGRADED
    assert degraded_metadata.applied_budget_policy is BudgetPolicy.ALLOW_DEGRADED
    assert bool(degraded_metadata.result_flags & ResultFlags.FALLBACK)


def test_pack_tensor_section_data_supports_mixed_codec_and_fixed_stride() -> None:
    section_payload = pack_tensor_section_data(
        TensorSectionData(
            role_id=9,
            default_codec_id=0,
            dtype_id=TensorDType.UINT8,
            tile_payloads=(b"ab", b"cde"),
            codec_ids=(1, 2),
            layout_id=TensorLayout.NHWC,
            payload_stride_bytes=4,
        )
    )

    tensor_body = unpack_tensor_body(section_payload, tile_index_bytes=0, section_count=1, tile_count=2)
    section = tensor_body.sections[0]

    assert bool(section.desc.flags & SectionFlags.FIXED_STRIDE)
    assert bool(section.desc.flags & SectionFlags.MIXED_CODEC)
    assert tuple(section.codec_table) == (1, 2)
    assert section.tile_lengths() == (2, 3)
    assert tuple(bytes(item) for item in section.payload_slices()) == (b"ab", b"cde")


def test_pack_tensor_section_data_rejects_payload_longer_than_fixed_stride() -> None:
    with pytest.raises(ValueError, match="exceeds fixed stride"):
        pack_tensor_section_data(
            TensorSectionData(
                role_id=10,
                default_codec_id=0,
                dtype_id=TensorDType.UINT8,
                tile_payloads=(b"abcd",),
                payload_stride_bytes=2,
            )
        )


def test_build_frame_submit_packet_rejects_out_of_order_sections() -> None:
    with pytest.raises(ValueError, match="tensor sections must be ordered"):
        build_frame_submit_packet(
            session_id=7,
            frame_id=102,
            operation_id=1002,
            src_width=640,
            src_height=360,
            tile_width=32,
            tile_height=32,
            tile_ids=(0, 1),
            sections=(
                TensorSectionData(
                    role_id=5,
                    default_codec_id=0,
                    dtype_id=TensorDType.UINT8,
                    tile_payloads=(b"aa", b"bb"),
                ),
                TensorSectionData(
                    role_id=4,
                    default_codec_id=0,
                    dtype_id=TensorDType.UINT8,
                    tile_payloads=(b"cc", b"dd"),
                ),
            ),
            camera_block=b"camera!!",
        )


def test_build_result_push_packet_assembles_body() -> None:
    packet = build_result_push_packet(
        session_id=7,
        frame_id=103,
        tile_ids=(0, 1, 2),
        sections=(
            TensorSectionData(
                role_id=100,
                default_codec_id=0,
                dtype_id=TensorDType.UINT8,
                tile_payloads=(b"ra", b"rb", b"rc"),
            ),
        ),
        active_profile_id=1,
        inference_ms=11,
        queue_ms=2,
        server_total_ms=13,
        tile_index_mode=TileIndexMode.RAW_U16,
        view_id=1,
    )

    metadata = ResultPushMetadata.unpack(packet.metadata)
    body = validate_result_push_body(metadata, packet.body)
    section_block = next(
        block
        for block in unpack_inline_object_blocks(body.inline_object_region)
        if block.header.object_kind is CacheObjectKind.TENSOR_SECTION_TABLE
    )
    tensor_body = unpack_tensor_body(
        section_block.payload,
        tile_index_bytes=0,
        section_count=metadata.section_count,
        tile_count=metadata.tile_count,
    )

    assert packet.header.wire_format is WireFormat.CURRENT
    assert packet.header.msg_type is MessageType.RESULT_PUSH
    assert metadata.result_flags is ResultFlags.NONE
    assert metadata.tile_count == 3
    assert tensor_body.sections[0].tile_lengths() == (2, 2, 2)


def test_build_result_push_packet_rejects_duplicate_section_role_ids() -> None:
    with pytest.raises(ValueError, match="tensor sections must be ordered"):
        build_result_push_packet(
            session_id=7,
            frame_id=104,
            tile_ids=(0, 1),
            sections=(
                TensorSectionData(
                    role_id=8,
                    default_codec_id=0,
                    dtype_id=TensorDType.UINT8,
                    tile_payloads=(b"ra", b"rb"),
                ),
                TensorSectionData(
                    role_id=8,
                    default_codec_id=0,
                    dtype_id=TensorDType.UINT8,
                    tile_payloads=(b"rc", b"rd"),
                ),
            ),
        )
