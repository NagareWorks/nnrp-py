# Rust Native Artifacts Migration Plan

## Goal

Move the Python SDK preview3 runtime path onto the canonical `nnrp-rs` native implementation so Python no longer owns hot-path wire packing, transport framing, session state, or QUIC behavior.

The Python package should keep a Pythonic host API, but delegate protocol-critical work to versioned native artifacts produced by `nnrp-rs`.

## Non-Goals

1. Do not redesign preview3 protocol semantics in this repository.
2. Do not make QUIC a mandatory Python dependency. QUIC remains a native transport slot that is present only when the selected native artifact includes it.
3. Do not remove pure-Python inspection helpers that are useful for fixtures, docs, or non-hot-path validation.

## Current Baseline

The existing Python SDK owns helper-level packet construction, transport-oriented utilities, and preview1/preview2 compatibility behavior. Preview3 should replace the runtime path in place with Rust-backed handles while preserving the public package shape where practical.

## Native Artifact Strategy

1. Pin an `nnrp-rs` commit, tag, or published artifact version in the Python release notes before packaging.
2. Load native artifacts by platform tag and architecture at import or first-use time.
3. Probe the loaded artifact for ABI version, protocol version, enabled transport slots, and feature flags before accepting it.
4. Route runtime operations through the native backend when the probe passes.
5. Keep a pure-Python fallback only for fixture inspection, diagnostics, and explicitly unsupported runtime combinations.

## Pinned Native Contract

The current preview3 binding work consumes `nnrp-rs` native artifact version `1.0.0-preview.3.4`.

This version is the native artifact contract pin for the current migration branch and includes:

1. The `nnrp_runtime_capabilities` export.
2. ABI version `1.2.0`.
3. Protocol version `1/0`.
4. Runtime feature flags for protocol core, client/server APIs, event polling, callback dispatch, cache/schema, recovery, typed payloads, and transport slots.
5. Transport slot bits for TCP and optional QUIC.

If a later `nnrp-rs` release changes exported symbol names, ABI struct layout, required feature flags, or transport-slot meanings, update this pin and rerun the pre/post migration benchmark table before accepting the new artifact.

## Target Platform Matrix

| OS | Architectures | Packaging target | Required before GA |
| --- | --- | --- | --- |
| Windows | x86, x86_64, arm64 | Dynamic `nnrp_ffi.dll` from `nnrp-rs` release assets | Yes |
| macOS | x86_64, arm64 | Dynamic `libnnrp_ffi.dylib` from `nnrp-rs` release assets | Yes |
| Linux | x86, x86_64, armv7, arm64 | Dynamic `libnnrp_ffi.so` from `nnrp-rs` release assets | Yes |
| Android | x86, x86_64, armv7, arm64 | Dynamic `libnnrp_ffi.so` for downstream app bundle use | Preview gate |
| iOS | x86_64 simulator, arm64 simulator/device | Static `libnnrp_ffi.a` for downstream app/toolchain linking | Preview gate |

## Benchmark Protocol

Run the baseline benchmark before migration and record it here. After the native backend lands, run the same benchmark suite on the same machine class and add the post-migration numbers.

Rules:

1. Record commit SHA, Python version, OS, architecture, CPU model, and native artifact version.
2. Use the same iteration counts and payload shapes before and after migration.
3. Report p50, p95, and p99 latency where the operation is request-like.
4. Report throughput, CPU, and peak memory where the operation is stream-like.
5. Keep QUIC benchmark rows separate from TCP and in-memory rows because QUIC is a slot, not a default dependency.

### Environment

| Run | Date | SDK commit | nnrp-rs artifact | Python | OS/arch | CPU | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Pre-migration baseline | 2026-05-25 | b83dadb | N/A | 3.13.5 | windows/amd64 | Intel(R) Core(TM)2 Duo CPU T7700 @ 2.40GHz | Conformance benchmark runner selected and measured 9 scenarios. |
| Post-migration native | 2026-05-28 | 6c9a067 | 1.0.0-preview.3.3 | 3.13.5 | windows/amd64 | Intel64 Family 6 Model 15 Stepping 11, GenuineIntel | Local `nnrp-rs` release artifact installed with `scripts/prepare_native_artifacts.py`; conformance benchmark plan selected and measured 9 scenarios. |

