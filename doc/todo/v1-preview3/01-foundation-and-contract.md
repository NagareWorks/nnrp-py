# Python Preview3 Foundation And Contract

## Canonical Ownership And Public Surface

- [ ] Lock Python preview3 onto the frozen Rust-owned protocol contract rather than another pure-Python hot path.
  - [x] Add native artifact loading, ABI/protocol probing, and deterministic rejection for mismatched Rust libraries.
  - [x] Add Rust-backed connection, session, operation, event, and result facades.
  - [x] Route adapter smoke execution through the Rust-backed facade or the explicit SDK-local fallback.
  - [ ] Route public host helpers that still execute packet/session hot paths in Python through Rust-backed facades.
  - [ ] Move remaining pure-Python packet/session hot-path code behind fixture, diagnostic, or unsupported-runtime boundaries.
- [ ] Finalize which Python surfaces remain first-class host APIs and which move behind Rust FFI handles.
  - [x] Keep native connection/session context helpers as first-class host APIs.
  - [x] Keep operation lifecycle/result wrappers as first-class host APIs.
  - [ ] Decide whether legacy transport smoke helpers remain public runtime APIs or move to tooling-only status.
  - [ ] Decide whether cache/schema/profile helpers expose handles directly or host-friendly descriptor wrappers only.
- [ ] Finalize the public Python surface on top of the current major-version boundary without carrying superseded preview-era shims.
  - [ ] Audit public imports for superseded helper names.
  - [ ] Remove or rename superseded helper names before release packaging.
  - [ ] Update README/API docs to describe only the current Rust-backed public surface.

## FFI Consumption

- [ ] Consume the frozen handle families for connection, session, operation, schema, and buffer views.
  - [x] Wrap connection, session, operation, event pump, and buffer value handles.
  - [ ] Wrap schema registry handles once exported by the ABI.
  - [x] Wrap current immutable/mutable buffer-view value structs.
  - [ ] Wrap borrowed buffer handles once exported by the ABI.
- [ ] Implement callback/polling adapters and async runtime glue according to the frozen Rust binding contract.
  - [x] Add explicit polling, bounded polling, async polling, and async event iteration.
  - [x] Add structured event, tool delta, and workflow-state async iterators over Rust result pumps.
  - [x] Add callback dispatch wrappers over Rust result pump events.
  - [x] Add batch polling once the ABI exposes it.
  - [ ] Add borrowed-buffer delivery once the ABI exposes stable borrowed handles.
- [x] Map stable preview3 error families into Python exception/result surfaces without collapsing family/code information.
  - [x] Map stable FFI status codes to Python exception classes while preserving status/family/detail fields.
  - [x] Add public diagnostic helpers for structured downgrade, retry, cache, and schema errors.
- [ ] Enforce buffer ownership and bounded-copy rules on Python views and async iterators.
  - [x] Snapshot polled native event payloads into Python-owned bytes.
  - [ ] Document and test copy boundaries for submit payloads, result payloads, and future borrowed views.
  - [ ] Add zero-copy guard tests once borrowed views are available.

## Protocol Contract Adoption

- [ ] Implement `SESSION_OPEN` / `SESSION_OPEN_ACK`, explicit session-close, and recovery semantics exactly as frozen in `nnrp-doc`.
  - [x] Expose explicit open-session and close-session helpers through native handles.
  - [x] Support multiple sessions on one native connection facade.
  - [x] Add connection-level host helper that opens and closes multiple native sessions.
  - [x] Add recovery validation helpers after the Rust recovery object boundary freezes.
  - [ ] Add executable resume/open-with-token helpers once Rust exposes the FFI operation.
- [ ] Implement session priority classes, operation lifecycle states, cancellation scopes, and `FLOW_UPDATE` semantics from frozen protocol enums and metadata tables.
  - [x] Preserve operation lifecycle states on Python result wrappers.
  - [x] Add operation cancellation helpers on native sessions and operations.
  - [x] Add priority and scheduling hint wrappers.
  - [x] Add `FLOW_UPDATE`/credit wrappers over Rust control/event delivery.
- [ ] Implement cache lease, schema registry, and typed payload descriptor wrappers against the frozen 32B / 24B layouts and standard error behavior.
  - [x] Add cache lease descriptor wrappers.
  - [x] Add schema/profile descriptor wrappers.
  - [x] Add typed payload descriptor view wrappers.
  - [x] Add standard schema/profile error mapping tests.
  - [ ] Add standard cache runtime error mapping tests once Rust exposes cache lease FFI operations.
- [ ] Consume Rust-generated conformance fixtures as the only canonical preview3 protocol baseline.
  - [x] Remove SDK-local vector exporters.
  - [x] Use adapter execution plans instead of SDK-owned vector manifests.
  - [ ] Extend adapter execution from smoke coverage to full suite-selected behavior.
  - [ ] Remove any remaining Python-owned canonical fixture generation paths.
