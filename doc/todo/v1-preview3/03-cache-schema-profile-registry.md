# Python Preview3 Cache, Schema, And Profile Registry

## Cache Lease Surface

- [x] Add Python host models for cache lease, object version, expiry, renewal, and dependency invalidation backed by Rust results.
  - [x] Add cache object identity wrapper.
  - [x] Add cache lease descriptor wrapper.
  - [x] Add cache object version wrapper.
  - [x] Add cache expiry/renewal result wrapper.
  - [x] Add dependency invalidation result wrapper.
- [x] Add Python cache query, touch, prefetch, and release helpers without re-implementing cache semantics locally.
  - [x] Add cache query helper that delegates to Rust.
  - [x] Add cache touch helper that delegates to Rust.
  - [x] Add cache prefetch helper that delegates to Rust.
  - [x] Add cache release helper that delegates to Rust.
  - [x] Add tests proving cache miss/lease-expiry diagnostics pass through Rust errors.
- [x] Preserve Rust ownership of lease policy, object dependency validation, and invalidation rules.
  - [x] Keep Python cache helpers from accepting local policy callbacks.
  - [x] Add tests that Python wrappers do not mutate dependency graphs locally.

## Schema And Registry Surface

- [x] Add Python helpers for schema/profile installation, lookup, invalidation, and version mismatch handling.
  - [x] Add schema install helper.
  - [x] Add profile install helper.
  - [x] Add schema/profile lookup helper.
  - [x] Add schema/profile invalidation helper.
  - [x] Add version mismatch diagnostic wrapper.
- [x] Model schema descriptor common headers and typed payload descriptor views against the frozen 32B / 24B layouts plus the first-round standard registry assignments from `nnrp-doc`.
  - [x] Add schema descriptor common-header view.
  - [x] Add typed payload descriptor view.
  - [x] Add registry assignment constants from `nnrp-doc`.
  - [x] Add tests for descriptor size/alignment pass-through.
- [x] Keep schema/profile interpretation Rust-owned; Python should expose stable descriptors and host-friendly wrappers only.
  - [x] Avoid body-specific schema decoding in Python helpers; native descriptor parse/write and binding validation delegate to Rust.
  - [x] Route `SchemaDescriptorHeader.pack/unpack` through native codec when a native runtime is selected.
  - [x] Route `Preview3TypedPayloadDescriptor.pack/unpack` through native codec when a native runtime is selected.
  - [x] Add public helper for native typed payload binding validation.
  - [x] Keep pure-Python descriptor struct packing available for tests and offline fixture inspection.
  - [x] Add tests for native schema mismatch status mapping.
  - [x] Add tests for unknown schema/profile pass-through.

## Standard Profiles And Payload Families

- [x] Treat `tensor` and `token` as peer first-round standard profiles on the public Python surface.
  - [x] Add tensor profile descriptor wrapper.
  - [x] Add token profile descriptor wrapper.
  - [x] Add tests that token profile does not depend on tensor defaults.
- [x] Treat `profile_id = 0` as `unspecified` on the Python public surface rather than an implicit tensor default.
  - [x] Add unspecified profile constant/wrapper.
  - [x] Audit public APIs for implicit tensor default behavior.
    - [x] Audit native session open defaults.
    - [x] Audit typed payload descriptor helper defaults.
    - [x] Audit benchmark scenario defaults.
    - [x] Audit README/API examples.
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
  - [ ] Add callback dispatch tests for structured-event payload family filtering.
  - [ ] Add callback dispatch tests for tool-delta payload family filtering.
  - [ ] Document these as payload families rather than schema/profile aliases.
