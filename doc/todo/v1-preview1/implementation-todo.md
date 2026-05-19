# NNRP/1-preview1 Implementation Todo

## 0. Scope

1. This file tracks the full Python-side implementation work for `NNRP/1-preview1`.
2. Host/backend/frontend integration steps are intentionally tracked outside this repository and only referenced here when they are needed to wire up end-to-end validation.
3. The goal here is to keep protocol/library evolution independent from the runtime repository cadence.

## 1. Current Baseline

- [x] Legacy preview1 protocol freeze has been reopened; preview1 is being realigned around a common core plus tensor-profile-specific structures.
- [x] Python SDK scaffold has been split into `core / client / server / adapters / tools`.
- [x] Fixed header codec has been implemented.
- [x] Python control-plane fixed metadata now covers the redesigned preview1 common-core `CLIENT_HELLO / SERVER_HELLO_ACK / SESSION_PATCH / SESSION_PATCH_ACK` models, plus `ERROR / CACHE_*`.
- [x] Python data-plane fixed metadata now covers the redesigned preview1 common-core `FRAME_SUBMIT / RESULT_PUSH` models plus tensor-profile `tensor_submit_block / tensor_result_block / TensorSectionDesc`.
- [x] Legacy `FRAME_SUBMIT=52B` and `RESULT_PUSH=44B` tables have been replaced by the redesigned common-core/profile split.
- [x] Minimal `NnrpPacket` and tensor body helpers are in place.
- [x] The remaining post-redesign preview1 follow-ups now focus on a business-facing Python API layer instead of reopening preview2 body/object-reference work.

## 1.1 Preview1 Unfreeze Alignment

- [x] Rewrite preview1 control-plane metadata around generic session capability negotiation instead of render-first tile/view/quality assumptions.
	- [x] Replace legacy `ClientHelloMetadata` common fields `supported_tile_index_bitmap`, `max_views`, `min_width/min_height/max_width/max_height`, `target_fps_x100`, and `quality_tier` with the new common capability table: `supported_profile_bitmap`, `supported_payload_kind_bitmap`, `cache_digest_bitmap`, `cache_object_bitmap`, `max_lane_count`, `target_cadence_x100`, `degrade_policy`, `auth_bytes`, and `control_extension_bytes`.
	- [x] Replace legacy `ServerHelloAckMetadata` common fields `tile_layout_id`, `max_views`, `min_width/min_height/max_width/max_height`, `target_fps_x100`, `quality_tier`, and `max_sections` with the new negotiated common table: `accepted_profile_bitmap`, `accepted_payload_kind_bitmap`, `max_lane_count`, `target_cadence_x100`, `degrade_policy`, `max_body_bytes`, `control_extension_bytes`, and `server_flags`.
- [x] Rework preview1 `SESSION_PATCH` / `SESSION_PATCH_ACK` from render-first fixed tables into the new 36B/44B common metadata plus `tensor_profile_patch_block` / `tensor_profile_patch_ack_block`.
	- [x] Replace common patch fields `target_fps_x100` and `active_view_mask` with `target_cadence_x100` and `active_lane_mask`.
	- [x] Move tensor shape clamp out of common fixed metadata and into `tensor_profile_patch_block`.
	- [x] Add body-level parsing/building for `profile_patch_bytes` and `profile_patch_ack_bytes`.
- [x] Split preview1 data-plane semantics into a common frame/result core plus tensor-profile-specific blocks or extensions.
	- [x] Replace legacy `FrameSubmitMetadata` with the new 32B common metadata and add `tensor_submit_block` parsing/building for `camera_bytes`, `tile_index_bytes`, `tile_count`, and `section_count`.
	- [x] Replace legacy `ResultPushMetadata` with the new 32B common metadata and add `tensor_result_block` parsing/building for coverage/topology fields.
- [x] Rework preview1 cache/control helpers so camera/tile topology caches are treated as tensor-profile objects instead of common protocol baseline objects.
- [x] Re-evaluate the legacy `FRAME_SUBMIT=52B` and `RESULT_PUSH=44B` tables against the redesigned core/profile split before landing more preview1 wire work.
- [x] Decide which existing render-oriented fields remain in the preview1 tensor profile and which should move to preview2 object-reference or typed-payload paths.
- [x] Sync the redesigned preview1 baseline into `nnrp-cs`, preview2 todos, and runtime protocol adapters before continuing new implementation slices.

## 1.2 Preview1 Business-Facing API Layer

