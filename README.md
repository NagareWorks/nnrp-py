<p align="center">
  <img src="assets/nnrp-readme-banner.svg" alt="NNRP - Neural Network Runtime Protocol" width="100%" />
</p>

<p align="center">
  <a href="https://github.com/NagareWorks/nnrp-py/actions"><img alt="CI" src="https://img.shields.io/badge/CI-python-22c55e"></a>
  <a href="https://www.python.org"><img alt="Python" src="https://img.shields.io/badge/Python-3.x-3776ab?logo=python&logoColor=white"></a>
  <a href="https://nagareworks.github.io/nnrp-doc/"><img alt="Docs" src="https://img.shields.io/badge/docs-nnrp--doc-38bdf8"></a>
  <a href="https://github.com/NagareWorks/nnrp-py/blob/develop/LICENSE"><img alt="Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-64748b"></a>
</p>

# nnrp-py

Python SDK scaffold for NNRP.

This repository keeps a neutral protocol-level name because it is intended to host shared wire-format code plus server- and client-facing helpers. Host-application integration stays outside this repository so the package layout can serve Python clients, servers, script hosts, or tooling without binding the SDK to any single backend checkout.

NNRP should be read as a lightweight real-time AI application protocol, not as a neural-rendering-only transport. The current runtime integration happens to start from tensor/tile-oriented super-resolution flows, but the current NNRP/1 wire already covers token streaming, multimodal payload delivery, structured events, tool deltas, transport probing, and migration-oriented session control.

## Contributors

<a href="https://github.com/NagareWorks/nnrp-py/graphs/contributors" title="Open the contributors graph for individual GitHub profiles and IDs.">
	<img src="https://contrib.rocks/image?repo=NagareWorks/nnrp-py" alt="Contributors" />
</a>

The avatar wall above updates automatically from the repository contributor list once this repository is published at the matching GitHub location.

GitHub README rendering does not support per-avatar dynamic tooltips for an auto-generated contributor wall, so use the linked contributors graph if you want individual profile pages and account IDs.

## Scope

This repository contains protocol-focused code only:

1. Rust-backed client connection/session helpers for host integrations.
2. Common wire constants, enums, and packet codecs for protocol fixtures and diagnostics.
3. Shared client/server protocol-side models.
4. Transport adapters, replay helpers, and smoke tooling for SDK bring-up.

It does not contain neural rendering runtime business logic.

## Layout

- `src/nnrp/core/`: shared protocol primitives and wire helpers.
- `src/nnrp/cache.py`: Preview3 cache identity, lease, version, and invalidation result wrappers.
- `src/nnrp/native.py`: FFI loader, ABI/protocol probes, native handle wrappers, and runtime facade.
- `src/nnrp/native_artifacts/`: packaged `nnrp-rs` native libraries, arranged by platform tag.
- `src/nnrp/schema.py`: Preview3 schema/profile descriptor views and first-round standard registry constants.
- `src/nnrp/client/`: client-facing native connection/session helpers plus transport smoke helpers.
- `src/nnrp/server/`: server-facing helpers and types.
- `src/nnrp/adapters/`: transport or host integration adapters.
- `src/nnrp/tools/`: adapter conformance, benchmark, replay, diagnostics, and smoke helpers.
- `tests/`: protocol-level, native facade, conformance, and smoke tests.

The top-level `nnrp` package keeps top-level re-exports for common imports, while new code should prefer the explicit submodules.

## Native Host API

Host integrations should start with the Rust-backed client helpers in `nnrp.client`. The Python layer owns a small, Pythonic surface, while protocol-critical session, operation, polling, and status behavior is delegated to the packaged `nnrp-rs` native runtime.

```python
from nnrp.client import (
	NativeClientConnectionOptions,
	NativeClientSessionOpenOptions,
	connect_native_client_connection,
)

with connect_native_client_connection(
	options=NativeClientConnectionOptions(connection_id=7),
	require_native=True,
) as connection:
	session = connection.open_session(
		NativeClientSessionOpenOptions(
			requested_session_id=42,
			profile_id=1,
			schema_id=1,
			schema_version=1,
		)
	)
	result = connection.submit_and_poll_result(
		session,
		operation_id=1001,
		frame_id=1,
		payload=b"tensor-or-typed-payload-bytes",
		max_events=8,
	)
	print(result.state, result.payload)
```

The native helpers provide:

1. `connect_native_client_connection()` for one Rust-backed connection that can own multiple sessions.
2. `NativeClientConnection.open_session()` for explicit session creation.
3. `NativeClientConnection.submit_and_poll_result()` for a host-friendly submit/result roundtrip over native session operations.
4. `NativeRuntimeSession.submit_operation()` and `NativeClientConnection.operation_scope()` for operation handles, parent/group metadata, and cancellation on exceptional exits.
5. `NativeClientConnection.poll_result()`, native async polling helpers, and callback dispatch helpers for result/event delivery.
6. `NativeClientConnection.cancel_frame()` / `NativeClientConnection.cancel_operation()` / `NativeClientConnection.send_control()` for host control paths.

