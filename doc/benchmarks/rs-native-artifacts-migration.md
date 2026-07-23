# Rust Native Artifacts Benchmark Baseline

## Goal

Keep the Python host API on the canonical `nnrp-rs` runtime without hiding transport or role behavior in a Python-only
shortcut. Release benchmarks must exercise a real provider carrier, adopted Rust client and server roles, and the same
submit, control, receive, result, and polling entrypoints used by applications.

## Current Runtime Strategy

1. Pin the `nnrp-rs` release before packaging.
2. Load one transport-scoped provider artifact for TCP, QUIC, IPC, or WebSocket.
3. Probe the artifact manifest, protocol version, ABI version, feature flags, and transport slot before adoption.
4. Open or listen on the provider carrier, then transfer carrier ownership to the Rust client or server role.
5. Keep role setup outside measured loops and preserve coarse role calls inside the loop.
6. Use Python codecs and explicit fallback backends only for fixtures, diagnostics, and non-native unit tests.

The production path does not ship the retired compact-result ABI or a compiled CFFI side runtime.

## Pinned Native Contract

The current Python package consumes `nnrp-rs` native artifact version `1.0.0-preview.4.15`.

This artifact contract includes:

1. `nnrp_runtime_capabilities` and ABI version `4.0.x`.
2. Protocol version `1/0`.
3. Carrier open/listen/accept and client/server role-adoption entrypoints.
4. Client submit/cancel/poll and server receive/result entrypoints.
5. Role-neutral runtime-frame send and poll entrypoints for preview4 controls and runtime objects.
6. Transport-scoped native artifacts for TCP, QUIC, IPC, and WebSocket.

Any later `nnrp-rs` release that changes exported symbols, ABI layouts, required features, ownership transfer, or
transport-slot meaning requires a new pin and a complete rerun of this plan.

## Target Platform Matrix

| OS | Architectures | Packaging target | Required before GA |
| --- | --- | --- | --- |
| Windows | x86, x86_64, arm64 | Provider-scoped `.dll` artifacts | Yes |
| macOS | x86_64, arm64 | Provider-scoped `.dylib` artifacts | Yes |
| Linux | x86, x86_64, armv7, arm64 | Provider-scoped `.so` artifacts | Yes |
| Android | x86, x86_64, armv7, arm64 | Provider-scoped `.so` artifacts for downstream bundles | Preview gate |
| iOS | x86_64 simulator, arm64 simulator/device | Provider-scoped static artifacts for downstream linking | Preview gate |

## Benchmark Protocol

1. Record SDK commit, Python version, OS, architecture, CPU, and native artifact version.
2. Use identical iteration counts and payload shapes for comparable runs.
3. Report p50, p95, and p99 for request-like operations.
4. Report throughput, CPU, and peak traced memory for stream-like operations.
5. Keep TCP, QUIC, IPC, and WebSocket rows separate because each provider owns its transport implementation.
6. Count production FFI entrypoints per operation and reject synthetic or benchmark-only result paths.
7. Run the benchmark from the wheel candidate that will be published.

The executable plan is `doc/benchmarks/native-runtime-benchmark-plan.json`; call-shape and smoke gates are in
`doc/benchmarks/native-runtime-smoke-thresholds.json`.

## ABI 4 Release Validation

The release run must populate a result artifact for these production paths:

| Scenario | Required path | Exact call-shape gate |
| --- | --- | --- |
| Role submit/result | IPC carrier, client submit, server receive, server result, client poll | 4 calls/op |
| Submit/cancel | IPC carrier, client submit/cancel, server drains both events | 4 calls/op |
| Progress/partial result | Server progress and partial frames followed by terminal result | 7 calls/op |
| Event polling | Adopted role event pump | measured and non-zero |
| IPC provider loopback | IPC provider artifact with adopted client/server roles | 4 calls/op |
| WebSocket provider loopback | WebSocket provider artifact with adopted client/server roles | 4 calls/op |

No ABI 4 release numbers are recorded here before the candidate wheel is measured. The generated JSON result is the
release evidence; this document records the stable procedure and interpretation boundary.

Run the gate with:

```bash
python -m nnrp.tools.benchmark \
  --plan doc/benchmarks/native-runtime-benchmark-plan.json \
  --output artifacts/native-runtime-benchmark-results.json
python scripts/check_benchmark_thresholds.py \
  --results artifacts/native-runtime-benchmark-results.json \
  --thresholds doc/benchmarks/native-runtime-smoke-thresholds.json
```

## Historical ABI 1 Measurements

These measurements preserve major migration evidence. They used retired compact/CFFI or pre-ABI-3 surfaces and must
not be used as evidence that the current production role path passed release validation.

| Run | Date | SDK commit | Rust artifact | Host | Result |
| --- | --- | --- | --- | --- | --- |
| Preview3 ctypes compact | 2026-06-06 | `a7acfba` | `1.0.0-preview.3.8` | Windows amd64, Python 3.13.1 | 400045.5 ops/s |
| Preview3 CFFI batch | 2026-06-06 | `a7acfba` | `1.0.0-preview.3.8` | Windows amd64, Python 3.13.1 | 8196608.0 ops/s |
| Preview4 compact smoke | 2026-06-22 | `c1bc3e2` | `1.0.0-preview.4.0`, ABI 1.11.0 | Windows amd64, Python 3.13.5 | 39738.3 ops/s |
| Preview4 CFFI batch smoke | 2026-06-22 | `c1bc3e2` | `1.0.0-preview.4.0`, ABI 1.11.0 | Windows amd64, Python 3.13.5 | 2705408.0 ops/s |
| Preview4 IPC provider smoke | 2026-06-22 | `c1bc3e2` | `1.0.0-preview.4.0`, ABI 1.11.0 | Windows amd64, Python 3.13.5 | 67442.3 ops/s |
| Preview4 WebSocket provider smoke | 2026-06-22 | `c1bc3e2` | `1.0.0-preview.4.0`, ABI 1.11.0 | Windows amd64, Python 3.13.5 | 57326.2 ops/s |

The old batch figures measured wrapper amortization, not a bidirectional client/server operation. They are useful
historical data but are not directly comparable to the ABI 4 role submit/result scenario.

## Release Acceptance Checklist

1. Native artifact pin matches the Rust release consumed by wheel construction.
2. Every platform wheel contains the expected ABI 4 transport-scoped artifacts and no retired compact/CFFI runtime.
3. The sdist contains no prebuilt native artifacts.
4. The role submit/result, submit/cancel, and progress/partial paths satisfy their exact FFI call-shape gates.
5. IPC and WebSocket provider scenarios run against their provider artifacts.
6. Generated results identify the candidate wheel, host, commit, and Rust artifact version.
