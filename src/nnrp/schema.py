"""Preview3 schema/profile registry descriptors.

This module exposes host-friendly views of the frozen public descriptor fields.
It deliberately does not decode profile-private payload bodies; Rust remains the
owner of schema compatibility and profile interpretation.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag

SCHEMA_DESCRIPTOR_HEADER_LENGTH = 32
TYPED_PAYLOAD_DESCRIPTOR_V3_LENGTH = 24
SCHEMA_FLAGS_KNOWN_MASK = 0x000F
DESCRIPTOR_FLAGS_KNOWN_MASK = 0x000F
TOKEN_DELTA_SCHEMA_ID = 0x0000_1001
TOKEN_DELTA_SCHEMA_VERSION = 3
TOKEN_DELTA_SCHEMA_HASH = 0x6E6E_7270_746F_6B33

_SCHEMA_DESCRIPTOR_HEADER_STRUCT = struct.Struct("<IIHHBBHIHHQ")
_TYPED_PAYLOAD_DESCRIPTOR_STRUCT = struct.Struct("<HHIIHHII")


class StandardProfile(IntEnum):
    UNSPECIFIED = 0x0000
    TENSOR = 0x0001
    TOKEN = 0x0002


class StreamSemantics(IntEnum):
    UNSPECIFIED = 0
    SNAPSHOT = 1
    APPEND = 2


class SchemaDescriptorFlags(IntFlag):
    NONE = 0
    BREAKING_CHANGE = 0x0001
    COMPATIBLE_UPDATE = 0x0002
    DEPENDENCY_BOUND = 0x0004
    BODY_SCHEMA_PRESENT = 0x0008


class TypedPayloadDescriptorFlags(IntFlag):
    NONE = 0
    TERMINAL = 0x0001
    PARTIAL = 0x0002
    SCHEMA_OVERRIDE = 0x0004
    PROFILE_HINT_PRESENT = 0x0008


class SchemaRegistryAction(IntEnum):
    INSTALLED = 1
    ALREADY_INSTALLED = 2
    UPDATED = 3
    INVALIDATED = 4


class SchemaRegistryFailure(IntEnum):
    UNKNOWN = 1
    VERSION_UNKNOWN = 2
    HASH_CONFLICT = 3
    INCOMPATIBLE = 4
    UPDATE_REJECTED = 5


@dataclass(frozen=True, slots=True)
class SchemaDescriptorHeader:
    schema_id: int
    schema_version: int
    profile_id: int | StandardProfile
    schema_flags: int | SchemaDescriptorFlags = SchemaDescriptorFlags.NONE
    min_version_major: int = 1
    max_version_major: int = 1
    body_bytes: int = 0
    dependency_count: int = 0
    default_stream_semantics: int | StreamSemantics = StreamSemantics.UNSPECIFIED
    schema_hash: int = 0

    def __post_init__(self) -> None:
        _validate_u32("schema_id", self.schema_id)
        _validate_u32("schema_version", self.schema_version)
        _validate_u16("profile_id", int(self.profile_id))
        _validate_mask("schema_flags", int(self.schema_flags), SCHEMA_FLAGS_KNOWN_MASK)
        _validate_u8("min_version_major", self.min_version_major)
        _validate_u8("max_version_major", self.max_version_major)
        _validate_u32("body_bytes", self.body_bytes)
        _validate_u16("dependency_count", self.dependency_count)
        _validate_u16("default_stream_semantics", int(self.default_stream_semantics))
        _validate_u64("schema_hash", self.schema_hash)

    @classmethod
    def unpack(cls, payload: bytes | bytearray | memoryview) -> SchemaDescriptorHeader:
        data = bytes(payload)
        if len(data) != SCHEMA_DESCRIPTOR_HEADER_LENGTH:
            raise ValueError(f"expected {SCHEMA_DESCRIPTOR_HEADER_LENGTH} bytes, got {len(data)}")
        (
            schema_id,
            schema_version,
            profile_id,
            schema_flags,
            min_version_major,
            max_version_major,
            reserved0,
            body_bytes,
            dependency_count,
            default_stream_semantics,
            schema_hash,
        ) = _SCHEMA_DESCRIPTOR_HEADER_STRUCT.unpack(data)
        if reserved0 != 0:
            raise ValueError("schema descriptor reserved0 must be 0")
        return cls(
            schema_id=schema_id,
            schema_version=schema_version,
            profile_id=profile_id,
            schema_flags=schema_flags,
            min_version_major=min_version_major,
            max_version_major=max_version_major,
            body_bytes=body_bytes,
            dependency_count=dependency_count,
            default_stream_semantics=default_stream_semantics,
            schema_hash=schema_hash,
        )

    def pack(self) -> bytes:
        return _SCHEMA_DESCRIPTOR_HEADER_STRUCT.pack(
            self.schema_id,
            self.schema_version,
            int(self.profile_id),
            int(self.schema_flags),
            self.min_version_major,
            self.max_version_major,
            0,
            self.body_bytes,
            self.dependency_count,
            int(self.default_stream_semantics),
            self.schema_hash,
        )


@dataclass(frozen=True, slots=True)
class SchemaVersionMismatch:
    requested_schema_id: int
    requested_schema_version: int
    available_schema_version: int | None
    profile_id: int | StandardProfile
    failure: SchemaRegistryFailure = SchemaRegistryFailure.VERSION_UNKNOWN

    def __post_init__(self) -> None:
        _validate_u32("requested_schema_id", self.requested_schema_id)
        _validate_u32("requested_schema_version", self.requested_schema_version)
        if self.available_schema_version is not None:
            _validate_u32("available_schema_version", self.available_schema_version)
        _validate_u16("profile_id", int(self.profile_id))
        SchemaRegistryFailure(self.failure)


class SchemaRegistryCatalog:
    """Host-side descriptor catalog.

    This catalog stores descriptor headers and performs exact key lookup only.
    It does not decode body schemas, resolve compatibility policy, or mutate
    dependency graphs locally.
    """

    def __init__(self, descriptors: tuple[SchemaDescriptorHeader, ...] = ()) -> None:
        self._descriptors: dict[tuple[int, int], SchemaDescriptorHeader] = {}
        for descriptor in descriptors:
            self.install(descriptor)

    def install(self, descriptor: SchemaDescriptorHeader) -> SchemaRegistryAction:
        key = (descriptor.schema_id, descriptor.schema_version)
        existing = self._descriptors.get(key)
        if existing == descriptor:
            return SchemaRegistryAction.ALREADY_INSTALLED
        if existing is not None and existing.schema_hash != descriptor.schema_hash:
            raise ValueError("schema hash conflict for installed schema version")

        has_older_version = any(schema_id == descriptor.schema_id for schema_id, _ in self._descriptors)
        self._descriptors[key] = descriptor
        return SchemaRegistryAction.UPDATED if has_older_version else SchemaRegistryAction.INSTALLED

    def install_profile(self, descriptor: SchemaDescriptorHeader) -> SchemaRegistryAction:
        return self.install(descriptor)

    def lookup(self, schema_id: int, schema_version: int) -> SchemaDescriptorHeader | None:
        _validate_u32("schema_id", schema_id)
        _validate_u32("schema_version", schema_version)
        return self._descriptors.get((schema_id, schema_version))

    def lookup_profile(self, profile_id: int | StandardProfile) -> tuple[SchemaDescriptorHeader, ...]:
        _validate_u16("profile_id", int(profile_id))
        return tuple(descriptor for descriptor in self._descriptors.values() if descriptor.profile_id == profile_id)

    def invalidate(self, schema_id: int, schema_version: int) -> SchemaRegistryAction:
        _validate_u32("schema_id", schema_id)
        _validate_u32("schema_version", schema_version)
        self._descriptors.pop((schema_id, schema_version), None)
        return SchemaRegistryAction.INVALIDATED

    def version_mismatch(
        self,
        *,
        schema_id: int,
        requested_schema_version: int,
        profile_id: int | StandardProfile,
    ) -> SchemaVersionMismatch | None:
        _validate_u32("schema_id", schema_id)
        _validate_u32("requested_schema_version", requested_schema_version)
        _validate_u16("profile_id", int(profile_id))
        if self.lookup(schema_id, requested_schema_version) is not None:
            return None
        available_versions = [
            version for (registered_schema_id, version) in self._descriptors if registered_schema_id == schema_id
        ]
        return SchemaVersionMismatch(
            requested_schema_id=schema_id,
            requested_schema_version=requested_schema_version,
            available_schema_version=max(available_versions) if available_versions else None,
            profile_id=profile_id,
        )

    def descriptors(self) -> tuple[SchemaDescriptorHeader, ...]:
        return tuple(self._descriptors.values())


@dataclass(frozen=True, slots=True)
class Preview3TypedPayloadDescriptor:
    profile_id: int | StandardProfile
    descriptor_flags: int | TypedPayloadDescriptorFlags
    schema_id: int
    schema_version: int
    stream_semantics: int | StreamSemantics
    offset: int
    length: int

    def __post_init__(self) -> None:
        _validate_u16("profile_id", int(self.profile_id))
        _validate_mask("descriptor_flags", int(self.descriptor_flags), DESCRIPTOR_FLAGS_KNOWN_MASK)
        _validate_u32("schema_id", self.schema_id)
        _validate_u32("schema_version", self.schema_version)
        _validate_u16("stream_semantics", int(self.stream_semantics))
        _validate_u32("offset", self.offset)
        _validate_u32("length", self.length)

    @property
    def is_terminal(self) -> bool:
        return bool(int(self.descriptor_flags) & int(TypedPayloadDescriptorFlags.TERMINAL))

    @property
    def is_partial(self) -> bool:
        return bool(int(self.descriptor_flags) & int(TypedPayloadDescriptorFlags.PARTIAL))

    @classmethod
    def unpack(cls, payload: bytes | bytearray | memoryview) -> Preview3TypedPayloadDescriptor:
        data = bytes(payload)
        if len(data) != TYPED_PAYLOAD_DESCRIPTOR_V3_LENGTH:
            raise ValueError(f"expected {TYPED_PAYLOAD_DESCRIPTOR_V3_LENGTH} bytes, got {len(data)}")
        (
            profile_id,
            descriptor_flags,
            schema_id,
            schema_version,
            stream_semantics,
            reserved0,
            offset,
            length,
        ) = _TYPED_PAYLOAD_DESCRIPTOR_STRUCT.unpack(data)
        if reserved0 != 0:
            raise ValueError("typed payload descriptor reserved0 must be 0")
        return cls(
            profile_id=profile_id,
            descriptor_flags=descriptor_flags,
            schema_id=schema_id,
            schema_version=schema_version,
            stream_semantics=stream_semantics,
            offset=offset,
            length=length,
        )

    def pack(self) -> bytes:
        return _TYPED_PAYLOAD_DESCRIPTOR_STRUCT.pack(
            int(self.profile_id),
            int(self.descriptor_flags),
            self.schema_id,
            self.schema_version,
            int(self.stream_semantics),
            0,
            self.offset,
            self.length,
        )


def token_delta_schema_descriptor() -> SchemaDescriptorHeader:
    return SchemaDescriptorHeader(
        schema_id=TOKEN_DELTA_SCHEMA_ID,
        schema_version=TOKEN_DELTA_SCHEMA_VERSION,
        profile_id=StandardProfile.TOKEN,
        default_stream_semantics=StreamSemantics.APPEND,
        schema_hash=TOKEN_DELTA_SCHEMA_HASH,
    )


def token_delta_payload_descriptor(
    *,
    offset: int,
    length: int,
    terminal: bool = False,
    partial: bool = True,
) -> Preview3TypedPayloadDescriptor:
    flags = TypedPayloadDescriptorFlags.NONE
    if terminal:
        flags |= TypedPayloadDescriptorFlags.TERMINAL
    if partial:
        flags |= TypedPayloadDescriptorFlags.PARTIAL
    return Preview3TypedPayloadDescriptor(
        profile_id=StandardProfile.TOKEN,
        descriptor_flags=flags,
        schema_id=TOKEN_DELTA_SCHEMA_ID,
        schema_version=TOKEN_DELTA_SCHEMA_VERSION,
        stream_semantics=StreamSemantics.APPEND,
        offset=offset,
        length=length,
    )


def tensor_payload_descriptor(
    *,
    schema_id: int = 0,
    schema_version: int = 0,
    stream_semantics: int | StreamSemantics = StreamSemantics.UNSPECIFIED,
    offset: int,
    length: int,
    descriptor_flags: int | TypedPayloadDescriptorFlags = TypedPayloadDescriptorFlags.NONE,
) -> Preview3TypedPayloadDescriptor:
    return Preview3TypedPayloadDescriptor(
        profile_id=StandardProfile.TENSOR,
        descriptor_flags=descriptor_flags,
        schema_id=schema_id,
        schema_version=schema_version,
        stream_semantics=stream_semantics,
        offset=offset,
        length=length,
    )


def unspecified_payload_descriptor(
    *,
    offset: int,
    length: int,
    schema_id: int = 0,
    schema_version: int = 0,
) -> Preview3TypedPayloadDescriptor:
    return Preview3TypedPayloadDescriptor(
        profile_id=StandardProfile.UNSPECIFIED,
        descriptor_flags=TypedPayloadDescriptorFlags.NONE,
        schema_id=schema_id,
        schema_version=schema_version,
        stream_semantics=StreamSemantics.UNSPECIFIED,
        offset=offset,
        length=length,
    )


def _validate_mask(name: str, value: int, known_mask: int) -> None:
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    unknown_bits = value & ~known_mask
    if unknown_bits:
        raise ValueError(f"{name} contains unknown bits: 0x{unknown_bits:04x}")


def _validate_u8(name: str, value: int) -> None:
    if not isinstance(value, int) or value < 0 or value > 0xFF:
        raise ValueError(f"{name} must be a uint8 value")


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
    "DESCRIPTOR_FLAGS_KNOWN_MASK",
    "SCHEMA_DESCRIPTOR_HEADER_LENGTH",
    "SCHEMA_FLAGS_KNOWN_MASK",
    "TOKEN_DELTA_SCHEMA_HASH",
    "TOKEN_DELTA_SCHEMA_ID",
    "TOKEN_DELTA_SCHEMA_VERSION",
    "TYPED_PAYLOAD_DESCRIPTOR_V3_LENGTH",
    "Preview3TypedPayloadDescriptor",
    "SchemaDescriptorFlags",
    "SchemaDescriptorHeader",
    "SchemaRegistryAction",
    "SchemaRegistryCatalog",
    "SchemaRegistryFailure",
    "SchemaVersionMismatch",
    "StandardProfile",
    "StreamSemantics",
    "TypedPayloadDescriptorFlags",
    "tensor_payload_descriptor",
    "token_delta_payload_descriptor",
    "token_delta_schema_descriptor",
    "unspecified_payload_descriptor",
]
