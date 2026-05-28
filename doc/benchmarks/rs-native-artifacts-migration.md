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
5. Prefer a packaged cffi API fast path for compact submit/result operations when `NNRP_NATIVE_BINDING_MODE=auto`.
6. Fall back to the zero-compile `ctypes` ABI path when the cffi API module is unavailable, a local environment cannot compile extension modules, or a call shape cannot be represented by the current cffi wrapper.
7. Keep a pure-Python fallback only for fixture inspection, diagnostics, and explicitly unsupported runtime combinations.

## Pinned Native Contract

The current preview3 binding work consumes `nnrp-rs` native artifact version `1.0.0-preview.3.6`.

This version is the native artifact contract pin for the current migration branch and includes:

1. The `nnrp_runtime_capabilities` export.
2. ABI version `1.5.0`.
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
| Current host fixture baseline | 2026-05-29 | 1e88eac | N/A | 3.13.1 | windows/amd64 | Intel64 Family 6 Model 183 Stepping 1, GenuineIntel | SDK-local pure Python fixture submit/result loop, 1024-byte payload, raw throughput mode. |
| Current host native ctypes | 2026-05-29 | 1e88eac | 1.0.0-preview.3.6 | 3.13.1 | windows/amd64 | Intel64 Family 6 Model 183 Stepping 1, GenuineIntel | Local `nnrp-rs` release artifact installed with `scripts/prepare_native_artifacts.py`; `NNRP_NATIVE_BINDING_MODE=ctypes`. |
| Current host native cffi API | 2026-05-29 | 1e88eac | 1.0.0-preview.3.6 | 3.13.1 | windows/amd64 | Intel64 Family 6 Model 183 Stepping 1, GenuineIntel | Same native artifact; benchmark-only cffi API wrapper compiled locally for the fast-path comparison. |

### Latency Benchmarks

| Benchmark | Payload | Iterations | Runtime path | p50 | p95 | p99 | Notes |
| --- | --- | ---: | --- | ---: | ---: | ---: | --- |
| Header encode/decode | L0 header | 100000 | Pure Python fixture codec | 1.9 us | 2.0 us | 2.6 us | Measured by `l4.header.encode_decode.latency`. |
| Metadata encode/decode | session open/open ack | 100000 | Pure Python fixture codec | 3.7 us | 4.0 us | 7.2 us | Measured by `l4.metadata.session_open_ack.latency`. |
| Metadata encode/decode | frame submit/result push | 100000 | Pure Python fixture codec | 3.2 us | 3.5 us | 5.3 us | Measured by `l4.metadata.submit_result.latency`. |
| Typed payload pack/unpack | tensor descriptor plus payload | 100000 | Pure Python fixture codec | 16.5 us | 20.2 us | 40.0 us | Measured by `l4.typed_payload.tensor_pack_unpack.latency`. |
| Runtime probe | version plus capability query | 100000 | Pure Python fixture probe | 0.5 us | 0.5 us | 0.7 us | Measured by `l4.runtime.probe.latency`. |
| Session lifecycle | open plus close loop | 100000 | Pure Python fixture lifecycle | 4.9 us | 5.3 us | 7.8 us | Measured by `l4.session.lifecycle.latency`. |
| Schema descriptor roundtrip | token delta descriptor | 100000 | Native schema codec through `ctypes` | 16.6 us | 20.0 us | 33.2 us | Measured by `l4.native.schema_descriptor.latency`. |
| Event polling | one result event | 100000 | Native single event polling through `ctypes` | 2.3 us | 2.5 us | 3.9 us | Measured by `l4.native.event_polling.latency`. |
| Submit/result loop | 1024-byte inline payload | 100000 | Pure Python fixture helper | 3.7 us | 3.9 us | 5.0 us | Local micro-latency measurement using the same fixture operation as `l4.submit_result.inline_tensor.throughput`. |
| Submit/result loop | 1024-byte inline payload | 100000 | Native compact ABI through `ctypes` | 2.2 us | 2.3 us | 2.5 us | Local micro-latency measurement over `NativeRuntimeSession.submit_result`; event materialization remains lazy. |
| Submit/result loop | 1024-byte inline payload | 100000 | Native compact ABI through cffi API | 0.5 us | 0.6 us | 0.6 us | Local micro-latency measurement over the benchmark-only cffi API wrapper. |

