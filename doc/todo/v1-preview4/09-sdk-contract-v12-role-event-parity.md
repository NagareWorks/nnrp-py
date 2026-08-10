# SDK Contract v12 Role-Event Parity

This workstream aligns the Python role APIs with the frozen NNRP/1 Preview4 SDK API contract
version 12. The server event pump preserves submit ownership and event order, while the native
operation lifecycle projection follows the exact FFI representation instead of inferring state
from legacy terminal event kinds.

## Role Event Surface

- [x] Expose asynchronous `NativeRuntimeSession.next_event(timeout)` for runtime and lifecycle events.
- [x] Expose asynchronous `NativeServer.accept(options)`.
- [x] Expose asynchronous `NativeRuntimeServerSession.next_event(timeout)` as the canonical ordered pump.
- [x] Expose selective `receive_submit(timeout)` without discarding skipped runtime or lifecycle events.
- [x] Represent server delivery as the closed `submit`, `runtime`, and `lifecycle` union.
- [x] Preserve the complete submit event and native reply ownership in `NativeRuntimeServerOperation`.
- [x] Keep role event polling coarse through bounded native batches and one serialized receive source.

## Native Lifecycle Projection

- [x] Recognize only native event kind `14` as `operation_lifecycle`.
- [x] Require an absent runtime header and an owned one-byte `OperationState` payload.
- [x] Resolve operation identity from `diagnostic.related_operation_id` and retain the native handle when live.
- [x] Reject missing identity, wrong payload length, unknown state values, and legacy event-kind substitution.
- [x] Keep legacy result terminal mapping inside result polling instead of exposing it through `next_event()`.

## Validation

- [x] Gate the exact contract v12 types, projections, role operations, event kind, and retention rules in CI.
- [x] Cover all eight operation states and malformed native lifecycle projections.
- [x] Cover interleaved control and submit delivery without event loss or reordering.
- [x] Exercise lifecycle decoding against Rust commit `09a0d92710475bc0de59227567947d9f73b781da`.
- [x] Pass total coverage, suite-owned adapter conformance, native artifact E2E, and all wire E2E modes.
