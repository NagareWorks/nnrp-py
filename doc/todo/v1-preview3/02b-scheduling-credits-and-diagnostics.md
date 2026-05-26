# Python Preview3 Scheduling, Credits, And Diagnostics

- [ ] Add Python wrappers for session priority class and operation-scoped scheduling hints.
  - [ ] Add immutable host model for session priority class values.
  - [ ] Add immutable host model for operation scheduling hint values.
  - [ ] Map those host models into native submit/control request fields once exposed.
  - [ ] Add validation tests for out-of-range priority and hint values.
- [x] Add Python host models for operation lifecycle state and cancel scope using upstream frozen enums.
- [x] Surface connection/session/operation credit updates without redefining scheduler semantics in Python.
  - [x] Add credit update event/result wrapper types.
  - [x] Route credit update events through native connection polling.
  - [x] Add async iterator coverage for credit updates.
  - [x] Add tests proving Python does not recompute scheduler policy locally.
- [x] Surface downgrade and retry reasons as structured Python diagnostics.
  - [x] Add downgrade diagnostic wrapper preserving Rust status/family/detail fields.
  - [x] Add retry diagnostic wrapper preserving Rust status/family/detail fields.
  - [x] Map diagnostic wrappers onto failed/degraded/stale result paths.
  - [x] Add tests for unknown diagnostic family pass-through.
