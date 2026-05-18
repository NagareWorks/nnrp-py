# nnrp-py

Python SDK scaffold for NNRP.

This repository keeps a neutral protocol-level name because it is intended to host shared wire-format code plus server- and client-facing helpers. The current implementation priority is server-side integration for `neural-render-runtime`, but the package layout is explicitly split so future Python clients, script hosts, or tooling can reuse the same SDK.

NNRP should be read as a lightweight real-time AI application protocol, not as a neural-rendering-only transport. The current runtime integration happens to start from tensor/tile-oriented super-resolution flows, but the current NNRP/1 wire already covers token streaming, multimodal payload delivery, structured events, tool deltas, transport probing, and migration-oriented session control.

## Contributors

<a href="https://github.com/NagareWorks/nnrp-py/graphs/contributors" title="Open the contributors graph for individual GitHub profiles and IDs.">
	<img src="https://contrib.rocks/image?repo=NagareWorks/nnrp-py" alt="Contributors" />
</a>

The avatar wall above updates automatically from the repository contributor list once this repository is published at the matching GitHub location.

GitHub README rendering does not support per-avatar dynamic tooltips for an auto-generated contributor wall, so use the linked contributors graph if you want individual profile pages and account IDs.

## Scope

This repository contains protocol-focused code only:

1. Common wire constants, enums, and header codecs.
2. Shared client/server protocol-side models.
3. Future transport adapters, replay helpers, and golden-vector tooling.

It does not contain neural rendering runtime business logic.

## Layout

- `src/nnrp/core/`: shared protocol primitives and wire helpers.
- `src/nnrp/client/`: client-facing helpers and types.
- `src/nnrp/server/`: server-facing helpers and types.
- `src/nnrp/adapters/`: transport or host integration adapters.
- `src/nnrp/tools/`: replay, diagnostics, and golden-vector helpers.
- `tests/`: protocol-level tests and golden-vector checks.

The top-level `nnrp` package keeps top-level re-exports for common imports, while new code should prefer the explicit submodules.

## Public Wire API

The current public wire surface is centered on two modules:

1. `nnrp.core`: fixed-width header/message codecs, packet builders, tensor section helpers, and packet/body parsing.
2. `nnrp.tools`: replay/export helpers, smoke helpers, and wire-size summary/comparison utilities.

Use `nnrp.core` when you already have protocol-shaped inputs and want explicit control over header fields, tile ids, section payloads, and packet assembly.

```python
from nnrp.core import (
	HeaderFlags,
	InputProfile,
	TensorSectionData,
	TensorDType,
	TileIndexMode,
	build_frame_submit_packet,
	unpack_tensor_body,
)

packet = build_frame_submit_packet(
	session_id=7,
	frame_id=42,
	src_width=640,
	src_height=360,
	tile_width=32,
	tile_height=32,
	tile_ids=(5, 6),
	sections=(
		TensorSectionData(
			role_id=1,
			default_codec_id=0,
			dtype_id=TensorDType.FP16,
			tile_payloads=(b"aa", b""),
		),
	),
	camera_block=b"cam",
	input_profile=InputProfile.DENSE_LUMA_FRAME,
	tile_index_mode=TileIndexMode.DENSE_RANGE,
	flags=HeaderFlags.ACK_REQUIRED,
)

encoded = packet.pack()
decoded_body = unpack_tensor_body(
	packet.body[3:],
	tile_index_bytes=0,
	section_count=1,
	tile_count=2,
)
```

The builder/parser layer currently guarantees:

1. Header length and packet length consistency.
2. Tile count / section count consistency.
3. Strictly increasing `role_id` ordering across tensor sections.
4. Fixed-stride, codec-table, and tile-length-table self-consistency checks.
5. `RESULT_PUSH` tensor coverage and result-flag consistency validation.

## Replay And Export Workflow

Use `nnrp.tools.replay` when the source object still looks like host-side runtime data instead of protocol-native inputs.

```python
from nnrp.tools import (
	compare_frame_features_wire_size,
	frame_features_to_wire_bytes,
	frame_features_to_wire_summary,
	render_preview_wire_summary,
	render_wire_size_comparison,
)

preview_bytes = frame_features_to_wire_bytes(frame_features)
summary = frame_features_to_wire_summary(frame_features)
comparison = compare_frame_features_wire_size(
	frame_features,
	reference_payload=protobuf_bytes,
	reference_label="protobuf",
)

print(len(preview_bytes))
print(render_preview_wire_summary(summary))
print(render_wire_size_comparison(comparison))
```

