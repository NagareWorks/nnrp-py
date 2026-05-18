# Python Preview3 Control Events And Recovery

- [ ] Add async-friendly wrappers for `FLOW_UPDATE`, `RESULT_HINT`, and result/event pump delivery.
- [ ] Keep Python event delivery aligned with Rust-native semantics rather than inventing a second Python session-pump contract.
- [ ] Add resume/recovery helpers only after the recovery object boundary freezes upstream.
- [ ] Keep resume tokens and windows opaque Rust-owned data on the Python host surface.