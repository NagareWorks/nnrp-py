# Rust Native Artifacts Benchmark Baseline

## Goal

Move the Python SDK runtime path onto the canonical `nnrp-rs` native implementation while keeping a Pythonic host API.
Protocol-critical work such as wire packing, session state, compact submit/result handling, and transport-slot probing is
delegated to versioned native artifacts.

## Runtime Strategy

1. Pin the `nnrp-rs` artifact version before packaging.
2. Load native artifacts by platform and architecture at import or first use.
3. Probe ABI version, protocol version, feature flags, and transport slots before accepting an artifact.
4. Prefer packaged cffi API wheels for hot submit/result paths when `NNRP_NATIVE_BINDING_MODE=auto`.
5. Use the zero-compiler `ctypes` path as the fallback for local development, restricted hosts, and unsupported cffi API
   call shapes.
6. Keep pure-Python helpers for fixtures, diagnostics, docs, and non-hot-path validation.

## Pinned Native Contract

The current Python package consumes `nnrp-rs` native artifact version `1.0.0-preview.3.8`.

This artifact contract includes:

1. `nnrp_runtime_capabilities`.
2. ABI version `1.6.0`.
3. Protocol version `1/0`.
4. Runtime feature flags for protocol core, client/server APIs, event polling, callback dispatch, cache/schema,
   recovery, typed payloads, and transport slots.
5. Transport slot bits for TCP and optional QUIC.
6. `nnrp_client_submit_result_compact_batch` for packaged cffi API submit/result hot paths.

If a later `nnrp-rs` release changes exported symbol names, ABI struct layout, required feature flags, or
transport-slot meanings, update this pin and rerun the benchmark plan before accepting the new artifact.

## Target Platform Matrix

| OS | Architectures | Packaging target | Required before GA |
| --- | --- | --- | --- |
| Windows | x86, x86_64, arm64 | Dynamic `nnrp_ffi.dll` from `nnrp-rs` release assets | Yes |
| macOS | x86_64, arm64 | Dynamic `libnnrp_ffi.dylib` from `nnrp-rs` release assets | Yes |
| Linux | x86, x86_64, armv7, arm64 | Dynamic `libnnrp_ffi.so` from `nnrp-rs` release assets | Yes |
| Android | x86, x86_64, armv7, arm64 | Dynamic `libnnrp_ffi.so` for downstream app bundle use | Preview gate |
| iOS | x86_64 simulator, arm64 simulator/device | Static `libnnrp_ffi.a` for downstream app/toolchain linking | Preview gate |

## Benchmark Protocol

Rules:

1. Record commit SHA, Python version, OS, architecture, CPU model, and native artifact version.
2. Use the same iteration counts and payload shapes for comparable rows.
3. Report p50, p95, and p99 latency where the operation is request-like.
4. Report throughput, CPU, and peak memory where the operation is stream-like.
5. Keep QUIC benchmark rows separate from TCP and in-memory rows because QUIC is a slot, not a default dependency.

The SDK-local benchmark plan lives in `doc/benchmarks/native-runtime-benchmark-plan.json`.

## Environment

| Run | Date | SDK commit | nnrp-rs artifact | Python | OS/arch | CPU | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Fixture baseline | 2026-05-29 | 1e88eac | N/A | 3.13.1 | windows/amd64 | Intel64 Family 6 Model 183 Stepping 1, GenuineIntel | SDK-local pure Python fixture submit/result loop, 1024-byte payload. |
| Native ctypes fallback | 2026-06-06 | a7acfba | 1.0.0-preview.3.8 | 3.13.1 | windows/amd64 | Intel64 Family 6 Model 183 Stepping 1, GenuineIntel | Split TCP/QUIC release artifacts installed with `scripts/prepare_native_artifacts.py`; `NNRP_NATIVE_BINDING_MODE=ctypes`. |
| Native cffi API batch | 2026-06-06 | a7acfba | 1.0.0-preview.3.8 | 3.13.1 | windows/amd64 | Intel64 Family 6 Model 183 Stepping 1, GenuineIntel | Same split artifact install; cffi API wrapper calls `nnrp_client_submit_result_compact_batch` in 1024-operation batches. |