- [x] Add explicit preview1 client session entry points alongside the existing preview2 helpers so callers do not bootstrap preview1 by assembling raw `ClientHello` packets.
	- [x] Add `connect_preview1_client_control` / `connect_preview1_client_session` helpers mirroring the preview2 flow with preview1 defaults.
	- [x] Export the preview1 helpers from `nnrp.client` as the primary preview1 entry point.
- [x] Add typed preview1 submit/result helper methods on `ClientPreviewSession` so the primary flow stops passing raw `NnrpPacket` objects.
	- [x] Add a logical preview1 submit request model that wraps camera bytes, tile ids, tensor sections, cadence/budget hints, and trace/session routing.
	- [x] Add a typed preview1 result view/model so callers receive parsed result metadata/body instead of unpacking `RESULT_PUSH` packets manually.
	- [x] Keep raw `send_submit_packet` / `receive_result_packet` only as advanced escape hatches once the typed helper path exists.
- [x] Cover the new preview1 business API with client transport tests and smoke helpers that validate submit/result loops without packet assembly in test bodies.

## 2. Wire Models

### 2.1 Control Plane

- [x] Freeze preview1 control-plane extension contract.
	- [x] Reserve a TLV-based `control_extension_block` for low-frequency control messages.
	- [x] Require unknown optional extensions to be ignored and unknown critical extensions to fail explicitly.
	- [x] Keep `FRAME_SUBMIT / RESULT_PUSH` closed to generic custom headers in preview1.
- [x] Add concrete `control_extension_block` TLV helpers.
	- [x] Encode and decode TLV entries with 8-byte alignment and zero padding.
	- [x] Add validation for malformed TLV length, truncation, and non-zero padding.
	- [x] Add behavior tests for unknown optional entries vs unknown critical entries.
- [x] Replace legacy fixed metadata for `CLIENT_HELLO`.
	- [x] Freeze the new 64-byte common field table with explicit `auth_bytes` and `control_extension_bytes` lengths.
	- [x] Move profile-local topology capability declarations out of common metadata and into control extensions.
	- [x] Add round-trip tests and exported public models for the redesigned `ClientHelloMetadata`.
- [x] Replace legacy fixed metadata for `SERVER_HELLO_ACK`.
	- [x] Freeze the new 80-byte common field table with `accepted_profile_bitmap`, `accepted_payload_kind_bitmap`, `control_extension_bytes`, and `server_flags`.
	- [x] Keep tensor profile topology limits out of common metadata; carry them in profile-local control extensions instead.
	- [x] Add round-trip tests and exported public models for the redesigned `ServerHelloAckMetadata`.
- [x] Replace legacy fixed metadata for `SESSION_PATCH` and `SESSION_PATCH_ACK`.
	- [x] Freeze the new 36-byte `SessionPatchMetadata` table and 44-byte `SessionPatchAckMetadata` table.
	- [x] Add tensor profile patch-block codecs and round-trip coverage.
	- [x] Export the redesigned patch models through `nnrp.core.messages` and `nnrp.core`.
- [x] Add fixed metadata for `SESSION_PATCH`.
	- [x] Freeze a concrete binary field table for target FPS, quality tier, resolution clamp, active view mask, and preferred codec/compression strategy.
	- [x] Add round-trip pack/unpack tests.
	- [x] Export the model through `nnrp.core.messages` and `nnrp.core`.
- [x] Add fixed metadata for `SESSION_PATCH_ACK`.
	- [x] Freeze ack status / applied mask / reject reason fields.
	- [x] Add round-trip pack/unpack tests.
	- [x] Export the model through the public API surface.
- [x] Decide whether `CLOSE` needs fixed metadata in preview1 or remains header+body only.
	- [x] `CLOSE` 当前保持 header+body only，不引入 preview1 固定 metadata。
- [x] Add first-class typed packet helpers for header-only preview1 control packets.
	- [x] Add `FRAME_CANCEL` packet helpers.
	- [x] Add `PING / PONG` packet helpers.
	- [x] Add round-trip tests that keep `meta_len=0` and `body_len=0` for these packet types.

### 2.2 Data Plane

- [x] Add first-class helpers for tile index block encode/decode.
	- [x] `dense_range`
	- [x] `raw_u16`
	- [x] `delta_u16`
	- [x] `bitset`
