import pytest

from nnrp import (
    CacheDependencyInvalidation,
    CacheInvalidationReason,
    CacheLeaseDescriptor,
    CacheLeaseOutcome,
    CacheLeaseResult,
    CacheObjectIdentity,
    CacheObjectKind,
    CacheObjectVersion,
    CacheRuntimeBackend,
    NativeProtocolError,
    NativeStatus,
    SchemaDescriptorHeader,
    SchemaRegistryAction,
    SchemaRegistryCatalog,
    SchemaRegistryFailure,
    StandardProfile,
    StreamSemantics,
    cache_prefetch,
    cache_query,
    cache_release,
    cache_touch,
    token_delta_schema_descriptor,
)


class FakeCacheBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.fail_query: BaseException | None = None

    def query_cache(self, identity: CacheObjectIdentity) -> CacheLeaseResult:
        self.calls.append(("query", identity))
        if self.fail_query is not None:
            raise self.fail_query
        return CacheLeaseResult(identity=identity, outcome=CacheLeaseOutcome.VALID)

    def touch_cache(self, identity: CacheObjectIdentity, *, ttl_ms: int | None = None) -> CacheLeaseResult:
        self.calls.append(("touch", (identity, ttl_ms)))
        lease = CacheLeaseDescriptor(
            identity=identity,
            owner_session_id=1,
            lease_epoch=2,
            expires_at_ms=100,
            ttl_ms=ttl_ms or 0,
        )
        return CacheLeaseResult(identity=identity, outcome=CacheLeaseOutcome.RENEWED, lease=lease)

    def prefetch_cache(self, identities: tuple[CacheObjectIdentity, ...]) -> tuple[CacheLeaseResult, ...]:
        self.calls.append(("prefetch", identities))
        return tuple(CacheLeaseResult(identity=identity, outcome=CacheLeaseOutcome.VALID) for identity in identities)

    def release_cache(self, identity: CacheObjectIdentity) -> CacheLeaseResult:
        self.calls.append(("release", identity))
        return CacheLeaseResult(identity=identity, outcome=CacheLeaseOutcome.RELEASED)


def test_cache_identity_and_version_wrappers_are_stable_value_objects() -> None:
    identity = CacheObjectIdentity(
        namespace=7,
        object_kind=CacheObjectKind.PROMPT_SEGMENT,
        key_hi=0x11223344,
        key_lo=0x55667788,
    )
    version = CacheObjectVersion(identity=identity, object_version=3, schema_id=0x1001, schema_version=3)

    assert identity.key == (0x11223344, 0x55667788)
    assert identity.cache_key_u64 == 0x1122334455667788
    assert version.matches_schema(schema_id=0x1001, schema_version=3) is True
    assert version.matches_schema(schema_id=0x1001, schema_version=4) is False


def test_cache_lease_result_models_expiry_renewal_and_identity_matching() -> None:
    identity = CacheObjectIdentity(namespace=1, object_kind=CacheObjectKind.TOOL_SCHEMA, key_hi=1, key_lo=2)
    lease = CacheLeaseDescriptor(identity=identity, owner_session_id=9, lease_epoch=1, expires_at_ms=1000, ttl_ms=250)
    renewed = lease.as_renewed(lease_epoch=2, expires_at_ms=1250)
    result = CacheLeaseResult(identity=identity, outcome=CacheLeaseOutcome.RENEWED, lease=renewed)

    assert lease.is_expired(999) is False
    assert lease.is_expired(1000) is True
    assert renewed.lease_epoch == 2
    assert renewed.ttl_ms == lease.ttl_ms
    assert result.succeeded is True

    other = CacheObjectIdentity(namespace=2, object_kind=CacheObjectKind.TOOL_SCHEMA, key_hi=1, key_lo=2)
    with pytest.raises(ValueError, match="lease identity"):
        CacheLeaseResult(identity=other, outcome=CacheLeaseOutcome.VALID, lease=lease)


def test_cache_dependency_invalidation_freezes_affected_snapshot_without_policy_callbacks() -> None:
    source = CacheObjectIdentity(namespace=1, object_kind=CacheObjectKind.STRUCTURED_EVENT_SCHEMA, key_hi=1, key_lo=1)
    affected = [
        CacheObjectIdentity(namespace=1, object_kind=CacheObjectKind.PROMPT_SEGMENT, key_hi=2, key_lo=2),
        CacheObjectIdentity(namespace=1, object_kind=CacheObjectKind.TOOL_SCHEMA, key_hi=3, key_lo=3),
    ]

    invalidation = CacheDependencyInvalidation(
        source=source,
        affected=affected,
        reason=CacheInvalidationReason.DEPENDENCY_INVALIDATED,
        schema_id=0x1001,
        schema_version=3,
    )
    affected.append(CacheObjectIdentity(namespace=9, object_kind=CacheObjectKind.CODEC_TABLE, key_hi=4, key_lo=4))

    assert invalidation.affected_count == 2
    assert invalidation.affected[0].object_kind == CacheObjectKind.PROMPT_SEGMENT
    assert invalidation.reason is CacheInvalidationReason.DEPENDENCY_INVALIDATED


