"""Cross-language golden-vector helpers for host SDK alignment."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

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
    InputProfile,
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
    SessionPatchAckStatus,
    SessionPatchField,
    SessionPatchRejectReason,
    SubmitMode,
    TileIndexMode,
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
from nnrp.core.messages import ClientHelloMetadata

_MANIFEST_SCHEMA = "nnrp.cross-language-golden-vectors.v1"
_ALLOWED_VECTOR_KINDS = frozenset(
    {
        "header",
        "metadata",
        "packet",
        "body_region",
        "object_reference",
        "typed_payload_descriptor",
        "typed_payload_descriptor_region",
        "typed_payload_frame_region",
    }
)


@dataclass(frozen=True, slots=True)
class CrossLanguageGoldenVector:
    name: str
    kind: str
    hex_payload: str
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("golden vector name must not be empty")
        if self.kind not in _ALLOWED_VECTOR_KINDS:
            raise ValueError(f"unsupported golden vector kind: {self.kind}")
        normalized_hex_payload = self.hex_payload.lower()
        if len(normalized_hex_payload) % 2 != 0:
            raise ValueError("golden vector hex payload must contain an even number of characters")
        try:
            bytes.fromhex(normalized_hex_payload)
        except ValueError as exc:
            raise ValueError("golden vector hex payload must be valid hexadecimal") from exc
        object.__setattr__(self, "hex_payload", normalized_hex_payload)

    @property
    def payload(self) -> bytes:
        return bytes.fromhex(self.hex_payload)

    @property
    def byte_length(self) -> int:
        return len(self.payload)

    def to_manifest_entry(self) -> dict[str, Any]:
        entry = {
            "name": self.name,
            "kind": self.kind,
            "hex": self.hex_payload,
            "bytes": self.byte_length,
        }
        if self.description:
            entry["description"] = self.description
        return entry


def export_cross_language_golden_vectors() -> tuple[CrossLanguageGoldenVector, ...]:
    descriptor_region, payload_region = pack_typed_payload_frames(
        (
            build_token_chunk_frame(b"tok", profile_id=1),
            build_audio_chunk_frame(b"au", profile_id=2),
            build_video_chunk_frame(b"video", profile_id=3),
            build_structured_event_frame(b"evt", profile_id=4),
        )
    )

    return (
        _build_header_golden_vector(),
        _build_client_hello_metadata_golden_vector(),
        _build_session_patch_ack_metadata_golden_vector(),
        _build_flow_update_packet_golden_vector(),
        _build_result_hint_packet_golden_vector(),
        _build_frame_submit_metadata_golden_vector(),
        _build_result_push_metadata_golden_vector(),
        _build_body_region_prelude_golden_vector(),
        _build_object_reference_block_golden_vector(),
        _build_typed_payload_descriptor_golden_vector(),
        CrossLanguageGoldenVector(
            name="current.typed_payload.frame_descriptor_region",
            kind="typed_payload_descriptor_region",
            hex_payload=descriptor_region.hex(),
            description="current typed-payload descriptor region for token/audio/video/event frames",
        ),
        CrossLanguageGoldenVector(
            name="current.typed_payload.frame_region",
            kind="typed_payload_frame_region",
            hex_payload=payload_region.hex(),
            description="current typed-payload frame region for token/audio/video/event frames",
        ),
    )


def render_cross_language_golden_vectors_json(
    vectors: tuple[CrossLanguageGoldenVector, ...] | None = None,
) -> str:
    payload = {
        "schema": _MANIFEST_SCHEMA,
        "vectors": [vector.to_manifest_entry() for vector in (vectors or export_cross_language_golden_vectors())],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def parse_cross_language_golden_vectors_json(
    payload: str | bytes,
) -> tuple[CrossLanguageGoldenVector, ...]:
    raw_payload = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    document = json.loads(raw_payload)
    if not isinstance(document, dict):
        raise ValueError("golden vector manifest must be a JSON object")
    if document.get("schema") != _MANIFEST_SCHEMA:
        raise ValueError(f"unsupported golden vector manifest schema: {document.get('schema')!r}")
    raw_vectors = document.get("vectors")
    if not isinstance(raw_vectors, list):
        raise ValueError("golden vector manifest must contain a vectors list")

    vectors: list[CrossLanguageGoldenVector] = []
    seen_names: set[str] = set()
    for raw_vector in raw_vectors:
        if not isinstance(raw_vector, dict):
            raise ValueError("golden vector entries must be JSON objects")
        vector = CrossLanguageGoldenVector(
            name=_require_manifest_string(raw_vector, "name"),
            kind=_require_manifest_string(raw_vector, "kind"),
            hex_payload=_require_manifest_string(raw_vector, "hex"),
            description=_optional_manifest_string(raw_vector, "description"),
        )
        if vector.name in seen_names:
            raise ValueError(f"duplicate golden vector name: {vector.name}")
        expected_byte_length = raw_vector.get("bytes")
        if expected_byte_length is not None and expected_byte_length != vector.byte_length:
            raise ValueError(
                f"golden vector byte count does not match hex payload: {expected_byte_length} != {vector.byte_length}"
            )
        vectors.append(vector)
        seen_names.add(vector.name)
    return tuple(vectors)


def _require_manifest_string(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise ValueError(f"golden vector field {field_name!r} must be a string")
    return value


def _optional_manifest_string(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name, "")
    if not isinstance(value, str):
        raise ValueError(f"golden vector field {field_name!r} must be a string when present")
    return value


def _build_header_golden_vector() -> CrossLanguageGoldenVector:
    payload = NnrpHeader(
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
    ).pack()
    return CrossLanguageGoldenVector(
        name="current.header.frame_submit_ack_required_keyframe",
        kind="header",
        hex_payload=payload.hex(),
        description="current FRAME_SUBMIT header golden vector",
    )


def _build_client_hello_metadata_golden_vector() -> CrossLanguageGoldenVector:
    payload = ClientHelloMetadata(
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
    ).pack()
    return CrossLanguageGoldenVector(
        name="current.metadata.client_hello",
        kind="metadata",
        hex_payload=payload.hex(),
        description="current CLIENT_HELLO fixed metadata golden vector",
    )


def _build_session_patch_ack_metadata_golden_vector() -> CrossLanguageGoldenVector:
    payload = SessionPatchAckMetadata(
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
    ).pack()
    return CrossLanguageGoldenVector(
        name="current.metadata.session_patch_ack",
        kind="metadata",
        hex_payload=payload.hex(),
        description="current SESSION_PATCH_ACK fixed metadata golden vector",
    )


def _build_flow_update_packet_golden_vector() -> CrossLanguageGoldenVector:
    payload = build_flow_update_packet(
        metadata=FlowUpdateMetadata(
            scope_kind=FlowUpdateScopeKind.SESSION,
            update_reason=FlowUpdateReason.CONGESTION,
            backpressure_level=FlowUpdateBackpressureLevel.HARD,
            session_credit=1,
            retry_after_ms=40,
            credit_epoch=5,
            flags=FlowUpdateFlags.CREDIT_VALID | FlowUpdateFlags.RETRY_AFTER_VALID,
        ),
        session_id=21,
        route_id=6,
        trace_id=13,
    ).pack()
    return CrossLanguageGoldenVector(
        name="current.packet.flow_update",
        kind="packet",
        hex_payload=payload.hex(),
        description="current FLOW_UPDATE packet golden vector",
    )


def _build_result_hint_packet_golden_vector() -> CrossLanguageGoldenVector:
    payload = build_result_hint_packet(
        metadata=ResultHintMetadata(
            applied_budget_policy=ResultHintBudgetPolicy.STALE_REUSE,
            congestion_state=ResultHintCongestionState.SATURATED,
            reason=ResultHintReason.BUDGET_EXCEEDED,
            retry_after_ms=60,
        ),
        session_id=21,
        frame_id=303,
        route_id=7,
        trace_id=14,
    ).pack()
    return CrossLanguageGoldenVector(
        name="current.packet.result_hint",
        kind="packet",
        hex_payload=payload.hex(),
        description="current RESULT_HINT packet golden vector",
    )


def _build_frame_submit_metadata_golden_vector() -> CrossLanguageGoldenVector:
    payload = FrameSubmitMetadata(
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
    ).pack()
    return CrossLanguageGoldenVector(
        name="current.metadata.frame_submit",
        kind="metadata",
        hex_payload=payload.hex(),
        description="current FRAME_SUBMIT metadata golden vector",
    )


def _build_result_push_metadata_golden_vector() -> CrossLanguageGoldenVector:
    payload = ResultPushMetadata(
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
    ).pack()
    return CrossLanguageGoldenVector(
        name="current.metadata.result_push",
        kind="metadata",
        hex_payload=payload.hex(),
        description="current RESULT_PUSH metadata golden vector",
    )


def _build_body_region_prelude_golden_vector() -> CrossLanguageGoldenVector:
    payload = BodyRegionPrelude(
        inline_object_bytes=24,
        object_reference_bytes=16,
        typed_payload_descriptor_bytes=16,
        typed_payload_frame_bytes=14,
        extension_descriptor_bytes=16,
        extension_payload_bytes=5,
    ).pack()
    return CrossLanguageGoldenVector(
        name="current.body_region.prelude",
        kind="body_region",
        hex_payload=payload.hex(),
        description="current body region prelude golden vector",
    )


def _build_object_reference_block_golden_vector() -> CrossLanguageGoldenVector:
    payload = ObjectReferenceBlock(
        object_kind=CacheObjectKind.TILE_INDEX_BLOCK,
        ref_flags=0,
        cache_namespace=7,
        cache_key_hi=0x11223344,
        cache_key_lo=0x55667788,
    ).pack()
    return CrossLanguageGoldenVector(
        name="current.object_reference.tile_index_block",
        kind="object_reference",
        hex_payload=payload.hex(),
        description="current TILE_INDEX_BLOCK reference golden vector",
    )


def _build_typed_payload_descriptor_golden_vector() -> CrossLanguageGoldenVector:
    payload = TypedPayloadDescriptor(
        payload_kind=PayloadKind.STRUCTURED_EVENT,
        descriptor_flags=0,
        profile_id=3,
        payload_offset=4,
        payload_length=7,
    ).pack()
    return CrossLanguageGoldenVector(
        name="current.typed_payload.descriptor",
        kind="typed_payload_descriptor",
        hex_payload=payload.hex(),
        description="current typed-payload descriptor golden vector",
    )
