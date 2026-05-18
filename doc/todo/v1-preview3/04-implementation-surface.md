# Python Preview3 Implementation Surface

## Scope

1. `04` owns how Python consumes the Rust binding and turns it into runtime-facing Python integration.
2. `04` does not own preview3 session semantics, operation-state meaning, or scheduler policy; those are defined in `nnrp-rs/02` and reflected in Python by `02a/02b/02c`.
3. `04` should not block `02` with async-runtime glue unless the binding contract itself changes.

## Sub-Shards

1. `04a-rust-binding-adoption.md`: Rust FFI consumption, handle wrappers, and error mapping.
2. `04b-python-host-api-surface.md`: Rust-backed preview3 host APIs and lifecycle-preserving Python wrappers.
3. `04c-async-runtime-integration.md`: async delivery, callback/poll integration, and non-hot-path pure-Python fallback boundaries.

## Dependency Gates

1. `04a` depends directly on `nnrp-rs/04`; it should not wait on Python host-shape debates.
2. `04b` depends on `04a` plus the semantic decisions frozen in `02a/02b/02c`; it should not redesign async delivery policy.
3. `04c` depends on `04a/04b` artifacts and focuses on Python runtime integration glue only.
4. If a PR changes both `02` and `04`, the change must state explicitly whether it moves a semantic boundary or only adapts binding/runtime wiring.