The replay helpers currently provide:

1. `frame_features_to_packet` / `frame_features_to_wire_bytes` for preview submit export.
2. `enhance_result_to_packet` / `enhance_result_to_wire_bytes` for preview result export.
3. `frame_features_to_wire_summary` / `enhance_result_to_wire_summary` for stable packet summaries.
4. `compare_frame_features_wire_size` / `compare_enhance_result_wire_size` for preview-vs-reference payload size comparison without taking a protobuf dependency.

`reference_payload` is intentionally just raw bytes. The protocol library does not depend on protobuf schemas; host applications remain responsible for producing the reference payload they want to compare against preview wire bytes.

## Workflow Notes

1. Prefer `nnrp.core` when writing protocol-native tests or SDK integration code.
2. Prefer `nnrp.tools` when exporting host objects, building replay fixtures, or generating stable regression summaries.
3. For transport bring-up, use `nnrp.tools.smoke` and `nnrp-quic-smoke` rather than reimplementing ad hoc control packets.

## Current Session Model

The canonical helper shape is a long-lived control path plus independent submit and result pumps.

1. Use `connect_preview2_client_session` or `connect_preview2_client_session_with_probe` to bootstrap the control path and selected transport binding.
2. Use `send_preview2_submit` or `Preview2ResultRouter.send_submit` as the submit pump.
3. Use `receive_preview2_result` or `manage_preview2_results()` as the result pump.
4. Use the same live session for control-path operations such as `send_flow_update` and `migrate_preview2_session`.
5. Treat `submit_and_receive_result` as a smoke/test convenience wrapper for raw packets, not as the normative host integration pattern.

Hosts should keep submission and result consumption decoupled so multiple frames can remain in flight while `RESULT_PUSH`, `RESULT_DROP`, `FLOW_UPDATE`, and migration control messages continue to arrive on the same session.

```python
from nnrp.client import Preview2SubmitRequest, connect_preview2_client_session

async with connect_preview2_client_session(
	host,
	tcp_port=9444,
	requested_session_id=7,
) as session:
	async with session.manage_preview2_results() as router:
		await router.send_submit(Preview2SubmitRequest(frame_id=101))
		result = await router.receive(101, timeout=1.0)
		print(result.payload_kinds)
```

## Cross-SDK Golden Vectors

`nnrp.tools` also exposes a stable cross-language golden-vector manifest so other SDKs can import the same packet and metadata fixtures instead of copying Python test constants by hand.

```python
from nnrp.tools import (
	export_cross_language_golden_vectors,
	render_cross_language_golden_vectors_json,
)

vectors = export_cross_language_golden_vectors()
manifest_json = render_cross_language_golden_vectors_json(vectors)

print(vectors[0].name)
print(manifest_json)
```

The exported manifest currently covers the current `FLOW_UPDATE`, `RESULT_HINT`, metadata, body-region, object-reference, and typed-payload fixtures used for cross-SDK wire alignment.

## Current Wire Additions

The current NNRP/1 wire keeps the 40-byte common header stable and changes the protocol surface in four main ways.

1. `FRAME_SUBMIT` and `RESULT_PUSH` gain aligned fixed metadata so submit mode, budget policy, dependency tracking, payload-kind bitmaps, payload-frame counts, and result classes become explicit wire fields instead of host-side conventions.
2. The current body is no longer an implicit tensor-only blob. It starts with `BodyRegionPrelude` and then carries deterministic ordered regions for inline objects, object references, typed-payload descriptors, typed-payload frames, extension descriptors, and extension payloads.
3. Submit/result flows are no longer tensor-only. The current wire can carry `tensor`, `token_chunk`, `audio_chunk`, `video_chunk`, `structured_event`, `tool_delta`, and `opaque_bytes` payload kinds in one packet, while still preserving tensor-specific coverage rules only when tensor payloads are actually present.
4. The current wire adds runtime control messages and session mechanics for `FLOW_UPDATE`, `RESULT_HINT`, `TRANSPORT_PROBE`, `TRANSPORT_PROBE_ACK`, `SESSION_MIGRATE`, and `SESSION_MIGRATE_ACK`.

In practice, the current wire is the general-purpose session model for mixed object references, mixed payload kinds, explicit degradation semantics, and long-lived asynchronous multi-frame sessions.

## Object Reference Workflow

The current wire treats cache-backed object references as first-class protocol inputs rather than ad hoc host shortcuts.

The expected cache lifecycle is:

