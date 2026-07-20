"""NNRP packet and tensor body helpers."""

from __future__ import annotations

import struct
from collections.abc import Iterable, Sequence, Set
from dataclasses import dataclass

from nnrp.core.enums import HeaderFlags, MessageType, WireFormat
from nnrp.core.header import HEADER_LENGTH, NnrpHeader
from nnrp.core.messages.control import (
    CacheObjectKind,
    FlowUpdateMetadata,
    FlowUpdateScopeKind,
    PayloadKind,
    ResultHintMetadata,
    SessionMigrateAckMetadata,
    SessionMigrateMetadata,
    TransportProbeAckMetadata,
    TransportProbeMetadata,
)
from nnrp.core.messages.data import (
    BODY_REGION_PRELUDE_LENGTH,
    EXTENSION_FRAME_DESCRIPTOR_LENGTH,
    INLINE_OBJECT_BLOCK_HEADER_LENGTH,
    OBJECT_REFERENCE_BLOCK_LENGTH,
    TENSOR_SECTION_DESC_LENGTH,
    TYPED_PAYLOAD_DESCRIPTOR_LENGTH,
    BodyRegionPrelude,
    BudgetPolicy,
    ExtensionFrameDescriptor,
    ExtensionFrameFlags,
    FrameSubmitMetadata,
    InlineObjectBlockHeader,
    InputProfile,
    ObjectReferenceBlock,
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

LENGTH_ENTRY_STRUCT = struct.Struct("<I")
TILE_ID_ENTRY_STRUCT = struct.Struct("<H")
BLOCK_ALIGNMENT = 8

_FRAME_SUBMIT_STANDARD_REFERENCE_SLOTS = (
    (1 << 0, CacheObjectKind.CAMERA_BLOCK),
    (1 << 1, CacheObjectKind.TILE_INDEX_BLOCK),
    (1 << 2, CacheObjectKind.TENSOR_SECTION_TABLE),
    (1 << 3, CacheObjectKind.PAYLOAD_LAYOUT_TEMPLATE),
)
_FRAME_SUBMIT_KNOWN_OBJECT_REF_MASK = sum(bit for bit, _kind in _FRAME_SUBMIT_STANDARD_REFERENCE_SLOTS)

_HEADER_ONLY_CONTROL_TYPES = frozenset(
    {
        MessageType.FRAME_CANCEL,
        MessageType.PING,
        MessageType.PONG,
    }
)


@dataclass(frozen=True, slots=True)
class TensorSectionData:
    """Logical tensor section payloads before they are packed on wire."""

    role_id: int
    default_codec_id: int
    dtype_id: TensorDType
    tile_payloads: tuple[bytes, ...]
    codec_ids: tuple[int, ...] = ()
    layout_id: TensorLayout = TensorLayout.NHWC
    scale_policy: ScalePolicy = ScalePolicy.NONE
    payload_stride_bytes: int = 0
    element_count_per_tile: int = 0

    def normalized_tile_payloads(self) -> tuple[bytes, ...]:
        return tuple(bytes(payload) for payload in self.tile_payloads)

    def normalized_codec_ids(self) -> tuple[int, ...]:
        return tuple(int(codec_id) for codec_id in self.codec_ids)


@dataclass(slots=True)
class NnrpPacket:
    """One complete NNRP packet, including header, metadata, and body."""

    header: NnrpHeader
    metadata: bytes = b""
    body: bytes = b""

    def pack(self) -> bytes:
        if len(self.metadata) != self.header.meta_len:
            raise ValueError(f"metadata length mismatch: header={self.header.meta_len}, actual={len(self.metadata)}")
        if len(self.body) != self.header.body_len:
            raise ValueError(f"body length mismatch: header={self.header.body_len}, actual={len(self.body)}")
        return self.header.pack() + self.metadata + self.body

    @classmethod
    def build(
        cls,
        *,
        version_major: int,
        wire_format: WireFormat,
        msg_type: MessageType,
        flags: HeaderFlags = HeaderFlags.NONE,
        session_id: int = 0,
        frame_id: int = 0,
        view_id: int = 0,
        route_id: int = 0,
        trace_id: int = 0,
        metadata: bytes = b"",
        body: bytes = b"",
    ) -> NnrpPacket:
        return cls(
            header=NnrpHeader(
                version_major=version_major,
                wire_format=wire_format,
                msg_type=msg_type,
                flags=flags,
                meta_len=len(metadata),
                body_len=len(body),
                session_id=session_id,
                frame_id=frame_id,
                view_id=view_id,
                route_id=route_id,
                trace_id=trace_id,
            ),
            metadata=metadata,
            body=body,
        )

    @classmethod
    def unpack(cls, payload: bytes) -> NnrpPacket:
        if len(payload) < HEADER_LENGTH:
            raise ValueError(f"expected at least {HEADER_LENGTH} bytes, got {len(payload)}")

        header = NnrpHeader.unpack(payload[:HEADER_LENGTH])
        expected_length = HEADER_LENGTH + header.meta_len + header.body_len
        if len(payload) != expected_length:
            raise ValueError(f"expected {expected_length} bytes, got {len(payload)}")

        metadata_start = HEADER_LENGTH
        body_start = metadata_start + header.meta_len
        return cls(
            header=header,
            metadata=payload[metadata_start:body_start],
            body=payload[body_start:],
        )


def _align_up(value: int, alignment: int = BLOCK_ALIGNMENT) -> int:
    if alignment <= 0:
        raise ValueError(f"alignment must be positive, got {alignment}")
    return ((value + alignment - 1) // alignment) * alignment


def _append_zero_padding(buffer: bytearray, *, alignment: int = BLOCK_ALIGNMENT) -> None:
    padded_length = _align_up(len(buffer), alignment)
    if padded_length > len(buffer):
        buffer.extend(b"\x00" * (padded_length - len(buffer)))


def _validate_zero_padding(payload: memoryview, start: int, end: int) -> None:
    if end <= start:
        return
    if any(payload[start:end]):
        raise ValueError("padding bytes must be zero")


def build_header_only_packet(
    *,
    msg_type: MessageType,
    session_id: int = 0,
    frame_id: int = 0,
    view_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
    flags: HeaderFlags = HeaderFlags.NONE,
) -> NnrpPacket:
    if msg_type not in _HEADER_ONLY_CONTROL_TYPES:
        raise ValueError(f"expected header-only control packet type, got {msg_type.name}")

    return NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=msg_type,
        flags=flags,
        session_id=session_id,
        frame_id=frame_id,
        view_id=view_id,
        route_id=route_id,
        trace_id=trace_id,
    )


def build_frame_cancel_packet(
    *,
    session_id: int,
    frame_id: int,
    view_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
    flags: HeaderFlags = HeaderFlags.NONE,
) -> NnrpPacket:
    return build_header_only_packet(
        msg_type=MessageType.FRAME_CANCEL,
        session_id=session_id,
        frame_id=frame_id,
        view_id=view_id,
        route_id=route_id,
        trace_id=trace_id,
        flags=flags,
    )


def build_ping_packet(
    *,
    session_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
    flags: HeaderFlags = HeaderFlags.NONE,
) -> NnrpPacket:
    return build_header_only_packet(
        msg_type=MessageType.PING,
        session_id=session_id,
        route_id=route_id,
        trace_id=trace_id,
        flags=flags,
    )


def build_pong_packet(
    *,
    session_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
    flags: HeaderFlags = HeaderFlags.NONE,
) -> NnrpPacket:
    return build_header_only_packet(
        msg_type=MessageType.PONG,
        session_id=session_id,
        route_id=route_id,
        trace_id=trace_id,
        flags=flags,
    )


def build_result_drop_packet(
    *,
    session_id: int,
    frame_id: int,
    view_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
    flags: HeaderFlags = HeaderFlags.NONE,
) -> NnrpPacket:
    return NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.RESULT_DROP,
        flags=flags,
        session_id=session_id,
        frame_id=frame_id,
        view_id=view_id,
        route_id=route_id,
        trace_id=trace_id,
    )


def build_flow_update_packet(
    *,
    metadata: FlowUpdateMetadata,
    session_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
    flags: HeaderFlags = HeaderFlags.NONE,
) -> NnrpPacket:
    if metadata.scope_kind is FlowUpdateScopeKind.CONNECTION and session_id != 0:
        raise ValueError("connection-scope FLOW_UPDATE requires session_id=0")
    if metadata.scope_kind in {FlowUpdateScopeKind.SESSION, FlowUpdateScopeKind.OPERATION} and session_id == 0:
        raise ValueError("session-scope and operation-scope FLOW_UPDATE require non-zero session_id")
    return NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.FLOW_UPDATE,
        flags=flags,
        session_id=session_id,
        route_id=route_id,
        trace_id=trace_id,
        metadata=metadata.pack(),
        body=b"",
    )


def build_result_hint_packet(
    *,
    metadata: ResultHintMetadata,
    session_id: int = 0,
    frame_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
    flags: HeaderFlags = HeaderFlags.NONE,
) -> NnrpPacket:
    return NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.RESULT_HINT,
        flags=flags,
        session_id=session_id,
        frame_id=frame_id,
        route_id=route_id,
        trace_id=trace_id,
        metadata=metadata.pack(),
        body=b"",
    )


def build_transport_probe_packet(
    *,
    metadata: TransportProbeMetadata,
    body: bytes,
    session_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
    flags: HeaderFlags = HeaderFlags.NONE,
) -> NnrpPacket:
    payload = bytes(body)
    if metadata.probe_payload_bytes != len(payload):
        raise ValueError(
            f"probe payload length mismatch: metadata={metadata.probe_payload_bytes}, actual={len(payload)}"
        )

    return NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.TRANSPORT_PROBE,
        flags=flags,
        session_id=session_id,
        route_id=route_id,
        trace_id=trace_id,
        metadata=metadata.pack(),
        body=payload,
    )


def build_transport_probe_ack_packet(
    *,
    metadata: TransportProbeAckMetadata,
    session_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
    flags: HeaderFlags = HeaderFlags.NONE,
) -> NnrpPacket:
    return NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.TRANSPORT_PROBE_ACK,
        flags=flags,
        session_id=session_id,
        route_id=route_id,
        trace_id=trace_id,
        metadata=metadata.pack(),
        body=b"",
    )


def build_session_migrate_packet(
    *,
    metadata: SessionMigrateMetadata,
    session_id: int,
    route_id: int = 0,
    trace_id: int = 0,
    flags: HeaderFlags = HeaderFlags.NONE,
    body: bytes = b"",
) -> NnrpPacket:
    return NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.SESSION_MIGRATE,
        flags=flags,
        session_id=session_id,
        route_id=route_id,
        trace_id=trace_id,
        metadata=metadata.pack(),
        body=bytes(body),
    )


def build_session_migrate_ack_packet(
    *,
    metadata: SessionMigrateAckMetadata,
    session_id: int,
    route_id: int = 0,
    trace_id: int = 0,
    flags: HeaderFlags = HeaderFlags.NONE,
    body: bytes = b"",
) -> NnrpPacket:
    return NnrpPacket.build(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.SESSION_MIGRATE_ACK,
        flags=flags,
        session_id=session_id,
        route_id=route_id,
        trace_id=trace_id,
        metadata=metadata.pack(),
        body=bytes(body),
    )


@dataclass(frozen=True, slots=True)
class TensorSectionView:
    """Zero-copy views into one tensor section."""

    desc: TensorSectionDesc
    codec_table: memoryview
    length_table: memoryview
    payload: memoryview

    @property
    def total_bytes(self) -> int:
        return TENSOR_SECTION_DESC_LENGTH + len(self.codec_table) + len(self.length_table) + len(self.payload)

    def tile_lengths(self) -> tuple[int, ...]:
        if len(self.length_table) % LENGTH_ENTRY_STRUCT.size != 0:
            raise ValueError("length_table_bytes must be a multiple of 4")

        return tuple(
            LENGTH_ENTRY_STRUCT.unpack_from(self.length_table, offset)[0]
            for offset in range(0, len(self.length_table), LENGTH_ENTRY_STRUCT.size)
        )

    def payload_slices(self) -> tuple[memoryview, ...]:
        lengths = self.tile_lengths()
        if self.desc.payload_stride_bytes:
            expected_bytes = self.desc.payload_stride_bytes * len(lengths)
            if len(self.payload) != expected_bytes:
                raise ValueError(
                    f"expected payload blob of {expected_bytes} bytes for fixed stride, got {len(self.payload)}"
                )

            slices = []
            for index, item_length in enumerate(lengths):
                if item_length > self.desc.payload_stride_bytes:
                    raise ValueError(f"tile length {item_length} exceeds stride {self.desc.payload_stride_bytes}")
                start = index * self.desc.payload_stride_bytes
                end = start + self.desc.payload_stride_bytes
                padding = self.payload[start + item_length : end]
                if any(padding):
                    raise ValueError("fixed-stride payload padding must be zero")
                slices.append(self.payload[start : start + item_length])
            return tuple(slices)

        total_length = sum(lengths)
        if total_length != len(self.payload):
            raise ValueError(f"expected payload blob of {total_length} bytes, got {len(self.payload)}")

        slices = []
        cursor = 0
        for item_length in lengths:
            slices.append(self.payload[cursor : cursor + item_length])
            cursor += item_length
        return tuple(slices)


