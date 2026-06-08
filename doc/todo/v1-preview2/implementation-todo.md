# NNRP/1-preview2 Implementation Todo

## 0. Scope

1. This file tracks the Python SDK work required to converge historical preview2 planning onto the current `NNRP/1.0` contract.
2. The active Python target is the current `NNRP/1.0` wire only; do not reintroduce superseded preview-iteration assumptions.
3. The protocol design document in `nnrp-doc` remains the source of truth for wire semantics.
4. preview2 helper/runtime work must converge on the canonical async session shape now; do not defer submit/result pump semantics to preview3 because the existing preview2 wire already supports them.

## 1. Current Baseline

- [x] Python control-plane metadata, data-plane metadata, replay helpers, and preview transport adapters are implemented for the active preview2 wire.
- [x] Cross-language smoke and replay/export helpers already exist for preview2.
- [x] `NNRP/1-preview2` control-plane baseline, core preview2 metadata, replay/export tensor path, and runtime preview `stale / partial / degraded` result push semantics are implemented.
- [x] `NNRP/1-preview2` object-reference submit and typed payload frame codecs are implemented on the Python wire surface.
- [x] Current high-level helpers still expose a convenient `submit_and_receive_result` path; preview2 still needs a clearly documented canonical helper path based on independent submit/result pumps and multi-frame in-flight behavior.
- [x] Python-side preview2 body/object-reference work is closed for this pass; the remaining follow-up is cross-SDK rollout sequencing in the C# SDK.

## 1.1 Legacy Baseline Cleanup

- [x] Rebase preview2 wire-difference documentation on top of the current preview2 common core instead of earlier render-first draft tables.
- [x] Revisit preview2 `CLIENT_HELLO / SERVER_HELLO_ACK` additions after the common control metadata is separated from render-specific capability knobs.
	- [x] Keep preview2 handshake extensions aligned with the current 64B `ClientHelloMetadata` and 80B `ServerHelloAckMetadata` tables instead of legacy tile/view/shape fields.
	- [x] Reconfirm which preview2 capabilities remain fixed metadata additions and which must stay in control extensions after profile-local topology moves out of common metadata.
- [x] Revisit preview2 `SESSION_PATCH` inheritance after the common patch flow becomes 36B/44B metadata plus tensor profile patch blocks.
- [x] Revisit preview2 `FrameSubmitMetadata / ResultPushMetadata` assumptions so non-tensor payloads do not inherit tile-based coverage semantics from the legacy draft baseline.
- [x] Revisit preview2 cache/object-kind tasks now that camera/tile topology is explicitly tensor-profile-local rather than part of the common protocol baseline.
	- [x] Keep tensor-profile-local cache capability advertisement on `TENSOR_PROFILE_CACHE_OBJECT_BITMAP` / `build_cache_object_bitmap(...)` rather than legacy hard-coded masks.
	- [x] Keep preview2 object-reference slots scoped to tensor-profile-local camera / tile-index / tensor-section-table objects instead of promoting them back into common protocol metadata.
- [x] Keep object-reference, typed-payload, and migration helper work aligned with the current preview2 core/profile split rather than encoding more dependencies on superseded submit/result layouts.
	- [x] Keep preview2 submit/result helpers on `FrameSubmitMetadata` / `ResultPushMetadata` plus preview2 body regions instead of legacy tensor submit/result block parsing.
	- [x] Keep the canonical preview2 helper model on independent submit/result pumps and migration control paths rather than synchronous legacy submit/result coupling.
- [x] Freeze preview2 typed-payload semantic contract in the protocol doc before either SDK starts inventing descriptor/body interpretations.
	- [x] Freeze `payload_frame_count` as logical typed payload frame count rather than tensor section count.
	- [x] Freeze descriptor `offset / length` as byte ranges relative to the typed payload frame region.
	- [x] Freeze the rule that non-tensor payloads must not masquerade as tensor sections or tensor coverage metadata.
	- [x] Freeze the final fixed-width preview2 `BodyRegionPrelude` / `InlineObjectBlockHeader` / `ObjectReferenceBlock` / `TypedPayloadDescriptor` / `ExtensionFrameDescriptor` byte layout before resuming SDK body-codec implementation.
	- [x] Freeze the scope boundary so the first preview2 pass supports only low-frequency object references plus inline typed payloads, and does not allow either SDK to invent a private typed-payload data-reference wire format.

## 2. Preview2 Wire Freeze

### 2.1 Versioning

- [x] Freeze the current version contract on `version_major=1` and `wire_format=0` with no implicit fallback.
- [x] Freeze ALPN handling on QUIC `nnrp/1` and TCP `nnrp/1-tcp`.
- [x] Add tests that reject packets carrying unsupported wire-format bytes.

### 2.2 Common Header

