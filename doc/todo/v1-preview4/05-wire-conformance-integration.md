# 05 - Wire Conformance Integration

## Target Manifest

- [x] Add command to emit a Python wire target manifest.
- [x] Include implementation name and preview4 protocol version.
- [x] Include suite version.
- [x] Include supported modes.
  - [x] Suite as client.
  - [x] Suite as server.
  - [x] Suite as proxy where Python owns proxy harness.
- [x] Include supported transports.
  - [x] TCP.
  - [x] QUIC.
  - [x] IPC.
  - [x] WebSocket.
- [x] Include supported capabilities.
- [x] Include frame and in-flight limits.

## Live Endpoint Harness

- [x] Add test harness entrypoint for suite-as-client scenarios.
- [x] Add test harness entrypoint for suite-as-server scenarios.
- [x] Add test harness entrypoint for proxy scenarios where available.
- [x] Write wire result reports with observed frames.
- [x] Write evidence files for frame logs and timing.

## Result Validation

- [x] Run `nnrp-conformance-runner wire-plan` in CI.
- [x] Run `nnrp-conformance-runner validate-wire-results` in CI when live endpoint tests are enabled.
- [x] Keep adapter conformance and wire conformance as separate jobs.
- [x] Add skip diagnostics for unavailable native transports.
- [x] Ensure skipped transport cases do not masquerade as passed cases.

## Regression Coverage

- [x] Add Python tests for target manifest generation.
- [x] Add Python tests for wire result report generation.
- [x] Add negative tests for unsupported transport declarations.
- [x] Add negative tests for missing result frames.

## Host Route E2E

- [x] Run suite-owned multi-route client scenarios.
  - [x] Cover at least two resolved providers under Auto/Prefer.
  - [x] Cover forced unresolved and security-incompatible routes without fallback.
  - [x] Verify the Rust runtime adopts only the selected carrier.
- [x] Run suite-owned multi-listener server scenarios.
  - [x] Accept a real session through each of two simultaneously bound providers.
  - [x] Verify the actual bound endpoint of every listener.
  - [x] Verify active transport identity per accepted session.
  - [x] Verify atomic rollback after an injected bind failure.
  - [x] Verify a terminal listener failure closes the logical set instead of shrinking it.
- [x] Run the route-local security matrix for plain TCP, TCP TLS, QUIC, IPC, WS, and WSS.
- [x] Verify known-but-uninstalled routes and combined failures use exact rejection precedence.
- [x] Fail CI when any claimed host-route scenario is skipped or adapter-only.
