# 02 - Runtime Control API

## Client Controls

- [x] Add client API for cancellation.
  - [x] Cancel by operation ID.
  - [x] Abort by operation ID.
  - [x] Provide cancellation reason.
  - [x] Suppress late results after cancellation.
- [x] Add client API for scheduling.
  - [x] Priority update.
  - [x] Deadline.
  - [x] Expire-at timestamp.
  - [x] Supersede operation.
  - [x] Budget update.
- [x] Add client API for route and execution hints.
  - [x] Local subagent route hint.
  - [x] Runtime execution hint.
  - [x] Preferred profile list.
  - [x] Degrade profile handling.

## Server Controls

- [x] Expose the contract v12 server event pump through `NativeRuntimeServerSession.next_event()`.
  - [x] Return `nnrp.server.NativeServerEvent` with exactly `submit`, `runtime`, and `lifecycle` variants.
  - [x] Convert every `FRAME_SUBMIT` to `NativeRuntimeServerOperation` before application delivery.
  - [x] Preserve non-submit wire messages as complete `NativeRuntimeEvent` values.
  - [x] Project only native event kind `14` with an absent header and one `OperationState` byte as `OperationLifecycleEvent`.
  - [x] Reject malformed lifecycle payloads and legacy terminal event kinds instead of inferring operation state.
  - [x] Serialize all receives through one per-session native event source.
- [x] Make `receive_submit(timeout)` a selective asynchronous convenience.
  - [x] Retain every skipped non-submit event in its original session order.
  - [x] Share the same pending queue and receive lock with `next_event()` and batch dispatch.
  - [x] Raise timeout or native errors without acknowledging or dropping retained events.
- [x] Align `NativeRuntimeServerOperation` with the frozen ownership contract.
  - [x] Expose only `operation_id`, `frame_id`, and the complete `submit` event as public data fields.
  - [x] Keep FFI handles, entrypoints, and session ownership private.
  - [x] Provide `send_result`, `send_result_drop`, `send_progress`, and `send_partial_result`.
  - [x] Reject streaming or drop metadata whose operation identity does not match.
- [x] Align role-level asynchronous entrypoints.
  - [x] Make `nnrp.server.NativeServer.accept(options)` asynchronous.
  - [x] Add client-session `next_event(timeout)` with runtime-or-lifecycle return semantics.
- [x] Add server API for progress events.
  - [x] Progress stage.
  - [x] Optional percent.
  - [x] Optional trace context.
- [x] Add server API for partial results.
  - [x] Partial object reference.
  - [x] Partial payload snapshot.
  - [x] Partial completion marker.
- [x] Add server API for result drop reasons.
  - [x] Deadline expired.
  - [x] Superseded.
  - [x] Backpressure.
  - [x] Peer cancellation.
- [x] Add server API for backpressure.
  - [x] Credit update.
  - [x] Pressure reason.
  - [x] Max in-flight operations.

## Diagnostics

- [x] Add trace context dataclasses.
- [x] Add result drop reason enum.
- [x] Add recoverable error dataclasses.
- [x] Preserve native status family and code in Python exceptions.
- [x] Add tests for trace propagation through cancellation and partial results.

## Native Binding

- [x] Bind preview4 control-frame native calls.
  - [x] Send each named control through one `nnrp_runtime_frame_send` ABI call.
  - [x] Decode the complete native wire header, typed metadata union, and typed tail union into `NativeRuntimeEvent`.
  - [x] Use `header.present` to separate wire events from `NativeLifecycleEvent`; do not duplicate wire identity fields outside the header.
  - [x] Release native payload owners after copying event bytes.
- [x] Bind batch event polling for progress, partial result, and drop reason events.
- [x] Keep submit/control/result loops on coarse native calls.
- [x] Add tests that count Python-to-native calls on representative hot paths.
- [x] Keep the native event-batch FFI coarse while projecting one owned event at a time.
- [x] Add a regression proving selective submit receive cannot discard an interleaved control event.
- [x] Gate the exact contract v12 type fields, native lifecycle projection, role methods, and retention rules in CI.
