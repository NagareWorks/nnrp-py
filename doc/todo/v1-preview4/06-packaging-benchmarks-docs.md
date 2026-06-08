# 06 - Packaging, Benchmarks, And Docs

## Wheel Packaging

- [ ] Package preview4 Rust artifacts into platform wheels.
- [ ] Include transport-scoped native artifacts.
  - [ ] TCP.
  - [ ] QUIC.
  - [ ] IPC.
  - [ ] WebSocket.
- [ ] Include cffi API fast path where supported.
- [ ] Keep ctypes fallback for environments without the cffi API fast path.
- [ ] Reject universal wheels for native preview4 releases.
- [ ] Verify wheel contents per platform.

## Benchmarks

- [ ] Extend native-runtime benchmark plan for preview4.
- [ ] Add control-frame submit/cancel benchmark.
- [ ] Add progress/partial-result polling benchmark.
- [ ] Add runtime object declare/ref/release benchmark.
- [ ] Add IPC loopback benchmark.
- [ ] Add WebSocket loopback benchmark.
- [ ] Compare cffi API and ctypes on the same plan.
- [ ] Compare preview4 native hot paths against preview3 baselines.

## Documentation

- [ ] Update quick-start for preview4 client controls.
- [ ] Update server docs for progress, partial result, and drop reason.
- [ ] Update native runtime docs for transport providers.
- [ ] Update benchmark docs with preview4 result tables.
- [ ] Update conformance docs with wire target manifest generation.
- [ ] Document cache reference as explicit workload behavior, not a universal latency promise.

## Release Checks

- [ ] Run unit tests.
- [ ] Run adapter conformance.
- [ ] Run wire conformance dry-run.
- [ ] Run native wheel inspection.
- [ ] Run benchmark smoke thresholds.
- [ ] Publish only after platform wheel checks pass.