@dataclass(frozen=True, slots=True)
class InlineObjectBlockView:
    """Zero-copy view into one aligned inline object block."""

    header: InlineObjectBlockHeader
    payload: memoryview


@dataclass(frozen=True, slots=True)
class BodyView:
    """Zero-copy view into one composed body made of ordered regions."""

    prelude: BodyRegionPrelude
    inline_object_region: memoryview
    object_reference_region: memoryview
    typed_payload_descriptor_region: memoryview
    typed_payload_frame_region: memoryview
    extension_descriptor_region: memoryview
    extension_payload_region: memoryview


@dataclass(frozen=True, slots=True)
class TypedPayloadFrame:
    """Logical typed payload frame before or after descriptor-table packing."""

    payload_kind: PayloadKind
    payload: bytes
    profile_id: int = 0
    descriptor_flags: int = 0


@dataclass(frozen=True, slots=True)
class ExtensionFrame:
    """Logical extension frame before or after descriptor-table packing."""

    extension_kind: int
    payload: bytes
    profile_id: int = 0
    extension_flags: ExtensionFrameFlags = ExtensionFrameFlags.NONE


@dataclass(frozen=True, slots=True)
class TensorBodyView:
    """Zero-copy view into a frame/result body with tile index block and sections."""

    tile_index_block: memoryview
    sections: tuple[TensorSectionView, ...]