By default the native loader searches `nnrp/native_artifacts/<os>-<arch>/` inside the installed package. Set `NNRP_NATIVE_ARTIFACT_ROOT` when testing an external artifact tree. Pass `require_native=True` in host code that must fail fast instead of falling back to SDK-local fixtures.

The native binding layer has two paths. The default `NNRP_NATIVE_BINDING_MODE=auto` tries a packaged cffi API fast path for compact submit/result operations and falls back to the zero-compile `ctypes` ABI path when that module is unavailable or cannot preserve the requested payload semantics. Set `NNRP_NATIVE_BINDING_MODE=ctypes` for compiler-free diagnostics, or `NNRP_NATIVE_BINDING_MODE=cffi_api` when a benchmark or deployment should fail fast unless the cffi API module is present.

Polled native events and results expose Python-owned `bytes` payload snapshots. The current Python API does not expose borrowed result buffers, so a result object remains stable even if the native runtime reuses its poll buffer after the call returns.

Cache leases and schema validation follow the same host/runtime split. Python code passes stable identifiers, descriptors, and payload views into the native runtime; lease policy, schema matching, and diagnostics remain owned by Rust:

```python
from nnrp import (
	CacheObjectIdentity,
	cache_query,
	cache_touch,
	token_delta_payload_descriptor,
	token_delta_schema_descriptor,
)
from nnrp.client import NativeClientSessionOpenOptions, connect_native_client_connection

with connect_native_client_connection(require_native=True) as connection:
	session = connection.open_session(NativeClientSessionOpenOptions(requested_session_id=42))

	cache = session.cache_backend(now_ms=10_000, ttl_ms=30_000)
	identity = CacheObjectIdentity(namespace=1, object_kind=1, key_hi=0, key_lo=7)
	lease = cache_query(cache, identity)
	if lease.succeeded:
		cache_touch(cache, identity, ttl_ms=60_000)

	registry = connection.schema_registry()
	registry.install(token_delta_schema_descriptor())
	registry.validate_typed_payload_binding(
		token_delta_payload_descriptor(offset=0, length=128)
	)
```

`profile_id = 0` means unspecified. It must not be treated as an implicit tensor profile. Tensor and token payloads are peer standard profiles, while structured-event, tool-delta, and workflow-state remain payload families routed through schema/profile bindings before any profile-private body decoding happens.

`NativeRuntimeResult.state` reports the host-visible operation lifecycle as `completed`, `partial`, `degraded`, `stale_reuse`, `cancelled`, or `failed`. `NativeRuntimeResult.diagnostic` preserves native status, error family, protocol detail, and related connection/session/operation/frame ids; use `NativeStructuredDiagnostic.to_report()` when emitting adapter or CI diagnostics instead of flattening native failures into strings.

## Public Wire API

The public wire surface remains available for protocol fixtures, diagnostics, and tooling. It should not be treated as the primary host runtime path when native artifacts are available.
The legacy `connect_client_session()` and `connect_client_session_with_probe()` helpers remain available from `nnrp.client.transport` only for packet transport smoke tests and adapter bring-up. Production host integrations should use the Rust-backed native connection/session helpers from `nnrp.client`.

### Schema And Profile Constants

Preview3 schema/profile helpers expose stable descriptor views without decoding profile-private payload bodies:

```python
from nnrp import StandardProfile, StreamSemantics, token_delta_payload_descriptor

descriptor = token_delta_payload_descriptor(offset=0, length=128)
assert descriptor.profile_id is StandardProfile.TOKEN
assert descriptor.stream_semantics is StreamSemantics.APPEND
```

`StandardProfile.UNSPECIFIED` stays distinct from `StandardProfile.TENSOR`; structured-event and tool-delta remain payload families interpreted through schema/profile bindings rather than standalone standard profiles.

`CacheObjectIdentity`, `CacheLeaseDescriptor`, and `SchemaRegistryCatalog` are host-side value wrappers for native/runtime results and diagnostics. Cache query/touch/prefetch/release helpers delegate to a backend object and do not accept local lease policy callbacks or profile body decoders; those decisions remain owned by Rust and the conformance baseline.

Native connections also expose async iterators and callback dispatch helpers for `structured_event`, `tool_delta`, and workflow-state payload families. These helpers wrap result/control events from the native pump and preserve Python-owned payload snapshots; profile-private body decoding still belongs to schema/profile handlers rather than the iterator or callback itself.

The wire surface is centered on two modules:

1. `nnrp.core`: fixed-width header/message codecs, packet builders, tensor section helpers, and packet/body parsing.
2. `nnrp.tools`: replay helpers, smoke helpers, adapter conformance, benchmark, and wire-size summary/comparison utilities.

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

## Replay And Diagnostics Workflow

