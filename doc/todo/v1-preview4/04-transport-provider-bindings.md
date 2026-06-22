# 04 - Transport Provider Bindings

## Provider Registry

- [x] Expose Python transport provider discovery over Rust artifacts.
- [x] Report available transport providers.
  - [x] TCP.
  - [x] QUIC.
  - [x] IPC.
  - [x] WebSocket.
- [x] Report provider cost and preference metadata.
- [x] Report provider platform limitations.
- [x] Reject provider names that are not advertised by the native artifact.
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
- [ ] Add loopback smoke tests against preview4 Rust IPC artifacts.
- [x] Add diagnostic skip behavior when the native artifact does not expose IPC.

## WebSocket Binding

- [x] Add Python endpoint model for `ws://`.
- [x] Add Python endpoint model for `wss://`.
- [x] Bind native WebSocket provider connect.
- [x] Bind native WebSocket provider listen.
- [x] Add binary-frame-only validation.
- [ ] Add loopback smoke tests against preview4 Rust WebSocket artifacts.
- [x] Add diagnostic skip behavior when the native artifact does not expose WebSocket.

## Probe Policy

- [x] Select the installed transport directly for single-provider installations.
- [x] Probe installed transports by policy for multi-provider installations.
- [x] Preserve explicit user transport selection.
- [x] Surface probe results in diagnostics.