def test_cache_runtime_helpers_delegate_without_local_policy() -> None:
    identity = CacheObjectIdentity(namespace=1, object_kind=CacheObjectKind.PROMPT_SEGMENT, key_hi=1, key_lo=2)
    other = CacheObjectIdentity(namespace=1, object_kind=CacheObjectKind.TOOL_SCHEMA, key_hi=3, key_lo=4)
    backend: CacheRuntimeBackend = FakeCacheBackend()

    assert cache_query(backend, identity).outcome is CacheLeaseOutcome.VALID
    assert cache_touch(backend, identity, ttl_ms=250).lease is not None
    assert [result.identity for result in cache_prefetch(backend, [identity, other])] == [identity, other]
    assert cache_release(backend, identity).outcome is CacheLeaseOutcome.RELEASED

    assert backend.calls == [
        ("query", identity),
        ("touch", (identity, 250)),
        ("prefetch", (identity, other)),
        ("release", identity),
    ]


def test_cache_runtime_helpers_preserve_native_cache_diagnostics() -> None:
    identity = CacheObjectIdentity(namespace=1, object_kind=CacheObjectKind.PROMPT_SEGMENT, key_hi=1, key_lo=2)
    backend = FakeCacheBackend()
    backend.fail_query = NativeProtocolError(
        NativeStatus(status_code=4, error_family=2, protocol_error_code=0x2001, detail_code=0),
        "cache miss from native runtime",
    )

    with pytest.raises(NativeProtocolError) as error:
        cache_query(backend, identity)

    assert error.value.status.error_family_name == "cache"
    assert error.value.status.protocol_error_code == 0x2001


def test_schema_registry_catalog_installs_looks_up_invalidates_and_reports_version_mismatch() -> None:
    token = token_delta_schema_descriptor()
    newer = SchemaDescriptorHeader(
        schema_id=token.schema_id,
        schema_version=4,
        profile_id=StandardProfile.TOKEN,
        default_stream_semantics=StreamSemantics.APPEND,
        schema_hash=token.schema_hash + 1,
    )
    catalog = SchemaRegistryCatalog()

    assert catalog.install(token) is SchemaRegistryAction.INSTALLED
    assert catalog.install(token) is SchemaRegistryAction.ALREADY_INSTALLED
    assert catalog.install_profile(newer) is SchemaRegistryAction.UPDATED
    assert catalog.lookup(token.schema_id, token.schema_version) == token
    assert catalog.lookup_profile(StandardProfile.TOKEN) == (token, newer)

    mismatch = catalog.version_mismatch(
        schema_id=token.schema_id,
        requested_schema_version=99,
        profile_id=token.profile_id,
    )
    assert mismatch is not None
    assert mismatch.available_schema_version == 4
    assert mismatch.failure is SchemaRegistryFailure.VERSION_UNKNOWN

    assert catalog.invalidate(token.schema_id, token.schema_version) is SchemaRegistryAction.INVALIDATED
    assert catalog.lookup(token.schema_id, token.schema_version) is None


def test_schema_registry_catalog_preserves_unknown_profile_ids_without_body_decoding() -> None:
    descriptor = SchemaDescriptorHeader(
        schema_id=0x2200,
        schema_version=1,
        profile_id=0x7FFF,
        default_stream_semantics=7,
        schema_hash=0xAABBCCDD,
    )
    catalog = SchemaRegistryCatalog((descriptor,))

    assert catalog.lookup(0x2200, 1) == descriptor
    assert catalog.lookup_profile(0x7FFF) == (descriptor,)
    assert catalog.descriptors() == (descriptor,)


def test_schema_registry_catalog_rejects_hash_conflicts_for_exact_versions() -> None:
    first = SchemaDescriptorHeader(schema_id=9, schema_version=1, profile_id=StandardProfile.TENSOR, schema_hash=1)
    conflict = SchemaDescriptorHeader(schema_id=9, schema_version=1, profile_id=StandardProfile.TENSOR, schema_hash=2)
    catalog = SchemaRegistryCatalog((first,))

    with pytest.raises(ValueError, match="hash conflict"):
        catalog.install(conflict)