def pack_tile_index_block(
    tile_ids: Iterable[int],
    *,
    mode: TileIndexMode,
    tile_base_id: int = 0,
) -> bytes:
    ordered_ids = tuple(int(tile_id) for tile_id in tile_ids)
    _validate_tile_ids(ordered_ids)

    if mode is TileIndexMode.DENSE_RANGE:
        expected = tuple(range(tile_base_id, tile_base_id + len(ordered_ids)))
        if ordered_ids != expected:
            raise ValueError(
                "dense_range tile ids must be contiguous and start at tile_base_id: "
                f"tile_base_id={tile_base_id} tile_ids={ordered_ids}"
            )
        return b""

    if mode is TileIndexMode.RAW_U16:
        return b"".join(_pack_u16(tile_id) for tile_id in ordered_ids)

    _validate_strictly_increasing_tile_ids(ordered_ids)
    if mode is TileIndexMode.DELTA_U16:
        if not ordered_ids:
            return b""

        payload = bytearray()
        previous_tile_id: int | None = None
        for tile_id in ordered_ids:
            value = tile_id if previous_tile_id is None else tile_id - previous_tile_id
            if value < 0 or value > 0xFFFF:
                raise ValueError(f"tile delta out of u16 range: {value}")
            payload.extend(_pack_u16(value))
            previous_tile_id = tile_id
        return bytes(payload)

    if mode is TileIndexMode.BITSET:
        if not ordered_ids:
            return b""

        payload = bytearray((ordered_ids[-1] // 8) + 1)
        for tile_id in ordered_ids:
            payload[tile_id // 8] |= 1 << (tile_id % 8)
        return bytes(payload)

    raise ValueError(f"unsupported tile index mode: {mode}")


def pack_inline_object_block(header: InlineObjectBlockHeader, payload: bytes) -> bytes:
    body = bytes(payload)
    if len(body) != header.object_bytes:
        raise ValueError(f"inline object payload length mismatch: expected {header.object_bytes}, got {len(body)}")

    buffer = bytearray(header.pack())
    buffer.extend(body)
    _append_zero_padding(buffer)
    return bytes(buffer)


def unpack_inline_object_block(payload: bytes | memoryview) -> InlineObjectBlockView:
    view = memoryview(payload)
    if len(view) < INLINE_OBJECT_BLOCK_HEADER_LENGTH:
        raise ValueError(
            f"expected at least {INLINE_OBJECT_BLOCK_HEADER_LENGTH} bytes for inline object block, got {len(view)}"
        )

    header = InlineObjectBlockHeader.unpack(view[:INLINE_OBJECT_BLOCK_HEADER_LENGTH])
    payload_end = INLINE_OBJECT_BLOCK_HEADER_LENGTH + header.object_bytes
    if len(view) < payload_end:
        raise ValueError(f"expected inline object payload to end at {payload_end} bytes, got {len(view)}")

    aligned_end = _align_up(payload_end)
    if len(view) != aligned_end:
        raise ValueError(f"expected {aligned_end} inline object block bytes, got {len(view)}")

    _validate_zero_padding(view, payload_end, aligned_end)
    return InlineObjectBlockView(
        header=header,
        payload=view[INLINE_OBJECT_BLOCK_HEADER_LENGTH:payload_end],
    )


def unpack_inline_object_blocks(
    payload: bytes | memoryview,
) -> tuple[InlineObjectBlockView, ...]:
    return tuple(unpack_inline_object_block(block) for block in _split_inline_object_region(payload))


def build_camera_inline_object_block(
    payload: bytes,
    *,
    profile_id: int = 0,
) -> bytes:
    return _build_standard_inline_object_block(
        object_kind=CacheObjectKind.CAMERA_BLOCK,
        payload=payload,
        profile_id=profile_id,
    )


def build_tile_index_inline_object_block(
    tile_ids: Iterable[int],
    *,
    mode: TileIndexMode,
    tile_base_id: int = 0,
    profile_id: int = 0,
) -> bytes:
    return _build_standard_inline_object_block(
        object_kind=CacheObjectKind.TILE_INDEX_BLOCK,
        payload=pack_tile_index_block(tile_ids, mode=mode, tile_base_id=tile_base_id),
        profile_id=profile_id,
    )


def build_tensor_section_table_inline_object_block(
    payload: bytes,
    *,
    profile_id: int = 0,
) -> bytes:
    return _build_standard_inline_object_block(
        object_kind=CacheObjectKind.TENSOR_SECTION_TABLE,
        payload=payload,
        profile_id=profile_id,
    )


def parse_camera_inline_object_block(
    payload: bytes | memoryview,
) -> InlineObjectBlockView:
    return _parse_standard_inline_object_block(payload, expected_kind=CacheObjectKind.CAMERA_BLOCK)


def parse_tile_index_inline_object_block(
    payload: bytes | memoryview,
) -> InlineObjectBlockView:
    return _parse_standard_inline_object_block(payload, expected_kind=CacheObjectKind.TILE_INDEX_BLOCK)


def parse_tensor_section_table_inline_object_block(
    payload: bytes | memoryview,
) -> InlineObjectBlockView:
    return _parse_standard_inline_object_block(payload, expected_kind=CacheObjectKind.TENSOR_SECTION_TABLE)


def pack_object_reference_blocks(blocks: Sequence[ObjectReferenceBlock]) -> bytes:
    return b"".join(block.pack() for block in blocks)


def unpack_object_reference_blocks(
    payload: bytes | memoryview,
) -> tuple[ObjectReferenceBlock, ...]:
    view = memoryview(payload)
    if len(view) % OBJECT_REFERENCE_BLOCK_LENGTH != 0:
        raise ValueError(
            "object reference block payload length must be a multiple of "
            f"{OBJECT_REFERENCE_BLOCK_LENGTH}, got {len(view)}"
        )

    return tuple(
        ObjectReferenceBlock.unpack(view[offset : offset + OBJECT_REFERENCE_BLOCK_LENGTH])
        for offset in range(0, len(view), OBJECT_REFERENCE_BLOCK_LENGTH)
    )


def build_camera_reference_block(*, cache_namespace: int, cache_key_hi: int, cache_key_lo: int) -> ObjectReferenceBlock:
    return _build_standard_object_reference_block(
        object_kind=CacheObjectKind.CAMERA_BLOCK,
        cache_namespace=cache_namespace,
        cache_key_hi=cache_key_hi,
        cache_key_lo=cache_key_lo,
    )


def build_tile_index_reference_block(
    *,
    cache_namespace: int,
    cache_key_hi: int,
    cache_key_lo: int,
) -> ObjectReferenceBlock:
    return _build_standard_object_reference_block(
        object_kind=CacheObjectKind.TILE_INDEX_BLOCK,
        cache_namespace=cache_namespace,
        cache_key_hi=cache_key_hi,
        cache_key_lo=cache_key_lo,
    )


def build_tensor_section_table_reference_block(
    *,
    cache_namespace: int,
    cache_key_hi: int,
    cache_key_lo: int,
) -> ObjectReferenceBlock:
    return _build_standard_object_reference_block(
        object_kind=CacheObjectKind.TENSOR_SECTION_TABLE,
        cache_namespace=cache_namespace,
        cache_key_hi=cache_key_hi,
        cache_key_lo=cache_key_lo,
    )


def parse_camera_reference_block(
    payload: ObjectReferenceBlock | bytes,
) -> ObjectReferenceBlock:
    return _parse_standard_object_reference_block(payload, expected_kind=CacheObjectKind.CAMERA_BLOCK)


def parse_tile_index_reference_block(
    payload: ObjectReferenceBlock | bytes,
) -> ObjectReferenceBlock:
    return _parse_standard_object_reference_block(payload, expected_kind=CacheObjectKind.TILE_INDEX_BLOCK)


def parse_tensor_section_table_reference_block(
    payload: ObjectReferenceBlock | bytes,
) -> ObjectReferenceBlock:
    return _parse_standard_object_reference_block(payload, expected_kind=CacheObjectKind.TENSOR_SECTION_TABLE)


def build_typed_payload_frame(
    payload_kind: PayloadKind | int,
    payload: bytes,
    *,
    profile_id: int = 0,
    descriptor_flags: int = 0,
) -> TypedPayloadFrame:
    body = bytes(payload)
    descriptor = TypedPayloadDescriptor(
        payload_kind=payload_kind,
        descriptor_flags=descriptor_flags,
        profile_id=profile_id,
        payload_offset=0,
        payload_length=len(body),
    )
    if descriptor.payload_kind is PayloadKind.TENSOR:
        raise ValueError("tensor payloads must be encoded through tensor body blocks, not typed payload frames")
    return TypedPayloadFrame(
        payload_kind=descriptor.payload_kind,
        payload=body,
        profile_id=descriptor.profile_id,
        descriptor_flags=descriptor.descriptor_flags,
    )


def parse_typed_payload_frame(
    frame: TypedPayloadFrame,
    *,
    expected_kind: PayloadKind | int | None = None,
) -> TypedPayloadFrame:
    normalized = build_typed_payload_frame(
        frame.payload_kind,
        frame.payload,
        profile_id=frame.profile_id,
        descriptor_flags=frame.descriptor_flags,
    )
    if expected_kind is not None:
        expected = TypedPayloadDescriptor(
            payload_kind=expected_kind,
            descriptor_flags=0,
            profile_id=0,
            payload_offset=0,
            payload_length=0,
        ).payload_kind
        if normalized.payload_kind is not expected:
            raise ValueError(f"expected {expected.name} typed payload frame, got {normalized.payload_kind.name}")
    return normalized


def build_token_chunk_frame(payload: bytes, *, profile_id: int = 0) -> TypedPayloadFrame:
    return build_typed_payload_frame(PayloadKind.TOKEN_CHUNK, payload, profile_id=profile_id)


def build_audio_chunk_frame(payload: bytes, *, profile_id: int = 0) -> TypedPayloadFrame:
    return build_typed_payload_frame(PayloadKind.AUDIO_CHUNK, payload, profile_id=profile_id)


def build_video_chunk_frame(payload: bytes, *, profile_id: int = 0) -> TypedPayloadFrame:
    return build_typed_payload_frame(PayloadKind.VIDEO_CHUNK, payload, profile_id=profile_id)


def build_structured_event_frame(payload: bytes, *, profile_id: int = 0) -> TypedPayloadFrame:
    return build_typed_payload_frame(PayloadKind.STRUCTURED_EVENT, payload, profile_id=profile_id)


def build_tool_delta_frame(payload: bytes, *, profile_id: int = 0) -> TypedPayloadFrame:
    return build_typed_payload_frame(PayloadKind.TOOL_DELTA, payload, profile_id=profile_id)


def build_opaque_bytes_frame(payload: bytes, *, profile_id: int = 0) -> TypedPayloadFrame:
    return build_typed_payload_frame(PayloadKind.OPAQUE_BYTES, payload, profile_id=profile_id)


def parse_token_chunk_frame(frame: TypedPayloadFrame) -> TypedPayloadFrame:
    return parse_typed_payload_frame(frame, expected_kind=PayloadKind.TOKEN_CHUNK)


def parse_audio_chunk_frame(frame: TypedPayloadFrame) -> TypedPayloadFrame:
    return parse_typed_payload_frame(frame, expected_kind=PayloadKind.AUDIO_CHUNK)


def parse_video_chunk_frame(frame: TypedPayloadFrame) -> TypedPayloadFrame:
    return parse_typed_payload_frame(frame, expected_kind=PayloadKind.VIDEO_CHUNK)


def parse_structured_event_frame(frame: TypedPayloadFrame) -> TypedPayloadFrame:
    return parse_typed_payload_frame(frame, expected_kind=PayloadKind.STRUCTURED_EVENT)


def parse_tool_delta_frame(frame: TypedPayloadFrame) -> TypedPayloadFrame:
    return parse_typed_payload_frame(frame, expected_kind=PayloadKind.TOOL_DELTA)


def parse_opaque_bytes_frame(frame: TypedPayloadFrame) -> TypedPayloadFrame:
    return parse_typed_payload_frame(frame, expected_kind=PayloadKind.OPAQUE_BYTES)


def pack_typed_payload_descriptors(
    descriptors: Sequence[TypedPayloadDescriptor],
) -> bytes:
    return b"".join(descriptor.pack() for descriptor in descriptors)


def unpack_typed_payload_descriptors(
    payload: bytes | memoryview,
) -> tuple[TypedPayloadDescriptor, ...]:
    view = memoryview(payload)
    if len(view) % TYPED_PAYLOAD_DESCRIPTOR_LENGTH != 0:
        raise ValueError(
            f"typed payload descriptor bytes must be a multiple of {TYPED_PAYLOAD_DESCRIPTOR_LENGTH}, got {len(view)}"
        )

    return tuple(
        TypedPayloadDescriptor.unpack(view[offset : offset + TYPED_PAYLOAD_DESCRIPTOR_LENGTH])
        for offset in range(0, len(view), TYPED_PAYLOAD_DESCRIPTOR_LENGTH)
    )


def pack_typed_payload_frames(
    frames: Sequence[TypedPayloadFrame],
) -> tuple[bytes, bytes]:
    descriptors: list[TypedPayloadDescriptor] = []
    payload_region = bytearray()
    payload_offset = 0

    for frame in frames:
        normalized = build_typed_payload_frame(
            frame.payload_kind,
            frame.payload,
            profile_id=frame.profile_id,
            descriptor_flags=frame.descriptor_flags,
        )
        descriptors.append(
            TypedPayloadDescriptor(
                payload_kind=normalized.payload_kind,
                descriptor_flags=normalized.descriptor_flags,
                profile_id=normalized.profile_id,
                payload_offset=payload_offset,
                payload_length=len(normalized.payload),
            )
        )
        payload_region.extend(normalized.payload)
        payload_offset += len(normalized.payload)

    return pack_typed_payload_descriptors(descriptors), bytes(payload_region)


def unpack_typed_payload_frames(
    descriptor_region: bytes | memoryview,
    payload_region: bytes | memoryview,
) -> tuple[TypedPayloadFrame, ...]:
    descriptors = unpack_typed_payload_descriptors(descriptor_region)
    payload_view = memoryview(payload_region)
    _validate_descriptor_offsets(
        descriptors,
        payload_region_length=len(payload_view),
        label="typed payload frame region",
    )
    return tuple(
        TypedPayloadFrame(
            payload_kind=descriptor.payload_kind,
            payload=bytes(
                payload_view[descriptor.payload_offset : descriptor.payload_offset + descriptor.payload_length]
            ),
            profile_id=descriptor.profile_id,
            descriptor_flags=descriptor.descriptor_flags,
        )
        for descriptor in descriptors
    )


def pack_extension_frame_descriptors(
    descriptors: Sequence[ExtensionFrameDescriptor],
) -> bytes:
    return b"".join(descriptor.pack() for descriptor in descriptors)


def unpack_extension_frame_descriptors(
    payload: bytes | memoryview,
) -> tuple[ExtensionFrameDescriptor, ...]:
    view = memoryview(payload)
    if len(view) % EXTENSION_FRAME_DESCRIPTOR_LENGTH != 0:
        raise ValueError(
            "extension frame descriptor bytes must be a multiple of "
            f"{EXTENSION_FRAME_DESCRIPTOR_LENGTH}, got {len(view)}"
        )

    return tuple(
        ExtensionFrameDescriptor.unpack(view[offset : offset + EXTENSION_FRAME_DESCRIPTOR_LENGTH])
        for offset in range(0, len(view), EXTENSION_FRAME_DESCRIPTOR_LENGTH)
    )


def build_extension_frame(
    extension_kind: int,
    payload: bytes,
    *,
    profile_id: int = 0,
    extension_flags: ExtensionFrameFlags | int = ExtensionFrameFlags.NONE,
) -> ExtensionFrame:
    body = bytes(payload)
    descriptor = ExtensionFrameDescriptor(
        extension_kind=extension_kind,
        extension_flags=extension_flags,
        profile_id=profile_id,
        reserved0=0,
        payload_offset=0,
        payload_length=len(body),
    )
    return ExtensionFrame(
        extension_kind=descriptor.extension_kind,
        payload=body,
        profile_id=descriptor.profile_id,
        extension_flags=descriptor.extension_flags,
    )


def pack_extension_frames(frames: Sequence[ExtensionFrame]) -> tuple[bytes, bytes]:
    descriptors: list[ExtensionFrameDescriptor] = []
    payload_region = bytearray()
    payload_offset = 0

    for frame in frames:
        normalized = build_extension_frame(
            frame.extension_kind,
            frame.payload,
            profile_id=frame.profile_id,
            extension_flags=frame.extension_flags,
        )
        descriptors.append(
            ExtensionFrameDescriptor(
                extension_kind=normalized.extension_kind,
                extension_flags=normalized.extension_flags,
                profile_id=normalized.profile_id,
                reserved0=0,
                payload_offset=payload_offset,
                payload_length=len(normalized.payload),
            )
        )
        payload_region.extend(normalized.payload)
        payload_offset += len(normalized.payload)

    return pack_extension_frame_descriptors(descriptors), bytes(payload_region)


def unpack_extension_frames(
    descriptor_region: bytes | memoryview,
    payload_region: bytes | memoryview,
    *,
    known_extension_kinds: Set[int] | None = None,
) -> tuple[ExtensionFrame, ...]:
    descriptors = unpack_extension_frame_descriptors(descriptor_region)
    payload_view = memoryview(payload_region)
    _validate_descriptor_offsets(
        descriptors,
        payload_region_length=len(payload_view),
        label="extension payload region",
    )

    frames: list[ExtensionFrame] = []
    for descriptor in descriptors:
        if known_extension_kinds is not None and descriptor.extension_kind not in known_extension_kinds:
            if descriptor.extension_flags & ExtensionFrameFlags.CRITICAL:
                raise ValueError(f"unknown critical extension frame kind: {descriptor.extension_kind}")
            continue
        frames.append(
            ExtensionFrame(
                extension_kind=descriptor.extension_kind,
                payload=bytes(
                    payload_view[descriptor.payload_offset : descriptor.payload_offset + descriptor.payload_length]
                ),
                profile_id=descriptor.profile_id,
                extension_flags=descriptor.extension_flags,
            )
        )

    return tuple(frames)


def pack_body(
    *,
    inline_object_region: bytes = b"",
    object_reference_region: bytes = b"",
    typed_payload_descriptor_region: bytes = b"",
    typed_payload_frame_region: bytes = b"",
    extension_descriptor_region: bytes = b"",
    extension_payload_region: bytes = b"",
) -> bytes:
    prelude = BodyRegionPrelude(
        inline_object_bytes=len(inline_object_region),
        object_reference_bytes=len(object_reference_region),
        typed_payload_descriptor_bytes=len(typed_payload_descriptor_region),
        typed_payload_frame_bytes=len(typed_payload_frame_region),
        extension_descriptor_bytes=len(extension_descriptor_region),
        extension_payload_bytes=len(extension_payload_region),
    )
    return b"".join(
        (
            prelude.pack(),
            bytes(inline_object_region),
            bytes(object_reference_region),
            bytes(typed_payload_descriptor_region),
            bytes(typed_payload_frame_region),
            bytes(extension_descriptor_region),
            bytes(extension_payload_region),
        )
    )


def unpack_body(body: bytes | memoryview) -> BodyView:
    view = memoryview(body)
    if len(view) < BODY_REGION_PRELUDE_LENGTH:
        raise ValueError(
            f"expected at least {BODY_REGION_PRELUDE_LENGTH} bytes for current body prelude, got {len(view)}"
        )

    prelude = BodyRegionPrelude.unpack(view[:BODY_REGION_PRELUDE_LENGTH])
    cursor = BODY_REGION_PRELUDE_LENGTH

    inline_end = cursor + prelude.inline_object_bytes
    object_reference_end = inline_end + prelude.object_reference_bytes
    typed_descriptor_end = object_reference_end + prelude.typed_payload_descriptor_bytes
    typed_frame_end = typed_descriptor_end + prelude.typed_payload_frame_bytes
    extension_descriptor_end = typed_frame_end + prelude.extension_descriptor_bytes
    extension_payload_end = extension_descriptor_end + prelude.extension_payload_bytes

    if extension_payload_end != len(view):
        raise ValueError(
            f"current body length mismatch: expected {extension_payload_end} bytes from prelude, got {len(view)}"
        )

    inline_object_region = view[cursor:inline_end]
    object_reference_region = view[inline_end:object_reference_end]
    typed_payload_descriptor_region = view[object_reference_end:typed_descriptor_end]
    typed_payload_frame_region = view[typed_descriptor_end:typed_frame_end]
    extension_descriptor_region = view[typed_frame_end:extension_descriptor_end]
    extension_payload_region = view[extension_descriptor_end:extension_payload_end]

    for payload in _split_inline_object_region(inline_object_region):
        unpack_inline_object_block(payload)
    unpack_object_reference_blocks(object_reference_region)
    descriptors = unpack_typed_payload_descriptors(typed_payload_descriptor_region)
    _validate_descriptor_offsets(
        descriptors,
        payload_region_length=len(typed_payload_frame_region),
        label="typed payload frame region",
    )
    extension_descriptors = unpack_extension_frame_descriptors(extension_descriptor_region)
    _validate_descriptor_offsets(
        extension_descriptors,
        payload_region_length=len(extension_payload_region),
        label="extension payload region",
    )

    return BodyView(
        prelude=prelude,
        inline_object_region=inline_object_region,
        object_reference_region=object_reference_region,
        typed_payload_descriptor_region=typed_payload_descriptor_region,
        typed_payload_frame_region=typed_payload_frame_region,
        extension_descriptor_region=extension_descriptor_region,
        extension_payload_region=extension_payload_region,
    )


def _split_inline_object_region(payload: bytes | memoryview) -> tuple[memoryview, ...]:
    view = memoryview(payload)
    cursor = 0
    blocks: list[memoryview] = []
    while cursor < len(view):
        if len(view) - cursor < INLINE_OBJECT_BLOCK_HEADER_LENGTH:
            raise ValueError(f"inline object region ends before a full block header: remaining={len(view) - cursor}")
        header = InlineObjectBlockHeader.unpack(view[cursor : cursor + INLINE_OBJECT_BLOCK_HEADER_LENGTH])
        total_bytes = _align_up(INLINE_OBJECT_BLOCK_HEADER_LENGTH + header.object_bytes)
        end = cursor + total_bytes
        if end > len(view):
            raise ValueError(
                f"inline object region ends before declared block payload: expected {end} bytes, got {len(view)}"
            )
        blocks.append(view[cursor:end])
        cursor = end
    return tuple(blocks)


def _build_standard_object_reference_block(
    *,
    object_kind: CacheObjectKind,
    cache_namespace: int,
    cache_key_hi: int,
    cache_key_lo: int,
) -> ObjectReferenceBlock:
    return ObjectReferenceBlock(
        object_kind=object_kind,
        ref_flags=0,
        cache_namespace=cache_namespace,
        cache_key_hi=cache_key_hi,
        cache_key_lo=cache_key_lo,
    )


def _parse_standard_object_reference_block(
    payload: ObjectReferenceBlock | bytes,
    *,
    expected_kind: CacheObjectKind,
) -> ObjectReferenceBlock:
    block = payload if isinstance(payload, ObjectReferenceBlock) else ObjectReferenceBlock.unpack(payload)
    if block.object_kind is not expected_kind:
        raise ValueError(f"expected {expected_kind.name} reference block, got {block.object_kind.name}")
    return block


def _build_standard_inline_object_block(
    *,
    object_kind: CacheObjectKind,
    payload: bytes,
    profile_id: int,
) -> bytes:
    body = bytes(payload)
    return pack_inline_object_block(
        InlineObjectBlockHeader(
            object_kind=object_kind,
            object_flags=0,
            profile_id=profile_id,
            reserved0=0,
            object_bytes=len(body),
        ),
        body,
    )


def _parse_standard_inline_object_block(
    payload: bytes | memoryview,
    *,
    expected_kind: CacheObjectKind,
) -> InlineObjectBlockView:
    block = unpack_inline_object_block(payload)
    if block.header.object_kind is not expected_kind:
        raise ValueError(f"expected {expected_kind.name} inline object block, got {block.header.object_kind.name}")
    return block


def validate_frame_submit_body(
    metadata: FrameSubmitMetadata,
    body: bytes | memoryview,
) -> BodyView:
    body_view = unpack_body(body)
    _validate_body_metadata_contract(
        payload_kind_bitmap=metadata.payload_kind_bitmap,
        payload_frame_count=metadata.payload_frame_count,
        body_view=body_view,
    )
    _validate_frame_submit_object_contract(metadata, body_view)
    return body_view


def validate_result_push_body(
    metadata: ResultPushMetadata,
    body: bytes | memoryview,
) -> BodyView:
    body_view = unpack_body(body)
    validate_result_push_tensor_coverage(metadata)
    _validate_body_metadata_contract(
        payload_kind_bitmap=metadata.payload_kind_bitmap,
        payload_frame_count=metadata.payload_frame_count,
        body_view=body_view,
    )
    _validate_result_push_object_contract(metadata, body_view)
    return body_view


def _validate_body_metadata_contract(
    *,
    payload_kind_bitmap: PayloadKind,
    payload_frame_count: int,
    body_view: BodyView,
) -> None:
    descriptors = unpack_typed_payload_descriptors(body_view.typed_payload_descriptor_region)
    if len(descriptors) != payload_frame_count:
        raise ValueError(
            "typed payload descriptor count does not match payload_frame_count: "
            f"{len(descriptors)} != {payload_frame_count}"
        )
    expected_descriptor_bytes = payload_frame_count * TYPED_PAYLOAD_DESCRIPTOR_LENGTH
    if body_view.prelude.typed_payload_descriptor_bytes != expected_descriptor_bytes:
        raise ValueError(
            "typed payload descriptor byte count does not match payload_frame_count: "
            f"{body_view.prelude.typed_payload_descriptor_bytes} != {expected_descriptor_bytes}"
        )
    if payload_frame_count == 0 and body_view.prelude.typed_payload_frame_bytes != 0:
        raise ValueError(
            "typed payload frame bytes must be 0 when payload_frame_count is 0: "
            f"{body_view.prelude.typed_payload_frame_bytes}"
        )
    for descriptor in descriptors:
        if not (payload_kind_bitmap & descriptor.payload_kind):
            raise ValueError(
                "typed payload descriptor kind is not declared by payload_kind_bitmap: "
                f"{descriptor.payload_kind.name} not in 0x{int(payload_kind_bitmap):08x}"
            )


def _validate_frame_submit_object_contract(
    metadata: FrameSubmitMetadata,
    body_view: BodyView,
) -> None:
    if metadata.object_ref_mask & ~_FRAME_SUBMIT_KNOWN_OBJECT_REF_MASK:
        raise ValueError(f"FRAME_SUBMIT object_ref_mask contains unknown bits: 0x{metadata.object_ref_mask:08x}")

    inline_blocks = unpack_inline_object_blocks(body_view.inline_object_region)
    reference_blocks = unpack_object_reference_blocks(body_view.object_reference_region)
    inline_by_kind: dict[CacheObjectKind, list[InlineObjectBlockView]] = {}
    reference_by_kind: dict[CacheObjectKind, list[ObjectReferenceBlock]] = {}

    for block in inline_blocks:
        inline_by_kind.setdefault(block.header.object_kind, []).append(block)
    for block in reference_blocks:
        reference_by_kind.setdefault(block.object_kind, []).append(block)

    if metadata.submit_mode is SubmitMode.INLINE:
        if metadata.object_ref_mask != 0:
            raise ValueError("inline FRAME_SUBMIT current body must not declare object_ref_mask")
        if reference_blocks:
            raise ValueError("inline FRAME_SUBMIT current body must not carry object reference blocks")

    expected_reference_kinds: list[CacheObjectKind] = []
    for bit, object_kind in _FRAME_SUBMIT_STANDARD_REFERENCE_SLOTS:
        inline_matches = inline_by_kind.get(object_kind, [])
        reference_matches = reference_by_kind.get(object_kind, [])
        if len(inline_matches) > 1:
            raise ValueError(f"FRAME_SUBMIT current body carries duplicate inline {object_kind.name} blocks")
        if len(reference_matches) > 1:
            raise ValueError(f"FRAME_SUBMIT current body carries duplicate {object_kind.name} reference blocks")

        if metadata.object_ref_mask & bit:
            expected_reference_kinds.append(object_kind)
            if inline_matches:
                raise ValueError(f"FRAME_SUBMIT current body references {object_kind.name} but also carries it inline")
            if len(reference_matches) != 1:
                raise ValueError(
                    "FRAME_SUBMIT current body references "
                    f"{object_kind.name} but object reference region must contain exactly one block"
                )
        elif reference_matches:
            raise ValueError(
                f"FRAME_SUBMIT current body carries {object_kind.name} reference block without object_ref_mask bit"
            )

    if metadata.submit_mode is SubmitMode.REFERENCE:
        for _bit, object_kind in _FRAME_SUBMIT_STANDARD_REFERENCE_SLOTS:
            if inline_by_kind.get(object_kind):
                raise ValueError("reference FRAME_SUBMIT current body must not carry inline standard object blocks")

    actual_reference_kinds = [block.object_kind for block in reference_blocks]
    if actual_reference_kinds != expected_reference_kinds:
        raise ValueError("FRAME_SUBMIT current object reference region does not match object_ref_mask order")

    camera_blocks = inline_by_kind.get(CacheObjectKind.CAMERA_BLOCK, [])
    if camera_blocks:
        camera_block = camera_blocks[0]
        if camera_block.header.object_bytes != metadata.camera_bytes:
            raise ValueError(
                "FRAME_SUBMIT current inline CAMERA_BLOCK bytes do not match metadata.camera_bytes: "
                f"{camera_block.header.object_bytes} != {metadata.camera_bytes}"
            )
    elif metadata.camera_bytes != 0:
        raise ValueError("FRAME_SUBMIT current body is missing required CAMERA_BLOCK inline object")

    tile_index_blocks = inline_by_kind.get(CacheObjectKind.TILE_INDEX_BLOCK, [])
    tile_index_is_referenced = bool(metadata.object_ref_mask & (1 << 1))
    if tile_index_blocks:
        tile_index_block = tile_index_blocks[0]
        if tile_index_block.header.object_bytes != metadata.tile_index_bytes:
            raise ValueError(
                "FRAME_SUBMIT current inline TILE_INDEX_BLOCK bytes do not match metadata.tile_index_bytes: "
                f"{tile_index_block.header.object_bytes} != {metadata.tile_index_bytes}"
            )
        unpack_tile_index_block(
            tile_index_block.payload,
            mode=metadata.tile_index_mode,
            tile_count=metadata.tile_count,
            tile_base_id=metadata.tile_base_id,
        )
    elif metadata.tile_count > 0 and not tile_index_is_referenced:
        raise ValueError("FRAME_SUBMIT current body is missing required TILE_INDEX_BLOCK")
    if tile_index_is_referenced and metadata.tile_index_bytes != 0:
        raise ValueError("FRAME_SUBMIT current tile_index_bytes must be 0 when TILE_INDEX_BLOCK is referenced")

    tensor_section_blocks = inline_by_kind.get(CacheObjectKind.TENSOR_SECTION_TABLE, [])
    tensor_section_is_referenced = bool(metadata.object_ref_mask & (1 << 2))
    if tensor_section_blocks:
        unpack_tensor_body(
            tensor_section_blocks[0].payload,
            tile_index_bytes=0,
            section_count=metadata.section_count,
            tile_count=metadata.tile_count,
        )
    elif metadata.section_count > 0 and not tensor_section_is_referenced:
        raise ValueError("FRAME_SUBMIT current body is missing required TENSOR_SECTION_TABLE")


def _validate_result_push_object_contract(
    metadata: ResultPushMetadata,
    body_view: BodyView,
) -> None:
    inline_blocks = unpack_inline_object_blocks(body_view.inline_object_region)
    reference_blocks = unpack_object_reference_blocks(body_view.object_reference_region)
    inline_by_kind: dict[CacheObjectKind, list[InlineObjectBlockView]] = {}

    for block in inline_blocks:
        inline_by_kind.setdefault(block.header.object_kind, []).append(block)

    previous_key: tuple[int, int, int, int] | None = None
    for block in reference_blocks:
        key = (
            int(block.object_kind),
            block.cache_namespace,
            block.cache_key_hi,
            block.cache_key_lo,
        )
        if previous_key is not None and key <= previous_key:
            raise ValueError("RESULT_PUSH current object reference region must be strictly ordered")
        previous_key = key

    for object_kind in (
        CacheObjectKind.TILE_INDEX_BLOCK,
        CacheObjectKind.TENSOR_SECTION_TABLE,
    ):
        if len(inline_by_kind.get(object_kind, [])) > 1:
            raise ValueError(f"RESULT_PUSH current body carries duplicate inline {object_kind.name} blocks")

    tile_index_inline_blocks = inline_by_kind.get(CacheObjectKind.TILE_INDEX_BLOCK, [])
    tile_index_reference_blocks = [
        block for block in reference_blocks if block.object_kind is CacheObjectKind.TILE_INDEX_BLOCK
    ]
    if len(tile_index_reference_blocks) > 1:
        raise ValueError("RESULT_PUSH current body carries duplicate TILE_INDEX_BLOCK reference blocks")
    if tile_index_inline_blocks and tile_index_reference_blocks:
        raise ValueError("RESULT_PUSH current body must not carry TILE_INDEX_BLOCK both inline and by reference")
    if tile_index_inline_blocks:
        tile_index_block = tile_index_inline_blocks[0]
        if tile_index_block.header.object_bytes != metadata.tile_index_bytes:
            raise ValueError(
                "RESULT_PUSH current inline TILE_INDEX_BLOCK bytes do not match metadata.tile_index_bytes: "
                f"{tile_index_block.header.object_bytes} != {metadata.tile_index_bytes}"
            )
        unpack_tile_index_block(
            tile_index_block.payload,
            mode=TileIndexMode.RAW_U16 if metadata.tile_index_bytes else TileIndexMode.DENSE_RANGE,
            tile_count=metadata.tile_count,
            tile_base_id=metadata.tile_base_id,
        )
    elif metadata.tile_count > 0 and not tile_index_reference_blocks:
        raise ValueError("RESULT_PUSH current body is missing required TILE_INDEX_BLOCK")
    if tile_index_reference_blocks and metadata.tile_index_bytes != 0:
        raise ValueError("RESULT_PUSH current tile_index_bytes must be 0 when TILE_INDEX_BLOCK is referenced")

    tensor_section_inline_blocks = inline_by_kind.get(CacheObjectKind.TENSOR_SECTION_TABLE, [])
    tensor_section_reference_blocks = [
        block for block in reference_blocks if block.object_kind is CacheObjectKind.TENSOR_SECTION_TABLE
    ]
    if len(tensor_section_reference_blocks) > 1:
        raise ValueError("RESULT_PUSH current body carries duplicate TENSOR_SECTION_TABLE reference blocks")
    if tensor_section_inline_blocks and tensor_section_reference_blocks:
        raise ValueError("RESULT_PUSH current body must not carry TENSOR_SECTION_TABLE both inline and by reference")
    if tensor_section_inline_blocks:
        unpack_tensor_body(
            tensor_section_inline_blocks[0].payload,
            tile_index_bytes=0,
            section_count=metadata.section_count,
            tile_count=metadata.tile_count,
        )
    elif metadata.section_count > 0 and not tensor_section_reference_blocks:
        raise ValueError("RESULT_PUSH current body is missing required TENSOR_SECTION_TABLE")


def _validate_descriptor_offsets(
    descriptors: Sequence[TypedPayloadDescriptor | ExtensionFrameDescriptor],
    *,
    payload_region_length: int,
    label: str,
) -> None:
    previous_end = -1
    for descriptor in descriptors:
        start = descriptor.payload_offset
        end = descriptor.payload_offset + descriptor.payload_length
        if start < previous_end:
            raise ValueError(
                f"{label} descriptors must be ordered by non-overlapping ascending offsets: {start} < {previous_end}"
            )
        if end > payload_region_length:
            raise ValueError(f"{label} is shorter than descriptor table requires: {end} > {payload_region_length}")
        previous_end = end


def unpack_tile_index_block(
    payload: bytes | memoryview,
    *,
    mode: TileIndexMode,
    tile_count: int,
    tile_base_id: int = 0,
) -> tuple[int, ...]:
    if tile_count < 0:
        raise ValueError(f"tile_count must be non-negative, got {tile_count}")

    view = memoryview(payload)
    if mode is TileIndexMode.DENSE_RANGE:
        if len(view) != 0:
            raise ValueError(f"dense_range expects empty tile_index_block, got {len(view)} bytes")
        return tuple(tile_base_id + index for index in range(tile_count))

    if mode is TileIndexMode.RAW_U16:
        expected_bytes = tile_count * TILE_ID_ENTRY_STRUCT.size
        if len(view) != expected_bytes:
            raise ValueError(f"raw_u16 expects {expected_bytes} bytes, got {len(view)}")
        return tuple(
            TILE_ID_ENTRY_STRUCT.unpack_from(view, offset)[0]
            for offset in range(0, len(view), TILE_ID_ENTRY_STRUCT.size)
        )

    if mode is TileIndexMode.DELTA_U16:
        expected_bytes = tile_count * TILE_ID_ENTRY_STRUCT.size
        if len(view) != expected_bytes:
            raise ValueError(f"delta_u16 expects {expected_bytes} bytes, got {len(view)}")
        if tile_count == 0:
            return ()

        tile_ids: list[int] = []
        previous_tile_id = 0
        for offset in range(0, len(view), TILE_ID_ENTRY_STRUCT.size):
            value = TILE_ID_ENTRY_STRUCT.unpack_from(view, offset)[0]
            tile_id = value if not tile_ids else previous_tile_id + value
            tile_ids.append(tile_id)
            previous_tile_id = tile_id
        return tuple(tile_ids)

    if mode is TileIndexMode.BITSET:
        tile_ids = [
            bit_index
            for byte_index, value in enumerate(view)
            for bit_offset in range(8)
            if value & (1 << bit_offset)
            for bit_index in (byte_index * 8 + bit_offset,)
        ]
        if len(tile_ids) != tile_count:
            raise ValueError(f"bitset decoded {len(tile_ids)} tile ids, expected {tile_count}")
        return tuple(tile_ids)

    raise ValueError(f"unsupported tile index mode: {mode}")


def pack_tensor_section_data(section: TensorSectionData) -> bytes:
    tile_payloads = section.normalized_tile_payloads()
    codec_ids = section.normalized_codec_ids()
    codec_table = _build_codec_table(
        codec_ids,
        default_codec_id=section.default_codec_id,
        tile_count=len(tile_payloads),
    )
    length_table = b"".join(LENGTH_ENTRY_STRUCT.pack(len(payload)) for payload in tile_payloads)
    payload_blob, flags = _build_section_payload_blob(tile_payloads, payload_stride_bytes=section.payload_stride_bytes)
    if codec_table:
        flags |= SectionFlags.MIXED_CODEC

    desc = TensorSectionDesc(
        role_id=section.role_id,
        codec_id=section.default_codec_id,
        dtype_id=section.dtype_id,
        layout_id=section.layout_id,
        scale_policy=section.scale_policy,
        flags=flags,
        element_count_per_tile=section.element_count_per_tile,
        codec_table_bytes=len(codec_table),
        length_table_bytes=len(length_table),
        payload_bytes=len(payload_blob),
        payload_stride_bytes=section.payload_stride_bytes,
    )
    return pack_tensor_section(
        desc,
        codec_table=codec_table,
        length_table=length_table,
        payload=payload_blob,
    )


def build_frame_submit_packet(
    *,
    session_id: int,
    frame_id: int,
    operation_id: int,
    src_width: int,
    src_height: int,
    tile_width: int,
    tile_height: int,
    tile_ids: Sequence[int],
    sections: Sequence[TensorSectionData],
    camera_block: bytes = b"",
    frame_class: int = 0,
    input_profile: InputProfile = InputProfile.UNSPECIFIED,
    tile_index_mode: TileIndexMode = TileIndexMode.RAW_U16,
    latency_budget_ms: int = 0,
    target_fps_x100: int = 0,
    retry_of_frame: int = 0,
    tile_base_id: int = 0,
    submit_mode: SubmitMode = SubmitMode.INLINE,
    object_ref_mask: int = 0,
    camera_reference: ObjectReferenceBlock | bytes | None = None,
    tile_index_reference: ObjectReferenceBlock | bytes | None = None,
    tensor_section_table_reference: ObjectReferenceBlock | bytes | None = None,
    budget_policy: BudgetPolicy = BudgetPolicy.NONE,
    dependency_frame_id: int = 0,
    loss_tolerance_policy: int = 0xFF,
    payload_kind_bitmap: PayloadKind = PayloadKind.TENSOR,
    payload_frame_count: int = 0,
    version_major: int = 1,
    wire_format: WireFormat = WireFormat.CURRENT,
    flags: HeaderFlags = HeaderFlags.NONE,
    view_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
) -> NnrpPacket:
    submit_mode = SubmitMode(submit_mode)
    normalized_camera_reference = None if camera_reference is None else parse_camera_reference_block(camera_reference)
    normalized_tile_index_reference = (
        None if tile_index_reference is None else parse_tile_index_reference_block(tile_index_reference)
    )
    normalized_tensor_section_table_reference = (
        None
        if tensor_section_table_reference is None
        else parse_tensor_section_table_reference_block(tensor_section_table_reference)
    )
    provided_reference_mask = 0
    if normalized_camera_reference is not None:
        provided_reference_mask |= 1 << 0
    if normalized_tile_index_reference is not None:
        provided_reference_mask |= 1 << 1
    if normalized_tensor_section_table_reference is not None:
        provided_reference_mask |= 1 << 2

    if submit_mode is SubmitMode.INLINE and object_ref_mask != 0:
        raise ValueError("inline FRAME_SUBMIT current builder does not accept object_ref_mask")
    if submit_mode is SubmitMode.INLINE and provided_reference_mask != 0:
        raise ValueError("inline FRAME_SUBMIT current builder does not accept reference blocks")
    if submit_mode is SubmitMode.REFERENCE and provided_reference_mask == 0:
        raise ValueError(
            "FRAME_SUBMIT current reference mode requires reference body blocks, which are not implemented"
        )
    if submit_mode is SubmitMode.MIXED and object_ref_mask == 0:
        raise ValueError("mixed FRAME_SUBMIT current builder requires a non-zero object_ref_mask")

    tensor_enabled = bool(payload_kind_bitmap & PayloadKind.TENSOR)
    if tensor_enabled:
        section_payloads = _pack_section_sequence(sections, tile_count=len(tile_ids))
        tile_index_block = pack_tile_index_block(tile_ids, mode=tile_index_mode, tile_base_id=tile_base_id)
        if submit_mode is SubmitMode.INLINE:
            body = bytearray(camera_block)
            _append_zero_padding(body)
            body.extend(tile_index_block)
            for section_payload in section_payloads:
                _append_zero_padding(body)
                body.extend(section_payload)
            inline_camera_bytes = len(camera_block)
            inline_tile_index_bytes = len(tile_index_block)
        else:
            (
                inline_object_region,
                object_reference_region,
                inline_camera_bytes,
                inline_tile_index_bytes,
            ) = _build_frame_submit_object_regions(
                submit_mode=submit_mode,
                camera_block=camera_block,
                camera_reference=normalized_camera_reference,
                tile_ids=tile_ids,
                tile_index_mode=tile_index_mode,
                tile_base_id=tile_base_id,
                tile_index_block=tile_index_block,
                tile_index_reference=normalized_tile_index_reference,
                section_payloads=section_payloads,
                tensor_section_table_reference=normalized_tensor_section_table_reference,
            )
            body = bytearray(
                pack_body(
                    inline_object_region=inline_object_region,
                    object_reference_region=object_reference_region,
                )
            )
    else:
        if tile_ids:
            raise ValueError("non-tensor FRAME_SUBMIT current builder does not accept tile_ids")
        if sections:
            raise ValueError("non-tensor FRAME_SUBMIT current builder does not accept tensor sections")
        if camera_block:
            raise ValueError("non-tensor FRAME_SUBMIT current builder does not accept camera_block")
        if provided_reference_mask != 0:
            raise ValueError("non-tensor FRAME_SUBMIT current builder does not accept tensor object reference blocks")
        if payload_frame_count != 0:
            raise ValueError(
                "non-tensor FRAME_SUBMIT current builder cannot emit payload frames before typed payload builders exist"
            )
        section_payloads = ()
        tile_index_block = b""
        body = bytearray()
        inline_camera_bytes = 0
        inline_tile_index_bytes = 0
    if submit_mode is not SubmitMode.INLINE and object_ref_mask != provided_reference_mask:
        raise ValueError(
            "FRAME_SUBMIT current object_ref_mask must match provided reference blocks: "
            f"0x{object_ref_mask:08x} != 0x{provided_reference_mask:08x}"
        )
    metadata = FrameSubmitMetadata(
        src_width=src_width if tensor_enabled else 0,
        src_height=src_height if tensor_enabled else 0,
        tile_width=tile_width if tensor_enabled else 0,
        tile_height=tile_height if tensor_enabled else 0,
        tile_count=len(tile_ids),
        section_count=len(section_payloads),
        frame_class=frame_class,
        input_profile=input_profile if tensor_enabled else InputProfile.UNSPECIFIED,
        tile_index_mode=tile_index_mode,
        reserved0=0,
        latency_budget_ms=latency_budget_ms,
        target_fps_x100=target_fps_x100,
        retry_of_frame=retry_of_frame,
        tile_base_id=tile_base_id if tensor_enabled else 0,
        camera_bytes=inline_camera_bytes if tensor_enabled else 0,
        tile_index_bytes=inline_tile_index_bytes if tensor_enabled else 0,
        operation_id=operation_id,
        submit_mode=submit_mode,
        budget_policy=budget_policy,
        loss_tolerance_policy=loss_tolerance_policy,
        object_ref_mask=object_ref_mask,
        dependency_frame_id=dependency_frame_id,
        payload_kind_bitmap=payload_kind_bitmap,
        payload_frame_count=payload_frame_count,
    )
    if submit_mode is not SubmitMode.INLINE and tensor_enabled:
        validate_frame_submit_body(metadata, body)

    return NnrpPacket.build(
        version_major=version_major,
        wire_format=wire_format,
        msg_type=MessageType.FRAME_SUBMIT,
        flags=flags,
        session_id=session_id,
        frame_id=frame_id,
        view_id=view_id,
        route_id=route_id,
        trace_id=trace_id,
        metadata=metadata.pack(),
        body=bytes(body),
    )


def _build_frame_submit_object_regions(
    *,
    submit_mode: SubmitMode,
    camera_block: bytes,
    camera_reference: ObjectReferenceBlock | None,
    tile_ids: Sequence[int],
    tile_index_mode: TileIndexMode,
    tile_base_id: int,
    tile_index_block: bytes,
    tile_index_reference: ObjectReferenceBlock | None,
    section_payloads: Sequence[bytes],
    tensor_section_table_reference: ObjectReferenceBlock | None,
) -> tuple[bytes, bytes, int, int]:
    inline_blocks: list[bytes] = []
    reference_blocks: list[ObjectReferenceBlock] = []

    if camera_reference is not None:
        reference_blocks.append(camera_reference)
        inline_camera_bytes = 0
    else:
        inline_camera_bytes = len(camera_block)
        if camera_block:
            if submit_mode is SubmitMode.REFERENCE:
                raise ValueError(
                    "reference FRAME_SUBMIT current builder requires camera_reference when camera_block is present"
                )
            inline_blocks.append(build_camera_inline_object_block(camera_block))

    if tile_index_reference is not None:
        reference_blocks.append(tile_index_reference)
        inline_tile_index_bytes = 0
    else:
        inline_tile_index_bytes = len(tile_index_block)
        if tile_ids:
            if submit_mode is SubmitMode.REFERENCE:
                raise ValueError(
                    "reference FRAME_SUBMIT current builder requires tile_index_reference when tile_ids are present"
                )
            inline_blocks.append(
                build_tile_index_inline_object_block(tile_ids, mode=tile_index_mode, tile_base_id=tile_base_id)
            )

    if tensor_section_table_reference is not None:
        reference_blocks.append(tensor_section_table_reference)
    elif section_payloads:
        if submit_mode is SubmitMode.REFERENCE:
            raise ValueError(
                "reference FRAME_SUBMIT current builder requires "
                "tensor_section_table_reference when sections are present"
            )
        inline_blocks.append(
            build_tensor_section_table_inline_object_block(_pack_tensor_section_region(section_payloads))
        )

    return (
        b"".join(inline_blocks),
        pack_object_reference_blocks(reference_blocks),
        inline_camera_bytes,
        inline_tile_index_bytes,
    )


def build_result_push_packet(
    *,
    session_id: int,
    frame_id: int,
    tile_ids: Sequence[int],
    sections: Sequence[TensorSectionData],
    result_flags: ResultFlags = ResultFlags.NONE,
    active_profile_id: int = 0,
    inference_ms: int = 0,
    queue_ms: int = 0,
    server_total_ms: int = 0,
    status_code: int = 0,
    tile_index_mode: TileIndexMode = TileIndexMode.RAW_U16,
    tile_base_id: int = 0,
    result_class: ResultClass = ResultClass.COMPLETE,
    applied_budget_policy: BudgetPolicy = BudgetPolicy.NONE,
    reused_frame_id: int = 0,
    covered_tile_count: int | None = None,
    dropped_tile_count: int = 0,
    payload_kind_bitmap: PayloadKind = PayloadKind.TENSOR,
    payload_frame_count: int = 0,
    version_major: int = 1,
    wire_format: WireFormat = WireFormat.CURRENT,
    flags: HeaderFlags = HeaderFlags.NONE,
    view_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
) -> NnrpPacket:
    tensor_enabled = bool(payload_kind_bitmap & PayloadKind.TENSOR)
    if tile_index_mode not in {TileIndexMode.RAW_U16, TileIndexMode.DENSE_RANGE}:
        raise ValueError(
            "RESULT_PUSH metadata does not encode tile_index_mode; "
            "only raw_u16 and dense_range are currently representable"
        )

    if tensor_enabled:
        section_payloads = _pack_section_sequence(sections, tile_count=len(tile_ids))
        tile_index_block = pack_tile_index_block(tile_ids, mode=tile_index_mode, tile_base_id=tile_base_id)
        resolved_covered_tile_count = len(tile_ids) if covered_tile_count is None else covered_tile_count
        if resolved_covered_tile_count < 0:
            raise ValueError("covered_tile_count must be non-negative")
        if dropped_tile_count < 0:
            raise ValueError("dropped_tile_count must be non-negative")
        if resolved_covered_tile_count > len(tile_ids):
            raise ValueError(
                f"covered_tile_count must not exceed tile_count: {resolved_covered_tile_count} > {len(tile_ids)}"
            )
        if dropped_tile_count > len(tile_ids):
            raise ValueError(f"dropped_tile_count must not exceed tile_count: {dropped_tile_count} > {len(tile_ids)}")
        if resolved_covered_tile_count + dropped_tile_count != len(tile_ids):
            raise ValueError(
                "covered_tile_count + dropped_tile_count must equal tile_count: "
                f"{resolved_covered_tile_count} + {dropped_tile_count} != {len(tile_ids)}"
            )
        body = bytearray(tile_index_block)
        for section_payload in section_payloads:
            _append_zero_padding(body)
            body.extend(section_payload)
    else:
        if tile_ids:
            raise ValueError("non-tensor RESULT_PUSH current builder does not accept tile_ids")
        if sections:
            raise ValueError("non-tensor RESULT_PUSH current builder does not accept tensor sections")
        if covered_tile_count not in {None, 0}:
            raise ValueError("non-tensor RESULT_PUSH current builder does not accept covered_tile_count")
        if dropped_tile_count != 0:
            raise ValueError("non-tensor RESULT_PUSH current builder does not accept dropped_tile_count")
        if payload_frame_count != 0:
            raise ValueError(
                "non-tensor RESULT_PUSH current builder cannot emit payload frames before typed payload builders exist"
            )
        section_payloads = ()
        tile_index_block = b""
        body = bytearray()
    metadata = ResultPushMetadata(
        status_code=status_code,
        result_flags=result_flags,
        section_count=len(section_payloads),
        tile_count=len(tile_ids),
        active_profile_id=active_profile_id,
        reserved0=0,
        inference_ms=inference_ms,
        queue_ms=queue_ms,
        server_total_ms=server_total_ms,
        reserved1=0,
        tile_base_id=tile_base_id if tensor_enabled else 0,
        tile_index_bytes=len(tile_index_block),
        result_class=result_class,
        applied_budget_policy=applied_budget_policy,
        reused_frame_id=reused_frame_id,
        covered_tile_count=resolved_covered_tile_count if tensor_enabled else 0,
        dropped_tile_count=dropped_tile_count if tensor_enabled else 0,
        payload_kind_bitmap=payload_kind_bitmap,
        payload_frame_count=payload_frame_count,
    )
    validate_result_push_tensor_coverage(metadata)

    return NnrpPacket.build(
        version_major=version_major,
        wire_format=wire_format,
        msg_type=MessageType.RESULT_PUSH,
        flags=flags,
        session_id=session_id,
        frame_id=frame_id,
        view_id=view_id,
        route_id=route_id,
        trace_id=trace_id,
        metadata=metadata.pack(),
        body=bytes(body),
    )


def build_partial_result_push_packet(
    *,
    session_id: int,
    frame_id: int,
    tile_ids: Sequence[int],
    sections: Sequence[TensorSectionData],
    covered_tile_count: int,
    dropped_tile_count: int,
    active_profile_id: int = 0,
    inference_ms: int = 0,
    queue_ms: int = 0,
    server_total_ms: int = 0,
    status_code: int = 0,
    tile_index_mode: TileIndexMode = TileIndexMode.RAW_U16,
    tile_base_id: int = 0,
    version_major: int = 1,
    wire_format: WireFormat = WireFormat.CURRENT,
    flags: HeaderFlags = HeaderFlags.NONE,
    view_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
) -> NnrpPacket:
    return build_result_push_packet(
        session_id=session_id,
        frame_id=frame_id,
        tile_ids=tile_ids,
        sections=sections,
        result_flags=ResultFlags.PARTIAL,
        active_profile_id=active_profile_id,
        inference_ms=inference_ms,
        queue_ms=queue_ms,
        server_total_ms=server_total_ms,
        status_code=status_code,
        tile_index_mode=tile_index_mode,
        tile_base_id=tile_base_id,
        result_class=ResultClass.PARTIAL,
        applied_budget_policy=BudgetPolicy.ALLOW_PARTIAL,
        reused_frame_id=0,
        covered_tile_count=covered_tile_count,
        dropped_tile_count=dropped_tile_count,
        version_major=version_major,
        wire_format=wire_format,
        flags=flags,
        view_id=view_id,
        route_id=route_id,
        trace_id=trace_id,
    )


def build_stale_reuse_result_push_packet(
    *,
    session_id: int,
    frame_id: int,
    tile_ids: Sequence[int],
    sections: Sequence[TensorSectionData],
    reused_frame_id: int,
    active_profile_id: int = 0,
    inference_ms: int = 0,
    queue_ms: int = 0,
    server_total_ms: int = 0,
    status_code: int = 0,
    tile_index_mode: TileIndexMode = TileIndexMode.RAW_U16,
    tile_base_id: int = 0,
    covered_tile_count: int | None = None,
    dropped_tile_count: int = 0,
    version_major: int = 1,
    wire_format: WireFormat = WireFormat.CURRENT,
    flags: HeaderFlags = HeaderFlags.NONE,
    view_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
) -> NnrpPacket:
    return build_result_push_packet(
        session_id=session_id,
        frame_id=frame_id,
        tile_ids=tile_ids,
        sections=sections,
        result_flags=ResultFlags.STALE,
        active_profile_id=active_profile_id,
        inference_ms=inference_ms,
        queue_ms=queue_ms,
        server_total_ms=server_total_ms,
        status_code=status_code,
        tile_index_mode=tile_index_mode,
        tile_base_id=tile_base_id,
        result_class=ResultClass.STALE_REUSE,
        applied_budget_policy=BudgetPolicy.ALLOW_STALE_REUSE,
        reused_frame_id=reused_frame_id,
        covered_tile_count=covered_tile_count,
        dropped_tile_count=dropped_tile_count,
        version_major=version_major,
        wire_format=wire_format,
        flags=flags,
        view_id=view_id,
        route_id=route_id,
        trace_id=trace_id,
    )


def build_degraded_result_push_packet(
    *,
    session_id: int,
    frame_id: int,
    tile_ids: Sequence[int],
    sections: Sequence[TensorSectionData],
    active_profile_id: int = 0,
    inference_ms: int = 0,
    queue_ms: int = 0,
    server_total_ms: int = 0,
    status_code: int = 0,
    tile_index_mode: TileIndexMode = TileIndexMode.RAW_U16,
    tile_base_id: int = 0,
    covered_tile_count: int | None = None,
    dropped_tile_count: int = 0,
    version_major: int = 1,
    wire_format: WireFormat = WireFormat.CURRENT,
    flags: HeaderFlags = HeaderFlags.NONE,
    view_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
) -> NnrpPacket:
    return build_result_push_packet(
        session_id=session_id,
        frame_id=frame_id,
        tile_ids=tile_ids,
        sections=sections,
        result_flags=ResultFlags.FALLBACK,
        active_profile_id=active_profile_id,
        inference_ms=inference_ms,
        queue_ms=queue_ms,
        server_total_ms=server_total_ms,
        status_code=status_code,
        tile_index_mode=tile_index_mode,
        tile_base_id=tile_base_id,
        result_class=ResultClass.DEGRADED,
        applied_budget_policy=BudgetPolicy.ALLOW_DEGRADED,
        reused_frame_id=0,
        covered_tile_count=covered_tile_count,
        dropped_tile_count=dropped_tile_count,
        version_major=version_major,
        wire_format=wire_format,
        flags=flags,
        view_id=view_id,
        route_id=route_id,
        trace_id=trace_id,
    )


def build_frame_submit_typed_payload_packet(
    *,
    session_id: int,
    frame_id: int,
    operation_id: int,
    frames: Sequence[TypedPayloadFrame],
    frame_class: int = 0,
    latency_budget_ms: int = 0,
    target_fps_x100: int = 0,
    retry_of_frame: int = 0,
    budget_policy: BudgetPolicy = BudgetPolicy.NONE,
    dependency_frame_id: int = 0,
    loss_tolerance_policy: int = 0xFF,
    version_major: int = 1,
    wire_format: WireFormat = WireFormat.CURRENT,
    flags: HeaderFlags = HeaderFlags.NONE,
    view_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
) -> NnrpPacket:
    typed_payload_descriptor_region, typed_payload_frame_region, payload_kind_bitmap = _build_typed_payload_regions(
        frames
    )
    metadata = FrameSubmitMetadata(
        src_width=0,
        src_height=0,
        tile_width=0,
        tile_height=0,
        tile_count=0,
        section_count=0,
        frame_class=frame_class,
        input_profile=InputProfile.UNSPECIFIED,
        tile_index_mode=TileIndexMode.RAW_U16,
        reserved0=0,
        latency_budget_ms=latency_budget_ms,
        target_fps_x100=target_fps_x100,
        retry_of_frame=retry_of_frame,
        tile_base_id=0,
        camera_bytes=0,
        tile_index_bytes=0,
        operation_id=operation_id,
        submit_mode=SubmitMode.INLINE,
        budget_policy=budget_policy,
        loss_tolerance_policy=loss_tolerance_policy,
        object_ref_mask=0,
        dependency_frame_id=dependency_frame_id,
        payload_kind_bitmap=payload_kind_bitmap,
        payload_frame_count=len(frames),
    ).pack()
    body = pack_body(
        typed_payload_descriptor_region=typed_payload_descriptor_region,
        typed_payload_frame_region=typed_payload_frame_region,
    )

    return NnrpPacket.build(
        version_major=version_major,
        wire_format=wire_format,
        msg_type=MessageType.FRAME_SUBMIT,
        flags=flags,
        session_id=session_id,
        frame_id=frame_id,
        view_id=view_id,
        route_id=route_id,
        trace_id=trace_id,
        metadata=metadata,
        body=body,
    )


def build_result_push_typed_payload_packet(
    *,
    session_id: int,
    frame_id: int,
    frames: Sequence[TypedPayloadFrame],
    result_flags: ResultFlags = ResultFlags.NONE,
    active_profile_id: int = 0,
    inference_ms: int = 0,
    queue_ms: int = 0,
    server_total_ms: int = 0,
    status_code: int = 0,
    result_class: ResultClass = ResultClass.COMPLETE,
    applied_budget_policy: BudgetPolicy = BudgetPolicy.NONE,
    reused_frame_id: int = 0,
    version_major: int = 1,
    wire_format: WireFormat = WireFormat.CURRENT,
    flags: HeaderFlags = HeaderFlags.NONE,
    view_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
) -> NnrpPacket:
    typed_payload_descriptor_region, typed_payload_frame_region, payload_kind_bitmap = _build_typed_payload_regions(
        frames
    )
    metadata = ResultPushMetadata(
        status_code=status_code,
        result_flags=result_flags,
        section_count=0,
        tile_count=0,
        active_profile_id=active_profile_id,
        reserved0=0,
        inference_ms=inference_ms,
        queue_ms=queue_ms,
        server_total_ms=server_total_ms,
        reserved1=0,
        tile_base_id=0,
        tile_index_bytes=0,
        result_class=result_class,
        applied_budget_policy=applied_budget_policy,
        reused_frame_id=reused_frame_id,
        covered_tile_count=0,
        dropped_tile_count=0,
        payload_kind_bitmap=payload_kind_bitmap,
        payload_frame_count=len(frames),
    )
    validate_result_push_tensor_coverage(metadata)
    body = pack_body(
        typed_payload_descriptor_region=typed_payload_descriptor_region,
        typed_payload_frame_region=typed_payload_frame_region,
    )

    return NnrpPacket.build(
        version_major=version_major,
        wire_format=wire_format,
        msg_type=MessageType.RESULT_PUSH,
        flags=flags,
        session_id=session_id,
        frame_id=frame_id,
        view_id=view_id,
        route_id=route_id,
        trace_id=trace_id,
        metadata=metadata.pack(),
        body=body,
    )


def build_frame_submit_mixed_packet(
    *,
    session_id: int,
    frame_id: int,
    operation_id: int,
    src_width: int,
    src_height: int,
    tile_width: int,
    tile_height: int,
    tile_ids: Sequence[int],
    sections: Sequence[TensorSectionData],
    frames: Sequence[TypedPayloadFrame],
    camera_block: bytes = b"",
    frame_class: int = 0,
    input_profile: InputProfile = InputProfile.UNSPECIFIED,
    tile_index_mode: TileIndexMode = TileIndexMode.RAW_U16,
    latency_budget_ms: int = 0,
    target_fps_x100: int = 0,
    retry_of_frame: int = 0,
    tile_base_id: int = 0,
    budget_policy: BudgetPolicy = BudgetPolicy.NONE,
    dependency_frame_id: int = 0,
    loss_tolerance_policy: int = 0xFF,
    version_major: int = 1,
    wire_format: WireFormat = WireFormat.CURRENT,
    flags: HeaderFlags = HeaderFlags.NONE,
    view_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
) -> NnrpPacket:
    (
        typed_payload_descriptor_region,
        typed_payload_frame_region,
        typed_payload_bitmap,
    ) = _build_typed_payload_regions(frames)
    section_payloads = _pack_section_sequence(sections, tile_count=len(tile_ids))
    tile_index_payload = pack_tile_index_block(tile_ids, mode=tile_index_mode, tile_base_id=tile_base_id)
    if not camera_block and not tile_ids and not section_payloads:
        raise ValueError("mixed FRAME_SUBMIT current builder requires tensor body content")

    inline_object_region = _build_submit_inline_object_region(
        camera_block=camera_block,
        tile_index_payload=tile_index_payload,
        section_payloads=section_payloads,
        has_tile_ids=bool(tile_ids),
    )
    metadata = FrameSubmitMetadata(
        src_width=src_width,
        src_height=src_height,
        tile_width=tile_width,
        tile_height=tile_height,
        tile_count=len(tile_ids),
        section_count=len(section_payloads),
        frame_class=frame_class,
        input_profile=input_profile,
        tile_index_mode=tile_index_mode,
        reserved0=0,
        latency_budget_ms=latency_budget_ms,
        target_fps_x100=target_fps_x100,
        retry_of_frame=retry_of_frame,
        tile_base_id=tile_base_id,
        camera_bytes=len(camera_block),
        tile_index_bytes=len(tile_index_payload),
        operation_id=operation_id,
        submit_mode=SubmitMode.INLINE,
        budget_policy=budget_policy,
        loss_tolerance_policy=loss_tolerance_policy,
        object_ref_mask=0,
        dependency_frame_id=dependency_frame_id,
        payload_kind_bitmap=PayloadKind.TENSOR | typed_payload_bitmap,
        payload_frame_count=len(frames),
    ).pack()
    body = pack_body(
        inline_object_region=inline_object_region,
        typed_payload_descriptor_region=typed_payload_descriptor_region,
        typed_payload_frame_region=typed_payload_frame_region,
    )

    return NnrpPacket.build(
        version_major=version_major,
        wire_format=wire_format,
        msg_type=MessageType.FRAME_SUBMIT,
        flags=flags,
        session_id=session_id,
        frame_id=frame_id,
        view_id=view_id,
        route_id=route_id,
        trace_id=trace_id,
        metadata=metadata,
        body=body,
    )


def build_result_push_mixed_packet(
    *,
    session_id: int,
    frame_id: int,
    tile_ids: Sequence[int],
    sections: Sequence[TensorSectionData],
    frames: Sequence[TypedPayloadFrame],
    result_flags: ResultFlags = ResultFlags.NONE,
    active_profile_id: int = 0,
    inference_ms: int = 0,
    queue_ms: int = 0,
    server_total_ms: int = 0,
    status_code: int = 0,
    tile_index_mode: TileIndexMode = TileIndexMode.RAW_U16,
    tile_base_id: int = 0,
    result_class: ResultClass = ResultClass.COMPLETE,
    applied_budget_policy: BudgetPolicy = BudgetPolicy.NONE,
    reused_frame_id: int = 0,
    covered_tile_count: int | None = None,
    dropped_tile_count: int = 0,
    version_major: int = 1,
    wire_format: WireFormat = WireFormat.CURRENT,
    flags: HeaderFlags = HeaderFlags.NONE,
    view_id: int = 0,
    route_id: int = 0,
    trace_id: int = 0,
) -> NnrpPacket:
    (
        typed_payload_descriptor_region,
        typed_payload_frame_region,
        typed_payload_bitmap,
    ) = _build_typed_payload_regions(frames)
    if tile_index_mode not in {TileIndexMode.RAW_U16, TileIndexMode.DENSE_RANGE}:
        raise ValueError(
            "RESULT_PUSH metadata does not encode tile_index_mode; "
            "only raw_u16 and dense_range are currently representable"
        )

    section_payloads = _pack_section_sequence(sections, tile_count=len(tile_ids))
    tile_index_payload = pack_tile_index_block(tile_ids, mode=tile_index_mode, tile_base_id=tile_base_id)
    if not tile_ids and not section_payloads:
        raise ValueError("mixed RESULT_PUSH current builder requires tensor body content")
    resolved_covered_tile_count = len(tile_ids) if covered_tile_count is None else covered_tile_count
    if resolved_covered_tile_count < 0:
        raise ValueError("covered_tile_count must be non-negative")
    if dropped_tile_count < 0:
        raise ValueError("dropped_tile_count must be non-negative")
    if resolved_covered_tile_count > len(tile_ids):
        raise ValueError(
            f"covered_tile_count must not exceed tile_count: {resolved_covered_tile_count} > {len(tile_ids)}"
        )
    if dropped_tile_count > len(tile_ids):
        raise ValueError(f"dropped_tile_count must not exceed tile_count: {dropped_tile_count} > {len(tile_ids)}")
    if resolved_covered_tile_count + dropped_tile_count != len(tile_ids):
        raise ValueError(
            "covered_tile_count + dropped_tile_count must equal tile_count: "
            f"{resolved_covered_tile_count} + {dropped_tile_count} != {len(tile_ids)}"
        )

    inline_object_region = _build_result_inline_object_region(
        tile_index_payload=tile_index_payload,
        section_payloads=section_payloads,
        has_tile_ids=bool(tile_ids),
    )
    metadata = ResultPushMetadata(
        status_code=status_code,
        result_flags=result_flags,
        section_count=len(section_payloads),
        tile_count=len(tile_ids),
        active_profile_id=active_profile_id,
        reserved0=0,
        inference_ms=inference_ms,
        queue_ms=queue_ms,
        server_total_ms=server_total_ms,
        reserved1=0,
        tile_base_id=tile_base_id,
        tile_index_bytes=len(tile_index_payload),
        result_class=result_class,
        applied_budget_policy=applied_budget_policy,
        reused_frame_id=reused_frame_id,
        covered_tile_count=resolved_covered_tile_count,
        dropped_tile_count=dropped_tile_count,
        payload_kind_bitmap=PayloadKind.TENSOR | typed_payload_bitmap,
        payload_frame_count=len(frames),
    )
    validate_result_push_tensor_coverage(metadata)
    body = pack_body(
        inline_object_region=inline_object_region,
        typed_payload_descriptor_region=typed_payload_descriptor_region,
        typed_payload_frame_region=typed_payload_frame_region,
    )

    return NnrpPacket.build(
        version_major=version_major,
        wire_format=wire_format,
        msg_type=MessageType.RESULT_PUSH,
        flags=flags,
        session_id=session_id,
        frame_id=frame_id,
        view_id=view_id,
        route_id=route_id,
        trace_id=trace_id,
        metadata=metadata.pack(),
        body=body,
    )


def _build_typed_payload_regions(
    frames: Sequence[TypedPayloadFrame],
) -> tuple[bytes, bytes, PayloadKind]:
    if not frames:
        raise ValueError("typed payload packet builder requires at least one frame")
    typed_payload_descriptor_region, typed_payload_frame_region = pack_typed_payload_frames(frames)
    payload_kind_bitmap = PayloadKind.NONE
    for frame in frames:
        normalized = parse_typed_payload_frame(frame)
        payload_kind_bitmap |= normalized.payload_kind
    return (
        typed_payload_descriptor_region,
        typed_payload_frame_region,
        payload_kind_bitmap,
    )


def _build_submit_inline_object_region(
    *,
    camera_block: bytes,
    tile_index_payload: bytes,
    section_payloads: Sequence[bytes],
    has_tile_ids: bool,
) -> bytes:
    blocks: list[bytes] = []
    if camera_block:
        blocks.append(build_camera_inline_object_block(camera_block))
    if has_tile_ids:
        blocks.append(
            _build_standard_inline_object_block(
                object_kind=CacheObjectKind.TILE_INDEX_BLOCK,
                payload=tile_index_payload,
                profile_id=0,
            )
        )
    if section_payloads:
        blocks.append(build_tensor_section_table_inline_object_block(_pack_tensor_section_region(section_payloads)))
    return b"".join(blocks)


def _build_result_inline_object_region(
    *,
    tile_index_payload: bytes,
    section_payloads: Sequence[bytes],
    has_tile_ids: bool,
) -> bytes:
    blocks: list[bytes] = []
    if has_tile_ids:
        blocks.append(
            _build_standard_inline_object_block(
                object_kind=CacheObjectKind.TILE_INDEX_BLOCK,
                payload=tile_index_payload,
                profile_id=0,
            )
        )
    if section_payloads:
        blocks.append(build_tensor_section_table_inline_object_block(_pack_tensor_section_region(section_payloads)))
    return b"".join(blocks)


def _pack_tensor_section_region(section_payloads: Sequence[bytes]) -> bytes:
    payload = bytearray()
    for section_payload in section_payloads:
        _append_zero_padding(payload)
        payload.extend(section_payload)
    return bytes(payload)


def validate_result_push_tensor_coverage(metadata: ResultPushMetadata) -> None:
    if not metadata.payload_kind_bitmap & PayloadKind.TENSOR:
        return
    if metadata.covered_tile_count < 0:
        raise ValueError("covered_tile_count must be non-negative")
    if metadata.dropped_tile_count < 0:
        raise ValueError("dropped_tile_count must be non-negative")
    if metadata.covered_tile_count > metadata.tile_count:
        raise ValueError(
            f"covered_tile_count must not exceed tile_count: {metadata.covered_tile_count} > {metadata.tile_count}"
        )
    if metadata.dropped_tile_count > metadata.tile_count:
        raise ValueError(
            f"dropped_tile_count must not exceed tile_count: {metadata.dropped_tile_count} > {metadata.tile_count}"
        )
    if metadata.covered_tile_count + metadata.dropped_tile_count != metadata.tile_count:
        raise ValueError(
            "covered_tile_count + dropped_tile_count must equal tile_count: "
            f"{metadata.covered_tile_count} + {metadata.dropped_tile_count} != {metadata.tile_count}"
        )
    if (
        metadata.result_class is ResultClass.PARTIAL or bool(metadata.result_flags & ResultFlags.PARTIAL)
    ) and metadata.dropped_tile_count == 0:
        raise ValueError("partial RESULT_PUSH requires dropped_tile_count > 0")


def pack_tensor_section(
    desc: TensorSectionDesc,
    *,
    codec_table: bytes = b"",
    length_table: bytes,
    payload: bytes,
) -> bytes:
    if len(codec_table) != desc.codec_table_bytes:
        raise ValueError(f"codec_table length mismatch: expected {desc.codec_table_bytes}, got {len(codec_table)}")
    if len(length_table) != desc.length_table_bytes:
        raise ValueError(f"length_table length mismatch: expected {desc.length_table_bytes}, got {len(length_table)}")
    if len(payload) != desc.payload_bytes:
        raise ValueError(f"payload length mismatch: expected {desc.payload_bytes}, got {len(payload)}")

    section_view = _build_tensor_section_view(
        desc, memoryview(codec_table), memoryview(length_table), memoryview(payload)
    )
    section_view.payload_slices()
    return desc.pack() + codec_table + length_table + payload


def unpack_tensor_body(
    body: bytes | memoryview,
    *,
    tile_index_bytes: int,
    section_count: int,
    tile_count: int | None = None,
) -> TensorBodyView:
    view = memoryview(body)
    if len(view) < tile_index_bytes:
        raise ValueError(f"expected at least {tile_index_bytes} tile index bytes, got {len(view)}")

    tile_index_block = view[:tile_index_bytes]
    cursor = tile_index_bytes
    if section_count > 0:
        aligned_cursor = _align_up(cursor)
        if aligned_cursor > len(view):
            raise ValueError(f"expected aligned tensor body start at {aligned_cursor} bytes, got {len(view)}")
        _validate_zero_padding(view, cursor, aligned_cursor)
        cursor = aligned_cursor
    sections: list[TensorSectionView] = []
    previous_role_id: int | None = None

    for index in range(section_count):
        section, consumed = _parse_tensor_section(view[cursor:])
        if previous_role_id is not None and section.desc.role_id <= previous_role_id:
            raise ValueError(
                "tensor sections must be ordered by strictly increasing role_id: "
                f"previous={previous_role_id} current={section.desc.role_id}"
            )
        if tile_count is not None:
            section_tile_count = len(section.tile_lengths())
            if section_tile_count != tile_count:
                raise ValueError(f"section tile length count mismatch: expected {tile_count}, got {section_tile_count}")
        sections.append(section)
        previous_role_id = section.desc.role_id
        next_cursor = cursor + consumed
        if index + 1 < section_count:
            aligned_cursor = _align_up(next_cursor)
            if aligned_cursor > len(view):
                raise ValueError(f"expected aligned tensor section end at {aligned_cursor} bytes, got {len(view)}")
            _validate_zero_padding(view, next_cursor, aligned_cursor)
            cursor = aligned_cursor
        else:
            cursor = next_cursor

    if cursor != len(view):
        raise ValueError(f"unexpected trailing bytes in tensor body: {len(view) - cursor}")

    return TensorBodyView(tile_index_block=tile_index_block, sections=tuple(sections))


def _parse_tensor_section(payload: memoryview) -> tuple[TensorSectionView, int]:
    if len(payload) < TENSOR_SECTION_DESC_LENGTH:
        raise ValueError(f"expected at least {TENSOR_SECTION_DESC_LENGTH} bytes for tensor section, got {len(payload)}")

    desc = TensorSectionDesc.unpack(payload[:TENSOR_SECTION_DESC_LENGTH])
    body_length = desc.codec_table_bytes + desc.length_table_bytes + desc.payload_bytes
    total_length = TENSOR_SECTION_DESC_LENGTH + body_length
    if len(payload) < total_length:
        raise ValueError(f"expected {total_length} section bytes, got {len(payload)}")

    tail = payload[TENSOR_SECTION_DESC_LENGTH:total_length]
    codec_end = desc.codec_table_bytes
    length_end = codec_end + desc.length_table_bytes
    section = _build_tensor_section_view(
        desc,
        tail[:codec_end],
        tail[codec_end:length_end],
        tail[length_end:],
    )
    section.payload_slices()
    return section, total_length


def _build_tensor_section_view(
    desc: TensorSectionDesc,
    codec_table: memoryview,
    length_table: memoryview,
    payload: memoryview,
) -> TensorSectionView:
    if len(codec_table) != desc.codec_table_bytes:
        raise ValueError(f"codec_table length mismatch: expected {desc.codec_table_bytes}, got {len(codec_table)}")
    if len(length_table) != desc.length_table_bytes:
        raise ValueError(f"length_table length mismatch: expected {desc.length_table_bytes}, got {len(length_table)}")
    if len(payload) != desc.payload_bytes:
        raise ValueError(f"payload length mismatch: expected {desc.payload_bytes}, got {len(payload)}")

    if len(length_table) % LENGTH_ENTRY_STRUCT.size != 0:
        raise ValueError("length_table_bytes must be a multiple of 4")

    if desc.flags & SectionFlags.FIXED_STRIDE:
        if desc.payload_stride_bytes == 0:
            raise ValueError("fixed-stride section requires payload_stride_bytes")
    elif desc.payload_stride_bytes != 0:
        raise ValueError("payload_stride_bytes requires FIXED_STRIDE flag")

    if desc.flags & SectionFlags.MIXED_CODEC:
        if len(codec_table) == 0:
            raise ValueError("mixed-codec section requires codec_table")
    elif len(codec_table) != 0:
        raise ValueError("codec_table requires MIXED_CODEC flag")

    length_entry_count = len(length_table) // LENGTH_ENTRY_STRUCT.size
    if len(codec_table) not in {0, length_entry_count}:
        raise ValueError(f"codec_table entry count mismatch: expected {length_entry_count}, got {len(codec_table)}")

    return TensorSectionView(
        desc=desc,
        codec_table=codec_table,
        length_table=length_table,
        payload=payload,
    )


def _pack_section_sequence(
    sections: Sequence[TensorSectionData],
    *,
    tile_count: int,
) -> list[bytes]:
    packed_sections: list[bytes] = []
    previous_role_id: int | None = None
    for section in sections:
        if previous_role_id is not None and section.role_id <= previous_role_id:
            raise ValueError(
                "tensor sections must be ordered by strictly increasing role_id: "
                f"previous={previous_role_id} current={section.role_id}"
            )
        payloads = section.normalized_tile_payloads()
        if len(payloads) != tile_count:
            raise ValueError(
                "section tile payload count mismatch: "
                f"role_id={section.role_id} expected={tile_count} actual={len(payloads)}"
            )
        packed_sections.append(pack_tensor_section_data(section))
        previous_role_id = section.role_id
    return packed_sections


def _summarize_tensor_sections(section_payloads: Sequence[bytes]) -> tuple[int, int]:
    descriptor_bytes = 0
    payload_bytes = 0
    for payload in section_payloads:
        desc = TensorSectionDesc.unpack(payload[:TENSOR_SECTION_DESC_LENGTH])
        descriptor_bytes += TENSOR_SECTION_DESC_LENGTH + desc.codec_table_bytes + desc.length_table_bytes
        payload_bytes += desc.payload_bytes
    return descriptor_bytes, payload_bytes


def _build_codec_table(
    codec_ids: tuple[int, ...],
    *,
    default_codec_id: int,
    tile_count: int,
) -> bytes:
    if not codec_ids:
        return b""
    if len(codec_ids) != tile_count:
        raise ValueError(f"codec_ids length mismatch: expected {tile_count}, got {len(codec_ids)}")
    if any(codec_id < 0 or codec_id > 0xFF for codec_id in codec_ids):
        raise ValueError(f"codec ids must fit into u8: {codec_ids}")
    if all(codec_id == default_codec_id for codec_id in codec_ids):
        return b""
    return bytes(codec_ids)


def _build_section_payload_blob(
    tile_payloads: tuple[bytes, ...],
    *,
    payload_stride_bytes: int,
) -> tuple[bytes, SectionFlags]:
    if payload_stride_bytes < 0:
        raise ValueError(f"payload_stride_bytes must be non-negative, got {payload_stride_bytes}")

    if payload_stride_bytes == 0:
        return b"".join(tile_payloads), SectionFlags.NONE

    payload = bytearray()
    for item in tile_payloads:
        if len(item) > payload_stride_bytes:
            raise ValueError(f"tile payload length {len(item)} exceeds fixed stride {payload_stride_bytes}")
        payload.extend(item)
        payload.extend(b"\x00" * (payload_stride_bytes - len(item)))
    return bytes(payload), SectionFlags.FIXED_STRIDE


def _validate_tile_ids(tile_ids: tuple[int, ...]) -> None:
    if any(tile_id < 0 for tile_id in tile_ids):
        raise ValueError(f"tile ids must be non-negative: {tile_ids}")
    if any(tile_id > 0xFFFF for tile_id in tile_ids):
        raise ValueError(f"tile ids must fit into u16: {tile_ids}")


def _validate_strictly_increasing_tile_ids(tile_ids: tuple[int, ...]) -> None:
    for previous, current in zip(tile_ids, tile_ids[1:], strict=False):
        if current <= previous:
            raise ValueError(f"tile ids must be strictly increasing for this tile index mode: {tile_ids}")


def _pack_u16(value: int) -> bytes:
    if value < 0 or value > 0xFFFF:
        raise ValueError(f"value out of u16 range: {value}")
    return TILE_ID_ENTRY_STRUCT.pack(value)
