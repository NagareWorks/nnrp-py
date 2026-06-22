# 03 - Runtime Object And Cache References

## Runtime Object API

- [x] Add Python object descriptor models.
  - [x] Object ID.
  - [x] Object kind.
  - [x] Producer.
  - [x] Consumer.
  - [x] Lifetime hint.
- [x] Add object declaration API.
- [x] Add object reference API.
- [x] Add object release API.
- [x] Add object delta API.
- [x] Add partial-result object helper API.

## Native Object Bindings

- [x] Bind native object descriptor creation.
- [x] Bind native object descriptor parsing.
- [x] Bind native object release.
- [ ] Bind native object delta descriptor helpers.
- [x] Add native-owned metadata buffer wrapper.
- [x] Add lifetime guard tests for native-owned object metadata.

## Cache References

- [x] Add cache reference model.
  - [x] Cache key.
  - [x] Optional lease ID.
  - [x] Schema/profile anchor.
  - [x] Producer trace.
- [x] Add cache miss model.
- [x] Add cache invalidate model.
- [x] Add cache policy options.
  - [x] Reuse scope.
  - [x] Expiration hint.
  - [x] Invalidation reason.
- [x] Keep cache use explicit per workload.
- [x] Add tests that cache miss remains a typed event, not a generic transport error.

## Copy Boundaries

- [x] Snapshot event payloads when borrow lifetime is not guaranteed.
- [ ] Expose borrowed result views only where native lifetime guards exist.
- [ ] Benchmark copied snapshot path.
- [ ] Benchmark borrowed view path in the native lifetime-guard fixture.
- [x] Document copy behavior for object metadata and partial results.
