# 02 - Runtime Control API

## Client Controls

- [ ] Add client API for cancellation.
  - [ ] Cancel by operation ID.
  - [ ] Abort by operation ID.
  - [ ] Provide cancellation reason.
  - [ ] Suppress late results after cancellation.
- [ ] Add client API for scheduling.
  - [ ] Priority update.
  - [ ] Deadline.
  - [ ] Expire-at timestamp.
  - [ ] Supersede operation.
  - [ ] Budget update.
- [ ] Add client API for route and execution hints.
  - [ ] Local subagent route hint.
  - [ ] Runtime execution hint.
  - [ ] Preferred profile list.
  - [ ] Degrade profile handling.

## Server Controls

- [ ] Add server API for progress events.
  - [ ] Progress stage.
  - [ ] Optional percent.
  - [ ] Optional trace context.
- [ ] Add server API for partial results.
  - [ ] Partial object reference.
  - [ ] Partial payload snapshot.
  - [ ] Partial completion marker.
- [ ] Add server API for result drop reasons.
  - [ ] Deadline expired.
  - [ ] Superseded.
  - [ ] Backpressure.
  - [ ] Peer cancellation.
- [ ] Add server API for backpressure.
  - [ ] Credit update.
  - [ ] Pressure reason.
  - [ ] Max in-flight operations.

## Diagnostics

- [x] Add trace context dataclasses.
- [x] Add result drop reason enum.
- [x] Add recoverable error dataclasses.
- [ ] Preserve native status family and code in Python exceptions.
- [ ] Add tests for trace propagation through cancellation and partial results.

## Native Binding

- [ ] Bind preview4 control-frame native calls.
- [ ] Bind batch event polling for progress, partial result, and drop reason events.
- [ ] Keep submit/control/result loops on coarse native calls.
- [ ] Add tests that count Python-to-native calls on representative hot paths.
