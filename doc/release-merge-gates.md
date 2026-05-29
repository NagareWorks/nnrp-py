# Release Merge Gates

This repository treats the Python package as a host-facing binding over the canonical Rust-owned NNRP runtime contract. Pull requests that touch protocol behavior, native bindings, hot paths, or release packaging must satisfy the relevant gates below before merge.

## Rust And Protocol Freeze References

Any PR that changes cache, schema, recovery, session, operation, event, or payload-family behavior must include:

1. The pinned `nnrp-rs` artifact version, tag, or commit used for the implementation.
2. The `nnrp-doc` protocol section or registry assignment that defines the behavior.
3. A short statement that Python is only exposing the frozen behavior and is not inventing protocol policy locally.

Reviewer reject conditions:

1. Python-side policy is added without a Rust or protocol reference.
2. A schema/profile/cache/recovery decision is inferred from Python-only code.
3. A PR changes public semantics while the matching Rust ABI or protocol text is still unsettled.

## Native ABI And Artifact Gate

Any PR that changes native entrypoints, handle wrappers, ABI structs, artifact loading, or release wheel contents must include:

1. The expected native ABI major/minor/patch and minimum accepted ABI version.
2. The required native feature flags and transport slots.
3. Evidence that `probe_native_artifact` rejects ABI/protocol mismatches deterministically.
4. Wheel inspection output when package contents change.

Reviewer reject conditions:

1. A native helper is exposed without an ABI probe path.
2. A wheel can be published as universal while native artifacts are required.
3. A native artifact path is accepted without platform-tag validation.
4. The sdist starts carrying prebuilt native libraries without an explicit release-policy change.

## Conformance Gate

Any PR that changes public host APIs, adapter behavior, protocol-visible errors, or native result/event routing must include:

1. SDK-local tests for the changed Python API.
2. Adapter conformance coverage when the behavior is suite-selectable.
3. Evidence that result diagnostics preserve native status, family, detail, and related ids.

Reviewer reject conditions:

1. SDK-owned vector generation is reintroduced as a canonical protocol baseline.
2. Adapter cases are bypassed with placeholder success.
3. Native errors are flattened into strings before reaching reports.

## Coverage And Benchmark Gate

Any PR that changes hot-path submit/result, event polling, payload ownership, schema descriptor routing, or native artifact probing must include:

1. Total coverage at or above the repository gate.
2. Incremental line coverage at or above the repository gate.
3. A targeted benchmark or smoke metric when runtime cost can change.
4. A note when smoke metrics are observational and not yet threshold-gated.

Reviewer reject conditions:

1. A hot-path change lands without targeted tests.
2. A payload ownership change lands without copy-boundary or lifetime tests.
3. Benchmark deltas are omitted for a release-artifact migration PR.

## Release Wheel Gate

Before publishing native wheels, the release PR must include:

1. The pinned `nnrp-rs` artifact version.
2. The platform wheel matrix and embedded artifact tags.
3. Wheel inspection output proving each wheel embeds exactly one matching native artifact.
4. Wheel inspection output proving each native wheel embeds a compiled cffi API fast-path module for its wheel tag.
5. Post-migration benchmark results captured on the release artifact.
6. Confirmation that the sdist remains free of prebuilt native libraries unless release policy changed.
7. Confirmation that GitHub Release assets are uploaded as top-level wheel files, not as a nested archive containing wheels and a duplicate source archive.

Reviewer reject conditions:

1. A platform wheel embeds a mismatched OS or architecture artifact.
2. A native release publishes a `py3-none-any` wheel.
3. Post-migration benchmark rows remain `TBD` for the platform being released.
4. A native wheel contains only the Python cffi shim without a compiled cffi API fast-path extension.
5. GitHub Release assets hide wheel files inside a repository-owned zip instead of attaching the wheels directly.