Use `nnrp.tools.replay` when the source object still looks like host-side runtime data and you need protocol-shaped fixture bytes, diagnostics, or wire-size comparisons.

```python
from nnrp.tools import (
	compare_frame_features_wire_size,
	frame_features_to_wire_bytes,
	frame_features_to_wire_summary,
	render_wire_summary,
	render_wire_size_comparison,
)

wire_bytes = frame_features_to_wire_bytes(frame_features)
summary = frame_features_to_wire_summary(frame_features)
comparison = compare_frame_features_wire_size(
	frame_features,
	reference_payload=protobuf_bytes,
	reference_label="protobuf",
)

print(len(wire_bytes))
print(render_wire_summary(summary))
print(render_wire_size_comparison(comparison))
```

The replay helpers currently provide:

1. `frame_features_to_packet` / `frame_features_to_wire_bytes` for submit fixture generation.
2. `enhance_result_to_packet` / `enhance_result_to_wire_bytes` for result fixture generation.
3. `frame_features_to_wire_summary` / `enhance_result_to_wire_summary` for stable packet summaries.
4. `compare_frame_features_wire_size` / `compare_enhance_result_wire_size` for wire-vs-reference payload size comparison without taking a protobuf dependency.

`reference_payload` is intentionally just raw bytes. The protocol library does not depend on protobuf schemas; host applications remain responsible for producing the reference payload they want to compare against NNRP wire bytes.

## Workflow Notes

1. Prefer `nnrp.client.connect_native_client_connection()` for host runtime integration.
2. Prefer `nnrp.core` when writing protocol-native tests or SDK integration code.
3. Prefer `nnrp.tools` when building replay fixtures or generating stable regression summaries.
4. For transport bring-up, use `nnrp.tools.smoke`, `nnrp-quic-smoke`, or the tooling-only packet session helpers rather than reimplementing ad hoc control packets.

## Current Session Model

The canonical host shape is a long-lived native connection with one or more explicit sessions. Hosts submit operations through a session and consume results through the native result/event pump.

```python
from nnrp.client import NativeClientSessionOpenOptions, connect_native_client_connection

with connect_native_client_connection(require_native=True) as connection:
	interactive = connection.open_session(
		NativeClientSessionOpenOptions(requested_session_id=10, profile_id=1)
	)
	batch = connection.open_session(
		NativeClientSessionOpenOptions(requested_session_id=11, profile_id=2)
	)

	interactive_op = interactive.submit_operation(
		operation_id=2001,
		frame_id=1,
		payload=b"interactive-frame",
	)
	batch_op = batch.submit_operation(
		operation_id=3001,
		frame_id=1,
		payload=b"batch-frame",
	)

	interactive_result = connection.poll_result(interactive, interactive_op, max_events=16)
	batch_result = connection.poll_result(batch, batch_op, max_events=16)
	print(interactive_result.state, batch_result.state)
```

Hosts should keep submission and result consumption decoupled so multiple operations can remain in flight while result, cancellation, control, and diagnostic events continue to arrive on the same connection. The connection context closes owned sessions on exit.

## Conformance

The shared `nnrp-conformance` suite owns protocol baselines, parameterized wire cases, adapter execution plans, and result validation. The Python SDK participates by declaring capabilities and running `python -m nnrp.tools.adapter_conformance --plan <path> --output <path>` against suite-selected cases.

SDK tests should exercise real Python APIs and native bridge behavior through adapter plans, benchmark plans, smoke tests, and focused unit tests rather than generating separate protocol vector manifests.

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
5. `build_frame_submit_typed_payload_packet`, `build_result_push_typed_payload_packet`, and mixed builders when tensor plus non-tensor payloads must travel together.

```python
from nnrp.core import (
	build_frame_submit_typed_payload_packet,
	build_structured_event_frame,
	build_token_chunk_frame,
)

packet = build_frame_submit_typed_payload_packet(
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

`nnrp-py` keeps the helpers that remain runtime-agnostic across different hosts and SDKs. These helpers are intentionally positioned as tooling, diagnostics, or cross-SDK bring-up surfaces, not as the default host runtime API:

1. QUIC connection/listener primitives in `nnrp.adapters`.
2. TLS / ALPN configuration helpers such as `create_quic_client_configuration` and `create_quic_server_configuration`.
3. Cross-SDK bring-up helpers in `nnrp.tools.smoke`.
4. Protocol-native packet builders, parsers, replay helpers, and wire-size diagnostics.

Host applications keep everything that depends on runtime policy, business objects, or deployment wiring:

1. Session lifecycle policy above the protocol primitives.
2. Runtime-specific request/response models and object adaptation.
3. Port sharing, service bootstrap, and multi-protocol listener orchestration.
4. Production health checks, telemetry pipelines, and application-specific retry policy.

In practice this means `nnrp-py` owns reusable protocol machinery, while host/application repositories own the code that binds those primitives to concrete service policy and deployment wiring.

## Development

```powershell
python -m pip install -e .[dev]
python -m pytest
```