### Latency Benchmarks

| Benchmark | Payload | Iterations | Pre p50 | Pre p95 | Pre p99 | Post p50 | Post p95 | Post p99 | Delta | Notes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Header encode/decode | L0 header | 100000 | 3.7 us | 7.3 us | 15.5 us | 3.7 us | 7.2 us | 17.0 us | p50 +0.0% | Measured by `l4.header.encode_decode.latency`. |
| Metadata encode/decode | session open/open ack | 100000 | 7.6 us | 15.6 us | 71.1 us | 7.4 us | 14.6 us | 40.7 us | p50 -2.6% | Measured by `l4.metadata.session_open_ack.latency`. |
| Metadata encode/decode | frame submit/result push | 100000 | 5.9 us | 12.4 us | 53.9 us | 6.0 us | 11.9 us | 26.7 us | p50 +1.7% | Measured by `l4.metadata.submit_result.latency`. |
| Typed payload pack/unpack | tensor descriptor plus payload | 100000 | 36.4 us | 83.4 us | 328.9 us | 36.5 us | 77.0 us | 221.1 us | p50 +0.3% | Measured by `l4.typed_payload.tensor_pack_unpack.latency`. |
| Runtime probe | version plus capability query | 100000 | 1.0 us | 2.0 us | 2.8 us | 1.0 us | 1.9 us | 2.1 us | p50 +0.0% | Measured by `l4.runtime.probe.latency`. |
| Session lifecycle | open plus close loop | 100000 | 9.6 us | 18.9 us | 43.9 us | 9.8 us | 18.8 us | 55.2 us | p50 +2.1% | Measured by `l4.session.lifecycle.latency`. |

### Throughput Benchmarks

| Benchmark | Payload | Duration | Pre throughput | Pre CPU | Pre peak memory | Post throughput | Post CPU | Post peak memory | Delta | Notes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Submit/result loop | inline tensor payload | 10 s | 95365.1 ops/s | TBD | TBD | 100471.2 ops/s | TBD | TBD | +5.4% | Measured by `l4.submit_result.inline_tensor.throughput` with raw throughput mode. |
| TCP loopback | request/result stream | 10 s | 83973.0 ops/s | TBD | TBD | 91017.4 ops/s | TBD | TBD | +8.4% | Measured by `l4.transport.tcp.loopback.throughput` against the SDK local transport-probe loopback path with raw throughput mode. |
| QUIC loopback | request/result stream | 10 s | 91296.2 ops/s | TBD | TBD | 89373.5 ops/s | TBD | TBD | -2.1% | Optional slot; measured by `l4.transport.quic.loopback.throughput` against the SDK local transport-probe loopback path with raw throughput mode. |

### Interpretation

Latency rows are effectively flat on p50 and improve on most tail measurements, so the native-backed binding did not introduce visible per-operation latency regression on this host.

Raw throughput improves on submit/result and TCP loopback while QUIC loopback is within a small negative band on this host. CPU and peak-memory tracing remain available through the benchmark runner's profiled throughput mode, but those numbers are intentionally kept out of the raw pre/post comparison because tracing adds measurable per-iteration overhead.

## Migration Phases

1. Capture pre-migration benchmarks and commit the results to `doc/benchmarks/rs-native-artifacts-migration.md`.
2. Add native artifact discovery, loader validation, and ABI/protocol probes.
3. Add Python handle wrappers for connection, session, operation, schema, and buffer views.
4. Move preview3 hot-path encode/decode and submit/result flow behind the native backend.
5. Keep Python APIs stable and isolate backend selection behind one internal module.
6. Add post-migration benchmarks and record the deltas in `doc/benchmarks/rs-native-artifacts-migration.md`.
7. Enable conformance and packaging CI for the supported platform matrix.

## Open Decisions

1. Whether Python should ship one wheel per platform or a thin wheel plus externally resolved native artifacts.
2. Whether the first binding layer should use `ctypes` directly or a generated binding layer over the stable C ABI.
3. Which native capability probe names are considered stable enough for Python-side feature gating.
