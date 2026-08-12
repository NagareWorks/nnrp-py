"""Native artifact discovery and ABI probe helpers for Rust-backed NNRP runtimes."""

from __future__ import annotations

import asyncio
import atexit
import ctypes
import json
import math
import os
import platform
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from enum import IntFlag, StrEnum
from functools import cmp_to_key
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable
from urllib.parse import SplitResult, urlsplit

if TYPE_CHECKING:
    from nnrp.client import NativeSessionRecoveryTicket, SubmitRequest

from nnrp.core import HeaderFlags, MessageType, SessionOpenMetadata, WireFormat
from nnrp.core.messages import (
    BudgetPolicy,
    FlowUpdateMetadata,
    FrameSubmitMetadata,
    InputProfile,
    ResultHintMetadata,
    ResultPushMetadata,
    SubmitMode,
    TileIndexMode,
)
from nnrp.core.messages.control import CacheInvalidateMetadata, PayloadKind, TransportId, TransportPolicy
from nnrp.runtime import (
    BudgetMetadata,
    CacheMissMetadata,
    CacheReferenceMetadata,
    CapabilityMetadata,
    ControlRequestMetadata,
    NativeClientEvent,
    NativeRuntimeEvent,
    NativeTerminalEvent,
    ObjectDeltaMetadata,
    ObjectDescriptorMetadata,
    ObjectReferenceMetadata,
    ObjectReleaseMetadata,
    OperationLifecycleEvent,
    OperationState,
    PartialResultMetadata,
    PressureMetadata,
    ProgressMetadata,
    RecoverableErrorMetadata,
    ResultDropReasonMetadata,
    ResultTerminalState,
    RetryAfterMetadata,
    RouteHintMetadata,
    RuntimeEventMetadata,
    RuntimeEventMetadataKind,
    RuntimeEventTail,
    RuntimeEventTailKind,
    RuntimeFrameHeader,
    SchedulingMetadata,
    SessionCloseMetadata,
    SupersedeMetadata,
    TraceContextMetadata,
    decode_runtime_control_metadata,
    decode_runtime_object_metadata,
    encode_runtime_control_metadata,
    encode_runtime_object_metadata,
)
from nnrp.runtime.types import _FixedRuntimeMetadata

_NATIVE_RUNTIME_SHUTDOWN_LOCK = threading.Lock()
_NATIVE_RUNTIME_SHUTDOWNS: dict[str, tuple[Any, Any]] = {}
_NATIVE_RUNTIME_ATEXIT_REGISTERED = False
_NATIVE_HANDLE_ID_LOCK = threading.Lock()
_NATIVE_NEXT_HANDLE_ID = 1


def _allocate_native_handle_id() -> int:
    global _NATIVE_NEXT_HANDLE_ID
    with _NATIVE_HANDLE_ID_LOCK:
        handle_id = _NATIVE_NEXT_HANDLE_ID
        _NATIVE_NEXT_HANDLE_ID = 1 if handle_id == 0xFFFFFFFFFFFFFFFF else handle_id + 1
    return handle_id


EXPECTED_PROTOCOL_MAJOR = 1
EXPECTED_PROTOCOL_WIRE_FORMAT = 0
EXPECTED_ABI_MAJOR = 4
EXPECTED_ABI_MINOR = 4
EXPECTED_ABI_PATCH = 0
TRANSPORT_SLOT_QUIC = 0x00000001
TRANSPORT_SLOT_TCP = 0x00000002
TRANSPORT_SLOT_IPC = 0x00000004
TRANSPORT_SLOT_WEBSOCKET = 0x00000008
NATIVE_TRANSPORT_SCOPES = ("tcp", "quic", "ipc", "websocket")
NATIVE_TRANSPORT_SLOT_BY_NAME = {
    "quic": TRANSPORT_SLOT_QUIC,
    "tcp": TRANSPORT_SLOT_TCP,
    "ipc": TRANSPORT_SLOT_IPC,
    "websocket": TRANSPORT_SLOT_WEBSOCKET,
}
NATIVE_TRANSPORT_ID_BY_NAME = {
    "quic": TransportId.QUIC,
    "tcp": TransportId.TCP,
    "ipc": TransportId.IPC,
    "websocket": TransportId.WEBSOCKET,
}
NATIVE_TRANSPORT_NAME_BY_ID = {transport_id: name for name, transport_id in NATIVE_TRANSPORT_ID_BY_NAME.items()}
NATIVE_ENDPOINT_TRANSPORT_BY_SCHEME = {
    "tcp": "tcp",
    "quic": "quic",
    "quic+tls": "quic",
    "unix": "ipc",
    "npipe": "ipc",
    "ws": "websocket",
    "wss": "websocket",
}
RUNTIME_FEATURE_PROTOCOL_CORE = 0x0000000000000001
RUNTIME_FEATURE_CLIENT_API = 0x0000000000000002
RUNTIME_FEATURE_SERVER_API = 0x0000000000000004
RUNTIME_FEATURE_EVENT_POLLING = 0x0000000000000008
RUNTIME_FEATURE_CALLBACK_DISPATCH = 0x0000000000000010
RUNTIME_FEATURE_CACHE_SCHEMA = 0x0000000000000020
RUNTIME_FEATURE_RECOVERY = 0x0000000000000040
RUNTIME_FEATURE_TYPED_PAYLOAD = 0x0000000000000080
RUNTIME_FEATURE_TRANSPORT_SLOTS = 0x0000000000000100
RUNTIME_FEATURE_BATCH_POLLING = 0x0000000000000200
_INDEFINITE_EVENT_POLL_SLICE_MS = 100
RUNTIME_FEATURE_CACHE_LEASE_OPS = 0x0000000000000400
RUNTIME_FEATURE_SCHEMA_REGISTRY_HANDLES = 0x0000000000000800
RUNTIME_FEATURE_BUFFER_HANDLES = 0x0000000000001000
RUNTIME_FEATURE_EXECUTABLE_RESUME = 0x0000000000002000
RUNTIME_FEATURE_PREVIEW4_CONTROL_EVENTS = 0x0000000000020000
RUNTIME_FEATURE_PREVIEW4_OBJECT_CACHE_EVENTS = 0x0000000000040000
RUNTIME_FEATURE_PREVIEW4_RUNTIME_FRAME_SEND = 0x0000000000080000


class NativeRuntimeFeatureFlag(IntFlag):
    PROTOCOL_CORE = RUNTIME_FEATURE_PROTOCOL_CORE
    CLIENT_API = RUNTIME_FEATURE_CLIENT_API
    SERVER_API = RUNTIME_FEATURE_SERVER_API
    EVENT_POLLING = RUNTIME_FEATURE_EVENT_POLLING
    CALLBACK_DISPATCH = RUNTIME_FEATURE_CALLBACK_DISPATCH
    CACHE_SCHEMA = RUNTIME_FEATURE_CACHE_SCHEMA
    RECOVERY = RUNTIME_FEATURE_RECOVERY
    TYPED_PAYLOAD = RUNTIME_FEATURE_TYPED_PAYLOAD
    TRANSPORT_SLOTS = RUNTIME_FEATURE_TRANSPORT_SLOTS
    BATCH_POLLING = RUNTIME_FEATURE_BATCH_POLLING
    CACHE_LEASE_OPS = RUNTIME_FEATURE_CACHE_LEASE_OPS
    SCHEMA_REGISTRY_HANDLES = RUNTIME_FEATURE_SCHEMA_REGISTRY_HANDLES
    BUFFER_HANDLES = RUNTIME_FEATURE_BUFFER_HANDLES
    EXECUTABLE_RESUME = RUNTIME_FEATURE_EXECUTABLE_RESUME
    PREVIEW4_CONTROL_EVENTS = RUNTIME_FEATURE_PREVIEW4_CONTROL_EVENTS
    PREVIEW4_OBJECT_CACHE_EVENTS = RUNTIME_FEATURE_PREVIEW4_OBJECT_CACHE_EVENTS
    PREVIEW4_RUNTIME_FRAME_SEND = RUNTIME_FEATURE_PREVIEW4_RUNTIME_FRAME_SEND


RUNTIME_CONTROL_FEATURE_FLAGS = (
    NativeRuntimeFeatureFlag.CLIENT_API
    | NativeRuntimeFeatureFlag.SERVER_API
    | NativeRuntimeFeatureFlag.EVENT_POLLING
    | NativeRuntimeFeatureFlag.CALLBACK_DISPATCH
    | NativeRuntimeFeatureFlag.BATCH_POLLING
    | NativeRuntimeFeatureFlag.PREVIEW4_CONTROL_EVENTS
    | NativeRuntimeFeatureFlag.PREVIEW4_RUNTIME_FRAME_SEND
)
RUNTIME_OBJECT_FEATURE_FLAGS = (
    NativeRuntimeFeatureFlag.CACHE_SCHEMA
    | NativeRuntimeFeatureFlag.TYPED_PAYLOAD
    | NativeRuntimeFeatureFlag.CACHE_LEASE_OPS
    | NativeRuntimeFeatureFlag.SCHEMA_REGISTRY_HANDLES
    | NativeRuntimeFeatureFlag.BUFFER_HANDLES
    | NativeRuntimeFeatureFlag.PREVIEW4_OBJECT_CACHE_EVENTS
    | NativeRuntimeFeatureFlag.PREVIEW4_RUNTIME_FRAME_SEND
)
_RUNTIME_FEATURE_FLAG_NAMES = {
    NativeRuntimeFeatureFlag.PROTOCOL_CORE: "protocol_core",
    NativeRuntimeFeatureFlag.CLIENT_API: "client_api",
    NativeRuntimeFeatureFlag.SERVER_API: "server_api",
    NativeRuntimeFeatureFlag.EVENT_POLLING: "event_polling",
    NativeRuntimeFeatureFlag.CALLBACK_DISPATCH: "callback_dispatch",
    NativeRuntimeFeatureFlag.CACHE_SCHEMA: "cache_schema",
    NativeRuntimeFeatureFlag.RECOVERY: "recovery",
    NativeRuntimeFeatureFlag.TYPED_PAYLOAD: "typed_payload",
    NativeRuntimeFeatureFlag.TRANSPORT_SLOTS: "transport_slots",
    NativeRuntimeFeatureFlag.BATCH_POLLING: "batch_polling",
    NativeRuntimeFeatureFlag.CACHE_LEASE_OPS: "cache_lease_ops",
    NativeRuntimeFeatureFlag.SCHEMA_REGISTRY_HANDLES: "schema_registry_handles",
    NativeRuntimeFeatureFlag.BUFFER_HANDLES: "buffer_handles",
    NativeRuntimeFeatureFlag.EXECUTABLE_RESUME: "executable_resume",
    NativeRuntimeFeatureFlag.PREVIEW4_CONTROL_EVENTS: "preview4_control_events",
    NativeRuntimeFeatureFlag.PREVIEW4_OBJECT_CACHE_EVENTS: "preview4_object_cache_events",
    NativeRuntimeFeatureFlag.PREVIEW4_RUNTIME_FRAME_SEND: "preview4_runtime_frame_send",
}
SCHEMA_REGISTRY_ACTION_INSTALLED = 0
SCHEMA_REGISTRY_ACTION_ALREADY_INSTALLED = 1
SCHEMA_REGISTRY_ACTION_UPDATED = 2
SCHEMA_REGISTRY_ACTION_INVALIDATED = 3
CACHE_LEASE_OUTCOME_VALID = 0
CACHE_LEASE_OUTCOME_MISS = 1
CACHE_LEASE_OUTCOME_EXPIRED = 2
CACHE_LEASE_OUTCOME_RELEASED = 3
SESSION_RECOVERY_OUTCOME_FRESH = 0
SESSION_RECOVERY_OUTCOME_RESUME_ENABLED = 1
SESSION_RECOVERY_OUTCOME_RESUMED = 2
SESSION_RECOVERY_OUTCOME_RESUME_REJECTED = 3
REQUIRED_RUNTIME_FEATURES = (
    RUNTIME_FEATURE_PROTOCOL_CORE
    | RUNTIME_FEATURE_CLIENT_API
    | RUNTIME_FEATURE_SERVER_API
    | RUNTIME_FEATURE_EVENT_POLLING
    | RUNTIME_FEATURE_CALLBACK_DISPATCH
    | RUNTIME_FEATURE_CACHE_SCHEMA
    | RUNTIME_FEATURE_RECOVERY
    | RUNTIME_FEATURE_TYPED_PAYLOAD
    | RUNTIME_FEATURE_TRANSPORT_SLOTS
    | RUNTIME_FEATURE_BATCH_POLLING
    | RUNTIME_FEATURE_CACHE_LEASE_OPS
    | RUNTIME_FEATURE_SCHEMA_REGISTRY_HANDLES
    | RUNTIME_FEATURE_BUFFER_HANDLES
    | RUNTIME_FEATURE_EXECUTABLE_RESUME
    | RUNTIME_FEATURE_PREVIEW4_CONTROL_EVENTS
    | RUNTIME_FEATURE_PREVIEW4_OBJECT_CACHE_EVENTS
    | RUNTIME_FEATURE_PREVIEW4_RUNTIME_FRAME_SEND
)
REQUIRED_TRANSPORT_SLOTS = TRANSPORT_SLOT_TCP
FFI_STATUS_OK = 0
FFI_STATUS_INVALID_ARGUMENT = 1
FFI_STATUS_INVALID_HANDLE = 2
FFI_STATUS_INVALID_STATE = 3
FFI_STATUS_PROTOCOL_ERROR = 4
FFI_STATUS_WOULD_BLOCK = 5
FFI_STATUS_CALLBACK_REJECTED = 6
FFI_STATUS_INTERNAL_ERROR = 0xFFFF
ERROR_FAMILY_NONE = 0
ERROR_FAMILY_SESSION = 1
ERROR_FAMILY_CACHE = 2
ERROR_FAMILY_SCHEMA = 3
ERROR_FAMILY_TRANSPORT = 4
ERROR_FAMILY_LIFECYCLE = 5
ERROR_FAMILY_OPERATION = 6
ERROR_FAMILY_INTERNAL = 0xFFFF
SESSION_ERROR_NONE = 0x00000000
SESSION_ERROR_AUTH_FAILED = 0x00010001
SESSION_ERROR_PROFILE_UNSUPPORTED = 0x00010002
SESSION_ERROR_SCHEMA_UNSUPPORTED = 0x00010003
SESSION_ERROR_PRIORITY_REJECTED = 0x00010004
SESSION_ERROR_LEASE_POLICY_REJECTED = 0x00010005
SESSION_ERROR_RESUME_REJECTED = 0x00010006
SESSION_ERROR_LIMIT_REACHED = 0x00010007
CACHE_ERROR_NONE = 0x00030000
CACHE_ERROR_MISS = 0x00030001
CACHE_ERROR_LEASE_EXPIRED = 0x00030002
CACHE_ERROR_VERSION_MISMATCH = 0x00030003
CACHE_ERROR_DEPENDENCY_INVALID = 0x00030004
CACHE_ERROR_SCHEMA_MISMATCH = 0x00030005
SCHEMA_ERROR_NONE = 0x00040000
SCHEMA_ERROR_UNKNOWN = 0x00040001
SCHEMA_ERROR_VERSION_UNKNOWN = 0x00040002
SCHEMA_ERROR_HASH_CONFLICT = 0x00040003
SCHEMA_ERROR_INCOMPATIBLE = 0x00040004
SCHEMA_ERROR_DEPENDENCY_MISSING = 0x00040005
SCHEMA_ERROR_UPDATE_REJECTED = 0x00040006
HANDLE_KIND_INVALID = 0
HANDLE_KIND_CONNECTION = 1
HANDLE_KIND_SESSION = 2
HANDLE_KIND_OPERATION = 3
HANDLE_KIND_EVENT_PUMP = 4
HANDLE_KIND_BUFFER = 5
HANDLE_KIND_SCHEMA_REGISTRY = 6
HANDLE_KIND_CACHE_LEASE = 7
HANDLE_KIND_OBJECT_DESCRIPTOR = 8
HANDLE_KIND_CACHE_REFERENCE_DESCRIPTOR = 9
HANDLE_KIND_TRANSPORT_CONNECTION = 10
HANDLE_KIND_TRANSPORT_LISTENER = 11
HANDLE_KIND_TRANSPORT_SECURITY_CONFIG = 12
HANDLE_KIND_SERVER_ACCEPT = 13
EVENT_KIND_NONE = 0
EVENT_KIND_CONNECTION_OPENED = 1
EVENT_KIND_SESSION_OPENED = 2
EVENT_KIND_SESSION_PATCHED = 3
EVENT_KIND_SESSION_CLOSED = 4
EVENT_KIND_SUBMIT_ACCEPTED = 5
EVENT_KIND_RESULT_PUSHED = 6
EVENT_KIND_RESULT_DROPPED = 7
EVENT_KIND_FLOW_UPDATED = 8
EVENT_KIND_CONTROL = 9
EVENT_KIND_ERROR = 10
EVENT_KIND_RESULT_HINT = 11
EVENT_KIND_PARTIAL_RESULT = 12
EVENT_KIND_RUNTIME_FRAME = 13
EVENT_KIND_OPERATION_LIFECYCLE = 14
DEFAULT_ARTIFACT_ROOT_ENV = "NNRP_NATIVE_ARTIFACT_ROOT"
_CallbackEventT = TypeVar("_CallbackEventT")


class NativeArtifactError(RuntimeError):
    """Raised when a native artifact cannot be resolved, loaded, or accepted."""

    def __init__(
        self,
        message: str,
        *,
        candidates: tuple[NativeTransportCandidateDiagnostic, ...] = (),
    ) -> None:
        super().__init__(message)
        self.candidates = candidates


@dataclass(frozen=True)
class NativePlatform:
    os_name: str
    arch: str

    @property
    def tag(self) -> str:
        return f"{self.os_name}-{self.arch}"


@dataclass(frozen=True)
class NativeProbeResult:
    artifact_path: Path
    abi_major: int
    abi_minor: int
    abi_patch: int
    protocol_major: int
    protocol_wire_format: int
    sdk_major: int
    sdk_minor: int
    sdk_patch: int
    sdk_channel: int
    sdk_revision: int
    transport_slots: int
    feature_flags: int

    @property
    def feature_flag_names(self) -> tuple[str, ...]:
        return native_runtime_feature_flag_names(self.feature_flags)

    @property
    def runtime_control_feature_names(self) -> tuple[str, ...]:
        return native_runtime_feature_flag_names(self.feature_flags, mask=RUNTIME_CONTROL_FEATURE_FLAGS)

    @property
    def runtime_object_feature_names(self) -> tuple[str, ...]:
        return native_runtime_feature_flag_names(self.feature_flags, mask=RUNTIME_OBJECT_FEATURE_FLAGS)

    @property
    def has_runtime_control_features(self) -> bool:
        return native_runtime_feature_flags_available(self.feature_flags, RUNTIME_CONTROL_FEATURE_FLAGS)

    @property
    def has_runtime_object_features(self) -> bool:
        return native_runtime_feature_flags_available(self.feature_flags, RUNTIME_OBJECT_FEATURE_FLAGS)


@dataclass(frozen=True)
class NativeTransportProviderCost:
    model_id: int
    units: int

    def __post_init__(self) -> None:
        _require_bounded_integer("model_id", self.model_id, 0xFFFF)
        _require_bounded_integer("units", self.units, 0xFFFFFFFFFFFFFFFF)
        if self.model_id == 0 and self.units != 0:
            raise ValueError("units must be zero when model_id is zero")


@dataclass(frozen=True)
class NativeTransportProviderLimits:
    max_frame_bytes: int

    def __post_init__(self) -> None:
        _require_bounded_integer("max_frame_bytes", self.max_frame_bytes, 0xFFFFFFFFFFFFFFFF)
        if self.max_frame_bytes == 0:
            raise ValueError("max_frame_bytes must be greater than zero")


class NativeTransportProviderLimitation(StrEnum):
    REQUIRES_UDP = "requires-udp"
    REQUIRES_TCP = "requires-tcp"
    LOCAL_HOST_ONLY = "local-host-only"
    NATIVE_HOST_ONLY = "native-host-only"
    BROWSER_HOST_ONLY = "browser-host-only"
    UNIX_DOMAIN_SOCKET = "unix-domain-socket"
    WINDOWS_NAMED_PIPE = "windows-named-pipe"


@dataclass(frozen=True)
class NativeTransportProviderMetadata:
    id: str
    cost: NativeTransportProviderCost
    preference_rank: int
    limits: NativeTransportProviderLimits
    limitations: tuple[NativeTransportProviderLimitation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id or not self.id.isascii():
            raise ValueError("id must be a non-empty ASCII string")
        if not isinstance(self.cost, NativeTransportProviderCost):
            raise ValueError("cost must be a NativeTransportProviderCost")
        _require_bounded_integer("preference_rank", self.preference_rank, 0xFFFF)
        if not isinstance(self.limits, NativeTransportProviderLimits):
            raise ValueError("limits must be a NativeTransportProviderLimits")
        if not isinstance(self.limitations, tuple):
            raise ValueError("limitations must be a tuple")
        if any(not isinstance(value, NativeTransportProviderLimitation) for value in self.limitations):
            raise ValueError("limitations must contain NativeTransportProviderLimitation values")
        if len(set(self.limitations)) != len(self.limitations):
            raise ValueError("limitations must not contain duplicates")


@dataclass(frozen=True)
class NativeTransportProvider:
    name: str
    artifact_path: Path
    manifest_path: Path
    transport_slots: tuple[str, ...]
    enabled_features: tuple[str, ...]
    package: str
    transport_scope: str
    platform_tag: str
    metadata: NativeTransportProviderMetadata


@dataclass(frozen=True)
class NativeTransportCandidateReadiness:
    transport_id: TransportId
    provider_id: str
    route_resolved: bool
    security_satisfied: bool
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if self.transport_id not in NATIVE_TRANSPORT_NAME_BY_ID:
            raise ValueError("transport_id must identify a selectable transport")
        if not isinstance(self.provider_id, str) or not self.provider_id or not self.provider_id.isascii():
            raise ValueError("provider_id must be a non-empty ASCII string")
        if not isinstance(self.route_resolved, bool):
            raise ValueError("route_resolved must be a bool")
        if not isinstance(self.security_satisfied, bool):
            raise ValueError("security_satisfied must be a bool")
        if self.diagnostic is not None and not isinstance(self.diagnostic, str):
            raise ValueError("diagnostic must be a string or None")

    @classmethod
    def ready(cls, provider: NativeTransportProvider) -> NativeTransportCandidateReadiness:
        return cls(
            transport_id=NATIVE_TRANSPORT_ID_BY_NAME[provider.name],
            provider_id=provider.metadata.id,
            route_resolved=True,
            security_satisfied=True,
        )

    @classmethod
    def route_unresolved(
        cls,
        provider: NativeTransportProvider,
        diagnostic: str,
    ) -> NativeTransportCandidateReadiness:
        return cls(
            transport_id=NATIVE_TRANSPORT_ID_BY_NAME[provider.name],
            provider_id=provider.metadata.id,
            route_resolved=False,
            security_satisfied=True,
            diagnostic=diagnostic,
        )

    @classmethod
    def security_unsatisfied(
        cls,
        provider: NativeTransportProvider,
        diagnostic: str,
    ) -> NativeTransportCandidateReadiness:
        return cls(
            transport_id=NATIVE_TRANSPORT_ID_BY_NAME[provider.name],
            provider_id=provider.metadata.id,
            route_resolved=True,
            security_satisfied=False,
            diagnostic=diagnostic,
        )


@dataclass(frozen=True)
class NativeTransportClientSecurity:
    server_name: str
    trusted_certificate_der: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.server_name, str) or not self.server_name:
            raise ValueError("server_name must be non-empty")
        if not isinstance(self.trusted_certificate_der, bytes) or not self.trusted_certificate_der:
            raise ValueError("trusted_certificate_der must be non-empty")


@dataclass(frozen=True)
class NativeTransportServerSecurity:
    certificate_der: bytes
    private_key_pkcs8_der: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.certificate_der, bytes) or not self.certificate_der:
            raise ValueError("certificate_der must be non-empty")
        if not isinstance(self.private_key_pkcs8_der, bytes) or not self.private_key_pkcs8_der:
            raise ValueError("private_key_pkcs8_der must be non-empty")


@dataclass(frozen=True)
class NnrpEndpoint:
    uri: str
    scheme: str
    authority: str
    path: str
    query: str
    secure: bool = False

    @classmethod
    def from_uri(cls, uri: str) -> NnrpEndpoint:
        return parse_nnrp_endpoint(uri)


@dataclass(frozen=True)
class NnrpEndpointSupport:
    endpoint: NnrpEndpoint
    selection: NativeTransportSelection | None
    available: bool
    skip_reason: str | None = None
    diagnostic: str | None = None


@dataclass(frozen=True)
class NativeTransportEndpoint:
    uri: str
    scheme: str
    transport_name: str
    transport_id: TransportId
    address: str
    secure: bool = False

    @classmethod
    def from_uri(cls, uri: str) -> NativeTransportEndpoint:
        return parse_native_transport_endpoint(uri)


@dataclass(frozen=True)
class NativeTransportEndpointSupport:
    endpoint: NativeTransportEndpoint
    provider: NativeTransportProvider | None
    available: bool
    skip_reason: str | None = None
    diagnostic: str | None = None


@dataclass(frozen=True)
class NativeTransportProbeSample:
    provider_id: str
    transport_name: str
    elapsed_us: int
    rtt_us: int | None = None
    bytes_sent: int = 0
    bytes_received: int = 0
    timed_out: bool = False
    failed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id or not self.provider_id.isascii():
            raise ValueError("provider_id must be a non-empty ASCII string")
        if not isinstance(self.transport_name, str):
            raise ValueError("transport_name must be a canonical transport name")
        try:
            canonical_transport_name = _normalize_native_transport_scope(self.transport_name)
        except NativeArtifactError as error:
            raise ValueError("transport_name must be a canonical transport name") from error
        if canonical_transport_name != self.transport_name:
            raise ValueError("transport_name must be a canonical transport name")
        _require_bounded_integer("elapsed_us", self.elapsed_us, 0xFFFFFFFFFFFFFFFF)
        if self.rtt_us is not None:
            _require_bounded_integer("rtt_us", self.rtt_us, 0xFFFFFFFFFFFFFFFF)
        _require_bounded_integer("bytes_sent", self.bytes_sent, 0xFFFFFFFFFFFFFFFF)
        _require_bounded_integer("bytes_received", self.bytes_received, 0xFFFFFFFFFFFFFFFF)


class NativeTransportProbeState(StrEnum):
    NOT_RUN = "not-run"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    MISSING = "missing"


@dataclass(frozen=True)
class NativeTransportProbeMetrics:
    sample_count: int
    success_count: int
    median_throughput_bytes_per_sec: int
    median_rtt_us: int

    def __post_init__(self) -> None:
        _require_bounded_integer("sample_count", self.sample_count, 0xFFFFFFFF)
        _require_bounded_integer("success_count", self.success_count, 0xFFFFFFFF)
        if self.sample_count == 0:
            raise ValueError("sample_count must be positive")
        if not 1 <= self.success_count <= self.sample_count:
            raise ValueError("success_count must be in 1..sample_count")
        _require_bounded_integer(
            "median_throughput_bytes_per_sec",
            self.median_throughput_bytes_per_sec,
            0xFFFFFFFFFFFFFFFF,
        )
        _require_bounded_integer("median_rtt_us", self.median_rtt_us, 0xFFFFFFFFFFFFFFFF)


@dataclass(frozen=True)
class NativeTransportProbeObservation:
    transport_id: TransportId
    provider_id: str
    state: NativeTransportProbeState
    metrics: NativeTransportProbeMetrics | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if self.transport_id not in NATIVE_TRANSPORT_NAME_BY_ID:
            raise ValueError("transport_id must identify a selectable transport")
        if not isinstance(self.provider_id, str) or not self.provider_id or not self.provider_id.isascii():
            raise ValueError("provider_id must be a non-empty ASCII string")
        if self.state not in {NativeTransportProbeState.SUCCEEDED, NativeTransportProbeState.FAILED}:
            raise ValueError("state must be SUCCEEDED or FAILED")
        if self.state is NativeTransportProbeState.SUCCEEDED and self.metrics is None:
            raise ValueError("SUCCEEDED observations require metrics")
        if self.metrics is not None and not isinstance(self.metrics, NativeTransportProbeMetrics):
            raise ValueError("metrics must be a NativeTransportProbeMetrics or None")
        if self.state is NativeTransportProbeState.FAILED and self.metrics is not None:
            raise ValueError("FAILED observations forbid metrics")
        if self.diagnostic is not None and not isinstance(self.diagnostic, str):
            raise ValueError("diagnostic must be a string or None")

    @classmethod
    def succeeded(
        cls,
        provider: NativeTransportProvider,
        metrics: NativeTransportProbeMetrics,
    ) -> NativeTransportProbeObservation:
        return cls(
            transport_id=NATIVE_TRANSPORT_ID_BY_NAME[provider.name],
            provider_id=provider.metadata.id,
            state=NativeTransportProbeState.SUCCEEDED,
            metrics=metrics,
        )

    @classmethod
    def failed(
        cls,
        provider: NativeTransportProvider,
        diagnostic: str,
    ) -> NativeTransportProbeObservation:
        return cls(
            transport_id=NATIVE_TRANSPORT_ID_BY_NAME[provider.name],
            provider_id=provider.metadata.id,
            state=NativeTransportProbeState.FAILED,
            diagnostic=diagnostic,
        )


class NativeTransportRejectionReason(StrEnum):
    POLICY_DISALLOWED = "policy-disallowed"
    LOCAL_UNAVAILABLE = "local-unavailable"
    PEER_UNSUPPORTED = "peer-unsupported"
    LIMIT_EXCEEDED = "limit-exceeded"
    ROUTE_UNRESOLVED = "route-unresolved"
    SECURITY_UNSATISFIED = "security-unsatisfied"
    PROBE_MISSING = "probe-missing"
    PROBE_FAILED = "probe-failed"


@dataclass(frozen=True)
class NativeTransportCandidateDiagnostic:
    transport_name: str
    transport_id: TransportId
    provider: NativeTransportProviderMetadata
    local_available: bool
    peer_supported: bool
    within_limits: bool
    probe_state: NativeTransportProbeState
    probe: NativeTransportProbeMetrics | None = None
    selection_rank: int | None = None
    rejection_reason: NativeTransportRejectionReason | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if _normalize_native_transport_scope(self.transport_name) != self.transport_name:
            raise ValueError("transport_name must use the canonical transport name")
        if NATIVE_TRANSPORT_ID_BY_NAME[self.transport_name] is not self.transport_id:
            raise ValueError("transport_id does not match transport_name")
        if not isinstance(self.provider, NativeTransportProviderMetadata):
            raise ValueError("provider must be a NativeTransportProviderMetadata")
        if self.probe_state is NativeTransportProbeState.SUCCEEDED and self.probe is None:
            raise ValueError("succeeded probe_state requires probe metrics")
        if self.probe_state is not NativeTransportProbeState.SUCCEEDED and self.probe is not None:
            raise ValueError("probe metrics require succeeded probe_state")
        if self.selection_rank is not None:
            _require_bounded_integer("selection_rank", self.selection_rank, 0xFFFFFFFF)
            if self.rejection_reason is not None:
                raise ValueError("rejected candidates must not have selection_rank")


@dataclass(frozen=True)
class NativeTransportSelection:
    selected_provider: NativeTransportProvider
    candidates: tuple[NativeTransportCandidateDiagnostic, ...]
    policy: TransportPolicy
    diagnostic: str | None = None

    @property
    def selected_transport_name(self) -> str:
        return self.selected_provider.name

    @property
    def selected_transport_id(self) -> TransportId:
        return NATIVE_TRANSPORT_ID_BY_NAME[self.selected_provider.name]


class NativeTransportSelectionErrorCode(StrEnum):
    INVALID_EVIDENCE = "invalid-evidence"
    FORCED_TRANSPORT_UNAVAILABLE = "forced-transport-unavailable"
    NO_VIABLE_TRANSPORT = "no-viable-transport"


class NativeTransportSelectionError(NativeArtifactError):
    """Raised when transport evidence is invalid or cannot produce a selection."""

    def __init__(
        self,
        code: NativeTransportSelectionErrorCode,
        diagnostic: str,
        *,
        policy: TransportPolicy | None = None,
        candidates: tuple[NativeTransportCandidateDiagnostic, ...] = (),
    ) -> None:
        super().__init__(diagnostic, candidates=candidates)
        self.code = code
        self.policy = policy
        self.diagnostic = diagnostic


class NativeHandleError(ValueError):
    """Raised when an FFI handle or buffer view violates the native ABI contract."""


@dataclass(frozen=True, slots=True)
class NativeStatus:
    status_code: int
    error_family: int = ERROR_FAMILY_NONE
    protocol_error_code: int = 0
    detail_code: int = 0

    def __post_init__(self) -> None:
        _validate_u32("status_code", self.status_code)
        _validate_u32("error_family", self.error_family)
        _validate_u32("protocol_error_code", self.protocol_error_code)
        _validate_u32("detail_code", self.detail_code)

    @classmethod
    def ok(cls) -> NativeStatus:
        return _NATIVE_STATUS_OK

    @classmethod
    def from_ffi(cls, status: _NnrpFfiStatus) -> NativeStatus:
        if (
            status.status_code == FFI_STATUS_OK
            and status.error_family == ERROR_FAMILY_NONE
            and status.protocol_error_code == 0
            and status.detail_code == 0
        ):
            return _NATIVE_STATUS_OK
        return cls(
            int(status.status_code),
            int(status.error_family),
            int(status.protocol_error_code),
            int(status.detail_code),
        )

    @property
    def succeeded(self) -> bool:
        return self.status_code == FFI_STATUS_OK

    @property
    def status_name(self) -> str:
        return _STATUS_NAMES.get(self.status_code, "unknown")

    @property
    def error_family_name(self) -> str:
        return _ERROR_FAMILY_NAMES.get(self.error_family, "unknown")

    @property
    def protocol_error_name(self) -> str:
        return _PROTOCOL_ERROR_NAMES.get(self.protocol_error_code, "unknown")

    @property
    def is_protocol_error(self) -> bool:
        return self.status_code == FFI_STATUS_PROTOCOL_ERROR

    @property
    def is_session_error(self) -> bool:
        return self.error_family == ERROR_FAMILY_SESSION

    @property
    def is_cache_error(self) -> bool:
        return self.error_family == ERROR_FAMILY_CACHE

    @property
    def is_schema_error(self) -> bool:
        return self.error_family == ERROR_FAMILY_SCHEMA

    @property
    def is_retryable(self) -> bool:
        return self.status_code == FFI_STATUS_WOULD_BLOCK or self.protocol_error_code in _RETRYABLE_PROTOCOL_ERRORS

    @property
    def is_downgrade(self) -> bool:
        return self.is_session_error and self.protocol_error_code == SESSION_ERROR_PRIORITY_REJECTED

    def to_ffi(self) -> _NnrpFfiStatus:
        return _NnrpFfiStatus(self.status_code, self.error_family, self.protocol_error_code, self.detail_code)


