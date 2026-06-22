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
- [x] Bind batch event polling for progress, partial result, and drop reason events.
- [x] Keep submit/control/result loops on coarse native calls.
- [x] Add tests that count Python-to-native calls on representative hot paths.