- [x] Reconfirm preview2 continues to use the 40-byte common header.
- [x] Add strict tests that preview2 only changes `version_stage` and message/metadata interpretation, not header length.

### 2.3 Message Types

- [x] Add `FLOW_UPDATE` message type.
- [x] Add `RESULT_HINT` message type.
- [x] Add `TRANSPORT_PROBE` and `TRANSPORT_PROBE_ACK` message types for Transport Probing Phase.
- [x] Add `SESSION_MIGRATE` and `SESSION_MIGRATE_ACK` message types for in-session transport fallback.
- [x] Keep existing `CACHE_*`, `FRAME_SUBMIT`, `RESULT_PUSH`, `RESULT_DROP`, and `SESSION_PATCH` message values stable where preview2 reuses them.

## 3. Control Plane

### 3.1 Handshake Metadata

- [x] Freeze which preview2 handshake/runtime capabilities stay in fixed metadata, which use `CLIENT_HELLO / SERVER_HELLO_ACK` control extensions, and which are standalone control messages.
	- [x] Keep object-reference capability / accepted reference kinds in fixed metadata via `cache_object_bitmap`.
	- [x] Keep downgrade-policy baseline in fixed metadata via `degrade_policy` rather than inventing extra handshake extensions.
	- [x] Keep runtime flow-control bounds in fixed metadata via `max_concurrent_frames`; dynamic credit and backpressure updates ride `FLOW_UPDATE`, not additional handshake extensions.
	- [x] Keep typed-payload negotiation in `0x0105 / 0x0106` while preserving `supported_payload_kind_bitmap / accepted_payload_kind_bitmap` in fixed metadata.
- [x] Add transport policy declaration to `CLIENT_HELLO` and `SERVER_HELLO_ACK`.
	- [x] Define `transport_policy` enum: `auto / prefer_quic / prefer_tcp / force_quic / force_tcp`.
	- [x] Add optional `preferred_transport_id` to `CLIENT_HELLO` extension.
	- [x] Add `active_transport_id` and accepted/downgraded transport policy echo to `SERVER_HELLO_ACK` extension.
- [x] Add session-level loss tolerance declaration to `CLIENT_HELLO` and `SERVER_HELLO_ACK`.
	- [x] Define `loss_tolerance` enum: `strict / best_effort / low_latency / fire_and_forget`.
	- [x] Freeze control extension type ids in protocol doc: `0x0103` for `CLIENT_HELLO`, `0x0104` for `SERVER_HELLO_ACK`.
	- [x] Add `session_loss_tolerance: u8` field to `CLIENT_HELLO` loss-tolerance control extension (`0x0103`).
	- [x] Add `accepted_loss_tolerance: u8` field to `SERVER_HELLO_ACK` loss-tolerance control extension (`0x0104`).
- [x] Add typed-payload negotiation to `CLIENT_HELLO` and `SERVER_HELLO_ACK`.
	- [x] Freeze control extension type ids in protocol doc: `0x0105` for `CLIENT_HELLO`, `0x0106` for `SERVER_HELLO_ACK`.
	- [x] Freeze `payload_kind_bitmap:u32` bit assignments in protocol doc for `tensor / token_chunk / audio_chunk / video_chunk / structured_event / tool_delta / opaque_bytes`.
	- [x] Add `payload_capabilities` / `payload_capabilities_ack` control extensions.
	- [x] Add reserved-zero `critical_extension_frame_bitmap:u32` negotiation.
- [x] Add round-trip pack/unpack tests and exported public models.

### 3.2 Flow Control Messages

- [x] Define fixed metadata for `FLOW_UPDATE`.
- [x] Encode dynamic in-flight credit and backpressure hints.
- [x] Add parser tests for invalid credit windows and unknown flags.

### 3.3 Result Hint Messages

- [x] Define fixed metadata for `RESULT_HINT`.
- [x] Encode service-side congestion state, degrade recommendation, and retry window.
- [x] Add stable reason/status enums for hint parsing.

### 3.4 Cache Object Semantics

- [x] Extend `CACHE_PUT`/`CACHE_ACK`/`CACHE_INVALIDATE` helpers to carry preview2 object kinds.
- [x] Freeze `object_kind:u16` and `invalidate_scope:u8` numeric values in protocol doc.
- [x] Model cache object kinds for camera block, tile index block, section table, codec table, and reusable result object.
- [x] Add strict tests for unknown object kinds and malformed invalidate scopes.

## 4. Data Plane

### 4.1 Preview2 Frame Submit

- [x] Define preview2 `FrameSubmitMetadata`.
- [x] Freeze `submit_mode:u8` and `budget_policy:u8` encoding rules in protocol doc.
- [x] Add `submit_mode` support for `inline / reference / mixed`.
- [x] Add `object_ref_mask` support.
- [x] Add `budget_policy` support.
- [x] Add `dependency_frame_id` support.
- [x] Add `payload_kind_bitmap` and `payload_frame_count` support.
- [x] Add validation that mixed submit mode cannot reference missing required blocks.

