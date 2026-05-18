# Python Preview3 Cache, Schema, And Profile Registry

## Cache Lease Surface

- [ ] Add Python host models for cache lease, object version, expiry, renewal, and dependency invalidation backed by Rust results.
- [ ] Add Python cache query, touch, prefetch, and release helpers without re-implementing cache semantics locally.
- [ ] Preserve Rust ownership of lease policy, object dependency validation, and invalidation rules.

## Schema And Registry Surface

- [ ] Add Python helpers for schema/profile installation, lookup, invalidation, and version mismatch handling.
- [ ] Model schema descriptor common headers and typed payload descriptor views only after 32B / 24B layouts freeze upstream.
- [ ] Keep schema/profile interpretation Rust-owned; Python should expose stable descriptors and host-friendly wrappers only.

## Standard Profiles And Payload Families

- [ ] Treat `tensor` and `token` as peer first-round standard profiles on the public Python surface.
- [ ] Add token-profile wrappers only after token minimum semantics freeze upstream.
- [ ] Surface `structured_event` and `tool_delta` as protocol-visible payload families without hard-coding their bodies into Python fixed metadata models.