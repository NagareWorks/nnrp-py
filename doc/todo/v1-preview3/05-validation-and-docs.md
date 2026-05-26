# Python Preview3 Validation And Docs

## Validation

- [x] Freeze the preview3 adapter command contract as a Python-owned wrapper over the suite-owned plan/result JSON: `python -m nnrp.tools.adapter_conformance --plan <path> --output <path>`.
- [x] Freeze that `nnrp-conformance` owns only the execution-plan/result JSON and selected-case semantics; `nnrp-py` owns the module path, interpreter selection, extra flags, and runtime bootstrap around the adapter wrapper.
- [x] Add the initial `nnrp.tools.adapter_conformance` wrapper so it can read the suite-owned execution plan and emit a schema-valid case-result report.
- [x] Implement SDK-local adapter smoke execution inside `nnrp.tools.adapter_conformance` so selected core cases stop returning placeholder results.
- [ ] Extend adapter execution from SDK-local smoke coverage to full suite-selected case behavior.
  - [ ] Add case dispatch table that maps suite-selected case ids to native host API operations.
  - [ ] Add case parameter decoding for connection/session/operation ids.
  - [ ] Add case parameter decoding for payload shapes and expected result states.
  - [ ] Add evidence artifact writing for each executed adapter case.
  - [ ] Add failure diagnostics that preserve native status/family/detail fields.
- [x] Keep conformance integration adapter-first: Python declares capabilities and executes suite-owned plans rather than maintaining an SDK vector exporter.
- [x] Add Python integration tests that exercise multiple native preview3 sessions on one live connection facade.
- [x] Add Python integration tests that exercise routed multi-session preview3 result delivery on one live connection.
- [ ] Add Python integration tests for cache lease expiry, schema mismatch, operation cancellation, priority-aware flow updates, and resume paths.
  - [ ] Add cache lease expiry integration test once Rust exposes cache lease operations.
  - [ ] Add schema mismatch integration test once Rust exposes schema registry operations.
  - [ ] Add operation cancellation integration test through native session/operation helpers.
  - [ ] Add priority-aware flow update integration test once Rust exposes priority/credit events.
  - [ ] Add resume-path integration test once Rust exposes recovery/resume operations.
- [x] Keep the active Python preview regression suite green while preview3 Rust-backed helpers replace the prior preview surface.
- [ ] Add performance smoke checks that verify Python preview3 hot paths do not regress into full payload copies by default.
  - [ ] Add submit/result allocation-count smoke.
  - [ ] Add payload-copy boundary smoke.
  - [ ] Add native artifact load/probe latency smoke.
  - [ ] Gate smoke thresholds with stable local baselines before enabling CI failure.
- [x] Add release packaging validation for installing `nnrp-rs` native artifacts into the Python wheel source tree.

## Documentation And Rollout

- [ ] Document the preview3 Python package as a Rust-backed binding layer plus host-facing control/session helpers.
  - [ ] Add native artifact installation/loading section.
  - [ ] Add native connection/session quick-start.
  - [ ] Add native submit/result/cancel/control examples.
  - [ ] Add fallback/require-native behavior section.
- [ ] Keep `doc/benchmarks/rs-native-artifacts-migration.md` updated with the native artifact plan, supported platform matrix, and pre/post migration benchmark results.
  - [ ] Update pinned `nnrp-rs` tag/commit before release.
  - [ ] Fill post-migration benchmark environment row.
  - [ ] Fill post-migration latency table.
  - [ ] Fill post-migration throughput table.
  - [ ] Add interpretation notes for regressions or wins.
- [ ] Document the current connection/session model and how it replaces the earlier single-session host mental model.
  - [ ] Document connection-owned session lifecycle.
  - [ ] Document multiple sessions on one connection.
  - [ ] Document routed result polling.
  - [ ] Document session close and connection context cleanup behavior.
- [ ] Document cache lease, schema registry, profile neutrality, and operation/workflow lifecycle semantics for Python hosts.
  - [ ] Document cache lease host wrapper behavior.
  - [ ] Document schema/profile registry host wrapper behavior.
  - [ ] Document `profile_id = 0` as unspecified.
  - [ ] Document tensor/token parity and structured-event/tool-delta payload families.
  - [ ] Document operation lifecycle states and diagnostics.
- [ ] Document how preview3 Rust-backed helpers replace the prior preview helper surface within `NNRP/1`.
  - [ ] Map old packet/session examples to native host API examples.
  - [ ] Move packet builder examples into protocol fixture/diagnostic sections.
  - [ ] Remove obsolete exporter/vector wording.
- [ ] Document PR merge gates for freeze-dependent work so GitHub reviewers can reject Python-side protocol invention.
  - [ ] List required upstream Rust/doc freeze references for cache/schema/recovery work.
  - [ ] List required conformance adapter coverage for public API changes.
  - [ ] List required coverage/benchmark gates for hot-path changes.
