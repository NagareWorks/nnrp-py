# Python Preview3 Host API Surface

- [ ] Replace Python-owned preview3 packet/session entry points with Rust-backed orchestration.
  - [x] Add Rust-backed connection/session/submit/result/control facades.
  - [ ] Redirect existing public preview helper call sites to the Rust-backed facades.
    - [ ] Redirect client connection/session bootstrap helpers.
    - [ ] Redirect submit/result helper paths.
    - [ ] Redirect cancellation/control helper paths.
    - [x] Redirect adapter conformance execution from placeholder reports to native-backed smoke case execution.
  - [ ] Remove or quarantine superseded Python-owned hot-path packet/session implementations.
    - [ ] Keep packet codecs only for fixture inspection, diagnostics, and unsupported runtime combinations.
    - [ ] Add explicit fallback selection so native-backed paths are the default when artifacts are present.
- [x] Add host-facing submit/result/control helpers that compose Rust preview3 handles rather than rebuilding packet logic in Python.
- [x] Add a client-facing native session context helper so host code can open Rust-backed sessions without using packet transport helpers.
- [x] Add Python-facing operation identifiers, parent/group relationships, and cancellation scopes on top of Rust lifecycle primitives.
- [x] Preserve the distinction among `partial`, `degraded`, `stale_reuse`, `cancelled`, `failed`, and `completed` lifecycle states on the Python API surface.
