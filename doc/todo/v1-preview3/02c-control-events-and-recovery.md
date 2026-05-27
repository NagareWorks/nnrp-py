# Python Preview3 Control Events And Recovery

- [x] Add async-friendly wrappers for result/event pump delivery.
- [ ] Add async-friendly wrappers for `FLOW_UPDATE` and `RESULT_HINT`.
  - [x] Add `FLOW_UPDATE` event wrapper over Rust poll results.
  - [ ] Add `RESULT_HINT` event wrapper over Rust poll results.
  - [x] Add async iterator helpers for control-event filtering.
  - [x] Add cancellation behavior tests for control-event iterators.
- [x] Keep Python event delivery aligned with Rust-native semantics rather than inventing a second Python session-pump contract.
- [ ] Add resume/recovery helpers only after the recovery object boundary freezes upstream.
  - [ ] Add opaque recovery-token wrapper.
  - [x] Add resume-window wrapper.
  - [ ] Add connection/session resume helper once Rust exposes the operation.
  - [x] Add tests for invalid/expired recovery diagnostics.
- [ ] Keep resume tokens and windows opaque Rust-owned data on the Python host surface.
  - [ ] Ensure token/window wrappers expose identity and diagnostic metadata only.
  - [ ] Add tests preventing Python-side token parsing.
