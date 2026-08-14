import struct
from dataclasses import dataclass, field

from nnrp.core import (
    BudgetPolicy,
    CacheObjectKind,
    FrameSubmitMetadata,
    PayloadKind,
    ResultClass,
    ResultFlags,
    ResultPushMetadata,
    SubmitMode,
    unpack_body,
    unpack_current_tensor_body,
    unpack_inline_object_blocks,
    unpack_object_reference_blocks,
    unpack_tensor_body,
)
from nnrp.tools import (
    ReplayCameraBlock,
    compare_enhance_result_wire_size,
    compare_frame_features_wire_size,
    enhance_result_to_packet,
    enhance_result_to_wire_summary,
    frame_features_to_packet,
    frame_features_to_wire_summary,
    pack_replay_camera_block,
    render_wire_size_comparison,
    render_wire_summary,
    unpack_replay_camera_block,
)


@dataclass(slots=True)
class FakeCoord:
    x: int
    y: int


@dataclass(slots=True)
class FakeCamera:
    view: list[float] = field(default_factory=list)
    proj: list[float] = field(default_factory=list)
    prev_view_proj: list[float] = field(default_factory=list)
    jitter_x: float = 0.0
    jitter_y: float = 0.0


@dataclass(slots=True)
class FakeFeatureTile:
    coord: FakeCoord
    depth_fp16: bytes = b""
    normal_oct: bytes = b""
    motion_i16: bytes = b""
    rough_metal: bytes = b""
    luma_hint: bytes = b""
    compression_algorithm: str = "none"


@dataclass(slots=True)
class FakeCacheReference:
    cache_namespace: int
    cache_key_hi: int
    cache_key_lo: int


@dataclass(slots=True)
class FakeFrameFeatures:
    session_id: str
    frame_id: int
    operation_id: int
    src_width: int
    src_height: int
    tile_size: int
    camera: FakeCamera
    tiles: list[FakeFeatureTile]
    latency_budget_ms: int = 0
    input_profile: str = "unspecified"
    client_trace_id: str = ""
    camera_reference: FakeCacheReference | None = None
    tile_index_reference: FakeCacheReference | None = None
    tensor_section_table_reference: FakeCacheReference | None = None


@dataclass(slots=True)
class FakeResultTile:
    tile_x: int
    tile_y: int
    sr_residual: bytes = b""
    detail_residual: bytes = b""
    sr_residual_compression: str = "none"
    detail_residual_compression: str = "none"


@dataclass(slots=True)
class FakeEnhanceResult:
    session_id: str
    frame_id: int
    request_id: str
    tiles: list[FakeResultTile]
    src_width: int | None = None
    tile_size: int | None = None
    stale: bool = False
    degraded: bool = False
    reused_frame_id: int | None = None
    covered_tile_count: int | None = None
    dropped_tile_count: int = 0
    inference_ms: int = 0
    queue_ms: int = 0
    round_trip_ms: int = 0
    requested_tile_ids: tuple[int, ...] | None = None
    tile_index_reference: FakeCacheReference | None = None
    tensor_section_table_reference: FakeCacheReference | None = None


def test_replay_camera_block_roundtrip() -> None:
    camera = ReplayCameraBlock(
        view=(1.0, 2.0, 3.0, 4.0),
        proj=(5.0, 6.0),
        prev_view_proj=(7.0, 8.0, 9.0),
        jitter_x=0.25,
        jitter_y=-0.5,
    )

    payload = pack_replay_camera_block(camera)

    assert unpack_replay_camera_block(payload) == camera


