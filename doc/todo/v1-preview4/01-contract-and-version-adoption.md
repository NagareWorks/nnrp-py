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
- [ ] Keep preview3 wheels installable for users pinned to preview3.
- [ ] Keep preview4 release notes separate from preview3 performance notes.
- [ ] Add release checks that fail if preview4 wheels embed preview3-only artifacts.

## Public API Boundary

- [ ] Keep host-facing client and server entrypoints stable where semantics match preview3.
- [ ] Add preview4-specific request options without overloading preview3 packet helper APIs.
- [ ] Keep fixture builders out of runtime quick-start paths.
- [ ] Document explicit native requirement for preview4 runtime-control hot paths.

## Internal Ownership

- [ ] Keep native loading in one internal backend module.
- [ ] Keep cffi API detection in the backend selector.
- [ ] Keep ctypes fallback available for development environments that cannot build cffi API locally.
- [ ] Keep fallback paths labeled as lower-performance compatibility paths.
