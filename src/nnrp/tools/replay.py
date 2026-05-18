"""Replay/export helpers for turning existing runtime payloads into NNRP packets."""

from __future__ import annotations

import math
import struct
import zlib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum

from nnrp.core import (
    BudgetPolicy,
    CacheObjectKind,
    FrameSubmitMetadata,
    HeaderFlags,
    InputProfile,
    MessageType,
    NnrpPacket,
    ObjectReferenceBlock,
    PayloadKind,
    ResultClass,
    ResultFlags,
    ResultPushMetadata,
    ScalePolicy,
    SubmitMode,
    TensorDType,
    TensorLayout,
    TensorSectionData,
    TileIndexMode,
    WireFormat,
    build_camera_inline_object_block,
    build_camera_reference_block,
    build_frame_submit_packet,
    build_result_push_packet,
    build_tensor_section_table_inline_object_block,
    build_tensor_section_table_reference_block,
    build_tile_index_inline_object_block,
    build_tile_index_reference_block,
    pack_body,
    pack_object_reference_blocks,
    pack_tensor_section_data,
    pack_tile_index_block,
    unpack_body,
    unpack_inline_object_blocks,
    unpack_tensor_body,
)

REPLAY_CAMERA_BLOCK_MAGIC = b"NRCM"
_REPLAY_CAMERA_BLOCK_HEADER = struct.Struct("<4sHHHHHff")
_RAW_U16_TILE_ID_STRUCT = struct.Struct("<H")
_U32_LENGTH_STRUCT = struct.Struct("<I")

_FRAME_SUBMIT_CAMERA_REF_MASK = 1 << 0
_FRAME_SUBMIT_TILE_INDEX_REF_MASK = 1 << 1
_FRAME_SUBMIT_TENSOR_SECTION_TABLE_REF_MASK = 1 << 2


