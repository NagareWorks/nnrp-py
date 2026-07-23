# NNRP Python v1 Preview4 Release Notes

Preview4 moves the Python SDK onto the Rust preview4 native artifact line and exposes the runtime-control,
runtime-object, transport-provider, and wire-conformance surfaces needed by NNRP/1 preview4.

This document is the preview4 release note entry point. Historical preview3 performance notes remain in their own
benchmark documents and are not used as preview4 release criteria.

## Runtime Control

- Client helpers emit the frozen preview4 runtime-control metadata for cancellation, abort, scheduling, deadlines,
  expire-at, supersede, budgets, route hints, execution hints, capability negotiation, and profile degradation.
- Result polling suppresses locally canceled operation or frame results so late native results do not re-enter the
  Python client facade after a cancellation path.
- Event dispatch keeps progress, partial-result, result-drop, backpressure, and payload-family updates on coarse native
  polling calls.
- Named client and server methods perform one role-neutral `nnrp_runtime_frame_send` ABI call; raw control codes and
  frame encoding remain internal to the SDK.
- Runtime-frame events copy native-owned payloads before releasing their owner handle, then expose typed metadata,
  body, diagnostic, and delta fields.

## Runtime Objects And Cache References

- Runtime-object metadata helpers encode and decode object declarations, references, releases, patches, deltas, and
  partial-result object references.
- Cache reference, cache miss, and cache invalidation metadata stay typed protocol events rather than generic transport
  failures.
- Cache lease results preserve the canonical object version, lease identity, owner scope/id, grant timestamp, and TTL
  returned by Rust ABI 4; Python derives expiration with saturating `u64` arithmetic and validates object versions.
- Python snapshots object metadata and partial-result payloads unless a native lifetime guard explicitly owns the
  borrowed view.

## Transport Providers

- Rust preview4 artifacts are transport scoped and expose TCP, QUIC, IPC, and WebSocket capability slots.
- Python `1.0.0rc4.post8` consumes Rust `1.0.0-preview.4.15` and exposes the frozen provider cost, preference,
  frame-limit, limitation, probe-metrics, and ordered candidate models without a Python-specific weighted score.
- Multi-provider selection follows the cross-SDK deterministic comparator and reports every eligible or rejected
  candidate; probe samples bind to the stable provider id carried by the owning artifact.
- Python provider selection keeps endpoint parsing separate from user-facing `nnrp://` naming so host code can stay
  stable while the selected provider resolves `tcp://`, `quic://`, `unix://`, `npipe://`, or `ws://` runtime endpoints.
- Provider packages must own meaningful native transport behavior; they are not configuration-only switches.
- `NativeTransportBinding` now performs real probe, connect, listen, accept, complete-packet batch I/O, and close calls
  through each transport artifact. Release smoke tests exchange packets in both directions over IPC and WebSocket.
- Carrier ownership transfers to the Rust client or server role only after adoption succeeds. Failed adoption leaves the
  Python carrier wrapper responsible for deterministic cleanup.
- The host API uses ABI 4 role lifecycles for submit, receive, result delivery, cancellation, and polling. The retired
  compact-result and compiled CFFI side paths are not shipped as an alternate runtime.

## Conformance

- Wire-level target manifests identify `nnrp-1-preview4`, the implementation name, supported transports, and capability
  tokens.
- Wire case reports record observed frames and decoded preview4 runtime metadata so conformance can validate behavior
  without depending on the Python SDK's own adapter helpers.

## Release Gates

- Python `1.0.0rc4.post8` pins Rust `1.0.0-preview.4.15` and requires ABI `4.0.x`.
- Source distributions exclude release workspace artifacts and are validated for payload boundaries and size before
  publication.
- Preview4 native wheels must embed preview4-shaped Rust artifact metadata.
- Universal wheels are rejected for native preview4 releases.
- Native hot paths must remain coarse-grained and covered by benchmark smoke thresholds before release.
