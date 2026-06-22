"""Preview3 cache host models.

These types are Python value wrappers for Rust-backed cache results and
diagnostics. They intentionally avoid local lease policy callbacks or dependency
graph mutation; cache ownership and validation stay in the native runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from nnrp.core import CacheObjectKind
from nnrp.runtime.types import CacheReuseScope


class CacheLeaseOutcome(StrEnum):
    VALID = "valid"
    EXPIRED = "expired"
    RENEWED = "renewed"
    RELEASED = "released"
    MISSING = "missing"


class CacheInvalidationReason(StrEnum):
    EXPLICIT = "explicit"
    DEPENDENCY_INVALIDATED = "dependency_invalidated"
    LEASE_EXPIRED = "lease_expired"
    VERSION_MISMATCH = "version_mismatch"
    SCHEMA_MISMATCH = "schema_mismatch"


@dataclass(frozen=True, slots=True)
class CacheObjectIdentity:
    namespace: int
    object_kind: int | CacheObjectKind
    key_hi: int
    key_lo: int

    def __post_init__(self) -> None:
        _validate_u32("namespace", self.namespace)
        _validate_u16("object_kind", int(self.object_kind))
        _validate_u32("key_hi", self.key_hi)
        _validate_u32("key_lo", self.key_lo)

    @property
    def key(self) -> tuple[int, int]:
        return (self.key_hi, self.key_lo)

    @property
    def cache_key_u64(self) -> int:
        return (self.key_hi << 32) | self.key_lo


@dataclass(frozen=True, slots=True)
class CacheLeaseDescriptor:
    identity: CacheObjectIdentity
    owner_session_id: int
    lease_epoch: int
    expires_at_ms: int
    ttl_ms: int = 0

    def __post_init__(self) -> None:
        _validate_u32("owner_session_id", self.owner_session_id)
        _validate_u64("lease_epoch", self.lease_epoch)
        _validate_u64("expires_at_ms", self.expires_at_ms)
        _validate_u32("ttl_ms", self.ttl_ms)

    def is_expired(self, now_ms: int) -> bool:
        _validate_u64("now_ms", now_ms)
        return now_ms >= self.expires_at_ms

    def as_renewed(self, *, lease_epoch: int, expires_at_ms: int, ttl_ms: int | None = None) -> CacheLeaseDescriptor:
        return CacheLeaseDescriptor(
            identity=self.identity,
            owner_session_id=self.owner_session_id,
            lease_epoch=lease_epoch,
            expires_at_ms=expires_at_ms,
            ttl_ms=self.ttl_ms if ttl_ms is None else ttl_ms,
        )


@dataclass(frozen=True, slots=True)
class CacheObjectVersion:
    identity: CacheObjectIdentity
    object_version: int
    schema_id: int = 0
    schema_version: int = 0

    def __post_init__(self) -> None:
        _validate_u64("object_version", self.object_version)
        _validate_u32("schema_id", self.schema_id)
        _validate_u32("schema_version", self.schema_version)

    def matches_schema(self, *, schema_id: int, schema_version: int) -> bool:
        _validate_u32("schema_id", schema_id)
        _validate_u32("schema_version", schema_version)
        return self.schema_id == schema_id and self.schema_version == schema_version


@dataclass(frozen=True, slots=True)
class CacheLeaseResult:
    identity: CacheObjectIdentity
    outcome: CacheLeaseOutcome | str
    lease: CacheLeaseDescriptor | None = None
    object_version: CacheObjectVersion | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        CacheLeaseOutcome(self.outcome)
        if self.lease is not None and self.lease.identity != self.identity:
            raise ValueError("lease identity must match result identity")
        if self.object_version is not None and self.object_version.identity != self.identity:
            raise ValueError("object_version identity must match result identity")

    @property
    def succeeded(self) -> bool:
        return CacheLeaseOutcome(self.outcome) in {CacheLeaseOutcome.VALID, CacheLeaseOutcome.RENEWED}


@dataclass(frozen=True, slots=True)
class CacheDependencyInvalidation:
    source: CacheObjectIdentity
    affected: tuple[CacheObjectIdentity, ...]
    reason: CacheInvalidationReason | str = CacheInvalidationReason.DEPENDENCY_INVALIDATED
    schema_id: int = 0
    schema_version: int = 0

    def __post_init__(self) -> None:
        CacheInvalidationReason(self.reason)
        _validate_u32("schema_id", self.schema_id)
        _validate_u32("schema_version", self.schema_version)
        object.__setattr__(self, "affected", tuple(self.affected))

    @property
    def affected_count(self) -> int:
        return len(self.affected)


@dataclass(frozen=True, slots=True)
class CacheInvalidation:
    identity: CacheObjectIdentity
    reason: CacheInvalidationReason | str = CacheInvalidationReason.EXPLICIT
    object_version: int = 0
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        CacheInvalidationReason(self.reason)
        _validate_u64("object_version", self.object_version)


@dataclass(frozen=True, slots=True)
class CachePolicyOptions:
    enabled: bool = False
    reuse_scope: CacheReuseScope | int | None = None
    expiration_hint_ms: int = 0
    invalidation_reason: CacheInvalidationReason | str = CacheInvalidationReason.EXPLICIT

    def __post_init__(self) -> None:
        if self.reuse_scope is not None:
            CacheReuseScope(self.reuse_scope)
        CacheInvalidationReason(self.invalidation_reason)
        _validate_u64("expiration_hint_ms", self.expiration_hint_ms)
        if self.enabled and self.reuse_scope is None:
            raise ValueError("enabled cache policy requires reuse_scope")
        if not self.enabled and self.reuse_scope is not None:
            raise ValueError("disabled cache policy must not set reuse_scope")
        if not self.enabled and self.expiration_hint_ms != 0:
            raise ValueError("disabled cache policy must not set expiration_hint_ms")


class CacheRuntimeBackend(Protocol):
    def query_cache(self, identity: CacheObjectIdentity) -> CacheLeaseResult:
        """Return the native/runtime cache state for one object."""

    def touch_cache(self, identity: CacheObjectIdentity, *, ttl_ms: int | None = None) -> CacheLeaseResult:
        """Renew or validate a cache lease through the native/runtime backend."""

    def prefetch_cache(self, identities: tuple[CacheObjectIdentity, ...]) -> tuple[CacheLeaseResult, ...]:
        """Ask the native/runtime backend to prefetch cache objects."""

    def release_cache(self, identity: CacheObjectIdentity) -> CacheLeaseResult:
        """Release a cache lease through the native/runtime backend."""


def cache_query(backend: CacheRuntimeBackend, identity: CacheObjectIdentity) -> CacheLeaseResult:
    return backend.query_cache(identity)


def cache_touch(
    backend: CacheRuntimeBackend,
    identity: CacheObjectIdentity,
    *,
    ttl_ms: int | None = None,
) -> CacheLeaseResult:
    if ttl_ms is not None:
        _validate_u32("ttl_ms", ttl_ms)
    return backend.touch_cache(identity, ttl_ms=ttl_ms)


def cache_prefetch(
    backend: CacheRuntimeBackend,
    identities: tuple[CacheObjectIdentity, ...] | list[CacheObjectIdentity],
) -> tuple[CacheLeaseResult, ...]:
    return backend.prefetch_cache(tuple(identities))


def cache_release(backend: CacheRuntimeBackend, identity: CacheObjectIdentity) -> CacheLeaseResult:
    return backend.release_cache(identity)


def _validate_u16(name: str, value: int) -> None:
    if not isinstance(value, int) or value < 0 or value > 0xFFFF:
        raise ValueError(f"{name} must be a uint16 value")


def _validate_u32(name: str, value: int) -> None:
    if not isinstance(value, int) or value < 0 or value > 0xFFFFFFFF:
        raise ValueError(f"{name} must be a uint32 value")


def _validate_u64(name: str, value: int) -> None:
    if not isinstance(value, int) or value < 0 or value > 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f"{name} must be a uint64 value")


__all__ = [
    "CacheDependencyInvalidation",
    "CacheInvalidation",
    "CacheInvalidationReason",
    "CacheLeaseDescriptor",
    "CacheLeaseOutcome",
    "CacheLeaseResult",
    "CacheObjectIdentity",
    "CacheObjectVersion",
    "CachePolicyOptions",
    "CacheRuntimeBackend",
    "cache_prefetch",
    "cache_query",
    "cache_release",
    "cache_touch",
]