def test_frame_features_export_to_current_packet() -> None:
    frame = FakeFrameFeatures(
        session_id="session-alpha",
        frame_id=7,
        operation_id=107,
        src_width=96,
        src_height=64,
        tile_size=32,
        camera=FakeCamera(
            view=[1.0] * 16,
            proj=[2.0] * 16,
            prev_view_proj=[3.0] * 16,
            jitter_x=0.125,
            jitter_y=-0.25,
        ),
        tiles=[
            FakeFeatureTile(coord=FakeCoord(2, 0), luma_hint=b"defg", compression_algorithm="lz4"),
            FakeFeatureTile(
                coord=FakeCoord(0, 0),
                depth_fp16=b"\x01\x02",
                luma_hint=b"abc",
                compression_algorithm="none",
            ),
        ],
        latency_budget_ms=50,
        input_profile="dense_luma_frame",
        client_trace_id="trace-alpha",
    )

    packet = frame_features_to_packet(frame, session_id=11, trace_id=22, view_id=3)
    metadata = FrameSubmitMetadata.unpack(packet.metadata)

    assert packet.header.session_id == 11
    assert packet.header.trace_id == 22
    assert packet.header.view_id == 3
    assert metadata.submit_mode is SubmitMode.INLINE
    assert metadata.budget_policy is BudgetPolicy.NONE
    assert metadata.payload_kind_bitmap is PayloadKind.TENSOR
    assert metadata.tile_count == 2
    assert metadata.section_count == 2
    assert metadata.camera_bytes > 0
    current_body = unpack_body(packet.body)
    inline_blocks = {
        block.header.object_kind: block for block in unpack_inline_object_blocks(current_body.inline_object_region)
    }
    camera = unpack_replay_camera_block(bytes(inline_blocks[CacheObjectKind.CAMERA_BLOCK].payload))
    assert camera.jitter_x == frame.camera.jitter_x
    assert len(camera.view) == 16

    body_view = unpack_current_tensor_body(
        current_body,
        section_count=metadata.section_count,
        tile_count=metadata.tile_count,
    )
    assert bytes(body_view.tile_index_block) == struct.pack("<HH", 0, 2)

    sections = {section.desc.role_id: section for section in body_view.sections}
    depth_section = sections[1]
    assert depth_section.tile_lengths() == (2, 0)
    assert bytes(depth_section.payload_slices()[0]) == b"\x01\x02"
    assert bytes(depth_section.payload_slices()[1]) == b""

    luma_section = sections[5]
    assert bytes(luma_section.codec_table) == b"\x00\x01"
    assert luma_section.tile_lengths() == (3, 4)
    assert bytes(luma_section.payload_slices()[0]) == b"abc"
    assert bytes(luma_section.payload_slices()[1]) == b"defg"


def test_frame_features_export_can_emit_mixed_object_reference_packet() -> None:
    frame = FakeFrameFeatures(
        session_id="session-alpha",
        frame_id=12,
        operation_id=112,
        src_width=96,
        src_height=64,
        tile_size=32,
        camera=FakeCamera(
            view=[1.0] * 16,
            proj=[2.0] * 16,
            prev_view_proj=[3.0] * 16,
        ),
        tiles=[
            FakeFeatureTile(coord=FakeCoord(2, 0), luma_hint=b"defg", compression_algorithm="lz4"),
            FakeFeatureTile(
                coord=FakeCoord(0, 0),
                depth_fp16=b"\x01\x02",
                luma_hint=b"abc",
                compression_algorithm="none",
            ),
        ],
        camera_reference=FakeCacheReference(cache_namespace=1, cache_key_hi=2, cache_key_lo=3),
    )

    packet = frame_features_to_packet(frame, session_id=21, trace_id=34, view_id=5)
    metadata = FrameSubmitMetadata.unpack(packet.metadata)
    body_view = unpack_body(packet.body)
    reference_blocks = unpack_object_reference_blocks(body_view.object_reference_region)
    inline_blocks = unpack_inline_object_blocks(body_view.inline_object_region)
    section_body = unpack_tensor_body(
        inline_blocks[1].payload,
        tile_index_bytes=0,
        section_count=metadata.section_count,
        tile_count=metadata.tile_count,
    )
    summary = frame_features_to_wire_summary(frame, session_id=21, trace_id=34, view_id=5)

    assert metadata.submit_mode is SubmitMode.MIXED
    assert metadata.object_ref_mask == 0x1
    assert metadata.camera_bytes == 0
    assert metadata.tile_index_bytes == 4
    assert [block.object_kind for block in reference_blocks] == [CacheObjectKind.CAMERA_BLOCK]
    assert [block.header.object_kind for block in inline_blocks] == [
        CacheObjectKind.TILE_INDEX_BLOCK,
        CacheObjectKind.TENSOR_SECTION_TABLE,
    ]
    assert tuple(section.desc.role_id for section in section_body.sections) == (1, 5)
    assert summary.camera_bytes == 0
    assert summary.tile_index_bytes == 4
    assert summary.role_ids == (1, 5)


