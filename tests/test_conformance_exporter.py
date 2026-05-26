import json
import os
from pathlib import Path

import pytest

from nnrp.tools.conformance import build_conformance_vector_manifest


def _write_recipe_manifest(tmp_path: Path, document: object) -> Path:
    recipe_manifest = tmp_path / "semantic-vectors.json"
    if isinstance(document, str):
        recipe_manifest.write_text(document, encoding="utf-8")
    else:
        recipe_manifest.write_text(json.dumps(document), encoding="utf-8")
    return recipe_manifest


def _resolve_shared_recipe_manifest() -> Path | None:
    manifest_path = os.environ.get("NNRP_CONFORMANCE_RECIPE_MANIFEST")
    if manifest_path:
        candidate = Path(manifest_path)
        if candidate.is_file():
            return candidate

    repo_root = Path(__file__).resolve().parents[1]
    candidates = (
        repo_root / "nnrp-conformance-action" / "protocol" / "nnrp-1-preview3" / "vectors" / "semantic-vectors.json",
        repo_root.parent / "nnrp-conformance" / "protocol" / "nnrp-1-preview3" / "vectors" / "semantic-vectors.json",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def test_build_conformance_vector_manifest_preview3(tmp_path) -> None:
    recipe_manifest = _write_recipe_manifest(
        tmp_path,
        {
            "protocol_version": "nnrp-1-preview3",
            "vectors": [
                {
                    "recipe_type": "header",
                    "name": "current.header.frame_submit_ack_required_keyframe",
                    "description": (
                        "Preview2 common header golden vector for a "
                        "FRAME_SUBMIT keyframe with ACK_REQUIRED."
                    ),
                    "version_major": 1,
                    "wire_format": 0,
                    "message_type": "frame_submit",
                    "flags": ["ack_required", "keyframe"],
                    "meta_len": 48,
                    "body_len": 4096,
                    "session_id": 7,
                    "frame_id": 11,
                    "view_id": 2,
                    "route_id": 0,
                    "trace_id": 123456789,
                },
                {
                    "recipe_type": "typed_payload_frame_region",
                    "name": "current.typed_payload.frame_region",
                    "description": (
                        "Preview2 typed-payload frame region golden vector for "
                        "token/audio/video/event frames."
                    ),
                    "frames": [
                        {"payload_kind": "token_chunk", "profile_id": 1, "payload_utf8": "tok"},
                        {"payload_kind": "audio_chunk", "profile_id": 2, "payload_utf8": "au"},
                        {"payload_kind": "video_chunk", "profile_id": 3, "payload_utf8": "video"},
                        {"payload_kind": "structured_event", "profile_id": 4, "payload_utf8": "evt"},
                    ],
                },
            ],
        },
    )

    manifest = build_conformance_vector_manifest("nnrp-1-preview3", recipe_manifest_path=recipe_manifest)

    assert manifest["protocol_version"] == "nnrp-1-preview3"
    assert manifest["generator"] == "nnrp-py"
    assert len(manifest["vectors"]) == 2
    assert manifest["vectors"][0]["name"] == "current.header.frame_submit_ack_required_keyframe"
    assert manifest["vectors"][1]["hex"] == "746f6b6175766964656f657674"


def test_build_conformance_vector_manifest_rejects_protocol_mismatch(tmp_path) -> None:
    recipe_manifest = _write_recipe_manifest(tmp_path, {"protocol_version": "nnrp-0-invalid", "vectors": []})

    with pytest.raises(ValueError, match="protocol version does not match requested export"):
        build_conformance_vector_manifest("nnrp-1-preview3", recipe_manifest_path=recipe_manifest)


def test_build_conformance_vector_manifest_supports_all_preview3_recipe_types(tmp_path) -> None:
    recipe_manifest = _write_recipe_manifest(
        tmp_path,
        {
            "protocol_version": "nnrp-1-preview3",
            "vectors": [
                {
                    "recipe_type": "header",
                    "name": "current.header.frame_submit_ack_required_keyframe",
                    "description": (
                        "Preview2 common header golden vector for a "
                        "FRAME_SUBMIT keyframe with ACK_REQUIRED."
                    ),
                    "version_major": 1,
                    "wire_format": 0,
                    "message_type": "frame_submit",
                    "flags": ["ack_required", "keyframe"],
                    "meta_len": 48,
                    "body_len": 4096,
                    "session_id": 7,
                    "frame_id": 11,
                    "view_id": 2,
                    "route_id": 0,
                    "trace_id": 123456789,
                },
                {
                    "recipe_type": "client_hello_metadata",
                    "name": "current.metadata.client_hello",
                    "description": "Preview2 CLIENT_HELLO fixed metadata golden vector.",
                    "min_version_major": 1,
                    "max_version_major": 1,
                    "supported_wire_format_bitmap": 1,
                    "supported_profile_bitmap": 1,
                    "supported_payload_kind_bitmap": ["tensor"],
                    "supported_codec_bitmap": 7,
                    "supported_compression_bitmap": 3,
                    "supported_dtype_bitmap": 31,
                    "supported_layout_bitmap": 3,
                    "cache_digest_bitmap": 1,
                    "cache_object_bitmap": 7,
                    "cache_namespace_count": 4,
                    "max_lane_count": 2,
                    "max_cache_entries": 256,
                    "max_cache_bytes": 8388608,
                    "target_cadence_x100": 6000,
                    "latency_budget_ms": 100,
                    "quality_tier": 2,
                    "degrade_policy": 2,
                    "requested_session_id": 0,
                    "auth_bytes": 96,
                    "control_extension_bytes": 0,
                },
                {
                    "recipe_type": "session_patch_ack_metadata",
                    "name": "current.metadata.session_patch_ack",
                    "description": "Preview2 SESSION_PATCH_ACK fixed metadata golden vector.",
                    "ack_status": "partially_applied",
                    "reject_reason": "unsupported_strategy",
                    "applied_patch_mask": ["target_cadence", "quality_tier", "active_lane_mask"],
                    "rejected_patch_mask": ["preferred_codec"],
                    "retry_after_ms": 0,
                    "effective_profile_id": 1,
                    "effective_target_cadence_x100": 9000,
                    "effective_quality_tier": 2,
                    "effective_degrade_policy": 2,
                    "effective_lane_mask": 3,
                    "effective_codec_bitmap": 1,
                    "effective_compression_bitmap": 3,
                    "profile_patch_ack_bytes": 0,
                    "reserved0": 0,
                },
                {
                    "recipe_type": "flow_update_packet",
                    "name": "current.packet.flow_update",
                    "description": "Preview2 FLOW_UPDATE packet golden vector.",
                    "version_major": 1,
                    "wire_format": 0,
                    "flags": [],
                    "session_id": 21,
                    "route_id": 6,
                    "trace_id": 13,
                    "scope_kind": "session",
                    "update_reason": "congestion",
                    "backpressure_level": "hard",
                    "connection_credit": 0,
                    "session_credit": 1,
                    "operation_credit": 0,
                    "operation_id": 0,
                    "retry_after_ms": 40,
                    "credit_epoch": 5,
                    "flow_update_flags": ["credit_valid", "retry_after_valid"],
                },
                {
                    "recipe_type": "result_hint_packet",
                    "name": "current.packet.result_hint",
                    "description": "Preview2 RESULT_HINT packet golden vector.",
                    "version_major": 1,
                    "wire_format": 0,
                    "flags": [],
                    "session_id": 21,
                    "frame_id": 303,
                    "route_id": 7,
                    "trace_id": 14,
                    "applied_budget_policy": "stale_reuse",
                    "congestion_state": "saturated",
                    "reason": "budget_exceeded",
                    "retry_after_ms": 60,
                },
                {
                    "recipe_type": "frame_submit_metadata",
                    "name": "current.metadata.frame_submit",
                    "description": "Preview2 FRAME_SUBMIT fixed metadata golden vector for mixed submit mode.",
                    "src_width": 640,
                    "src_height": 360,
                    "tile_width": 32,
                    "tile_height": 32,
                    "tile_count": 84,
                    "section_count": 2,
                    "frame_class": 1,
                    "input_profile": "dense_luma_frame",
                    "tile_index_mode": "dense_range",
                    "reserved0": 0,
                    "latency_budget_ms": 100,
                    "target_fps_x100": 6000,
                    "retry_of_frame": 7,
                    "tile_base_id": 0,
                    "camera_bytes": 192,
                    "tile_index_bytes": 0,
                    "submit_mode": "mixed",
                    "budget_policy": ["allow_partial", "allow_degraded"],
                    "loss_tolerance_policy": "inherit_session",
                    "object_ref_mask": 3,
                    "dependency_frame_id": 41,
                    "payload_kind_bitmap": ["tensor", "structured_event"],
                    "payload_frame_count": 2,
                },
                {
                    "recipe_type": "result_push_metadata",
                    "name": "current.metadata.result_push",
                    "description": "Preview2 RESULT_PUSH fixed metadata golden vector for partial stale-reuse results.",
                    "status_code": 0,
                    "result_flags": ["partial"],
                    "section_count": 1,
                    "tile_count": 84,
                    "active_profile_id": 2,
                    "reserved0": 0,
                    "inference_ms": 843,
                    "queue_ms": 2,
                    "server_total_ms": 846,
                    "reserved1": 0,
                    "tile_base_id": 0,
                    "tile_index_bytes": 16,
                    "result_class": "partial",
                    "applied_budget_policy": ["allow_partial"],
                    "reused_frame_id": 41,
                    "covered_tile_count": 53,
                    "dropped_tile_count": 31,
                    "payload_kind_bitmap": ["tensor", "token_chunk"],
                    "payload_frame_count": 3,
                },
                {
                    "recipe_type": "body_region_prelude",
                    "name": "current.body_region.prelude",
                    "description": "Preview2 body-region prelude golden vector.",
                    "inline_object_bytes": 24,
                    "object_reference_bytes": 16,
                    "typed_payload_descriptor_bytes": 16,
                    "typed_payload_frame_bytes": 14,
                    "extension_descriptor_bytes": 16,
                    "extension_payload_bytes": 5,
                },
                {
                    "recipe_type": "object_reference_block",
                    "name": "current.object_reference.tile_index_block",
                    "description": "Preview2 object-reference block golden vector for a tile-index cache object.",
                    "object_kind": "tile_index_block",
                    "ref_flags": 0,
                    "cache_namespace": 7,
                    "cache_key_hi": 287454020,
                    "cache_key_lo": 1432778632,
                },
                {
                    "recipe_type": "typed_payload_descriptor",
                    "name": "current.typed_payload.descriptor",
                    "description": "Preview2 typed-payload descriptor golden vector.",
                    "payload_kind": "structured_event",
                    "descriptor_flags": 0,
                    "profile_id": 3,
                    "payload_offset": 4,
                    "payload_length": 7,
                },
                {
                    "recipe_type": "typed_payload_descriptor_region",
                    "name": "current.typed_payload.frame_descriptor_region",
                    "description": (
                        "Preview2 typed-payload descriptor region golden vector "
                        "for token/audio/video/event frames."
                    ),
                    "frames": [
                        {"payload_kind": "token_chunk", "profile_id": 1, "payload_utf8": "tok"},
                        {"payload_kind": "audio_chunk", "profile_id": 2, "payload_utf8": "au"},
                        {"payload_kind": "video_chunk", "profile_id": 3, "payload_utf8": "video"},
                        {"payload_kind": "structured_event", "profile_id": 4, "payload_utf8": "evt"},
                    ],
                },
                {
                    "recipe_type": "typed_payload_frame_region",
                    "name": "current.typed_payload.frame_region",
                    "description": (
                        "Preview2 typed-payload frame region golden vector for "
                        "token/audio/video/event frames."
                    ),
                    "frames": [
                        {"payload_kind": "token_chunk", "profile_id": 1, "payload_utf8": "tok"},
                        {"payload_kind": "audio_chunk", "profile_id": 2, "payload_utf8": "au"},
                        {"payload_kind": "video_chunk", "profile_id": 3, "payload_utf8": "video"},
                        {"payload_kind": "structured_event", "profile_id": 4, "payload_utf8": "evt"},
                    ],
                },
            ],
        },
    )

    manifest = build_conformance_vector_manifest("nnrp-1-preview3", recipe_manifest_path=recipe_manifest)

    assert manifest["protocol_version"] == "nnrp-1-preview3"
    assert manifest["generator"] == "nnrp-py"
    assert len(manifest["vectors"]) == 12
    assert manifest["vectors"][-1]["name"] == "current.typed_payload.frame_region"
    assert manifest["vectors"][-1]["hex"] == "746f6b6175766964656f657674"
    assert manifest["vectors"][-2]["hex"] == (
        "02000100000000000300000000000000"
        "04000200030000000200000000000000"
        "08000300050000000500000000000000"
        "100004000a0000000300000000000000"
    )


def test_build_conformance_vector_manifest_uses_shared_preview3_recipe_when_available() -> None:
    recipe_manifest = _resolve_shared_recipe_manifest()
    if recipe_manifest is None:
        pytest.skip("shared preview3 semantic vector recipe manifest is not available in this environment")

    manifest = build_conformance_vector_manifest("nnrp-1-preview3", recipe_manifest_path=recipe_manifest)

    assert manifest["protocol_version"] == "nnrp-1-preview3"
    assert manifest["generator"] == "nnrp-py"
    assert len(manifest["vectors"]) == 12
    assert manifest["vectors"][0]["name"] == "current.header.frame_submit_ack_required_keyframe"
    assert manifest["vectors"][-1]["name"] == "current.typed_payload.frame_region"
    assert manifest["vectors"][-1]["hex"] == "746f6b6175766964656f657674"


@pytest.mark.parametrize(
    ("document", "match"),
    [
        ("[]", "must be a JSON object"),
        ({"protocol_version": "nnrp-1-preview3", "vectors": {}}, "must contain a vectors list"),
        ({"protocol_version": "nnrp-1-preview3", "vectors": [1]}, "entries must be JSON objects"),
        (
            {"protocol_version": "nnrp-1-preview3", "vectors": [{"recipe_type": "unknown", "name": "bad"}]},
            "unsupported semantic vector recipe type",
        ),
        (
            {
                "protocol_version": "nnrp-1-preview3",
                "vectors": [
                    {
                        "recipe_type": "header",
                        "name": "bad.header",
                        "description": 123,
                        "version_major": 1,
                        "wire_format": 0,
                        "message_type": "frame_submit",
                        "flags": [],
                        "meta_len": 48,
                        "body_len": 0,
                        "session_id": 0,
                        "frame_id": 0,
                        "view_id": 0,
                        "route_id": 0,
                        "trace_id": 0,
                    }
                ],
            },
            "description must be a string",
        ),
    ],
)
def test_build_conformance_vector_manifest_rejects_invalid_manifest_shapes(tmp_path, document, match: str) -> None:
    recipe_manifest = _write_recipe_manifest(tmp_path, document)

    with pytest.raises(ValueError, match=match):
        build_conformance_vector_manifest("nnrp-1-preview3", recipe_manifest_path=recipe_manifest)


def test_build_conformance_vector_manifest_requires_existing_recipe_manifest_path(tmp_path) -> None:
    with pytest.raises(ValueError, match="recipe manifest path is required"):
        build_conformance_vector_manifest("nnrp-1-preview3")

    with pytest.raises(ValueError, match="recipe manifest path does not exist"):
        build_conformance_vector_manifest("nnrp-1-preview3", recipe_manifest_path=tmp_path / "missing.json")


@pytest.mark.parametrize(
    ("vector", "match"),
    [
        (
            {
                "recipe_type": "frame_submit_metadata",
                "name": "bad.frame_submit",
                "src_width": 1,
                "src_height": 1,
                "tile_width": 1,
                "tile_height": 1,
                "tile_count": 1,
                "section_count": 1,
                "frame_class": 1,
                "input_profile": "dense_luma_frame",
                "tile_index_mode": "dense_range",
                "reserved0": 0,
                "latency_budget_ms": 1,
                "target_fps_x100": 1,
                "retry_of_frame": 0,
                "tile_base_id": 0,
                "camera_bytes": 0,
                "tile_index_bytes": 0,
                "submit_mode": "mixed",
                "budget_policy": ["allow_partial"],
                "loss_tolerance_policy": "bad_policy",
                "object_ref_mask": 0,
                "dependency_frame_id": 0,
                "payload_kind_bitmap": ["tensor"],
                "payload_frame_count": 0,
            },
            "unsupported loss tolerance policy",
        ),
        (
            {
                "recipe_type": "typed_payload_frame_region",
                "name": "bad.frames",
                "frames": [{"payload_kind": "bad_kind", "profile_id": 1, "payload_utf8": "x"}],
            },
            "unsupported typed payload frame recipe kind",
        ),
        (
            {
                "recipe_type": "typed_payload_frame_region",
                "name": "bad.payload_utf8",
                "frames": [{"payload_kind": "token_chunk", "profile_id": 1, "payload_utf8": 3}],
            },
            "payload_utf8 must be a string",
        ),
        (
            {
                "recipe_type": "typed_payload_frame_region",
                "name": "bad.payload_hex",
                "frames": [{"payload_kind": "token_chunk", "profile_id": 1, "payload_hex": 3}],
            },
            "payload_hex must be a string",
        ),
        (
            {
                "recipe_type": "typed_payload_frame_region",
                "name": "missing.payload",
                "frames": [{"payload_kind": "token_chunk", "profile_id": 1}],
            },
            "must define payload_utf8 or payload_hex",
        ),
        (
            {
                "recipe_type": "result_hint_packet",
                "name": "bad.result_hint",
                "version_major": 1,
                "wire_format": 0,
                "flags": [],
                "session_id": 21,
                "frame_id": 303,
                "route_id": 7,
                "trace_id": 14,
                "applied_budget_policy": "stale_reuse",
                "congestion_state": "saturated",
                "reason": "bad_reason",
                "retry_after_ms": 60,
            },
            "unsupported ResultHintReason name",
        ),
        (
            {
                "recipe_type": "object_reference_block",
                "name": "bad.object_kind",
                "object_kind": "bad_kind",
                "ref_flags": 0,
                "cache_namespace": 7,
                "cache_key_hi": 1,
                "cache_key_lo": 2,
            },
            "unsupported CacheObjectKind name",
        ),
    ],
)
def test_build_conformance_vector_manifest_rejects_invalid_recipe_values(tmp_path, vector, match: str) -> None:
    recipe_manifest = _write_recipe_manifest(
        tmp_path,
        {"protocol_version": "nnrp-1-preview3", "vectors": [vector]},
    )

    with pytest.raises(ValueError, match=match):
        build_conformance_vector_manifest("nnrp-1-preview3", recipe_manifest_path=recipe_manifest)


def test_build_conformance_vector_manifest_supports_hex_and_extended_typed_payload_families(tmp_path) -> None:
    recipe_manifest = _write_recipe_manifest(
        tmp_path,
        {
            "protocol_version": "nnrp-1-preview3",
            "vectors": [
                {
                    "recipe_type": "typed_payload_frame_region",
                    "name": "extended.typed_payload.frame_region",
                    "frames": [
                        {"payload_kind": "tool_delta", "profile_id": 8, "payload_utf8": "tool"},
                        {"payload_kind": "opaque_bytes", "profile_id": 9, "payload_hex": "00ff10"},
                    ],
                }
            ],
        },
    )

    manifest = build_conformance_vector_manifest("nnrp-1-preview3", recipe_manifest_path=recipe_manifest)

    assert manifest["vectors"][0]["hex"] == "746f6f6c00ff10"
    assert manifest["vectors"][0]["bytes"] == 7


def test_build_conformance_vector_manifest_rejects_unknown_protocol() -> None:
    with pytest.raises(ValueError, match="unsupported protocol version"):
        build_conformance_vector_manifest("nnrp-1-preview4")