_NATIVE_STATUS_OK = object.__new__(NativeStatus)
object.__setattr__(_NATIVE_STATUS_OK, "status_code", FFI_STATUS_OK)
object.__setattr__(_NATIVE_STATUS_OK, "error_family", ERROR_FAMILY_NONE)
object.__setattr__(_NATIVE_STATUS_OK, "protocol_error_code", 0)
object.__setattr__(_NATIVE_STATUS_OK, "detail_code", 0)


class NativeRuntimeError(RuntimeError):
    """Base exception for non-OK Rust FFI status results."""

    def __init__(self, status: NativeStatus, message: str | None = None) -> None:
        self.status = status
        super().__init__(message or _format_status_message(status))


class NativeInvalidArgumentError(NativeRuntimeError):
    """Raised when Rust FFI rejects invalid caller-owned input."""


class NativeInvalidHandleError(NativeRuntimeError):
    """Raised when Rust FFI rejects a stale, wrong-kind, or unknown handle."""


class NativeInvalidStateError(NativeRuntimeError):
    """Raised when Rust FFI rejects an operation for the current runtime state."""


class NativeProtocolError(NativeRuntimeError):
    """Raised when Rust FFI reports a protocol-family error."""


class NativeWouldBlockError(NativeRuntimeError):
    """Raised when Rust FFI has no event/result available for a non-blocking call."""


class NativeCallbackRejectedError(NativeRuntimeError):
    """Raised when a callback sink rejects a Rust FFI event."""


class NativeInternalError(NativeRuntimeError):
    """Raised when Rust FFI reports an internal failure."""


class _NativeSubmitWaitCancelled(RuntimeError):
    """Stop a worker-thread submit wait after caller cancellation."""


@dataclass(frozen=True, slots=True)
class NativeHandle:
    kind: int
    id: int
    generation: int
    flags: int = 0

    def __post_init__(self) -> None:
        _validate_u32("kind", self.kind)
        _validate_u64("id", self.id)
        _validate_u32("generation", self.generation)
        _validate_u32("flags", self.flags)
        if self.kind == HANDLE_KIND_INVALID:
            if self.id != 0 or self.generation != 0 or self.flags != 0:
                raise NativeHandleError("invalid handles must use zero id, generation, and flags")
            return
        if self.id == 0 or self.generation == 0:
            raise NativeHandleError("native handles require non-zero id and generation")

    @classmethod
    def invalid(cls) -> NativeHandle:
        return cls(HANDLE_KIND_INVALID, 0, 0, 0)

    @classmethod
    def from_ffi(cls, handle: _NnrpHandle) -> NativeHandle:
        return cls(int(handle.kind), int(handle.id), int(handle.generation), int(handle.flags))

    @property
    def is_valid(self) -> bool:
        return self.kind != HANDLE_KIND_INVALID

    def require_kind(self, expected_kind: int) -> None:
        if self.kind != expected_kind:
            raise NativeHandleError(f"expected native handle kind {expected_kind}, got {self.kind}")

    def to_ffi(self) -> _NnrpHandle:
        return _NnrpHandle(self.kind, self.id, self.generation, self.flags)


def _native_handle_from_trusted_ffi(handle: _NnrpHandle) -> NativeHandle:
    native_handle = object.__new__(NativeHandle)
    object.__setattr__(native_handle, "kind", int(handle.kind))
    object.__setattr__(native_handle, "id", int(handle.id))
    object.__setattr__(native_handle, "generation", int(handle.generation))
    object.__setattr__(native_handle, "flags", int(handle.flags))
    return native_handle


@dataclass(frozen=True)
class NativeConnectionHandle:
    handle: NativeHandle

    def __post_init__(self) -> None:
        self.handle.require_kind(HANDLE_KIND_CONNECTION)

    @classmethod
    def from_ffi(cls, handle: _NnrpHandle) -> NativeConnectionHandle:
        return cls(NativeHandle.from_ffi(handle))

    def to_ffi(self) -> _NnrpHandle:
        return self.handle.to_ffi()


@dataclass(frozen=True)
class NativeSessionHandle:
    handle: NativeHandle

    def __post_init__(self) -> None:
        self.handle.require_kind(HANDLE_KIND_SESSION)

    @classmethod
    def from_ffi(cls, handle: _NnrpHandle) -> NativeSessionHandle:
        return cls(NativeHandle.from_ffi(handle))

    def to_ffi(self) -> _NnrpHandle:
        return self.handle.to_ffi()


@dataclass(frozen=True)
class NativeOperationHandle:
    handle: NativeHandle

    def __post_init__(self) -> None:
        self.handle.require_kind(HANDLE_KIND_OPERATION)

    @classmethod
    def from_ffi(cls, handle: _NnrpHandle) -> NativeOperationHandle:
        return cls(NativeHandle.from_ffi(handle))

    def to_ffi(self) -> _NnrpHandle:
        return self.handle.to_ffi()


@dataclass(frozen=True)
class NativeEventPumpHandle:
    handle: NativeHandle

    def __post_init__(self) -> None:
        self.handle.require_kind(HANDLE_KIND_EVENT_PUMP)

    @classmethod
    def from_ffi(cls, handle: _NnrpHandle) -> NativeEventPumpHandle:
        return cls(NativeHandle.from_ffi(handle))

    def to_ffi(self) -> _NnrpHandle:
        return self.handle.to_ffi()


@dataclass(frozen=True)
class NativeBufferHandle:
    handle: NativeHandle

    def __post_init__(self) -> None:
        self.handle.require_kind(HANDLE_KIND_BUFFER)

    @classmethod
    def from_ffi(cls, handle: _NnrpHandle) -> NativeBufferHandle:
        return cls(NativeHandle.from_ffi(handle))

    def to_ffi(self) -> _NnrpHandle:
        return self.handle.to_ffi()


@dataclass(frozen=True)
class NativeSchemaRegistryHandle:
    handle: NativeHandle

    def __post_init__(self) -> None:
        self.handle.require_kind(HANDLE_KIND_SCHEMA_REGISTRY)

    @classmethod
    def from_ffi(cls, handle: _NnrpHandle) -> NativeSchemaRegistryHandle:
        return cls(NativeHandle.from_ffi(handle))

    def to_ffi(self) -> _NnrpHandle:
        return self.handle.to_ffi()


@dataclass(frozen=True)
class NativeCacheLeaseHandle:
    handle: NativeHandle

    def __post_init__(self) -> None:
        self.handle.require_kind(HANDLE_KIND_CACHE_LEASE)

    @classmethod
    def from_ffi(cls, handle: _NnrpHandle) -> NativeCacheLeaseHandle:
        return cls(NativeHandle.from_ffi(handle))

    def to_ffi(self) -> _NnrpHandle:
        return self.handle.to_ffi()


@dataclass(frozen=True)
class NativeObjectDescriptorHandle:
    handle: NativeHandle

    def __post_init__(self) -> None:
        self.handle.require_kind(HANDLE_KIND_OBJECT_DESCRIPTOR)

    @classmethod
    def from_ffi(cls, handle: _NnrpHandle) -> NativeObjectDescriptorHandle:
        return cls(NativeHandle.from_ffi(handle))

    def to_ffi(self) -> _NnrpHandle:
        return self.handle.to_ffi()


@dataclass(frozen=True)
class NativeBufferView:
    ptr: int
    length: int

    def __post_init__(self) -> None:
        _validate_pointer_and_length(self.ptr, self.length, detail="buffer views")

    @classmethod
    def empty(cls) -> NativeBufferView:
        return cls(0, 0)

    @classmethod
    def from_ffi(cls, view: _NnrpBufferView) -> NativeBufferView:
        return cls(_pointer_value(view.ptr), int(view.len))

    def to_ffi(self) -> _NnrpBufferView:
        return _NnrpBufferView(_void_pointer(self.ptr), self.length)


@dataclass(frozen=True)
class NativeMutableBufferView:
    ptr: int
    length: int

    def __post_init__(self) -> None:
        _validate_pointer_and_length(self.ptr, self.length, detail="mutable buffer views")

    @classmethod
    def empty(cls) -> NativeMutableBufferView:
        return cls(0, 0)

    @classmethod
    def from_ffi(cls, view: _NnrpBufferViewMut) -> NativeMutableBufferView:
        return cls(_pointer_value(view.ptr), int(view.len))

    def to_ffi(self) -> _NnrpBufferViewMut:
        return _NnrpBufferViewMut(_void_pointer(self.ptr), self.length)


@dataclass
class NativeBorrowedBufferView:
    owner: Any
    view: NativeBufferView
    _active: bool = field(default=False, init=False, repr=False, compare=False)

    def __enter__(self) -> memoryview:
        if self._active:
            raise NativeInvalidStateError(
                NativeStatus(FFI_STATUS_INVALID_STATE),
                "native borrowed buffer view is already active",
            )
        self.owner._ensure_open()
        self.owner._borrow_count += 1
        self._active = True
        try:
            return _borrow_buffer_view(self.view)
        except Exception:
            self._active = False
            self.owner._borrow_count -= 1
            raise

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._active:
            self._active = False
            self.owner._borrow_count -= 1


class _NnrpProtocolVersion(ctypes.Structure):
    _fields_ = [
        ("major", ctypes.c_uint8),
        ("wire_format", ctypes.c_uint8),
    ]


class _NnrpFfiStatus(ctypes.Structure):
    _fields_ = [
        ("status_code", ctypes.c_uint32),
        ("error_family", ctypes.c_uint32),
        ("protocol_error_code", ctypes.c_uint32),
        ("detail_code", ctypes.c_uint32),
    ]


class _NnrpHandle(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_uint32),
        ("id", ctypes.c_uint64),
        ("generation", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]


class _NnrpBufferView(ctypes.Structure):
    _fields_ = [
        ("ptr", ctypes.c_void_p),
        ("len", ctypes.c_size_t),
    ]


class _NnrpBufferViewMut(ctypes.Structure):
    _fields_ = [
        ("ptr", ctypes.c_void_p),
        ("len", ctypes.c_size_t),
    ]


class _NnrpU16Slice(ctypes.Structure):
    _fields_ = [
        ("ptr", ctypes.POINTER(ctypes.c_uint16)),
        ("len", ctypes.c_size_t),
    ]


class _NnrpU32Slice(ctypes.Structure):
    _fields_ = [
        ("ptr", ctypes.POINTER(ctypes.c_uint32)),
        ("len", ctypes.c_size_t),
    ]


class _NnrpServerPolicyDecision(ctypes.Structure):
    _fields_ = [
        ("accepted", ctypes.c_uint8),
        ("reserved0", ctypes.c_uint8 * 3),
        ("session_error_code", ctypes.c_uint32),
        ("diagnostic", _NnrpBufferView),
    ]


class _NnrpServerPolicyCompleteRequest(ctypes.Structure):
    _fields_ = [
        ("request_id", ctypes.c_uint64),
        ("decision", _NnrpServerPolicyDecision),
    ]


_NnrpServerPolicyBeginCallback = ctypes.CFUNCTYPE(
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.c_uint64,
    _NnrpBufferView,
)


class _NnrpServerPolicySink(ctypes.Structure):
    _fields_ = [
        ("user_data", ctypes.c_void_p),
        ("begin", _NnrpServerPolicyBeginCallback),
    ]


class _NnrpRuntimeCapabilities(ctypes.Structure):
    _fields_ = [
        ("abi_major", ctypes.c_uint16),
        ("abi_minor", ctypes.c_uint16),
        ("abi_patch", ctypes.c_uint16),
        ("reserved0", ctypes.c_uint16),
        ("protocol_version", _NnrpProtocolVersion),
        ("sdk_major", ctypes.c_uint16),
        ("sdk_minor", ctypes.c_uint16),
        ("sdk_patch", ctypes.c_uint16),
        ("sdk_channel", ctypes.c_uint16),
        ("sdk_revision", ctypes.c_uint16),
        ("reserved1", ctypes.c_uint16),
        ("transport_slots", ctypes.c_uint32),
        ("feature_flags", ctypes.c_uint64),
    ]


class _NnrpFfiDiagnostic(ctypes.Structure):
    _fields_ = [
        ("status", _NnrpFfiStatus),
        ("related_connection_id", ctypes.c_uint64),
        ("related_session_id", ctypes.c_uint32),
        ("related_operation_id", ctypes.c_uint64),
        ("related_frame_id", ctypes.c_uint32),
    ]


class _NnrpRuntimeFrameHeader(ctypes.Structure):
    _fields_ = [
        ("present", ctypes.c_uint8),
        ("version_major", ctypes.c_uint8),
        ("wire_format", ctypes.c_uint8),
        ("message_type", ctypes.c_uint8),
        ("flags", ctypes.c_uint32),
        ("session_id", ctypes.c_uint32),
        ("frame_id", ctypes.c_uint32),
        ("view_id", ctypes.c_uint16),
        ("route_id", ctypes.c_uint16),
        ("trace_id", ctypes.c_uint64),
    ]


class _NnrpEvent(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_uint32),
        ("header", _NnrpRuntimeFrameHeader),
        ("connection", _NnrpHandle),
        ("session", _NnrpHandle),
        ("operation", _NnrpHandle),
        ("payload_owner", _NnrpHandle),
        ("payload", _NnrpBufferView),
        ("diagnostic", _NnrpFfiDiagnostic),
    ]


_NnrpEventCallback = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(_NnrpEvent))


class _NnrpCallbackSink(ctypes.Structure):
    _fields_ = [
        ("user_data", ctypes.c_void_p),
        ("on_event", _NnrpEventCallback),
    ]


class _NnrpPollResult(ctypes.Structure):
    _fields_ = [
        ("status", _NnrpFfiStatus),
        ("has_event", ctypes.c_uint8),
        ("event", _NnrpEvent),
    ]


