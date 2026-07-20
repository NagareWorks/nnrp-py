import pytest

from nnrp.core.messages import (
    BODY_REGION_PRELUDE_LENGTH,
    EXTENSION_FRAME_DESCRIPTOR_LENGTH,
    FRAME_SUBMIT_METADATA_LENGTH,
    INLINE_OBJECT_BLOCK_HEADER_LENGTH,
    OBJECT_REFERENCE_BLOCK_LENGTH,
    RESULT_PUSH_METADATA_LENGTH,
    TENSOR_SECTION_DESC_LENGTH,
    TYPED_PAYLOAD_DESCRIPTOR_LENGTH,
    BodyRegionPrelude,
    BudgetPolicy,
    CacheObjectKind,
    ExtensionFrameDescriptor,
    ExtensionFrameFlags,
    FrameSubmitMetadata,
    InlineObjectBlockHeader,
    InputProfile,
    ObjectReferenceBlock,
    PayloadKind,
    ResultClass,
    ResultFlags,
    ResultPushMetadata,
    ScalePolicy,
    SectionFlags,
    SubmitMode,
    TensorDType,
    TensorLayout,
    TensorSectionDesc,
    TileIndexMode,
    TypedPayloadDescriptor,
)


def test_tensor_section_desc_roundtrip() -> None:
    section = TensorSectionDesc(
        role_id=1,
        codec_id=1,
        dtype_id=TensorDType.FP16,
        layout_id=TensorLayout.NHWC,
        scale_policy=ScalePolicy.NONE,
        flags=SectionFlags.FIXED_STRIDE,
        element_count_per_tile=1024,
        codec_table_bytes=0,
        length_table_bytes=84 * 4,
        payload_bytes=8192,
        payload_stride_bytes=4096,
        reserved=0,
    )

    payload = section.pack()

    assert len(payload) == TENSOR_SECTION_DESC_LENGTH
    assert TENSOR_SECTION_DESC_LENGTH == 32
    assert TensorSectionDesc.unpack(payload) == section


def test_body_region_prelude_roundtrip() -> None:
    prelude = BodyRegionPrelude(
        inline_object_bytes=48,
        object_reference_bytes=16,
        typed_payload_descriptor_bytes=32,
        typed_payload_frame_bytes=96,
        extension_descriptor_bytes=16,
        extension_payload_bytes=24,
    )

    payload = prelude.pack()

    assert len(payload) == BODY_REGION_PRELUDE_LENGTH
    assert BODY_REGION_PRELUDE_LENGTH == 32
    assert BodyRegionPrelude.unpack(payload) == prelude


def test_body_region_prelude_rejects_misaligned_descriptor_tables() -> None:
    with pytest.raises(ValueError, match="typed_payload_descriptor_bytes must be a multiple"):
        BodyRegionPrelude(
            inline_object_bytes=0,
            object_reference_bytes=0,
            typed_payload_descriptor_bytes=4,
            typed_payload_frame_bytes=0,
            extension_descriptor_bytes=0,
            extension_payload_bytes=0,
        )


def test_inline_object_block_header_roundtrip() -> None:
    header = InlineObjectBlockHeader(
        object_kind=CacheObjectKind.CAMERA_BLOCK,
        object_flags=0,
        profile_id=0,
        reserved0=0,
        object_bytes=192,
    )

    payload = header.pack()

    assert len(payload) == INLINE_OBJECT_BLOCK_HEADER_LENGTH
    assert INLINE_OBJECT_BLOCK_HEADER_LENGTH == 16
    assert InlineObjectBlockHeader.unpack(payload) == header


def test_object_reference_block_roundtrip() -> None:
    block = ObjectReferenceBlock(
        object_kind=CacheObjectKind.TILE_INDEX_BLOCK,
        ref_flags=0,
        cache_namespace=7,
        cache_key_hi=0x1122334455667788,
        cache_key_lo=0x99AABBCCDDEEFF00,
    )

    payload = block.pack()

    assert len(payload) == OBJECT_REFERENCE_BLOCK_LENGTH
    assert OBJECT_REFERENCE_BLOCK_LENGTH == 24
    assert payload == bytes.fromhex("0200000007000000887766554433221100ffeeddccbbaa99")
    assert ObjectReferenceBlock.unpack(payload) == block