def _align_up(value: int, alignment: int = 8) -> int:
    return ((value + alignment - 1) // alignment) * alignment


class ReplaySectionRole(IntEnum):
    DEPTH_FP16 = 1
    NORMAL_OCT = 2
    MOTION_I16 = 3
    ROUGH_METAL = 4
    LUMA_HINT = 5
    SR_RESIDUAL = 100
    DETAIL_RESIDUAL = 101


class ReplayCodecId(IntEnum):
    NONE = 0
    LZ4 = 1


@dataclass(frozen=True, slots=True)
class ReplayCameraBlock:
    view: tuple[float, ...] = ()
    proj: tuple[float, ...] = ()
    prev_view_proj: tuple[float, ...] = ()
    jitter_x: float = 0.0
    jitter_y: float = 0.0


@dataclass(frozen=True, slots=True)
class WireSummary:
    subject: str
    message_type: MessageType
    wire_bytes: int
    metadata_bytes: int
    body_bytes: int
    tile_count: int
    section_count: int
    tile_index_bytes: int
    role_ids: tuple[int, ...]
    camera_bytes: int = 0
    result_flags: ResultFlags = ResultFlags.NONE


@dataclass(frozen=True, slots=True)
class WireSizeComparison:
    reference_label: str
    reference_bytes: int
    current: WireSummary

    @property
    def wire_bytes(self) -> int:
        return self.current.wire_bytes

    @property
    def delta_bytes(self) -> int:
        return self.wire_bytes - self.reference_bytes

    @property
    def wire_ratio_percent(self) -> str:
        if self.reference_bytes == 0:
            return "n/a"
        basis_points = (self.wire_bytes * 10000) // self.reference_bytes
        return f"{basis_points // 100}.{basis_points % 100:02d}%"


@dataclass(frozen=True, slots=True)
class _SectionSpec:
    field_name: str
    role_id: ReplaySectionRole
    dtype_id: TensorDType
    compression_field_name: str | None = None


_FEATURE_SECTION_SPECS = (
    _SectionSpec("depth_fp16", ReplaySectionRole.DEPTH_FP16, TensorDType.FP16),
    _SectionSpec("normal_oct", ReplaySectionRole.NORMAL_OCT, TensorDType.UINT8),
    _SectionSpec("motion_i16", ReplaySectionRole.MOTION_I16, TensorDType.INT16),
    _SectionSpec("rough_metal", ReplaySectionRole.ROUGH_METAL, TensorDType.UINT8),
    _SectionSpec("luma_hint", ReplaySectionRole.LUMA_HINT, TensorDType.UINT8),
)

_RESULT_SECTION_SPECS = (
    _SectionSpec(
        "sr_residual",
        ReplaySectionRole.SR_RESIDUAL,
        TensorDType.UINT8,
        "sr_residual_compression",
    ),
    _SectionSpec(
        "detail_residual",
        ReplaySectionRole.DETAIL_RESIDUAL,
        TensorDType.UINT8,
        "detail_residual_compression",
    ),
)


def frame_features_to_packet(
    frame_features: object,
    *,
    session_id: int | None = None,
    frame_class: int = 0,
    view_id: int = 0,
    trace_id: int | None = None,
) -> NnrpPacket:
    src_width = _as_int(_read_required(frame_features, "src_width"))
    src_height = _as_int(_read_required(frame_features, "src_height"))
    tile_size = _as_int(_read_required(frame_features, "tile_size"))
    tiles = _sort_tiles(
        _read_iterable(frame_features, "tiles"),
        src_width=src_width,
        tile_size=tile_size,
    )
    camera_block = pack_replay_camera_block(_coerce_camera_block(_read_optional(frame_features, "camera")))
    tile_ids = [_tile_id(_tile_x(tile), _tile_y(tile), src_width=src_width, tile_size=tile_size) for tile in tiles]
    sections = _build_sections(
        tiles,
        specs=_FEATURE_SECTION_SPECS,
        compression_field_name="compression_algorithm",
    )
    camera_reference = _coerce_standard_reference_block(
        _read_optional(frame_features, "camera_reference"),
        expected_kind=CacheObjectKind.CAMERA_BLOCK,
        builder=build_camera_reference_block,
    )
    tile_index_reference = _coerce_standard_reference_block(
        _read_optional(frame_features, "tile_index_reference"),
        expected_kind=CacheObjectKind.TILE_INDEX_BLOCK,
        builder=build_tile_index_reference_block,
    )
    tensor_section_table_reference = _coerce_standard_reference_block(
        _read_optional(frame_features, "tensor_section_table_reference"),
        expected_kind=CacheObjectKind.TENSOR_SECTION_TABLE,
        builder=build_tensor_section_table_reference_block,
    )
    budget_policy = BudgetPolicy.NONE
    if bool(_read_optional(frame_features, "allow_stale_reuse", False)):
        budget_policy |= BudgetPolicy.ALLOW_STALE_REUSE

    packet_kwargs = dict(
        session_id=_coerce_session_id(_read_required(frame_features, "session_id"), override=session_id),
        frame_id=_as_int(_read_required(frame_features, "frame_id")),
        src_width=src_width,
        src_height=src_height,
        tile_width=tile_size,
        tile_height=tile_size,
        tile_ids=tile_ids,
        sections=sections,
        camera_block=camera_block,
        frame_class=frame_class,
        input_profile=_map_input_profile(_read_optional(frame_features, "input_profile") or "unspecified"),
        tile_index_mode=TileIndexMode.RAW_U16,
        latency_budget_ms=_as_int(_read_optional(frame_features, "latency_budget_ms", 0)),
        target_fps_x100=0,
        retry_of_frame=0,
        tile_base_id=0,
        budget_policy=budget_policy,
        dependency_frame_id=0,
        loss_tolerance_policy=0xFF,
        version_major=1,
        wire_format=WireFormat.CURRENT,
        flags=HeaderFlags.NONE,
        view_id=view_id,
        trace_id=_coerce_trace_id(_read_optional(frame_features, "client_trace_id", ""), override=trace_id),
    )

    if camera_reference is not None or tile_index_reference is not None or tensor_section_table_reference is not None:
        return _build_frame_submit_current_reference_packet(
            **packet_kwargs,
            camera_reference=camera_reference,
            tile_index_reference=tile_index_reference,
            tensor_section_table_reference=tensor_section_table_reference,
        )

    return build_frame_submit_packet(
        **packet_kwargs,
        submit_mode=SubmitMode.INLINE,
        object_ref_mask=0,
        payload_kind_bitmap=PayloadKind.TENSOR,
        payload_frame_count=0,
    )


def frame_features_to_wire_bytes(frame_features: object, **kwargs) -> bytes:
    return frame_features_to_packet(frame_features, **kwargs).pack()


def frame_features_to_wire_summary(frame_features: object, **kwargs) -> WireSummary:
    return summarize_wire_packet(frame_features_to_packet(frame_features, **kwargs), subject="frame_submit")


def enhance_result_to_packet(
    enhance_result: object,
    *,
    session_id: int | None = None,
    view_id: int = 0,
    trace_id: int | None = None,
    active_profile_id: int = 0,
) -> NnrpPacket:
    src_width = _infer_result_src_width(enhance_result)
    tile_size = _infer_result_tile_size(enhance_result)
    tiles = _sort_tiles(
        _read_iterable(enhance_result, "tiles"),
        src_width=src_width,
        tile_size=tile_size,
    )
    delivered_tile_ids = [
        _tile_id(_tile_x(tile), _tile_y(tile), src_width=src_width, tile_size=tile_size) for tile in tiles
    ]
    stale = bool(_read_optional(enhance_result, "stale", False))
    degraded = bool(_read_optional(enhance_result, "degraded", False))
    covered_tile_count = _read_optional(enhance_result, "covered_tile_count", None)
    dropped_tile_count_value = _read_optional(enhance_result, "dropped_tile_count", 0)
    dropped_tile_count = 0 if dropped_tile_count_value is None else _as_int(dropped_tile_count_value)
    tile_ids = _resolve_result_tile_ids(
        enhance_result,
        delivered_tile_ids=delivered_tile_ids,
        covered_tile_count=covered_tile_count,
        dropped_tile_count=dropped_tile_count,
    )
    sections = _build_sections_for_tile_ids(
        tiles,
        requested_tile_ids=tile_ids,
        specs=_RESULT_SECTION_SPECS,
        compression_field_name=None,
        src_width=src_width,
        tile_size=tile_size,
    )
    reused_frame_id = _read_optional(enhance_result, "reused_frame_id", 0)
    result_flags = _read_optional(enhance_result, "result_flags", None)
    result_class = _read_optional(enhance_result, "result_class", None)
    applied_budget_policy = _read_optional(enhance_result, "applied_budget_policy", None)
    tile_index_reference = _coerce_standard_reference_block(
        _read_optional(enhance_result, "tile_index_reference"),
        expected_kind=CacheObjectKind.TILE_INDEX_BLOCK,
        builder=build_tile_index_reference_block,
    )
    tensor_section_table_reference = _coerce_standard_reference_block(
        _read_optional(enhance_result, "tensor_section_table_reference"),
        expected_kind=CacheObjectKind.TENSOR_SECTION_TABLE,
        builder=build_tensor_section_table_reference_block,
    )

    packet_kwargs = dict(
        session_id=_coerce_session_id(_read_required(enhance_result, "session_id"), override=session_id),
        frame_id=_as_int(_read_required(enhance_result, "frame_id")),
        tile_ids=tile_ids,
        sections=sections,
        result_flags=_default_result_flags(
            stale=stale,
            degraded=degraded,
            dropped_tile_count=dropped_tile_count,
            explicit=result_flags,
        ),
        active_profile_id=active_profile_id,
        inference_ms=_as_int(_read_optional(enhance_result, "inference_ms", 0)),
        queue_ms=_as_int(_read_optional(enhance_result, "queue_ms", 0)),
        server_total_ms=_as_int(_read_optional(enhance_result, "round_trip_ms", 0)),
        status_code=0,
        tile_index_mode=TileIndexMode.RAW_U16,
        tile_base_id=0,
        result_class=_default_result_class(
            stale=stale,
            degraded=degraded,
            dropped_tile_count=dropped_tile_count,
            explicit=result_class,
        ),
        applied_budget_policy=_default_applied_budget_policy(
            stale=stale,
            degraded=degraded,
            dropped_tile_count=dropped_tile_count,
            explicit=applied_budget_policy,
        ),
        reused_frame_id=0 if reused_frame_id is None else _as_int(reused_frame_id),
        covered_tile_count=len(tile_ids) if covered_tile_count is None else _as_int(covered_tile_count),
        dropped_tile_count=dropped_tile_count,
        version_major=1,
        wire_format=WireFormat.CURRENT,
        flags=HeaderFlags.NONE,
        view_id=view_id,
        trace_id=_coerce_trace_id(
            _read_optional(enhance_result, "request_id", "") or _read_optional(enhance_result, "client_trace_id", ""),
            override=trace_id,
        ),
    )

    if tile_index_reference is not None or tensor_section_table_reference is not None:
        return _build_result_push_current_reference_packet(
            **packet_kwargs,
            tile_index_reference=tile_index_reference,
            tensor_section_table_reference=tensor_section_table_reference,
        )

    return build_result_push_packet(
        **packet_kwargs,
        payload_kind_bitmap=PayloadKind.TENSOR,
        payload_frame_count=0,
    )


def _build_frame_submit_current_reference_packet(
    *,
    session_id: int,
    frame_id: int,
    src_width: int,
    src_height: int,
    tile_width: int,
    tile_height: int,
    tile_ids: list[int],
    sections: list[TensorSectionData],
    camera_block: bytes,
    camera_reference: ObjectReferenceBlock | None,
    tile_index_reference: ObjectReferenceBlock | None,
    tensor_section_table_reference: ObjectReferenceBlock | None,
    frame_class: int,
    input_profile: InputProfile,
    tile_index_mode: TileIndexMode,
    latency_budget_ms: int,
    target_fps_x100: int,
    retry_of_frame: int,
    tile_base_id: int,
    budget_policy: BudgetPolicy,
    dependency_frame_id: int,
    loss_tolerance_policy: int,
    version_major: int,
    wire_format: WireFormat,
    flags: HeaderFlags,
    view_id: int,
    trace_id: int,
) -> NnrpPacket:
    inline_blocks: list[bytes] = []
    reference_blocks: list[ObjectReferenceBlock] = []
    object_ref_mask = 0

    if camera_reference is not None:
        reference_blocks.append(camera_reference)
        object_ref_mask |= _FRAME_SUBMIT_CAMERA_REF_MASK
        camera_bytes = 0
    else:
        camera_bytes = len(camera_block)
        if camera_block:
            inline_blocks.append(build_camera_inline_object_block(camera_block))

    tile_index_payload = (
        pack_tile_index_block(tile_ids, mode=tile_index_mode, tile_base_id=tile_base_id) if tile_ids else b""
    )
    if tile_index_reference is not None:
        reference_blocks.append(tile_index_reference)
        object_ref_mask |= _FRAME_SUBMIT_TILE_INDEX_REF_MASK
        tile_index_bytes = 0
    else:
        tile_index_bytes = len(tile_index_payload)
        if tile_index_payload:
            inline_blocks.append(
                build_tile_index_inline_object_block(tile_ids, mode=tile_index_mode, tile_base_id=tile_base_id)
            )

    section_count = len(sections)
    if tensor_section_table_reference is not None:
        reference_blocks.append(tensor_section_table_reference)
        object_ref_mask |= _FRAME_SUBMIT_TENSOR_SECTION_TABLE_REF_MASK
    elif sections:
        inline_blocks.append(build_tensor_section_table_inline_object_block(_pack_tensor_section_region(sections)))

    submit_mode = SubmitMode.REFERENCE if not inline_blocks else SubmitMode.MIXED
    body = pack_body(
        inline_object_region=b"".join(inline_blocks),
        object_reference_region=pack_object_reference_blocks(reference_blocks),
    )
    metadata = FrameSubmitMetadata(
        src_width=src_width,
        src_height=src_height,
        tile_width=tile_width,
        tile_height=tile_height,
        tile_count=len(tile_ids),
        section_count=section_count,
        frame_class=frame_class,
        input_profile=input_profile,
        tile_index_mode=tile_index_mode,
        reserved0=0,
        latency_budget_ms=latency_budget_ms,
        target_fps_x100=target_fps_x100,
        retry_of_frame=retry_of_frame,
        tile_base_id=tile_base_id,
        camera_bytes=camera_bytes,
        tile_index_bytes=tile_index_bytes,
        submit_mode=submit_mode,
        budget_policy=budget_policy,
        loss_tolerance_policy=loss_tolerance_policy,
        object_ref_mask=object_ref_mask,
        dependency_frame_id=dependency_frame_id,
        payload_kind_bitmap=PayloadKind.TENSOR,
        payload_frame_count=0,
    ).pack()

    return NnrpPacket.build(
        version_major=version_major,
        wire_format=wire_format,
        msg_type=MessageType.FRAME_SUBMIT,
        flags=flags,
        session_id=session_id,
        frame_id=frame_id,
        view_id=view_id,
        trace_id=trace_id,
        metadata=metadata,
        body=body,
    )


def _build_result_push_current_reference_packet(
    *,
    session_id: int,
    frame_id: int,
    tile_ids: list[int],
    sections: list[TensorSectionData],
    tile_index_reference: ObjectReferenceBlock | None,
    tensor_section_table_reference: ObjectReferenceBlock | None,
    result_flags: ResultFlags,
    active_profile_id: int,
    inference_ms: int,
    queue_ms: int,
    server_total_ms: int,
    status_code: int,
    tile_index_mode: TileIndexMode,
    tile_base_id: int,
    result_class: ResultClass,
    applied_budget_policy: BudgetPolicy,
    reused_frame_id: int,
    covered_tile_count: int,
    dropped_tile_count: int,
    version_major: int,
    wire_format: WireFormat,
    flags: HeaderFlags,
    view_id: int,
    trace_id: int,
) -> NnrpPacket:
    inline_blocks: list[bytes] = []
    reference_blocks: list[ObjectReferenceBlock] = []
    tile_index_payload = (
        pack_tile_index_block(tile_ids, mode=tile_index_mode, tile_base_id=tile_base_id) if tile_ids else b""
    )

    if tile_index_reference is not None:
        reference_blocks.append(tile_index_reference)
        tile_index_bytes = 0
    else:
        tile_index_bytes = len(tile_index_payload)
        if tile_index_payload:
            inline_blocks.append(
                build_tile_index_inline_object_block(tile_ids, mode=tile_index_mode, tile_base_id=tile_base_id)
            )

    if tensor_section_table_reference is not None:
        reference_blocks.append(tensor_section_table_reference)
    elif sections:
        inline_blocks.append(build_tensor_section_table_inline_object_block(_pack_tensor_section_region(sections)))

    body = pack_body(
        inline_object_region=b"".join(inline_blocks),
        object_reference_region=pack_object_reference_blocks(sorted(reference_blocks, key=_object_reference_sort_key)),
    )
    metadata = ResultPushMetadata(
        status_code=status_code,
        result_flags=result_flags,
        section_count=len(sections),
        tile_count=len(tile_ids),
        active_profile_id=active_profile_id,
        reserved0=0,
        inference_ms=inference_ms,
        queue_ms=queue_ms,
        server_total_ms=server_total_ms,
        reserved1=0,
        tile_base_id=tile_base_id,
        tile_index_bytes=tile_index_bytes,
        result_class=result_class,
        applied_budget_policy=applied_budget_policy,
        reused_frame_id=reused_frame_id,
        covered_tile_count=covered_tile_count,
        dropped_tile_count=dropped_tile_count,
        payload_kind_bitmap=PayloadKind.TENSOR,
        payload_frame_count=0,
    ).pack()

    return NnrpPacket.build(
        version_major=version_major,
        wire_format=wire_format,
        msg_type=MessageType.RESULT_PUSH,
        flags=flags,
        session_id=session_id,
        frame_id=frame_id,
        view_id=view_id,
        trace_id=trace_id,
        metadata=metadata,
        body=body,
    )


def _default_result_flags(
    *,
    stale: bool,
    degraded: bool,
    dropped_tile_count: int,
    explicit: object | None,
) -> ResultFlags:
    if explicit is not None:
        return ResultFlags(explicit)

    flags = ResultFlags.NONE
    if stale:
        flags |= ResultFlags.STALE
    if degraded:
        flags |= ResultFlags.FALLBACK
    if dropped_tile_count > 0:
        flags |= ResultFlags.PARTIAL
    return flags


def _default_result_class(
    *,
    stale: bool,
    degraded: bool,
    dropped_tile_count: int,
    explicit: object | None,
) -> ResultClass:
    if explicit is not None:
        return ResultClass(explicit)
    if stale:
        return ResultClass.STALE_REUSE
    if degraded:
        return ResultClass.DEGRADED
    if dropped_tile_count > 0:
        return ResultClass.PARTIAL
    return ResultClass.COMPLETE


def _default_applied_budget_policy(
    *,
    stale: bool,
    degraded: bool,
    dropped_tile_count: int,
    explicit: object | None,
) -> BudgetPolicy:
    if explicit is not None:
        return BudgetPolicy(explicit)

    policy = BudgetPolicy.NONE
    if stale:
        policy |= BudgetPolicy.ALLOW_STALE_REUSE
    if degraded:
        policy |= BudgetPolicy.ALLOW_DEGRADED
    if dropped_tile_count > 0:
        policy |= BudgetPolicy.ALLOW_PARTIAL
    return policy


def enhance_result_to_wire_bytes(enhance_result: object, **kwargs) -> bytes:
    return enhance_result_to_packet(enhance_result, **kwargs).pack()


def enhance_result_to_wire_summary(enhance_result: object, **kwargs) -> WireSummary:
    return summarize_wire_packet(enhance_result_to_packet(enhance_result, **kwargs), subject="result_push")


def compare_frame_features_wire_size(
    frame_features: object,
    *,
    reference_payload: bytes,
    reference_label: str = "protobuf",
    **kwargs,
) -> WireSizeComparison:
    return compare_wire_size(
        frame_features_to_packet(frame_features, **kwargs),
        reference_payload=reference_payload,
        reference_label=reference_label,
        subject="frame_submit",
    )


def compare_enhance_result_wire_size(
    enhance_result: object,
    *,
    reference_payload: bytes,
    reference_label: str = "protobuf",
    **kwargs,
) -> WireSizeComparison:
    return compare_wire_size(
        enhance_result_to_packet(enhance_result, **kwargs),
        reference_payload=reference_payload,
        reference_label=reference_label,
        subject="result_push",
    )


def compare_wire_size(
    packet: NnrpPacket,
    *,
    reference_payload: bytes,
    reference_label: str = "protobuf",
    subject: str | None = None,
) -> WireSizeComparison:
    return WireSizeComparison(
        reference_label=reference_label,
        reference_bytes=len(reference_payload),
        current=summarize_wire_packet(packet, subject=subject),
    )


def summarize_wire_packet(packet: NnrpPacket, *, subject: str | None = None) -> WireSummary:
    if packet.header.msg_type is MessageType.FRAME_SUBMIT:
        metadata = FrameSubmitMetadata.unpack(packet.metadata)
        current_body = _try_unpack_body(packet.body)
        if current_body is not None:
            role_ids = _extract_inline_role_ids(
                current_body,
                section_count=metadata.section_count,
                tile_count=metadata.tile_count,
            )
        else:
            body_view = unpack_tensor_body(
                packet.body[_align_up(metadata.camera_bytes) :],
                tile_index_bytes=metadata.tile_index_bytes,
                section_count=metadata.section_count,
                tile_count=metadata.tile_count,
            )
            role_ids = tuple(section.desc.role_id for section in body_view.sections)
        return WireSummary(
            subject=subject or "frame_submit",
            message_type=packet.header.msg_type,
            wire_bytes=len(packet.pack()),
            metadata_bytes=len(packet.metadata),
            body_bytes=len(packet.body),
            tile_count=metadata.tile_count,
            section_count=metadata.section_count,
            tile_index_bytes=metadata.tile_index_bytes,
            role_ids=role_ids,
            camera_bytes=metadata.camera_bytes,
            result_flags=ResultFlags.NONE,
        )

    if packet.header.msg_type is MessageType.RESULT_PUSH:
        metadata = ResultPushMetadata.unpack(packet.metadata)
        current_body = _try_unpack_body(packet.body)
        if current_body is not None:
            role_ids = _extract_inline_role_ids(
                current_body,
                section_count=metadata.section_count,
                tile_count=metadata.tile_count,
            )
        else:
            body_view = unpack_tensor_body(
                packet.body,
                tile_index_bytes=metadata.tile_index_bytes,
                section_count=metadata.section_count,
                tile_count=metadata.tile_count,
            )
            role_ids = tuple(section.desc.role_id for section in body_view.sections)
        return WireSummary(
            subject=subject or "result_push",
            message_type=packet.header.msg_type,
            wire_bytes=len(packet.pack()),
            metadata_bytes=len(packet.metadata),
            body_bytes=len(packet.body),
            tile_count=metadata.tile_count,
            section_count=metadata.section_count,
            tile_index_bytes=metadata.tile_index_bytes,
            role_ids=role_ids,
            camera_bytes=0,
            result_flags=metadata.result_flags,
        )

    raise ValueError(
        f"current packet summary only supports FRAME_SUBMIT/RESULT_PUSH, got {packet.header.msg_type.name}"
    )


def render_wire_summary(summary: WireSummary) -> str:
    fields = [
        f"subject={summary.subject}",
        f"msg={summary.message_type.name}",
        f"wire={summary.wire_bytes}",
        f"meta={summary.metadata_bytes}",
        f"body={summary.body_bytes}",
        f"tiles={summary.tile_count}",
        f"sections={summary.section_count}",
        f"tile_index={summary.tile_index_bytes}",
        f"camera={summary.camera_bytes}",
        f"roles={','.join(str(role_id) for role_id in summary.role_ids) or '-'}",
    ]
    if summary.message_type is MessageType.RESULT_PUSH:
        fields.append(f"result_flags={_render_result_flags(summary.result_flags)}")
    return " ".join(fields)


def render_wire_size_comparison(comparison: WireSizeComparison) -> str:
    return (
        f"reference_label={comparison.reference_label} reference={comparison.reference_bytes} "
        f"current={comparison.wire_bytes} delta={comparison.delta_bytes} "
        f"current_ratio={comparison.wire_ratio_percent} "
        f"{render_wire_summary(comparison.current)}"
    )


def pack_replay_camera_block(camera: ReplayCameraBlock) -> bytes:
    payload = _REPLAY_CAMERA_BLOCK_HEADER.pack(
        REPLAY_CAMERA_BLOCK_MAGIC,
        1,
        len(camera.view),
        len(camera.proj),
        len(camera.prev_view_proj),
        0,
        float(camera.jitter_x),
        float(camera.jitter_y),
    )
    payload += _pack_float_array(camera.view)
    payload += _pack_float_array(camera.proj)
    payload += _pack_float_array(camera.prev_view_proj)
    return payload


def unpack_replay_camera_block(payload: bytes) -> ReplayCameraBlock:
    if len(payload) < _REPLAY_CAMERA_BLOCK_HEADER.size:
        raise ValueError(f"expected at least {_REPLAY_CAMERA_BLOCK_HEADER.size} camera bytes, got {len(payload)}")
    (
        magic,
        version,
        view_count,
        proj_count,
        prev_view_proj_count,
        _reserved,
        jitter_x,
        jitter_y,
    ) = _REPLAY_CAMERA_BLOCK_HEADER.unpack(payload[: _REPLAY_CAMERA_BLOCK_HEADER.size])
    if magic != REPLAY_CAMERA_BLOCK_MAGIC:
        raise ValueError(f"unexpected replay camera block magic: {magic!r}")
    if version != 1:
        raise ValueError(f"unsupported replay camera block version: {version}")

    cursor = _REPLAY_CAMERA_BLOCK_HEADER.size
    view, cursor = _unpack_float_array(payload, cursor=cursor, count=view_count)
    proj, cursor = _unpack_float_array(payload, cursor=cursor, count=proj_count)
    prev_view_proj, cursor = _unpack_float_array(payload, cursor=cursor, count=prev_view_proj_count)
    if cursor != len(payload):
        raise ValueError(f"unexpected trailing bytes in replay camera block: {len(payload) - cursor}")
    return ReplayCameraBlock(
        view=view,
        proj=proj,
        prev_view_proj=prev_view_proj,
        jitter_x=jitter_x,
        jitter_y=jitter_y,
    )


def _build_sections(
    tiles: list[object],
    *,
    specs: tuple[_SectionSpec, ...],
    compression_field_name: str | None,
) -> list[TensorSectionData]:
    sections: list[TensorSectionData] = []
    for spec in specs:
        payloads = [bytes(_read_optional(tile, spec.field_name, b"")) for tile in tiles]
        if not any(payloads):
            continue

        codec_ids = [
            _codec_id_from_name(
                _read_optional(tile, spec.compression_field_name or compression_field_name, "none") or "none"
            )
            for tile in tiles
        ]
        default_codec_id = Counter(codec_ids).most_common(1)[0][0]
        sections.append(
            TensorSectionData(
                role_id=int(spec.role_id),
                default_codec_id=default_codec_id,
                dtype_id=spec.dtype_id,
                tile_payloads=tuple(payloads),
                codec_ids=tuple(codec_ids),
                layout_id=TensorLayout.NHWC,
                scale_policy=ScalePolicy.NONE,
                payload_stride_bytes=0,
                element_count_per_tile=0,
            )
        )
    return sections


def _build_sections_for_tile_ids(
    tiles: list[object],
    *,
    requested_tile_ids: list[int],
    specs: tuple[_SectionSpec, ...],
    compression_field_name: str | None,
    src_width: int,
    tile_size: int,
) -> list[TensorSectionData]:
    tile_by_id: dict[int, object] = {}
    for tile in tiles:
        tile_id = _tile_id(_tile_x(tile), _tile_y(tile), src_width=src_width, tile_size=tile_size)
        if tile_id in tile_by_id:
            raise ValueError(f"duplicate replay tile id: {tile_id}")
        tile_by_id[tile_id] = tile

    sections: list[TensorSectionData] = []
    for spec in specs:
        payloads: list[bytes] = []
        codec_names: list[str] = []
        for tile_id in requested_tile_ids:
            tile = tile_by_id.get(tile_id)
            payloads.append(bytes(_read_optional(tile, spec.field_name, b"")) if tile is not None else b"")
            codec_names.append(
                str(
                    (
                        _read_optional(
                            tile,
                            spec.compression_field_name or compression_field_name,
                            "none",
                        )
                        if tile is not None
                        else "none"
                    )
                    or "none"
                )
            )
        if not any(payloads):
            continue

        codec_ids = [_codec_id_from_name(codec_name) for codec_name in codec_names]
        default_codec_id = Counter(codec_ids).most_common(1)[0][0]
        sections.append(
            TensorSectionData(
                role_id=int(spec.role_id),
                default_codec_id=default_codec_id,
                dtype_id=spec.dtype_id,
                tile_payloads=tuple(payloads),
                codec_ids=tuple(codec_ids),
                layout_id=TensorLayout.NHWC,
                scale_policy=ScalePolicy.NONE,
                payload_stride_bytes=0,
                element_count_per_tile=0,
            )
        )
    return sections


def _resolve_result_tile_ids(
    enhance_result: object,
    *,
    delivered_tile_ids: list[int],
    covered_tile_count: object | None,
    dropped_tile_count: int,
) -> list[int]:
    requested_tile_ids = _read_optional(enhance_result, "requested_tile_ids", None)
    if requested_tile_ids is None:
        resolved = list(delivered_tile_ids)
    else:
        resolved = sorted(_as_int(tile_id) for tile_id in requested_tile_ids)
        if len(set(resolved)) != len(resolved):
            raise ValueError("requested_tile_ids must not contain duplicates")
        missing_ids = [tile_id for tile_id in delivered_tile_ids if tile_id not in set(resolved)]
        if missing_ids:
            raise ValueError(f"requested_tile_ids must contain every delivered tile id: missing {missing_ids}")

    expected_total = (
        len(delivered_tile_ids) if covered_tile_count is None else _as_int(covered_tile_count) + dropped_tile_count
    )
    if expected_total > len(resolved):
        raise ValueError(
            "partial replay export requires requested_tile_ids to describe dropped tiles: "
            f"expected at least {expected_total} tile ids, got {len(resolved)}"
        )
    return resolved


def _coerce_camera_block(camera: object | None) -> ReplayCameraBlock:
    if camera is None:
        return ReplayCameraBlock()
    return ReplayCameraBlock(
        view=tuple(float(item) for item in _read_iterable(camera, "view", ())),
        proj=tuple(float(item) for item in _read_iterable(camera, "proj", ())),
        prev_view_proj=tuple(float(item) for item in _read_iterable(camera, "prev_view_proj", ())),
        jitter_x=float(_read_optional(camera, "jitter_x", 0.0)),
        jitter_y=float(_read_optional(camera, "jitter_y", 0.0)),
    )


def _coerce_standard_reference_block(
    reference: object | None,
    *,
    expected_kind: CacheObjectKind,
    builder,
) -> ObjectReferenceBlock | None:
    if reference is None:
        return None
    if isinstance(reference, ObjectReferenceBlock):
        if reference.object_kind is not expected_kind:
            raise ValueError(f"expected {expected_kind.name} object reference, got {reference.object_kind.name}")
        return reference
    return builder(
        cache_namespace=_read_reference_int(reference, "cache_namespace"),
        cache_key_hi=_read_reference_int(reference, "cache_key_hi"),
        cache_key_lo=_read_reference_int(reference, "cache_key_lo"),
    )


def _read_reference_int(reference: object, name: str) -> int:
    if isinstance(reference, dict):
        if name not in reference:
            raise ValueError(f"reference mapping is missing required field {name!r}")
        return _as_int(reference[name])
    if not hasattr(reference, name):
        raise ValueError(f"reference object {type(reference).__name__} is missing required field {name!r}")
    return _as_int(getattr(reference, name))


def _map_input_profile(value: str) -> InputProfile:
    normalized = str(value or "").strip().lower()
    if normalized == "changed_tiles_luma":
        return InputProfile.CHANGED_TILES_LUMA
    if normalized == "dense_luma_frame":
        return InputProfile.DENSE_LUMA_FRAME
    return InputProfile.UNSPECIFIED


def _sort_tiles(tiles: Iterable[object], *, src_width: int, tile_size: int) -> list[object]:
    return sorted(
        list(tiles),
        key=lambda tile: _tile_id(_tile_x(tile), _tile_y(tile), src_width=src_width, tile_size=tile_size),
    )


def _tile_x(tile: object) -> int:
    coord = _read_optional(tile, "coord")
    if coord is not None:
        return _as_int(_read_required(coord, "x"))
    return _as_int(_read_required(tile, "tile_x"))


def _tile_y(tile: object) -> int:
    coord = _read_optional(tile, "coord")
    if coord is not None:
        return _as_int(_read_required(coord, "y"))
    return _as_int(_read_required(tile, "tile_y"))


def _tile_id(tile_x: int, tile_y: int, *, src_width: int, tile_size: int) -> int:
    if tile_size <= 0:
        raise ValueError("tile_size must be positive")
    tiles_per_row = max(1, math.ceil(src_width / tile_size))
    return tile_y * tiles_per_row + tile_x


def _codec_id_from_name(name: str) -> int:
    normalized = str(name or "none").strip().lower()
    if normalized in {"", "none"}:
        return int(ReplayCodecId.NONE)
    if normalized == "lz4":
        return int(ReplayCodecId.LZ4)
    raise ValueError(f"unsupported replay codec/compression name: {name}")


def _render_result_flags(flags: ResultFlags) -> str:
    if flags is ResultFlags.NONE:
        return "NONE"

    names: list[str] = []
    for candidate in (ResultFlags.STALE, ResultFlags.FALLBACK, ResultFlags.PARTIAL):
        if flags & candidate:
            names.append(candidate.name)
    return "|".join(names)


def _coerce_session_id(source_session_id: object, *, override: int | None) -> int:
    if override is not None:
        return _as_int(override)
    return zlib.crc32(str(source_session_id).encode("utf-8")) & 0xFFFFFFFF


def _coerce_trace_id(source_trace: object, *, override: int | None) -> int:
    if override is not None:
        return _as_int(override)
    value = str(source_trace or "")
    if not value:
        return 0
    return zlib.crc32(value.encode("utf-8")) & 0xFFFFFFFF


def _infer_result_src_width(result: object) -> int:
    explicit_src_width = _read_optional(result, "src_width")
    if explicit_src_width is not None:
        return _as_int(explicit_src_width)
    max_tile_x = max((_tile_x(tile) for tile in _read_iterable(result, "tiles")), default=0)
    return (max_tile_x + 1) * _infer_result_tile_size(result)


def _infer_result_tile_size(result: object) -> int:
    explicit_tile_size = _read_optional(result, "tile_size")
    if explicit_tile_size is not None:
        return _as_int(explicit_tile_size)
    return 1


def _pack_float_array(values: tuple[float, ...]) -> bytes:
    if not values:
        return b""
    return struct.pack(f"<{len(values)}f", *values)


def _unpack_float_array(payload: bytes, *, cursor: int, count: int) -> tuple[tuple[float, ...], int]:
    if count == 0:
        return (), cursor
    size = struct.calcsize(f"<{count}f")
    end = cursor + size
    if end > len(payload):
        raise ValueError(f"expected {size} float bytes, got {len(payload) - cursor}")
    return tuple(struct.unpack(f"<{count}f", payload[cursor:end])), end


def _read_required(obj: object, name: str):
    if not hasattr(obj, name):
        raise ValueError(f"object {type(obj).__name__} is missing required field {name!r}")
    return getattr(obj, name)


def _read_optional(obj: object, name: str, default=None):
    return getattr(obj, name, default)


def _read_iterable(obj: object, name: str, default: Iterable[object] = ()) -> Iterable[object]:
    value = getattr(obj, name, default)
    return value if value is not None else default


def _as_int(value: object) -> int:
    return int(value)


def _pack_tensor_section_region(sections: list[TensorSectionData]) -> bytes:
    payload = bytearray()
    for section in sections:
        _append_zero_padding(payload)
        payload.extend(pack_tensor_section_data(section))
    return bytes(payload)


def _append_zero_padding(payload: bytearray, alignment: int = 8) -> None:
    aligned = _align_up(len(payload), alignment)
    if aligned > len(payload):
        payload.extend(b"\x00" * (aligned - len(payload)))


def _object_reference_sort_key(
    block: ObjectReferenceBlock,
) -> tuple[int, int, int, int]:
    return (
        int(block.object_kind),
        block.cache_namespace,
        block.cache_key_hi,
        block.cache_key_lo,
    )


def _try_unpack_body(body: bytes | memoryview):
    try:
        return unpack_body(body)
    except ValueError:
        return None


def _extract_inline_role_ids(
    body_view,
    *,
    section_count: int,
    tile_count: int,
) -> tuple[int, ...]:
    if section_count == 0 or not body_view.inline_object_region:
        return ()
    for block in unpack_inline_object_blocks(body_view.inline_object_region):
        if block.header.object_kind is CacheObjectKind.TENSOR_SECTION_TABLE:
            tensor_body = unpack_tensor_body(
                block.payload,
                tile_index_bytes=0,
                section_count=section_count,
                tile_count=tile_count,
            )
            return tuple(section.desc.role_id for section in tensor_body.sections)
    return ()