def test_enhance_result_export_to_current_packet() -> None:
    result = FakeEnhanceResult(
        session_id="session-alpha",
        frame_id=9,
        request_id="request-zeta",
        stale=True,
        reused_frame_id=8,
        inference_ms=17,
        queue_ms=2,
        round_trip_ms=23,
        tiles=[
            FakeResultTile(tile_x=1, tile_y=0, sr_residual=b"AA", sr_residual_compression="none"),
            FakeResultTile(
                tile_x=0,
                tile_y=0,
                sr_residual=b"BBB",
                detail_residual=b"zz",
                sr_residual_compression="lz4",
                detail_residual_compression="none",
            ),
        ],
    )

    packet = enhance_result_to_packet(result, session_id=15, view_id=4, trace_id=33)
    metadata = ResultPushMetadata.unpack(packet.metadata)

    assert packet.header.session_id == 15
    assert packet.header.view_id == 4
    assert packet.header.trace_id == 33
    assert metadata.result_flags is ResultFlags.STALE
    assert metadata.result_class is ResultClass.STALE_REUSE
    assert metadata.applied_budget_policy is BudgetPolicy.ALLOW_STALE_REUSE
    assert metadata.reused_frame_id == 8
    assert metadata.payload_kind_bitmap is PayloadKind.TENSOR
    assert metadata.tile_count == 2
    assert metadata.section_count == 2
    assert metadata.covered_tile_count == 2
    assert metadata.dropped_tile_count == 0

    body_view = unpack_current_tensor_body(
        unpack_body(packet.body),
        section_count=metadata.section_count,
        tile_count=metadata.tile_count,
    )
    assert bytes(body_view.tile_index_block) == struct.pack("<HH", 0, 1)

    sections = {section.desc.role_id: section for section in body_view.sections}
    sr_section = sections[100]
    assert bytes(sr_section.codec_table) == b"\x01\x00"
    assert sr_section.tile_lengths() == (3, 2)
    assert bytes(sr_section.payload_slices()[0]) == b"BBB"
    assert bytes(sr_section.payload_slices()[1]) == b"AA"

    detail_section = sections[101]
    assert bytes(detail_section.codec_table) == b""
    assert detail_section.tile_lengths() == (2, 0)
    assert bytes(detail_section.payload_slices()[0]) == b"zz"
    assert bytes(detail_section.payload_slices()[1]) == b""


def test_enhance_result_export_can_emit_object_reference_packet() -> None:
    result = FakeEnhanceResult(
        session_id="session-alpha",
        frame_id=13,
        request_id="request-ref",
        stale=True,
        reused_frame_id=12,
        inference_ms=17,
        queue_ms=2,
        round_trip_ms=23,
        tile_index_reference=FakeCacheReference(cache_namespace=4, cache_key_hi=5, cache_key_lo=6),
        tensor_section_table_reference=FakeCacheReference(cache_namespace=7, cache_key_hi=8, cache_key_lo=9),
        tiles=[
            FakeResultTile(tile_x=1, tile_y=0, sr_residual=b"AA", sr_residual_compression="none"),
            FakeResultTile(
                tile_x=0,
                tile_y=0,
                sr_residual=b"BBB",
                detail_residual=b"zz",
                sr_residual_compression="lz4",
                detail_residual_compression="none",
            ),
        ],
    )

    packet = enhance_result_to_packet(result, session_id=31, view_id=6, trace_id=55)
    metadata = ResultPushMetadata.unpack(packet.metadata)
    body_view = unpack_body(packet.body)
    reference_blocks = unpack_object_reference_blocks(body_view.object_reference_region)
    summary = enhance_result_to_wire_summary(result, session_id=31, view_id=6, trace_id=55)

    assert metadata.tile_index_bytes == 0
    assert metadata.result_flags is ResultFlags.STALE
    assert metadata.reused_frame_id == 12
    assert bytes(body_view.inline_object_region) == b""
    assert [block.object_kind for block in reference_blocks] == [
        CacheObjectKind.TILE_INDEX_BLOCK,
        CacheObjectKind.TENSOR_SECTION_TABLE,
    ]
    assert summary.tile_index_bytes == 0
    assert summary.role_ids == ()
    assert summary.result_flags is ResultFlags.STALE


def test_enhance_result_export_marks_partial_from_counts() -> None:
    result = FakeEnhanceResult(
        session_id="session-alpha",
        frame_id=10,
        request_id="request-partial",
        requested_tile_ids=(0, 1),
        covered_tile_count=1,
        dropped_tile_count=1,
        tiles=[
            FakeResultTile(tile_x=0, tile_y=0, sr_residual=b"AA", sr_residual_compression="none"),
        ],
    )

    packet = enhance_result_to_packet(result, session_id=16, view_id=5, trace_id=44)
    metadata = ResultPushMetadata.unpack(packet.metadata)
    body_view = unpack_current_tensor_body(
        unpack_body(packet.body),
        section_count=metadata.section_count,
        tile_count=metadata.tile_count,
    )
    sr_section = body_view.sections[0]

    assert metadata.result_flags is ResultFlags.PARTIAL
    assert metadata.result_class is ResultClass.PARTIAL
    assert metadata.applied_budget_policy is BudgetPolicy.ALLOW_PARTIAL
    assert metadata.tile_count == 2
    assert metadata.covered_tile_count == 1
    assert metadata.dropped_tile_count == 1
    assert bytes(body_view.tile_index_block) == struct.pack("<HH", 0, 1)
    assert sr_section.tile_lengths() == (2, 0)
    assert bytes(sr_section.payload_slices()[0]) == b"AA"
    assert bytes(sr_section.payload_slices()[1]) == b""


