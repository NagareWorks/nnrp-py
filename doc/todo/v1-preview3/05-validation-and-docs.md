# Python Preview3 Validation And Docs

## Validation

- [x] Freeze the preview3 adapter command contract as a Python-owned wrapper over the suite-owned plan/result JSON: `python -m nnrp.tools.adapter_conformance --plan <path> --output <path>`.
- [x] Freeze that `nnrp-conformance` owns only the execution-plan/result JSON and selected-case semantics; `nnrp-py` owns the module path, interpreter selection, extra flags, and runtime bootstrap around the adapter wrapper.
- [x] Add the initial `nnrp.tools.adapter_conformance` wrapper so it can read the suite-owned execution plan and emit a schema-valid case-result report.
- [x] Implement SDK-local adapter smoke execution inside `nnrp.tools.adapter_conformance` so selected core cases stop returning placeholder results.
- [ ] Extend adapter execution from SDK-local smoke coverage to full suite-selected case behavior.
- [x] Keep conformance integration adapter-first: Python declares capabilities and executes suite-owned plans rather than maintaining an SDK vector exporter.
- [x] Add Python integration tests that exercise multiple native preview3 sessions on one live connection facade.
- [x] Add Python integration tests that exercise routed multi-session preview3 result delivery on one live connection.
- [ ] Add Python integration tests for cache lease expiry, schema mismatch, operation cancellation, priority-aware flow updates, and resume paths.
- [x] Keep the active Python preview regression suite green while preview3 Rust-backed helpers replace the prior preview surface.
- [ ] Add performance smoke checks that verify Python preview3 hot paths do not regress into full payload copies by default.
- [x] Add release packaging validation for installing `nnrp-rs` native artifacts into the Python wheel source tree.

## Documentation And Rollout

- [ ] Document the preview3 Python package as a Rust-backed binding layer plus host-facing control/session helpers.
- [ ] Keep `doc/benchmarks/rs-native-artifacts-migration.md` updated with the native artifact plan, supported platform matrix, and pre/post migration benchmark results.
- [ ] Document the current connection/session model and how it replaces the earlier single-session host mental model.
- [ ] Document cache lease, schema registry, profile neutrality, and operation/workflow lifecycle semantics for Python hosts.
- [ ] Document how preview3 Rust-backed helpers replace the prior preview helper surface within `NNRP/1`.
- [ ] Document PR merge gates for freeze-dependent work so GitHub reviewers can reject Python-side protocol invention.
