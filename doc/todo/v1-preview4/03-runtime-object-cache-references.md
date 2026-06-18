# 03 - Runtime Object And Cache References

## Runtime Object API

- [x] Add Python object descriptor models.
  - [x] Object ID.
  - [x] Object kind.
  - [x] Producer.
  - [x] Consumer.
  - [x] Lifetime hint.
- [ ] Add object declaration API.
- [ ] Add object reference API.
- [ ] Add object release API.
- [ ] Add object delta API.
- [ ] Add partial-result object helper API.

## Native Object Bindings

- [ ] Bind native object descriptor creation.
- [ ] Bind native object descriptor parsing.
- [ ] Bind native object release.
- [ ] Bind native object delta descriptor helpers.
- [ ] Add native-owned metadata buffer wrapper.
- [ ] Add lifetime guard tests for native-owned object metadata.

## Cache References

- [x] Add cache reference model.
  - [x] Cache key.
  - [x] Optional lease ID.
  - [x] Schema/profile anchor.
  - [x] Producer trace.
- [x] Add cache miss model.
- [ ] Add cache invalidate model.
- [ ] Add cache policy options.
  - [x] Reuse scope.
  - [x] Expiration hint.
  - [ ] Invalidation reason.
- [ ] Keep cache use explicit per workload.
- [ ] Add tests that cache miss remains a typed event, not a generic transport error.

## Copy Boundaries

- [ ] Snapshot event payloads when borrow lifetime is not guaranteed.
- [ ] Expose borrowed result views only where native lifetime guards exist.
- [ ] Benchmark copied snapshot path.
- [ ] Benchmark borrowed view path in the native lifetime-guard fixture.
- [ ] Document copy behavior for object metadata and partial results.
