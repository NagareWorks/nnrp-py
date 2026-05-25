# Python Preview3 Rust Binding Adoption

- [ ] Consume the frozen Rust FFI surface from `nnrp-rs`.
- [x] Pin the exact `nnrp-rs` commit, tag, or artifact version used by the Python package.
- [x] Define the packaged native artifact layout for Windows, macOS, Linux, Android, and iOS.
- [x] Add a platform and architecture resolver for x86, x86_64, arm, and arm64 variants.
- [x] Load the native artifact through one internal backend module before exposing any host-facing API.
- [x] Probe ABI version, protocol version, enabled transport slots, and feature flags before accepting the native artifact.
- [x] Reject ABI/protocol mismatches with a deterministic Python exception and actionable diagnostic text.
- [ ] Map connection, session, operation, schema, and buffer handles into Python-owned wrapper types.
- [ ] Define ownership and lifetime rules for native buffers returned to Python.
- [ ] Ensure callbacks or poll results never outlive the native connection/session handle that owns them.
- [ ] Map stable Rust error codes into Python exception hierarchies.
- [ ] Keep pure-Python codec helpers limited to fixture inspection, diagnostics, and explicitly unsupported runtime combinations.
- [x] Add loader and probe tests for every supported platform tag using fake or fixture native artifacts where real artifacts are unavailable.
