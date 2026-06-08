# Native Release Boundary

This document defines what belongs in the first Rust-backed Python package release and what stays outside the preview3 release gate. The Python SDK must expose the native runtime contract without inventing protocol fields that are absent from the frozen FFI structs.

## Release-Required Surface

The first native-backed release includes:

1. Native artifact discovery, platform resolution, ABI/protocol probing, and deterministic mismatch errors.
2. Connection, session, operation, event pump, schema registry, cache lease, and native-owned buffer handles.
3. Explicit connection/session lifecycle helpers, operation submit/cancel/result helpers, control dispatch, flow updates, result hints, payload-family event helpers, and recovery validation/resume helpers.
4. Copy-boundary guarantees for submit payloads and polled result/control payloads.
5. Platform wheel validation proving each wheel embeds exactly one matching native artifact.

## Scheduling Hints

Python exposes immutable scheduling hint models so hosts can carry operation grouping, parent operation ids, and deadline hints through their own orchestration code. The preview3 native `NnrpSubmitRequest` contains session, operation id, frame id, and payload. Python must not encode scheduling hints into private control payloads or mutate the submit struct locally.

Release rule:

1. Validate and preserve scheduling hints on Python operation wrappers.
2. Send only the fields present in `NnrpSubmitRequest` to native submit.
3. Reject conflicting Python hint inputs before calling native submit.
4. Add native request-shape tests whenever the FFI submit struct changes.

## Recovery Tokens

Python exposes recovery validation reports, resume windows, migration replay decisions, and executable resume helpers. It does not expose a first-class recovery-token parser in the first release. Token bytes remain native/protocol-owned data, and Python surfaces only identity and diagnostic metadata that the native runtime returns.

Release rule:

1. Keep token bytes opaque on the Python host surface.
2. Do not parse token bodies or infer token policy in Python.
3. Add a dedicated token wrapper only if a future native ABI exposes a stable token handle with lifetime semantics.

## Borrowed And Zero-Copy Buffers

The first release uses copied Python-owned snapshots for polled payloads and native-owned copied buffer handles for explicit buffer acquisition. Borrowed result/body views are outside the preview3 release-required surface and must be enabled only after lifetime ownership and release semantics are documented and guarded in Python.

Release rule:

1. Keep copied snapshots as the default result/control payload boundary.
2. Keep native-owned acquired buffers behind explicit close/release APIs.
3. Do not expose borrowed mutable views in the first release.
4. Gate any future borrowed view with sync and async lifetime tests before public exposure.

## Benchmark Gate

The release can ship with copied snapshots if benchmark results show acceptable overhead. If benchmark results show copy costs dominate hot-path latency or allocation count, borrowed-buffer work becomes a blocker for the release line that introduces that borrowed-view surface, not retroactively for the first native-backed release.
