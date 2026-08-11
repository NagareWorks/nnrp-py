# SDK contract v15: submit wait and correlation

This task adopts the frozen SDK API contract v15 without exposing raw frame-send primitives or compatibility behavior for older previews.

- [x] Preserve each submitted `operation_id` to `frame_id` pair for operation-scoped runtime controls.
  - [x] Bind client scheduling, budget, routing, cancellation, supersede, and object-reference controls to the submit frame.
  - [x] Bind server object-reference controls to the accepted submit frame.
  - [x] Reject controls that reference an inactive non-zero operation.
  - [x] Release the binding after terminal evidence, cancellation, supersede, abort, or session close.
- [x] Preserve frozen session-scope correlation.
  - [x] Send session-scoped `CANCEL`, `ABORT`, `BUDGET_UPDATE`, `OBJECT_REF`, and `OBJECT_RELEASE` with `operation_id = 0` and `frame_id = 0`.
  - [x] Reject `operation_id = 0` for controls that are operation-scoped only.
  - [x] Keep client and server session-scope rules distinct where the frozen role surface differs.
- [x] Implement the frozen submit-and-wait lifecycle.
  - [x] Emit `DEADLINE` on the exact submit frame before a time-bounded submit dispatch.
  - [x] Convert wait expiry to Python `TimeoutError` and send native cancellation after dispatch.
  - [x] Emit no submit or cancellation frame when an async wait is cancelled before dispatch.
  - [x] Convert cancellation after dispatch to `asyncio.CancelledError` and send native cancellation.
  - [x] Accept independently produced terminal lifecycle evidence as `NativeRuntimeResult`.
  - [x] Keep non-terminal lifecycle evidence observable without completing the wait.
- [x] Keep high-level client convenience behavior identical to `NativeRuntimeSession`.
- [x] Cover frame binding, session scope, timeout, cancellation, terminal lifecycle, and cross-session isolation in tests.
- [x] Keep the live wire target valid as the suite adds same-endpoint scenarios.
  - [x] Reuse one native listener for scenarios that share a transport endpoint.
  - [x] Serve same-endpoint scenarios sequentially without skipping independent wire cases.
  - [x] Verify the external `DEADLINE` frame arrives before its exactly correlated `FRAME_SUBMIT`.
- [x] Run the complete local lint, contract, coverage, conformance, wire E2E, and incremental-coverage gates.
- [x] Pin wire E2E to the merged Rust commit that implements the same v15 correlation semantics.
