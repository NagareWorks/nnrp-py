"""Fixed-width control-plane metadata models for the current NNRP/1 wire format."""

from __future__ import annotations

import struct
from collections.abc import Iterable, Set
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import ClassVar, TypeVar


class ErrorScope(IntEnum):
    CONNECTION = 0
    SESSION = 1
    FRAME = 2


class CacheObjectKind(IntEnum):
    CAMERA_BLOCK = 0x0001
    TILE_INDEX_BLOCK = 0x0002
    TENSOR_SECTION_TABLE = 0x0003
    CODEC_TABLE = 0x0004
    REUSABLE_RESULT_OBJECT = 0x0005
    PAYLOAD_LAYOUT_TEMPLATE = 0x0006
    PROMPT_SEGMENT = 0x0007
    TOOL_SCHEMA = 0x0008
    STRUCTURED_EVENT_SCHEMA = 0x0009

    TILE_INDEX_TEMPLATE = TILE_INDEX_BLOCK
    CODEC_AUX_BLOCK = CODEC_TABLE
    FALLBACK_RESOURCE = REUSABLE_RESULT_OBJECT


def build_cache_object_bitmap(*object_kinds: CacheObjectKind | int) -> int:
    bitmap = 0
    for object_kind in object_kinds:
        normalized_kind = CacheObjectKind(object_kind)
        bitmap |= 1 << (int(normalized_kind) - 1)
    return bitmap


TENSOR_PROFILE_CACHE_OBJECT_BITMAP = build_cache_object_bitmap(
    CacheObjectKind.CAMERA_BLOCK,
    CacheObjectKind.TILE_INDEX_BLOCK,
    CacheObjectKind.TENSOR_SECTION_TABLE,
)


class CacheAckStatus(IntEnum):
    ACCEPTED = 0
    REJECTED = 1
    REPLACED = 2


class CacheInvalidateScope(IntEnum):
    WHOLE_SESSION = 0
    NAMESPACE = 1
    OBJECT_KIND = 2
    OBJECT_KEY = 3

    ENTRY = OBJECT_KEY
    SESSION = WHOLE_SESSION


class CachePutFlags(IntFlag):
    NONE = 0
    PINNED = 0x00000001
    REUSABLE = 0x00000002


class SessionPatchField(IntFlag):
    NONE = 0
    TARGET_CADENCE = 0x00000001
    QUALITY_TIER = 0x00000002
    DEGRADE_POLICY = 0x00000004
    ACTIVE_LANE_MASK = 0x00000008
    PREFERRED_CODEC = 0x00000010
    PREFERRED_COMPRESSION = 0x00000020
    PROFILE_PATCH = 0x00000040

    TARGET_FPS = TARGET_CADENCE
    ACTIVE_VIEW_MASK = ACTIVE_LANE_MASK


class SessionPatchAckStatus(IntEnum):
    ACCEPTED = 0
    PARTIALLY_APPLIED = 1
    REJECTED = 2


class SessionPatchRejectReason(IntEnum):
    NONE = 0
    UNSUPPORTED_FIELD = 1
    INVALID_RANGE = 2
    UNSUPPORTED_STRATEGY = 3
    INVALID_LANE_MASK = 4
    RATE_LIMITED = 5

    INVALID_VIEW_MASK = INVALID_LANE_MASK


class FlowUpdateScopeKind(IntEnum):
    CONNECTION = 0
    SESSION = 1
    OPERATION = 2


class FlowUpdateReason(IntEnum):
    GRANT = 0
    REDUCE = 1
    PAUSE = 2
    RESUME = 3
    CONGESTION = 4


class FlowUpdateBackpressureLevel(IntEnum):
    NONE = 0
    SOFT = 1
    HARD = 2


class FlowUpdateFlags(IntFlag):
    NONE = 0
    CREDIT_VALID = 0x00000001
    RETRY_AFTER_VALID = 0x00000002
    BACKGROUND_ONLY = 0x00000004
    DRAIN_IN_FLIGHT_ONLY = 0x00000008


class ResultHintBudgetPolicy(IntEnum):
    NONE = 0
    FULL = 1
    PARTIAL = 2
    STALE_REUSE = 3
    DROP = 4


class ResultHintCongestionState(IntEnum):
    NONE = 0
    STEADY = 1
    ELEVATED = 2
    SATURATED = 3


class ResultHintReason(IntEnum):
    NONE = 0
    QUEUE_FULL = 1
    SERVER_BUSY = 2
    BUDGET_EXCEEDED = 3
    SUPERSEDED = 4


MessageMetadataT = TypeVar("MessageMetadataT", bound="_FixedWidthMetadata")


class _FixedWidthMetadata:
    STRUCT: ClassVar[struct.Struct]

    def pack(self) -> bytes:
        raise NotImplementedError

    @classmethod
    def unpack(cls, payload: bytes) -> MessageMetadataT:
        if len(payload) != cls.STRUCT.size:
            raise ValueError(f"expected {cls.STRUCT.size} bytes, got {len(payload)}")
        return cls._from_tuple(cls.STRUCT.unpack(payload))

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> MessageMetadataT:
        raise NotImplementedError


FLOW_UPDATE_STRUCT = struct.Struct("<BBBBHHHHQIII")
RESULT_HINT_STRUCT = struct.Struct("<IIII")
TRANSPORT_PROBE_STRUCT = struct.Struct("<IIQ")
TRANSPORT_PROBE_ACK_STRUCT = struct.Struct("<IIQ")
SESSION_MIGRATE_STRUCT = struct.Struct("<IIQQ")
SESSION_MIGRATE_ACK_STRUCT = struct.Struct("<IQIQ")

_FLOW_UPDATE_ALLOWED_FLAGS = (
    FlowUpdateFlags.CREDIT_VALID
    | FlowUpdateFlags.RETRY_AFTER_VALID
    | FlowUpdateFlags.BACKGROUND_ONLY
    | FlowUpdateFlags.DRAIN_IN_FLIGHT_ONLY
)