### 4.2 Preview2 Result Push

- [x] Define preview2 `ResultPushMetadata`.
- [x] Freeze `result_class:u8` numeric values in protocol doc.
- [x] Add `result_class` support for `complete / partial / stale_reuse`.
- [x] Add `result_class` support for `degraded`.
- [x] Add `applied_budget_policy` support for tensor `complete / partial / stale_reuse / degraded` result packets.
- [x] Add `reused_frame_id` support for single-source stale reuse results.
- [x] Add `covered_tile_count` and `dropped_tile_count` support for tensor results.
- [x] Add `payload_kind_bitmap` and `payload_frame_count` support.
- [x] Add profile-specific coverage helpers so token/audio/video/event payloads do not have to pretend to be tile-based results.
- [x] Add validation that tile coverage fields match body/tile-index information.

### 4.3 Body Block Models

- [x] Define Python-side models for `BodyRegionPrelude`, `InlineObjectBlockHeader`, and `ObjectReferenceBlock`.
- [x] Define `TypedPayloadDescriptor` and `ExtensionFrameDescriptor` models.
- [x] Add `BodyRegionPrelude` validation so each region has explicit lengths and deterministic offsets.
- [x] Add parsing/building helpers for camera reference blocks.
- [x] Add parsing/building helpers for tile index reference blocks.
- [x] Add parsing/building helpers for tensor section reference blocks.
- [x] Add parsing/building helpers for `token_chunk`, `audio_chunk`, `video_chunk`, `structured_event`, and `tool_delta` payload frames.
- [x] Add fast-skip support for unknown non-critical extension frames.
- [x] Keep body block ordering explicit and deterministic.

### 4.4 Mixed Submit / Partial Result Builders

- [x] Add convenience builders that emit mixed preview2 submit packets.
- [x] Add convenience builders that emit partial/stale/degraded preview2 result packets.
- [x] Add convenience builders that emit token-stream, audio-chunk, video-chunk, and structured-event preview2 packets.
- [x] Add mixed typed-payload builders so one submit/result can carry tensor plus non-tensor payload frames when required.
- [x] Add strict validation around illegal combinations such as `partial + full coverage`, `reference + missing object kind`, or `descriptor bytes != payload_frame_count * 16`.

## 5. Replay And Golden Vectors

- [x] Extend replay export so preview2 packets can encode tensor partial-result cases.
- [x] Extend replay export so preview2 packets can encode object-reference cases.
- [x] Add preview2 golden wire vectors for `FLOW_UPDATE`, `RESULT_HINT`, `FrameSubmitMetadata`, and `ResultPushMetadata`.
- [x] Add preview2 golden wire vectors for `BodyRegionPrelude`, `ObjectReferenceBlock`, `TypedPayloadDescriptor`, token-stream chunks, audio/video chunks, and structured event frames.
- [x] Add cross-language golden-vector import/export hooks for `nnrp-cs` alignment.

## 6. Transport Helpers

- [x] Extend the QUIC transport adapter to carry the current ALPN and wire-format selection.
	- [x] Freeze the QUIC ALPN value at `nnrp/1` and the TCP ALPN value at `nnrp/1-tcp`.
	- [x] Thread `wire_format` through client/server configuration builders.
	- [x] Cover current ALPN/wire-format selection through higher-level helper entry points and loopback validation.
- [x] Add transport-facing helpers for runtime flow-update messages.
	- [x] Keep preview2 `FLOW_UPDATE` metadata and packet builder on the core wire surface.
	- [x] Add bootstrapped transport/session helpers to send and receive `FLOW_UPDATE` on the control path.
	- [x] Add loopback coverage for runtime-facing `FLOW_UPDATE` exchange on helper-managed connections.
- [x] Add a client-facing preview2 bootstrap/session helper stack.
	- [x] Resolve selected/forced binding policy into `CLIENT_HELLO` packet initiation.
	- [x] Consume probe selection and emit a ready-to-send `CLIENT_HELLO`.
	- [x] Connect on the selected binding and complete `CLIENT_HELLO / SERVER_HELLO_ACK`.
	- [x] On QUIC, send the first `FRAME_SUBMIT` and receive `RESULT_PUSH` on the bootstrapped session.
	- [x] On QUIC, allow the client-facing session helper to reuse auto-probe selection before the first `FRAME_SUBMIT`.
	- [x] Add a canonical preview2 helper surface that exposes `send_submit_packet` and result consumption as separate long-lived operations rather than making `submit_and_receive_result` the default usage pattern.
	- [x] Add helper-managed background result consumption and result correlation utilities for multi-frame in-flight sessions.
	- [x] Keep `submit_and_receive_result` only as a convenience helper for smoke/tests/synchronous hosts, not as the normative preview2 session model.
