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

## Benchmarks

- [x] Extend native-runtime benchmark plan for preview4.
- [ ] Add control-frame submit/cancel benchmark.
- [ ] Add progress/partial-result polling benchmark.
- [x] Add runtime object declare/ref/release benchmark.
- [ ] Add IPC loopback benchmark.
- [ ] Add WebSocket loopback benchmark.
- [x] Compare cffi API and ctypes on the same plan.
- [ ] Compare preview4 native hot paths against preview3 baselines.

## Documentation

- [ ] Update quick-start for preview4 client controls.
- [ ] Update server docs for progress, partial result, and drop reason.
- [x] Update native runtime docs for transport providers.
- [ ] Update benchmark docs with preview4 result tables.
- [ ] Update conformance docs with wire target manifest generation.
- [x] Document cache reference as explicit workload behavior, not a universal latency promise.

## Release Checks

- [x] Run unit tests.
- [x] Run adapter conformance.
- [x] Run wire conformance dry-run.
- [x] Run native wheel inspection.
- [x] Run benchmark smoke thresholds.
- [x] Publish only after platform wheel checks pass.
