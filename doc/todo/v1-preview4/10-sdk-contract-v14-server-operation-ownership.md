# SDK Contract v14 Server-Operation Ownership

This workstream aligns the Python role API with SDK API contract version 14. Operation-scoped replies use the
native operation handle retained from `FRAME_SUBMIT`; server sessions expose only session-scoped controls.

## Public Surface

- [x] Make `NativeRuntimeServerOperation.send_result(...)` asynchronous and terminal.
- [x] Make `send_result_drop(...)` asynchronous and terminal.
- [x] Make `send_progress(...)` and `send_partial_result(...)` asynchronous and nonterminal.
- [x] Remove progress, partial-result, and result-drop reply bypasses from `NativeRuntimeServerSession`.
- [x] Keep selective `receive_submit(...)` ordering and retained-event behavior unchanged.
- [x] Retain accepted-operation reply ownership after peer cancel, abort, supersede, and lifecycle delivery.
- [x] Release Python operation correlation only after one terminal reply succeeds or the session closes.
- [x] Reject incremental and duplicate terminal replies after terminal success, allow failed sends to retry, and settle in-flight native sends before propagating cancellation.

## Native Boundary

- [x] Route progress, partial-result, and result-drop frames through the retained native operation handle.
- [x] Keep each reply as one coarse native call without introducing metadata-level FFI crossings.
- [x] Retain Python-owned payload storage through asynchronous native result calls.
- [x] Reject operation-scoped frame sends through session handles in the native test double.

## Validation And Public Guidance

- [x] Gate contract version 14 role method names, signatures, ownership, and terminal semantics.
- [x] Cover operation identity checks, terminal retry, lifecycle retention, and the absence of session reply bypasses.
- [x] Exercise operation-owned replies in benchmark, wire-target, and packaged-artifact E2E paths.
- [x] Run wire conformance against merged Rust commit `11f3afcb12a71cde39a02fd918a51a542ae0c7fb`.
- [x] Update the README example to distinguish operation replies from session controls.
- [x] Pass lint, the complete Python test suite, and total coverage above 90 percent.