def test_enhance_result_export_marks_degraded_from_flag() -> None:
    result = FakeEnhanceResult(
        session_id="session-alpha",
        frame_id=11,
        request_id="request-degraded",
        degraded=True,
        tiles=[
            FakeResultTile(tile_x=0, tile_y=0, sr_residual=b"AA", sr_residual_compression="none"),
        ],
    )

    packet = enhance_result_to_packet(result, session_id=17, view_id=6, trace_id=45)
    metadata = ResultPushMetadata.unpack(packet.metadata)

    assert metadata.result_flags is ResultFlags.FALLBACK
    assert metadata.result_class is ResultClass.DEGRADED
    assert metadata.applied_budget_policy is BudgetPolicy.ALLOW_DEGRADED
    assert metadata.covered_tile_count == 1
    assert metadata.dropped_tile_count == 0


def test_enhance_result_export_prefers_explicit_result_grid_context() -> None:
    result = FakeEnhanceResult(
        session_id="session-alpha",
        frame_id=10,
        request_id="request-grid",
        src_width=96,
        tile_size=32,
        tiles=[
            FakeResultTile(tile_x=0, tile_y=1, sr_residual=b"A"),
            FakeResultTile(tile_x=2, tile_y=0, sr_residual=b"B"),
        ],
    )

    packet = enhance_result_to_packet(result)
    metadata = ResultPushMetadata.unpack(packet.metadata)
    body_view = unpack_current_tensor_body(
        unpack_body(packet.body),
        section_count=metadata.section_count,
        tile_count=metadata.tile_count,
    )

    assert bytes(body_view.tile_index_block) == struct.pack("<HH", 2, 3)


def test_frame_features_wire_summary_and_comparison_are_stable() -> None:
    frame = FakeFrameFeatures(
        session_id="session-alpha",
        frame_id=7,
        operation_id=107,
        src_width=96,
        src_height=64,
        tile_size=32,
        camera=FakeCamera(
            view=[1.0] * 16,
            proj=[2.0] * 16,
            prev_view_proj=[3.0] * 16,
            jitter_x=0.125,
            jitter_y=-0.25,
        ),
        tiles=[
            FakeFeatureTile(coord=FakeCoord(2, 0), luma_hint=b"defg", compression_algorithm="lz4"),
            FakeFeatureTile(
                coord=FakeCoord(0, 0),
                depth_fp16=b"\x01\x02",
                luma_hint=b"abc",
                compression_algorithm="none",
            ),
        ],
        latency_budget_ms=50,
        input_profile="dense_luma_frame",
        client_trace_id="trace-alpha",
    )

    summary = frame_features_to_wire_summary(frame, session_id=11, trace_id=22, view_id=3)
    comparison = compare_frame_features_wire_size(
        frame,
        session_id=11,
        trace_id=22,
        view_id=3,
        reference_payload=b"x" * 512,
    )

    assert render_wire_summary(summary) == (
        "subject=frame_submit msg=FRAME_SUBMIT wire=520 meta=72 body=408 tiles=2 sections=2 "
        "tile_index=4 camera=214 roles=1,5"
    )
    assert render_wire_size_comparison(comparison) == (
        "reference_label=protobuf reference=512 current=520 delta=8 current_ratio=101.56% "
        "subject=frame_submit msg=FRAME_SUBMIT wire=520 meta=72 body=408 tiles=2 sections=2 "
        "tile_index=4 camera=214 roles=1,5"
    )


def test_enhance_result_wire_summary_and_comparison_are_stable() -> None:
    result = FakeEnhanceResult(
        session_id="session-alpha",
        frame_id=9,
        request_id="request-zeta",
        stale=True,
        reused_frame_id=8,
        inference_ms=17,
        queue_ms=2,
        round_trip_ms=23,
        tiles=[
            FakeResultTile(tile_x=1, tile_y=0, sr_residual=b"AA", sr_residual_compression="none"),
            FakeResultTile(
                tile_x=0,
                tile_y=0,
                sr_residual=b"BBB",
                detail_residual=b"zz",
                sr_residual_compression="lz4",
                detail_residual_compression="none",
            ),
        ],
    )

    summary = enhance_result_to_wire_summary(result, session_id=15, view_id=4, trace_id=33)
    comparison = compare_enhance_result_wire_size(
        result,
        session_id=15,
        view_id=4,
        trace_id=33,
        reference_payload=b"y" * 256,
    )

    assert render_wire_summary(summary) == (
        "subject=result_push msg=RESULT_PUSH wire=272 meta=64 body=168 tiles=2 sections=2 "
        "tile_index=4 camera=0 roles=100,101 result_flags=STALE"
    )
    assert render_wire_size_comparison(comparison) == (
        "reference_label=protobuf reference=256 current=272 delta=16 current_ratio=106.25% "
        "subject=result_push msg=RESULT_PUSH wire=272 meta=64 body=168 tiles=2 sections=2 "
        "tile_index=4 camera=0 roles=100,101 result_flags=STALE"
    )
