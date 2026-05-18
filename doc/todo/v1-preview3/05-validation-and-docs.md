# Python Preview3 Validation And Docs

## Validation

- [ ] Add a preview3 conformance exporter and wire it into the suite-owned conformance action against the Rust canonical vectors.
- [ ] Add Python integration tests that exercise multi-session preview3 flows on one live connection.
- [ ] Add Python integration tests for cache lease expiry, schema mismatch, operation cancellation, priority-aware flow updates, and resume paths.
- [ ] Keep the active Python preview regression suite green while preview3 Rust-backed helpers replace the prior preview surface.
- [ ] Add performance smoke checks that verify Python preview3 hot paths do not regress into full payload copies by default.

## Documentation And Rollout

- [ ] Document the preview3 Python package as a Rust-backed binding layer plus host-facing control/session helpers.
- [ ] Document the current connection/session model and how it replaces the earlier single-session host mental model.
- [ ] Document cache lease, schema registry, profile neutrality, and operation/workflow lifecycle semantics for Python hosts.
- [ ] Document how preview3 Rust-backed helpers replace the prior preview helper surface within `NNRP/1`.
- [ ] Document PR merge gates for freeze-dependent work so GitHub reviewers can reject Python-side protocol invention.