- [x] Add a client-facing auto-probe bootstrap helper that can probe QUIC/TCP and immediately connect the selected control path.
	- [x] Expose a client-facing probe-only helper that returns the raw transport probe selection without immediately bootstrapping a session.
	- [x] Expose one helper entry point that runs transport probing and returns both the probe selection and a bootstrapped control session.
	- [x] Keep the selected binding visible to callers so policy/debug output can explain why QUIC or TCP won.
- [x] Add preview2 TCP data-plane mapping for submit/result traffic.
	- [x] Define how `FRAME_SUBMIT` and `RESULT_PUSH` share or multiplex over the TCP transport instead of relying on QUIC stream primitives.
	- [x] Lift the preview2 client session helper to permit TCP once the TCP submit/result path exists.
- [x] Add at least one loopback test with mixed submit and partial result on one connection.
	- [x] Reuse the QUIC preview session helper for multiple submit/result exchanges on one live connection.
	- [x] Cover at least one non-terminal partial `RESULT_PUSH` before the terminal/final result for the same frame.

### 6.1 Transport Probing Phase

- [x] Add a minimal TCP control-channel adapter skeleton so probe packets have a non-QUIC binding target.
- [x] Implement `TRANSPORT_PROBE` packet builder (16-byte metadata + padding body).
- [x] Implement `TRANSPORT_PROBE_ACK` packet parser with server-side receive timestamp.
- [x] Add a smoke-level client-side probe orchestration helper: send probes concurrently on QUIC and TCP bindings, select the path with higher effective throughput.
	- [x] Upgrade transport probing from single-sample scoring to multi-sample scoring so transient network jitter does not dominate path selection.
	- [x] Aggregate probe samples with a robust score such as median RTT/effective throughput rather than picking the best single sample.
	- [x] Penalize timeout/failure samples so a flaky binding does not win on one lucky response.
- [x] Add a client-facing local dial policy helper so callers can skip probing and directly use a specified binding when they want to force `QUIC/TCP`.
- [x] Freeze `transport_id:u32` values in protocol doc: `0=unspecified`, `1=quic`, `2=tcp`.
- [x] Thread typed `TransportId` through smoke `CLIENT_HELLO / SERVER_HELLO_ACK` helpers so callers do not pass transport magic numbers.
- [x] Mirror the selected or forced binding into client-facing `CLIENT_HELLO.transport_policy` / `preferred_transport_id` resolver.
- [x] Add loopback probe tests for both bindings.

### 6.2 Session Migration

- [x] Keep preview2 `SESSION_MIGRATE` / `SESSION_MIGRATE_ACK` metadata pack-unpack and packet builders on the core wire surface.
- [x] Add transport/session helper methods to establish a second path and exchange `SESSION_MIGRATE / SESSION_MIGRATE_ACK`.
- [x] Add client-side migration trigger: continuous path health monitoring (RTT, jitter, throughput); trigger migration when degradation threshold is crossed.
- [x] Bind `resume_from_frame_id` from `SESSION_MIGRATE_ACK` into migration replay/skip behavior.
- [x] Enforce `frame_id` monotonicity across migration; do not replay frames older than `resume_from_frame_id`.
- [x] Add loopback migration tests and verify that frame continuity is preserved after switching bindings.

## 7. Validation

- [x] Add malformed packet tests for preview2 metadata length mismatches.
- [x] Add malformed packet tests for missing cache object references.
- [x] Add regression tests for partial/stale result classification.
- [x] Add regression tests for degraded result classification.
- [x] Keep `python -m pytest -q` green after each preview2 slice.
- [x] Keep the external host transport smoke green after helper/control-adjacent preview2 slices.
	- Revalidated `tests/unit/test_preview_bridge.py`, `tests/unit/test_preview_server.py`, and `tests/unit/test_transport_smoke.py` against `PYTHONPATH=src;../nnrp-py/src`; all 36 tests passed after aligning partial-result bridge expectations.
- [x] Add external-host TCP transport smoke coverage once the preview2 TCP data-plane path exists.
	- `tests/unit/test_transport_smoke.py` already exercises the external host `nnrp+tcp` binding path and remains green in the same targeted trio.

## 8. Documentation

- [x] Document preview2 wire differences on the active wire surface.
- [x] Document object-reference workflow and cache lifecycle examples.
- [x] Document partial/stale/degraded result semantics for host repositories.
- [x] Document NNRP as a lightweight real-time AI application protocol rather than a neural-rendering-only transport.
- [x] Document the canonical preview2 helper model as `submit pump + result pump + control path`, and clarify that `submit_and_receive_result` is only a convenience wrapper.
- [x] Document typed-payload / extension-frame usage for token streaming, multimodal dialogue, and coding-agent events.
