# 01 - Contract And Version Adoption

## Rust Artifact Baseline

- [x] Pin the preview4 Rust artifact version used by Python.
- [x] Probe preview4 protocol version before accepting native artifacts.
- [x] Probe ABI version before enabling preview4 native paths.
- [x] Probe transport slots.
  - [x] TCP.
  - [x] QUIC.
  - [x] IPC.
  - [x] WebSocket.
- [ ] Probe runtime-control feature flags.
- [ ] Probe runtime-object feature flags.
- [x] Reject mismatched artifacts with deterministic Python exceptions.

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

- [x] Mirror the Rust preview4 control capability token names exactly.
  - [x] `control.cancel_abort`.
  - [x] `control.supersede`.
  - [x] `control.priority_update`.
  - [x] `control.deadline_expire`.
  - [x] `control.progress_partial`.
  - [x] `control.credit_backpressure`.
  - [x] `control.capability_costs`.
  - [x] `control.route_execution_hint`.
  - [x] `control.trace_context`.
  - [x] `control.result_drop_reason`.
  - [x] `control.degrade_profile`.
  - [x] `control.budget_update`.
  - [x] `control.recoverable_error`.
  - [x] `control.retry_after`.
- [x] Mirror the Rust preview4 runtime-object and cache capability token names exactly.
  - [x] `object.lifecycle`.
  - [x] `object.delta`.
  - [x] `object.cost`.
  - [x] `object.ownership`.
  - [x] `cache.reference`.
- [x] Mirror the Rust preview4 transport names exactly.
  - [x] `tcp`.
  - [x] `quic`.
  - [x] `ipc`.
  - [x] `websocket`.

## Internal Ownership

- [x] Keep native loading in one internal backend module.
- [x] Keep cffi API detection in the backend selector.
- [x] Keep ctypes execution available only as an explicit development or diagnostic path when cffi API artifacts are unavailable.
- [x] Keep diagnostic execution paths labeled as lower-performance non-default paths.
