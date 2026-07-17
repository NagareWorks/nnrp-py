"""Fixed-width data-plane metadata models for the current NNRP/1 wire format."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import ClassVar, TypeVar

from nnrp.core.messages.control import CacheObjectKind, LossTolerance, PayloadKind


class InputProfile(IntEnum):
    UNSPECIFIED = 0
    CHANGED_TILES_LUMA = 1
    DENSE_LUMA_FRAME = 2


class TileIndexMode(IntEnum):
    DENSE_RANGE = 0
    RAW_U16 = 1
    DELTA_U16 = 2
    BITSET = 3


class TensorDType(IntEnum):
    FP16 = 0
    FP32 = 1
    FP8_E4M3 = 2
    FP8_E5M2 = 3
    INT8 = 4
    UINT8 = 5
    INT16 = 6
    UINT16 = 7


class TensorLayout(IntEnum):
    NHWC = 0
    NCHW = 1


class ScalePolicy(IntEnum):
    NONE = 0
    LINEAR = 1
    ZERO_POINT = 2


class ResultFlags(IntFlag):
    NONE = 0
    STALE = 0x0001
    FALLBACK = 0x0002
    PARTIAL = 0x0004


class SubmitMode(IntEnum):
    INLINE = 0
    REFERENCE = 1
    MIXED = 2


class BudgetPolicy(IntFlag):
    NONE = 0
    ALLOW_PARTIAL = 0x01
    ALLOW_STALE_REUSE = 0x02
    ALLOW_DEGRADED = 0x04
    ALLOW_DROP = 0x08


class ResultClass(IntEnum):
    COMPLETE = 0
    PARTIAL = 1
    STALE_REUSE = 2
    DEGRADED = 3


class SectionFlags(IntFlag):
    NONE = 0
    MIXED_CODEC = 0x0001
    FIXED_STRIDE = 0x0002


class ExtensionFrameFlags(IntFlag):
    NONE = 0
    CRITICAL = 0x0001


MessageMetadataT = TypeVar("MessageMetadataT", bound="_FixedWidthMetadata")


class _FixedWidthMetadata:
    STRUCT: ClassVar[struct.Struct]

    def pack(self) -> bytes:
        raise NotImplementedError

    @classmethod
    def unpack(cls, payload: bytes) -> MessageMetadataT:
        if len(payload) != cls.STRUCT.size:
            raise ValueError(f"expected {cls.STRUCT.size} bytes, got {len(payload)}")
        return cls._from_tuple(cls.STRUCT.unpack(payload))

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> MessageMetadataT:
        raise NotImplementedError


FRAME_SUBMIT_STRUCT = struct.Struct("<HHHHHHBBBBHHIIIIQQBBBBIIIHH")
RESULT_PUSH_STRUCT = struct.Struct("<HHHHHHHHHHIIQQBBHIHHIHH")
BODY_REGION_PRELUDE_STRUCT = struct.Struct("<IIIIIIII")
INLINE_OBJECT_BLOCK_HEADER_STRUCT = struct.Struct("<HHHHII")
OBJECT_REFERENCE_BLOCK_STRUCT = struct.Struct("<HHIQQ")
TYPED_PAYLOAD_DESCRIPTOR_STRUCT = struct.Struct("<BBHIII")
EXTENSION_FRAME_DESCRIPTOR_STRUCT = struct.Struct("<HHHHII")
TENSOR_SECTION_DESC_STRUCT = struct.Struct("<HBBBBHIIIII I")

FRAME_SUBMIT_METADATA_LENGTH = FRAME_SUBMIT_STRUCT.size
RESULT_PUSH_METADATA_LENGTH = RESULT_PUSH_STRUCT.size
BODY_REGION_PRELUDE_LENGTH = BODY_REGION_PRELUDE_STRUCT.size
INLINE_OBJECT_BLOCK_HEADER_LENGTH = INLINE_OBJECT_BLOCK_HEADER_STRUCT.size
OBJECT_REFERENCE_BLOCK_LENGTH = OBJECT_REFERENCE_BLOCK_STRUCT.size
TYPED_PAYLOAD_DESCRIPTOR_LENGTH = TYPED_PAYLOAD_DESCRIPTOR_STRUCT.size
EXTENSION_FRAME_DESCRIPTOR_LENGTH = EXTENSION_FRAME_DESCRIPTOR_STRUCT.size
TENSOR_SECTION_DESC_LENGTH = TENSOR_SECTION_DESC_STRUCT.size

_RESULT_FLAGS_MASK = int(ResultFlags.STALE | ResultFlags.FALLBACK | ResultFlags.PARTIAL)
_BUDGET_POLICY_MASK = int(
    BudgetPolicy.ALLOW_PARTIAL | BudgetPolicy.ALLOW_STALE_REUSE | BudgetPolicy.ALLOW_DEGRADED | BudgetPolicy.ALLOW_DROP
)
_PAYLOAD_KIND_MASK = int(
    PayloadKind.TENSOR
    | PayloadKind.TOKEN_CHUNK
    | PayloadKind.AUDIO_CHUNK
    | PayloadKind.VIDEO_CHUNK
    | PayloadKind.STRUCTURED_EVENT
    | PayloadKind.TOOL_DELTA
    | PayloadKind.OPAQUE_BYTES
)
_EXTENSION_FRAME_FLAGS_MASK = int(ExtensionFrameFlags.CRITICAL)


def _coerce_result_flags(value: ResultFlags | int) -> ResultFlags:
    raw_value = int(value)
    if raw_value < 0:
        raise ValueError(f"result_flags must be non-negative, got {raw_value}")

    unknown_bits = raw_value & ~_RESULT_FLAGS_MASK
    if unknown_bits:
        raise ValueError(f"result_flags contains unknown bits: 0x{unknown_bits:04x}")
    return ResultFlags(raw_value)


def _coerce_budget_policy(value: BudgetPolicy | int) -> BudgetPolicy:
    raw_value = int(value)
    if raw_value < 0:
        raise ValueError(f"budget_policy must be non-negative, got {raw_value}")

    unknown_bits = raw_value & ~_BUDGET_POLICY_MASK
    if unknown_bits:
        raise ValueError(f"budget_policy contains unknown bits: 0x{unknown_bits:02x}")
    return BudgetPolicy(raw_value)


def _coerce_payload_kind_bitmap(value: PayloadKind | int) -> PayloadKind:
    raw_value = int(value)
    if raw_value < 0:
        raise ValueError(f"payload_kind_bitmap must be non-negative, got {raw_value}")

    unknown_bits = raw_value & ~_PAYLOAD_KIND_MASK
    if unknown_bits:
        raise ValueError(f"payload_kind_bitmap contains unknown bits: 0x{unknown_bits:08x}")
    return PayloadKind(raw_value)


def _coerce_single_payload_kind(value: PayloadKind | int) -> PayloadKind:
    normalized_bitmap = _coerce_payload_kind_bitmap(value)
    raw_value = int(normalized_bitmap)
    if raw_value == 0 or raw_value & (raw_value - 1) != 0:
        raise ValueError(f"payload_kind must contain exactly one current payload kind bit, got 0x{raw_value:08x}")
    return PayloadKind(raw_value)


def _coerce_extension_frame_flags(
    value: ExtensionFrameFlags | int,
) -> ExtensionFrameFlags:
    raw_value = int(value)
    if raw_value < 0:
        raise ValueError(f"extension_flags must be non-negative, got {raw_value}")
    if raw_value & ~_EXTENSION_FRAME_FLAGS_MASK:
        raise ValueError(f"extension_flags contains unknown bits: 0x{raw_value:04x}")
    return ExtensionFrameFlags(raw_value)


def _validate_non_tensor_submit_fields(metadata: FrameSubmitMetadata) -> None:
    if metadata.payload_kind_bitmap & PayloadKind.TENSOR:
        return

    invalid_fields = {
        "src_width": metadata.src_width,
        "src_height": metadata.src_height,
        "tile_width": metadata.tile_width,
        "tile_height": metadata.tile_height,
        "tile_count": metadata.tile_count,
        "section_count": metadata.section_count,
        "tile_base_id": metadata.tile_base_id,
        "camera_bytes": metadata.camera_bytes,
        "tile_index_bytes": metadata.tile_index_bytes,
    }
    for field_name, value in invalid_fields.items():
        if value != 0:
            raise ValueError(f"{field_name} must be 0 when payload_kind_bitmap has no tensor payload")
    if metadata.input_profile is not InputProfile.UNSPECIFIED:
        raise ValueError("input_profile must be UNSPECIFIED when payload_kind_bitmap has no tensor payload")


def _validate_non_tensor_result_fields(metadata: ResultPushMetadata) -> None:
    if metadata.payload_kind_bitmap & PayloadKind.TENSOR:
        return

    invalid_fields = {
        "section_count": metadata.section_count,
        "tile_count": metadata.tile_count,
        "tile_base_id": metadata.tile_base_id,
        "tile_index_bytes": metadata.tile_index_bytes,
        "covered_tile_count": metadata.covered_tile_count,
        "dropped_tile_count": metadata.dropped_tile_count,
    }
    for field_name, value in invalid_fields.items():
        if value != 0:
            raise ValueError(f"{field_name} must be 0 when payload_kind_bitmap has no tensor payload")


def _coerce_loss_tolerance_policy(value: LossTolerance | int) -> int:
    raw_value = int(value)
    if raw_value == 0xFF:
        return raw_value
    LossTolerance(raw_value)
    return raw_value


@dataclass(slots=True)
class BodyRegionPrelude(_FixedWidthMetadata):
    """Fixed-width body prelude that locates all body regions."""

    STRUCT: ClassVar[struct.Struct] = BODY_REGION_PRELUDE_STRUCT

    inline_object_bytes: int
    object_reference_bytes: int
    typed_payload_descriptor_bytes: int
    typed_payload_frame_bytes: int
    extension_descriptor_bytes: int
    extension_payload_bytes: int
    body_flags: int = 0
    reserved: int = 0

    def __post_init__(self) -> None:
        if self.body_flags != 0:
            raise ValueError("body_flags must be 0 in current")
        if self.reserved != 0:
            raise ValueError("reserved must be 0 in current body prelude")
        if self.typed_payload_descriptor_bytes % TYPED_PAYLOAD_DESCRIPTOR_LENGTH != 0:
            raise ValueError(f"typed_payload_descriptor_bytes must be a multiple of {TYPED_PAYLOAD_DESCRIPTOR_LENGTH}")
        if self.extension_descriptor_bytes % EXTENSION_FRAME_DESCRIPTOR_LENGTH != 0:
            raise ValueError(f"extension_descriptor_bytes must be a multiple of {EXTENSION_FRAME_DESCRIPTOR_LENGTH}")

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.inline_object_bytes,
            self.object_reference_bytes,
            self.typed_payload_descriptor_bytes,
            self.typed_payload_frame_bytes,
            self.extension_descriptor_bytes,
            self.extension_payload_bytes,
            self.body_flags,
            self.reserved,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> BodyRegionPrelude:
        return cls(*values)


@dataclass(slots=True)
class InlineObjectBlockHeader(_FixedWidthMetadata):
    """Fixed-width header for one inline low-frequency object block."""

    STRUCT: ClassVar[struct.Struct] = INLINE_OBJECT_BLOCK_HEADER_STRUCT

    object_kind: CacheObjectKind
    object_flags: int
    profile_id: int
    reserved0: int
    object_bytes: int
    reserved1: int = 0

    def __post_init__(self) -> None:
        self.object_kind = CacheObjectKind(self.object_kind)
        if self.object_flags != 0:
            raise ValueError("object_flags must be 0 in current inline object blocks")
        if self.reserved0 != 0:
            raise ValueError("reserved0 must be 0 in current inline object blocks")
        if self.reserved1 != 0:
            raise ValueError("reserved1 must be 0 in current inline object blocks")

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            int(self.object_kind),
            self.object_flags,
            self.profile_id,
            self.reserved0,
            self.object_bytes,
            self.reserved1,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> InlineObjectBlockHeader:
        object_kind, object_flags, profile_id, reserved0, object_bytes, reserved1 = values
        return cls(
            object_kind=CacheObjectKind(object_kind),
            object_flags=object_flags,
            profile_id=profile_id,
            reserved0=reserved0,
            object_bytes=object_bytes,
            reserved1=reserved1,
        )


@dataclass(slots=True)
class ObjectReferenceBlock(_FixedWidthMetadata):
    """Fixed-width cache-backed low-frequency object reference."""

    STRUCT: ClassVar[struct.Struct] = OBJECT_REFERENCE_BLOCK_STRUCT

    object_kind: CacheObjectKind
    ref_flags: int
    cache_namespace: int
    cache_key_hi: int
    cache_key_lo: int

    def __post_init__(self) -> None:
        self.object_kind = CacheObjectKind(self.object_kind)
        if self.ref_flags != 0:
            raise ValueError("ref_flags must be 0 in current object reference blocks")

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            int(self.object_kind),
            self.ref_flags,
            self.cache_namespace,
            self.cache_key_hi,
            self.cache_key_lo,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> ObjectReferenceBlock:
        object_kind, ref_flags, cache_namespace, cache_key_hi, cache_key_lo = values
        return cls(
            object_kind=CacheObjectKind(object_kind),
            ref_flags=ref_flags,
            cache_namespace=cache_namespace,
            cache_key_hi=cache_key_hi,
            cache_key_lo=cache_key_lo,
        )


@dataclass(slots=True)
class TypedPayloadDescriptor(_FixedWidthMetadata):
    """Fixed-width descriptor for one logical typed payload frame."""

    STRUCT: ClassVar[struct.Struct] = TYPED_PAYLOAD_DESCRIPTOR_STRUCT

    payload_kind: PayloadKind
    descriptor_flags: int
    profile_id: int
    payload_offset: int
    payload_length: int
    reserved: int = 0

    def __post_init__(self) -> None:
        self.payload_kind = _coerce_single_payload_kind(self.payload_kind)
        if self.descriptor_flags != 0:
            raise ValueError("descriptor_flags must be 0 in current typed payload descriptors")
        if self.reserved != 0:
            raise ValueError("reserved must be 0 in current typed payload descriptors")

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            int(self.payload_kind),
            self.descriptor_flags,
            self.profile_id,
            self.payload_offset,
            self.payload_length,
            self.reserved,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> TypedPayloadDescriptor:
        (
            payload_kind,
            descriptor_flags,
            profile_id,
            payload_offset,
            payload_length,
            reserved,
        ) = values
        return cls(
            payload_kind=_coerce_single_payload_kind(payload_kind),
            descriptor_flags=descriptor_flags,
            profile_id=profile_id,
            payload_offset=payload_offset,
            payload_length=payload_length,
            reserved=reserved,
        )


@dataclass(slots=True)
class ExtensionFrameDescriptor(_FixedWidthMetadata):
    """Fixed-width descriptor for one extension frame payload."""

    STRUCT: ClassVar[struct.Struct] = EXTENSION_FRAME_DESCRIPTOR_STRUCT

    extension_kind: int
    extension_flags: ExtensionFrameFlags
    profile_id: int
    reserved0: int
    payload_offset: int
    payload_length: int

    def __post_init__(self) -> None:
        self.extension_flags = _coerce_extension_frame_flags(self.extension_flags)
        if self.reserved0 != 0:
            raise ValueError("reserved0 must be 0 in current extension frame descriptors")

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.extension_kind,
            int(self.extension_flags),
            self.profile_id,
            self.reserved0,
            self.payload_offset,
            self.payload_length,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> ExtensionFrameDescriptor:
        (
            extension_kind,
            extension_flags,
            profile_id,
            reserved0,
            payload_offset,
            payload_length,
        ) = values
        return cls(
            extension_kind=extension_kind,
            extension_flags=_coerce_extension_frame_flags(extension_flags),
            profile_id=profile_id,
            reserved0=reserved0,
            payload_offset=payload_offset,
            payload_length=payload_length,
        )


@dataclass(slots=True)
class FrameSubmitMetadata(_FixedWidthMetadata):
    """Aligned metadata for FRAME_SUBMIT."""

    STRUCT: ClassVar[struct.Struct] = FRAME_SUBMIT_STRUCT

    src_width: int
    src_height: int
    tile_width: int
    tile_height: int
    tile_count: int
    section_count: int
    frame_class: int
    input_profile: InputProfile
    tile_index_mode: TileIndexMode
    reserved0: int
    latency_budget_ms: int
    target_fps_x100: int
    retry_of_frame: int
    tile_base_id: int
    camera_bytes: int
    tile_index_bytes: int
    submit_mode: SubmitMode = SubmitMode.INLINE
    budget_policy: BudgetPolicy = BudgetPolicy.NONE
    reserved1: int = 0
    reserved2: int = 0
    loss_tolerance_policy: int = 0xFF
    reserved3: int = 0
    object_ref_mask: int = 0
    dependency_frame_id: int = 0
    payload_kind_bitmap: PayloadKind = PayloadKind.TENSOR
    payload_frame_count: int = 0
    reserved4: int = 0

    def __post_init__(self) -> None:
        self.submit_mode = SubmitMode(self.submit_mode)
        self.budget_policy = _coerce_budget_policy(self.budget_policy)
        self.loss_tolerance_policy = _coerce_loss_tolerance_policy(self.loss_tolerance_policy)
        self.payload_kind_bitmap = _coerce_payload_kind_bitmap(self.payload_kind_bitmap)
        _validate_non_tensor_submit_fields(self)

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.src_width,
            self.src_height,
            self.tile_width,
            self.tile_height,
            self.tile_count,
            self.section_count,
            self.frame_class,
            int(self.input_profile),
            int(self.tile_index_mode),
            self.reserved0,
            self.latency_budget_ms,
            self.target_fps_x100,
            self.retry_of_frame,
            self.tile_base_id,
            self.camera_bytes,
            self.tile_index_bytes,
            self.reserved1,
            self.reserved2,
            int(self.submit_mode),
            int(self.budget_policy),
            self.loss_tolerance_policy,
            self.reserved3,
            self.object_ref_mask,
            self.dependency_frame_id,
            int(self.payload_kind_bitmap),
            self.payload_frame_count,
            self.reserved4,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> FrameSubmitMetadata:
        (
            src_width,
            src_height,
            tile_width,
            tile_height,
            tile_count,
            section_count,
            frame_class,
            input_profile,
            tile_index_mode,
            reserved0,
            latency_budget_ms,
            target_fps_x100,
            retry_of_frame,
            tile_base_id,
            camera_bytes,
            tile_index_bytes,
            reserved1,
            reserved2,
            submit_mode,
            budget_policy,
            loss_tolerance_policy,
            reserved3,
            object_ref_mask,
            dependency_frame_id,
            payload_kind_bitmap,
            payload_frame_count,
            reserved4,
        ) = values
        return cls(
            src_width=src_width,
            src_height=src_height,
            tile_width=tile_width,
            tile_height=tile_height,
            tile_count=tile_count,
            section_count=section_count,
            frame_class=frame_class,
            input_profile=InputProfile(input_profile),
            tile_index_mode=TileIndexMode(tile_index_mode),
            reserved0=reserved0,
            latency_budget_ms=latency_budget_ms,
            target_fps_x100=target_fps_x100,
            retry_of_frame=retry_of_frame,
            tile_base_id=tile_base_id,
            camera_bytes=camera_bytes,
            tile_index_bytes=tile_index_bytes,
            reserved1=reserved1,
            reserved2=reserved2,
            submit_mode=SubmitMode(submit_mode),
            budget_policy=_coerce_budget_policy(budget_policy),
            loss_tolerance_policy=_coerce_loss_tolerance_policy(loss_tolerance_policy),
            reserved3=reserved3,
            object_ref_mask=object_ref_mask,
            dependency_frame_id=dependency_frame_id,
            payload_kind_bitmap=_coerce_payload_kind_bitmap(payload_kind_bitmap),
            payload_frame_count=payload_frame_count,
            reserved4=reserved4,
        )


@dataclass(slots=True)
class TensorSectionDesc(_FixedWidthMetadata):
    """Fixed-width descriptor for one tensor section."""

    STRUCT: ClassVar[struct.Struct] = TENSOR_SECTION_DESC_STRUCT

    role_id: int
    codec_id: int
    dtype_id: TensorDType
    layout_id: TensorLayout
    scale_policy: ScalePolicy
    flags: SectionFlags
    element_count_per_tile: int
    codec_table_bytes: int
    length_table_bytes: int
    payload_bytes: int
    payload_stride_bytes: int
    reserved: int = 0

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.role_id,
            self.codec_id,
            int(self.dtype_id),
            int(self.layout_id),
            int(self.scale_policy),
            int(self.flags),
            self.element_count_per_tile,
            self.codec_table_bytes,
            self.length_table_bytes,
            self.payload_bytes,
            self.payload_stride_bytes,
            self.reserved,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> TensorSectionDesc:
        (
            role_id,
            codec_id,
            dtype_id,
            layout_id,
            scale_policy,
            flags,
            element_count_per_tile,
            codec_table_bytes,
            length_table_bytes,
            payload_bytes,
            payload_stride_bytes,
            reserved,
        ) = values
        return cls(
            role_id=role_id,
            codec_id=codec_id,
            dtype_id=TensorDType(dtype_id),
            layout_id=TensorLayout(layout_id),
            scale_policy=ScalePolicy(scale_policy),
            flags=SectionFlags(flags),
            element_count_per_tile=element_count_per_tile,
            codec_table_bytes=codec_table_bytes,
            length_table_bytes=length_table_bytes,
            payload_bytes=payload_bytes,
            payload_stride_bytes=payload_stride_bytes,
            reserved=reserved,
        )


@dataclass(slots=True)
class ResultPushMetadata(_FixedWidthMetadata):
    """Aligned metadata for RESULT_PUSH."""

    STRUCT: ClassVar[struct.Struct] = RESULT_PUSH_STRUCT

    status_code: int
    result_flags: ResultFlags
    section_count: int
    tile_count: int
    active_profile_id: int
    reserved0: int
    inference_ms: int
    queue_ms: int
    server_total_ms: int
    reserved1: int
    tile_base_id: int
    tile_index_bytes: int
    reserved2: int = 0
    reserved3: int = 0
    result_class: ResultClass = ResultClass.COMPLETE
    applied_budget_policy: BudgetPolicy = BudgetPolicy.NONE
    reserved4: int = 0
    reused_frame_id: int = 0
    covered_tile_count: int = 0
    dropped_tile_count: int = 0
    payload_kind_bitmap: PayloadKind = PayloadKind.TENSOR
    payload_frame_count: int = 0
    reserved5: int = 0

    def __post_init__(self) -> None:
        self.result_flags = _coerce_result_flags(self.result_flags)
        self.result_class = ResultClass(self.result_class)
        self.applied_budget_policy = _coerce_budget_policy(self.applied_budget_policy)
        self.payload_kind_bitmap = _coerce_payload_kind_bitmap(self.payload_kind_bitmap)
        _validate_non_tensor_result_fields(self)

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.status_code,
            int(self.result_flags),
            self.section_count,
            self.tile_count,
            self.active_profile_id,
            self.reserved0,
            self.inference_ms,
            self.queue_ms,
            self.server_total_ms,
            self.reserved1,
            self.tile_base_id,
            self.tile_index_bytes,
            self.reserved2,
            self.reserved3,
            int(self.result_class),
            int(self.applied_budget_policy),
            self.reserved4,
            self.reused_frame_id,
            self.covered_tile_count,
            self.dropped_tile_count,
            int(self.payload_kind_bitmap),
            self.payload_frame_count,
            self.reserved5,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> ResultPushMetadata:
        (
            status_code,
            result_flags,
            section_count,
            tile_count,
            active_profile_id,
            reserved0,
            inference_ms,
            queue_ms,
            server_total_ms,
            reserved1,
            tile_base_id,
            tile_index_bytes,
            reserved2,
            reserved3,
            result_class,
            applied_budget_policy,
            reserved4,
            reused_frame_id,
            covered_tile_count,
            dropped_tile_count,
            payload_kind_bitmap,
            payload_frame_count,
            reserved5,
        ) = values
        return cls(
            status_code=status_code,
            result_flags=_coerce_result_flags(result_flags),
            section_count=section_count,
            tile_count=tile_count,
            active_profile_id=active_profile_id,
            reserved0=reserved0,
            inference_ms=inference_ms,
            queue_ms=queue_ms,
            server_total_ms=server_total_ms,
            reserved1=reserved1,
            tile_base_id=tile_base_id,
            tile_index_bytes=tile_index_bytes,
            reserved2=reserved2,
            reserved3=reserved3,
            result_class=ResultClass(result_class),
            applied_budget_policy=_coerce_budget_policy(applied_budget_policy),
            reserved4=reserved4,
            reused_frame_id=reused_frame_id,
            covered_tile_count=covered_tile_count,
            dropped_tile_count=dropped_tile_count,
            payload_kind_bitmap=_coerce_payload_kind_bitmap(payload_kind_bitmap),
            payload_frame_count=payload_frame_count,
            reserved5=reserved5,
        )
