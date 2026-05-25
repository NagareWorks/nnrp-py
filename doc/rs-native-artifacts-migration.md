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

## Target Platform Matrix

| OS | Architectures | Packaging target | Required before GA |
| --- | --- | --- | --- |
| Windows | x86, x86_64, arm64 | Wheel native data or per-platform wheels | Yes |
| macOS | x86_64, arm64 | Universal or per-arch wheel artifacts | Yes |
| Linux | x86, x86_64, arm, arm64 | manylinux or equivalent wheel artifacts | Yes |
| Android | x86, x86_64, armv7, arm64 | Embedded host package or downstream app bundle | Preview gate |
| iOS | x86_64 simulator, arm64 simulator/device | Embedded host package or downstream app bundle | Preview gate |

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
| Pre-migration baseline | TBD | TBD | N/A | TBD | TBD | TBD | TBD |
| Post-migration native | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

### Latency Benchmarks

| Benchmark | Payload | Iterations | Pre p50 | Pre p95 | Pre p99 | Post p50 | Post p95 | Post p99 | Delta | Notes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Header encode/decode | L0 header | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Metadata encode/decode | session open/open ack | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Metadata encode/decode | frame submit/result push | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Typed payload pack/unpack | tensor descriptor plus payload | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Runtime probe | version plus capability query | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Session lifecycle | open plus close loop | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

### Throughput Benchmarks

| Benchmark | Payload | Duration | Pre throughput | Pre CPU | Pre peak memory | Post throughput | Post CPU | Post peak memory | Delta | Notes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Submit/result loop | inline tensor payload | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| TCP loopback | request/result stream | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| QUIC loopback | request/result stream | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | Optional slot |

## Migration Phases

1. Capture pre-migration benchmarks and commit the results to this document.
2. Add native artifact discovery, loader validation, and ABI/protocol probes.
3. Add Python handle wrappers for connection, session, operation, schema, and buffer views.
4. Move preview3 hot-path encode/decode and submit/result flow behind the native backend.
5. Keep Python APIs stable and isolate backend selection behind one internal module.
6. Add post-migration benchmarks and record the deltas in this document.
7. Enable conformance and packaging CI for the supported platform matrix.

## Open Decisions

1. Whether Python should ship one wheel per platform or a thin wheel plus externally resolved native artifacts.
2. Whether the first binding layer should use `ctypes` directly or a generated binding layer over the stable C ABI.
3. Which native capability probe names are considered stable enough for Python-side feature gating.