## Latency Benchmarks

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
| Submit/result loop | 1024-byte inline payload | 100000 | Pure Python fixture helper | 3.7 us | 3.9 us | 5.0 us | Fixture helper micro-latency. |
| Submit/result loop | 1024-byte inline payload | 100000 | Native compact ABI through `ctypes` | 2.2 us | 2.3 us | 2.5 us | `NativeRuntimeSession.submit_result`; event materialization remains lazy. |
| Submit/result loop | 1024-byte inline payload | 100000 | Native cffi API compact wrapper | 0.5 us | 0.6 us | 0.6 us | Compiled cffi API wrapper over the native compact ABI. |

## Throughput Benchmarks

| Benchmark | Payload | Duration | Binding/runtime path | Throughput | Delta vs fixture baseline | Notes |
| --- | --- | ---: | --- | ---: | ---: | --- |
| Submit/result loop | 1024-byte inline payload | 10 s | Pure Python fixture helper | 264148.8 ops/s | baseline | Fixture/diagnostic path. |
| Submit/result loop | 1024-byte inline payload | 10 s | Native compact ABI through `ctypes` | 400045.5 ops/s | +51.4% | Zero-compiler fallback path; one compact Rust FFI call per operation. |
| Submit/result loop | 1024-byte inline payload | 10 s | Native batch compact ABI through cffi API | 8196608.0 ops/s | +3003.8% | Preferred packaged fast path; one batch wrapper call per 1024 operations. Split artifacts are slightly faster than the previous all-in-one artifact run, so no split regression was observed. |
| Batch event polling | empty batch | 10 s | Native batch event polling through `ctypes` | 390234.1 ops/s | N/A | Native pump smoke baseline. |

## Profiled CPU And Memory Smoke

| Benchmark | Payload | Duration | Binding/runtime path | Throughput under tracing | CPU | Peak traced memory | Notes |
| --- | --- | ---: | --- | ---: | ---: | ---: | --- |
| Submit/result loop | 1024-byte inline payload | 10 s | Native compact ABI through `ctypes` | 40434.6 ops/s | 98.1% | 1188 B | Tracing overhead is intentionally not compared with raw throughput. |
| Submit/result loop | 1024-byte inline payload | 10 s | Native batch compact ABI through cffi API | 7985766.4 ops/s | 94.7% | 380 B | Batch wrapper keeps Python-side traced memory nearly flat. |
| Batch event polling | empty batch | 10 s | Native batch event polling through `ctypes` | 71057.7 ops/s | 93.9% | 6361 B | Native pump memory baseline. |

## Smoke Threshold Gate

The local native-runtime smoke thresholds live in `doc/benchmarks/native-runtime-smoke-thresholds.json` and can be
enforced with:

```bash
python scripts/check_benchmark_thresholds.py \
  --results artifacts/native-runtime-benchmark-results.json \
  --thresholds doc/benchmarks/native-runtime-smoke-thresholds.json
```

The cffi API threshold is marked `allow_skip` so zero-compiler environments can validate the ctypes fallback without
claiming that the preferred packaged fast path is present.

## Release Acceptance Checklist

1. Native artifact pin matches the `nnrp-rs` release consumed by the wheel build.
2. Packaged cffi API wheels expose `nnrp_py_client_submit_result_compact_batch`.
3. Native wheels include the platform artifact and cffi API fast path for every published platform.
4. `NNRP_NATIVE_BINDING_MODE=auto` selects the cffi API fast path when it is available.
5. `NNRP_NATIVE_BINDING_MODE=ctypes` remains usable on hosts without a local C toolchain.
6. Benchmark results record the selected binding mode, batch size, and FFI calls per operation.