@dataclass(slots=True)
class FlowUpdateMetadata(_FixedWidthMetadata):
    """Fixed-width metadata for FLOW_UPDATE."""

    STRUCT: ClassVar[struct.Struct] = FLOW_UPDATE_STRUCT

    scope_kind: FlowUpdateScopeKind = FlowUpdateScopeKind.SESSION
    update_reason: FlowUpdateReason = FlowUpdateReason.GRANT
    backpressure_level: FlowUpdateBackpressureLevel = FlowUpdateBackpressureLevel.NONE
    connection_credit: int = 0
    session_credit: int = 0
    operation_credit: int = 0
    operation_id: int = 0
    retry_after_ms: int = 0
    credit_epoch: int = 0
    flags: FlowUpdateFlags = FlowUpdateFlags.NONE

    def _validate(self) -> None:
        if self.connection_credit < 0 or self.connection_credit > 0xFFFF:
            raise ValueError("connection_credit must fit in u16")
        if self.session_credit < 0 or self.session_credit > 0xFFFF:
            raise ValueError("session_credit must fit in u16")
        if self.operation_credit < 0 or self.operation_credit > 0xFFFF:
            raise ValueError("operation_credit must fit in u16")
        if self.operation_id < 0 or self.operation_id > 0xFFFFFFFFFFFFFFFF:
            raise ValueError("operation_id must fit in u64")
        if self.retry_after_ms < 0 or self.retry_after_ms > 0xFFFFFFFF:
            raise ValueError("retry_after_ms must fit in u32")
        if self.credit_epoch < 0 or self.credit_epoch > 0xFFFFFFFF:
            raise ValueError("credit_epoch must fit in u32")
        unknown_flags = int(self.flags) & ~int(_FLOW_UPDATE_ALLOWED_FLAGS)
        if unknown_flags:
            raise ValueError(f"unknown FLOW_UPDATE flags: 0x{int(self.flags):08x}")
        if self.retry_after_ms and not (self.flags & FlowUpdateFlags.RETRY_AFTER_VALID):
            raise ValueError("retry_after_ms requires RETRY_AFTER_VALID")
        if self.scope_kind is FlowUpdateScopeKind.CONNECTION:
            if self.session_credit != 0 or self.operation_credit != 0 or self.operation_id != 0:
                raise ValueError(
                    "connection-scope FLOW_UPDATE must not carry session_credit, operation_credit, or operation_id"
                )
        elif self.scope_kind is FlowUpdateScopeKind.SESSION:
            if self.connection_credit != 0 or self.operation_credit != 0 or self.operation_id != 0:
                raise ValueError(
                    "session-scope FLOW_UPDATE must not carry connection_credit, operation_credit, or operation_id"
                )
        elif self.scope_kind is FlowUpdateScopeKind.OPERATION:
            if self.connection_credit != 0 or self.session_credit != 0:
                raise ValueError("operation-scope FLOW_UPDATE must not carry connection_credit or session_credit")
            if self.operation_id == 0:
                raise ValueError("operation-scope FLOW_UPDATE requires a non-zero operation_id")

    def pack(self) -> bytes:
        self._validate()
        return self.STRUCT.pack(
            int(self.scope_kind),
            int(self.update_reason),
            int(self.backpressure_level),
            0,
            self.connection_credit,
            self.session_credit,
            self.operation_credit,
            0,
            self.operation_id,
            self.retry_after_ms,
            self.credit_epoch,
            int(self.flags),
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> FlowUpdateMetadata:
        metadata = cls(
            scope_kind=FlowUpdateScopeKind(values[0]),
            update_reason=FlowUpdateReason(values[1]),
            backpressure_level=FlowUpdateBackpressureLevel(values[2]),
            connection_credit=values[4],
            session_credit=values[5],
            operation_credit=values[6],
            operation_id=values[8],
            retry_after_ms=values[9],
            credit_epoch=values[10],
            flags=FlowUpdateFlags(values[11]),
        )
        metadata._validate()
        return metadata


@dataclass(slots=True)
class ResultHintMetadata(_FixedWidthMetadata):
    """Fixed-width metadata for RESULT_HINT."""

    STRUCT: ClassVar[struct.Struct] = RESULT_HINT_STRUCT

    applied_budget_policy: ResultHintBudgetPolicy = ResultHintBudgetPolicy.NONE
    congestion_state: ResultHintCongestionState = ResultHintCongestionState.NONE
    reason: ResultHintReason = ResultHintReason.NONE
    retry_after_ms: int = 0

    def _validate(self) -> None:
        if self.retry_after_ms < 0:
            raise ValueError("retry_after_ms must be non-negative")

    def pack(self) -> bytes:
        self._validate()
        return self.STRUCT.pack(
            int(self.applied_budget_policy),
            int(self.congestion_state),
            int(self.reason),
            self.retry_after_ms,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> ResultHintMetadata:
        metadata = cls(
            applied_budget_policy=ResultHintBudgetPolicy(values[0]),
            congestion_state=ResultHintCongestionState(values[1]),
            reason=ResultHintReason(values[2]),
            retry_after_ms=values[3],
        )
        metadata._validate()
        return metadata


@dataclass(slots=True)
class TransportProbeMetadata(_FixedWidthMetadata):
    """Fixed-width metadata for TRANSPORT_PROBE."""

    STRUCT: ClassVar[struct.Struct] = TRANSPORT_PROBE_STRUCT

    probe_id: int
    probe_payload_bytes: int
    client_send_ts_us: int

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.probe_id,
            self.probe_payload_bytes,
            self.client_send_ts_us,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> TransportProbeMetadata:
        return cls(*values)


@dataclass(slots=True)
class TransportProbeAckMetadata(_FixedWidthMetadata):
    """Fixed-width metadata for TRANSPORT_PROBE_ACK."""

    STRUCT: ClassVar[struct.Struct] = TRANSPORT_PROBE_ACK_STRUCT

    probe_id: int
    reserved: int
    server_recv_ts_us: int

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.probe_id,
            self.reserved,
            self.server_recv_ts_us,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> TransportProbeAckMetadata:
        return cls(*values)


@dataclass(slots=True)
class SessionMigrateMetadata(_FixedWidthMetadata):
    """Fixed-width metadata for SESSION_MIGRATE."""

    STRUCT: ClassVar[struct.Struct] = SESSION_MIGRATE_STRUCT

    old_transport_id: TransportId
    new_transport_id: TransportId
    last_result_frame_id: int
    client_migrate_ts_us: int

    def _validate(self) -> None:
        _coerce_transport_id(self.old_transport_id, allow_unspecified=False)
        _coerce_transport_id(self.new_transport_id, allow_unspecified=False)

    def pack(self) -> bytes:
        self._validate()
        return self.STRUCT.pack(
            int(self.old_transport_id),
            int(self.new_transport_id),
            self.last_result_frame_id,
            self.client_migrate_ts_us,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> SessionMigrateMetadata:
        return cls(
            old_transport_id=_coerce_transport_id(values[0], allow_unspecified=False),
            new_transport_id=_coerce_transport_id(values[1], allow_unspecified=False),
            last_result_frame_id=values[2],
            client_migrate_ts_us=values[3],
        )


@dataclass(slots=True)
class SessionMigrateAckMetadata(_FixedWidthMetadata):
    """Fixed-width metadata for SESSION_MIGRATE_ACK."""

    STRUCT: ClassVar[struct.Struct] = SESSION_MIGRATE_ACK_STRUCT

    accept_code: int
    resume_from_frame_id: int
    grace_window_ms: int
    server_migrate_ts_us: int

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.accept_code,
            self.resume_from_frame_id,
            self.grace_window_ms,
            self.server_migrate_ts_us,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> SessionMigrateAckMetadata:
        return cls(*values)


CLIENT_HELLO_STRUCT = struct.Struct("<BBH" + "I" * 6 + "H" * 4 + "I" * 2 + "H" * 4 + "I" * 3)
SERVER_HELLO_ACK_STRUCT = struct.Struct("<BBBB" + "I" * 11 + "H" * 6 + "I" * 5)
SESSION_PATCH_STRUCT = struct.Struct("<HHIIHHQIII")
SESSION_PATCH_ACK_STRUCT = struct.Struct("<HHIIIHHIHHQIII")
TENSOR_PROFILE_PATCH_BLOCK_STRUCT = struct.Struct("<IIII")
TENSOR_PROFILE_PATCH_ACK_BLOCK_STRUCT = struct.Struct("<IIII")
ERROR_STRUCT = struct.Struct("<8I")
CACHE_PUT_STRUCT = struct.Struct("<8I")
CACHE_ACK_STRUCT = struct.Struct("<7I")
CACHE_INVALIDATE_STRUCT = struct.Struct("<5I")
CONTROL_EXTENSION_HEADER_STRUCT = struct.Struct("<HHI")

CLIENT_HELLO_METADATA_LENGTH = CLIENT_HELLO_STRUCT.size
SERVER_HELLO_ACK_METADATA_LENGTH = SERVER_HELLO_ACK_STRUCT.size
SESSION_PATCH_METADATA_LENGTH = SESSION_PATCH_STRUCT.size
SESSION_PATCH_ACK_METADATA_LENGTH = SESSION_PATCH_ACK_STRUCT.size
TENSOR_PROFILE_PATCH_BLOCK_LENGTH = TENSOR_PROFILE_PATCH_BLOCK_STRUCT.size
TENSOR_PROFILE_PATCH_ACK_BLOCK_LENGTH = TENSOR_PROFILE_PATCH_ACK_BLOCK_STRUCT.size
ERROR_METADATA_LENGTH = ERROR_STRUCT.size
CACHE_PUT_METADATA_LENGTH = CACHE_PUT_STRUCT.size
CACHE_ACK_METADATA_LENGTH = CACHE_ACK_STRUCT.size
CACHE_INVALIDATE_METADATA_LENGTH = CACHE_INVALIDATE_STRUCT.size
FLOW_UPDATE_METADATA_LENGTH = FLOW_UPDATE_STRUCT.size
RESULT_HINT_METADATA_LENGTH = RESULT_HINT_STRUCT.size
TRANSPORT_PROBE_METADATA_LENGTH = TRANSPORT_PROBE_STRUCT.size
TRANSPORT_PROBE_ACK_METADATA_LENGTH = TRANSPORT_PROBE_ACK_STRUCT.size
SESSION_MIGRATE_METADATA_LENGTH = SESSION_MIGRATE_STRUCT.size
SESSION_MIGRATE_ACK_METADATA_LENGTH = SESSION_MIGRATE_ACK_STRUCT.size
CONTROL_EXTENSION_HEADER_LENGTH = CONTROL_EXTENSION_HEADER_STRUCT.size
CONTROL_EXTENSION_ALIGNMENT = 8


class ControlExtensionFlags(IntFlag):
    NONE = 0
    CRITICAL = 0x0001


class TransportPolicy(IntEnum):
    AUTO = 0
    PREFER_QUIC = 1
    PREFER_TCP = 2
    FORCE_QUIC = 3
    FORCE_TCP = 4


class TransportId(IntEnum):
    UNSPECIFIED = 0
    QUIC = 1
    TCP = 2


class LossTolerance(IntEnum):
    STRICT = 0
    BEST_EFFORT = 1
    LOW_LATENCY = 2
    FIRE_AND_FORGET = 3


class PayloadKind(IntFlag):
    NONE = 0
    TENSOR = 0x00000001
    TOKEN_CHUNK = 0x00000002
    AUDIO_CHUNK = 0x00000004
    VIDEO_CHUNK = 0x00000008
    STRUCTURED_EVENT = 0x00000010
    TOOL_DELTA = 0x00000020
    OPAQUE_BYTES = 0x00000040


CLIENT_HELLO_TRANSPORT_POLICY_EXTENSION = 0x0101
SERVER_HELLO_ACK_TRANSPORT_POLICY_EXTENSION = 0x0102
CLIENT_HELLO_LOSS_TOLERANCE_EXTENSION = 0x0103
SERVER_HELLO_ACK_LOSS_TOLERANCE_EXTENSION = 0x0104
CLIENT_HELLO_PAYLOAD_CAPABILITIES_EXTENSION = 0x0105
SERVER_HELLO_ACK_PAYLOAD_CAPABILITIES_EXTENSION = 0x0106
TRANSPORT_POLICY_EXTENSION_STRUCT = struct.Struct("<BBHI")
LOSS_TOLERANCE_EXTENSION_STRUCT = struct.Struct("<BBHI")
PAYLOAD_CAPABILITIES_EXTENSION_STRUCT = struct.Struct("<II")

_PAYLOAD_KIND_ALLOWED_MASK = (
    PayloadKind.TENSOR
    | PayloadKind.TOKEN_CHUNK
    | PayloadKind.AUDIO_CHUNK
    | PayloadKind.VIDEO_CHUNK
    | PayloadKind.STRUCTURED_EVENT
    | PayloadKind.TOOL_DELTA
    | PayloadKind.OPAQUE_BYTES
)


def _coerce_transport_id(value: int | TransportId, *, allow_unspecified: bool) -> TransportId:
    transport_id = TransportId(value)
    if not allow_unspecified and transport_id is TransportId.UNSPECIFIED:
        raise ValueError("transport id must not be unspecified")
    return transport_id


@dataclass(frozen=True, slots=True)
class ClientHelloTransportPolicyExtension:
    transport_policy: TransportPolicy = TransportPolicy.AUTO
    preferred_transport_id: TransportId = TransportId.UNSPECIFIED

    def _validate(self) -> None:
        _coerce_transport_id(self.preferred_transport_id, allow_unspecified=True)

    def pack(self) -> bytes:
        self._validate()
        return TRANSPORT_POLICY_EXTENSION_STRUCT.pack(
            int(self.transport_policy),
            0,
            0,
            int(self.preferred_transport_id),
        )

    @classmethod
    def unpack(cls, payload: bytes) -> ClientHelloTransportPolicyExtension:
        if len(payload) != TRANSPORT_POLICY_EXTENSION_STRUCT.size:
            raise ValueError(f"expected {TRANSPORT_POLICY_EXTENSION_STRUCT.size} bytes, got {len(payload)}")
        transport_policy, _accepted_policy, _reserved, preferred_transport_id = (
            TRANSPORT_POLICY_EXTENSION_STRUCT.unpack(payload)
        )
        return cls(
            transport_policy=TransportPolicy(transport_policy),
            preferred_transport_id=_coerce_transport_id(preferred_transport_id, allow_unspecified=True),
        )


@dataclass(frozen=True, slots=True)
class ServerHelloAckTransportPolicyExtension:
    transport_policy: TransportPolicy = TransportPolicy.AUTO
    accepted_transport_policy: TransportPolicy = TransportPolicy.AUTO
    active_transport_id: TransportId = TransportId.UNSPECIFIED

    def _validate(self) -> None:
        _coerce_transport_id(self.active_transport_id, allow_unspecified=True)

    def pack(self) -> bytes:
        self._validate()
        return TRANSPORT_POLICY_EXTENSION_STRUCT.pack(
            int(self.transport_policy),
            int(self.accepted_transport_policy),
            0,
            int(self.active_transport_id),
        )

    @classmethod
    def unpack(cls, payload: bytes) -> ServerHelloAckTransportPolicyExtension:
        if len(payload) != TRANSPORT_POLICY_EXTENSION_STRUCT.size:
            raise ValueError(f"expected {TRANSPORT_POLICY_EXTENSION_STRUCT.size} bytes, got {len(payload)}")
        transport_policy, accepted_transport_policy, _reserved, active_transport_id = (
            TRANSPORT_POLICY_EXTENSION_STRUCT.unpack(payload)
        )
        return cls(
            transport_policy=TransportPolicy(transport_policy),
            accepted_transport_policy=TransportPolicy(accepted_transport_policy),
            active_transport_id=_coerce_transport_id(active_transport_id, allow_unspecified=True),
        )


@dataclass(frozen=True, slots=True)
class ClientHelloLossToleranceExtension:
    session_loss_tolerance: LossTolerance = LossTolerance.BEST_EFFORT

    def pack(self) -> bytes:
        return LOSS_TOLERANCE_EXTENSION_STRUCT.pack(
            int(self.session_loss_tolerance),
            0,
            0,
            0,
        )

    @classmethod
    def unpack(cls, payload: bytes) -> ClientHelloLossToleranceExtension:
        if len(payload) != LOSS_TOLERANCE_EXTENSION_STRUCT.size:
            raise ValueError(f"expected {LOSS_TOLERANCE_EXTENSION_STRUCT.size} bytes, got {len(payload)}")
        session_loss_tolerance, reserved0, reserved1, reserved2 = LOSS_TOLERANCE_EXTENSION_STRUCT.unpack(payload)
        if reserved0 != 0 or reserved1 != 0 or reserved2 != 0:
            raise ValueError("client hello loss tolerance extension reserved fields must be zero")
        return cls(session_loss_tolerance=LossTolerance(session_loss_tolerance))


@dataclass(frozen=True, slots=True)
class ServerHelloAckLossToleranceExtension:
    accepted_loss_tolerance: LossTolerance = LossTolerance.BEST_EFFORT

    def pack(self) -> bytes:
        return LOSS_TOLERANCE_EXTENSION_STRUCT.pack(
            int(self.accepted_loss_tolerance),
            0,
            0,
            0,
        )

    @classmethod
    def unpack(cls, payload: bytes) -> ServerHelloAckLossToleranceExtension:
        if len(payload) != LOSS_TOLERANCE_EXTENSION_STRUCT.size:
            raise ValueError(f"expected {LOSS_TOLERANCE_EXTENSION_STRUCT.size} bytes, got {len(payload)}")
        accepted_loss_tolerance, reserved0, reserved1, reserved2 = LOSS_TOLERANCE_EXTENSION_STRUCT.unpack(payload)
        if reserved0 != 0 or reserved1 != 0 or reserved2 != 0:
            raise ValueError("server hello ack loss tolerance extension reserved fields must be zero")
        return cls(accepted_loss_tolerance=LossTolerance(accepted_loss_tolerance))


def _validate_payload_capabilities(
    *,
    payload_kind_bitmap: PayloadKind,
    critical_extension_frame_bitmap: int,
) -> None:
    unknown_payload_kind_bits = int(payload_kind_bitmap) & ~int(_PAYLOAD_KIND_ALLOWED_MASK)
    if unknown_payload_kind_bits:
        raise ValueError(f"unknown payload kind bits: 0x{int(payload_kind_bitmap):08x}")
    if critical_extension_frame_bitmap != 0:
        raise ValueError("critical extension frame bitmap must be zero in current")


@dataclass(frozen=True, slots=True)
class ClientHelloPayloadCapabilitiesExtension:
    payload_kind_bitmap: PayloadKind = PayloadKind.TENSOR
    critical_extension_frame_bitmap: int = 0

    def pack(self) -> bytes:
        _validate_payload_capabilities(
            payload_kind_bitmap=self.payload_kind_bitmap,
            critical_extension_frame_bitmap=self.critical_extension_frame_bitmap,
        )
        return PAYLOAD_CAPABILITIES_EXTENSION_STRUCT.pack(
            int(self.payload_kind_bitmap),
            self.critical_extension_frame_bitmap,
        )

    @classmethod
    def unpack(cls, payload: bytes) -> ClientHelloPayloadCapabilitiesExtension:
        if len(payload) != PAYLOAD_CAPABILITIES_EXTENSION_STRUCT.size:
            raise ValueError(f"expected {PAYLOAD_CAPABILITIES_EXTENSION_STRUCT.size} bytes, got {len(payload)}")
        payload_kind_bitmap, critical_extension_frame_bitmap = PAYLOAD_CAPABILITIES_EXTENSION_STRUCT.unpack(payload)
        extension = cls(
            payload_kind_bitmap=PayloadKind(payload_kind_bitmap),
            critical_extension_frame_bitmap=critical_extension_frame_bitmap,
        )
        _validate_payload_capabilities(
            payload_kind_bitmap=extension.payload_kind_bitmap,
            critical_extension_frame_bitmap=extension.critical_extension_frame_bitmap,
        )
        return extension


@dataclass(frozen=True, slots=True)
class ServerHelloAckPayloadCapabilitiesExtension:
    accepted_payload_kind_bitmap: PayloadKind = PayloadKind.TENSOR
    accepted_critical_extension_frame_bitmap: int = 0

    def pack(self) -> bytes:
        _validate_payload_capabilities(
            payload_kind_bitmap=self.accepted_payload_kind_bitmap,
            critical_extension_frame_bitmap=self.accepted_critical_extension_frame_bitmap,
        )
        return PAYLOAD_CAPABILITIES_EXTENSION_STRUCT.pack(
            int(self.accepted_payload_kind_bitmap),
            self.accepted_critical_extension_frame_bitmap,
        )

    @classmethod
    def unpack(cls, payload: bytes) -> ServerHelloAckPayloadCapabilitiesExtension:
        if len(payload) != PAYLOAD_CAPABILITIES_EXTENSION_STRUCT.size:
            raise ValueError(f"expected {PAYLOAD_CAPABILITIES_EXTENSION_STRUCT.size} bytes, got {len(payload)}")
        accepted_payload_kind_bitmap, accepted_critical_extension_frame_bitmap = (
            PAYLOAD_CAPABILITIES_EXTENSION_STRUCT.unpack(payload)
        )
        extension = cls(
            accepted_payload_kind_bitmap=PayloadKind(accepted_payload_kind_bitmap),
            accepted_critical_extension_frame_bitmap=accepted_critical_extension_frame_bitmap,
        )
        _validate_payload_capabilities(
            payload_kind_bitmap=extension.accepted_payload_kind_bitmap,
            critical_extension_frame_bitmap=extension.accepted_critical_extension_frame_bitmap,
        )
        return extension


def build_client_hello_transport_policy_extension(
    extension: ClientHelloTransportPolicyExtension,
) -> ControlExtensionEntry:
    return ControlExtensionEntry(
        ext_type=CLIENT_HELLO_TRANSPORT_POLICY_EXTENSION,
        payload=extension.pack(),
    )


def build_server_hello_ack_transport_policy_extension(
    extension: ServerHelloAckTransportPolicyExtension,
) -> ControlExtensionEntry:
    return ControlExtensionEntry(
        ext_type=SERVER_HELLO_ACK_TRANSPORT_POLICY_EXTENSION,
        payload=extension.pack(),
    )


def build_client_hello_loss_tolerance_extension(
    extension: ClientHelloLossToleranceExtension,
) -> ControlExtensionEntry:
    return ControlExtensionEntry(
        ext_type=CLIENT_HELLO_LOSS_TOLERANCE_EXTENSION,
        payload=extension.pack(),
    )


def build_server_hello_ack_loss_tolerance_extension(
    extension: ServerHelloAckLossToleranceExtension,
) -> ControlExtensionEntry:
    return ControlExtensionEntry(
        ext_type=SERVER_HELLO_ACK_LOSS_TOLERANCE_EXTENSION,
        payload=extension.pack(),
    )


def build_client_hello_payload_capabilities_extension(
    extension: ClientHelloPayloadCapabilitiesExtension,
) -> ControlExtensionEntry:
    return ControlExtensionEntry(
        ext_type=CLIENT_HELLO_PAYLOAD_CAPABILITIES_EXTENSION,
        payload=extension.pack(),
    )


def build_server_hello_ack_payload_capabilities_extension(
    extension: ServerHelloAckPayloadCapabilitiesExtension,
) -> ControlExtensionEntry:
    return ControlExtensionEntry(
        ext_type=SERVER_HELLO_ACK_PAYLOAD_CAPABILITIES_EXTENSION,
        payload=extension.pack(),
    )


def parse_client_hello_transport_policy_extension(
    entry: ControlExtensionEntry,
) -> ClientHelloTransportPolicyExtension:
    if entry.ext_type != CLIENT_HELLO_TRANSPORT_POLICY_EXTENSION:
        raise ValueError(f"unexpected client hello transport extension type: 0x{entry.ext_type:04X}")
    return ClientHelloTransportPolicyExtension.unpack(entry.payload)


def parse_server_hello_ack_transport_policy_extension(
    entry: ControlExtensionEntry,
) -> ServerHelloAckTransportPolicyExtension:
    if entry.ext_type != SERVER_HELLO_ACK_TRANSPORT_POLICY_EXTENSION:
        raise ValueError(f"unexpected server hello ack transport extension type: 0x{entry.ext_type:04X}")
    return ServerHelloAckTransportPolicyExtension.unpack(entry.payload)


def parse_client_hello_loss_tolerance_extension(
    entry: ControlExtensionEntry,
) -> ClientHelloLossToleranceExtension:
    if entry.ext_type != CLIENT_HELLO_LOSS_TOLERANCE_EXTENSION:
        raise ValueError(f"unexpected client hello loss tolerance extension type: 0x{entry.ext_type:04X}")
    return ClientHelloLossToleranceExtension.unpack(entry.payload)


def parse_server_hello_ack_loss_tolerance_extension(
    entry: ControlExtensionEntry,
) -> ServerHelloAckLossToleranceExtension:
    if entry.ext_type != SERVER_HELLO_ACK_LOSS_TOLERANCE_EXTENSION:
        raise ValueError(f"unexpected server hello ack loss tolerance extension type: 0x{entry.ext_type:04X}")
    return ServerHelloAckLossToleranceExtension.unpack(entry.payload)


def parse_client_hello_payload_capabilities_extension(
    entry: ControlExtensionEntry,
) -> ClientHelloPayloadCapabilitiesExtension:
    if entry.ext_type != CLIENT_HELLO_PAYLOAD_CAPABILITIES_EXTENSION:
        raise ValueError(f"unexpected client hello payload capabilities extension type: 0x{entry.ext_type:04X}")
    return ClientHelloPayloadCapabilitiesExtension.unpack(entry.payload)


def parse_server_hello_ack_payload_capabilities_extension(
    entry: ControlExtensionEntry,
) -> ServerHelloAckPayloadCapabilitiesExtension:
    if entry.ext_type != SERVER_HELLO_ACK_PAYLOAD_CAPABILITIES_EXTENSION:
        raise ValueError(f"unexpected server hello ack payload capabilities extension type: 0x{entry.ext_type:04X}")
    return ServerHelloAckPayloadCapabilitiesExtension.unpack(entry.payload)


@dataclass(frozen=True, slots=True)
class ControlExtensionEntry:
    """One control_extension_block TLV entry."""

    ext_type: int
    ext_flags: ControlExtensionFlags = ControlExtensionFlags.NONE
    payload: bytes = b""

    def pack(self) -> bytes:
        payload = bytes(self.payload)
        header = CONTROL_EXTENSION_HEADER_STRUCT.pack(
            self.ext_type,
            int(self.ext_flags),
            len(payload),
        )
        return header + payload + (b"\x00" * _control_extension_padding(len(payload)))


def pack_control_extension_block(entries: Iterable[ControlExtensionEntry]) -> bytes:
    return b"".join(entry.pack() for entry in entries)


def unpack_control_extension_block(
    payload: bytes,
    *,
    known_types: Set[int] | None = None,
) -> tuple[ControlExtensionEntry, ...]:
    offset = 0
    entries: list[ControlExtensionEntry] = []

    while offset < len(payload):
        remaining = len(payload) - offset
        if remaining < CONTROL_EXTENSION_HEADER_LENGTH:
            raise ValueError("truncated control extension header")

        ext_type, ext_flags, ext_len = CONTROL_EXTENSION_HEADER_STRUCT.unpack_from(payload, offset)
        payload_start = offset + CONTROL_EXTENSION_HEADER_LENGTH
        payload_end = payload_start + ext_len
        if payload_end > len(payload):
            raise ValueError("truncated control extension payload")

        padding_end = payload_end + _control_extension_padding(ext_len)
        if padding_end > len(payload):
            raise ValueError("truncated control extension padding")

        padding = payload[payload_end:padding_end]
        if any(padding):
            raise ValueError("control extension padding must be zero")

        entry = ControlExtensionEntry(
            ext_type=ext_type,
            ext_flags=ControlExtensionFlags(ext_flags),
            payload=payload[payload_start:payload_end],
        )
        offset = padding_end

        if known_types is not None and entry.ext_type not in known_types:
            if entry.ext_flags & ControlExtensionFlags.CRITICAL:
                raise ValueError(f"unknown critical control extension type: 0x{entry.ext_type:04X}")
            continue

        entries.append(entry)

    return tuple(entries)


def _control_extension_padding(payload_length: int) -> int:
    return (-payload_length) % CONTROL_EXTENSION_ALIGNMENT


@dataclass(slots=True)
class ClientHelloMetadata(_FixedWidthMetadata):
    """Fixed-width metadata for CLIENT_HELLO.

    Capability sets are carried as bitmaps so the body can remain free for
    opaque blocks such as auth material.
    """

    STRUCT: ClassVar[struct.Struct] = CLIENT_HELLO_STRUCT

    min_version_major: int
    max_version_major: int
    supported_wire_format_bitmap: int
    supported_profile_bitmap: int
    supported_payload_kind_bitmap: int
    supported_codec_bitmap: int
    supported_compression_bitmap: int
    supported_dtype_bitmap: int
    supported_layout_bitmap: int
    cache_digest_bitmap: int
    cache_object_bitmap: int
    cache_namespace_count: int
    max_lane_count: int
    max_cache_entries: int
    max_cache_bytes: int
    target_cadence_x100: int
    latency_budget_ms: int
    quality_tier: int
    degrade_policy: int
    requested_session_id: int
    auth_bytes: int
    control_extension_bytes: int

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.min_version_major,
            self.max_version_major,
            self.supported_wire_format_bitmap,
            self.supported_profile_bitmap,
            self.supported_payload_kind_bitmap,
            self.supported_codec_bitmap,
            self.supported_compression_bitmap,
            self.supported_dtype_bitmap,
            self.supported_layout_bitmap,
            self.cache_digest_bitmap,
            self.cache_object_bitmap,
            self.cache_namespace_count,
            self.max_lane_count,
            self.max_cache_entries,
            self.max_cache_bytes,
            self.target_cadence_x100,
            self.latency_budget_ms,
            self.quality_tier,
            self.degrade_policy,
            self.requested_session_id,
            self.auth_bytes,
            self.control_extension_bytes,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> ClientHelloMetadata:
        return cls(*values)


@dataclass(slots=True)
class ServerHelloAckMetadata(_FixedWidthMetadata):
    """Fixed-width metadata for SERVER_HELLO_ACK."""

    STRUCT: ClassVar[struct.Struct] = SERVER_HELLO_ACK_STRUCT

    selected_version_major: int
    selected_wire_format: int
    auth_status: int
    session_id: int
    accepted_profile_bitmap: int
    accepted_payload_kind_bitmap: int
    accepted_codec_bitmap: int
    accepted_compression_bitmap: int
    accepted_dtype_bitmap: int
    accepted_layout_bitmap: int
    cache_digest_bitmap: int
    cache_object_bitmap: int
    max_cache_entries: int
    max_cache_bytes: int
    max_lane_count: int
    max_concurrent_frames: int
    target_cadence_x100: int
    latency_budget_ms: int
    quality_tier: int
    degrade_policy: int
    max_body_bytes: int
    token_ttl_ms: int
    retry_after_ms: int
    control_extension_bytes: int
    server_flags: int
    reserved0: int = 0

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.selected_version_major,
            self.selected_wire_format,
            self.auth_status,
            self.reserved0,
            self.session_id,
            self.accepted_profile_bitmap,
            self.accepted_payload_kind_bitmap,
            self.accepted_codec_bitmap,
            self.accepted_compression_bitmap,
            self.accepted_dtype_bitmap,
            self.accepted_layout_bitmap,
            self.cache_digest_bitmap,
            self.cache_object_bitmap,
            self.max_cache_entries,
            self.max_cache_bytes,
            self.max_lane_count,
            self.max_concurrent_frames,
            self.target_cadence_x100,
            self.latency_budget_ms,
            self.quality_tier,
            self.degrade_policy,
            self.max_body_bytes,
            self.token_ttl_ms,
            self.retry_after_ms,
            self.control_extension_bytes,
            self.server_flags,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> ServerHelloAckMetadata:
        (
            selected_version_major,
            selected_wire_format,
            auth_status,
            reserved0,
            session_id,
            accepted_profile_bitmap,
            accepted_payload_kind_bitmap,
            accepted_codec_bitmap,
            accepted_compression_bitmap,
            accepted_dtype_bitmap,
            accepted_layout_bitmap,
            cache_digest_bitmap,
            cache_object_bitmap,
            max_cache_entries,
            max_cache_bytes,
            max_lane_count,
            max_concurrent_frames,
            target_cadence_x100,
            latency_budget_ms,
            quality_tier,
            degrade_policy,
            max_body_bytes,
            token_ttl_ms,
            retry_after_ms,
            control_extension_bytes,
            server_flags,
        ) = values
        return cls(
            selected_version_major=selected_version_major,
            selected_wire_format=selected_wire_format,
            auth_status=auth_status,
            session_id=session_id,
            accepted_profile_bitmap=accepted_profile_bitmap,
            accepted_payload_kind_bitmap=accepted_payload_kind_bitmap,
            accepted_codec_bitmap=accepted_codec_bitmap,
            accepted_compression_bitmap=accepted_compression_bitmap,
            accepted_dtype_bitmap=accepted_dtype_bitmap,
            accepted_layout_bitmap=accepted_layout_bitmap,
            cache_digest_bitmap=cache_digest_bitmap,
            cache_object_bitmap=cache_object_bitmap,
            max_cache_entries=max_cache_entries,
            max_cache_bytes=max_cache_bytes,
            max_lane_count=max_lane_count,
            max_concurrent_frames=max_concurrent_frames,
            target_cadence_x100=target_cadence_x100,
            latency_budget_ms=latency_budget_ms,
            quality_tier=quality_tier,
            degrade_policy=degrade_policy,
            max_body_bytes=max_body_bytes,
            token_ttl_ms=token_ttl_ms,
            retry_after_ms=retry_after_ms,
            control_extension_bytes=control_extension_bytes,
            server_flags=server_flags,
            reserved0=reserved0,
        )


@dataclass(slots=True)
class SessionPatchMetadata(_FixedWidthMetadata):
    """Fixed-width metadata for SESSION_PATCH.

    Patchable low-frequency fields are carried in-place and gated by a bitmask,
    so clients can send one fixed-width control record without re-sending the
    full session contract.
    """

    STRUCT: ClassVar[struct.Struct] = SESSION_PATCH_STRUCT

    profile_id: int
    patch_mask: SessionPatchField
    target_cadence_x100: int
    quality_tier: int
    degrade_policy: int
    active_lane_mask: int
    preferred_codec_bitmap: int
    preferred_compression_bitmap: int
    profile_patch_bytes: int
    reserved0: int = 0

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.profile_id,
            self.reserved0,
            int(self.patch_mask),
            self.target_cadence_x100,
            self.quality_tier,
            self.degrade_policy,
            self.active_lane_mask,
            self.preferred_codec_bitmap,
            self.preferred_compression_bitmap,
            self.profile_patch_bytes,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> SessionPatchMetadata:
        (
            profile_id,
            reserved0,
            patch_mask,
            target_cadence_x100,
            quality_tier,
            degrade_policy,
            active_lane_mask,
            preferred_codec_bitmap,
            preferred_compression_bitmap,
            profile_patch_bytes,
        ) = values
        return cls(
            profile_id=profile_id,
            patch_mask=SessionPatchField(patch_mask),
            target_cadence_x100=target_cadence_x100,
            quality_tier=quality_tier,
            degrade_policy=degrade_policy,
            active_lane_mask=active_lane_mask,
            preferred_codec_bitmap=preferred_codec_bitmap,
            preferred_compression_bitmap=preferred_compression_bitmap,
            profile_patch_bytes=profile_patch_bytes,
            reserved0=reserved0,
        )


@dataclass(slots=True)
class SessionPatchAckMetadata(_FixedWidthMetadata):
    """Fixed-width metadata for SESSION_PATCH_ACK."""

    STRUCT: ClassVar[struct.Struct] = SESSION_PATCH_ACK_STRUCT

    ack_status: SessionPatchAckStatus
    reject_reason: SessionPatchRejectReason
    applied_patch_mask: SessionPatchField
    rejected_patch_mask: SessionPatchField
    retry_after_ms: int
    effective_profile_id: int
    effective_target_cadence_x100: int
    effective_quality_tier: int
    effective_degrade_policy: int
    effective_lane_mask: int
    effective_codec_bitmap: int
    effective_compression_bitmap: int
    profile_patch_ack_bytes: int
    reserved0: int = 0

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            int(self.ack_status),
            int(self.reject_reason),
            int(self.applied_patch_mask),
            int(self.rejected_patch_mask),
            self.retry_after_ms,
            self.effective_profile_id,
            self.reserved0,
            self.effective_target_cadence_x100,
            self.effective_quality_tier,
            self.effective_degrade_policy,
            self.effective_lane_mask,
            self.effective_codec_bitmap,
            self.effective_compression_bitmap,
            self.profile_patch_ack_bytes,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> SessionPatchAckMetadata:
        (
            ack_status,
            reject_reason,
            applied_patch_mask,
            rejected_patch_mask,
            retry_after_ms,
            effective_profile_id,
            reserved0,
            effective_target_cadence_x100,
            effective_quality_tier,
            effective_degrade_policy,
            effective_lane_mask,
            effective_codec_bitmap,
            effective_compression_bitmap,
            profile_patch_ack_bytes,
        ) = values
        return cls(
            ack_status=SessionPatchAckStatus(ack_status),
            reject_reason=SessionPatchRejectReason(reject_reason),
            applied_patch_mask=SessionPatchField(applied_patch_mask),
            rejected_patch_mask=SessionPatchField(rejected_patch_mask),
            retry_after_ms=retry_after_ms,
            effective_profile_id=effective_profile_id,
            effective_target_cadence_x100=effective_target_cadence_x100,
            effective_quality_tier=effective_quality_tier,
            effective_degrade_policy=effective_degrade_policy,
            effective_lane_mask=effective_lane_mask,
            effective_codec_bitmap=effective_codec_bitmap,
            effective_compression_bitmap=effective_compression_bitmap,
            profile_patch_ack_bytes=profile_patch_ack_bytes,
            reserved0=reserved0,
        )


@dataclass(slots=True)
class TensorProfilePatchBlock(_FixedWidthMetadata):
    """Tensor profile-specific clamp block for SESSION_PATCH body."""

    STRUCT: ClassVar[struct.Struct] = TENSOR_PROFILE_PATCH_BLOCK_STRUCT

    min_width: int
    min_height: int
    max_width: int
    max_height: int

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.min_width,
            self.min_height,
            self.max_width,
            self.max_height,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> TensorProfilePatchBlock:
        return cls(*values)


@dataclass(slots=True)
class TensorProfilePatchAckBlock(_FixedWidthMetadata):
    """Tensor profile-specific effective clamp block for SESSION_PATCH_ACK body."""

    STRUCT: ClassVar[struct.Struct] = TENSOR_PROFILE_PATCH_ACK_BLOCK_STRUCT

    min_width: int
    min_height: int
    max_width: int
    max_height: int

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.min_width,
            self.min_height,
            self.max_width,
            self.max_height,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> TensorProfilePatchAckBlock:
        return cls(*values)


def pack_session_patch_body(
    *,
    profile_patch_block: TensorProfilePatchBlock | None = None,
) -> bytes:
    if profile_patch_block is None:
        return b""
    return profile_patch_block.pack()


def unpack_session_patch_body(
    body: bytes,
    *,
    profile_patch_bytes: int,
) -> TensorProfilePatchBlock | None:
    if profile_patch_bytes == 0:
        if len(body) != 0:
            raise ValueError(f"expected empty SESSION_PATCH body, got {len(body)} bytes")
        return None
    if profile_patch_bytes != TENSOR_PROFILE_PATCH_BLOCK_LENGTH:
        raise ValueError(
            "SESSION_PATCH profile_patch_bytes must be 0 or "
            f"{TENSOR_PROFILE_PATCH_BLOCK_LENGTH}, got {profile_patch_bytes}"
        )
    if len(body) != profile_patch_bytes:
        raise ValueError(f"expected {profile_patch_bytes} SESSION_PATCH body bytes, got {len(body)}")
    return TensorProfilePatchBlock.unpack(body)


def pack_session_patch_ack_body(
    *,
    profile_patch_ack_block: TensorProfilePatchAckBlock | None = None,
) -> bytes:
    if profile_patch_ack_block is None:
        return b""
    return profile_patch_ack_block.pack()


def unpack_session_patch_ack_body(
    body: bytes,
    *,
    profile_patch_ack_bytes: int,
) -> TensorProfilePatchAckBlock | None:
    if profile_patch_ack_bytes == 0:
        if len(body) != 0:
            raise ValueError(f"expected empty SESSION_PATCH_ACK body, got {len(body)} bytes")
        return None
    if profile_patch_ack_bytes != TENSOR_PROFILE_PATCH_ACK_BLOCK_LENGTH:
        raise ValueError(
            "SESSION_PATCH_ACK profile_patch_ack_bytes must be 0 or "
            f"{TENSOR_PROFILE_PATCH_ACK_BLOCK_LENGTH}, got {profile_patch_ack_bytes}"
        )
    if len(body) != profile_patch_ack_bytes:
        raise ValueError(f"expected {profile_patch_ack_bytes} SESSION_PATCH_ACK body bytes, got {len(body)}")
    return TensorProfilePatchAckBlock.unpack(body)


@dataclass(slots=True)
class ErrorMetadata(_FixedWidthMetadata):
    """Fixed-width metadata for ERROR."""

    STRUCT: ClassVar[struct.Struct] = ERROR_STRUCT

    error_code: int
    error_scope: ErrorScope
    is_fatal: int
    retry_after_ms: int
    related_session_id: int
    related_frame_id: int
    related_view_id: int
    diagnostic_bytes: int

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.error_code,
            int(self.error_scope),
            self.is_fatal,
            self.retry_after_ms,
            self.related_session_id,
            self.related_frame_id,
            self.related_view_id,
            self.diagnostic_bytes,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> ErrorMetadata:
        (
            error_code,
            error_scope,
            is_fatal,
            retry_after_ms,
            related_session_id,
            related_frame_id,
            related_view_id,
            diagnostic_bytes,
        ) = values
        return cls(
            error_code=error_code,
            error_scope=ErrorScope(error_scope),
            is_fatal=is_fatal,
            retry_after_ms=retry_after_ms,
            related_session_id=related_session_id,
            related_frame_id=related_frame_id,
            related_view_id=related_view_id,
            diagnostic_bytes=diagnostic_bytes,
        )


@dataclass(slots=True)
class CachePutMetadata(_FixedWidthMetadata):
    """Fixed-width metadata for CACHE_PUT."""

    STRUCT: ClassVar[struct.Struct] = CACHE_PUT_STRUCT

    cache_namespace: int
    cache_key_hi: int
    cache_key_lo: int
    object_kind: CacheObjectKind
    ttl_ms: int
    object_bytes: int
    codec_bitmap: int
    flags: CachePutFlags = CachePutFlags.NONE

    def __post_init__(self) -> None:
        self.object_kind = CacheObjectKind(self.object_kind)
        self.flags = CachePutFlags(self.flags)

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.cache_namespace,
            self.cache_key_hi,
            self.cache_key_lo,
            int(self.object_kind),
            self.ttl_ms,
            self.object_bytes,
            self.codec_bitmap,
            int(self.flags),
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> CachePutMetadata:
        (
            cache_namespace,
            cache_key_hi,
            cache_key_lo,
            object_kind,
            ttl_ms,
            object_bytes,
            codec_bitmap,
            flags,
        ) = values
        return cls(
            cache_namespace=cache_namespace,
            cache_key_hi=cache_key_hi,
            cache_key_lo=cache_key_lo,
            object_kind=CacheObjectKind(object_kind),
            ttl_ms=ttl_ms,
            object_bytes=object_bytes,
            codec_bitmap=codec_bitmap,
            flags=CachePutFlags(flags),
        )


@dataclass(slots=True)
class CacheAckMetadata(_FixedWidthMetadata):
    """Fixed-width metadata for CACHE_ACK."""

    STRUCT: ClassVar[struct.Struct] = CACHE_ACK_STRUCT

    cache_namespace: int
    cache_key_hi: int
    cache_key_lo: int
    status: CacheAckStatus
    accepted_ttl_ms: int
    max_object_bytes: int
    detail_code: int

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            self.cache_namespace,
            self.cache_key_hi,
            self.cache_key_lo,
            int(self.status),
            self.accepted_ttl_ms,
            self.max_object_bytes,
            self.detail_code,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> CacheAckMetadata:
        (
            cache_namespace,
            cache_key_hi,
            cache_key_lo,
            status,
            accepted_ttl_ms,
            max_object_bytes,
            detail_code,
        ) = values
        return cls(
            cache_namespace=cache_namespace,
            cache_key_hi=cache_key_hi,
            cache_key_lo=cache_key_lo,
            status=CacheAckStatus(status),
            accepted_ttl_ms=accepted_ttl_ms,
            max_object_bytes=max_object_bytes,
            detail_code=detail_code,
        )


@dataclass(slots=True)
class CacheInvalidateMetadata(_FixedWidthMetadata):
    """Fixed-width metadata for CACHE_INVALIDATE."""

    STRUCT: ClassVar[struct.Struct] = CACHE_INVALIDATE_STRUCT

    invalidate_scope: CacheInvalidateScope
    cache_namespace: int
    cache_key_hi: int
    cache_key_lo: int
    reason_code: int

    def __post_init__(self) -> None:
        self.invalidate_scope = CacheInvalidateScope(self.invalidate_scope)

    def pack(self) -> bytes:
        return self.STRUCT.pack(
            int(self.invalidate_scope),
            self.cache_namespace,
            self.cache_key_hi,
            self.cache_key_lo,
            self.reason_code,
        )

    @classmethod
    def _from_tuple(cls, values: tuple[int, ...]) -> CacheInvalidateMetadata:
        invalidate_scope, cache_namespace, cache_key_hi, cache_key_lo, reason_code = values
        return cls(
            invalidate_scope=CacheInvalidateScope(invalidate_scope),
            cache_namespace=cache_namespace,
            cache_key_hi=cache_key_hi,
            cache_key_lo=cache_key_lo,
            reason_code=reason_code,
        )
