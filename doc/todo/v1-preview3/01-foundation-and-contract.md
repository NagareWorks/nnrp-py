# Python Preview3 Foundation And Contract

## Canonical Ownership And Public Surface

- [ ] Lock Python preview3 onto the frozen Rust-owned protocol contract rather than another pure-Python hot path.
- [ ] Finalize which Python surfaces remain first-class host APIs and which move behind Rust FFI handles.
- [ ] Finalize the public Python surface on top of the current major-version boundary without carrying superseded preview-era shims.

## FFI Consumption

- [ ] Consume the frozen handle families for connection, session, operation, schema, and buffer views.
- [ ] Implement callback/polling adapters and async runtime glue according to the frozen Rust binding contract.
- [ ] Map stable preview3 error families into Python exception/result surfaces without collapsing family/code information.
- [ ] Enforce buffer ownership and bounded-copy rules on Python views and async iterators.

## Protocol Contract Adoption

- [ ] Implement `SESSION_OPEN` / `SESSION_OPEN_ACK`, explicit session-close, and recovery semantics exactly as frozen in `nnrp-doc`.
- [ ] Implement session priority classes, operation lifecycle states, cancellation scopes, and `FLOW_UPDATE` semantics from frozen protocol enums and metadata tables.
- [ ] Implement cache lease, schema registry, and typed payload descriptor wrappers against the frozen 32B / 24B layouts and standard error behavior.
- [ ] Consume Rust-generated conformance fixtures as the only canonical preview3 protocol baseline.