def test_typed_payload_descriptor_roundtrip() -> None:
    descriptor = TypedPayloadDescriptor(
        payload_kind=PayloadKind.STRUCTURED_EVENT,
        descriptor_flags=0,
        profile_id=0,
        payload_offset=64,
        payload_length=24,
    )

    payload = descriptor.pack()

    assert len(payload) == TYPED_PAYLOAD_DESCRIPTOR_LENGTH
    assert TYPED_PAYLOAD_DESCRIPTOR_LENGTH == 16
    assert TypedPayloadDescriptor.unpack(payload) == descriptor


def test_typed_payload_descriptor_rejects_multi_bit_payload_kind() -> None:
    with pytest.raises(ValueError, match="payload_kind must contain exactly one"):
        TypedPayloadDescriptor(
            payload_kind=PayloadKind.TENSOR | PayloadKind.TOKEN_CHUNK,
            descriptor_flags=0,
            profile_id=0,
            payload_offset=0,
            payload_length=8,
        )


def test_extension_frame_descriptor_roundtrip() -> None:
    descriptor = ExtensionFrameDescriptor(
        extension_kind=9,
        extension_flags=ExtensionFrameFlags.CRITICAL,
        profile_id=1,
        reserved0=0,
        payload_offset=32,
        payload_length=48,
    )

    payload = descriptor.pack()

    assert len(payload) == EXTENSION_FRAME_DESCRIPTOR_LENGTH
    assert EXTENSION_FRAME_DESCRIPTOR_LENGTH == 16
    assert ExtensionFrameDescriptor.unpack(payload) == descriptor


def test_extension_frame_descriptor_rejects_unknown_flag_bits() -> None:
    with pytest.raises(ValueError, match="extension_flags contains unknown bits"):
        ExtensionFrameDescriptor(
            extension_kind=9,
            extension_flags=0x0002,
            profile_id=1,
            reserved0=0,
            payload_offset=0,
            payload_length=8,
        )


def test_current_frame_submit_metadata_roundtrip() -> None:
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
        operation_id=0x1122334455667788,
        submit_mode=SubmitMode.MIXED,
        budget_policy=BudgetPolicy.ALLOW_PARTIAL | BudgetPolicy.ALLOW_DEGRADED,
        loss_tolerance_policy=0xFF,
        object_ref_mask=0x00000003,
        dependency_frame_id=41,
        payload_kind_bitmap=PayloadKind.TENSOR | PayloadKind.STRUCTURED_EVENT,
        payload_frame_count=2,
    )

    payload = metadata.pack()

    assert len(payload) == FRAME_SUBMIT_METADATA_LENGTH
    assert FRAME_SUBMIT_METADATA_LENGTH == 72
    assert payload[36:40] == bytes(4)
    assert payload[40:48] == bytes.fromhex("8877665544332211")
    assert payload[48:52] == bytes(4)
    assert FrameSubmitMetadata.unpack(payload) == metadata


def test_current_frame_submit_metadata_rejects_zero_operation_id() -> None:
    with pytest.raises(ValueError, match="frame_submit.operation_id must be non-zero"):
        FrameSubmitMetadata(
            src_width=1,
            src_height=1,
            tile_width=1,
            tile_height=1,
            tile_count=1,
            section_count=0,
            frame_class=0,
            input_profile=InputProfile.DENSE_LUMA_FRAME,
            tile_index_mode=TileIndexMode.DENSE_RANGE,
            reserved0=0,
            latency_budget_ms=1,
            target_fps_x100=1,
            retry_of_frame=0,
            tile_base_id=0,
            camera_bytes=0,
            tile_index_bytes=0,
            operation_id=0,
            submit_mode=SubmitMode.INLINE,
            budget_policy=BudgetPolicy.NONE,
            loss_tolerance_policy=0,
            object_ref_mask=0,
            dependency_frame_id=0,
            payload_kind_bitmap=PayloadKind.TENSOR,
            payload_frame_count=0,
        )


def test_current_frame_submit_metadata_rejects_unknown_budget_policy_bits() -> None:
    with pytest.raises(ValueError, match="budget_policy contains unknown bits"):
        FrameSubmitMetadata(
            src_width=1,
            src_height=1,
            tile_width=1,
            tile_height=1,
            tile_count=1,
            section_count=0,
            frame_class=0,
            input_profile=InputProfile.UNSPECIFIED,
            tile_index_mode=TileIndexMode.RAW_U16,
            reserved0=0,
            latency_budget_ms=0,
            target_fps_x100=0,
            retry_of_frame=0,
            tile_base_id=0,
            camera_bytes=0,
            tile_index_bytes=0,
            operation_id=1,
            submit_mode=SubmitMode.INLINE,
            budget_policy=0x80,
        )


