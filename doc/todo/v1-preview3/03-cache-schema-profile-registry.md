# Python Preview3 Cache, Schema, And Profile Registry

## Cache Lease Surface

- [ ] Add Python host models for cache lease, object version, expiry, renewal, and dependency invalidation backed by Rust results.
  - [ ] Add cache object identity wrapper.
  - [ ] Add cache lease descriptor wrapper.
  - [ ] Add cache object version wrapper.
  - [ ] Add cache expiry/renewal result wrapper.
  - [ ] Add dependency invalidation result wrapper.
- [ ] Add Python cache query, touch, prefetch, and release helpers without re-implementing cache semantics locally.
  - [ ] Add cache query helper that delegates to Rust.
  - [ ] Add cache touch helper that delegates to Rust.
  - [ ] Add cache prefetch helper that delegates to Rust.
  - [ ] Add cache release helper that delegates to Rust.
  - [ ] Add tests proving cache miss/lease-expiry diagnostics pass through Rust errors.
- [ ] Preserve Rust ownership of lease policy, object dependency validation, and invalidation rules.
  - [ ] Keep Python cache helpers from accepting local policy callbacks.
  - [ ] Add tests that Python wrappers do not mutate dependency graphs locally.

## Schema And Registry Surface

- [ ] Add Python helpers for schema/profile installation, lookup, invalidation, and version mismatch handling.
  - [ ] Add schema install helper.
  - [ ] Add profile install helper.
  - [ ] Add schema/profile lookup helper.
  - [ ] Add schema/profile invalidation helper.
  - [ ] Add version mismatch diagnostic wrapper.
- [x] Model schema descriptor common headers and typed payload descriptor views against the frozen 32B / 24B layouts plus the first-round standard registry assignments from `nnrp-doc`.
  - [x] Add schema descriptor common-header view.
  - [x] Add typed payload descriptor view.
  - [x] Add registry assignment constants from `nnrp-doc`.
  - [x] Add tests for descriptor size/alignment pass-through.
- [ ] Keep schema/profile interpretation Rust-owned; Python should expose stable descriptors and host-friendly wrappers only.
  - [x] Avoid body-specific schema decoding in Python helpers.
  - [ ] Add tests for unknown schema/profile pass-through.

## Standard Profiles And Payload Families

- [x] Treat `tensor` and `token` as peer first-round standard profiles on the public Python surface.
  - [x] Add tensor profile descriptor wrapper.
  - [x] Add token profile descriptor wrapper.
  - [x] Add tests that token profile does not depend on tensor defaults.
- [ ] Treat `profile_id = 0` as `unspecified` on the Python public surface rather than an implicit tensor default.
  - [x] Add unspecified profile constant/wrapper.
  - [ ] Audit public APIs for implicit tensor default behavior.
  - [x] Add tests for profile id 0 pass-through.
- [x] Add token-profile wrappers against the frozen token minimum semantics and first-round registry assignments from `nnrp-doc`.
  - [x] Add token stream profile wrapper.
  - [x] Add token delta/result wrapper.
  - [x] Add tests for minimum token profile fields.
- [ ] Surface `structured_event` and `tool_delta` as protocol-visible payload families without hard-coding their bodies into Python fixed metadata models.
  - [x] Add structured-event payload family wrapper.
  - [x] Add tool-delta payload family wrapper.
  - [ ] Add async delivery tests for structured-event payloads.
  - [ ] Add async delivery tests for tool-delta payloads.
