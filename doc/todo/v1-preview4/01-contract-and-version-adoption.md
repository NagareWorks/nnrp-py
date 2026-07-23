# 01 - Contract And Version Adoption

## Rust Artifact Baseline

- [x] Pin Rust artifact `1.0.0-preview.4.15` and ABI `4.0.x` used by Python.
- [x] Probe preview4 protocol version before accepting native artifacts.
- [x] Probe ABI version before enabling preview4 native paths.
- [x] Probe transport slots.
  - [x] TCP.
  - [x] QUIC.
  - [x] IPC.
  - [x] WebSocket.
- [x] Probe runtime-control feature flags.
- [x] Probe runtime-object feature flags.
- [x] Reject mismatched artifacts with deterministic Python exceptions.

## Package Versioning

- [x] Move Python package version to the preview4 line in the release-preparation commit.
- [x] Keep preview4 release notes separate from preview3 performance notes.
- [x] Add release checks that fail if preview4 wheels embed preview3-only artifacts.

## Public API Boundary

- [x] Expose only the preview4 host-facing client and server entrypoints frozen in `nnrp-doc`.
- [x] Add preview4-specific request options without overloading preview3 packet helper APIs.
- [x] Keep fixture builders out of runtime quick-start paths.
- [x] Document explicit native requirement for preview4 runtime-control hot paths.
- [x] Keep role-neutral frame encoding and raw control codes internal to the SDK.

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
- [x] Keep production carrier and role adoption in the ABI 4 `ctypes` backend.
- [x] Remove the retired compact-result and CFFI side paths instead of preserving a preview3 compatibility route.
- [x] Keep explicit fallback backends limited to tests and diagnostics; production host helpers adopt real Rust roles.
