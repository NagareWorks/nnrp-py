"""Suite-owned conformance exporter entrypoints for SDK workflows."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from enum import IntEnum, IntFlag
from pathlib import Path
from typing import Any

from nnrp.core import (
    BodyRegionPrelude,
    BudgetPolicy,
    CacheObjectKind,
    ClientHelloMetadata,
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
    build_opaque_bytes_frame,
    build_result_hint_packet,
    build_structured_event_frame,
    build_token_chunk_frame,
    build_tool_delta_frame,
    build_video_chunk_frame,
    pack_typed_payload_frames,
)

_SUPPORTED_PROTOCOL_VERSION = "nnrp-1-preview3"
_VECTOR_KIND_BY_RECIPE_TYPE = {
    "header": "header",
    "client_hello_metadata": "metadata",
    "session_patch_ack_metadata": "metadata",
    "flow_update_packet": "packet",
    "result_hint_packet": "packet",
    "frame_submit_metadata": "metadata",
    "result_push_metadata": "metadata",
    "body_region_prelude": "body_region",
    "object_reference_block": "object_reference",
    "typed_payload_descriptor": "typed_payload_descriptor",
    "typed_payload_descriptor_region": "typed_payload_descriptor_region",
    "typed_payload_frame_region": "typed_payload_frame_region",
}
_LOSS_TOLERANCE_POLICY_VALUES = {
    "strict": 0,
    "best_effort": 1,
    "low_latency": 2,
    "fire_and_forget": 3,
    "inherit_session": 0xFF,
}


def build_conformance_vector_manifest(
    protocol_version: str,
    recipe_manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    if protocol_version != _SUPPORTED_PROTOCOL_VERSION:
        raise ValueError(f"unsupported protocol version for Python conformance export: {protocol_version}")

    manifest_path = _require_recipe_manifest_path(recipe_manifest_path)
    vector_recipes = _load_vector_recipes(manifest_path, protocol_version)

    return {
        "protocol_version": protocol_version,
        "generator": "nnrp-py",
        "vectors": [_build_vector_entry(recipe) for recipe in vector_recipes],
    }


def write_conformance_vector_manifest(
    protocol_version: str,
    output_path: Path,
    recipe_manifest_path: str | Path | None = None,
) -> None:
    manifest = build_conformance_vector_manifest(
        protocol_version,
        recipe_manifest_path=recipe_manifest_path,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{json.dumps(manifest, indent=2)}\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nnrp-export-conformance-vectors")
    parser.add_argument("--protocol-version", required=True)
    parser.add_argument("--recipe-manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)

    write_conformance_vector_manifest(
        args.protocol_version,
        Path(args.output),
        recipe_manifest_path=Path(args.recipe_manifest),
    )
    return 0


def _require_recipe_manifest_path(recipe_manifest_path: str | Path | None) -> Path:
    if recipe_manifest_path is None:
        raise ValueError("recipe manifest path is required for Python conformance export")

    path = Path(recipe_manifest_path)
    if not path.is_file():
        raise ValueError(f"recipe manifest path does not exist: {path}")
    return path


def _load_vector_recipes(recipe_manifest_path: Path, protocol_version: str) -> list[dict[str, Any]]:
    document = json.loads(recipe_manifest_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("semantic vector recipe manifest must be a JSON object")

    manifest_protocol_version = document.get("protocol_version")
    if manifest_protocol_version != protocol_version:
        raise ValueError(
            "semantic vector recipe manifest protocol version does not match requested export: "
            f"{manifest_protocol_version!r} != {protocol_version!r}"
        )

    raw_vectors = document.get("vectors")
    if not isinstance(raw_vectors, list):
        raise ValueError("semantic vector recipe manifest must contain a vectors list")

    vector_recipes: list[dict[str, Any]] = []
    for raw_vector in raw_vectors:
        if not isinstance(raw_vector, dict):
            raise ValueError("semantic vector recipe entries must be JSON objects")
        vector_recipes.append(raw_vector)
    return vector_recipes


def _build_vector_entry(recipe: dict[str, Any]) -> dict[str, Any]:
    recipe_type = _require_string(recipe, "recipe_type")
    kind = _VECTOR_KIND_BY_RECIPE_TYPE.get(recipe_type)
    if kind is None:
        raise ValueError(f"unsupported semantic vector recipe type: {recipe_type}")

    payload = _build_vector_payload(recipe_type, recipe)
    entry = {
        "name": _require_string(recipe, "name"),
        "kind": kind,
        "hex": payload.hex(),
        "bytes": len(payload),
    }

    description = recipe.get("description")
    if description:
        if not isinstance(description, str):
            raise ValueError("semantic vector description must be a string when present")
        entry["description"] = description
    return entry


def _build_vector_payload(recipe_type: str, recipe: dict[str, Any]) -> bytes:
    if recipe_type == "header":
        return NnrpHeader(
            version_major=_require_int(recipe, "version_major"),
            wire_format=WireFormat(_require_int(recipe, "wire_format")),
            msg_type=_parse_named_enum(MessageType, _require_string(recipe, "message_type")),
            flags=_parse_named_flags(HeaderFlags, _require_string_list(recipe, "flags")),
            meta_len=_require_int(recipe, "meta_len"),
            body_len=_require_int(recipe, "body_len"),
            session_id=_require_int(recipe, "session_id"),
            frame_id=_require_int(recipe, "frame_id"),
            view_id=_require_int(recipe, "view_id"),
            route_id=_require_int(recipe, "route_id"),
            trace_id=_require_int(recipe, "trace_id"),
        ).pack()

    if recipe_type == "client_hello_metadata":
        return ClientHelloMetadata(
            min_version_major=_require_int(recipe, "min_version_major"),
            max_version_major=_require_int(recipe, "max_version_major"),
            supported_wire_format_bitmap=_require_int(recipe, "supported_wire_format_bitmap"),
            supported_profile_bitmap=_require_int(recipe, "supported_profile_bitmap"),
            supported_payload_kind_bitmap=_parse_named_flags(
                PayloadKind, _require_string_list(recipe, "supported_payload_kind_bitmap")
            ),
            supported_codec_bitmap=_require_int(recipe, "supported_codec_bitmap"),
            supported_compression_bitmap=_require_int(recipe, "supported_compression_bitmap"),
            supported_dtype_bitmap=_require_int(recipe, "supported_dtype_bitmap"),
            supported_layout_bitmap=_require_int(recipe, "supported_layout_bitmap"),
            cache_digest_bitmap=_require_int(recipe, "cache_digest_bitmap"),
            cache_object_bitmap=_require_int(recipe, "cache_object_bitmap"),
            cache_namespace_count=_require_int(recipe, "cache_namespace_count"),
            max_lane_count=_require_int(recipe, "max_lane_count"),
            max_cache_entries=_require_int(recipe, "max_cache_entries"),
            max_cache_bytes=_require_int(recipe, "max_cache_bytes"),
            target_cadence_x100=_require_int(recipe, "target_cadence_x100"),
            latency_budget_ms=_require_int(recipe, "latency_budget_ms"),
            quality_tier=_require_int(recipe, "quality_tier"),
            degrade_policy=_require_int(recipe, "degrade_policy"),
            requested_session_id=_require_int(recipe, "requested_session_id"),
            auth_bytes=_require_int(recipe, "auth_bytes"),
            control_extension_bytes=_require_int(recipe, "control_extension_bytes"),
        ).pack()

    if recipe_type == "session_patch_ack_metadata":
        return SessionPatchAckMetadata(
            ack_status=_parse_named_enum(
                SessionPatchAckStatus,
                _require_string(recipe, "ack_status"),
            ),
            reject_reason=_parse_named_enum(
                SessionPatchRejectReason,
                _require_string(recipe, "reject_reason"),
            ),
            applied_patch_mask=_parse_named_flags(
                SessionPatchField,
                _require_string_list(recipe, "applied_patch_mask"),
            ),
            rejected_patch_mask=_parse_named_flags(
                SessionPatchField,
                _require_string_list(recipe, "rejected_patch_mask"),
            ),
            retry_after_ms=_require_int(recipe, "retry_after_ms"),
            effective_profile_id=_require_int(recipe, "effective_profile_id"),
            effective_target_cadence_x100=_require_int(recipe, "effective_target_cadence_x100"),
            effective_quality_tier=_require_int(recipe, "effective_quality_tier"),
            effective_degrade_policy=_require_int(recipe, "effective_degrade_policy"),
            effective_lane_mask=_require_int(recipe, "effective_lane_mask"),
            effective_codec_bitmap=_require_int(recipe, "effective_codec_bitmap"),
            effective_compression_bitmap=_require_int(recipe, "effective_compression_bitmap"),
            profile_patch_ack_bytes=_require_int(recipe, "profile_patch_ack_bytes"),
            reserved0=_require_int(recipe, "reserved0"),
        ).pack()

    if recipe_type == "flow_update_packet":
        return build_flow_update_packet(
            metadata=FlowUpdateMetadata(
                scope_kind=_parse_named_enum(FlowUpdateScopeKind, _require_string(recipe, "scope_kind")),
                update_reason=_parse_named_enum(FlowUpdateReason, _require_string(recipe, "update_reason")),
                backpressure_level=_parse_named_enum(
                    FlowUpdateBackpressureLevel, _require_string(recipe, "backpressure_level")
                ),
                connection_credit=_require_int(recipe, "connection_credit"),
                session_credit=_require_int(recipe, "session_credit"),
                operation_credit=_require_int(recipe, "operation_credit"),
                operation_id=_require_int(recipe, "operation_id"),
                retry_after_ms=_require_int(recipe, "retry_after_ms"),
                credit_epoch=_require_int(recipe, "credit_epoch"),
                flags=_parse_named_flags(FlowUpdateFlags, _require_string_list(recipe, "flow_update_flags")),
            ),
            session_id=_require_int(recipe, "session_id"),
            route_id=_require_int(recipe, "route_id"),
            trace_id=_require_int(recipe, "trace_id"),
        ).pack()

    if recipe_type == "result_hint_packet":
        return build_result_hint_packet(
            metadata=ResultHintMetadata(
                applied_budget_policy=_parse_named_enum(
                    ResultHintBudgetPolicy, _require_string(recipe, "applied_budget_policy")
                ),
                congestion_state=_parse_named_enum(
                    ResultHintCongestionState, _require_string(recipe, "congestion_state")
                ),
                reason=_parse_named_enum(ResultHintReason, _require_string(recipe, "reason")),
                retry_after_ms=_require_int(recipe, "retry_after_ms"),
            ),
            session_id=_require_int(recipe, "session_id"),
            frame_id=_require_int(recipe, "frame_id"),
            route_id=_require_int(recipe, "route_id"),
            trace_id=_require_int(recipe, "trace_id"),
        ).pack()

    if recipe_type == "frame_submit_metadata":
        return FrameSubmitMetadata(
            src_width=_require_int(recipe, "src_width"),
            src_height=_require_int(recipe, "src_height"),
            tile_width=_require_int(recipe, "tile_width"),
            tile_height=_require_int(recipe, "tile_height"),
            tile_count=_require_int(recipe, "tile_count"),
            section_count=_require_int(recipe, "section_count"),
            frame_class=_require_int(recipe, "frame_class"),
            input_profile=_parse_named_enum(InputProfile, _require_string(recipe, "input_profile")),
            tile_index_mode=_parse_named_enum(TileIndexMode, _require_string(recipe, "tile_index_mode")),
            reserved0=_require_int(recipe, "reserved0"),
            latency_budget_ms=_require_int(recipe, "latency_budget_ms"),
            target_fps_x100=_require_int(recipe, "target_fps_x100"),
            retry_of_frame=_require_int(recipe, "retry_of_frame"),
            tile_base_id=_require_int(recipe, "tile_base_id"),
            camera_bytes=_require_int(recipe, "camera_bytes"),
            tile_index_bytes=_require_int(recipe, "tile_index_bytes"),
            submit_mode=_parse_named_enum(SubmitMode, _require_string(recipe, "submit_mode")),
            budget_policy=_parse_named_flags(BudgetPolicy, _require_string_list(recipe, "budget_policy")),
            loss_tolerance_policy=_parse_loss_tolerance_policy(_require_string(recipe, "loss_tolerance_policy")),
            object_ref_mask=_require_int(recipe, "object_ref_mask"),
            dependency_frame_id=_require_int(recipe, "dependency_frame_id"),
            payload_kind_bitmap=_parse_named_flags(PayloadKind, _require_string_list(recipe, "payload_kind_bitmap")),
            payload_frame_count=_require_int(recipe, "payload_frame_count"),
        ).pack()

    if recipe_type == "result_push_metadata":
        return ResultPushMetadata(
            status_code=_require_int(recipe, "status_code"),
            result_flags=_parse_named_flags(ResultFlags, _require_string_list(recipe, "result_flags")),
            section_count=_require_int(recipe, "section_count"),
            tile_count=_require_int(recipe, "tile_count"),
            active_profile_id=_require_int(recipe, "active_profile_id"),
            reserved0=_require_int(recipe, "reserved0"),
            inference_ms=_require_int(recipe, "inference_ms"),
            queue_ms=_require_int(recipe, "queue_ms"),
            server_total_ms=_require_int(recipe, "server_total_ms"),
            reserved1=_require_int(recipe, "reserved1"),
            tile_base_id=_require_int(recipe, "tile_base_id"),
            tile_index_bytes=_require_int(recipe, "tile_index_bytes"),
            result_class=_parse_named_enum(ResultClass, _require_string(recipe, "result_class")),
            applied_budget_policy=_parse_named_flags(
                BudgetPolicy,
                _require_string_list(recipe, "applied_budget_policy"),
            ),
            reused_frame_id=_require_int(recipe, "reused_frame_id"),
            covered_tile_count=_require_int(recipe, "covered_tile_count"),
            dropped_tile_count=_require_int(recipe, "dropped_tile_count"),
            payload_kind_bitmap=_parse_named_flags(PayloadKind, _require_string_list(recipe, "payload_kind_bitmap")),
            payload_frame_count=_require_int(recipe, "payload_frame_count"),
        ).pack()

    if recipe_type == "body_region_prelude":
        return BodyRegionPrelude(
            inline_object_bytes=_require_int(recipe, "inline_object_bytes"),
            object_reference_bytes=_require_int(recipe, "object_reference_bytes"),
            typed_payload_descriptor_bytes=_require_int(recipe, "typed_payload_descriptor_bytes"),
            typed_payload_frame_bytes=_require_int(recipe, "typed_payload_frame_bytes"),
            extension_descriptor_bytes=_require_int(recipe, "extension_descriptor_bytes"),
            extension_payload_bytes=_require_int(recipe, "extension_payload_bytes"),
        ).pack()

    if recipe_type == "object_reference_block":
        return ObjectReferenceBlock(
            object_kind=_parse_named_enum(CacheObjectKind, _require_string(recipe, "object_kind")),
            ref_flags=_require_int(recipe, "ref_flags"),
            cache_namespace=_require_int(recipe, "cache_namespace"),
            cache_key_hi=_require_int(recipe, "cache_key_hi"),
            cache_key_lo=_require_int(recipe, "cache_key_lo"),
        ).pack()

    if recipe_type == "typed_payload_descriptor":
        return TypedPayloadDescriptor(
            payload_kind=_parse_named_enum(PayloadKind, _require_string(recipe, "payload_kind")),
            descriptor_flags=_require_int(recipe, "descriptor_flags"),
            profile_id=_require_int(recipe, "profile_id"),
            payload_offset=_require_int(recipe, "payload_offset"),
            payload_length=_require_int(recipe, "payload_length"),
        ).pack()

    if recipe_type in {"typed_payload_descriptor_region", "typed_payload_frame_region"}:
        descriptor_region, payload_region = pack_typed_payload_frames(
            tuple(_build_typed_payload_frame(frame) for frame in _require_object_list(recipe, "frames"))
        )
        return descriptor_region if recipe_type == "typed_payload_descriptor_region" else payload_region

    raise ValueError(f"unsupported semantic vector recipe type: {recipe_type}")


def _build_typed_payload_frame(frame_recipe: dict[str, Any]) -> Any:
    payload_kind_name = _require_string(frame_recipe, "payload_kind")
    payload = _require_frame_payload(frame_recipe)
    profile_id = _require_int(frame_recipe, "profile_id")

    if payload_kind_name == "token_chunk":
        return build_token_chunk_frame(payload, profile_id=profile_id)
    if payload_kind_name == "audio_chunk":
        return build_audio_chunk_frame(payload, profile_id=profile_id)
    if payload_kind_name == "video_chunk":
        return build_video_chunk_frame(payload, profile_id=profile_id)
    if payload_kind_name == "structured_event":
        return build_structured_event_frame(payload, profile_id=profile_id)
    if payload_kind_name == "tool_delta":
        return build_tool_delta_frame(payload, profile_id=profile_id)
    if payload_kind_name == "opaque_bytes":
        return build_opaque_bytes_frame(payload, profile_id=profile_id)
    raise ValueError(f"unsupported typed payload frame recipe kind: {payload_kind_name}")


def _require_frame_payload(frame_recipe: dict[str, Any]) -> bytes:
    payload_utf8 = frame_recipe.get("payload_utf8")
    if payload_utf8 is not None:
        if not isinstance(payload_utf8, str):
            raise ValueError("typed payload frame recipe payload_utf8 must be a string")
        return payload_utf8.encode("utf-8")

    payload_hex = frame_recipe.get("payload_hex")
    if payload_hex is not None:
        if not isinstance(payload_hex, str):
            raise ValueError("typed payload frame recipe payload_hex must be a string")
        return bytes.fromhex(payload_hex)

    raise ValueError("typed payload frame recipe must define payload_utf8 or payload_hex")


def _parse_named_enum(enum_type: type[IntEnum], value: str) -> IntEnum:
    member_name = value.upper()
    try:
        return enum_type[member_name]
    except KeyError as exc:
        raise ValueError(f"unsupported {enum_type.__name__} name in semantic vector recipe: {value}") from exc


def _parse_named_flags(enum_type: type[IntFlag], values: list[str]) -> IntFlag:
    result = enum_type(0)
    for value in values:
        member_name = value.upper()
        try:
            result |= enum_type[member_name]
        except KeyError as exc:
            raise ValueError(f"unsupported {enum_type.__name__} flag in semantic vector recipe: {value}") from exc
    return result


def _parse_loss_tolerance_policy(value: str) -> int:
    parsed = _LOSS_TOLERANCE_POLICY_VALUES.get(value)
    if parsed is None:
        raise ValueError(f"unsupported loss tolerance policy in semantic vector recipe: {value}")
    return parsed


def _require_string(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise ValueError(f"semantic vector recipe field {field_name!r} must be a string")
    return value


def _require_int(payload: dict[str, Any], field_name: str) -> int:
    value = payload.get(field_name)
    if not isinstance(value, int):
        raise ValueError(f"semantic vector recipe field {field_name!r} must be an integer")
    return value


def _require_string_list(payload: dict[str, Any], field_name: str) -> list[str]:
    value = payload.get(field_name)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"semantic vector recipe field {field_name!r} must be a string list")
    return list(value)


def _require_object_list(payload: dict[str, Any], field_name: str) -> list[dict[str, Any]]:
    value = payload.get(field_name)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"semantic vector recipe field {field_name!r} must be an object list")
    return list(value)


if __name__ == "__main__":
    raise SystemExit(main())