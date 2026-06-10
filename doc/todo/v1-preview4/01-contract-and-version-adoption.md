# 01 - Contract And Version Adoption

## Rust Artifact Baseline

- [ ] Pin the preview4 Rust artifact version used by Python.
- [ ] Probe preview4 protocol version before accepting native artifacts.
- [ ] Probe ABI version before enabling preview4 native paths.
- [ ] Probe transport slots.
  - [ ] TCP.
  - [ ] QUIC.
  - [ ] IPC.
  - [ ] WebSocket.
- [ ] Probe runtime-control feature flags.
- [ ] Probe runtime-object feature flags.
- [ ] Reject mismatched artifacts with deterministic Python exceptions.

## Package Versioning

- [ ] Move Python package version to the preview4 line in the release-preparation commit.
- [ ] Keep preview4 release notes separate from preview3 performance notes.
- [ ] Add release checks that fail if preview4 wheels embed preview3-only artifacts.

## Public API Boundary

- [ ] Keep host-facing client and server entrypoints stable where semantics match preview3.
- [ ] Add preview4-specific request options without overloading preview3 packet helper APIs.
- [ ] Keep fixture builders out of runtime quick-start paths.
- [ ] Document explicit native requirement for preview4 runtime-control hot paths.

## Capability Token Catalog

- [ ] Mirror the Rust preview4 control capability token names exactly.
  - [ ] `control.cancel_abort`.
  - [ ] `control.supersede`.
  - [ ] `control.priority_update`.
  - [ ] `control.deadline_expire`.
  - [ ] `control.progress_partial`.
  - [ ] `control.credit_backpressure`.
  - [ ] `control.capability_costs`.
  - [ ] `control.route_execution_hint`.
  - [ ] `control.trace_context`.
  - [ ] `control.result_drop_reason`.
  - [ ] `control.degrade_profile`.
  - [ ] `control.budget_update`.
  - [ ] `control.recoverable_error`.
  - [ ] `control.retry_after`.
- [ ] Mirror the Rust preview4 runtime-object and cache capability token names exactly.
  - [ ] `object.lifecycle`.
  - [ ] `object.delta`.
  - [ ] `object.cost`.
  - [ ] `object.ownership`.
  - [ ] `cache.reference`.
- [ ] Mirror the Rust preview4 transport names exactly.
  - [ ] `tcp`.
  - [ ] `quic`.
  - [ ] `ipc`.
  - [ ] `websocket`.

## Internal Ownership

- [ ] Keep native loading in one internal backend module.
- [ ] Keep cffi API detection in the backend selector.
- [ ] Keep ctypes execution available only as an explicit development or diagnostic path when cffi API artifacts are unavailable.
- [ ] Keep diagnostic execution paths labeled as lower-performance non-default paths.
