# NNRP Python v1 Preview4 Release Notes

Preview4 moves the Python SDK onto the Rust preview4 native artifact line and exposes the runtime-control,
runtime-object, transport-provider, and wire-conformance surfaces needed by NNRP/1 preview4.

This document is the preview4 release note entry point. Historical preview3 performance notes remain in their own
benchmark documents and are not used as preview4 release criteria.

## 1.0.0rc4.post12

- Pins Rust `1.0.0-preview.4.20` at commit `d55c972` and requires the exact FFI ABI `4.3.0`.
- Replaces role-specific public event unions with the frozen role-neutral runtime-event envelope while preserving the
  complete common header, closed typed metadata, and owned body, diagnostic, and delta tails.
- Requires typed submit requests at the public native boundary and keeps local lifecycle notifications separate from
  received wire events.
- Runs the independent-process Preview4 wire matrix against the exact Rust source commit used to build the four
  transport-scoped artifacts.

## 1.0.0rc4.post11

- Pins Rust `1.0.0-preview.4.19` at merge commit `e37779d` and requires the exact FFI ABI `4.1.1`.
- Requires the native runtime shutdown boundary and verifies shutdown, stale-handle invalidation, and runtime restart.
- Verifies the release ABI layout independently in Python and decodes multiple events from one native poll batch.
- Installs a built Linux wheel into an isolated environment and runs the real native client/server role E2E against the
  packaged transport libraries before publication.

## 1.0.0rc4.post10

- Pins Rust `1.0.0-preview.4.18` at merge commit `67b85b9` while retaining FFI ABI `4.1.x`.
- Rejects transport evidence whose provider identity does not match the selected binding or provider metadata.
- Runs the complete suite-owned adapter contract and independent-process preview4 host-route matrix against the exact
  Rust release source used to build packaged transport artifacts.

## 1.0.0rc4.post9

- Pins Rust `1.0.0-preview.4.17` and FFI ABI `4.1.x`.
- Makes explicit client and server `transports` registries authoritative while preserving exact provider identity,
  including known-but-uninstalled providers represented by non-callable bindings.
- Exposes selected transport evidence on client connections and rejects duplicate transport or provider identities.
- Runs the ten mandatory native host-route scenarios through an independent process that calls the public Python
  client/server APIs, including multi-route selection, multi-listener acceptance, route-local security, rollback,
  terminal listener closure, rejection precedence, and known-uninstalled provider behavior.

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
- Python `1.0.0rc4.post12` consumes Rust `1.0.0-preview.4.20` and exposes the frozen provider cost, preference,
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

- Python `1.0.0rc4.post12` pins Rust `1.0.0-preview.4.20` and requires the exact ABI `4.3.0`.
- The release candidate must pass the complete Python suite with at least 90% total and incremental line coverage, all
  selected adapter-conformance cases, all independent-process frame scenarios, and all native host-route scenarios
  without skips or synthesized success paths.
- All 16 platform wheels must be rebuilt from the 64 transport-scoped Rust release assets and verified to contain one
  platform plus independently owned TCP, QUIC, IPC, and WebSocket manifests and libraries at ABI `4.3.0`.
- Source distributions must exclude release workspace artifacts and pass payload-boundary and size validation before
  publication.
- Preview4 native wheels must embed preview4-shaped Rust artifact metadata.
- Universal wheels are rejected for native preview4 releases.
- Native hot paths must remain coarse-grained and covered by benchmark smoke thresholds before release.