def test_current_frame_submit_metadata_allows_non_tensor_payload_without_tile_fields() -> None:
    metadata = FrameSubmitMetadata(
        src_width=0,
        src_height=0,
        tile_width=0,
        tile_height=0,
        tile_count=0,
        section_count=0,
        frame_class=0,
        input_profile=InputProfile.UNSPECIFIED,
        tile_index_mode=TileIndexMode.DENSE_RANGE,
        reserved0=0,
        latency_budget_ms=0,
        target_fps_x100=0,
        retry_of_frame=0,
        tile_base_id=0,
        camera_bytes=0,
        tile_index_bytes=0,
        operation_id=1,
        payload_kind_bitmap=PayloadKind.STRUCTURED_EVENT,
        payload_frame_count=1,
    )

    payload = metadata.pack()

    assert len(payload) == FRAME_SUBMIT_METADATA_LENGTH
    assert FrameSubmitMetadata.unpack(payload) == metadata


def test_current_frame_submit_metadata_rejects_tensor_fields_for_non_tensor_payload() -> None:
    with pytest.raises(ValueError, match="tile_count must be 0"):
        FrameSubmitMetadata(
            src_width=0,
            src_height=0,
            tile_width=0,
            tile_height=0,
            tile_count=1,
            section_count=0,
            frame_class=0,
            input_profile=InputProfile.UNSPECIFIED,
            tile_index_mode=TileIndexMode.DENSE_RANGE,
            reserved0=0,
            latency_budget_ms=0,
            target_fps_x100=0,
            retry_of_frame=0,
            tile_base_id=0,
            camera_bytes=0,
            tile_index_bytes=0,
            operation_id=1,
            payload_kind_bitmap=PayloadKind.STRUCTURED_EVENT,
            payload_frame_count=1,
        )


def test_current_result_push_metadata_roundtrip() -> None:
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

    assert len(payload) == RESULT_PUSH_METADATA_LENGTH
    assert RESULT_PUSH_METADATA_LENGTH == 64
    decoded = ResultPushMetadata.unpack(payload)
    assert decoded == metadata
    assert decoded.result_class is ResultClass.PARTIAL


def test_current_result_push_metadata_rejects_unknown_payload_kind_bits() -> None:
    with pytest.raises(ValueError, match="payload_kind_bitmap contains unknown bits"):
        ResultPushMetadata(
            status_code=0,
            result_flags=ResultFlags.NONE,
            section_count=0,
            tile_count=0,
            active_profile_id=0,
            reserved0=0,
            inference_ms=0,
            queue_ms=0,
            server_total_ms=0,
            reserved1=0,
            tile_base_id=0,
            tile_index_bytes=0,
            payload_kind_bitmap=0x80000000,
        )


def test_current_result_push_metadata_allows_non_tensor_payload_without_tile_coverage() -> None:
    metadata = ResultPushMetadata(
        status_code=0,
        result_flags=ResultFlags.NONE,
        section_count=0,
        tile_count=0,
        active_profile_id=0,
        reserved0=0,
        inference_ms=0,
        queue_ms=0,
        server_total_ms=0,
        reserved1=0,
        tile_base_id=0,
        tile_index_bytes=0,
        result_class=ResultClass.COMPLETE,
        applied_budget_policy=BudgetPolicy.NONE,
        reused_frame_id=0,
        covered_tile_count=0,
        dropped_tile_count=0,
        payload_kind_bitmap=PayloadKind.TOKEN_CHUNK,
        payload_frame_count=2,
    )

    payload = metadata.pack()

    assert len(payload) == RESULT_PUSH_METADATA_LENGTH
    assert ResultPushMetadata.unpack(payload) == metadata


def test_current_result_push_metadata_rejects_tile_coverage_for_non_tensor_payload() -> None:
    with pytest.raises(ValueError, match="covered_tile_count must be 0"):
        ResultPushMetadata(
            status_code=0,
            result_flags=ResultFlags.NONE,
            section_count=0,
            tile_count=0,
            active_profile_id=0,
            reserved0=0,
            inference_ms=0,
            queue_ms=0,
            server_total_ms=0,
            reserved1=0,
            tile_base_id=0,
            tile_index_bytes=0,
            result_class=ResultClass.COMPLETE,
            applied_budget_policy=BudgetPolicy.NONE,
            reused_frame_id=0,
            covered_tile_count=1,
            dropped_tile_count=0,
            payload_kind_bitmap=PayloadKind.TOKEN_CHUNK,
            payload_frame_count=1,
        )
