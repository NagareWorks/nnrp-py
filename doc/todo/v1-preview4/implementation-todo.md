# Python Preview4 Implementation Todo

Preview4 Python work adapts the SDK to runtime control frames, runtime objects, transport providers, and wire-level conformance while keeping Rust as the canonical protocol and transport implementation source.

## Workstreams

- [x] [01 - Contract and version adoption](01-contract-and-version-adoption.md)
- [x] [02 - Runtime control API](02-runtime-control-api.md)
- [x] [03 - Runtime object and cache references](03-runtime-object-cache-references.md)
- [x] [04 - Transport provider bindings](04-transport-provider-bindings.md)
- [x] [05 - Wire conformance integration](05-wire-conformance-integration.md)
- [ ] [06 - Packaging, benchmarks, and docs](06-packaging-benchmarks-docs.md)

## Coordination Rules

- [x] Keep Python public APIs thin over Rust-owned runtime behavior.
- [x] Keep transport providers meaningful; do not use transport packages as configuration-only switches.
- [x] Keep cffi API hot paths coarse and benchmarked.
- [x] Keep pure-Python protocol helpers limited to fixtures, diagnostics, and explicit unsupported-runtime paths.
- [x] Update this index as workstreams split or complete.
