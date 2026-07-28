# 06 - Packaging, Benchmarks, And Docs

## Coordinated Route-Contract Release Gate

- [x] Do not publish another Python Preview4 package until route-set client/server behavior and host-level conformance pass.
- [x] Pin reviewed Rust artifact `1.0.0-preview.4.19`, containing the complete route/security and shutdown ABI.
- [x] Inspect every wheel and verify transport-scoped artifacts remain correctly owned.
- [x] Compare Python public signatures with the frozen `nnrp-doc` route types before release.
- [x] Record the completed cross-SDK audit and conformance evidence in the release notes.

## Wheel Packaging

- [x] Package preview4 Rust artifacts into platform wheels.
- [x] Include transport-scoped native artifacts.
  - [x] TCP.
  - [x] QUIC.
  - [x] IPC.
  - [x] WebSocket.
- [x] Bind production ABI 4 role entrypoints directly through ctypes without a generated CFFI sidecar.
- [x] Keep client/server hot paths coarse at submit, event-batch, result, and runtime-frame boundaries.
- [x] Reject universal wheels for native preview4 releases.
- [x] Verify wheel contents per platform.
- [x] Require Rust ABI `4.1.1` in the release workflow.

## Benchmarks

- [x] Extend native-runtime benchmark plan for preview4.
- [x] Add control-frame submit/cancel benchmark.
- [x] Add progress/partial-result polling benchmark.
- [x] Add runtime object declare/ref/release benchmark.
- [x] Add IPC loopback benchmark.
- [x] Add WebSocket loopback benchmark.
- [x] Measure real IPC carrier adoption and bidirectional client/server role execution on the production ABI.
- [x] Count actual client and server FFI entrypoint calls per completed operation.
- [x] Compare preview4 native hot paths against preview3 baselines.

## Documentation

- [x] Update quick-start for preview4 client controls.
- [x] Update server docs for progress, partial result, and drop reason.
- [x] Update native runtime docs for transport providers.
- [x] Update benchmark docs with preview4 result tables.
- [x] Update conformance docs with wire target manifest generation.
- [x] Document cache reference as explicit workload behavior, not a universal latency promise.

## Release Checks

- [x] Run unit tests.
- [x] Run adapter conformance.
- [x] Run wire conformance dry-run and the independent-process plain/TLS carrier matrix.
- [x] Run native wheel inspection.
- [x] Run real bidirectional complete-packet IPC and WebSocket loopbacks against packaged artifacts.
- [x] Run benchmark smoke thresholds.
- [x] Publish only after platform wheel checks pass.
