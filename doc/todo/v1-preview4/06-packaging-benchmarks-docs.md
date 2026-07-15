# 06 - Packaging, Benchmarks, And Docs

## Wheel Packaging

- [x] Package preview4 Rust artifacts into platform wheels.
- [x] Include transport-scoped native artifacts.
  - [x] TCP.
  - [x] QUIC.
  - [x] IPC.
  - [x] WebSocket.
- [x] Include cffi API fast path where supported.
- [x] Keep ctypes diagnostic execution available for environments without the cffi API fast path.
- [x] Reject universal wheels for native preview4 releases.
- [x] Verify wheel contents per platform.
- [x] Require Rust ABI `1.12.1` in the release workflow.

## Benchmarks

- [x] Extend native-runtime benchmark plan for preview4.
- [x] Add control-frame submit/cancel benchmark.
- [x] Add progress/partial-result polling benchmark.
- [x] Add runtime object declare/ref/release benchmark.
- [x] Add IPC loopback benchmark.
- [x] Add WebSocket loopback benchmark.
- [x] Compare cffi API and ctypes on the same plan.
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
- [x] Run wire conformance dry-run.
- [x] Run native wheel inspection.
- [x] Run real bidirectional complete-packet IPC and WebSocket loopbacks against packaged artifacts.
- [x] Run benchmark smoke thresholds.
- [x] Publish only after platform wheel checks pass.