- [x] Rework preview1 packet/body builders around `profile_block + payload_descriptor + payload_data` layout.
	- [x] Keep the common `FRAME_SUBMIT` / `RESULT_PUSH` metadata profile-agnostic.
	- [x] Make tensor profile builders emit and parse `tensor_submit_block` / `tensor_result_block` explicitly.
	- [x] Reserve room for future non-tensor profile descriptors without reopening preview1 common metadata.
	- [x] Keep cache-sensitive `camera_block` / `tile_index_block` details isolated to tensor profile body blocks so they no longer leak back into common metadata assumptions.
- [x] Add convenience builders for `FRAME_SUBMIT` packet assembly.
	- [x] Compose header + metadata + tile index block + tensor sections.
	- [x] Validate section count / tile count consistency.
- [x] Add convenience builders for `RESULT_PUSH` packet assembly.
	- [x] Compose result metadata + tile index block + tensor sections.
	- [x] Validate section count / tile count consistency.
	- [x] Validate result flags.
- [x] Tighten packet/body validation.
	- [x] Reject section/metadata tile count mismatches.
	- [x] Reject inconsistent codec table declarations.
	- [x] Reject impossible fixed-stride declarations.
	- [x] Reject malformed section ordering.
	- [x] Reject mismatched tile length tables.
- [x] Add a typed `RESULT_DROP` wire model.
	- [x] Freeze the preview1 metadata/body contract for superseded, expired, or discardable results.
	- [x] Preview1 `RESULT_DROP` currently stays header-only with `meta_len=0` and `body_len=0`.
	- [x] Add round-trip packet tests and public API exports.

## 3. Replay And Golden Vectors

- [x] Add replay helpers under `nnrp.tools`.
	- [x] Export preview submit frames from existing `FrameFeatures` captures.
	- [x] Export preview result frames from existing `EnhanceResult` captures.
- [x] Add golden wire vectors.
	- [x] Header vectors.
	- [x] Control message vectors.
	- [x] Data message vectors.
	- [x] Packet/body vectors with multiple sections.
- [x] Add wire-size comparison utilities.
	- [x] Compare protobuf payload size vs preview wire size.
	- [x] Emit stable textual summaries for regression checks.

## 4. Transport Layer

- [x] Freeze the Python QUIC library choice.
	- [x] Use `aioquic` as the Python QUIC v1 base library for preview1.
- [x] Add adapter interfaces for stream/datagram transport.
- [x] Validate preview1 packet-to-carrier mapping in the QUIC adapter.
	- [x] Keep `CLIENT_HELLO / SERVER_HELLO_ACK / SESSION_PATCH / SESSION_PATCH_ACK / CLOSE / ERROR` on the control stream.
	- [x] Keep `FRAME_SUBMIT` on client-opened unidirectional submit streams.
	- [x] Keep `RESULT_PUSH / RESULT_DROP` on server-opened unidirectional result streams.
	- [x] Exercise `FRAME_CANCEL / PING / PONG` as typed small-packet control traffic instead of raw smoke payloads.
- [x] Implement a loopback transport PoC in `nnrp.adapters`.
	- [x] Minimal client hello / ack exchange.
	- [x] Minimal frame submit / result push exchange.
	- [x] Error-path validation.
	- [x] ALPN mismatch surfaces an explicit connection failure.
	- [x] Multiple control packets can share one bidirectional stream.
	- [x] Smoke client can run with explicit trusted-CA verification.
	- [x] Minimal datagram send/receive path.
	- [x] Minimal QUIC smoke client/server harness exists for cross-SDK bring-up.
- [x] Make preview session lifetime explicit in the Python QUIC helpers.
	- [x] Expose client/server `idle_timeout` knobs instead of relying on library defaults.
	- [x] Add a smoke path that can connect, optionally ping, and hold the session open before the first submit.
	- [x] Keep transport helpers usable by runtime hosts without forcing application-specific warm-up policy into the SDK.
- [x] Decide which transport-facing helpers stay runtime-agnostic in `nnrp-py` and which stay in host applications.

## 5. Validation

- [x] Expand unit coverage around malformed packets.
- [x] Add integration-style loopback tests for packet exchange.
- [x] Cover preview1 control-stream body payloads used by current integrations.
	- [x] `CLIENT_HELLO` smoke helpers and adapter tests preserve non-empty auth/request bodies and keep `auth_bytes` aligned.
	- [x] `SERVER_HELLO_ACK` smoke helpers and adapter tests preserve non-empty body payloads on the control stream.
- [x] Keep `python -m pytest -q` green after each slice.
- [x] Add at least one replay-driven regression test once replay tooling exists.

## 6. Documentation

- [x] Document the public Python wire API.
- [x] Document packet/body assembly examples.
- [x] Document replay/export workflow for host repositories.