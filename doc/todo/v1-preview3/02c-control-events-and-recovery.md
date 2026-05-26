# Python Preview3 Control Events And Recovery

- [x] Add async-friendly wrappers for result/event pump delivery.
- [ ] Add async-friendly wrappers for `FLOW_UPDATE` and `RESULT_HINT`.
- [x] Keep Python event delivery aligned with Rust-native semantics rather than inventing a second Python session-pump contract.
- [ ] Add resume/recovery helpers only after the recovery object boundary freezes upstream.
- [ ] Keep resume tokens and windows opaque Rust-owned data on the Python host surface.
