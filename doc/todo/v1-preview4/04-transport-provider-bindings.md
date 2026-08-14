# 04 - Transport Provider Bindings

## Provider Registry

- [x] Expose Python transport provider discovery over Rust artifacts.
- [x] Report available transport providers.
  - [x] TCP.
  - [x] QUIC.
  - [x] IPC.
  - [x] WebSocket.
- [x] Validate and expose the complete frozen provider metadata object.
  - [x] Stable provider id.
  - [x] Typed cost model id and units.
  - [x] Preference rank.
  - [x] Maximum frame bytes.
  - [x] Registered platform limitations.
- [x] Reject aggregate and legacy provider manifest shapes.
- [x] Reject provider names that are not advertised by the native artifact, keep provider-owned names distinct from protocol transport ids, and reject duplicate transport/provider ids.
- [x] Keep application-facing `nnrp://` / `nnrps://` endpoints separate from provider-local locators.

## TCP And QUIC Preview4 Runtime Continuity

- [x] Keep TCP provider behavior aligned with preview4 runtime objects.
- [x] Keep QUIC provider behavior aligned with preview4 runtime objects.
- [x] Route TCP and QUIC progress/backpressure events through the shared event pump.
- [x] Add tests that TCP and QUIC providers remain distinct native artifact slots.

## IPC Binding

- [x] Add Python endpoint model for `unix://`.
- [x] Add Python endpoint model for `npipe://`.
- [x] Bind native IPC provider connect.
- [x] Bind native IPC provider listen.
- [x] Exchange ordered batches of complete NNRP packets through the IPC artifact FFI.
- [x] Release Rust-owned receive and listener-endpoint buffers exactly once.
- [x] Add loopback smoke tests against preview4 Rust IPC artifacts.
- [x] Add diagnostic skip behavior when the native artifact does not expose IPC.

## WebSocket Binding

- [x] Add Python endpoint model for `ws://`.
- [x] Add Python endpoint model for `wss://`.
- [x] Bind native WebSocket provider connect.
- [x] Bind native WebSocket provider listen.
- [x] Exchange ordered batches of complete NNRP packets through the WebSocket artifact FFI.
- [x] Keep blocking carrier operations off the Python event-loop thread.
- [x] Add binary-frame-only validation.
- [x] Add loopback smoke tests against preview4 Rust WebSocket artifacts.
- [x] Add diagnostic skip behavior when the native artifact does not expose WebSocket.

## Probe Policy

- [x] Select the installed transport directly for single-provider installations.
- [x] Probe installed transports by policy for multi-provider installations.
- [x] Preserve explicit user transport selection.
- [x] Accept selection evidence only through the frozen `NativeTransportSelectionOptions`; require exact readiness and observations keyed by transport id and stable provider id, keep package/provider names distinct from canonical transport identity, and reject duplicate, unmatched, or incomplete evidence before selection.
- [x] Summarize raw probe samples into succeeded/failed observations with per-sample throughput and exact odd/even medians.
- [x] Apply the frozen success, throughput, RTT, cost, preference, transport-id, and provider-id comparator.
- [x] Surface typed probe metrics and complete ordered candidate diagnostics.
- [x] Carry complete candidate diagnostics on typed invalid-evidence, forced-unavailable, and no-viable selection errors.
- [x] Remove the Python-specific weighted score API.

## Public Binding Surface

- [x] Expose one `NativeTransportBinding` per transport-scoped Rust artifact.
- [x] Expose typed connection and listener wrappers instead of raw native handles.
- [x] Expose typed client and server security configuration objects.
- [x] Make connection and listener close idempotent.
- [x] Reject provider-local endpoint locators that do not match the owning artifact.

## Role Runtime Carrier Ownership

- [x] Connect production client roles through the selected provider artifact.
  - [x] Open the provider-local carrier endpoint.
  - [x] Transfer carrier ownership to the role runtime in the same library.
  - [x] Perform the real session handshake before exposing a usable connection.
  - [x] Keep raw transfer handles private and invalidate packet wrappers after success.
- [x] Bind production server roles through the selected provider artifact.
  - [x] Transfer listener ownership to the role runtime in the same library.
  - [x] Accept real carrier connections and complete server handshake in Rust.
  - [x] Surface submit/control/object/cache events from Rust-owned reads.
  - [x] Send partial/terminal/drop/trace output over the accepted carrier.
- [x] Remove logical-only and synthetic production paths.
  - [x] Do not complete submit/result locally when no peer is connected.
  - [x] Keep packet-level `connect`/`listen` limited to diagnostics, conformance, and custom carriers.
  - [x] Reject artifacts that expose standalone transport calls without role adoption symbols.
- [x] Add carrier-backed E2E tests for TCP, QUIC, IPC, and WebSocket.
  - [x] Cover client/server handshake and submit/result exchange.
  - [x] Cover runtime control and object/cache events.
  - [x] Cover route-local TCP TLS through packaged artifacts and the independent wire target.
  - [x] Cover ownership transfer failure and idempotent role close.

## Host Route Cardinality

- [x] Replace singular production role routing with the frozen route maps.
  - [x] Add `NativeClientProviderRoute` and a canonical transport-keyed client mapping.
  - [x] Add `NativeServerProviderRoute` and a canonical transport-keyed server mapping.
  - [x] Keep the exact owned client/server security fields on each route.
  - [x] Remove role-level `provider_endpoint`, `security`, and explicit single-transport options.
  - [x] Keep singular endpoint/security only on low-level one-provider packet APIs.
  - [x] Report configured known-but-uninstalled routes as `local-unavailable`.
  - [x] Apply the exact rejection precedence when multiple checks fail.
- [x] Complete native client route orchestration.
  - [x] Resolve every installed route independently.
  - [x] Probe every eligible Auto/Prefer route.
  - [x] Preserve unresolved and security-incompatible candidates in diagnostics.
  - [x] Transfer only the selected carrier to the Rust role runtime.
  - [x] Fail Force without fallback.
- [x] Complete native server listener-set orchestration.
  - [x] Resolve every policy-allowed installed route.
  - [x] Bind every eligible Auto/Prefer route into one logical server.
  - [x] Accept sessions from every bound listener.
  - [x] Expose the actual listener transport as `active_transport_name` on every accepted session.
  - [x] Expose actual bound provider endpoints, including assigned ports.
  - [x] Roll back all listeners when any required bind or adoption fails.
  - [x] Fail and close the complete logical server after a terminal provider-listener failure.
  - [x] Close every listener and accepted session exactly once.
- [x] Enforce route-local application security intent.
  - [x] Add TCP TLS client and server routes.
  - [x] Keep QUIC and native WSS credentials isolated by route.
  - [x] Reject IPC, plain TCP, and WS for `nnrps://` with `security-unsatisfied`.
  - [x] Add `route-unresolved` and `security-unsatisfied` to Python diagnostics.