1. Advertise the supported cache object kinds during handshake through `cache_object_bitmap` and related fixed metadata.
2. Put stable objects into the session cache through `CACHE_PUT` / `CACHE_ACK` before the hot path starts referencing them.
3. Reference stable objects from `FRAME_SUBMIT` or `RESULT_PUSH` through object-reference regions instead of resending the same bytes inline every frame.
4. Invalidate session-, namespace-, object-kind-, or object-key-scoped entries through `CACHE_INVALIDATE` when the producer knows the references should no longer be reused.
5. Treat cache misses and unsupported object kinds as explicit protocol errors; do not silently fall back to a guessed inline path.

Typical submit-side mixed mode looks like this:

1. Keep rapidly changing tensor section data inline.
2. Move low-frequency camera blocks, tile-index templates, or tensor section tables into cache objects.
3. Set `submit_mode` to `reference` or `mixed` and align `object_ref_mask` with the standard reference slots present in the body.

This lets hosts reduce repeated hot-path bytes without hiding cache policy inside runtime-private handles.

## Current Result Semantics

Host repositories should treat current result classes as display policy signals, not just transport decoration.

1. `complete` means the result fully covers the requested tensor scope or fully satisfies the non-tensor payload set carried by the packet.
2. `partial` means the result is still displayable or consumable, but only covers part of the requested output. Tensor results must make that visible through `covered_tile_count` and `dropped_tile_count`.
3. `stale_reuse` means the result intentionally reuses older frame/object content. Hosts should surface the reuse relationship instead of treating it as a fresh complete inference.
4. `degraded` means the service intentionally lowered fidelity or fell back because of budget, congestion, or resource pressure. Hosts should not collapse this into transport failure.
5. `RESULT_DROP` remains the non-displayable terminal path. A degraded or stale result is still a positive result path and should usually stay on the render or consumer timeline.

For host integrations, the important rule is to preserve the distinction between “nothing usable arrived” and “a usable but lower-quality result arrived”. The current wire keeps backpressure, budget enforcement, stale reuse, and graceful degradation explicit instead of burying them in app-specific heuristics.

## Typed Payload And Extension Frames

Typed payloads let one packet carry non-tensor application content without pretending everything is a tensor section.

Current payload helpers in `nnrp.core` cover:

1. `build_token_chunk_frame` for token streaming and incremental text generation.
2. `build_audio_chunk_frame` and `build_video_chunk_frame` for multimodal streaming payloads.
3. `build_structured_event_frame` for structured dialogue or agent-side event records.
4. `build_tool_delta_frame` for tool-call progress and coding-agent style delta streams.
5. `build_preview2_frame_submit_typed_payload_packet`, `build_preview2_result_push_typed_payload_packet`, and mixed builders when tensor plus non-tensor payloads must travel together.

```python
from nnrp.core import (
	build_preview2_frame_submit_typed_payload_packet,
	build_structured_event_frame,
	build_token_chunk_frame,
)

packet = build_preview2_frame_submit_typed_payload_packet(
	session_id=7,
	frame_id=101,
	frames=(
		build_token_chunk_frame(b"tok", profile_id=1),
		build_structured_event_frame(b'{"phase":"thinking"}', profile_id=2),
	),
)
```

Extension frames remain the escape hatch for standardized or future protocol-side metadata that should not be forced into fixed metadata fields. Unknown non-critical extension frames must be skippable, while unknown critical extension frames must remain hard failures so SDKs do not silently misinterpret application semantics.

## Transport Helper Boundary

The current transport-facing boundary is intentionally narrow.

`nnrp-py` keeps the helpers that remain runtime-agnostic across different hosts and SDKs:

1. QUIC connection/listener primitives in `nnrp.adapters`.
2. TLS / ALPN configuration helpers such as `create_quic_client_configuration` and `create_quic_server_configuration`.
3. Cross-SDK bring-up helpers in `nnrp.tools.smoke`.
4. Protocol-native packet builders, parsers, replay/export helpers, and wire-size diagnostics.

Host applications keep everything that depends on runtime policy, business objects, or deployment wiring:

1. Session lifecycle policy above the protocol primitives.
2. Runtime-specific request/response models and object adaptation.
3. Port sharing, service bootstrap, and multi-protocol listener orchestration.
4. Production health checks, telemetry pipelines, and application-specific retry policy.

In practice this means `nnrp-py` owns reusable protocol machinery, while repositories such as `neural-render-runtime` own the code that binds those primitives to a concrete runtime service.

## Development

```powershell
python -m pip install -e .[dev]
python -m pytest
```