### Throughput Benchmarks

| Benchmark | Payload | Duration | Binding/runtime path | Throughput | Delta vs fixture baseline | Notes |
| --- | --- | ---: | --- | ---: | ---: | --- |
| Submit/result loop | 1024-byte inline payload | 10 s | Pure Python fixture helper | 264148.8 ops/s | baseline | Measured by `l4.submit_result.inline_tensor.throughput`; this is a fixture/diagnostic path, not the preferred runtime path. |
| Submit/result loop | 1024-byte inline payload | 10 s | Native compact ABI through `ctypes` | 434336.7 ops/s | +64.4% | Measured by `l4.native.submit_result.throughput`; one compact Rust FFI call per operation, `NNRP_NATIVE_BINDING_MODE=ctypes`. |
| Submit/result loop | 1024-byte inline payload | 10 s | Native compact ABI through cffi API | 1747087.8 ops/s | +561.4% | Measured by `l4.native.submit_result.cffi_api.throughput`; benchmark-only compiled wrapper on this host. |
| Batch event polling | empty batch | 10 s | Native batch event polling through `ctypes` | 398662.6 ops/s | N/A | Measured by `l4.native.event_polling.throughput`; included as a native pump smoke baseline. |

### Profiled CPU And Memory Smoke

| Benchmark | Payload | Duration | Binding/runtime path | Throughput under tracing | CPU | Peak traced memory | Notes |
| --- | --- | ---: | --- | ---: | ---: | ---: | --- |
| Submit/result loop | 1024-byte inline payload | 10 s | Native compact ABI through `ctypes` | 42198.9 ops/s | 98.6% | 1188 B | Measured by `l4.native.submit_result.profile`; tracing overhead is intentionally not compared with raw throughput. |
| Submit/result loop | 1024-byte inline payload | 10 s | Native compact ABI through cffi API | 625687.1 ops/s | 99.4% | 288 B | Measured by `l4.native.submit_result.cffi_api.profile`; confirms the compiled wrapper keeps Python-side traced memory nearly flat. |
| Batch event polling | empty batch | 10 s | Native batch event polling through `ctypes` | 77871.8 ops/s | 99.1% | 6361 B | Measured by `l4.native.event_polling.profile`; included as a native pump memory baseline. |

### Smoke Threshold Gate

The local native-runtime smoke thresholds live in `doc/benchmarks/native-runtime-smoke-thresholds.json` and can be enforced with:

```bash
python scripts/check_benchmark_thresholds.py \
  --results artifacts/native-runtime-benchmark-results.json \
  --thresholds doc/benchmarks/native-runtime-smoke-thresholds.json
```

The current host run passes the gate with the `nnrp-rs` `1.0.0-preview.3.6` Windows x86_64 artifact. The cffi API threshold is marked `allow_skip` so zero-compiler environments can still validate the ctypes fallback without pretending the preferred fast path is available.

### Interpretation

The previous ctypes path was roughly flat against the fixture baseline because Python-side result/event materialization dominated the cost after the Rust compact ABI reduced the native call count. The current hot-path pass removes eager event materialization and avoids duplicate payload view construction, so the zero-compile ctypes path now clears the 30% target with a +64.4% submit/result throughput gain on this host. On the submit/result micro-latency measurement, ctypes also improves p50 latency from 3.7 us to 2.2 us.

The cffi API path remains much faster at 1.75M ops/s and 0.5 us p50 submit/result latency, about 4.0x the optimized ctypes throughput and 6.6x the fixture throughput baseline. That confirms the Rust runtime and compact ABI have enough headroom; ctypes is still useful as a compiler-free fallback, while packaged cffi API wheels should be treated as the preferred fast path where platform/Python ABI artifacts are available.

CPU and peak-memory tracing are now recorded in separate profiled smoke rows. Those numbers are intentionally kept out of the raw comparison because tracing adds measurable per-iteration overhead.

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
2. Which native capability probe names are considered stable enough for Python-side feature gating.
