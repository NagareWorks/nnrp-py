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

- [ ] Add test harness entrypoint for suite-as-client scenarios.
- [ ] Add test harness entrypoint for suite-as-server scenarios.
- [ ] Add test harness entrypoint for proxy scenarios where available.
- [ ] Write wire result reports with observed frames.
- [ ] Write evidence files for frame logs and timing.

## Result Validation

- [ ] Run `nnrp-conformance-runner wire-plan` in CI.
- [ ] Run `nnrp-conformance-runner validate-wire-results` in CI when live endpoint tests are enabled.
- [ ] Keep adapter conformance and wire conformance as separate jobs.
- [ ] Add skip diagnostics for unavailable native transports.
- [ ] Ensure skipped transport cases do not masquerade as passed cases.

## Regression Coverage

- [ ] Add Python tests for target manifest generation.
- [ ] Add Python tests for wire result report generation.
- [ ] Add negative tests for unsupported transport declarations.
- [ ] Add negative tests for missing result frames.