class _NnrpClientConnectRequest(ctypes.Structure):
    _fields_ = [
        ("connection_id", ctypes.c_uint64),
        ("generation", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
        ("transport_connection", _NnrpHandle),
    ]


class _NnrpServerBindRequest(ctypes.Structure):
    _fields_ = [
        ("server_id", ctypes.c_uint64),
        ("generation", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
        ("transport_listener", _NnrpHandle),
        ("supported_profiles", _NnrpU16Slice),
        ("supported_cache_objects", _NnrpU32Slice),
        ("max_cache_objects", ctypes.c_uint64),
        ("max_cache_object_bytes", ctypes.c_uint32),
        ("resume_token_bytes", ctypes.c_uint32),
        ("max_in_flight_operations", ctypes.c_uint16),
        ("granted_operation_credit", ctypes.c_uint16),
        ("lease_ttl_ms", ctypes.c_uint32),
        ("resume_window_ms", ctypes.c_uint32),
        ("schema_registry", _NnrpHandle),
        ("application_policy", _NnrpServerPolicySink),
    ]


class _NnrpSessionOpenRequest(ctypes.Structure):
    _fields_ = [
        ("connection", _NnrpHandle),
        ("requested_session_id", ctypes.c_uint32),
        ("session_handle_id", ctypes.c_uint64),
        ("generation", ctypes.c_uint32),
        ("profile_id", ctypes.c_uint16),
        ("priority_class", ctypes.c_uint8),
        ("allow_resume", ctypes.c_uint8),
        ("schema_id", ctypes.c_uint32),
        ("schema_version", ctypes.c_uint32),
        ("default_deadline_ms", ctypes.c_uint32),
        ("max_in_flight_operations", ctypes.c_uint16),
        ("reserved0", ctypes.c_uint16),
        ("lease_ttl_hint_ms", ctypes.c_uint32),
        ("resume_token_bytes", ctypes.c_uint32),
        ("cache_hints", _NnrpU32Slice),
    ]


class _NnrpSubmitRequest(ctypes.Structure):
    _fields_ = [
        ("session", _NnrpHandle),
        ("operation_id", ctypes.c_uint64),
        ("frame_id", ctypes.c_uint32),
        ("header_flags", ctypes.c_uint32),
        ("view_id", ctypes.c_uint16),
        ("route_id", ctypes.c_uint16),
        ("trace_id", ctypes.c_uint64),
        ("payload", _NnrpBufferView),
    ]


class _NnrpClientCancelRequest(ctypes.Structure):
    _fields_ = [
        ("session", _NnrpHandle),
        ("frame_id", ctypes.c_uint32),
    ]


class _NnrpRuntimeObjectDescriptor(ctypes.Structure):
    _fields_ = [
        ("object_id", ctypes.c_uint64),
        ("object_kind", ctypes.c_uint16),
        ("producer_role", ctypes.c_uint8),
        ("consumer_role", ctypes.c_uint8),
        ("session_id", ctypes.c_uint32),
        ("byte_size", ctypes.c_uint64),
        ("compute_cost_units", ctypes.c_uint32),
        ("memory_location_hint", ctypes.c_uint16),
        ("ownership_hint", ctypes.c_uint16),
        ("lifetime_hint_ms", ctypes.c_uint32),
        ("metadata_bytes", ctypes.c_uint32),
    ]


class _NnrpServerAcceptBeginRequest(ctypes.Structure):
    _fields_ = [
        ("server", _NnrpHandle),
        ("accept_handle_id", ctypes.c_uint64),
        ("generation", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
    ]


class _NnrpServerAcceptWaitRequest(ctypes.Structure):
    _fields_ = [
        ("accept", _NnrpHandle),
        ("timeout_ms", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]


class _NnrpServerAcceptClaimRequest(ctypes.Structure):
    _fields_ = [
        ("accept", _NnrpHandle),
        ("session_handle_id", ctypes.c_uint64),
        ("generation", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
    ]


class _NnrpServerAcceptResult(ctypes.Structure):
    _fields_ = [
        ("session", _NnrpHandle),
        ("active_transport_id", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
    ]


class _NnrpRoleEventPollRequest(ctypes.Structure):
    _fields_ = [
        ("scope", _NnrpHandle),
        ("max_events", ctypes.c_uint32),
        ("timeout_ms", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
    ]


class _NnrpServerSendResultRequest(ctypes.Structure):
    _fields_ = [
        ("operation", _NnrpHandle),
        ("payload", _NnrpBufferView),
    ]


class _NnrpRuntimeFrameSendRequest(ctypes.Structure):
    _fields_ = [
        ("handle", _NnrpHandle),
        ("message_type", ctypes.c_uint32),
        ("frame_id", ctypes.c_uint32),
        ("payload", _NnrpBufferView),
    ]


class _NnrpSchemaDescriptorHeader(ctypes.Structure):
    _fields_ = [
        ("schema_id", ctypes.c_uint32),
        ("schema_version", ctypes.c_uint32),
        ("profile_id", ctypes.c_uint16),
        ("schema_flags", ctypes.c_uint16),
        ("min_version_major", ctypes.c_uint8),
        ("max_version_major", ctypes.c_uint8),
        ("reserved0", ctypes.c_uint16),
        ("body_bytes", ctypes.c_uint32),
        ("dependency_count", ctypes.c_uint16),
        ("default_stream_semantics", ctypes.c_uint16),
        ("schema_hash", ctypes.c_uint64),
    ]


class _NnrpTypedPayloadDescriptor(ctypes.Structure):
    _fields_ = [
        ("profile_id", ctypes.c_uint16),
        ("payload_kind", ctypes.c_uint8),
        ("descriptor_flags", ctypes.c_uint8),
        ("schema_id", ctypes.c_uint32),
        ("schema_version", ctypes.c_uint32),
        ("stream_semantics", ctypes.c_uint16),
        ("reserved0", ctypes.c_uint16),
        ("offset", ctypes.c_uint32),
        ("length", ctypes.c_uint32),
    ]


class _NnrpSessionRecoveryOutcome(ctypes.Structure):
    _fields_ = [
        ("outcome_code", ctypes.c_uint32),
        ("resume_window_ms", ctypes.c_uint32),
    ]


class _NnrpCacheObjectId(ctypes.Structure):
    _fields_ = [
        ("cache_namespace", ctypes.c_uint32),
        ("object_kind", ctypes.c_uint32),
        ("cache_key_hi", ctypes.c_uint64),
        ("cache_key_lo", ctypes.c_uint64),
    ]


class _NnrpCacheLeaseRequest(ctypes.Structure):
    _fields_ = [
        ("owner", _NnrpHandle),
        ("object_id", _NnrpCacheObjectId),
        ("expected_version", ctypes.c_uint64),
        ("now_ms", ctypes.c_uint64),
        ("ttl_ms", ctypes.c_uint32),
    ]


class _NnrpCacheLeaseResult(ctypes.Structure):
    _fields_ = [
        ("outcome_code", ctypes.c_uint32),
        ("lease_handle", _NnrpHandle),
        ("object_id", _NnrpCacheObjectId),
        ("object_version", ctypes.c_uint64),
        ("lease_id", ctypes.c_uint64),
        ("owner_scope", ctypes.c_uint32),
        ("ttl_ms", ctypes.c_uint32),
        ("owner_id", ctypes.c_uint64),
        ("granted_at_ms", ctypes.c_uint64),
    ]


class _NnrpSessionResumeRequest(ctypes.Structure):
    _fields_ = [
        ("open", _NnrpSessionOpenRequest),
        ("recovery_ticket", _NnrpBufferView),
    ]


class _NnrpTransportOpenRequest(ctypes.Structure):
    _fields_ = [
        ("transport_id", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("endpoint", _NnrpBufferView),
        ("config", _NnrpHandle),
        ("max_packet_bytes", ctypes.c_uint64),
        ("timeout_ms", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
    ]


class _NnrpTransportAcceptRequest(ctypes.Structure):
    _fields_ = [
        ("listener", _NnrpHandle),
        ("timeout_ms", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
    ]


class _NnrpTransportWriteBatchRequest(ctypes.Structure):
    _fields_ = [
        ("connection", _NnrpHandle),
        ("frames", ctypes.POINTER(_NnrpBufferView)),
        ("frame_count", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
    ]


class _NnrpTransportReadBatchRequest(ctypes.Structure):
    _fields_ = [
        ("connection", _NnrpHandle),
        ("max_frames", ctypes.c_uint32),
        ("timeout_ms", ctypes.c_uint32),
        ("max_bytes", ctypes.c_uint64),
    ]


class _NnrpTransportFrameBatch(ctypes.Structure):
    _fields_ = [
        ("payload_owner", _NnrpHandle),
        ("payload", _NnrpBufferView),
        ("frame_count", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
    ]


class _NnrpTransportProbeRequest(ctypes.Structure):
    _fields_ = [
        ("open", _NnrpTransportOpenRequest),
        ("sample_count", ctypes.c_uint32),
        ("probe_payload_bytes", ctypes.c_uint32),
    ]


class _NnrpTransportProbeResult(ctypes.Structure):
    _fields_ = [
        ("sample_count", ctypes.c_uint32),
        ("success_count", ctypes.c_uint32),
        ("median_throughput_bytes_per_second", ctypes.c_uint64),
        ("median_rtt_microseconds", ctypes.c_uint64),
    ]


class _NnrpTransportClientSecurityConfigRequest(ctypes.Structure):
    _fields_ = [
        ("transport_id", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("server_name", _NnrpBufferView),
        ("trusted_certificate_der", _NnrpBufferView),
    ]


class _NnrpTransportServerSecurityConfigRequest(ctypes.Structure):
    _fields_ = [
        ("transport_id", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("certificate_der", _NnrpBufferView),
        ("private_key_pkcs8_der", _NnrpBufferView),
    ]


class NativeRuntimeEntrypoints:
    """ctypes entrypoint table for the frozen Rust runtime ABI."""

    def __init__(
        self,
        library: Any,
        *,
        artifact_path: Path | None = None,
    ) -> None:
        self.artifact_path = artifact_path
        self.current_protocol_version = _bind_native_function(
            library, "nnrp_current_protocol_version", _NnrpProtocolVersion, []
        )
        self.runtime_capabilities = _bind_native_function(
            library, "nnrp_runtime_capabilities", _NnrpRuntimeCapabilities, []
        )
        self.client_connect = _bind_native_function(
            library,
            "nnrp_client_connect",
            _NnrpFfiStatus,
            [_NnrpClientConnectRequest, ctypes.POINTER(_NnrpHandle)],
        )
        self.session_open = _bind_native_function(
            library,
            "nnrp_session_open",
            _NnrpFfiStatus,
            [_NnrpSessionOpenRequest, ctypes.POINTER(_NnrpHandle)],
        )
        self.client_open_session = _bind_native_function(
            library,
            "nnrp_client_open_session",
            _NnrpFfiStatus,
            [_NnrpSessionOpenRequest, ctypes.POINTER(_NnrpHandle)],
        )
        self.client_resume_session = _bind_native_function(
            library,
            "nnrp_client_resume_session",
            _NnrpFfiStatus,
            [_NnrpSessionResumeRequest, ctypes.POINTER(_NnrpHandle), ctypes.POINTER(_NnrpSessionRecoveryOutcome)],
        )
        self.client_session_recovery_ticket = _bind_native_function(
            library,
            "nnrp_client_session_recovery_ticket",
            _NnrpFfiStatus,
            [_NnrpHandle, ctypes.POINTER(_NnrpHandle), ctypes.POINTER(_NnrpBufferView)],
        )
        self.submit = _bind_native_function(
            library,
            "nnrp_submit",
            _NnrpFfiStatus,
            [_NnrpSubmitRequest, ctypes.POINTER(_NnrpHandle)],
        )
        self.client_submit = _bind_native_function(
            library,
            "nnrp_client_submit",
            _NnrpFfiStatus,
            [_NnrpSubmitRequest, ctypes.POINTER(_NnrpHandle)],
        )
        self.session_close = _bind_native_function(library, "nnrp_session_close", _NnrpFfiStatus, [_NnrpHandle])
        self.client_close = _bind_native_function(library, "nnrp_client_close", _NnrpFfiStatus, [_NnrpHandle])
        self.client_close_connection = _bind_native_function(
            library, "nnrp_client_close_connection", _NnrpFfiStatus, [_NnrpHandle]
        )
        self.client_cancel = _bind_native_function(
            library, "nnrp_client_cancel", _NnrpFfiStatus, [_NnrpClientCancelRequest]
        )
        self.client_await_event = _bind_native_function(
            library,
            "nnrp_client_await_event",
            _NnrpFfiStatus,
            [_NnrpHandle, ctypes.POINTER(_NnrpPollResult)],
        )
        self.client_await_events = _bind_native_function(
            library,
            "nnrp_client_await_events",
            _NnrpFfiStatus,
            [_NnrpRoleEventPollRequest, ctypes.POINTER(_NnrpEvent), ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)],
        )
        self.server_bind = _bind_native_function(
            library,
            "nnrp_server_bind",
            _NnrpFfiStatus,
            [_NnrpServerBindRequest, ctypes.POINTER(_NnrpHandle)],
        )
        self.server_policy_complete = _bind_native_function(
            library,
            "nnrp_server_policy_complete",
            _NnrpFfiStatus,
            [_NnrpServerPolicyCompleteRequest],
        )
        self.server_accept_begin = _bind_native_function(
            library,
            "nnrp_server_accept_begin",
            _NnrpFfiStatus,
            [_NnrpServerAcceptBeginRequest, ctypes.POINTER(_NnrpHandle)],
        )
        self.server_accept_wait = _bind_native_function(
            library,
            "nnrp_server_accept_wait",
            _NnrpFfiStatus,
            [_NnrpServerAcceptWaitRequest],
        )
        self.server_accept_claim = _bind_native_function(
            library,
            "nnrp_server_accept_claim",
            _NnrpFfiStatus,
            [_NnrpServerAcceptClaimRequest, ctypes.POINTER(_NnrpServerAcceptResult)],
        )
        self.server_accept_release = _bind_native_function(
            library,
            "nnrp_server_accept_release",
            _NnrpFfiStatus,
            [_NnrpHandle],
        )
        self.server_await_events = _bind_native_function(
            library,
            "nnrp_server_await_events",
            _NnrpFfiStatus,
            [_NnrpRoleEventPollRequest, ctypes.POINTER(_NnrpEvent), ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)],
        )
        self.server_send_result = _bind_native_function(
            library, "nnrp_server_send_result", _NnrpFfiStatus, [_NnrpServerSendResultRequest]
        )
        self.server_close = _bind_native_function(library, "nnrp_server_close", _NnrpFfiStatus, [_NnrpHandle])
        self.runtime_frame_send = _bind_native_function(
            library,
            "nnrp_runtime_frame_send",
            _NnrpFfiStatus,
            [_NnrpRuntimeFrameSendRequest],
        )
        self.schema_descriptor_parse = _bind_native_function(
            library,
            "nnrp_schema_descriptor_parse",
            _NnrpFfiStatus,
            [_NnrpBufferView, ctypes.POINTER(_NnrpSchemaDescriptorHeader)],
        )
        self.schema_descriptor_write = _bind_native_function(
            library,
            "nnrp_schema_descriptor_write",
            _NnrpFfiStatus,
            [_NnrpSchemaDescriptorHeader, _NnrpBufferViewMut],
        )
        self.token_delta_schema_descriptor = _bind_native_function(
            library,
            "nnrp_token_delta_schema_descriptor",
            _NnrpFfiStatus,
            [ctypes.POINTER(_NnrpSchemaDescriptorHeader)],
        )
        self.typed_payload_descriptor_parse = _bind_native_function(
            library,
            "nnrp_typed_payload_descriptor_parse",
            _NnrpFfiStatus,
            [_NnrpBufferView, ctypes.POINTER(_NnrpTypedPayloadDescriptor)],
        )
        self.typed_payload_descriptor_write = _bind_native_function(
            library,
            "nnrp_typed_payload_descriptor_write",
            _NnrpFfiStatus,
            [_NnrpTypedPayloadDescriptor, _NnrpBufferViewMut],
        )
        self.typed_payload_validate_binding = _bind_native_function(
            library,
            "nnrp_typed_payload_validate_binding",
            _NnrpFfiStatus,
            [ctypes.POINTER(_NnrpSchemaDescriptorHeader), ctypes.c_size_t, _NnrpTypedPayloadDescriptor],
        )
        self.schema_registry_create = _bind_native_function(
            library,
            "nnrp_schema_registry_create",
            _NnrpFfiStatus,
            [ctypes.POINTER(_NnrpHandle)],
        )
        self.schema_registry_install = _bind_native_function(
            library,
            "nnrp_schema_registry_install",
            _NnrpFfiStatus,
            [_NnrpHandle, _NnrpSchemaDescriptorHeader, ctypes.POINTER(ctypes.c_uint32)],
        )
        self.schema_registry_lookup = _bind_native_function(
            library,
            "nnrp_schema_registry_lookup",
            _NnrpFfiStatus,
            [_NnrpHandle, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(_NnrpSchemaDescriptorHeader)],
        )
        self.schema_registry_invalidate = _bind_native_function(
            library,
            "nnrp_schema_registry_invalidate",
            _NnrpFfiStatus,
            [_NnrpHandle, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)],
        )
        self.schema_registry_validate_binding = _bind_native_function(
            library,
            "nnrp_schema_registry_validate_binding",
            _NnrpFfiStatus,
            [_NnrpHandle, _NnrpTypedPayloadDescriptor],
        )
        self.schema_registry_release = _bind_native_function(
            library, "nnrp_schema_registry_release", _NnrpFfiStatus, [_NnrpHandle]
        )
        self.session_recovery_request_validate = _bind_native_function(
            library,
            "nnrp_session_recovery_request_validate",
            _NnrpFfiStatus,
            [_NnrpBufferView],
        )
        self.session_recovery_ack_validate = _bind_native_function(
            library,
            "nnrp_session_recovery_ack_validate",
            _NnrpFfiStatus,
            [_NnrpBufferView, _NnrpBufferView, ctypes.POINTER(_NnrpSessionRecoveryOutcome)],
        )
        self.migration_recovery_validate = _bind_native_function(
            library,
            "nnrp_migration_recovery_validate",
            _NnrpFfiStatus,
            [_NnrpBufferView, _NnrpBufferView],
        )
        self.migration_should_replay_frame = _bind_native_function(
            library,
            "nnrp_migration_should_replay_frame",
            _NnrpFfiStatus,
            [_NnrpBufferView, ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint8)],
        )
        self.buffer_acquire_copy = _bind_native_function(
            library,
            "nnrp_buffer_acquire_copy",
            _NnrpFfiStatus,
            [_NnrpBufferView, ctypes.POINTER(_NnrpHandle), ctypes.POINTER(_NnrpBufferView)],
        )
        self.buffer_view = _bind_native_function(
            library,
            "nnrp_buffer_view",
            _NnrpFfiStatus,
            [_NnrpHandle, ctypes.POINTER(_NnrpBufferView)],
        )
        self.buffer_release = _bind_native_function(library, "nnrp_buffer_release", _NnrpFfiStatus, [_NnrpHandle])
        self.object_metadata_buffer_acquire_copy = _bind_native_function(
            library,
            "nnrp_object_metadata_buffer_acquire_copy",
            _NnrpFfiStatus,
            [_NnrpBufferView, ctypes.POINTER(_NnrpHandle), ctypes.POINTER(_NnrpBufferView)],
        )
        self.object_metadata_buffer_view = _bind_native_function(
            library,
            "nnrp_object_metadata_buffer_view",
            _NnrpFfiStatus,
            [_NnrpHandle, ctypes.POINTER(_NnrpBufferView)],
        )
        self.object_metadata_buffer_release = _bind_native_function(
            library, "nnrp_object_metadata_buffer_release", _NnrpFfiStatus, [_NnrpHandle]
        )
        self.object_descriptor_create = _bind_native_function(
            library,
            "nnrp_object_descriptor_create",
            _NnrpFfiStatus,
            [_NnrpRuntimeObjectDescriptor, _NnrpBufferView, ctypes.POINTER(_NnrpHandle)],
        )
        self.object_descriptor_view = _bind_native_function(
            library,
            "nnrp_object_descriptor_view",
            _NnrpFfiStatus,
            [_NnrpHandle, ctypes.POINTER(_NnrpRuntimeObjectDescriptor), ctypes.POINTER(_NnrpBufferView)],
        )
        self.object_descriptor_metadata_snapshot = _bind_native_function(
            library,
            "nnrp_object_descriptor_metadata_snapshot",
            _NnrpFfiStatus,
            [_NnrpHandle, ctypes.POINTER(_NnrpHandle), ctypes.POINTER(_NnrpBufferView)],
        )
        self.object_descriptor_release = _bind_native_function(
            library, "nnrp_object_descriptor_release", _NnrpFfiStatus, [_NnrpHandle]
        )
        self.cache_query = _bind_native_function(
            library,
            "nnrp_cache_query",
            _NnrpFfiStatus,
            [_NnrpCacheLeaseRequest, ctypes.POINTER(_NnrpCacheLeaseResult)],
        )
        self.cache_touch = _bind_native_function(
            library,
            "nnrp_cache_touch",
            _NnrpFfiStatus,
            [_NnrpCacheLeaseRequest, ctypes.POINTER(_NnrpCacheLeaseResult)],
        )
        self.cache_prefetch = _bind_native_function(
            library,
            "nnrp_cache_prefetch",
            _NnrpFfiStatus,
            [
                _NnrpHandle,
                ctypes.POINTER(_NnrpCacheObjectId),
                ctypes.c_size_t,
                ctypes.c_uint64,
                ctypes.c_uint32,
                ctypes.POINTER(_NnrpCacheLeaseResult),
            ],
        )
        self.cache_release = _bind_native_function(
            library,
            "nnrp_cache_release",
            _NnrpFfiStatus,
            [_NnrpHandle, ctypes.POINTER(_NnrpCacheLeaseResult)],
        )
        self.poll_empty = _bind_native_function(
            library, "nnrp_poll_empty", _NnrpFfiStatus, [ctypes.POINTER(_NnrpPollResult)]
        )
        self.dispatch_event = _bind_native_function(
            library,
            "nnrp_dispatch_event",
            _NnrpFfiStatus,
            [_NnrpCallbackSink, ctypes.POINTER(_NnrpEvent)],
        )


class _NativeTransportEntrypoints:
    """ctypes entrypoint table for complete-packet native transport I/O."""

    def __init__(self, library: Any, *, artifact_path: Path | None = None) -> None:
        self.library = library
        self.artifact_path = artifact_path
        self.client_security_config_create = _bind_native_function(
            library,
            "nnrp_transport_client_security_config_create",
            _NnrpFfiStatus,
            [_NnrpTransportClientSecurityConfigRequest, ctypes.POINTER(_NnrpHandle)],
        )
        self.server_security_config_create = _bind_native_function(
            library,
            "nnrp_transport_server_security_config_create",
            _NnrpFfiStatus,
            [_NnrpTransportServerSecurityConfigRequest, ctypes.POINTER(_NnrpHandle)],
        )
        self.probe = _bind_native_function(
            library,
            "nnrp_transport_probe",
            _NnrpFfiStatus,
            [_NnrpTransportProbeRequest, ctypes.POINTER(_NnrpTransportProbeResult)],
        )
        self.connect = _bind_native_function(
            library,
            "nnrp_transport_connect",
            _NnrpFfiStatus,
            [_NnrpTransportOpenRequest, ctypes.POINTER(_NnrpHandle)],
        )
        self.listen = _bind_native_function(
            library,
            "nnrp_transport_listen",
            _NnrpFfiStatus,
            [_NnrpTransportOpenRequest, ctypes.POINTER(_NnrpHandle)],
        )
        self.accept = _bind_native_function(
            library,
            "nnrp_transport_accept",
            _NnrpFfiStatus,
            [_NnrpTransportAcceptRequest, ctypes.POINTER(_NnrpHandle)],
        )
        self.listener_endpoint = _bind_native_function(
            library,
            "nnrp_transport_listener_endpoint",
            _NnrpFfiStatus,
            [_NnrpHandle, ctypes.POINTER(_NnrpHandle), ctypes.POINTER(_NnrpBufferView)],
        )
        self.write_batch = _bind_native_function(
            library,
            "nnrp_transport_write_batch",
            _NnrpFfiStatus,
            [_NnrpTransportWriteBatchRequest],
        )
        self.read_batch = _bind_native_function(
            library,
            "nnrp_transport_read_batch",
            _NnrpFfiStatus,
            [_NnrpTransportReadBatchRequest, ctypes.POINTER(_NnrpTransportFrameBatch)],
        )
        self.close = _bind_native_function(
            library,
            "nnrp_transport_close",
            _NnrpFfiStatus,
            [_NnrpHandle],
        )
        self.buffer_release = _bind_native_function(
            library,
            "nnrp_buffer_release",
            _NnrpFfiStatus,
            [_NnrpHandle],
        )


class NativeTransportConnection:
    """One native carrier connection exchanging complete NNRP packets."""

    def __init__(
        self,
        entrypoints: _NativeTransportEntrypoints,
        provider: NativeTransportProvider,
        endpoint: NativeTransportEndpoint,
        handle: NativeHandle,
    ) -> None:
        handle.require_kind(HANDLE_KIND_TRANSPORT_CONNECTION)
        self._entrypoints = entrypoints
        self._provider = provider
        self._endpoint = endpoint
        self._handle = handle
        self._closed = False
        self._lock = threading.Lock()

    @property
    def kind(self) -> str:
        return self._provider.name

    @property
    def endpoint(self) -> NativeTransportEndpoint:
        return self._endpoint

    @property
    def connected(self) -> bool:
        return not self._closed

    async def send(
        self,
        packets: bytes | bytearray | memoryview | Iterable[bytes | bytearray | memoryview],
    ) -> None:
        await asyncio.to_thread(self._send, packets)

    def _send(
        self,
        packets: bytes | bytearray | memoryview | Iterable[bytes | bytearray | memoryview],
    ) -> None:
        payloads = _normalize_transport_packets(packets)
        with self._lock:
            self._require_open()
            views_and_owners = tuple(_buffer_view_from_payload(payload) for payload in payloads)
            views = (_NnrpBufferView * len(views_and_owners))(*(item[0] for item in views_and_owners))
            status = self._entrypoints.write_batch(
                _NnrpTransportWriteBatchRequest(self._handle.to_ffi(), views, len(views_and_owners), 0)
            )
            _raise_for_native_ffi_status(status)

    async def receive(
        self,
        *,
        max_packets: int = 0,
        max_bytes: int = 0,
        timeout_ms: int = 0,
    ) -> tuple[bytes, ...]:
        return await asyncio.to_thread(
            self._receive,
            max_packets=max_packets,
            max_bytes=max_bytes,
            timeout_ms=timeout_ms,
        )

    def _receive(self, *, max_packets: int, max_bytes: int, timeout_ms: int) -> tuple[bytes, ...]:
        _require_bounded_integer("max_packets", max_packets, 0xFFFFFFFF)
        _require_bounded_integer("max_bytes", max_bytes, 0xFFFFFFFFFFFFFFFF)
        _require_bounded_integer("timeout_ms", timeout_ms, 0xFFFFFFFF)
        with self._lock:
            self._require_open()
            batch = _NnrpTransportFrameBatch()
            status = self._entrypoints.read_batch(
                _NnrpTransportReadBatchRequest(
                    self._handle.to_ffi(),
                    max_packets,
                    timeout_ms,
                    max_bytes,
                ),
                ctypes.byref(batch),
            )
            _raise_for_native_ffi_status(status)
            owner = NativeHandle.from_ffi(batch.payload_owner)
            try:
                encoded = _copy_buffer_view(batch.payload)
                return _decode_transport_packet_batch(encoded, int(batch.frame_count))
            finally:
                if owner.is_valid:
                    owner.require_kind(HANDLE_KIND_BUFFER)
                    _raise_for_native_ffi_status(self._entrypoints.buffer_release(owner.to_ffi()))

    async def close(self) -> None:
        await asyncio.to_thread(self._close)

    def _close(self) -> None:
        with self._lock:
            if self._closed:
                return
            _raise_for_native_ffi_status(self._entrypoints.close(self._handle.to_ffi()))
            self._closed = True

    def _adopt_client_role(
        self,
        entrypoints: NativeRuntimeEntrypoints,
        *,
        connection_id: int,
        generation: int,
    ) -> NativeRuntimeConnection:
        _validate_u64("connection_id", connection_id)
        _validate_u32("generation", generation)
        with self._lock:
            self._require_open()
            request = _NnrpClientConnectRequest(
                connection_id,
                generation,
                0,
                self._handle.to_ffi(),
            )
            output = _NnrpHandle()
            status = entrypoints.client_connect(request, ctypes.byref(output))
            raise_for_native_status(status)
            self._closed = True
            self._handle = NativeHandle.invalid()
            return NativeRuntimeConnection(entrypoints, NativeConnectionHandle.from_ffi(output))

    def _require_open(self) -> None:
        if self._closed:
            raise NativeInvalidStateError(
                NativeStatus(FFI_STATUS_INVALID_STATE), "native transport connection is closed"
            )


class _NativeServerPolicyDispatcher:
    def __init__(
        self,
        entrypoints: NativeRuntimeEntrypoints,
        application_policy: Callable[[SessionOpenMetadata], tuple[bool, int, str | None]],
    ) -> None:
        self._entrypoints = entrypoints
        self._application_policy = application_policy
        self._executor = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="nnrp-server-policy",
        )
        self._lock = threading.Lock()
        self._futures: set[Future[None]] = set()
        self._closed = False
        self._first_error: BaseException | None = None

        @_NnrpServerPolicyBeginCallback
        def begin_callback(
            _user_data: int,
            request_id: int,
            metadata_view: _NnrpBufferView,
        ) -> int:
            return self._begin(request_id, metadata_view)

        self.callback = begin_callback
        self.sink = _NnrpServerPolicySink(None, begin_callback)

    def _begin(self, request_id: int, metadata_view: _NnrpBufferView) -> int:
        try:
            encoded_metadata = _copy_buffer_view(metadata_view)
        except Exception:
            return FFI_STATUS_CALLBACK_REJECTED
        with self._lock:
            if self._closed:
                return FFI_STATUS_CALLBACK_REJECTED
            try:
                future = self._executor.submit(self._evaluate_and_complete, request_id, encoded_metadata)
            except RuntimeError:
                return FFI_STATUS_CALLBACK_REJECTED
            self._futures.add(future)
        future.add_done_callback(self._on_complete)
        return FFI_STATUS_OK

    def _evaluate_and_complete(self, request_id: int, encoded_metadata: bytes) -> None:
        try:
            open_metadata: SessionOpenMetadata = SessionOpenMetadata.unpack(encoded_metadata)
            accepted, session_error_code, diagnostic = self._application_policy(open_metadata)
        except Exception:
            accepted = False
            session_error_code = SESSION_ERROR_LIMIT_REACHED
            diagnostic = "application policy evaluation failed"
        diagnostic_view, diagnostic_owner = _buffer_view_from_payload(
            b"" if diagnostic is None else diagnostic.encode("utf-8")
        )
        decision = _NnrpServerPolicyDecision(
            int(accepted),
            (ctypes.c_uint8 * 3)(0, 0, 0),
            session_error_code,
            diagnostic_view,
        )
        status = self._entrypoints.server_policy_complete(_NnrpServerPolicyCompleteRequest(request_id, decision))
        del diagnostic_owner
        raise_for_native_status(status)

    def _on_complete(self, future: Future[None]) -> None:
        error = future.exception()
        with self._lock:
            self._futures.discard(future)
            if error is not None and self._first_error is None:
                self._first_error = error

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            futures = tuple(self._futures)
        self._executor.shutdown(wait=True, cancel_futures=False)
        completion_error: BaseException | None = None
        for future in futures:
            completion_error = completion_error or future.exception()
        with self._lock:
            final_error = self._first_error or completion_error
        if final_error is not None:
            raise final_error


class NativeTransportListener:
    """One native carrier listener that accepts complete-packet connections."""

    def __init__(
        self,
        entrypoints: _NativeTransportEntrypoints,
        provider: NativeTransportProvider,
        endpoint: NativeTransportEndpoint,
        handle: NativeHandle,
    ) -> None:
        handle.require_kind(HANDLE_KIND_TRANSPORT_LISTENER)
        self._entrypoints = entrypoints
        self._provider = provider
        self._endpoint = endpoint
        self._handle = handle
        self._closed = False
        self._lock = threading.Lock()

    @property
    def kind(self) -> str:
        return self._provider.name

    @property
    def endpoint(self) -> NativeTransportEndpoint:
        return self._endpoint

    @property
    def listening(self) -> bool:
        return not self._closed

    async def accept(self, *, timeout_ms: int = 0) -> NativeTransportConnection:
        return await asyncio.to_thread(self._accept, timeout_ms)

    def _accept(self, timeout_ms: int) -> NativeTransportConnection:
        _require_bounded_integer("timeout_ms", timeout_ms, 0xFFFFFFFF)
        with self._lock:
            self._require_open()
            output = _NnrpHandle()
            status = self._entrypoints.accept(
                _NnrpTransportAcceptRequest(self._handle.to_ffi(), timeout_ms, 0),
                ctypes.byref(output),
            )
            _raise_for_native_ffi_status(status)
            return NativeTransportConnection(
                self._entrypoints,
                self._provider,
                self._endpoint,
                NativeHandle.from_ffi(output),
            )

    async def close(self) -> None:
        await asyncio.to_thread(self._close)

    def _close(self) -> None:
        with self._lock:
            if self._closed:
                return
            _raise_for_native_ffi_status(self._entrypoints.close(self._handle.to_ffi()))
            self._closed = True

    def _adopt_server_role(
        self,
        entrypoints: NativeRuntimeEntrypoints,
        *,
        server_id: int,
        generation: int,
        supported_profiles: Iterable[int],
        supported_cache_objects: Iterable[int],
        max_cache_objects: int,
        max_cache_object_bytes: int,
        resume_token_bytes: int,
        max_in_flight_operations: int,
        granted_operation_credit: int,
        lease_ttl_ms: int,
        resume_window_ms: int,
        schema_descriptors: Iterable[Any],
        application_policy: Callable[[SessionOpenMetadata], tuple[bool, int, str | None]] | None,
    ) -> NativeRuntimeServer:
        _validate_u64("server_id", server_id)
        _validate_u32("generation", generation)
        with self._lock:
            self._require_open()
            profile_slice, profile_owner = _u16_slice_from_values(supported_profiles)
            cache_slice, cache_owner = _u32_slice_from_values(supported_cache_objects)
            schema_registry = NativeSchemaRegistry.create(entrypoints)
            policy_dispatcher: _NativeServerPolicyDispatcher | None = None
            try:
                for descriptor in schema_descriptors:
                    schema_registry.install(descriptor)
                if application_policy is None:
                    policy_sink = _NnrpServerPolicySink(None, _NnrpServerPolicyBeginCallback())
                else:
                    policy_dispatcher = _NativeServerPolicyDispatcher(entrypoints, application_policy)
                    policy_sink = policy_dispatcher.sink
                request = _NnrpServerBindRequest(
                    server_id,
                    generation,
                    0,
                    self._handle.to_ffi(),
                    profile_slice,
                    cache_slice,
                    max_cache_objects,
                    max_cache_object_bytes,
                    resume_token_bytes,
                    max_in_flight_operations,
                    granted_operation_credit,
                    lease_ttl_ms,
                    resume_window_ms,
                    schema_registry.handle.to_ffi(),
                    policy_sink,
                )
                output = _NnrpHandle()
                status = entrypoints.server_bind(request, ctypes.byref(output))
            finally:
                schema_registry.close()
                del profile_owner, cache_owner
            try:
                raise_for_native_status(status)
            except BaseException:
                if policy_dispatcher is not None:
                    policy_dispatcher.close()
                raise
            self._closed = True
            self._handle = NativeHandle.invalid()
            return NativeRuntimeServer(
                entrypoints,
                NativeConnectionHandle.from_ffi(output),
                self._provider.name,
                policy_dispatcher,
            )

    def _require_open(self) -> None:
        if self._closed:
            raise NativeInvalidStateError(NativeStatus(FFI_STATUS_INVALID_STATE), "native transport listener is closed")


def _native_ffi_endpoint_uri(endpoint: NativeTransportEndpoint) -> str:
    if endpoint.transport_name == "quic" and endpoint.scheme == "quic+tls":
        return f"quic://{endpoint.address}"
    return endpoint.uri


class NativeTransportBinding:
    """Host-facing binding for one transport-scoped Rust artifact."""

    def __init__(
        self,
        entrypoints: _NativeTransportEntrypoints | None,
        provider: NativeTransportProvider,
        role_entrypoints: NativeRuntimeEntrypoints | None = None,
        *,
        unavailable_diagnostic: str | None = None,
    ) -> None:
        if provider.name not in provider.transport_slots:
            raise NativeArtifactError(f"native provider {provider.name!r} does not own its transport slot")
        if entrypoints is None and not unavailable_diagnostic:
            raise ValueError("unavailable native transport bindings require a diagnostic")
        if entrypoints is not None and unavailable_diagnostic is not None:
            raise ValueError("available native transport bindings must not declare an unavailable diagnostic")
        self.entrypoints = entrypoints
        self.provider = provider
        self._role_entrypoints = role_entrypoints
        self._unavailable_diagnostic = unavailable_diagnostic

    @classmethod
    def unavailable(
        cls,
        provider: NativeTransportProvider,
        diagnostic: str,
    ) -> NativeTransportBinding:
        return cls(None, provider, unavailable_diagnostic=diagnostic)

    def adopt_client(
        self,
        connection: NativeTransportConnection,
        *,
        connection_id: int,
        generation: int,
    ) -> NativeRuntimeConnection:
        self._require_available()
        if connection._entrypoints is not self.entrypoints:
            raise NativeArtifactError("native carrier must be adopted by its owning transport artifact")
        if self._role_entrypoints is None:
            raise NativeArtifactError("native transport artifact does not expose role adoption entrypoints")
        return connection._adopt_client_role(
            self._role_entrypoints,
            connection_id=connection_id,
            generation=generation,
        )

    def adopt_server(
        self,
        listener: NativeTransportListener,
        *,
        server_id: int,
        generation: int,
        supported_profiles: Iterable[int],
        supported_cache_objects: Iterable[int],
        max_cache_objects: int,
        max_cache_object_bytes: int,
        resume_token_bytes: int,
        max_in_flight_operations: int,
        granted_operation_credit: int,
        lease_ttl_ms: int,
        resume_window_ms: int,
        schema_descriptors: Iterable[Any],
        application_policy: Callable[[SessionOpenMetadata], tuple[bool, int, str | None]] | None,
    ) -> NativeRuntimeServer:
        self._require_available()
        if listener._entrypoints is not self.entrypoints:
            raise NativeArtifactError("native listener must be adopted by its owning transport artifact")
        if self._role_entrypoints is None:
            raise NativeArtifactError("native transport artifact does not expose role adoption entrypoints")
        return listener._adopt_server_role(
            self._role_entrypoints,
            server_id=server_id,
            generation=generation,
            supported_profiles=supported_profiles,
            supported_cache_objects=supported_cache_objects,
            max_cache_objects=max_cache_objects,
            max_cache_object_bytes=max_cache_object_bytes,
            resume_token_bytes=resume_token_bytes,
            max_in_flight_operations=max_in_flight_operations,
            granted_operation_credit=granted_operation_credit,
            lease_ttl_ms=lease_ttl_ms,
            resume_window_ms=resume_window_ms,
            schema_descriptors=schema_descriptors,
            application_policy=application_policy,
        )

    @property
    def kind(self) -> str:
        return self.provider.name

    @property
    def local_available(self) -> bool:
        return self.entrypoints is not None

    @property
    def diagnostic(self) -> str | None:
        return self._unavailable_diagnostic

    async def probe(
        self,
        endpoint: str | NativeTransportEndpoint,
        *,
        security: NativeTransportClientSecurity | None = None,
        sample_count: int = 0,
        probe_payload_bytes: int = 0,
        max_packet_bytes: int = 0,
        timeout_ms: int = 0,
    ) -> NativeTransportProbeMetrics:
        self._require_available()
        return await asyncio.to_thread(
            self._probe,
            endpoint,
            security,
            sample_count,
            probe_payload_bytes,
            max_packet_bytes,
            timeout_ms,
        )

    def _probe(
        self,
        endpoint: str | NativeTransportEndpoint,
        security: NativeTransportClientSecurity | None,
        sample_count: int,
        probe_payload_bytes: int,
        max_packet_bytes: int,
        timeout_ms: int,
    ) -> NativeTransportProbeMetrics:
        self._require_available()
        _require_bounded_integer("sample_count", sample_count, 0xFFFFFFFF)
        _require_bounded_integer("probe_payload_bytes", probe_payload_bytes, 0xFFFFFFFF)
        parsed = self._endpoint(endpoint)
        config = self._client_security_config(security)
        try:
            request, _owner = self._open_request(parsed, config, max_packet_bytes, timeout_ms)
            result = _NnrpTransportProbeResult()
            status = self.entrypoints.probe(
                _NnrpTransportProbeRequest(request, sample_count, probe_payload_bytes),
                ctypes.byref(result),
            )
            _raise_for_native_ffi_status(status)
            return NativeTransportProbeMetrics(
                sample_count=int(result.sample_count),
                success_count=int(result.success_count),
                median_throughput_bytes_per_sec=int(result.median_throughput_bytes_per_second),
                median_rtt_us=int(result.median_rtt_microseconds),
            )
        finally:
            self._close_config(config)

    async def connect(
        self,
        endpoint: str | NativeTransportEndpoint,
        *,
        security: NativeTransportClientSecurity | None = None,
        max_packet_bytes: int = 0,
        timeout_ms: int = 0,
    ) -> NativeTransportConnection:
        self._require_available()
        return await asyncio.to_thread(
            self._connect,
            endpoint,
            security,
            max_packet_bytes,
            timeout_ms,
        )

    def _connect(
        self,
        endpoint: str | NativeTransportEndpoint,
        security: NativeTransportClientSecurity | None,
        max_packet_bytes: int,
        timeout_ms: int,
    ) -> NativeTransportConnection:
        self._require_available()
        parsed = self._endpoint(endpoint)
        config = self._client_security_config(security)
        try:
            request, _owner = self._open_request(parsed, config, max_packet_bytes, timeout_ms)
            output = _NnrpHandle()
            _raise_for_native_ffi_status(self.entrypoints.connect(request, ctypes.byref(output)))
            return NativeTransportConnection(
                self.entrypoints,
                self.provider,
                parsed,
                NativeHandle.from_ffi(output),
            )
        finally:
            self._close_config(config)

    async def listen(
        self,
        endpoint: str | NativeTransportEndpoint,
        *,
        security: NativeTransportServerSecurity | None = None,
        max_packet_bytes: int = 0,
        timeout_ms: int = 0,
    ) -> NativeTransportListener:
        self._require_available()
        return await asyncio.to_thread(
            self._listen,
            endpoint,
            security,
            max_packet_bytes,
            timeout_ms,
        )

    def _listen(
        self,
        endpoint: str | NativeTransportEndpoint,
        security: NativeTransportServerSecurity | None,
        max_packet_bytes: int,
        timeout_ms: int,
    ) -> NativeTransportListener:
        self._require_available()
        parsed = self._endpoint(endpoint)
        config = self._server_security_config(security)
        try:
            request, _owner = self._open_request(parsed, config, max_packet_bytes, timeout_ms)
            output = _NnrpHandle()
            _raise_for_native_ffi_status(self.entrypoints.listen(request, ctypes.byref(output)))
            handle = NativeHandle.from_ffi(output)
            try:
                bound_endpoint = self._listener_endpoint(handle)
            except BaseException:
                _raise_for_native_ffi_status(self.entrypoints.close(handle.to_ffi()))
                raise
            return NativeTransportListener(self.entrypoints, self.provider, bound_endpoint, handle)
        finally:
            self._close_config(config)

    def _endpoint(self, endpoint: str | NativeTransportEndpoint) -> NativeTransportEndpoint:
        parsed = (
            endpoint if isinstance(endpoint, NativeTransportEndpoint) else parse_native_transport_endpoint(endpoint)
        )
        if parsed.transport_name != self.provider.name:
            raise NativeArtifactError(
                f"native provider {self.provider.name!r} cannot open {parsed.transport_name!r} endpoint"
            )
        return parsed

    def _open_request(
        self,
        endpoint: NativeTransportEndpoint,
        config: NativeHandle,
        max_packet_bytes: int,
        timeout_ms: int,
    ) -> tuple[_NnrpTransportOpenRequest, object | None]:
        _require_bounded_integer("max_packet_bytes", max_packet_bytes, 0xFFFFFFFFFFFFFFFF)
        _require_bounded_integer("timeout_ms", timeout_ms, 0xFFFFFFFF)
        view, owner = _buffer_view_from_payload(_native_ffi_endpoint_uri(endpoint).encode("utf-8"))
        return (
            _NnrpTransportOpenRequest(
                int(endpoint.transport_id),
                0,
                view,
                config.to_ffi(),
                max_packet_bytes,
                timeout_ms,
                0,
            ),
            owner,
        )

    def _listener_endpoint(self, listener: NativeHandle) -> NativeTransportEndpoint:
        owner = _NnrpHandle()
        view = _NnrpBufferView()
        _raise_for_native_ffi_status(
            self.entrypoints.listener_endpoint(listener.to_ffi(), ctypes.byref(owner), ctypes.byref(view))
        )
        owner_handle = NativeHandle.from_ffi(owner)
        try:
            return self._endpoint(_copy_buffer_view(view).decode("utf-8"))
        finally:
            if owner_handle.is_valid:
                owner_handle.require_kind(HANDLE_KIND_BUFFER)
                _raise_for_native_ffi_status(self.entrypoints.buffer_release(owner_handle.to_ffi()))

    def _client_security_config(self, security: NativeTransportClientSecurity | None) -> NativeHandle:
        if security is None:
            return NativeHandle.invalid()
        server_name, _server_owner = _buffer_view_from_payload(security.server_name.encode("utf-8"))
        certificate, _certificate_owner = _buffer_view_from_payload(security.trusted_certificate_der)
        output = _NnrpHandle()
        _raise_for_native_ffi_status(
            self.entrypoints.client_security_config_create(
                _NnrpTransportClientSecurityConfigRequest(
                    int(NATIVE_TRANSPORT_ID_BY_NAME[self.provider.name]),
                    0,
                    server_name,
                    certificate,
                ),
                ctypes.byref(output),
            )
        )
        return NativeHandle.from_ffi(output)

    def _server_security_config(self, security: NativeTransportServerSecurity | None) -> NativeHandle:
        if security is None:
            return NativeHandle.invalid()
        certificate, _certificate_owner = _buffer_view_from_payload(security.certificate_der)
        private_key, _private_key_owner = _buffer_view_from_payload(security.private_key_pkcs8_der)
        output = _NnrpHandle()
        _raise_for_native_ffi_status(
            self.entrypoints.server_security_config_create(
                _NnrpTransportServerSecurityConfigRequest(
                    int(NATIVE_TRANSPORT_ID_BY_NAME[self.provider.name]),
                    0,
                    certificate,
                    private_key,
                ),
                ctypes.byref(output),
            )
        )
        return NativeHandle.from_ffi(output)

    def _close_config(self, config: NativeHandle) -> None:
        if config.is_valid:
            _raise_for_native_ffi_status(self.entrypoints.close(config.to_ffi()))

    def _require_available(self) -> None:
        if not self.local_available:
            raise NativeArtifactError(
                self.diagnostic or f"native transport provider {self.provider.metadata.id!r} is unavailable"
            )


@dataclass(frozen=True)
class NativeSchemaCodec:
    entrypoints: NativeRuntimeEntrypoints

    def parse_schema_descriptor(self, payload: bytes | bytearray | memoryview) -> Any:
        source, _owner = _buffer_view_from_payload(payload)
        descriptor = _NnrpSchemaDescriptorHeader()
        status = self.entrypoints.schema_descriptor_parse(source, ctypes.byref(descriptor))
        raise_for_native_status(status)
        return _schema_descriptor_from_ffi(descriptor)

    def write_schema_descriptor(self, descriptor: Any) -> bytes:
        destination = ctypes.create_string_buffer(ctypes.sizeof(_NnrpSchemaDescriptorHeader))
        status = self.entrypoints.schema_descriptor_write(
            _schema_descriptor_to_ffi(descriptor),
            _NnrpBufferViewMut(ctypes.cast(destination, ctypes.c_void_p), len(destination.raw)),
        )
        raise_for_native_status(status)
        return destination.raw

    def token_delta_schema_descriptor(self) -> Any:
        descriptor = _NnrpSchemaDescriptorHeader()
        status = self.entrypoints.token_delta_schema_descriptor(ctypes.byref(descriptor))
        raise_for_native_status(status)
        return _schema_descriptor_from_ffi(descriptor)

    def parse_typed_payload_descriptor(self, payload: bytes | bytearray | memoryview) -> Any:
        source, _owner = _buffer_view_from_payload(payload)
        descriptor = _NnrpTypedPayloadDescriptor()
        status = self.entrypoints.typed_payload_descriptor_parse(source, ctypes.byref(descriptor))
        raise_for_native_status(status)
        return _typed_payload_descriptor_from_ffi(descriptor)

    def write_typed_payload_descriptor(self, descriptor: Any) -> bytes:
        destination = ctypes.create_string_buffer(ctypes.sizeof(_NnrpTypedPayloadDescriptor))
        status = self.entrypoints.typed_payload_descriptor_write(
            _typed_payload_descriptor_to_ffi(descriptor),
            _NnrpBufferViewMut(ctypes.cast(destination, ctypes.c_void_p), len(destination.raw)),
        )
        raise_for_native_status(status)
        return destination.raw

    def validate_typed_payload_binding(self, schemas: tuple[Any, ...], descriptor: Any) -> None:
        schema_count = len(schemas)
        ffi_descriptor = _typed_payload_descriptor_to_ffi(descriptor)
        if schema_count == 0:
            schema_pointer = ctypes.POINTER(_NnrpSchemaDescriptorHeader)()
        else:
            schema_array = (_NnrpSchemaDescriptorHeader * schema_count)(
                *(_schema_descriptor_to_ffi(schema) for schema in schemas)
            )
            schema_pointer = ctypes.cast(schema_array, ctypes.POINTER(_NnrpSchemaDescriptorHeader))
        status = self.entrypoints.typed_payload_validate_binding(schema_pointer, schema_count, ffi_descriptor)
        raise_for_native_status(status)


@dataclass
class NativeSchemaRegistry:
    entrypoints: NativeRuntimeEntrypoints
    handle: NativeSchemaRegistryHandle
    _released: bool = field(default=False, init=False, repr=False, compare=False)

    @classmethod
    def create(cls, entrypoints: NativeRuntimeEntrypoints) -> NativeSchemaRegistry:
        out_registry = _NnrpHandle()
        status = entrypoints.schema_registry_create(ctypes.byref(out_registry))
        raise_for_native_status(status)
        return cls(entrypoints, NativeSchemaRegistryHandle.from_ffi(out_registry))

    def install(self, descriptor: Any) -> Any:
        self._ensure_open()
        out_action = ctypes.c_uint32()
        status = self.entrypoints.schema_registry_install(
            self.handle.to_ffi(),
            _schema_descriptor_to_ffi(descriptor),
            ctypes.byref(out_action),
        )
        raise_for_native_status(status)
        return _schema_registry_action_from_ffi(int(out_action.value))

    def lookup(self, schema_id: int, schema_version: int) -> Any:
        self._ensure_open()
        _validate_u32("schema_id", schema_id)
        _validate_u32("schema_version", schema_version)
        out_descriptor = _NnrpSchemaDescriptorHeader()
        status = self.entrypoints.schema_registry_lookup(
            self.handle.to_ffi(),
            schema_id,
            schema_version,
            ctypes.byref(out_descriptor),
        )
        raise_for_native_status(status)
        return _schema_descriptor_from_ffi(out_descriptor)

    def invalidate(self, schema_id: int, schema_version: int) -> Any:
        self._ensure_open()
        _validate_u32("schema_id", schema_id)
        _validate_u32("schema_version", schema_version)
        out_action = ctypes.c_uint32()
        status = self.entrypoints.schema_registry_invalidate(
            self.handle.to_ffi(),
            schema_id,
            schema_version,
            ctypes.byref(out_action),
        )
        raise_for_native_status(status)
        return _schema_registry_action_from_ffi(int(out_action.value))

    def validate_typed_payload_binding(self, descriptor: Any) -> None:
        self._ensure_open()
        status = self.entrypoints.schema_registry_validate_binding(
            self.handle.to_ffi(),
            _typed_payload_descriptor_to_ffi(descriptor),
        )
        raise_for_native_status(status)

    def close(self) -> None:
        self._ensure_open()
        status = self.entrypoints.schema_registry_release(self.handle.to_ffi())
        raise_for_native_status(status)
        self._released = True

    def _ensure_open(self) -> None:
        if self._released:
            raise NativeInvalidStateError(NativeStatus(FFI_STATUS_INVALID_STATE), "native schema registry is released")


@dataclass
class NativeOwnedBuffer:
    entrypoints: NativeRuntimeEntrypoints
    handle: NativeBufferHandle
    view: NativeBufferView
    _released: bool = field(default=False, init=False, repr=False, compare=False)
    _borrow_count: int = field(default=0, init=False, repr=False, compare=False)

    @classmethod
    def acquire_copy(
        cls, entrypoints: NativeRuntimeEntrypoints, payload: bytes | bytearray | memoryview
    ) -> NativeOwnedBuffer:
        source, _owner = _buffer_view_from_payload(payload)
        out_buffer = _NnrpHandle()
        out_view = _NnrpBufferView()
        status = entrypoints.buffer_acquire_copy(source, ctypes.byref(out_buffer), ctypes.byref(out_view))
        raise_for_native_status(status)
        return cls(entrypoints, NativeBufferHandle.from_ffi(out_buffer), NativeBufferView.from_ffi(out_view))

    def refresh_view(self) -> NativeBufferView:
        self._ensure_open()
        out_view = _NnrpBufferView()
        status = self.entrypoints.buffer_view(self.handle.to_ffi(), ctypes.byref(out_view))
        raise_for_native_status(status)
        view = NativeBufferView.from_ffi(out_view)
        self.view = view
        return view

    def to_bytes(self) -> bytes:
        self._ensure_open()
        out_view = _NnrpBufferView()
        status = self.entrypoints.buffer_view(self.handle.to_ffi(), ctypes.byref(out_view))
        raise_for_native_status(status)
        return _copy_buffer_view(out_view)

    def borrow_view(self) -> NativeBorrowedBufferView:
        self._ensure_open()
        return NativeBorrowedBufferView(self, self.refresh_view())

    def close(self) -> None:
        self._ensure_open()
        if self._borrow_count:
            raise NativeInvalidStateError(
                NativeStatus(FFI_STATUS_INVALID_STATE),
                "native buffer has active borrowed views",
            )
        status = self.entrypoints.buffer_release(self.handle.to_ffi())
        raise_for_native_status(status)
        self._released = True

    def _ensure_open(self) -> None:
        if self._released:
            raise NativeInvalidStateError(NativeStatus(FFI_STATUS_INVALID_STATE), "native buffer is released")


@dataclass
class NativeObjectMetadataBuffer:
    entrypoints: NativeRuntimeEntrypoints
    handle: NativeBufferHandle
    view: NativeBufferView
    _released: bool = field(default=False, init=False, repr=False, compare=False)
    _borrow_count: int = field(default=0, init=False, repr=False, compare=False)

    @classmethod
    def acquire_copy(
        cls, entrypoints: NativeRuntimeEntrypoints, payload: bytes | bytearray | memoryview
    ) -> NativeObjectMetadataBuffer:
        source, _owner = _buffer_view_from_payload(payload)
        out_buffer = _NnrpHandle()
        out_view = _NnrpBufferView()
        status = entrypoints.object_metadata_buffer_acquire_copy(
            source,
            ctypes.byref(out_buffer),
            ctypes.byref(out_view),
        )
        raise_for_native_status(status)
        return cls(entrypoints, NativeBufferHandle.from_ffi(out_buffer), NativeBufferView.from_ffi(out_view))

    def refresh_view(self) -> NativeBufferView:
        self._ensure_open()
        out_view = _NnrpBufferView()
        status = self.entrypoints.object_metadata_buffer_view(self.handle.to_ffi(), ctypes.byref(out_view))
        raise_for_native_status(status)
        view = NativeBufferView.from_ffi(out_view)
        self.view = view
        return view

    def to_bytes(self) -> bytes:
        self._ensure_open()
        out_view = _NnrpBufferView()
        status = self.entrypoints.object_metadata_buffer_view(self.handle.to_ffi(), ctypes.byref(out_view))
        raise_for_native_status(status)
        return _copy_buffer_view(out_view)

    def borrow_view(self) -> NativeBorrowedBufferView:
        self._ensure_open()
        return NativeBorrowedBufferView(self, self.refresh_view())

    def close(self) -> None:
        self._ensure_open()
        if self._borrow_count:
            raise NativeInvalidStateError(
                NativeStatus(FFI_STATUS_INVALID_STATE),
                "native object metadata buffer has active borrowed views",
            )
        status = self.entrypoints.object_metadata_buffer_release(self.handle.to_ffi())
        raise_for_native_status(status)
        self._released = True

    def _ensure_open(self) -> None:
        if self._released:
            raise NativeInvalidStateError(
                NativeStatus(FFI_STATUS_INVALID_STATE),
                "native object metadata buffer is released",
            )


@dataclass
class NativeObjectDescriptor:
    entrypoints: NativeRuntimeEntrypoints
    handle: NativeObjectDescriptorHandle
    descriptor: Any
    metadata_view: NativeBufferView
    _released: bool = field(default=False, init=False, repr=False, compare=False)

    @classmethod
    def create(
        cls,
        entrypoints: NativeRuntimeEntrypoints,
        descriptor: Any,
        metadata: bytes | bytearray | memoryview = b"",
    ) -> NativeObjectDescriptor:
        metadata_view, _owner = _buffer_view_from_payload(metadata)
        out_handle = _NnrpHandle()
        status = entrypoints.object_descriptor_create(
            _runtime_object_descriptor_to_ffi(descriptor),
            metadata_view,
            ctypes.byref(out_handle),
        )
        raise_for_native_status(status)
        native_descriptor = cls(
            entrypoints,
            NativeObjectDescriptorHandle.from_ffi(out_handle),
            descriptor,
            NativeBufferView.empty(),
        )
        native_descriptor.refresh_view()
        return native_descriptor

    def refresh_view(self) -> tuple[Any, NativeBufferView]:
        self._ensure_open()
        out_descriptor = _NnrpRuntimeObjectDescriptor()
        out_metadata = _NnrpBufferView()
        status = self.entrypoints.object_descriptor_view(
            self.handle.to_ffi(),
            ctypes.byref(out_descriptor),
            ctypes.byref(out_metadata),
        )
        raise_for_native_status(status)
        descriptor = _runtime_object_descriptor_from_ffi(out_descriptor)
        metadata_view = NativeBufferView.from_ffi(out_metadata)
        self.descriptor = descriptor
        self.metadata_view = metadata_view
        return descriptor, metadata_view

    def metadata_snapshot(self) -> NativeObjectMetadataBuffer:
        self._ensure_open()
        out_buffer = _NnrpHandle()
        out_view = _NnrpBufferView()
        status = self.entrypoints.object_descriptor_metadata_snapshot(
            self.handle.to_ffi(),
            ctypes.byref(out_buffer),
            ctypes.byref(out_view),
        )
        raise_for_native_status(status)
        return NativeObjectMetadataBuffer(
            self.entrypoints,
            NativeBufferHandle.from_ffi(out_buffer),
            NativeBufferView.from_ffi(out_view),
        )

    def close(self) -> None:
        self._ensure_open()
        status = self.entrypoints.object_descriptor_release(self.handle.to_ffi())
        raise_for_native_status(status)
        self._released = True

    def _ensure_open(self) -> None:
        if self._released:
            raise NativeInvalidStateError(
                NativeStatus(FFI_STATUS_INVALID_STATE),
                "native object descriptor is released",
            )


@dataclass(frozen=True)
class NativeSessionRecoveryOutcome:
    outcome_code: int
    resume_window_ms: int = 0

    def __post_init__(self) -> None:
        _validate_u32("outcome_code", self.outcome_code)
        _validate_u32("resume_window_ms", self.resume_window_ms)

    @classmethod
    def from_ffi(cls, outcome: _NnrpSessionRecoveryOutcome) -> NativeSessionRecoveryOutcome:
        return cls(int(outcome.outcome_code), int(outcome.resume_window_ms))

    @property
    def outcome_name(self) -> str:
        return _SESSION_RECOVERY_OUTCOME_NAMES.get(self.outcome_code, "unknown")

    @property
    def is_fresh(self) -> bool:
        return self.outcome_code == SESSION_RECOVERY_OUTCOME_FRESH

    @property
    def resume_enabled(self) -> bool:
        return self.outcome_code == SESSION_RECOVERY_OUTCOME_RESUME_ENABLED

    @property
    def resumed(self) -> bool:
        return self.outcome_code == SESSION_RECOVERY_OUTCOME_RESUMED

    @property
    def resume_rejected(self) -> bool:
        return self.outcome_code == SESSION_RECOVERY_OUTCOME_RESUME_REJECTED


@dataclass(frozen=True)
class NativeRecoveryCodec:
    entrypoints: NativeRuntimeEntrypoints

    def validate_session_recovery_request(self, session_open_metadata: bytes | bytearray | memoryview) -> None:
        source, _owner = _buffer_view_from_payload(session_open_metadata)
        status = self.entrypoints.session_recovery_request_validate(source)
        raise_for_native_status(status)

    def validate_session_recovery_ack(
        self,
        session_open_metadata: bytes | bytearray | memoryview,
        session_open_ack_metadata: bytes | bytearray | memoryview,
    ) -> NativeSessionRecoveryOutcome:
        open_source, _open_owner = _buffer_view_from_payload(session_open_metadata)
        ack_source, _ack_owner = _buffer_view_from_payload(session_open_ack_metadata)
        outcome = _NnrpSessionRecoveryOutcome()
        status = self.entrypoints.session_recovery_ack_validate(open_source, ack_source, ctypes.byref(outcome))
        raise_for_native_status(status)
        return NativeSessionRecoveryOutcome.from_ffi(outcome)

    def validate_migration_recovery(
        self,
        session_migrate_metadata: bytes | bytearray | memoryview,
        session_migrate_ack_metadata: bytes | bytearray | memoryview,
    ) -> None:
        migrate_source, _migrate_owner = _buffer_view_from_payload(session_migrate_metadata)
        ack_source, _ack_owner = _buffer_view_from_payload(session_migrate_ack_metadata)
        status = self.entrypoints.migration_recovery_validate(migrate_source, ack_source)
        raise_for_native_status(status)

    def should_replay_frame_after_migration(
        self,
        session_migrate_ack_metadata: bytes | bytearray | memoryview,
        frame_id: int,
    ) -> bool:
        _validate_u64("frame_id", frame_id)
        ack_source, _ack_owner = _buffer_view_from_payload(session_migrate_ack_metadata)
        out_should_replay = ctypes.c_uint8()
        status = self.entrypoints.migration_should_replay_frame(
            ack_source,
            frame_id,
            ctypes.byref(out_should_replay),
        )
        raise_for_native_status(status)
        return bool(out_should_replay.value)


@dataclass(frozen=True, slots=True)
class NativeRuntimeDiagnostic:
    status: NativeStatus
    related_connection_id: int
    related_session_id: int
    related_operation_id: int
    related_frame_id: int

    @classmethod
    def from_ffi(cls, diagnostic: _NnrpFfiDiagnostic) -> NativeRuntimeDiagnostic:
        return cls(
            status=NativeStatus.from_ffi(diagnostic.status),
            related_connection_id=int(diagnostic.related_connection_id),
            related_session_id=int(diagnostic.related_session_id),
            related_operation_id=int(diagnostic.related_operation_id),
            related_frame_id=int(diagnostic.related_frame_id),
        )


@dataclass(frozen=True, slots=True)
class NativeStructuredDiagnostic:
    status: NativeStatus
    related_connection_id: int = 0
    related_session_id: int = 0
    related_operation_id: int = 0
    related_frame_id: int = 0

    @classmethod
    def from_status(cls, status: NativeStatus) -> NativeStructuredDiagnostic:
        return cls(status=status)

    @classmethod
    def from_runtime_diagnostic(cls, diagnostic: NativeRuntimeDiagnostic) -> NativeStructuredDiagnostic:
        return cls(
            status=diagnostic.status,
            related_connection_id=diagnostic.related_connection_id,
            related_session_id=diagnostic.related_session_id,
            related_operation_id=diagnostic.related_operation_id,
            related_frame_id=diagnostic.related_frame_id,
        )

    @property
    def status_name(self) -> str:
        return self.status.status_name

    @property
    def error_family_name(self) -> str:
        return self.status.error_family_name

    @property
    def protocol_error_name(self) -> str:
        return self.status.protocol_error_name

    @property
    def failed(self) -> bool:
        return not self.status.succeeded

    @property
    def is_session_error(self) -> bool:
        return self.status.is_session_error

    @property
    def is_cache_error(self) -> bool:
        return self.status.is_cache_error

    @property
    def is_schema_error(self) -> bool:
        return self.status.is_schema_error

    @property
    def is_retryable(self) -> bool:
        return self.status.is_retryable

    @property
    def is_downgrade(self) -> bool:
        return self.status.is_downgrade

    def to_report(self) -> dict[str, int | str | bool]:
        return {
            "status_code": self.status.status_code,
            "status_name": self.status_name,
            "error_family": self.status.error_family,
            "error_family_name": self.error_family_name,
            "protocol_error_code": self.status.protocol_error_code,
            "protocol_error_name": self.protocol_error_name,
            "detail_code": self.status.detail_code,
            "failed": self.failed,
            "retryable": self.is_retryable,
            "downgrade": self.is_downgrade,
            "related_connection_id": self.related_connection_id,
            "related_session_id": self.related_session_id,
            "related_operation_id": self.related_operation_id,
            "related_frame_id": self.related_frame_id,
        }


_NATIVE_RUNTIME_DIAGNOSTIC_OK = NativeRuntimeDiagnostic(
    status=_NATIVE_STATUS_OK,
    related_connection_id=0,
    related_session_id=0,
    related_operation_id=0,
    related_frame_id=0,
)


@dataclass(frozen=True, slots=True)
class _NativeRuntimeEventContext:
    kind: int
    connection: NativeHandle = field(default_factory=NativeHandle.invalid)
    session: NativeHandle = field(default_factory=NativeHandle.invalid)
    operation: NativeHandle = field(default_factory=NativeHandle.invalid)
    diagnostic: NativeRuntimeDiagnostic = _NATIVE_RUNTIME_DIAGNOSTIC_OK


@dataclass(frozen=True, slots=True)
class NativeLifecycleEvent:
    kind: int
    connection: NativeHandle
    session: NativeHandle
    operation: NativeHandle
    payload: bytes
    diagnostic: NativeRuntimeDiagnostic

    @property
    def kind_name(self) -> str:
        return _EVENT_KIND_NAMES.get(self.kind, "unknown")

    @property
    def is_result_event(self) -> bool:
        return self.kind in {EVENT_KIND_RESULT_PUSHED, EVENT_KIND_RESULT_DROPPED}

    @property
    def is_flow_update(self) -> bool:
        return self.kind == EVENT_KIND_FLOW_UPDATED

    @property
    def is_control_event(self) -> bool:
        return self.kind in {EVENT_KIND_FLOW_UPDATED, EVENT_KIND_CONTROL}


NativePolledEvent = NativeRuntimeEvent | NativeLifecycleEvent


@dataclass(frozen=True)
class NativeCreditUpdateEvent:
    event: NativeRuntimeEvent
    metadata: FlowUpdateMetadata

    @classmethod
    def from_event(cls, event: NativePolledEvent) -> NativeCreditUpdateEvent:
        if not isinstance(event, NativeRuntimeEvent):
            raise NativeHandleError(f"expected FLOW_UPDATE event, got {event.kind_name}")
        if event.metadata.kind is not RuntimeEventMetadataKind.FLOW_UPDATE:
            raise NativeHandleError(f"expected FLOW_UPDATE event, got {event.header.message_type.name}")
        return cls(event, event.metadata.value)


@dataclass(frozen=True)
class NativeResultHintEvent:
    event: NativeRuntimeEvent
    metadata: ResultHintMetadata

    @classmethod
    def from_event(cls, event: NativePolledEvent) -> NativeResultHintEvent:
        if not isinstance(event, NativeRuntimeEvent):
            raise NativeHandleError(f"expected RESULT_HINT event, got {event.kind_name}")
        if event.metadata.kind is not RuntimeEventMetadataKind.RESULT_HINT:
            raise NativeHandleError(f"expected RESULT_HINT event, got {event.header.message_type.name}")
        return cls(event, event.metadata.value)


@dataclass(frozen=True)
class NativePayloadFamilyEvent:
    payload_family: str
    payload: bytes
    event: NativeRuntimeEvent

    @classmethod
    def from_event(cls, event: NativeRuntimeEvent, *, payload_family: str) -> NativePayloadFamilyEvent:
        normalized_family = payload_family.strip().lower()
        if normalized_family not in _PAYLOAD_FAMILY_NAMES:
            raise NativeHandleError(f"unknown native payload family {payload_family!r}")
        if event.header.message_type not in {
            MessageType.RESULT_PUSH,
            MessageType.RESULT_DROP,
            MessageType.CANCEL,
            MessageType.ABORT,
        }:
            raise NativeHandleError(f"expected native result/control event, got {event.header.message_type.name}")
        return cls(
            payload_family=normalized_family,
            payload=(event.tail.body if event.tail.kind is RuntimeEventTailKind.BODY else event.tail.diagnostic),
            event=event,
        )

    @property
    def is_structured_event(self) -> bool:
        return self.payload_family == "structured_event"

    @property
    def is_tool_delta(self) -> bool:
        return self.payload_family == "tool_delta"

    @property
    def is_workflow_state(self) -> bool:
        return self.payload_family == "workflow_state"


NativeRuntimeEventCallback = Callable[[NativeRuntimeEvent], None]
NativeLifecycleEventCallback = Callable[[NativeLifecycleEvent], None]
NativePolledEventCallback = Callable[[NativePolledEvent], None]
NativeCreditUpdateCallback = Callable[[NativeCreditUpdateEvent], None]
NativeResultHintCallback = Callable[[NativeResultHintEvent], None]
NativePayloadFamilyCallback = Callable[[NativePayloadFamilyEvent], None]


@dataclass(frozen=True)
class NativeRuntimePollResult:
    status: NativeStatus
    event: NativePolledEvent | None = None

    @classmethod
    def from_ffi(
        cls,
        result: _NnrpPollResult,
        entrypoints: NativeRuntimeEntrypoints,
    ) -> NativeRuntimePollResult:
        status = NativeStatus.from_ffi(result.status)
        event = _native_event_from_ffi(result.event, entrypoints) if result.has_event else None
        return cls(status, event)


def _native_event_from_ffi(
    event: _NnrpEvent,
    entrypoints: NativeRuntimeEntrypoints,
) -> NativePolledEvent:
    payload = _copy_owned_event_payload(entrypoints, event)
    context = _NativeRuntimeEventContext(
        kind=int(event.kind),
        connection=_native_handle_from_trusted_ffi(event.connection),
        session=_native_handle_from_trusted_ffi(event.session),
        operation=_native_handle_from_trusted_ffi(event.operation),
        diagnostic=NativeRuntimeDiagnostic.from_ffi(event.diagnostic),
    )
    present = int(event.header.present)
    if present == 0:
        return NativeLifecycleEvent(
            kind=context.kind,
            connection=context.connection,
            session=context.session,
            operation=context.operation,
            payload=payload,
            diagnostic=context.diagnostic,
        )
    if present != 1:
        raise NativeProtocolError(
            NativeStatus(FFI_STATUS_PROTOCOL_ERROR),
            f"invalid native runtime event header presence marker {present}",
        )
    try:
        message_type = MessageType(int(event.header.message_type))
    except ValueError as error:
        raise NativeProtocolError(
            NativeStatus(FFI_STATUS_PROTOCOL_ERROR),
            f"unknown Preview4 runtime message type 0x{int(event.header.message_type):02x}",
        ) from error
    try:
        wire_format = WireFormat(int(event.header.wire_format))
    except ValueError as error:
        raise NativeProtocolError(
            NativeStatus(FFI_STATUS_PROTOCOL_ERROR),
            f"unknown Preview4 runtime wire format {int(event.header.wire_format)}",
        ) from error
    try:
        header = RuntimeFrameHeader(
            message_type=message_type,
            flags=HeaderFlags(int(event.header.flags)),
            session_id=int(event.header.session_id),
            frame_id=int(event.header.frame_id),
            view_id=int(event.header.view_id),
            route_id=int(event.header.route_id),
            trace_id=int(event.header.trace_id),
            version_major=int(event.header.version_major),
            wire_format=wire_format,
        )
    except ValueError as error:
        raise NativeProtocolError(
            NativeStatus(FFI_STATUS_PROTOCOL_ERROR),
            f"invalid Preview4 runtime frame header: {error}",
        ) from error
    return _decode_wire_runtime_event(header, payload, context)


_RUNTIME_METADATA_KIND_BY_MESSAGE = {
    MessageType.CANCEL: RuntimeEventMetadataKind.CONTROL_REQUEST,
    MessageType.ABORT: RuntimeEventMetadataKind.CONTROL_REQUEST,
    MessageType.PRIORITY_UPDATE: RuntimeEventMetadataKind.SCHEDULING,
    MessageType.DEADLINE: RuntimeEventMetadataKind.SCHEDULING,
    MessageType.EXPIRE_AT: RuntimeEventMetadataKind.SCHEDULING,
    MessageType.SUPERSEDE: RuntimeEventMetadataKind.SUPERSEDE,
    MessageType.BUDGET_UPDATE: RuntimeEventMetadataKind.BUDGET,
    MessageType.PROGRESS: RuntimeEventMetadataKind.PROGRESS,
    MessageType.PARTIAL_RESULT: RuntimeEventMetadataKind.PARTIAL_RESULT,
    MessageType.BACKPRESSURE: RuntimeEventMetadataKind.PRESSURE,
    MessageType.CREDIT_UPDATE: RuntimeEventMetadataKind.PRESSURE,
    MessageType.CAPABILITY_NEGOTIATION: RuntimeEventMetadataKind.CAPABILITY,
    MessageType.DEGRADE_PROFILE: RuntimeEventMetadataKind.CAPABILITY,
    MessageType.ROUTE_HINT: RuntimeEventMetadataKind.ROUTE_HINT,
    MessageType.EXECUTION_HINT: RuntimeEventMetadataKind.ROUTE_HINT,
    MessageType.TRACE_CONTEXT: RuntimeEventMetadataKind.TRACE_CONTEXT,
    MessageType.RESULT_DROP_REASON: RuntimeEventMetadataKind.RESULT_DROP_REASON,
    MessageType.ERROR_RECOVERABLE: RuntimeEventMetadataKind.RECOVERABLE_ERROR,
    MessageType.RETRY_AFTER: RuntimeEventMetadataKind.RETRY_AFTER,
    MessageType.OBJECT_DECLARE: RuntimeEventMetadataKind.OBJECT_DESCRIPTOR,
    MessageType.OBJECT_REF: RuntimeEventMetadataKind.OBJECT_REFERENCE,
    MessageType.OBJECT_RELEASE: RuntimeEventMetadataKind.OBJECT_RELEASE,
    MessageType.OBJECT_PATCH: RuntimeEventMetadataKind.OBJECT_DELTA,
    MessageType.OBJECT_DELTA: RuntimeEventMetadataKind.OBJECT_DELTA,
    MessageType.CACHE_REFERENCE: RuntimeEventMetadataKind.CACHE_REFERENCE,
    MessageType.CACHE_MISS: RuntimeEventMetadataKind.CACHE_MISS,
}
_RUNTIME_OBJECT_MESSAGE_TYPES = {
    MessageType.OBJECT_DECLARE,
    MessageType.OBJECT_REF,
    MessageType.OBJECT_RELEASE,
    MessageType.OBJECT_PATCH,
    MessageType.OBJECT_DELTA,
    MessageType.CACHE_REFERENCE,
    MessageType.CACHE_MISS,
}
_RUNTIME_BODY_MESSAGE_TYPES = {
    MessageType.PROGRESS,
    MessageType.PARTIAL_RESULT,
    MessageType.CAPABILITY_NEGOTIATION,
    MessageType.DEGRADE_PROFILE,
    MessageType.ROUTE_HINT,
    MessageType.EXECUTION_HINT,
    MessageType.TRACE_CONTEXT,
    MessageType.OBJECT_DECLARE,
    MessageType.OBJECT_REF,
    MessageType.CACHE_REFERENCE,
}
_RUNTIME_DIAGNOSTIC_MESSAGE_TYPES = {
    MessageType.CANCEL,
    MessageType.ABORT,
    MessageType.SUPERSEDE,
    MessageType.RESULT_DROP_REASON,
    MessageType.ERROR_RECOVERABLE,
    MessageType.RETRY_AFTER,
    MessageType.OBJECT_RELEASE,
    MessageType.CACHE_MISS,
}


def _copy_owned_event_payload(entrypoints: NativeRuntimeEntrypoints, event: _NnrpEvent) -> bytes:
    try:
        return _copy_buffer_view(event.payload)
    finally:
        _release_owned_event_payload(entrypoints, event)


def _release_owned_event_payload(entrypoints: NativeRuntimeEntrypoints, event: _NnrpEvent) -> None:
    if int(event.payload_owner.kind) == HANDLE_KIND_BUFFER:
        status = entrypoints.buffer_release(event.payload_owner)
        raise_for_native_status(status)


def _decode_wire_runtime_event(
    header: RuntimeFrameHeader,
    payload: bytes,
    context: _NativeRuntimeEventContext,
) -> NativeRuntimeEvent:
    message_type = MessageType(header.message_type)
    if message_type in {MessageType.FRAME_CANCEL, MessageType.RESULT_DROP}:
        if payload:
            raise NativeProtocolError(
                NativeStatus(FFI_STATUS_PROTOCOL_ERROR),
                f"{message_type.name} runtime event must not carry a payload",
            )
        metadata = RuntimeEventMetadata(RuntimeEventMetadataKind.NONE)
        tail = RuntimeEventTail.none()
    elif message_type == MessageType.SESSION_CLOSE:
        value = SessionCloseMetadata.unpack(payload)
        metadata = RuntimeEventMetadata(RuntimeEventMetadataKind.SESSION_CLOSE, value)
        tail = RuntimeEventTail.none()
    elif message_type == MessageType.FRAME_SUBMIT:
        value, body = _unpack_runtime_event_prefix(FrameSubmitMetadata, payload)
        metadata = RuntimeEventMetadata(RuntimeEventMetadataKind.FRAME_SUBMIT, value)
        tail = RuntimeEventTail.with_body(body)
    elif message_type == MessageType.RESULT_PUSH:
        value, body = _unpack_runtime_event_prefix(ResultPushMetadata, payload)
        metadata = RuntimeEventMetadata(RuntimeEventMetadataKind.RESULT_PUSH, value)
        tail = RuntimeEventTail.with_body(body)
    elif message_type == MessageType.FLOW_UPDATE:
        value = FlowUpdateMetadata.unpack(payload)
        metadata = RuntimeEventMetadata(RuntimeEventMetadataKind.FLOW_UPDATE, value)
        tail = RuntimeEventTail.none()
    elif message_type == MessageType.RESULT_HINT:
        value = ResultHintMetadata.unpack(payload)
        metadata = RuntimeEventMetadata(RuntimeEventMetadataKind.RESULT_HINT, value)
        tail = RuntimeEventTail.none()
    elif message_type == MessageType.CACHE_INVALIDATE:
        value = CacheInvalidateMetadata.unpack(payload)
        metadata = RuntimeEventMetadata(RuntimeEventMetadataKind.CACHE_INVALIDATE, value)
        tail = RuntimeEventTail.none()
    elif message_type in _RUNTIME_OBJECT_MESSAGE_TYPES:
        object_decoded = decode_runtime_object_metadata(message_type, payload)
        kind = _RUNTIME_METADATA_KIND_BY_MESSAGE[message_type]
        object_metadata = object_decoded.metadata
        metadata = RuntimeEventMetadata(kind, object_metadata)
        if message_type in {MessageType.OBJECT_PATCH, MessageType.OBJECT_DELTA}:
            if not isinstance(object_metadata, ObjectDeltaMetadata):
                raise NativeRuntimeError(
                    NativeStatus(FFI_STATUS_PROTOCOL_ERROR),
                    f"{message_type.name} requires ObjectDeltaMetadata",
                )
            metadata_length = int(object_metadata.metadata_bytes)
            tail = RuntimeEventTail.with_metadata_body_and_delta(
                object_decoded.tail[:metadata_length],
                object_decoded.tail[metadata_length:],
            )
        elif message_type in _RUNTIME_BODY_MESSAGE_TYPES:
            tail = RuntimeEventTail.with_body(object_decoded.tail)
        else:
            tail = RuntimeEventTail.with_diagnostic(object_decoded.tail)
    elif message_type in _RUNTIME_METADATA_KIND_BY_MESSAGE:
        control_decoded = decode_runtime_control_metadata(message_type, payload)
        metadata = RuntimeEventMetadata(
            _RUNTIME_METADATA_KIND_BY_MESSAGE[message_type],
            control_decoded.metadata,
        )
        if message_type in _RUNTIME_BODY_MESSAGE_TYPES:
            tail = RuntimeEventTail.with_body(control_decoded.tail)
        elif message_type in _RUNTIME_DIAGNOSTIC_MESSAGE_TYPES:
            tail = RuntimeEventTail.with_diagnostic(control_decoded.tail)
        else:
            tail = RuntimeEventTail.none()
    else:
        raise NativeProtocolError(
            NativeStatus(FFI_STATUS_PROTOCOL_ERROR),
            f"message type {message_type.name} is not a frozen Preview4 runtime event",
        )
    return NativeRuntimeEvent(header, metadata, tail, context)


def _unpack_runtime_event_prefix(metadata_type: type[Any], payload: bytes) -> tuple[Any, bytes]:
    metadata_length = metadata_type.STRUCT.size
    if len(payload) < metadata_length:
        raise NativeProtocolError(
            NativeStatus(FFI_STATUS_PROTOCOL_ERROR),
            f"{metadata_type.__name__} payload is shorter than {metadata_length} bytes",
        )
    return metadata_type.unpack(payload[:metadata_length]), payload[metadata_length:]


def _runtime_event_context(event: NativeRuntimeEvent) -> _NativeRuntimeEventContext:
    context = event._native_context
    if not isinstance(context, _NativeRuntimeEventContext):
        return _NativeRuntimeEventContext(EVENT_KIND_RUNTIME_FRAME)
    return context


def _native_event_kind(event: NativePolledEvent) -> int:
    if isinstance(event, NativeRuntimeEvent):
        return _runtime_event_context(event).kind
    return event.kind


def _event_operation_id(event: NativePolledEvent) -> int:
    if isinstance(event, NativeRuntimeEvent):
        value = event.metadata.value
        if event.header.message_type is MessageType.SUPERSEDE and isinstance(value, SupersedeMetadata):
            return value.old_operation_id
        context = _runtime_event_context(event)
        if context.diagnostic.related_operation_id:
            return context.diagnostic.related_operation_id
        return int(getattr(value, "operation_id", 0))
    return event.diagnostic.related_operation_id


class NativeSessionPriorityClass(StrEnum):
    INTERACTIVE = "interactive"
    BALANCED = "balanced"
    BACKGROUND = "background"

    @property
    def code(self) -> int:
        return _SESSION_PRIORITY_CLASS_CODES[self]

    @classmethod
    def from_code(cls, code: int) -> NativeSessionPriorityClass:
        _validate_u32("priority_class", code)
        try:
            return _SESSION_PRIORITY_CLASS_BY_CODE[code]
        except KeyError as exc:
            raise NativeHandleError(f"unknown native session priority class {code}") from exc


@dataclass(frozen=True)
class NativeOperationSchedulingHint:
    parent_operation_id: int | None = None
    operation_group_id: int | None = None
    deadline_ms: int | None = None

    def __post_init__(self) -> None:
        if self.parent_operation_id is not None:
            _validate_u64("parent_operation_id", self.parent_operation_id)
        if self.operation_group_id is not None:
            _validate_u64("operation_group_id", self.operation_group_id)
        if self.deadline_ms is not None:
            _validate_u32("deadline_ms", self.deadline_ms)

    @property
    def has_scope(self) -> bool:
        return self.parent_operation_id is not None or self.operation_group_id is not None


@dataclass(frozen=True, slots=True)
class NativeRuntimeResult:
    operation_id: int
    terminal_state: ResultTerminalState
    event: NativeTerminalEvent

    def __post_init__(self) -> None:
        if type(self.operation_id) is not int or not 1 <= self.operation_id <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("operation_id must be a non-zero unsigned 64-bit integer")
        object.__setattr__(self, "terminal_state", ResultTerminalState(self.terminal_state))
        if not isinstance(self.event, NativeTerminalEvent):
            raise TypeError("event must be a NativeTerminalEvent")
        lifecycle = self.event.as_lifecycle()
        if lifecycle is not None and lifecycle.operation_id != self.operation_id:
            raise ValueError("lifecycle event operation_id must match result operation_id")
        expected_terminal_state = _terminal_state_from_terminal_event(self.event)
        if self.terminal_state is not expected_terminal_state:
            raise ValueError(
                f"terminal_state {self.terminal_state.name} does not match "
                f"{self.event.kind.value} terminal evidence ({expected_terminal_state.name})"
            )

    @classmethod
    def _from_polled_event(
        cls,
        event: NativePolledEvent,
        *,
        operation_id: int | None = None,
    ) -> NativeRuntimeResult:
        selected_operation_id = _event_operation_id(event) if operation_id is None else operation_id
        if isinstance(event, NativeRuntimeEvent):
            message_type = event.header.message_type
            if message_type is MessageType.RESULT_PUSH:
                if event.metadata.kind is not RuntimeEventMetadataKind.RESULT_PUSH:
                    raise NativeHandleError("RESULT_PUSH terminal event requires ResultPushMetadata")
                terminal_state = ResultTerminalState.SUCCESS
            elif message_type in {MessageType.RESULT_DROP, MessageType.RESULT_DROP_REASON}:
                terminal_state = ResultTerminalState.DROPPED
            else:
                raise NativeHandleError(f"{message_type.name} is not a terminal result event")
            terminal_event = NativeTerminalEvent.runtime(event)
        else:
            if event.kind == EVENT_KIND_OPERATION_LIFECYCLE:
                lifecycle = _operation_lifecycle_from_native_event(event)
                if lifecycle.operation_id != selected_operation_id:
                    raise NativeHandleError("operation lifecycle identity does not match the requested operation")
            else:
                lifecycle = OperationLifecycleEvent(
                    selected_operation_id,
                    _operation_state_from_lifecycle_event(event),
                )
            terminal_state = _terminal_state_from_operation_state(lifecycle.state)
            terminal_event = NativeTerminalEvent.lifecycle(lifecycle)
        return cls(selected_operation_id, terminal_state, terminal_event)


def _operation_state_from_lifecycle_event(event: NativeLifecycleEvent) -> OperationState:
    if not event.diagnostic.status.succeeded or event.kind == EVENT_KIND_ERROR:
        return OperationState.FAILED
    if event.kind == EVENT_KIND_RESULT_DROPPED:
        return OperationState.CANCELLED
    if event.kind == EVENT_KIND_RESULT_PUSHED:
        return OperationState.COMPLETED
    raise NativeHandleError(f"{event.kind_name} is not a terminal operation lifecycle event")


def _operation_lifecycle_from_native_event(event: NativeLifecycleEvent) -> OperationLifecycleEvent:
    if event.kind != EVENT_KIND_OPERATION_LIFECYCLE:
        raise NativeProtocolError(
            NativeStatus(FFI_STATUS_PROTOCOL_ERROR),
            f"headerless {event.kind_name} event is not an operation lifecycle event",
        )
    operation_id = _event_operation_id(event)
    if operation_id == 0:
        raise NativeProtocolError(
            NativeStatus(FFI_STATUS_PROTOCOL_ERROR),
            "operation_lifecycle event has no operation identity",
        )
    if len(event.payload) != 1:
        raise NativeProtocolError(
            NativeStatus(FFI_STATUS_PROTOCOL_ERROR),
            "operation_lifecycle payload must contain exactly one OperationState byte",
        )
    try:
        state = OperationState(event.payload[0])
    except ValueError as error:
        raise NativeProtocolError(
            NativeStatus(FFI_STATUS_PROTOCOL_ERROR),
            f"operation_lifecycle payload contains unknown OperationState {event.payload[0]}",
        ) from error
    return OperationLifecycleEvent(operation_id, state)


def _terminal_state_from_operation_state(state: OperationState) -> ResultTerminalState:
    try:
        return {
            OperationState.COMPLETED: ResultTerminalState.SUCCESS,
            OperationState.CANCELLED: ResultTerminalState.CANCELLED,
            OperationState.SUPERSEDED: ResultTerminalState.DROPPED,
            OperationState.FAILED: ResultTerminalState.ERROR,
        }[OperationState(state)]
    except KeyError as error:
        raise NativeHandleError(f"{OperationState(state).name} is not a terminal operation state") from error


def _terminal_state_from_terminal_event(event: NativeTerminalEvent) -> ResultTerminalState:
    runtime_event = event.as_runtime()
    if runtime_event is not None:
        message_type = runtime_event.header.message_type
        if message_type is MessageType.RESULT_PUSH:
            if runtime_event.metadata.kind is not RuntimeEventMetadataKind.RESULT_PUSH:
                raise ValueError("RESULT_PUSH terminal event requires ResultPushMetadata")
            return ResultTerminalState.SUCCESS
        if message_type in {MessageType.RESULT_DROP, MessageType.RESULT_DROP_REASON}:
            return ResultTerminalState.DROPPED
        raise ValueError(f"{message_type.name} is not terminal result evidence")

    lifecycle = event.as_lifecycle()
    if lifecycle is None:
        raise TypeError("event must contain runtime or lifecycle terminal evidence")
    try:
        return _terminal_state_from_operation_state(lifecycle.state)
    except NativeHandleError as error:
        raise ValueError(str(error)) from error


@dataclass(frozen=True)
class NativeRuntimeOperation:
    entrypoints: NativeRuntimeEntrypoints
    session: NativeSessionHandle
    handle: NativeOperationHandle
    operation_id: int
    frame_id: int
    scheduling_hint: NativeOperationSchedulingHint = field(default_factory=NativeOperationSchedulingHint)
    parent_operation_id: int | None = None
    operation_group_id: int | None = None
    _owner: NativeRuntimeSession | None = field(default=None, repr=False, compare=False)

    def cancel(self) -> None:
        if self._owner is not None:
            self._owner.cancel(frame_id=self.frame_id)
            return
        request = _NnrpClientCancelRequest(self.session.to_ffi(), self.frame_id)
        status = self.entrypoints.client_cancel(request)
        raise_for_native_status(status)

    def cache_backend(self, *, now_ms: int = 0, ttl_ms: int = 0, expected_version: int = 0) -> NativeCacheLeaseBackend:
        return NativeCacheLeaseBackend(
            self.entrypoints,
            self.handle.handle,
            now_ms=now_ms,
            ttl_ms=ttl_ms,
            expected_version=expected_version,
        )


@dataclass
class NativeCacheLeaseBackend:
    entrypoints: NativeRuntimeEntrypoints
    owner: NativeHandle
    now_ms: int = 0
    ttl_ms: int = 0
    expected_version: int = 0
    _lease_handles: dict[tuple[int, int, int, int], NativeCacheLeaseHandle] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _validate_u64("now_ms", self.now_ms)
        _validate_u32("ttl_ms", self.ttl_ms)
        _validate_u64("expected_version", self.expected_version)

    def query_cache(self, identity: Any) -> Any:
        request = self._request(identity, ttl_ms=0)
        result = _NnrpCacheLeaseResult()
        status = self.entrypoints.cache_query(request, ctypes.byref(result))
        return self._finish_cache_status(status, result)

    def touch_cache(self, identity: Any, *, ttl_ms: int | None = None) -> Any:
        selected_ttl_ms = self.ttl_ms if ttl_ms is None else ttl_ms
        _validate_u32("ttl_ms", selected_ttl_ms)
        request = self._request(identity, ttl_ms=selected_ttl_ms)
        result = _NnrpCacheLeaseResult()
        status = self.entrypoints.cache_touch(request, ctypes.byref(result))
        return self._finish_cache_status(status, result)

    def prefetch_cache(self, identities: tuple[Any, ...]) -> tuple[Any, ...]:
        object_count = len(identities)
        if object_count == 0:
            return ()
        object_array = (_NnrpCacheObjectId * object_count)(
            *(_cache_identity_to_ffi(identity) for identity in identities)
        )
        result_array = (_NnrpCacheLeaseResult * object_count)()
        status = self.entrypoints.cache_prefetch(
            self.owner.to_ffi(),
            ctypes.cast(object_array, ctypes.POINTER(_NnrpCacheObjectId)),
            object_count,
            self.now_ms,
            self.ttl_ms,
            ctypes.cast(result_array, ctypes.POINTER(_NnrpCacheLeaseResult)),
        )
        raise_for_native_status(status)
        return tuple(self._cache_result_from_ffi(result_array[index]) for index in range(object_count))

    def release_cache(self, identity: Any) -> Any:
        key = _cache_identity_key(identity)
        lease = self._lease_handles.get(key)
        if lease is None:
            from nnrp.cache import CacheLeaseOutcome, CacheLeaseResult

            return CacheLeaseResult(identity=identity, outcome=CacheLeaseOutcome.MISSING)
        result = _NnrpCacheLeaseResult()
        status = self.entrypoints.cache_release(lease.to_ffi(), ctypes.byref(result))
        converted = self._finish_cache_status(status, result)
        self._lease_handles.pop(key, None)
        return converted

    def _request(self, identity: Any, *, ttl_ms: int) -> _NnrpCacheLeaseRequest:
        return _NnrpCacheLeaseRequest(
            self.owner.to_ffi(),
            _cache_identity_to_ffi(identity),
            self.expected_version,
            self.now_ms,
            ttl_ms,
        )

    def _finish_cache_status(self, status: _NnrpFfiStatus, result: _NnrpCacheLeaseResult) -> Any:
        try:
            raise_for_native_status(status)
        except NativeProtocolError:
            if result.outcome_code in {CACHE_LEASE_OUTCOME_MISS, CACHE_LEASE_OUTCOME_EXPIRED}:
                return self._cache_result_from_ffi(result)
            raise
        return self._cache_result_from_ffi(result)

    def _cache_result_from_ffi(self, result: _NnrpCacheLeaseResult) -> Any:
        converted = _cache_lease_result_from_ffi(result)
        if result.lease_handle.kind == HANDLE_KIND_CACHE_LEASE:
            self._lease_handles[_cache_identity_key(converted.identity)] = NativeCacheLeaseHandle.from_ffi(
                result.lease_handle
            )
        return converted


@runtime_checkable
class NativeRuntimeBackend(Protocol):
    def connect(
        self,
        *,
        connection_id: int,
        generation: int,
        transport_connection: NativeTransportConnection,
    ) -> NativeRuntimeConnection: ...


@dataclass(frozen=True)
class NativeRuntimeClient:
    entrypoints: NativeRuntimeEntrypoints

    def connect(
        self,
        *,
        connection_id: int,
        generation: int,
        transport_connection: NativeTransportConnection,
    ) -> NativeRuntimeConnection:
        return transport_connection._adopt_client_role(
            self.entrypoints,
            connection_id=connection_id,
            generation=generation,
        )

    def bind_server(
        self,
        *,
        server_id: int,
        generation: int,
        transport_listener: NativeTransportListener,
    ) -> NativeRuntimeServer:
        from nnrp.schema import token_delta_schema_descriptor

        descriptor = token_delta_schema_descriptor()
        return transport_listener._adopt_server_role(
            self.entrypoints,
            server_id=server_id,
            generation=generation,
            supported_profiles=(int(descriptor.profile_id),),
            supported_cache_objects=(),
            max_cache_objects=0,
            max_cache_object_bytes=0,
            resume_token_bytes=24,
            max_in_flight_operations=4,
            granted_operation_credit=2,
            lease_ttl_ms=30_000,
            resume_window_ms=120_000,
            schema_descriptors=(descriptor,),
            application_policy=None,
        )


@dataclass(frozen=True)
class NativeRuntimeServer:
    entrypoints: NativeRuntimeEntrypoints
    handle: NativeConnectionHandle
    transport_name: str
    _policy_dispatcher: _NativeServerPolicyDispatcher | None = field(default=None, repr=False, compare=False)
    _closed: bool = field(default=False, init=False, repr=False, compare=False)
    _accept_ticket: NativeHandle | None = field(default=None, init=False, repr=False, compare=False)
    _accept_session_handle_id: int | None = field(default=None, init=False, repr=False, compare=False)
    _accept_generation: int | None = field(default=None, init=False, repr=False, compare=False)

    def accept_session(
        self,
        *,
        session_handle_id: int,
        generation: int,
        timeout_ms: int = 0,
    ) -> NativeRuntimeServerSession:
        self._ensure_open()
        _validate_u64("session_handle_id", session_handle_id)
        _validate_u32("generation", generation)
        _validate_u32("timeout_ms", timeout_ms)
        accept_ticket = self._accept_ticket
        if accept_ticket is None:
            begin_request = _NnrpServerAcceptBeginRequest(
                self.handle.to_ffi(),
                session_handle_id,
                generation,
                0,
            )
            out_accept = _NnrpHandle()
            status = self.entrypoints.server_accept_begin(begin_request, ctypes.byref(out_accept))
            raise_for_native_status(status)
            accept_ticket = NativeHandle.from_ffi(out_accept)
            accept_ticket.require_kind(HANDLE_KIND_SERVER_ACCEPT)
            object.__setattr__(self, "_accept_ticket", accept_ticket)
            object.__setattr__(self, "_accept_session_handle_id", session_handle_id)
            object.__setattr__(self, "_accept_generation", generation)
        elif session_handle_id != self._accept_session_handle_id or generation != self._accept_generation:
            raise NativeInvalidStateError(
                NativeStatus(FFI_STATUS_INVALID_STATE),
                "pending native server accept ticket requires the original session handle id and generation",
            )

        wait_request = _NnrpServerAcceptWaitRequest(accept_ticket.to_ffi(), timeout_ms, 0)
        status = self.entrypoints.server_accept_wait(wait_request)
        raise_for_native_status(status)

        claim_request = _NnrpServerAcceptClaimRequest(
            accept_ticket.to_ffi(),
            session_handle_id,
            generation,
            0,
        )
        result = _NnrpServerAcceptResult()
        status = self.entrypoints.server_accept_claim(claim_request, ctypes.byref(result))
        raise_for_native_status(status)
        object.__setattr__(self, "_accept_ticket", None)
        object.__setattr__(self, "_accept_session_handle_id", None)
        object.__setattr__(self, "_accept_generation", None)
        try:
            active_transport_id = TransportId(int(result.active_transport_id))
        except ValueError as error:
            raise NativeArtifactError(
                f"native server accept returned unsupported transport id {int(result.active_transport_id)}"
            ) from error
        transport_name = NATIVE_TRANSPORT_NAME_BY_ID[active_transport_id]
        return NativeRuntimeServerSession(
            self.entrypoints,
            self.handle,
            NativeSessionHandle.from_ffi(result.session),
            transport_name,
        )

    def close(self) -> None:
        self._ensure_open()
        first_error: BaseException | None = None
        if self._policy_dispatcher is not None:
            try:
                self._policy_dispatcher.close()
            except BaseException as error:
                first_error = error
        try:
            self._release_pending_accept_ticket()
        except BaseException as error:
            first_error = first_error or error
        try:
            raise_for_native_status(self.entrypoints.client_close_connection(self.handle.to_ffi()))
        except BaseException as error:
            first_error = first_error or error
        object.__setattr__(self, "_closed", True)
        if first_error is not None:
            raise first_error

    def _release_pending_accept_ticket(self) -> None:
        accept_ticket = self._accept_ticket
        if accept_ticket is None:
            return
        try:
            raise_for_native_status(self.entrypoints.server_accept_release(accept_ticket.to_ffi()))
        finally:
            object.__setattr__(self, "_accept_ticket", None)
            object.__setattr__(self, "_accept_session_handle_id", None)
            object.__setattr__(self, "_accept_generation", None)

    def _ensure_open(self) -> None:
        if self._closed:
            raise NativeInvalidStateError(NativeStatus(FFI_STATUS_INVALID_STATE), "native runtime server is closed")


@dataclass(frozen=True)
class NativeRuntimeConnection:
    entrypoints: NativeRuntimeEntrypoints
    handle: NativeConnectionHandle
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    def open_session(
        self,
        *,
        requested_session_id: int,
        profile_id: int,
        schema_id: int,
        schema_version: int,
        priority_class: NativeSessionPriorityClass | str = NativeSessionPriorityClass.BALANCED,
        default_deadline_ms: int = 500,
        max_in_flight_operations: int = 4,
        lease_ttl_hint_ms: int = 30_000,
        allow_resume: bool = False,
        resume_token_bytes: int = 0,
        cache_hints: Iterable[int] = (),
    ) -> NativeRuntimeSession:
        self._ensure_open()
        selected_priority_class = NativeSessionPriorityClass(priority_class)
        cache_hint_slice, cache_hint_owner = _u32_slice_from_values(cache_hints)
        request = _NnrpSessionOpenRequest(
            connection=self.handle.to_ffi(),
            requested_session_id=requested_session_id,
            session_handle_id=_allocate_native_handle_id(),
            generation=1,
            profile_id=profile_id,
            priority_class=selected_priority_class.code,
            allow_resume=int(allow_resume),
            schema_id=schema_id,
            schema_version=schema_version,
            default_deadline_ms=default_deadline_ms,
            max_in_flight_operations=max_in_flight_operations,
            reserved0=0,
            lease_ttl_hint_ms=lease_ttl_hint_ms,
            resume_token_bytes=resume_token_bytes,
            cache_hints=cache_hint_slice,
        )
        out_session = _NnrpHandle()
        status = self.entrypoints.client_open_session(request, ctypes.byref(out_session))
        del cache_hint_owner
        raise_for_native_status(status)
        return NativeRuntimeSession(
            self.entrypoints,
            self.handle,
            NativeSessionHandle.from_ffi(out_session),
            selected_priority_class,
        )

    def resume_session(
        self,
        *,
        recovery_ticket: bytes | bytearray | memoryview,
        requested_session_id: int,
        profile_id: int,
        schema_id: int,
        schema_version: int,
        resume_token_bytes: int,
        priority_class: NativeSessionPriorityClass | str = NativeSessionPriorityClass.BALANCED,
        default_deadline_ms: int = 500,
        max_in_flight_operations: int = 4,
        lease_ttl_hint_ms: int = 30_000,
        cache_hints: Iterable[int] = (),
    ) -> tuple[NativeRuntimeSession, NativeSessionRecoveryOutcome]:
        self._ensure_open()
        selected_priority_class = NativeSessionPriorityClass(priority_class)
        cache_hint_slice, cache_hint_owner = _u32_slice_from_values(cache_hints)
        ticket_view, ticket_owner = _buffer_view_from_payload(recovery_ticket)
        request = _NnrpSessionResumeRequest(
            _NnrpSessionOpenRequest(
                connection=self.handle.to_ffi(),
                requested_session_id=requested_session_id,
                session_handle_id=_allocate_native_handle_id(),
                generation=1,
                profile_id=profile_id,
                priority_class=selected_priority_class.code,
                allow_resume=1,
                schema_id=schema_id,
                schema_version=schema_version,
                default_deadline_ms=default_deadline_ms,
                max_in_flight_operations=max_in_flight_operations,
                reserved0=0,
                lease_ttl_hint_ms=lease_ttl_hint_ms,
                resume_token_bytes=resume_token_bytes,
                cache_hints=cache_hint_slice,
            ),
            ticket_view,
        )
        out_session = _NnrpHandle()
        out_outcome = _NnrpSessionRecoveryOutcome()
        status = self.entrypoints.client_resume_session(request, ctypes.byref(out_session), ctypes.byref(out_outcome))
        del cache_hint_owner, ticket_owner
        raise_for_native_status(status)
        return (
            NativeRuntimeSession(
                self.entrypoints,
                self.handle,
                NativeSessionHandle.from_ffi(out_session),
                selected_priority_class,
            ),
            NativeSessionRecoveryOutcome.from_ffi(out_outcome),
        )

    def schema_registry(self) -> NativeSchemaRegistry:
        self._ensure_open()
        return NativeSchemaRegistry.create(self.entrypoints)

    def acquire_buffer_copy(self, payload: bytes | bytearray | memoryview) -> NativeOwnedBuffer:
        self._ensure_open()
        return NativeOwnedBuffer.acquire_copy(self.entrypoints, payload)

    def acquire_object_metadata_copy(self, payload: bytes | bytearray | memoryview) -> NativeObjectMetadataBuffer:
        self._ensure_open()
        return NativeObjectMetadataBuffer.acquire_copy(self.entrypoints, payload)

    def acquire_object_patch_metadata_copy(
        self,
        metadata: Any,
        *,
        metadata_tail: bytes | bytearray | memoryview = b"",
        delta: bytes | bytearray | memoryview = b"",
    ) -> NativeObjectMetadataBuffer:
        from nnrp.runtime import patch_runtime_object

        return self.acquire_object_metadata_copy(
            patch_runtime_object(metadata, metadata_tail=metadata_tail, delta=delta)
        )

    def acquire_object_delta_metadata_copy(
        self,
        metadata: Any,
        *,
        metadata_tail: bytes | bytearray | memoryview = b"",
        delta: bytes | bytearray | memoryview = b"",
    ) -> NativeObjectMetadataBuffer:
        from nnrp.runtime import delta_runtime_object

        return self.acquire_object_metadata_copy(
            delta_runtime_object(metadata, metadata_tail=metadata_tail, delta=delta)
        )

    def create_object_descriptor(
        self,
        descriptor: Any,
        *,
        metadata: bytes | bytearray | memoryview = b"",
    ) -> NativeObjectDescriptor:
        self._ensure_open()
        return NativeObjectDescriptor.create(self.entrypoints, descriptor, metadata)

    def cache_backend(self, *, now_ms: int = 0, ttl_ms: int = 0, expected_version: int = 0) -> NativeCacheLeaseBackend:
        return NativeCacheLeaseBackend(
            self.entrypoints,
            self.handle.handle,
            now_ms=now_ms,
            ttl_ms=ttl_ms,
            expected_version=expected_version,
        )

    def close(self) -> None:
        self._ensure_open()
        status = self.entrypoints.client_close_connection(self.handle.to_ffi())
        raise_for_native_status(status)
        object.__setattr__(self, "_closed", True)

    def _ensure_open(self) -> None:
        if self._closed:
            raise NativeInvalidStateError(NativeStatus(FFI_STATUS_INVALID_STATE), "native runtime connection is closed")


def _join_runtime_object_tail(
    metadata_body: bytes | bytearray | memoryview,
    delta: bytes | bytearray | memoryview,
) -> bytes:
    return bytes(metadata_body) + bytes(delta)


def _encode_native_runtime_frame(
    message_type: MessageType,
    metadata: _FixedRuntimeMetadata | CacheInvalidateMetadata,
    tail: bytes | bytearray | memoryview,
) -> bytes:
    if message_type == MessageType.CACHE_INVALIDATE:
        if not isinstance(metadata, CacheInvalidateMetadata):
            raise TypeError("CACHE_INVALIDATE requires CacheInvalidateMetadata")
        if memoryview(tail).nbytes:
            raise ValueError("CACHE_INVALIDATE does not declare a tail")
        return metadata.pack()
    if message_type in _RUNTIME_OBJECT_MESSAGE_TYPES:
        if not isinstance(metadata, _FixedRuntimeMetadata):
            raise TypeError(f"{message_type.name} requires runtime object metadata")
        return encode_runtime_object_metadata(message_type, metadata, tail=tail)
    if not isinstance(metadata, _FixedRuntimeMetadata):
        raise TypeError(f"{message_type.name} requires runtime control metadata")
    return encode_runtime_control_metadata(message_type, metadata, tail=tail)


def _canonical_token_submit_metadata(operation_id: int) -> FrameSubmitMetadata:
    return FrameSubmitMetadata(
        src_width=0,
        src_height=0,
        tile_width=0,
        tile_height=0,
        tile_count=0,
        section_count=0,
        frame_class=0,
        input_profile=InputProfile.UNSPECIFIED,
        tile_index_mode=TileIndexMode.DENSE_RANGE,
        reserved0=0,
        latency_budget_ms=25,
        target_fps_x100=0,
        retry_of_frame=0,
        tile_base_id=0,
        camera_bytes=0,
        tile_index_bytes=0,
        operation_id=operation_id,
        submit_mode=SubmitMode.INLINE,
        budget_policy=BudgetPolicy.NONE,
        loss_tolerance_policy=0xFF,
        object_ref_mask=0,
        dependency_frame_id=0,
        payload_kind_bitmap=PayloadKind.TOKEN_CHUNK,
        payload_frame_count=1,
    )


@dataclass(frozen=True)
class NativeRuntimeSession:
    entrypoints: NativeRuntimeEntrypoints
    connection: NativeConnectionHandle
    handle: NativeSessionHandle
    priority_class: NativeSessionPriorityClass = NativeSessionPriorityClass.BALANCED
    _closed: bool = field(default=False, init=False, repr=False, compare=False)
    _poll_event_buffer: Any = field(default=None, init=False, repr=False, compare=False)
    _poll_event_buffer_capacity: int = field(default=0, init=False, repr=False, compare=False)
    _poll_event_count: Any = field(default=None, init=False, repr=False, compare=False)
    _pending_events: list[NativePolledEvent] = field(default_factory=list, init=False, repr=False, compare=False)
    _poll_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)
    _next_runtime_frame_id: int = field(default=1, init=False, repr=False, compare=False)
    _next_control_sequence: int = field(default=1, init=False, repr=False, compare=False)
    _operation_frames: dict[int, int] = field(default_factory=dict, init=False, repr=False, compare=False)
    _operation_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def recovery_ticket(self) -> NativeSessionRecoveryTicket | None:
        self._ensure_open()
        out_buffer = _NnrpHandle()
        out_ticket = _NnrpBufferView()
        status = self.entrypoints.client_session_recovery_ticket(
            self.handle.to_ffi(),
            ctypes.byref(out_buffer),
            ctypes.byref(out_ticket),
        )
        native_status = NativeStatus.from_ffi(status)
        if native_status.status_code == FFI_STATUS_INVALID_ARGUMENT and native_status.detail_code == 104:
            return None
        raise_for_native_status(native_status)
        owner = NativeHandle.from_ffi(out_buffer)
        owner.require_kind(HANDLE_KIND_BUFFER)
        try:
            encoded = _copy_buffer_view(out_ticket)
        finally:
            raise_for_native_status(self.entrypoints.buffer_release(owner.to_ffi()))
        from nnrp.client.native import NativeSessionRecoveryTicket

        return NativeSessionRecoveryTicket.from_bytes(encoded)

    def await_event(self) -> NativeRuntimePollResult:
        self._ensure_open()
        with self._poll_lock:
            if self._pending_events:
                return NativeRuntimePollResult(NativeStatus.ok(), self._pending_events.pop(0))
            result = _NnrpPollResult()
            status = self.entrypoints.client_await_event(self.handle.to_ffi(), ctypes.byref(result))
            raise_for_native_status(status)
            raise_for_native_status(result.status)
            decoded = NativeRuntimePollResult.from_ffi(result, self.entrypoints)
            if decoded.event is not None:
                self._observe_polled_event(decoded.event)
            return decoded

    def poll_event(self, *, timeout_ms: int = 0) -> NativePolledEvent | None:
        events = self.poll_events_batch(max_events=1, timeout_ms=timeout_ms)
        return events[0] if events else None

    def poll_events(
        self,
        *,
        max_events: int | None = None,
        event_kind: int | None = None,
        timeout_ms: int = 0,
    ) -> tuple[NativePolledEvent, ...]:
        if max_events is not None:
            return self.poll_events_batch(
                max_events=max_events,
                event_kind=event_kind,
                timeout_ms=timeout_ms,
            )
        if event_kind is not None:
            _validate_u32("event_kind", event_kind)

        events: list[NativePolledEvent] = []
        next_timeout_ms = timeout_ms
        while True:
            polled = self.poll_events_batch(max_events=1, timeout_ms=next_timeout_ms)
            next_timeout_ms = 0
            if not polled:
                break
            event = polled[0]
            if event_kind is None or _native_event_kind(event) == event_kind:
                events.append(event)
        return tuple(events)

    def poll_events_batch(
        self,
        *,
        max_events: int,
        event_kind: int | None = None,
        timeout_ms: int = 0,
    ) -> tuple[NativePolledEvent, ...]:
        self._ensure_open()
        _require_bounded_integer("max_events", max_events, 0xFFFFFFFF)
        _require_bounded_integer("timeout_ms", timeout_ms, 0xFFFFFFFF)
        if max_events == 0:
            return ()
        if event_kind is not None:
            _validate_u32("event_kind", event_kind)

        with self._poll_lock:
            events = self._take_pending_events(max_events=max_events, event_kind=event_kind)
            remaining = max_events - len(events)
            if remaining == 0:
                return tuple(events)
            for event in self._poll_native_events_unlocked(max_events=remaining, timeout_ms=timeout_ms):
                if event_kind is None or _native_event_kind(event) == event_kind:
                    events.append(event)
                else:
                    self._pending_events.append(event)
            return tuple(events)

    def _poll_native_events_unlocked(
        self,
        *,
        max_events: int,
        timeout_ms: int,
    ) -> tuple[NativePolledEvent, ...]:
        event_buffer, event_count = self._borrow_poll_event_buffer(max_events)
        status = self.entrypoints.client_await_events(
            _NnrpRoleEventPollRequest(
                self.handle.to_ffi(),
                max_events,
                _ffi_role_poll_timeout_ms(timeout_ms),
                0,
                0,
            ),
            event_buffer,
            max_events,
            ctypes.byref(event_count),
        )
        native_status = NativeStatus.from_ffi(status)
        if native_status.status_code == FFI_STATUS_WOULD_BLOCK:
            return ()
        raise_for_native_status(native_status)
        events = tuple(
            _native_event_from_ffi(event_buffer[index], self.entrypoints) for index in range(int(event_count.value))
        )
        for event in events:
            self._observe_polled_event(event)
        return events

    def _take_pending_events(
        self,
        *,
        max_events: int,
        event_kind: int | None,
    ) -> list[NativePolledEvent]:
        selected: list[NativePolledEvent] = []
        retained: list[NativePolledEvent] = []
        for event in self._pending_events:
            if len(selected) < max_events and (event_kind is None or _native_event_kind(event) == event_kind):
                selected.append(event)
            else:
                retained.append(event)
        self._pending_events[:] = retained
        return selected

    def poll_credit_updates(
        self,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> tuple[NativeCreditUpdateEvent, ...]:
        return tuple(
            NativeCreditUpdateEvent.from_event(event)
            for event in self.poll_events(
                max_events=max_events,
                event_kind=EVENT_KIND_FLOW_UPDATED,
                timeout_ms=timeout_ms,
            )
            if isinstance(event, NativeRuntimeEvent)
        )

    def poll_result_hints(
        self,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> tuple[NativeResultHintEvent, ...]:
        return tuple(
            NativeResultHintEvent.from_event(event)
            for event in self.poll_events(
                max_events=max_events,
                event_kind=EVENT_KIND_RESULT_HINT,
                timeout_ms=timeout_ms,
            )
            if isinstance(event, NativeRuntimeEvent)
        )

    def poll_payload_family_events(
        self,
        payload_family: str,
        *,
        max_events: int | None = None,
        event_kind: int = EVENT_KIND_RESULT_PUSHED,
        timeout_ms: int = 0,
    ) -> tuple[NativePayloadFamilyEvent, ...]:
        return tuple(
            NativePayloadFamilyEvent.from_event(event, payload_family=payload_family)
            for event in self.poll_events(
                max_events=max_events,
                event_kind=event_kind,
                timeout_ms=timeout_ms,
            )
            if isinstance(event, NativeRuntimeEvent)
        )

    def poll_structured_events(
        self,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> tuple[NativePayloadFamilyEvent, ...]:
        return self.poll_payload_family_events(
            "structured_event",
            max_events=max_events,
            timeout_ms=timeout_ms,
        )

    def poll_tool_deltas(
        self,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> tuple[NativePayloadFamilyEvent, ...]:
        return self.poll_payload_family_events("tool_delta", max_events=max_events, timeout_ms=timeout_ms)

    def poll_workflow_states(
        self,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> tuple[NativePayloadFamilyEvent, ...]:
        return self.poll_payload_family_events("workflow_state", max_events=max_events, timeout_ms=timeout_ms)

    def poll_runtime_frames(
        self,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> tuple[NativeRuntimeEvent, ...]:
        if max_events is not None:
            _require_bounded_integer("max_events", max_events, 0xFFFFFFFF)
            if max_events == 0:
                return ()
        _require_bounded_integer("timeout_ms", timeout_ms, 0xFFFFFFFF)
        with self._poll_lock:
            frames: list[NativeRuntimeEvent] = []
            retained: list[NativePolledEvent] = []
            for event in self._pending_events:
                if isinstance(event, NativeRuntimeEvent) and (max_events is None or len(frames) < max_events):
                    frames.append(event)
                else:
                    retained.append(event)
            self._pending_events[:] = retained
            if max_events is not None and len(frames) == max_events:
                return tuple(frames)

            batch_size = 64 if max_events is None else max(64, max_events - len(frames))
            next_timeout_ms = timeout_ms
            while True:
                events = self._poll_native_events_unlocked(max_events=batch_size, timeout_ms=next_timeout_ms)
                next_timeout_ms = 0
                if not events:
                    break
                for event in events:
                    if isinstance(event, NativeRuntimeEvent) and (max_events is None or len(frames) < max_events):
                        frames.append(event)
                    else:
                        self._pending_events.append(event)
                if max_events is not None:
                    break
            return tuple(frames)

    def dispatch_events(
        self,
        callback: NativePolledEventCallback,
        *,
        max_events: int | None = None,
        event_kind: int | None = None,
        timeout_ms: int = 0,
    ) -> int:
        return _dispatch_callback_batch(
            self.poll_events(max_events=max_events, event_kind=event_kind, timeout_ms=timeout_ms),
            callback,
        )

    def dispatch_credit_updates(
        self,
        callback: NativeCreditUpdateCallback,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> int:
        return _dispatch_callback_batch(
            self.poll_credit_updates(max_events=max_events, timeout_ms=timeout_ms),
            callback,
        )

    def dispatch_result_hints(
        self,
        callback: NativeResultHintCallback,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> int:
        return _dispatch_callback_batch(
            self.poll_result_hints(max_events=max_events, timeout_ms=timeout_ms),
            callback,
        )

    def dispatch_payload_family_events(
        self,
        payload_family: str,
        callback: NativePayloadFamilyCallback,
        *,
        max_events: int | None = None,
        event_kind: int = EVENT_KIND_RESULT_PUSHED,
        timeout_ms: int = 0,
    ) -> int:
        return _dispatch_callback_batch(
            self.poll_payload_family_events(
                payload_family,
                max_events=max_events,
                event_kind=event_kind,
                timeout_ms=timeout_ms,
            ),
            callback,
        )

    def dispatch_structured_events(
        self,
        callback: NativePayloadFamilyCallback,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> int:
        return self.dispatch_payload_family_events(
            "structured_event",
            callback,
            max_events=max_events,
            timeout_ms=timeout_ms,
        )

    def dispatch_tool_deltas(
        self,
        callback: NativePayloadFamilyCallback,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> int:
        return self.dispatch_payload_family_events(
            "tool_delta",
            callback,
            max_events=max_events,
            timeout_ms=timeout_ms,
        )

    def dispatch_workflow_states(
        self,
        callback: NativePayloadFamilyCallback,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> int:
        return self.dispatch_payload_family_events(
            "workflow_state",
            callback,
            max_events=max_events,
            timeout_ms=timeout_ms,
        )

    async def async_poll_event(self, *, timeout_ms: int = 0) -> NativePolledEvent | None:
        return await asyncio.to_thread(self.poll_event, timeout_ms=timeout_ms)

    async def next_event(self, timeout: float | None = None) -> NativeClientEvent:
        return await asyncio.to_thread(self._next_event_blocking, timeout)

    def _next_event_blocking(self, timeout: float | None = None) -> NativeClientEvent:
        deadline = _event_deadline(timeout)
        while True:
            event = self.poll_event(timeout_ms=_event_poll_timeout_ms(deadline))
            if isinstance(event, NativeRuntimeEvent):
                return event
            if isinstance(event, NativeLifecycleEvent):
                return _operation_lifecycle_from_native_event(event)
            _raise_if_event_deadline_expired(deadline)

    async def iter_events(
        self,
        *,
        max_events: int | None = None,
        event_kind: int | None = None,
        timeout_ms: int = 0,
    ) -> AsyncIterator[NativePolledEvent]:
        events = await asyncio.to_thread(
            self.poll_events,
            max_events=max_events,
            event_kind=event_kind,
            timeout_ms=timeout_ms,
        )
        for event in events:
            yield event

    async def iter_credit_updates(
        self,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> AsyncIterator[NativeCreditUpdateEvent]:
        updates = await asyncio.to_thread(
            self.poll_credit_updates,
            max_events=max_events,
            timeout_ms=timeout_ms,
        )
        for update in updates:
            yield update

    async def iter_result_hints(
        self,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> AsyncIterator[NativeResultHintEvent]:
        hints = await asyncio.to_thread(
            self.poll_result_hints,
            max_events=max_events,
            timeout_ms=timeout_ms,
        )
        for hint in hints:
            yield hint

    async def iter_payload_family_events(
        self,
        payload_family: str,
        *,
        max_events: int | None = None,
        event_kind: int = EVENT_KIND_RESULT_PUSHED,
        timeout_ms: int = 0,
    ) -> AsyncIterator[NativePayloadFamilyEvent]:
        events = await asyncio.to_thread(
            self.poll_payload_family_events,
            payload_family,
            max_events=max_events,
            event_kind=event_kind,
            timeout_ms=timeout_ms,
        )
        for event in events:
            yield event

    async def iter_structured_events(
        self,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> AsyncIterator[NativePayloadFamilyEvent]:
        async for event in self.iter_payload_family_events(
            "structured_event",
            max_events=max_events,
            timeout_ms=timeout_ms,
        ):
            yield event

    async def iter_tool_deltas(
        self,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> AsyncIterator[NativePayloadFamilyEvent]:
        async for event in self.iter_payload_family_events(
            "tool_delta",
            max_events=max_events,
            timeout_ms=timeout_ms,
        ):
            yield event

    async def iter_workflow_states(
        self,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> AsyncIterator[NativePayloadFamilyEvent]:
        async for event in self.iter_payload_family_events(
            "workflow_state",
            max_events=max_events,
            timeout_ms=timeout_ms,
        ):
            yield event

    async def iter_runtime_frames(
        self,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> AsyncIterator[NativeRuntimeEvent]:
        frames = await asyncio.to_thread(
            self.poll_runtime_frames,
            max_events=max_events,
            timeout_ms=timeout_ms,
        )
        for frame in frames:
            yield frame

    def submit(
        self,
        request: SubmitRequest,
    ) -> NativeOperationHandle:
        self._ensure_open()
        return self.submit_operation(request).handle

    def submit_operation(
        self,
        request: SubmitRequest,
        *,
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
        scheduling_hint: NativeOperationSchedulingHint | None = None,
    ) -> NativeRuntimeOperation:
        self._ensure_open()
        self._validate_submit_request_identity(request)
        selected_scheduling_hint = _coerce_operation_scheduling_hint(
            scheduling_hint,
            parent_operation_id=parent_operation_id,
            operation_group_id=operation_group_id,
        )
        encoded_submit = request.metadata.pack() + request.body
        payload_view, _payload_owner = _buffer_view_from_payload(encoded_submit)
        ffi_request = _NnrpSubmitRequest(
            self.handle.to_ffi(),
            request.operation_id,
            request.frame_id,
            int(request.header.flags),
            request.header.view_id,
            request.header.route_id,
            request.header.trace_id,
            payload_view,
        )
        out_operation = _NnrpHandle()
        status = self.entrypoints.client_submit(ffi_request, ctypes.byref(out_operation))
        raise_for_native_status(status)
        operation = NativeRuntimeOperation(
            entrypoints=self.entrypoints,
            session=self.handle,
            handle=NativeOperationHandle.from_ffi(out_operation),
            operation_id=request.operation_id,
            frame_id=request.frame_id,
            scheduling_hint=selected_scheduling_hint,
            parent_operation_id=selected_scheduling_hint.parent_operation_id,
            operation_group_id=selected_scheduling_hint.operation_group_id,
            _owner=self,
        )
        self._remember_operation_frame(operation.operation_id, operation.frame_id)
        return operation

    def cache_backend(self, *, now_ms: int = 0, ttl_ms: int = 0, expected_version: int = 0) -> NativeCacheLeaseBackend:
        self._ensure_open()
        return NativeCacheLeaseBackend(
            self.entrypoints,
            self.handle.handle,
            now_ms=now_ms,
            ttl_ms=ttl_ms,
            expected_version=expected_version,
        )

    async def async_submit_operation(
        self,
        request: SubmitRequest,
        *,
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
        scheduling_hint: NativeOperationSchedulingHint | None = None,
    ) -> NativeRuntimeOperation:
        dispatch_lock = threading.Lock()
        dispatch_allowed = True
        dispatch_started = False

        def dispatch() -> NativeRuntimeOperation | None:
            nonlocal dispatch_started
            with dispatch_lock:
                if not dispatch_allowed:
                    return None
                dispatch_started = True
            return self.submit_operation(
                request,
                parent_operation_id=parent_operation_id,
                operation_group_id=operation_group_id,
                scheduling_hint=scheduling_hint,
            )

        submit_task = asyncio.create_task(asyncio.to_thread(dispatch))
        try:
            operation = await asyncio.shield(submit_task)
            if operation is None:
                raise asyncio.CancelledError
            return operation
        except asyncio.CancelledError:
            with dispatch_lock:
                cancelled_before_dispatch = not dispatch_started
                if cancelled_before_dispatch:
                    dispatch_allowed = False
            if cancelled_before_dispatch:
                submit_task.cancel()
                raise
            operation = await asyncio.shield(submit_task)
            if operation is None:
                raise
            operation.cancel()
            raise

    def poll_result(
        self,
        operation: NativeRuntimeOperation,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> NativeRuntimeResult:
        self._ensure_open()
        if max_events is not None and max_events < 0:
            raise ValueError("max_events must be non-negative")
        _require_bounded_integer("timeout_ms", timeout_ms, 0xFFFFFFFF)
        result = self._poll_result_batch(
            operation,
            max_events=1 if max_events is None else max_events,
            timeout_ms=timeout_ms,
        )
        if result is not None:
            return result
        raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))

    def _poll_result_batch(
        self,
        operation: NativeRuntimeOperation,
        *,
        max_events: int,
        timeout_ms: int,
    ) -> NativeRuntimeResult | None:
        with self._poll_lock:
            matched_result = self._take_pending_result(operation)
            if matched_result is not None or max_events == 0:
                return matched_result

            event_buffer, event_count = self._borrow_poll_event_buffer(max_events)
            status = self.entrypoints.client_await_events(
                _NnrpRoleEventPollRequest(
                    self.handle.to_ffi(),
                    max_events,
                    _ffi_role_poll_timeout_ms(timeout_ms),
                    0,
                    0,
                ),
                event_buffer,
                max_events,
                ctypes.byref(event_count),
            )
            native_status = NativeStatus.from_ffi(status)
            if native_status.status_code == FFI_STATUS_WOULD_BLOCK:
                return None
            raise_for_native_status(native_status)

            for index in range(int(event_count.value)):
                event = _native_event_from_ffi(event_buffer[index], self.entrypoints)
                self._observe_polled_event(event)
                if (
                    matched_result is None
                    and _event_is_result_event(event)
                    and _event_matches_operation(event, operation)
                ):
                    matched_result = NativeRuntimeResult._from_polled_event(
                        event,
                        operation_id=operation.operation_id,
                    )
                else:
                    self._pending_events.append(event)
            return matched_result

    def _take_pending_result(
        self,
        operation: NativeRuntimeOperation,
    ) -> NativeRuntimeResult | None:
        for index, event in enumerate(self._pending_events):
            if _event_is_result_event(event) and _event_matches_operation(event, operation):
                del self._pending_events[index]
                return NativeRuntimeResult._from_polled_event(
                    event,
                    operation_id=operation.operation_id,
                )
        return None

    def _borrow_poll_event_buffer(self, max_events: int) -> tuple[Any, ctypes.c_size_t]:
        event_buffer = self._poll_event_buffer
        if event_buffer is None or self._poll_event_buffer_capacity < max_events:
            event_buffer = (_NnrpEvent * max_events)()
            object.__setattr__(self, "_poll_event_buffer", event_buffer)
            object.__setattr__(self, "_poll_event_buffer_capacity", max_events)
        event_count = self._poll_event_count
        if event_count is None:
            event_count = ctypes.c_size_t()
            object.__setattr__(self, "_poll_event_count", event_count)
        else:
            event_count.value = 0
        return event_buffer, event_count

    def submit_and_poll_result(
        self,
        request: SubmitRequest,
        *,
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
        scheduling_hint: NativeOperationSchedulingHint | None = None,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> NativeRuntimeResult:
        wait_deadline = _submit_wait_deadline(timeout_ms)
        selected_scheduling_hint = self._prepare_submit_wait(
            request,
            parent_operation_id=parent_operation_id,
            operation_group_id=operation_group_id,
            scheduling_hint=scheduling_hint,
            max_events=max_events,
            timeout_ms=timeout_ms,
        )
        operation = self.submit_operation(
            request,
            scheduling_hint=selected_scheduling_hint,
        )
        return self._poll_submitted_result_until_deadline(
            operation,
            max_events=max_events,
            wait_deadline=wait_deadline,
        )

    async def async_submit_and_poll_result(
        self,
        request: SubmitRequest,
        *,
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
        scheduling_hint: NativeOperationSchedulingHint | None = None,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> NativeRuntimeResult:
        wait_deadline = _submit_wait_deadline(timeout_ms)
        selected_scheduling_hint = self._prepare_submit_wait(
            request,
            parent_operation_id=parent_operation_id,
            operation_group_id=operation_group_id,
            scheduling_hint=scheduling_hint,
            max_events=max_events,
            timeout_ms=timeout_ms,
        )
        operation = await self.async_submit_operation(
            request,
            scheduling_hint=selected_scheduling_hint,
        )
        cancellation_requested = threading.Event()

        def finish_cancellation() -> None:
            try:
                operation.cancel()
            except Exception as error:
                raise _NativeSubmitWaitCancelled from error
            raise _NativeSubmitWaitCancelled

        def poll_until_complete() -> NativeRuntimeResult:
            try:
                return self._poll_submitted_result_until_deadline(
                    operation,
                    max_events=max_events,
                    wait_deadline=wait_deadline,
                    cancellation_requested=cancellation_requested,
                )
            except _NativeSubmitWaitCancelled:
                finish_cancellation()
            except Exception:
                if cancellation_requested.is_set():
                    finish_cancellation()
                raise

        poll_task = asyncio.create_task(asyncio.to_thread(poll_until_complete))
        try:
            return await asyncio.shield(poll_task)
        except asyncio.CancelledError:
            cancellation_requested.set()
            try:
                await asyncio.shield(poll_task)
            except _NativeSubmitWaitCancelled:
                pass
            raise

    def _poll_submitted_result_until_deadline(
        self,
        operation: NativeRuntimeOperation,
        *,
        max_events: int | None,
        wait_deadline: float | None,
        cancellation_requested: threading.Event | None = None,
    ) -> NativeRuntimeResult:
        while True:
            if cancellation_requested is not None and cancellation_requested.is_set():
                raise _NativeSubmitWaitCancelled
            try:
                result = self.poll_result(
                    operation,
                    max_events=max_events,
                    timeout_ms=_submit_wait_poll_timeout_ms(wait_deadline),
                )
                if cancellation_requested is not None and cancellation_requested.is_set():
                    raise _NativeSubmitWaitCancelled
                return result
            except NativeWouldBlockError as error:
                if cancellation_requested is not None and cancellation_requested.is_set():
                    raise _NativeSubmitWaitCancelled from None
                if wait_deadline is None or max_events == 0:
                    raise
                if time.monotonic() < wait_deadline:
                    continue
                operation.cancel()
                raise TimeoutError(
                    f"NNRP operation {operation.operation_id} exceeded its submit wait deadline"
                ) from error

    def cancel_operation(
        self,
        metadata: ControlRequestMetadata,
        diagnostic: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.CANCEL, metadata, diagnostic)

    def abort_operation(
        self,
        metadata: ControlRequestMetadata,
        diagnostic: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.ABORT, metadata, diagnostic)

    def update_priority(self, metadata: SchedulingMetadata) -> None:
        self._send_runtime_frame(MessageType.PRIORITY_UPDATE, metadata)

    def update_deadline(self, metadata: SchedulingMetadata) -> None:
        self._send_runtime_frame(MessageType.DEADLINE, metadata)

    def expire_at(self, metadata: SchedulingMetadata) -> None:
        self._send_runtime_frame(MessageType.EXPIRE_AT, metadata)

    def supersede(
        self,
        metadata: SupersedeMetadata,
        diagnostic: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.SUPERSEDE, metadata, diagnostic)

    def update_budget(self, metadata: BudgetMetadata) -> None:
        self._send_runtime_frame(MessageType.BUDGET_UPDATE, metadata)

    def negotiate_capabilities(
        self,
        metadata: CapabilityMetadata,
        body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.CAPABILITY_NEGOTIATION, metadata, body)

    def degrade_profile(
        self,
        metadata: CapabilityMetadata,
        body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.DEGRADE_PROFILE, metadata, body)

    def send_route_hint(
        self,
        metadata: RouteHintMetadata,
        body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.ROUTE_HINT, metadata, body)

    def send_execution_hint(
        self,
        metadata: RouteHintMetadata,
        body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.EXECUTION_HINT, metadata, body)

    def send_trace_context(
        self,
        metadata: TraceContextMetadata,
        body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.TRACE_CONTEXT, metadata, body)

    def declare_object(
        self,
        metadata: ObjectDescriptorMetadata,
        body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.OBJECT_DECLARE, metadata, body)

    def reference_object(
        self,
        metadata: ObjectReferenceMetadata,
        body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.OBJECT_REF, metadata, body)

    def release_object(
        self,
        metadata: ObjectReleaseMetadata,
        diagnostic: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.OBJECT_RELEASE, metadata, diagnostic)

    def patch_object(
        self,
        metadata: ObjectDeltaMetadata,
        delta: bytes | bytearray | memoryview,
        metadata_body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.OBJECT_PATCH, metadata, _join_runtime_object_tail(metadata_body, delta))

    def send_object_delta(
        self,
        metadata: ObjectDeltaMetadata,
        delta: bytes | bytearray | memoryview,
        metadata_body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.OBJECT_DELTA, metadata, _join_runtime_object_tail(metadata_body, delta))

    def reference_cache(
        self,
        metadata: CacheReferenceMetadata,
        body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.CACHE_REFERENCE, metadata, body)

    def report_cache_miss(
        self,
        metadata: CacheMissMetadata,
        diagnostic: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.CACHE_MISS, metadata, diagnostic)

    def invalidate_cache(self, metadata: CacheInvalidateMetadata) -> None:
        self._send_runtime_frame(MessageType.CACHE_INVALIDATE, metadata)

    def close(self) -> None:
        with self._poll_lock:
            if self._closed:
                return
            status = self.entrypoints.client_close(self.handle.to_ffi())
            raise_for_native_status(status)
            with self._operation_lock:
                self._operation_frames.clear()
            object.__setattr__(self, "_closed", True)

    def cancel(self, *, frame_id: int) -> None:
        self._ensure_open()
        with self._poll_lock:
            request = _NnrpClientCancelRequest(self.handle.to_ffi(), frame_id)
            status = self.entrypoints.client_cancel(request)
            raise_for_native_status(status)
            self._forget_operation_frame_by_frame(frame_id)

    def _send_runtime_frame(
        self,
        message_type: MessageType,
        metadata: _FixedRuntimeMetadata | CacheInvalidateMetadata,
        tail: bytes | bytearray | memoryview = b"",
        *,
        frame_id: int | None = None,
    ) -> None:
        self._ensure_open()
        payload = _encode_native_runtime_frame(message_type, metadata, tail)
        payload_view, _payload_owner = _buffer_view_from_payload(payload)
        selected_frame_id = self._runtime_frame_id(message_type, metadata) if frame_id is None else frame_id
        request = _NnrpRuntimeFrameSendRequest(
            self.handle.to_ffi(),
            int(message_type),
            selected_frame_id,
            payload_view,
        )
        status = self.entrypoints.runtime_frame_send(request)
        raise_for_native_status(status)
        operation_id = _runtime_operation_id(message_type, metadata)
        if frame_id is None and operation_id is None:
            object.__setattr__(
                self,
                "_next_runtime_frame_id",
                1 if selected_frame_id == 0xFFFFFFFF else selected_frame_id + 1,
            )
        if (
            message_type in {MessageType.CANCEL, MessageType.ABORT, MessageType.SUPERSEDE}
            and operation_id is not None
            and operation_id != 0
        ):
            self._forget_operation_frame(operation_id)

    def _prepare_submit_wait(
        self,
        request: SubmitRequest,
        *,
        parent_operation_id: int | None,
        operation_group_id: int | None,
        scheduling_hint: NativeOperationSchedulingHint | None,
        max_events: int | None,
        timeout_ms: int,
    ) -> NativeOperationSchedulingHint:
        self._ensure_open()
        self._validate_submit_request_identity(request)
        if max_events is not None and max_events < 0:
            raise ValueError("max_events must be non-negative")
        selected_scheduling_hint = _coerce_operation_scheduling_hint(
            scheduling_hint,
            parent_operation_id=parent_operation_id,
            operation_group_id=operation_group_id,
        )
        _require_bounded_integer("timeout_ms", timeout_ms, 0xFFFFFFFF)
        if timeout_ms == 0:
            return selected_scheduling_hint
        metadata = SchedulingMetadata(
            operation_id=request.operation_id,
            control_sequence=self._allocate_control_sequence(),
            priority_class=0,
            priority_delta=0,
            deadline_unix_ms=math.ceil(time.time() * 1000) + timeout_ms,
            flags=0,
        )
        self._send_runtime_frame(MessageType.DEADLINE, metadata, frame_id=request.frame_id)
        return selected_scheduling_hint

    @staticmethod
    def _validate_submit_request_identity(request: SubmitRequest) -> None:
        if request.metadata.operation_id != request.operation_id:
            raise ValueError(
                "metadata.operation_id must equal the submit operation_id: "
                f"expected {request.operation_id}, got {request.metadata.operation_id}"
            )

    def _runtime_frame_id(
        self,
        message_type: MessageType,
        metadata: _FixedRuntimeMetadata | CacheInvalidateMetadata,
    ) -> int:
        operation_id = _runtime_operation_id(message_type, metadata)
        if operation_id is None:
            return self._next_runtime_frame_id
        if operation_id == 0:
            if message_type not in {
                MessageType.CANCEL,
                MessageType.ABORT,
                MessageType.BUDGET_UPDATE,
                MessageType.OBJECT_REF,
                MessageType.OBJECT_RELEASE,
            }:
                raise NativeInvalidStateError(
                    NativeStatus(FFI_STATUS_INVALID_STATE),
                    f"{message_type.name} requires an operation-scoped non-zero operation_id",
                )
            return 0
        with self._operation_lock:
            try:
                return self._operation_frames[operation_id]
            except KeyError as error:
                raise NativeInvalidStateError(
                    NativeStatus(FFI_STATUS_INVALID_STATE),
                    f"{message_type.name} references inactive operation {operation_id}",
                ) from error

    def _allocate_control_sequence(self) -> int:
        with self._operation_lock:
            sequence = self._next_control_sequence
            object.__setattr__(
                self,
                "_next_control_sequence",
                1 if sequence == 0xFFFFFFFFFFFFFFFF else sequence + 1,
            )
            return sequence

    def _remember_operation_frame(self, operation_id: int, frame_id: int) -> None:
        with self._operation_lock:
            self._operation_frames[operation_id] = frame_id

    def _forget_operation_frame(self, operation_id: int) -> None:
        with self._operation_lock:
            self._operation_frames.pop(operation_id, None)

    def _forget_operation_frame_by_frame(self, frame_id: int) -> None:
        with self._operation_lock:
            for operation_id, operation_frame_id in tuple(self._operation_frames.items()):
                if operation_frame_id == frame_id:
                    self._operation_frames.pop(operation_id, None)

    def _observe_polled_event(self, event: NativePolledEvent) -> None:
        event_session = (
            _runtime_event_context(event).session if isinstance(event, NativeRuntimeEvent) else event.session
        )
        if event_session != self.handle.handle:
            return
        if isinstance(event, NativeRuntimeEvent) and event.header.message_type is MessageType.SESSION_CLOSE:
            with self._operation_lock:
                self._operation_frames.clear()
            return
        operation_id = _event_operation_id(event)
        if operation_id == 0:
            return
        terminal = _event_is_result_event(event)
        if isinstance(event, NativeRuntimeEvent):
            terminal = terminal or event.header.message_type in {
                MessageType.CANCEL,
                MessageType.ABORT,
                MessageType.SUPERSEDE,
            }
        if terminal:
            self._forget_operation_frame(operation_id)

    def _ensure_open(self) -> None:
        if self._closed:
            raise NativeInvalidStateError(NativeStatus(FFI_STATUS_INVALID_STATE), "native runtime session is closed")


@dataclass(frozen=True)
class NativeRuntimeServerOperation:
    operation_id: int
    frame_id: int
    submit: NativeRuntimeEvent
    _entrypoints: NativeRuntimeEntrypoints = field(repr=False, compare=False)
    _session: NativeSessionHandle = field(repr=False, compare=False)
    _handle: NativeOperationHandle = field(repr=False, compare=False)
    _owner: NativeRuntimeServerSession = field(repr=False, compare=False)
    _terminal_reply_started: bool = field(default=False, init=False, repr=False, compare=False)
    _reply_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.submit.header.message_type is not MessageType.FRAME_SUBMIT:
            raise ValueError("submit must be a FRAME_SUBMIT runtime event")
        metadata = self.submit.metadata.value
        if not isinstance(metadata, FrameSubmitMetadata):
            raise TypeError("submit metadata must be FrameSubmitMetadata")
        if metadata.operation_id != self.operation_id:
            raise ValueError("operation_id must match submit metadata")
        if self.submit.header.frame_id != self.frame_id:
            raise ValueError("frame_id must match submit header")

    async def send_result(
        self,
        metadata: ResultPushMetadata,
        body: bytes | bytearray | memoryview = b"",
    ) -> None:
        if not isinstance(metadata, ResultPushMetadata):
            raise TypeError("metadata must be ResultPushMetadata")
        payload = metadata.pack() + bytes(body)
        payload_view, payload_owner = _buffer_view_from_payload(payload)
        request = _NnrpServerSendResultRequest(self._handle.to_ffi(), payload_view)
        await self._send_terminal_reply(lambda: self._send_result_blocking(request, payload_owner))

    async def send_result_drop(
        self,
        metadata: ResultDropReasonMetadata,
        diagnostic: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._require_operation_metadata(metadata)
        diagnostic_snapshot = bytes(diagnostic)
        await self._send_terminal_reply(
            lambda: self._owner._send_operation_runtime_frame(
                self._handle,
                self.frame_id,
                MessageType.RESULT_DROP_REASON,
                metadata,
                diagnostic_snapshot,
            )
        )

    async def _send_terminal_reply(self, send: Callable[[], None]) -> None:
        self._begin_reply(terminal=True)
        worker = asyncio.create_task(asyncio.to_thread(send))
        cancellation: asyncio.CancelledError | None = None
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError as error:
                cancellation = cancellation or error
            except BaseException:
                break

        try:
            worker.result()
        except BaseException:
            self._restore_terminal_reply()
            if cancellation is not None:
                raise cancellation from None
            raise
        self._complete_terminal_reply()
        if cancellation is not None:
            raise cancellation

    async def send_progress(
        self,
        metadata: ProgressMetadata,
        body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._require_operation_metadata(metadata)
        body_snapshot = bytes(body)
        self._begin_reply(terminal=False)
        await asyncio.to_thread(
            self._owner._send_operation_runtime_frame,
            self._handle,
            self.frame_id,
            MessageType.PROGRESS,
            metadata,
            body_snapshot,
        )

    async def send_partial_result(
        self,
        metadata: PartialResultMetadata,
        body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._require_operation_metadata(metadata)
        body_snapshot = bytes(body)
        self._begin_reply(terminal=False)
        await asyncio.to_thread(
            self._owner._send_operation_runtime_frame,
            self._handle,
            self.frame_id,
            MessageType.PARTIAL_RESULT,
            metadata,
            body_snapshot,
        )

    def _send_result_blocking(
        self,
        request: _NnrpServerSendResultRequest,
        payload_owner: ctypes.Array[ctypes.c_char] | None,
    ) -> None:
        self._owner._ensure_open()
        try:
            status = self._entrypoints.server_send_result(request)
            raise_for_native_status(status)
        finally:
            del payload_owner

    def _require_operation_metadata(self, metadata: object) -> None:
        if int(getattr(metadata, "operation_id", 0)) != self.operation_id:
            raise ValueError("metadata operation_id must match the server operation")

    def _begin_reply(self, *, terminal: bool) -> None:
        self._owner._ensure_open()
        with self._reply_lock:
            if self._terminal_reply_started:
                kind = "terminal" if terminal else "incremental"
                raise NativeInvalidStateError(
                    NativeStatus(FFI_STATUS_INVALID_STATE),
                    f"{kind} reply is not allowed after a terminal server reply started",
                )
            if terminal:
                object.__setattr__(self, "_terminal_reply_started", True)

    def _restore_terminal_reply(self) -> None:
        with self._reply_lock:
            object.__setattr__(self, "_terminal_reply_started", False)

    def _complete_terminal_reply(self) -> None:
        self._owner._forget_operation_frame(self.operation_id)


class NativeServerEventKind(StrEnum):
    SUBMIT = "submit"
    RUNTIME = "runtime"
    LIFECYCLE = "lifecycle"


@dataclass(frozen=True, slots=True)
class NativeServerEvent:
    kind: NativeServerEventKind
    value: NativeRuntimeServerOperation | NativeRuntimeEvent | OperationLifecycleEvent

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", NativeServerEventKind(self.kind))
        expected_type = {
            NativeServerEventKind.SUBMIT: NativeRuntimeServerOperation,
            NativeServerEventKind.RUNTIME: NativeRuntimeEvent,
            NativeServerEventKind.LIFECYCLE: OperationLifecycleEvent,
        }[self.kind]
        if not isinstance(self.value, expected_type):
            raise TypeError(f"{self.kind.value} server event requires {expected_type.__name__}")

    @classmethod
    def submit(cls, operation: NativeRuntimeServerOperation) -> NativeServerEvent:
        return cls(NativeServerEventKind.SUBMIT, operation)

    @classmethod
    def runtime(cls, event: NativeRuntimeEvent) -> NativeServerEvent:
        return cls(NativeServerEventKind.RUNTIME, event)

    @classmethod
    def lifecycle(cls, event: OperationLifecycleEvent) -> NativeServerEvent:
        return cls(NativeServerEventKind.LIFECYCLE, event)

    def as_submit(self) -> NativeRuntimeServerOperation | None:
        return self.value if isinstance(self.value, NativeRuntimeServerOperation) else None

    def as_runtime(self) -> NativeRuntimeEvent | None:
        return self.value if isinstance(self.value, NativeRuntimeEvent) else None

    def as_lifecycle(self) -> OperationLifecycleEvent | None:
        return self.value if isinstance(self.value, OperationLifecycleEvent) else None


@dataclass(frozen=True)
class NativeRuntimeServerSession:
    entrypoints: NativeRuntimeEntrypoints
    server: NativeConnectionHandle
    handle: NativeSessionHandle
    active_transport_name: str
    _closed: bool = field(default=False, init=False, repr=False, compare=False)
    _pending_events: list[NativeServerEvent] = field(default_factory=list, init=False, repr=False, compare=False)
    _poll_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)
    _next_runtime_frame_id: int = field(default=1, init=False, repr=False, compare=False)
    _operation_frames: dict[int, int] = field(default_factory=dict, init=False, repr=False, compare=False)
    _operation_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    async def next_event(self, timeout: float | None = None) -> NativeServerEvent:
        return await asyncio.to_thread(self._next_event_blocking, timeout)

    async def receive_submit(self, timeout: float | None = None) -> NativeRuntimeServerOperation:
        return await asyncio.to_thread(self._receive_submit_blocking, timeout)

    def _next_event_blocking(self, timeout: float | None = None) -> NativeServerEvent:
        self._ensure_open()
        deadline = _event_deadline(timeout)
        with self._poll_lock:
            while True:
                if self._pending_events:
                    return self._pending_events.pop(0)
                self._pending_events.extend(
                    self._poll_native_server_events_unlocked(
                        max_events=1,
                        timeout_ms=_event_poll_timeout_ms(deadline),
                    )
                )
                if self._pending_events:
                    return self._pending_events.pop(0)
                _raise_if_event_deadline_expired(deadline)

    def _receive_submit_blocking(self, timeout: float | None = None) -> NativeRuntimeServerOperation:
        self._ensure_open()
        deadline = _event_deadline(timeout)
        with self._poll_lock:
            while True:
                operation = self._take_pending_submit_unlocked()
                if operation is not None:
                    return operation
                self._pending_events.extend(
                    self._poll_native_server_events_unlocked(
                        max_events=64,
                        timeout_ms=_event_poll_timeout_ms(deadline),
                    )
                )
                operation = self._take_pending_submit_unlocked()
                if operation is not None:
                    return operation
                _raise_if_event_deadline_expired(deadline)

    def _take_pending_submit_unlocked(self) -> NativeRuntimeServerOperation | None:
        for index, event in enumerate(self._pending_events):
            operation = event.as_submit()
            if operation is not None:
                del self._pending_events[index]
                return operation
        return None

    def poll_events(
        self,
        *,
        max_events: int = 1,
        timeout_ms: int = 0,
    ) -> tuple[NativeServerEvent, ...]:
        self._ensure_open()
        _require_bounded_integer("max_events", max_events, 0xFFFFFFFF)
        _require_bounded_integer("timeout_ms", timeout_ms, 0xFFFFFFFF)
        if max_events == 0:
            return ()
        with self._poll_lock:
            events = self._pending_events[:max_events]
            del self._pending_events[: len(events)]
            remaining = max_events - len(events)
            if remaining:
                events.extend(self._poll_native_server_events_unlocked(max_events=remaining, timeout_ms=timeout_ms))
            return tuple(events)

    def _poll_native_server_events_unlocked(
        self,
        *,
        max_events: int,
        timeout_ms: int,
    ) -> tuple[NativeServerEvent, ...]:
        return tuple(
            self._server_event_from_polled(event)
            for event in self._poll_native_events_unlocked(max_events=max_events, timeout_ms=timeout_ms)
        )

    def _poll_native_events_unlocked(
        self,
        *,
        max_events: int,
        timeout_ms: int,
    ) -> tuple[NativePolledEvent, ...]:
        event_buffer = (_NnrpEvent * max_events)()
        event_count = ctypes.c_size_t()
        status = self.entrypoints.server_await_events(
            _NnrpRoleEventPollRequest(
                self.handle.to_ffi(),
                max_events,
                _ffi_role_poll_timeout_ms(timeout_ms),
                0,
                0,
            ),
            event_buffer,
            max_events,
            ctypes.byref(event_count),
        )
        native_status = NativeStatus.from_ffi(status)
        if native_status.status_code == FFI_STATUS_WOULD_BLOCK:
            return ()
        raise_for_native_status(native_status)
        return tuple(
            _native_event_from_ffi(event_buffer[index], self.entrypoints) for index in range(int(event_count.value))
        )

    def poll_event(self, *, timeout_ms: int = 0) -> NativeServerEvent | None:
        events = self.poll_events(max_events=1, timeout_ms=timeout_ms)
        return events[0] if events else None

    def _server_event_from_polled(self, event: NativePolledEvent) -> NativeServerEvent:
        if isinstance(event, NativeRuntimeEvent):
            if event.header.message_type is not MessageType.FRAME_SUBMIT:
                self._observe_polled_event(event)
                if event.header.message_type is MessageType.SESSION_CLOSE:
                    with self._operation_lock:
                        self._operation_frames.clear()
                return NativeServerEvent.runtime(event)
            context = _runtime_event_context(event)
            context.operation.require_kind(HANDLE_KIND_OPERATION)
            submit_metadata = event.metadata.value
            if not isinstance(submit_metadata, FrameSubmitMetadata):
                raise NativeProtocolError(
                    NativeStatus(FFI_STATUS_PROTOCOL_ERROR),
                    "FRAME_SUBMIT event did not decode FrameSubmitMetadata",
                )
            operation = NativeRuntimeServerOperation(
                submit_metadata.operation_id,
                event.header.frame_id,
                event,
                self.entrypoints,
                self.handle,
                NativeOperationHandle(context.operation),
                self,
            )
            self._remember_operation_frame(operation.operation_id, operation.frame_id)
            return NativeServerEvent.submit(operation)
        self._observe_polled_event(event)
        return NativeServerEvent.lifecycle(_operation_lifecycle_from_native_event(event))

    def poll_runtime_frames(
        self,
        *,
        max_events: int = 1,
        timeout_ms: int = 0,
    ) -> tuple[NativeRuntimeEvent, ...]:
        _require_bounded_integer("max_events", max_events, 0xFFFFFFFF)
        _require_bounded_integer("timeout_ms", timeout_ms, 0xFFFFFFFF)
        if max_events == 0:
            return ()
        with self._poll_lock:
            frames: list[NativeRuntimeEvent] = []
            retained: list[NativeServerEvent] = []
            for event in self._pending_events:
                runtime_event = event.as_runtime()
                if runtime_event is not None and len(frames) < max_events:
                    frames.append(runtime_event)
                else:
                    retained.append(event)
            self._pending_events[:] = retained
            if len(frames) == max_events:
                return tuple(frames)

            events = self._poll_native_server_events_unlocked(
                max_events=max(64, max_events - len(frames)),
                timeout_ms=timeout_ms,
            )
            for event in events:
                runtime_event = event.as_runtime()
                if runtime_event is not None and len(frames) < max_events:
                    frames.append(runtime_event)
                else:
                    self._pending_events.append(event)
            return tuple(frames)

    def send_backpressure(self, metadata: PressureMetadata) -> None:
        self._send_runtime_frame(MessageType.BACKPRESSURE, metadata)

    def send_credit_update(self, metadata: PressureMetadata) -> None:
        self._send_runtime_frame(MessageType.CREDIT_UPDATE, metadata)

    def send_trace_context(
        self,
        metadata: TraceContextMetadata,
        body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.TRACE_CONTEXT, metadata, body)

    def send_recoverable_error(
        self,
        metadata: RecoverableErrorMetadata,
        diagnostic: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.ERROR_RECOVERABLE, metadata, diagnostic)

    def send_retry_after(
        self,
        metadata: RetryAfterMetadata,
        diagnostic: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.RETRY_AFTER, metadata, diagnostic)

    def declare_object(
        self,
        metadata: ObjectDescriptorMetadata,
        body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.OBJECT_DECLARE, metadata, body)

    def reference_object(
        self,
        metadata: ObjectReferenceMetadata,
        body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.OBJECT_REF, metadata, body)

    def release_object(
        self,
        metadata: ObjectReleaseMetadata,
        diagnostic: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.OBJECT_RELEASE, metadata, diagnostic)

    def patch_object(
        self,
        metadata: ObjectDeltaMetadata,
        delta: bytes | bytearray | memoryview,
        metadata_body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.OBJECT_PATCH, metadata, _join_runtime_object_tail(metadata_body, delta))

    def send_object_delta(
        self,
        metadata: ObjectDeltaMetadata,
        delta: bytes | bytearray | memoryview,
        metadata_body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.OBJECT_DELTA, metadata, _join_runtime_object_tail(metadata_body, delta))

    def reference_cache(
        self,
        metadata: CacheReferenceMetadata,
        body: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.CACHE_REFERENCE, metadata, body)

    def report_cache_miss(
        self,
        metadata: CacheMissMetadata,
        diagnostic: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._send_runtime_frame(MessageType.CACHE_MISS, metadata, diagnostic)

    def invalidate_cache(self, metadata: CacheInvalidateMetadata) -> None:
        self._send_runtime_frame(MessageType.CACHE_INVALIDATE, metadata)

    def _send_runtime_frame(
        self,
        message_type: MessageType,
        metadata: _FixedRuntimeMetadata | CacheInvalidateMetadata,
        tail: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._ensure_open()
        payload = _encode_native_runtime_frame(message_type, metadata, tail)
        payload_view, _payload_owner = _buffer_view_from_payload(payload)
        frame_id = self._runtime_frame_id(message_type, metadata)
        request = _NnrpRuntimeFrameSendRequest(self.handle.to_ffi(), int(message_type), frame_id, payload_view)
        status = self.entrypoints.runtime_frame_send(request)
        raise_for_native_status(status)
        if _runtime_operation_id(message_type, metadata) is None:
            object.__setattr__(self, "_next_runtime_frame_id", 1 if frame_id == 0xFFFFFFFF else frame_id + 1)

    def _send_operation_runtime_frame(
        self,
        operation: NativeOperationHandle,
        frame_id: int,
        message_type: MessageType,
        metadata: _FixedRuntimeMetadata,
        tail: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._ensure_open()
        payload = _encode_native_runtime_frame(message_type, metadata, tail)
        payload_view, _payload_owner = _buffer_view_from_payload(payload)
        request = _NnrpRuntimeFrameSendRequest(operation.to_ffi(), int(message_type), frame_id, payload_view)
        status = self.entrypoints.runtime_frame_send(request)
        raise_for_native_status(status)

    def close(self) -> None:
        with self._poll_lock:
            if self._closed:
                return
            status = self.entrypoints.server_close(self.handle.to_ffi())
            raise_for_native_status(status)
            with self._operation_lock:
                self._operation_frames.clear()
            object.__setattr__(self, "_closed", True)

    def _runtime_frame_id(
        self,
        message_type: MessageType,
        metadata: _FixedRuntimeMetadata | CacheInvalidateMetadata,
    ) -> int:
        operation_id = _runtime_operation_id(message_type, metadata)
        if operation_id is None:
            return self._next_runtime_frame_id
        if operation_id == 0:
            if message_type not in {MessageType.OBJECT_REF, MessageType.OBJECT_RELEASE}:
                raise NativeInvalidStateError(
                    NativeStatus(FFI_STATUS_INVALID_STATE),
                    f"{message_type.name} requires an operation-scoped non-zero operation_id",
                )
            return 0
        with self._operation_lock:
            try:
                return self._operation_frames[operation_id]
            except KeyError as error:
                raise NativeInvalidStateError(
                    NativeStatus(FFI_STATUS_INVALID_STATE),
                    f"{message_type.name} references inactive operation {operation_id}",
                ) from error

    def _remember_operation_frame(self, operation_id: int, frame_id: int) -> None:
        with self._operation_lock:
            self._operation_frames[operation_id] = frame_id

    def _forget_operation_frame(self, operation_id: int) -> None:
        with self._operation_lock:
            self._operation_frames.pop(operation_id, None)

    def _observe_polled_event(self, event: NativePolledEvent) -> None:
        event_session = (
            _runtime_event_context(event).session if isinstance(event, NativeRuntimeEvent) else event.session
        )
        if event_session != self.handle.handle:
            return
        # Server operation ownership survives peer cancellation, abort,
        # supersession, and lifecycle delivery so the application can still
        # send exactly one terminal result or drop reply. Successful terminal
        # reply methods release the Python correlation; session close clears
        # every remaining operation.

    def _ensure_open(self) -> None:
        if self._closed:
            raise NativeInvalidStateError(
                NativeStatus(FFI_STATUS_INVALID_STATE),
                "native runtime server session is closed",
            )


def _runtime_operation_id(
    message_type: MessageType,
    metadata: _FixedRuntimeMetadata | CacheInvalidateMetadata,
) -> int | None:
    if message_type in {MessageType.CANCEL, MessageType.ABORT} and isinstance(metadata, ControlRequestMetadata):
        return metadata.operation_id
    if message_type in {MessageType.PRIORITY_UPDATE, MessageType.DEADLINE, MessageType.EXPIRE_AT} and isinstance(
        metadata,
        SchedulingMetadata,
    ):
        return metadata.operation_id
    if message_type is MessageType.SUPERSEDE and isinstance(metadata, SupersedeMetadata):
        return metadata.old_operation_id
    if message_type is MessageType.BUDGET_UPDATE and isinstance(metadata, BudgetMetadata):
        return metadata.operation_id
    if message_type in {MessageType.ROUTE_HINT, MessageType.EXECUTION_HINT} and isinstance(
        metadata,
        RouteHintMetadata,
    ):
        return metadata.operation_id
    if message_type is MessageType.OBJECT_REF and isinstance(metadata, ObjectReferenceMetadata):
        return metadata.operation_id
    if message_type is MessageType.OBJECT_RELEASE and isinstance(metadata, ObjectReleaseMetadata):
        return metadata.operation_id
    return None


def _event_matches_operation(event: NativePolledEvent, operation: NativeRuntimeOperation) -> bool:
    context = _runtime_event_context(event) if isinstance(event, NativeRuntimeEvent) else event
    if context.session != operation.session.handle:
        return False
    return _event_operation_id(event) == operation.operation_id


def _event_is_result_event(event: NativePolledEvent) -> bool:
    event_kind = _native_event_kind(event)
    if event_kind in {
        EVENT_KIND_RESULT_PUSHED,
        EVENT_KIND_RESULT_DROPPED,
        EVENT_KIND_ERROR,
    }:
        return True
    if isinstance(event, NativeLifecycleEvent) and event_kind == EVENT_KIND_OPERATION_LIFECYCLE:
        lifecycle = _operation_lifecycle_from_native_event(event)
        return lifecycle.state in {
            OperationState.COMPLETED,
            OperationState.CANCELLED,
            OperationState.SUPERSEDED,
            OperationState.FAILED,
        }
    return False


def _dispatch_callback_batch(
    events: tuple[_CallbackEventT, ...],
    callback: Callable[[_CallbackEventT], None],
) -> int:
    dispatched = 0
    for event in events:
        try:
            callback(event)
        except Exception as error:
            raise NativeCallbackRejectedError(
                NativeStatus(FFI_STATUS_CALLBACK_REJECTED),
                "native runtime callback rejected an event",
            ) from error
        dispatched += 1
    return dispatched


def current_native_platform() -> NativePlatform:
    return NativePlatform(_normalize_os(platform.system()), _normalize_arch(platform.machine()))


def native_library_name(os_name: str) -> str:
    normalized = _normalize_os(os_name)
    if normalized == "windows":
        return "nnrp_ffi.dll"
    if normalized in {"macos", "ios"}:
        return "libnnrp_ffi.dylib"
    if normalized in {"linux", "android"}:
        return "libnnrp_ffi.so"
    raise NativeArtifactError(f"unsupported native artifact OS: {os_name}")


def default_artifact_root() -> Path:
    configured = os.environ.get(DEFAULT_ARTIFACT_ROOT_ENV)
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent / "native_artifacts"


def resolve_native_artifact(
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    transport: str | None = None,
) -> Path:
    selected_platform = native_platform or current_native_platform()
    artifact_root = Path(root) if root is not None else default_artifact_root()
    platform_dir = artifact_root / selected_platform.tag
    library_name = native_library_name(selected_platform.os_name)

    if transport is not None:
        candidate_dirs = [platform_dir / _normalize_native_transport_scope(transport)]
    else:
        candidate_dirs = [platform_dir, *(platform_dir / scope for scope in NATIVE_TRANSPORT_SCOPES)]

    for candidate_dir in candidate_dirs:
        artifact_path = candidate_dir / library_name
        if artifact_path.is_file():
            return artifact_path

    checked = ", ".join(str(candidate_dir / library_name) for candidate_dir in candidate_dirs)
    raise NativeArtifactError(f"native artifact was not found; checked: {checked}")


def _normalize_native_transport_scope(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized in NATIVE_TRANSPORT_SCOPES:
        return normalized
    if normalized in {"", "auto", "default"}:
        return "tcp"
    raise NativeArtifactError(f"unsupported native transport scope: {value}")


def _native_transport_endpoint_address(parsed: SplitResult) -> str:
    scheme = parsed.scheme.lower()
    if scheme == "unix":
        if parsed.netloc or not parsed.path:
            raise NativeArtifactError("unix native transport endpoints must use unix:///path form")
        return parsed.path
    if scheme == "npipe":
        address = f"{parsed.netloc}{parsed.path}"
        if not address:
            raise NativeArtifactError("npipe native transport endpoints must include a pipe path")
        return address
    if not parsed.netloc:
        raise NativeArtifactError(f"{scheme} native transport endpoints must include an authority")
    address = f"{parsed.netloc}{parsed.path}"
    if parsed.query:
        address = f"{address}?{parsed.query}"
    return address


def discover_native_transport_providers(
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
) -> tuple[NativeTransportProvider, ...]:
    selected_platform = native_platform or current_native_platform()
    artifact_root = Path(root) if root is not None else default_artifact_root()
    platform_dir = artifact_root / selected_platform.tag
    if not platform_dir.is_dir():
        return ()

    providers: list[NativeTransportProvider] = []
    transport_ids: set[TransportId] = set()
    provider_ids: set[str] = set()
    candidate_dirs = [platform_dir / scope for scope in NATIVE_TRANSPORT_SCOPES]
    for candidate_dir in candidate_dirs:
        if not candidate_dir.is_dir():
            continue
        provider = _provider_from_artifact_dir(candidate_dir, selected_platform)
        if provider is not None:
            transport_id = NATIVE_TRANSPORT_ID_BY_NAME[provider.name]
            if transport_id in transport_ids:
                raise NativeArtifactError(f"duplicate native provider for transport {provider.name}")
            if provider.metadata.id in provider_ids:
                raise NativeArtifactError(f"duplicate native provider id: {provider.metadata.id}")
            transport_ids.add(transport_id)
            provider_ids.add(provider.metadata.id)
            providers.append(provider)
    return tuple(providers)


def resolve_native_transport_provider(
    name: str,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
) -> NativeTransportProvider:
    normalized = _normalize_native_transport_scope(name)
    for provider in discover_native_transport_providers(root, native_platform):
        if provider.name == normalized:
            return provider
    raise NativeArtifactError(f"native transport provider is not advertised by the native artifact: {name}")


def parse_nnrp_endpoint(uri: str) -> NnrpEndpoint:
    raw_uri = uri.strip()
    if not raw_uri:
        raise NativeArtifactError("NNRP endpoint URI must be non-empty")
    parsed = urlsplit(raw_uri)
    scheme = parsed.scheme.lower()
    if scheme not in {"nnrp", "nnrps"}:
        raise NativeArtifactError(f"unsupported NNRP endpoint scheme: {parsed.scheme}")
    if not parsed.netloc:
        raise NativeArtifactError("NNRP endpoint URI must include an authority")
    if parsed.fragment:
        raise NativeArtifactError("NNRP endpoint URI must not include a fragment")
    return NnrpEndpoint(
        uri=raw_uri,
        scheme=scheme,
        authority=parsed.netloc,
        path=parsed.path or "/",
        query=parsed.query,
        secure=scheme == "nnrps",
    )


def parse_native_transport_endpoint(uri: str) -> NativeTransportEndpoint:
    raw_uri = uri.strip()
    if not raw_uri:
        raise NativeArtifactError("native transport endpoint URI must be non-empty")
    parsed = urlsplit(raw_uri)
    scheme = parsed.scheme.lower()
    if scheme in {"nnrp", "nnrps"}:
        raise NativeArtifactError(
            "NNRP application endpoints must be parsed with parse_nnrp_endpoint; "
            "native transport endpoint locators are provider-local"
        )
    try:
        transport_name = NATIVE_ENDPOINT_TRANSPORT_BY_SCHEME[scheme]
    except KeyError as error:
        raise NativeArtifactError(f"unsupported native transport endpoint scheme: {parsed.scheme}") from error
    if parsed.fragment:
        raise NativeArtifactError("native transport endpoint URI must not include a fragment")
    address = _native_transport_endpoint_address(parsed)
    return NativeTransportEndpoint(
        uri=raw_uri,
        scheme=scheme,
        transport_name=transport_name,
        transport_id=NATIVE_TRANSPORT_ID_BY_NAME[transport_name],
        address=address,
        secure=scheme in {"quic+tls", "wss"},
    )


def diagnose_nnrp_endpoint_support(
    uri: str | NnrpEndpoint,
    policy: TransportPolicy | str | int = TransportPolicy.AUTO,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    supported_transports: (
        tuple[str | TransportId, ...] | list[str | TransportId] | set[str | TransportId] | None
    ) = None,
    requested_max_frame_bytes: int | None = None,
    probe_samples: tuple[NativeTransportProbeSample, ...] | list[NativeTransportProbeSample] | None = None,
) -> NnrpEndpointSupport:
    endpoint = uri if isinstance(uri, NnrpEndpoint) else parse_nnrp_endpoint(uri)
    try:
        providers = discover_native_transport_providers(root, native_platform)
        selection = select_native_transport_provider(
            policy,
            root=root,
            native_platform=native_platform,
            supported_transports=supported_transports,
            requested_max_frame_bytes=requested_max_frame_bytes,
            candidate_readiness=[NativeTransportCandidateReadiness.ready(provider) for provider in providers],
            probe_observations=_probe_observations_from_samples(providers, tuple(probe_samples or ())),
        )
    except NativeArtifactError as error:
        message = str(error)
        return NnrpEndpointSupport(
            endpoint=endpoint,
            selection=None,
            available=False,
            skip_reason=message,
            diagnostic=f"skip {endpoint.uri}: {message}",
        )
    return NnrpEndpointSupport(
        endpoint=endpoint,
        selection=selection,
        available=True,
        diagnostic=f"NNRP endpoint {endpoint.uri} selected {selection.selected_transport_name} carrier",
    )


def diagnose_native_transport_endpoint_support(
    uri: str | NativeTransportEndpoint,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
) -> NativeTransportEndpointSupport:
    endpoint = uri if isinstance(uri, NativeTransportEndpoint) else parse_native_transport_endpoint(uri)
    providers = discover_native_transport_providers(root, native_platform)
    for provider in providers:
        if endpoint.transport_name in provider.transport_slots:
            return NativeTransportEndpointSupport(
                endpoint=endpoint,
                provider=provider,
                available=True,
                diagnostic=f"native transport provider {provider.name!r} exposes {endpoint.transport_name}",
            )
    return NativeTransportEndpointSupport(
        endpoint=endpoint,
        provider=None,
        available=False,
        skip_reason=f"native artifact does not expose {endpoint.transport_name} transport",
        diagnostic=f"skip {endpoint.uri}: install a preview4 {endpoint.transport_name} native transport artifact",
    )


def select_native_transport_provider(
    policy: TransportPolicy | str | int = TransportPolicy.AUTO,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    supported_transports: (
        tuple[str | TransportId, ...] | list[str | TransportId] | set[str | TransportId] | None
    ) = None,
    requested_max_frame_bytes: int | None = None,
    candidate_readiness: (tuple[NativeTransportCandidateReadiness, ...] | list[NativeTransportCandidateReadiness]),
    probe_observations: (
        tuple[NativeTransportProbeObservation, ...] | list[NativeTransportProbeObservation] | None
    ) = None,
) -> NativeTransportSelection:
    resolved_policy = _normalize_native_transport_policy(policy)
    providers = discover_native_transport_providers(root, native_platform)
    return _select_native_transport_provider_from_providers(
        providers,
        resolved_policy,
        supported_transports=supported_transports,
        requested_max_frame_bytes=requested_max_frame_bytes,
        candidate_readiness=candidate_readiness,
        probe_observations=probe_observations,
    )


def _select_native_transport_provider_from_providers(
    providers: tuple[NativeTransportProvider, ...],
    policy: TransportPolicy | str | int = TransportPolicy.AUTO,
    *,
    supported_transports: (
        tuple[str | TransportId, ...] | list[str | TransportId] | set[str | TransportId] | None
    ) = None,
    requested_max_frame_bytes: int | None = None,
    candidate_readiness: (tuple[NativeTransportCandidateReadiness, ...] | list[NativeTransportCandidateReadiness]),
    probe_observations: (
        tuple[NativeTransportProbeObservation, ...] | list[NativeTransportProbeObservation] | None
    ) = None,
    provider_availability: Mapping[str, bool] | None = None,
    provider_diagnostics: Mapping[str, str | None] | None = None,
) -> NativeTransportSelection:
    resolved_policy = _normalize_native_transport_policy(policy)
    supported = _normalize_supported_native_transports(supported_transports)
    if requested_max_frame_bytes is not None:
        _require_bounded_integer("requested_max_frame_bytes", requested_max_frame_bytes, 0xFFFFFFFFFFFFFFFF)
    readiness = tuple(candidate_readiness)
    observations = tuple(probe_observations or ())
    _validate_native_transport_selection_evidence(providers, readiness, observations)
    candidates = _evaluate_native_transport_candidates(
        providers,
        supported,
        resolved_policy,
        requested_max_frame_bytes,
        readiness,
        provider_availability or {},
        provider_diagnostics or {},
    )
    eligible = [index for index, (_, candidate) in enumerate(candidates) if candidate.rejection_reason is None]
    if len(eligible) == 1:
        selected_index = eligible[0]
        selected_provider, selected_candidate = candidates[selected_index]
        candidates[selected_index] = (selected_provider, replace(selected_candidate, selection_rank=0))
        return NativeTransportSelection(
            selected_provider=selected_provider,
            candidates=_ordered_native_transport_diagnostics(candidates),
            policy=resolved_policy,
            diagnostic="single eligible native transport selected directly",
        )

    for index in eligible:
        provider, candidate = candidates[index]
        observation = _matching_native_probe_observation(provider, observations)
        if observation is not None and observation.state is NativeTransportProbeState.SUCCEEDED:
            updated = replace(
                candidate,
                probe_state=NativeTransportProbeState.SUCCEEDED,
                probe=observation.metrics,
                diagnostic=observation.diagnostic,
            )
        elif observation is not None:
            updated = replace(
                candidate,
                probe_state=NativeTransportProbeState.FAILED,
                rejection_reason=NativeTransportRejectionReason.PROBE_FAILED,
                diagnostic=observation.diagnostic,
            )
        else:
            updated = replace(
                candidate,
                probe_state=NativeTransportProbeState.MISSING,
                rejection_reason=NativeTransportRejectionReason.PROBE_MISSING,
            )
        candidates[index] = (provider, updated)

    successful = [
        index
        for index, (_, candidate) in enumerate(candidates)
        if candidate.probe_state is NativeTransportProbeState.SUCCEEDED
    ]

    def compare_candidate_indices(left: int, right: int) -> int:
        return _compare_native_transport_candidates(
            candidates[left],
            candidates[right],
            resolved_policy,
        )

    successful.sort(key=cmp_to_key(compare_candidate_indices))
    for rank, index in enumerate(successful):
        provider, candidate = candidates[index]
        candidates[index] = (provider, replace(candidate, selection_rank=rank))
    if not successful:
        raise _native_transport_selection_error(
            resolved_policy,
            _ordered_native_transport_diagnostics(candidates),
        )
    selected_provider = candidates[successful[0]][0]
    return NativeTransportSelection(
        selected_provider=selected_provider,
        candidates=_ordered_native_transport_diagnostics(candidates),
        policy=resolved_policy,
        diagnostic="native transport selected by deterministic probe ordering",
    )


def native_transport_slot_names(mask: int) -> tuple[str, ...]:
    return tuple(name for name, slot in NATIVE_TRANSPORT_SLOT_BY_NAME.items() if mask & slot)


def native_runtime_feature_flag_names(
    feature_flags: int | NativeRuntimeFeatureFlag,
    *,
    mask: int | NativeRuntimeFeatureFlag | None = None,
) -> tuple[str, ...]:
    selected_flags = int(feature_flags)
    if mask is not None:
        selected_flags &= int(mask)
    return tuple(name for flag, name in _RUNTIME_FEATURE_FLAG_NAMES.items() if selected_flags & int(flag))


def native_runtime_feature_flags_available(
    feature_flags: int | NativeRuntimeFeatureFlag,
    required: int | NativeRuntimeFeatureFlag,
) -> bool:
    required_flags = int(required)
    return int(feature_flags) & required_flags == required_flags


def _evaluate_native_transport_candidates(
    providers: tuple[NativeTransportProvider, ...],
    supported_transports: frozenset[str],
    policy: TransportPolicy,
    requested_max_frame_bytes: int | None,
    readiness: tuple[NativeTransportCandidateReadiness, ...],
    provider_availability: Mapping[str, bool],
    provider_diagnostics: Mapping[str, str | None],
) -> list[tuple[NativeTransportProvider, NativeTransportCandidateDiagnostic]]:
    candidates: list[tuple[NativeTransportProvider, NativeTransportCandidateDiagnostic]] = []
    for provider in providers:
        transport_name = provider.name
        provider_readiness = _matching_native_candidate_readiness(provider, readiness)
        local_available = provider_availability.get(provider.metadata.id, True)
        peer_supported = transport_name in supported_transports
        within_limits = (
            requested_max_frame_bytes is None or requested_max_frame_bytes <= provider.metadata.limits.max_frame_bytes
        )
        if not _native_transport_policy_allows(policy, transport_name):
            rejection_reason = NativeTransportRejectionReason.POLICY_DISALLOWED
        elif not local_available:
            rejection_reason = NativeTransportRejectionReason.LOCAL_UNAVAILABLE
        elif not peer_supported:
            rejection_reason = NativeTransportRejectionReason.PEER_UNSUPPORTED
        elif not within_limits:
            rejection_reason = NativeTransportRejectionReason.LIMIT_EXCEEDED
        elif not provider_readiness.route_resolved:
            rejection_reason = NativeTransportRejectionReason.ROUTE_UNRESOLVED
        elif not provider_readiness.security_satisfied:
            rejection_reason = NativeTransportRejectionReason.SECURITY_UNSATISFIED
        else:
            rejection_reason = None
        candidates.append(
            (
                provider,
                NativeTransportCandidateDiagnostic(
                    transport_name=transport_name,
                    transport_id=NATIVE_TRANSPORT_ID_BY_NAME[transport_name],
                    provider=provider.metadata,
                    local_available=local_available,
                    peer_supported=peer_supported,
                    within_limits=within_limits,
                    probe_state=NativeTransportProbeState.NOT_RUN,
                    rejection_reason=rejection_reason,
                    diagnostic=(
                        provider_diagnostics.get(provider.metadata.id)
                        if not local_available
                        else provider_readiness.diagnostic
                    ),
                ),
            )
        )
    return candidates


def summarize_native_provider_probe(
    provider: NativeTransportProvider,
    samples: tuple[NativeTransportProbeSample, ...] | list[NativeTransportProbeSample],
) -> NativeTransportProbeMetrics | None:
    matching = tuple(_matching_native_probe_samples(provider, tuple(samples)))
    return _summarize_native_probe_samples(matching)


def _probe_observations_from_samples(
    providers: tuple[NativeTransportProvider, ...],
    samples: tuple[NativeTransportProbeSample, ...],
) -> tuple[NativeTransportProbeObservation, ...]:
    observations: list[NativeTransportProbeObservation] = []
    for provider in providers:
        matching = tuple(_matching_native_probe_samples(provider, samples))
        if not matching:
            continue
        metrics = _summarize_native_probe_samples(matching)
        if metrics is None:
            observations.append(NativeTransportProbeObservation.failed(provider, "transport probe failed"))
        else:
            observations.append(NativeTransportProbeObservation.succeeded(provider, metrics))
    return tuple(observations)


def _summarize_native_probe_samples(
    matching: tuple[NativeTransportProbeSample, ...],
) -> NativeTransportProbeMetrics | None:
    successful = tuple(
        sample
        for sample in matching
        if not sample.failed and not sample.timed_out and sample.rtt_us is not None and sample.elapsed_us > 0
    )
    if not successful:
        return None
    throughputs = [
        min(
            min(sample.bytes_sent + sample.bytes_received, 0xFFFFFFFFFFFFFFFF) * 1_000_000 // sample.elapsed_us,
            0xFFFFFFFFFFFFFFFF,
        )
        for sample in successful
    ]
    rtts = [sample.rtt_us for sample in successful if sample.rtt_us is not None]
    return NativeTransportProbeMetrics(
        sample_count=min(len(matching), 0xFFFFFFFF),
        success_count=min(len(successful), 0xFFFFFFFF),
        median_throughput_bytes_per_sec=_native_transport_median(throughputs),
        median_rtt_us=_native_transport_median(rtts),
    )


def _compare_native_transport_candidates(
    left: tuple[NativeTransportProvider, NativeTransportCandidateDiagnostic],
    right: tuple[NativeTransportProvider, NativeTransportCandidateDiagnostic],
    policy: TransportPolicy,
) -> int:
    left_provider, left_candidate = left
    right_provider, right_candidate = right
    left_probe = left_candidate.probe
    right_probe = right_candidate.probe
    if left_probe is None or right_probe is None:
        raise AssertionError("successful candidates must carry probe metrics")
    comparisons = (
        _compare_values(right_probe.success_count, left_probe.success_count),
        _compare_values(
            right_probe.median_throughput_bytes_per_sec,
            left_probe.median_throughput_bytes_per_sec,
        ),
        _compare_values(left_probe.median_rtt_us, right_probe.median_rtt_us),
        _compare_native_transport_cost(left_provider.metadata.cost, right_provider.metadata.cost),
        _compare_values(
            0 if _native_transport_is_preferred(policy, left_provider.name) else 1,
            0 if _native_transport_is_preferred(policy, right_provider.name) else 1,
        ),
        _compare_values(left_provider.metadata.preference_rank, right_provider.metadata.preference_rank),
        _compare_values(int(left_candidate.transport_id), int(right_candidate.transport_id)),
        _compare_values(left_provider.metadata.id.encode(), right_provider.metadata.id.encode()),
    )
    return next((comparison for comparison in comparisons if comparison), 0)


def _compare_native_transport_cost(
    left: NativeTransportProviderCost,
    right: NativeTransportProviderCost,
) -> int:
    if left.model_id != 0 and left.model_id == right.model_id:
        return _compare_values(left.units, right.units)
    return 0


def _compare_values(left: Any, right: Any) -> int:
    return (left > right) - (left < right)


def _native_transport_is_preferred(policy: TransportPolicy, transport_name: str) -> bool:
    preferred = {
        TransportPolicy.PREFER_QUIC: "quic",
        TransportPolicy.PREFER_TCP: "tcp",
        TransportPolicy.PREFER_IPC: "ipc",
        TransportPolicy.PREFER_WEBSOCKET: "websocket",
    }.get(policy)
    return preferred == transport_name


def _ordered_native_transport_diagnostics(
    candidates: list[tuple[NativeTransportProvider, NativeTransportCandidateDiagnostic]],
) -> tuple[NativeTransportCandidateDiagnostic, ...]:
    return tuple(
        candidate
        for _, candidate in sorted(
            candidates,
            key=lambda item: (
                item[1].selection_rank is None,
                item[1].selection_rank if item[1].selection_rank is not None else int(item[1].transport_id),
                b"" if item[1].selection_rank is not None else item[1].provider.id.encode(),
            ),
        )
    )


def _native_transport_median(values: list[int]) -> int:
    values.sort()
    upper = len(values) // 2
    if len(values) % 2:
        return values[upper]
    lower_value = values[upper - 1]
    return lower_value + (values[upper] - lower_value) // 2


def _matching_native_probe_samples(
    provider: NativeTransportProvider,
    samples: tuple[NativeTransportProbeSample, ...],
) -> tuple[NativeTransportProbeSample, ...]:
    transport_id = NATIVE_TRANSPORT_ID_BY_NAME[provider.name]
    return tuple(
        sample
        for sample in samples
        if sample.provider_id == provider.metadata.id
        and NATIVE_TRANSPORT_ID_BY_NAME[_normalize_native_transport_scope(sample.transport_name)] == transport_id
    )


def _native_transport_provider_key(provider: NativeTransportProvider) -> tuple[TransportId, str]:
    return NATIVE_TRANSPORT_ID_BY_NAME[provider.name], provider.metadata.id


def _matching_native_candidate_readiness(
    provider: NativeTransportProvider,
    readiness: tuple[NativeTransportCandidateReadiness, ...],
) -> NativeTransportCandidateReadiness:
    provider_key = _native_transport_provider_key(provider)
    return next(record for record in readiness if (record.transport_id, record.provider_id) == provider_key)


def _matching_native_probe_observation(
    provider: NativeTransportProvider,
    observations: tuple[NativeTransportProbeObservation, ...],
) -> NativeTransportProbeObservation | None:
    provider_key = _native_transport_provider_key(provider)
    return next(
        (
            observation
            for observation in observations
            if (observation.transport_id, observation.provider_id) == provider_key
        ),
        None,
    )


def _validate_native_transport_selection_evidence(
    providers: tuple[NativeTransportProvider, ...],
    readiness: tuple[NativeTransportCandidateReadiness, ...],
    observations: tuple[NativeTransportProbeObservation, ...],
) -> None:
    transport_ids = [NATIVE_TRANSPORT_ID_BY_NAME[provider.name] for provider in providers]
    provider_ids = [provider.metadata.id for provider in providers]
    provider_keys = {_native_transport_provider_key(provider) for provider in providers}
    readiness_keys = [(record.transport_id, record.provider_id) for record in readiness]
    observation_keys = [(record.transport_id, record.provider_id) for record in observations]

    if len(set(transport_ids)) != len(transport_ids) or len(set(provider_ids)) != len(provider_ids):
        raise NativeTransportSelectionError(
            NativeTransportSelectionErrorCode.INVALID_EVIDENCE,
            "provider registry contains duplicate transport or provider identifiers",
        )
    if any(key not in provider_keys for key in readiness_keys):
        raise NativeTransportSelectionError(
            NativeTransportSelectionErrorCode.INVALID_EVIDENCE,
            "candidate readiness contains an unmatched provider key",
        )
    if len(set(readiness_keys)) != len(readiness_keys):
        raise NativeTransportSelectionError(
            NativeTransportSelectionErrorCode.INVALID_EVIDENCE,
            "candidate readiness contains a duplicate provider key",
        )
    if set(readiness_keys) != provider_keys:
        raise NativeTransportSelectionError(
            NativeTransportSelectionErrorCode.INVALID_EVIDENCE,
            "candidate readiness must contain exactly one record for every provider",
        )
    if any(key not in provider_keys for key in observation_keys):
        raise NativeTransportSelectionError(
            NativeTransportSelectionErrorCode.INVALID_EVIDENCE,
            "probe observations contain an unmatched provider key",
        )
    if len(set(observation_keys)) != len(observation_keys):
        raise NativeTransportSelectionError(
            NativeTransportSelectionErrorCode.INVALID_EVIDENCE,
            "probe observations contain a duplicate provider key",
        )


def _native_transport_selection_error(
    policy: TransportPolicy,
    candidates: tuple[NativeTransportCandidateDiagnostic, ...],
) -> NativeTransportSelectionError:
    forced_transport = _forced_native_transport_name(policy)
    if forced_transport is not None:
        forced_candidate = next(
            (candidate for candidate in candidates if candidate.transport_name == forced_transport),
            None,
        )
        if forced_candidate is not None and forced_candidate.rejection_reason is not None:
            return NativeTransportSelectionError(
                NativeTransportSelectionErrorCode.FORCED_TRANSPORT_UNAVAILABLE,
                f"forced native transport {forced_transport} rejected: {forced_candidate.rejection_reason.value}",
                policy=policy,
                candidates=candidates,
            )
        return NativeTransportSelectionError(
            NativeTransportSelectionErrorCode.FORCED_TRANSPORT_UNAVAILABLE,
            f"forced native transport is not available: {forced_transport}",
            policy=policy,
            candidates=candidates,
        )
    return NativeTransportSelectionError(
        NativeTransportSelectionErrorCode.NO_VIABLE_TRANSPORT,
        "no viable native transport provider after applying policy and remote support",
        policy=policy,
        candidates=candidates,
    )


def _normalize_native_transport_policy(policy: TransportPolicy | str | int) -> TransportPolicy:
    if isinstance(policy, TransportPolicy):
        return policy
    if isinstance(policy, int):
        return TransportPolicy(policy)
    normalized = policy.strip().lower().replace("-", "_")
    if normalized == "auto":
        return TransportPolicy.AUTO
    try:
        return TransportPolicy[normalized.upper()]
    except KeyError as error:
        raise NativeArtifactError(f"unsupported native transport policy: {policy}") from error


def _normalize_supported_native_transports(
    supported_transports: tuple[str | TransportId, ...] | list[str | TransportId] | set[str | TransportId] | None,
) -> frozenset[str]:
    if supported_transports is None:
        return frozenset(NATIVE_TRANSPORT_SCOPES)
    normalized: set[str] = set()
    for value in supported_transports:
        if isinstance(value, TransportId):
            try:
                normalized.add(NATIVE_TRANSPORT_NAME_BY_ID[value])
            except KeyError as error:
                raise NativeArtifactError(f"unsupported native transport id: {value}") from error
        else:
            normalized.add(_normalize_native_transport_scope(value))
    return frozenset(normalized)


def _forced_native_transport_name(policy: TransportPolicy) -> str | None:
    if policy is TransportPolicy.FORCE_QUIC:
        return "quic"
    if policy is TransportPolicy.FORCE_TCP:
        return "tcp"
    if policy is TransportPolicy.FORCE_IPC:
        return "ipc"
    if policy is TransportPolicy.FORCE_WEBSOCKET:
        return "websocket"
    return None


def _native_transport_policy_allows(policy: TransportPolicy, transport_name: str) -> bool:
    forced_transport = _forced_native_transport_name(policy)
    return forced_transport is None or forced_transport == transport_name


def load_native_library(artifact_path: Path | str) -> ctypes.CDLL:
    try:
        return ctypes.CDLL(str(artifact_path))
    except OSError as error:
        raise NativeArtifactError(f"failed to load native artifact {artifact_path}: {error}") from error


def _register_native_runtime_shutdown(library: Any, artifact_path: Path) -> None:
    shutdown = _bind_native_function(
        library,
        "nnrp_transport_runtime_shutdown",
        _NnrpFfiStatus,
        [],
    )
    key = os.path.normcase(str(artifact_path.resolve()))
    global _NATIVE_RUNTIME_ATEXIT_REGISTERED
    with _NATIVE_RUNTIME_SHUTDOWN_LOCK:
        _NATIVE_RUNTIME_SHUTDOWNS.setdefault(key, (library, shutdown))
        if not _NATIVE_RUNTIME_ATEXIT_REGISTERED:
            atexit.register(_shutdown_registered_native_runtimes)
            _NATIVE_RUNTIME_ATEXIT_REGISTERED = True


def _shutdown_registered_native_runtimes() -> None:
    with _NATIVE_RUNTIME_SHUTDOWN_LOCK:
        shutdowns = tuple(_NATIVE_RUNTIME_SHUTDOWNS.values())
        _NATIVE_RUNTIME_SHUTDOWNS.clear()
    for _library, shutdown in shutdowns:
        try:
            shutdown()
        except Exception:
            # Interpreter teardown must continue if one native module cannot stop cleanly.
            pass


def load_native_runtime(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    transport: str | None = None,
    library: Any | None = None,
) -> NativeRuntimeEntrypoints:
    resolved_path = (
        Path(artifact_path)
        if artifact_path is not None
        else resolve_native_artifact(root, native_platform, transport=transport)
    )
    loaded_library = library if library is not None else load_native_library(resolved_path)
    capabilities = _call_runtime_capabilities(loaded_library)
    _validate_runtime_capabilities(
        capabilities,
        required_transport_slots=_required_transport_slots_for_artifact(resolved_path, transport),
    )
    _register_native_runtime_shutdown(loaded_library, resolved_path)
    return NativeRuntimeEntrypoints(
        loaded_library,
        artifact_path=resolved_path,
    )


def resolve_native_transport_endpoint(
    endpoint: str | NnrpEndpoint,
    transport_name: str,
    *,
    provider_endpoint: str | NativeTransportEndpoint | None = None,
) -> NativeTransportEndpoint:
    application_endpoint = endpoint if isinstance(endpoint, NnrpEndpoint) else parse_nnrp_endpoint(endpoint)
    normalized_transport = _normalize_native_transport_scope(transport_name)
    if provider_endpoint is not None:
        resolved = (
            provider_endpoint
            if isinstance(provider_endpoint, NativeTransportEndpoint)
            else parse_native_transport_endpoint(provider_endpoint)
        )
        if resolved.transport_name != normalized_transport:
            raise NativeArtifactError(
                f"{normalized_transport} provider cannot use {resolved.transport_name} carrier endpoint"
            )
        return resolved
    if normalized_transport in {"ipc", "websocket"}:
        raise NativeArtifactError(f"{normalized_transport} requires an explicit provider_endpoint")

    parsed = urlsplit(application_endpoint.uri)
    hostname = parsed.hostname
    if hostname is None:
        raise NativeArtifactError("NNRP application endpoint authority does not contain a host")
    port = parsed.port or 4433
    host = f"[{hostname}]" if ":" in hostname else hostname
    if normalized_transport == "tcp":
        scheme = "tcp"
    elif normalized_transport == "quic":
        scheme = "quic+tls" if application_endpoint.secure else "quic"
    else:
        raise NativeArtifactError(f"unsupported native transport provider: {normalized_transport}")
    return parse_native_transport_endpoint(f"{scheme}://{host}:{port}")


def load_native_transport_binding(
    name: str,
    *,
    artifact_path: Path | str | None = None,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    library: Any | None = None,
) -> NativeTransportBinding:
    provider = resolve_native_transport_provider(
        name,
        root=root,
        native_platform=native_platform,
    )
    if artifact_path is not None:
        provider = replace(provider, artifact_path=Path(artifact_path))
    loaded_library = library if library is not None else load_native_library(provider.artifact_path)
    capabilities = _call_runtime_capabilities(loaded_library)
    _validate_runtime_capabilities(
        capabilities,
        required_transport_slots=NATIVE_TRANSPORT_SLOT_BY_NAME[provider.name],
    )
    _register_native_runtime_shutdown(loaded_library, provider.artifact_path)
    return NativeTransportBinding(
        _NativeTransportEntrypoints(loaded_library, artifact_path=provider.artifact_path),
        provider,
        NativeRuntimeEntrypoints(loaded_library, artifact_path=provider.artifact_path),
    )


def load_native_client(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    transport: str | None = None,
    library: Any | None = None,
) -> NativeRuntimeClient:
    return NativeRuntimeClient(
        load_native_runtime(
            artifact_path,
            root=root,
            native_platform=native_platform,
            transport=transport,
            library=library,
        )
    )


def load_native_schema_codec(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    transport: str | None = None,
    library: Any | None = None,
) -> NativeSchemaCodec:
    return NativeSchemaCodec(
        load_native_runtime(
            artifact_path,
            root=root,
            native_platform=native_platform,
            transport=transport,
            library=library,
        )
    )


def load_native_recovery_codec(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    transport: str | None = None,
    library: Any | None = None,
) -> NativeRecoveryCodec:
    return NativeRecoveryCodec(
        load_native_runtime(
            artifact_path,
            root=root,
            native_platform=native_platform,
            transport=transport,
            library=library,
        )
    )


def select_native_runtime_backend(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    transport: str | None = None,
    library: Any | None = None,
    fallback: NativeRuntimeBackend | None = None,
    require_native: bool = False,
) -> NativeRuntimeBackend:
    try:
        return load_native_client(
            artifact_path,
            root=root,
            native_platform=native_platform,
            transport=transport,
            library=library,
        )
    except NativeArtifactError:
        if fallback is None or require_native:
            raise
        return fallback


def probe_native_artifact(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    transport: str | None = None,
    library: Any | None = None,
) -> NativeProbeResult:
    resolved_path = (
        Path(artifact_path)
        if artifact_path is not None
        else resolve_native_artifact(root, native_platform, transport=transport)
    )
    loaded_library = library if library is not None else load_native_library(resolved_path)
    capabilities = _call_runtime_capabilities(loaded_library)
    _validate_runtime_capabilities(
        capabilities,
        required_transport_slots=_required_transport_slots_for_artifact(resolved_path, transport),
    )
    return NativeProbeResult(
        artifact_path=resolved_path,
        abi_major=int(capabilities.abi_major),
        abi_minor=int(capabilities.abi_minor),
        abi_patch=int(capabilities.abi_patch),
        protocol_major=int(capabilities.protocol_version.major),
        protocol_wire_format=int(capabilities.protocol_version.wire_format),
        sdk_major=int(capabilities.sdk_major),
        sdk_minor=int(capabilities.sdk_minor),
        sdk_patch=int(capabilities.sdk_patch),
        sdk_channel=int(capabilities.sdk_channel),
        sdk_revision=int(capabilities.sdk_revision),
        transport_slots=int(capabilities.transport_slots),
        feature_flags=int(capabilities.feature_flags),
    )


def _validate_runtime_capabilities(
    capabilities: _NnrpRuntimeCapabilities,
    *,
    required_transport_slots: int = REQUIRED_TRANSPORT_SLOTS,
) -> None:
    if (
        capabilities.abi_major != EXPECTED_ABI_MAJOR
        or capabilities.abi_minor != EXPECTED_ABI_MINOR
        or capabilities.abi_patch != EXPECTED_ABI_PATCH
    ):
        raise NativeArtifactError(
            "native artifact ABI mismatch: "
            f"expected {EXPECTED_ABI_MAJOR}.{EXPECTED_ABI_MINOR}.{EXPECTED_ABI_PATCH}, "
            f"got {capabilities.abi_major}.{capabilities.abi_minor}.{capabilities.abi_patch}"
        )
    version = capabilities.protocol_version
    if version.major != EXPECTED_PROTOCOL_MAJOR or version.wire_format != EXPECTED_PROTOCOL_WIRE_FORMAT:
        raise NativeArtifactError(
            "native artifact protocol mismatch: "
            f"expected {EXPECTED_PROTOCOL_MAJOR}/{EXPECTED_PROTOCOL_WIRE_FORMAT}, "
            f"got {version.major}/{version.wire_format}"
        )
    missing_features = REQUIRED_RUNTIME_FEATURES & ~int(capabilities.feature_flags)
    if missing_features:
        raise NativeArtifactError(
            f"native artifact is missing required runtime feature flags: 0x{missing_features:016x}"
        )
    missing_transport_slots = required_transport_slots & ~int(capabilities.transport_slots)
    if missing_transport_slots:
        raise NativeArtifactError(
            f"native artifact is missing required transport slots: 0x{missing_transport_slots:08x}"
        )


def _provider_from_artifact_dir(
    artifact_dir: Path,
    native_platform: NativePlatform,
) -> NativeTransportProvider | None:
    library_path = artifact_dir / native_library_name(native_platform.os_name)
    if not library_path.is_file():
        return None
    manifest_path = artifact_dir / "manifest.json"
    if not manifest_path.is_file():
        raise NativeArtifactError(f"native transport artifact is missing manifest.json: {artifact_dir}")
    manifest = _load_native_artifact_manifest(manifest_path)
    scope = _manifest_transport_scope(manifest, artifact_dir)
    slots = _manifest_transport_slots(manifest, scope)
    if scope not in slots:
        raise NativeArtifactError(f"native artifact manifest scope {scope!r} is not listed in transport_slots")
    return NativeTransportProvider(
        name=scope,
        artifact_path=library_path,
        manifest_path=manifest_path,
        transport_slots=slots,
        enabled_features=_manifest_string_tuple(manifest, "enabled_features"),
        package=_manifest_required_string(manifest, "package"),
        transport_scope=scope,
        platform_tag=native_platform.tag,
        metadata=_manifest_provider_metadata(manifest),
    )


def _required_transport_slots_for_artifact(artifact_path: Path, transport: str | None) -> int:
    if transport is not None:
        return NATIVE_TRANSPORT_SLOT_BY_NAME[_normalize_native_transport_scope(transport)]
    manifest_path = artifact_path.with_name("manifest.json")
    if not manifest_path.is_file():
        return REQUIRED_TRANSPORT_SLOTS
    manifest = _load_native_artifact_manifest(manifest_path)
    scope = _manifest_transport_scope(manifest, artifact_path.parent)
    return NATIVE_TRANSPORT_SLOT_BY_NAME[scope]


def _load_native_artifact_manifest(manifest_path: Path) -> dict[str, Any]:
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as error:
        raise NativeArtifactError(f"native artifact manifest is invalid JSON: {manifest_path}") from error
    if not isinstance(document, dict):
        raise NativeArtifactError(f"native artifact manifest must be a JSON object: {manifest_path}")
    return document


def _manifest_transport_scope(manifest: Mapping[str, Any], artifact_dir: Path) -> str:
    raw_scope = manifest.get("transport_scope")
    if raw_scope is None:
        raise NativeArtifactError(f"native artifact manifest is missing transport_scope: {artifact_dir}")
    if not isinstance(raw_scope, str):
        raise NativeArtifactError("native artifact manifest transport_scope must be a string")
    scope = raw_scope.strip().lower().replace("_", "-")
    if scope in NATIVE_TRANSPORT_SCOPES:
        return scope
    raise NativeArtifactError(f"unsupported native transport scope: {raw_scope}")


def _manifest_transport_slots(manifest: Mapping[str, Any], scope: str) -> tuple[str, ...]:
    raw_slots = manifest.get("transport_slots")
    if raw_slots is None:
        raise NativeArtifactError("native artifact manifest is missing transport_slots")
    if not isinstance(raw_slots, list) or not raw_slots:
        raise NativeArtifactError("native artifact manifest transport_slots must be a non-empty list")
    slots: list[str] = []
    for raw_slot in raw_slots:
        if not isinstance(raw_slot, str):
            raise NativeArtifactError("native artifact manifest transport_slots entries must be strings")
        slot = raw_slot.strip().lower().replace("_", "-")
        if slot not in NATIVE_TRANSPORT_SCOPES:
            raise NativeArtifactError(f"unsupported native transport slot: {raw_slot}")
        if slot not in slots:
            slots.append(slot)
    return tuple(slots)


def _manifest_string_tuple(manifest: Mapping[str, Any], field_name: str) -> tuple[str, ...]:
    raw_values = manifest.get(field_name)
    if raw_values is None:
        return ()
    if not isinstance(raw_values, list):
        raise NativeArtifactError(f"native artifact manifest {field_name} must be a list")
    values: list[str] = []
    for raw_value in raw_values:
        if not isinstance(raw_value, str) or not raw_value:
            raise NativeArtifactError(f"native artifact manifest {field_name} entries must be non-empty strings")
        values.append(raw_value)
    return tuple(values)


def _manifest_optional_string(manifest: Mapping[str, Any], field_name: str) -> str | None:
    value = manifest.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise NativeArtifactError(f"native artifact manifest {field_name} must be a non-empty string")
    return value


def _manifest_required_string(manifest: Mapping[str, Any], field_name: str) -> str:
    value = _manifest_optional_string(manifest, field_name)
    if value is None:
        raise NativeArtifactError(f"native artifact manifest is missing {field_name}")
    return value


def _manifest_provider_metadata(manifest: Mapping[str, Any]) -> NativeTransportProviderMetadata:
    provider = manifest.get("provider")
    if not isinstance(provider, Mapping):
        raise NativeArtifactError("native artifact manifest provider must be an object")
    provider_id = _manifest_required_string(provider, "id")
    if not provider_id.isascii():
        raise NativeArtifactError("native artifact manifest provider.id must be ASCII")
    cost = provider.get("cost")
    if not isinstance(cost, Mapping):
        raise NativeArtifactError("native artifact manifest provider.cost must be an object")
    limits = provider.get("limits")
    if not isinstance(limits, Mapping):
        raise NativeArtifactError("native artifact manifest provider.limits must be an object")
    model_id = _manifest_bounded_integer(cost, "model_id", 0xFFFF)
    units = _manifest_canonical_u64(cost, "units")
    if model_id == 0 and units != 0:
        raise NativeArtifactError("native artifact manifest provider.cost.units must be zero when model_id is zero")
    preference_rank = _manifest_bounded_integer(provider, "preference_rank", 0xFFFF)
    max_frame_bytes = _manifest_canonical_u64(limits, "max_frame_bytes")
    if max_frame_bytes == 0:
        raise NativeArtifactError("native artifact manifest provider.limits.max_frame_bytes must be greater than zero")
    raw_limitations = provider.get("limitations")
    if not isinstance(raw_limitations, list):
        raise NativeArtifactError("native artifact manifest provider.limitations must be a list")
    limitations: list[NativeTransportProviderLimitation] = []
    for raw_limitation in raw_limitations:
        if not isinstance(raw_limitation, str):
            raise NativeArtifactError("native artifact manifest provider.limitations entries must be strings")
        try:
            limitation = NativeTransportProviderLimitation(raw_limitation)
        except ValueError as error:
            raise NativeArtifactError(f"unsupported native transport provider limitation: {raw_limitation}") from error
        if limitation in limitations:
            raise NativeArtifactError(f"duplicate native transport provider limitation: {raw_limitation}")
        limitations.append(limitation)
    return NativeTransportProviderMetadata(
        id=provider_id,
        cost=NativeTransportProviderCost(model_id=model_id, units=units),
        preference_rank=preference_rank,
        limits=NativeTransportProviderLimits(max_frame_bytes=max_frame_bytes),
        limitations=tuple(limitations),
    )


def _manifest_bounded_integer(manifest: Mapping[str, Any], field_name: str, maximum: int) -> int:
    value = manifest.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise NativeArtifactError(f"native artifact manifest {field_name} must be an integer in 0..{maximum}")
    return value


def _manifest_canonical_u64(manifest: Mapping[str, Any], field_name: str) -> int:
    value = manifest.get(field_name)
    if (
        not isinstance(value, str)
        or not value
        or (value != "0" and (not value.isascii() or not value.isdecimal() or value[0] == "0"))
    ):
        raise NativeArtifactError(f"native artifact manifest {field_name} must be a canonical decimal u64 string")
    parsed = int(value)
    if parsed > 0xFFFFFFFFFFFFFFFF:
        raise NativeArtifactError(f"native artifact manifest {field_name} exceeds u64")
    return parsed


def _call_runtime_capabilities(library: Any) -> _NnrpRuntimeCapabilities:
    try:
        function = library.nnrp_runtime_capabilities
    except AttributeError as error:
        raise NativeArtifactError("native artifact is missing nnrp_runtime_capabilities") from error

    try:
        function.restype = _NnrpRuntimeCapabilities
        function.argtypes = []
    except AttributeError:
        pass

    capabilities = function()
    if not hasattr(capabilities, "protocol_version") or not hasattr(capabilities, "feature_flags"):
        raise NativeArtifactError("native artifact returned an invalid runtime capabilities shape")
    return capabilities


def _bind_native_function(library: Any, name: str, restype: Any, argtypes: list[Any]) -> Any:
    try:
        function = getattr(library, name)
    except AttributeError as error:
        raise NativeArtifactError(f"native artifact is missing {name}") from error

    try:
        function.restype = restype
        function.argtypes = argtypes
    except AttributeError:
        pass
    return function


def raise_for_native_status(status: NativeStatus | _NnrpFfiStatus) -> None:
    if isinstance(status, _NnrpFfiStatus):
        return _raise_for_native_ffi_status(status)
    else:
        native_status = status
    if native_status.succeeded:
        return

    error_type = _STATUS_EXCEPTION_TYPES.get(native_status.status_code, NativeInternalError)
    raise error_type(native_status)


def _raise_for_native_ffi_status(status: _NnrpFfiStatus) -> None:
    if status.status_code == FFI_STATUS_OK:
        return

    native_status = NativeStatus.from_ffi(status)
    error_type = _STATUS_EXCEPTION_TYPES.get(native_status.status_code, NativeInternalError)
    raise error_type(native_status)


def _normalize_os(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "darwin": "macos",
        "macosx": "macos",
        "osx": "macos",
        "win32": "windows",
        "cygwin": "windows",
        "msys": "windows",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"windows", "macos", "linux", "android", "ios"}:
        raise NativeArtifactError(f"unsupported native artifact OS: {value}")
    return normalized


def _normalize_arch(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "i386": "x86",
        "i686": "x86",
        "aarch64": "arm64",
        "armv8": "arm64",
        "armv7": "arm",
        "armv7l": "arm",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"x86", "x86_64", "arm", "arm64"}:
        raise NativeArtifactError(f"unsupported native artifact architecture: {value}")
    return normalized


def _validate_u32(name: str, value: int) -> None:
    if not isinstance(value, int) or value < 0 or value > 0xFFFFFFFF:
        raise NativeHandleError(f"{name} must be a uint32 value")


def _require_bounded_integer(name: str, value: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f"{name} must be an integer in 0..{maximum}")


def _event_deadline(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout):
        raise TypeError("timeout must be a finite number of seconds or None")
    if timeout < 0:
        raise ValueError("timeout must be non-negative")
    return time.monotonic() + float(timeout)


def _event_poll_timeout_ms(deadline: float | None) -> int:
    if deadline is None:
        return _INDEFINITE_EVENT_POLL_SLICE_MS
    remaining_seconds = deadline - time.monotonic()
    if remaining_seconds <= 0:
        return 1
    return min(0xFFFFFFFF, max(1, math.ceil(remaining_seconds * 1_000)))


def _raise_if_event_deadline_expired(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))


def _ffi_role_poll_timeout_ms(timeout_ms: int) -> int:
    return 1 if timeout_ms == 0 else timeout_ms


def _submit_wait_deadline(timeout_ms: int) -> float | None:
    _require_bounded_integer("timeout_ms", timeout_ms, 0xFFFFFFFF)
    return None if timeout_ms == 0 else time.monotonic() + (timeout_ms / 1_000)


def _submit_wait_poll_timeout_ms(deadline: float | None) -> int:
    if deadline is None:
        return 0
    return _event_poll_timeout_ms(deadline)


def _validate_u64(name: str, value: int) -> None:
    if not isinstance(value, int) or value < 0 or value > 0xFFFFFFFFFFFFFFFF:
        raise NativeHandleError(f"{name} must be a uint64 value")


def _validate_pointer_and_length(ptr: int, length: int, *, detail: str) -> None:
    _validate_u64("ptr", ptr)
    if not isinstance(length, int) or length < 0:
        raise NativeHandleError("length must be non-negative")
    if length > 0 and ptr == 0:
        raise NativeHandleError(f"non-empty {detail} require a non-null pointer")


def _pointer_value(value: int | None) -> int:
    return int(value or 0)


def _void_pointer(value: int) -> ctypes.c_void_p:
    return ctypes.c_void_p(value or None)


def _buffer_view_from_payload(payload: bytes | bytearray | memoryview) -> tuple[_NnrpBufferView, object | None]:
    if isinstance(payload, bytes):
        length = len(payload)
        if length == 0:
            return _NnrpBufferView(None, 0), None
        buffer = ctypes.c_char_p(payload)
        return _NnrpBufferView(ctypes.cast(buffer, ctypes.c_void_p), length), buffer

    view = memoryview(payload)
    if view.nbytes == 0:
        return _NnrpBufferView(None, 0), None
    if not view.contiguous:
        raise NativeHandleError("native submit payload must be contiguous")
    try:
        buffer = (ctypes.c_char * view.nbytes).from_buffer(view)
    except TypeError:
        buffer = ctypes.c_char_p(view.tobytes())
    return _NnrpBufferView(ctypes.cast(buffer, ctypes.c_void_p), view.nbytes), buffer


def _u16_slice_from_values(values: Iterable[int]) -> tuple[_NnrpU16Slice, object | None]:
    normalized = tuple(int(value) for value in values)
    if any(not 0 <= value <= 0xFFFF for value in normalized):
        raise ValueError("u16 slice values must fit in u16")
    if not normalized:
        return _NnrpU16Slice(ctypes.POINTER(ctypes.c_uint16)(), 0), None
    owner = (ctypes.c_uint16 * len(normalized))(*normalized)
    return _NnrpU16Slice(ctypes.cast(owner, ctypes.POINTER(ctypes.c_uint16)), len(normalized)), owner


def _u32_slice_from_values(values: Iterable[int]) -> tuple[_NnrpU32Slice, object | None]:
    normalized = tuple(int(value) for value in values)
    if any(not 0 <= value <= 0xFFFFFFFF for value in normalized):
        raise ValueError("u32 slice values must fit in u32")
    if not normalized:
        return _NnrpU32Slice(ctypes.POINTER(ctypes.c_uint32)(), 0), None
    owner = (ctypes.c_uint32 * len(normalized))(*normalized)
    return _NnrpU32Slice(ctypes.cast(owner, ctypes.POINTER(ctypes.c_uint32)), len(normalized)), owner


def _normalize_transport_packets(
    packets: bytes | bytearray | memoryview | Iterable[bytes | bytearray | memoryview],
) -> tuple[bytes | bytearray | memoryview, ...]:
    normalized: tuple[bytes | bytearray | memoryview, ...]
    if isinstance(packets, (bytes, bytearray, memoryview)):
        normalized = (packets,)
    else:
        normalized = tuple(packets)
    if not normalized:
        raise ValueError("native transport send requires at least one complete NNRP packet")
    for packet in normalized:
        if not isinstance(packet, (bytes, bytearray, memoryview)):
            raise TypeError("native transport packets must be bytes-like values")
        if len(packet) == 0:
            raise ValueError("native transport packets must be non-empty")
    if len(normalized) > 0xFFFFFFFF:
        raise ValueError("native transport packet batch exceeds uint32 frame count")
    return normalized


def _decode_transport_packet_batch(encoded: bytes, frame_count: int) -> tuple[bytes, ...]:
    _require_bounded_integer("frame_count", frame_count, 0xFFFFFFFF)
    packets: list[bytes] = []
    offset = 0
    while offset < len(encoded):
        if len(encoded) - offset < 4:
            raise NativeHandleError("native transport batch ends inside a packet length prefix")
        packet_length = int.from_bytes(encoded[offset : offset + 4], "little")
        offset += 4
        packet_end = offset + packet_length
        if packet_end > len(encoded):
            raise NativeHandleError("native transport batch packet exceeds the owned payload")
        packets.append(encoded[offset:packet_end])
        offset = packet_end
    if len(packets) != frame_count:
        raise NativeHandleError(f"native transport batch declared {frame_count} packets but encoded {len(packets)}")
    return tuple(packets)


def _copy_buffer_view(view: _NnrpBufferView) -> bytes:
    length = int(view.len)
    if length == 0:
        return b""
    if not view.ptr:
        raise NativeHandleError("native event payload has non-empty null pointer")
    return ctypes.string_at(view.ptr, length)


def _borrow_buffer_view(view: NativeBufferView) -> memoryview:
    if view.length == 0:
        return memoryview(b"")
    array_type = ctypes.c_ubyte * view.length
    return memoryview(array_type.from_address(view.ptr)).toreadonly()


def _schema_descriptor_from_ffi(descriptor: _NnrpSchemaDescriptorHeader) -> Any:
    from nnrp.schema import SchemaDescriptorHeader

    return SchemaDescriptorHeader(
        schema_id=int(descriptor.schema_id),
        schema_version=int(descriptor.schema_version),
        profile_id=int(descriptor.profile_id),
        schema_flags=int(descriptor.schema_flags),
        min_version_major=int(descriptor.min_version_major),
        max_version_major=int(descriptor.max_version_major),
        body_bytes=int(descriptor.body_bytes),
        dependency_count=int(descriptor.dependency_count),
        default_stream_semantics=int(descriptor.default_stream_semantics),
        schema_hash=int(descriptor.schema_hash),
    )


def _schema_descriptor_to_ffi(descriptor: Any) -> _NnrpSchemaDescriptorHeader:
    return _NnrpSchemaDescriptorHeader(
        int(descriptor.schema_id),
        int(descriptor.schema_version),
        int(descriptor.profile_id),
        int(descriptor.schema_flags),
        int(descriptor.min_version_major),
        int(descriptor.max_version_major),
        0,
        int(descriptor.body_bytes),
        int(descriptor.dependency_count),
        int(descriptor.default_stream_semantics),
        int(descriptor.schema_hash),
    )


def _typed_payload_descriptor_from_ffi(descriptor: _NnrpTypedPayloadDescriptor) -> Any:
    from nnrp.schema import TypedPayloadDescriptor

    return TypedPayloadDescriptor(
        profile_id=int(descriptor.profile_id),
        payload_kind=PayloadKind(int(descriptor.payload_kind)),
        descriptor_flags=int(descriptor.descriptor_flags),
        schema_id=int(descriptor.schema_id),
        schema_version=int(descriptor.schema_version),
        stream_semantics=int(descriptor.stream_semantics),
        offset=int(descriptor.offset),
        length=int(descriptor.length),
    )


def _typed_payload_descriptor_to_ffi(descriptor: Any) -> _NnrpTypedPayloadDescriptor:
    return _NnrpTypedPayloadDescriptor(
        int(descriptor.profile_id),
        int(descriptor.payload_kind),
        int(descriptor.descriptor_flags),
        int(descriptor.schema_id),
        int(descriptor.schema_version),
        int(descriptor.stream_semantics),
        0,
        int(descriptor.offset),
        int(descriptor.length),
    )


def _runtime_object_descriptor_from_ffi(descriptor: _NnrpRuntimeObjectDescriptor) -> Any:
    from nnrp.runtime import MemoryLocationHint, ObjectDescriptorMetadata, OwnershipHint, RuntimeObjectKind, RuntimeRole

    return ObjectDescriptorMetadata(
        object_id=int(descriptor.object_id),
        object_kind=RuntimeObjectKind(int(descriptor.object_kind)),
        producer_role=RuntimeRole(int(descriptor.producer_role)),
        consumer_role=RuntimeRole(int(descriptor.consumer_role)),
        session_id=int(descriptor.session_id),
        byte_size=int(descriptor.byte_size),
        compute_cost_units=int(descriptor.compute_cost_units),
        memory_location_hint=MemoryLocationHint(int(descriptor.memory_location_hint)),
        ownership_hint=OwnershipHint(int(descriptor.ownership_hint)),
        lifetime_hint_ms=int(descriptor.lifetime_hint_ms),
        metadata_bytes=int(descriptor.metadata_bytes),
    )


def _runtime_object_descriptor_to_ffi(descriptor: Any) -> _NnrpRuntimeObjectDescriptor:
    return _NnrpRuntimeObjectDescriptor(
        int(descriptor.object_id),
        int(descriptor.object_kind),
        int(descriptor.producer_role),
        int(descriptor.consumer_role),
        int(descriptor.session_id),
        int(descriptor.byte_size),
        int(descriptor.compute_cost_units),
        int(descriptor.memory_location_hint),
        int(descriptor.ownership_hint),
        int(descriptor.lifetime_hint_ms),
        int(descriptor.metadata_bytes),
    )


def _schema_registry_action_from_ffi(action_code: int) -> Any:
    from nnrp.schema import SchemaRegistryAction

    mapping = {
        SCHEMA_REGISTRY_ACTION_INSTALLED: SchemaRegistryAction.INSTALLED,
        SCHEMA_REGISTRY_ACTION_ALREADY_INSTALLED: SchemaRegistryAction.ALREADY_INSTALLED,
        SCHEMA_REGISTRY_ACTION_UPDATED: SchemaRegistryAction.UPDATED,
        SCHEMA_REGISTRY_ACTION_INVALIDATED: SchemaRegistryAction.INVALIDATED,
    }
    try:
        return mapping[action_code]
    except KeyError as error:
        raise NativeHandleError(f"unknown native schema registry action {action_code}") from error


def _cache_identity_to_ffi(identity: Any) -> _NnrpCacheObjectId:
    return _NnrpCacheObjectId(
        int(identity.cache_namespace),
        int(identity.object_kind),
        int(identity.cache_key_hi),
        int(identity.cache_key_lo),
    )


def _cache_identity_key(identity: Any) -> tuple[int, int, int, int]:
    return (
        int(identity.cache_namespace),
        int(identity.cache_key_hi),
        int(identity.cache_key_lo),
        int(identity.object_kind),
    )


def _cache_identity_from_ffi(object_id: _NnrpCacheObjectId) -> Any:
    from nnrp.cache import CacheObjectIdentity

    return CacheObjectIdentity(
        cache_namespace=int(object_id.cache_namespace),
        object_kind=int(object_id.object_kind),
        cache_key_hi=int(object_id.cache_key_hi),
        cache_key_lo=int(object_id.cache_key_lo),
    )


def _cache_lease_result_from_ffi(result: _NnrpCacheLeaseResult) -> Any:
    from nnrp.cache import CacheLeaseDescriptor, CacheLeaseOwnerScope, CacheLeaseResult, CacheObjectVersion

    identity = _cache_identity_from_ffi(result.object_id)
    outcome = _CACHE_LEASE_OUTCOME_BY_CODE.get(int(result.outcome_code))
    if outcome is None:
        raise NativeHandleError(f"unknown native cache lease outcome {int(result.outcome_code)}")

    lease = None
    object_version = None
    if result.lease_handle.kind == HANDLE_KIND_CACHE_LEASE:
        lease = CacheLeaseDescriptor(
            identity=identity,
            object_version=int(result.object_version),
            lease_id=int(result.lease_id),
            owner_scope=CacheLeaseOwnerScope(int(result.owner_scope)),
            owner_id=int(result.owner_id),
            granted_at_ms=int(result.granted_at_ms),
            ttl_ms=int(result.ttl_ms),
        )
    if result.object_version != 0:
        object_version = CacheObjectVersion(identity=identity, object_version=int(result.object_version))
    return CacheLeaseResult(identity=identity, outcome=outcome, lease=lease, object_version=object_version)


def _coerce_operation_scheduling_hint(
    scheduling_hint: NativeOperationSchedulingHint | None,
    *,
    parent_operation_id: int | None,
    operation_group_id: int | None,
) -> NativeOperationSchedulingHint:
    if scheduling_hint is None:
        return NativeOperationSchedulingHint(
            parent_operation_id=parent_operation_id,
            operation_group_id=operation_group_id,
        )
    if parent_operation_id is not None and scheduling_hint.parent_operation_id != parent_operation_id:
        raise NativeHandleError("parent_operation_id conflicts with scheduling_hint")
    if operation_group_id is not None and scheduling_hint.operation_group_id != operation_group_id:
        raise NativeHandleError("operation_group_id conflicts with scheduling_hint")
    return scheduling_hint


def _format_status_message(status: NativeStatus) -> str:
    return (
        "native runtime status failed: "
        f"status_code={status.status_code}, "
        f"error_family={status.error_family}, "
        f"protocol_error_code={status.protocol_error_code}, "
        f"detail_code={status.detail_code}"
    )


_STATUS_EXCEPTION_TYPES = {
    FFI_STATUS_INVALID_ARGUMENT: NativeInvalidArgumentError,
    FFI_STATUS_INVALID_HANDLE: NativeInvalidHandleError,
    FFI_STATUS_INVALID_STATE: NativeInvalidStateError,
    FFI_STATUS_PROTOCOL_ERROR: NativeProtocolError,
    FFI_STATUS_WOULD_BLOCK: NativeWouldBlockError,
    FFI_STATUS_CALLBACK_REJECTED: NativeCallbackRejectedError,
    FFI_STATUS_INTERNAL_ERROR: NativeInternalError,
}

_STATUS_NAMES = {
    FFI_STATUS_OK: "ok",
    FFI_STATUS_INVALID_ARGUMENT: "invalid_argument",
    FFI_STATUS_INVALID_HANDLE: "invalid_handle",
    FFI_STATUS_INVALID_STATE: "invalid_state",
    FFI_STATUS_PROTOCOL_ERROR: "protocol_error",
    FFI_STATUS_WOULD_BLOCK: "would_block",
    FFI_STATUS_CALLBACK_REJECTED: "callback_rejected",
    FFI_STATUS_INTERNAL_ERROR: "internal_error",
}

_ERROR_FAMILY_NAMES = {
    ERROR_FAMILY_NONE: "none",
    ERROR_FAMILY_SESSION: "session",
    ERROR_FAMILY_CACHE: "cache",
    ERROR_FAMILY_SCHEMA: "schema",
    ERROR_FAMILY_TRANSPORT: "transport",
    ERROR_FAMILY_LIFECYCLE: "lifecycle",
    ERROR_FAMILY_OPERATION: "operation",
    ERROR_FAMILY_INTERNAL: "internal",
}

_PROTOCOL_ERROR_NAMES = {
    SESSION_ERROR_NONE: "session.none",
    SESSION_ERROR_AUTH_FAILED: "session.auth_failed",
    SESSION_ERROR_PROFILE_UNSUPPORTED: "session.profile_unsupported",
    SESSION_ERROR_SCHEMA_UNSUPPORTED: "session.schema_unsupported",
    SESSION_ERROR_PRIORITY_REJECTED: "session.priority_rejected",
    SESSION_ERROR_LEASE_POLICY_REJECTED: "session.lease_policy_rejected",
    SESSION_ERROR_RESUME_REJECTED: "session.resume_rejected",
    SESSION_ERROR_LIMIT_REACHED: "session.limit_reached",
    CACHE_ERROR_NONE: "cache.none",
    CACHE_ERROR_MISS: "cache.miss",
    CACHE_ERROR_LEASE_EXPIRED: "cache.lease_expired",
    CACHE_ERROR_VERSION_MISMATCH: "cache.version_mismatch",
    CACHE_ERROR_DEPENDENCY_INVALID: "cache.dependency_invalid",
    CACHE_ERROR_SCHEMA_MISMATCH: "cache.schema_mismatch",
    SCHEMA_ERROR_NONE: "schema.none",
    SCHEMA_ERROR_UNKNOWN: "schema.unknown",
    SCHEMA_ERROR_VERSION_UNKNOWN: "schema.version_unknown",
    SCHEMA_ERROR_HASH_CONFLICT: "schema.hash_conflict",
    SCHEMA_ERROR_INCOMPATIBLE: "schema.incompatible",
    SCHEMA_ERROR_DEPENDENCY_MISSING: "schema.dependency_missing",
    SCHEMA_ERROR_UPDATE_REJECTED: "schema.update_rejected",
}

_RETRYABLE_PROTOCOL_ERRORS = {
    SESSION_ERROR_LIMIT_REACHED,
    CACHE_ERROR_MISS,
    CACHE_ERROR_LEASE_EXPIRED,
    CACHE_ERROR_VERSION_MISMATCH,
    CACHE_ERROR_DEPENDENCY_INVALID,
    SCHEMA_ERROR_VERSION_UNKNOWN,
    SCHEMA_ERROR_DEPENDENCY_MISSING,
}

_EVENT_KIND_NAMES = {
    EVENT_KIND_NONE: "none",
    EVENT_KIND_CONNECTION_OPENED: "connection_opened",
    EVENT_KIND_SESSION_OPENED: "session_opened",
    EVENT_KIND_SESSION_PATCHED: "session_patched",
    EVENT_KIND_SESSION_CLOSED: "session_closed",
    EVENT_KIND_SUBMIT_ACCEPTED: "submit_accepted",
    EVENT_KIND_RESULT_PUSHED: "result_pushed",
    EVENT_KIND_RESULT_DROPPED: "result_dropped",
    EVENT_KIND_FLOW_UPDATED: "flow_updated",
    EVENT_KIND_CONTROL: "control",
    EVENT_KIND_ERROR: "error",
    EVENT_KIND_RESULT_HINT: "result_hint",
    EVENT_KIND_PARTIAL_RESULT: "partial_result",
    EVENT_KIND_RUNTIME_FRAME: "runtime_frame",
    EVENT_KIND_OPERATION_LIFECYCLE: "operation_lifecycle",
}

_PAYLOAD_FAMILY_NAMES = {"structured_event", "tool_delta", "workflow_state"}

_SESSION_PRIORITY_CLASS_CODES = {
    NativeSessionPriorityClass.INTERACTIVE: 0,
    NativeSessionPriorityClass.BALANCED: 1,
    NativeSessionPriorityClass.BACKGROUND: 2,
}

_SESSION_PRIORITY_CLASS_BY_CODE = {
    code: priority_class for priority_class, code in _SESSION_PRIORITY_CLASS_CODES.items()
}

_SESSION_RECOVERY_OUTCOME_NAMES = {
    SESSION_RECOVERY_OUTCOME_FRESH: "fresh",
    SESSION_RECOVERY_OUTCOME_RESUME_ENABLED: "resume_enabled",
    SESSION_RECOVERY_OUTCOME_RESUMED: "resumed",
    SESSION_RECOVERY_OUTCOME_RESUME_REJECTED: "resume_rejected",
}


def _cache_lease_outcomes() -> dict[int, Any]:
    from nnrp.cache import CacheLeaseOutcome

    return {
        CACHE_LEASE_OUTCOME_VALID: CacheLeaseOutcome.VALID,
        CACHE_LEASE_OUTCOME_MISS: CacheLeaseOutcome.MISSING,
        CACHE_LEASE_OUTCOME_EXPIRED: CacheLeaseOutcome.EXPIRED,
        CACHE_LEASE_OUTCOME_RELEASED: CacheLeaseOutcome.RELEASED,
    }


_CACHE_LEASE_OUTCOME_BY_CODE = _cache_lease_outcomes()
