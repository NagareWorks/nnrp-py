"""Native artifact discovery and ABI probe helpers for Rust-backed NNRP runtimes."""

from __future__ import annotations

import asyncio
import ctypes
import importlib
import json
import os
import platform
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from enum import IntFlag, StrEnum
from pathlib import Path
from typing import Any, Protocol, TypeVar, runtime_checkable
from urllib.parse import SplitResult, urlsplit

from nnrp.core.messages.control import TransportId, TransportPolicy

EXPECTED_PROTOCOL_MAJOR = 1
EXPECTED_PROTOCOL_WIRE_FORMAT = 0
EXPECTED_ABI_MAJOR = 1
MINIMUM_ABI_MINOR = 4
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
RUNTIME_FEATURE_CACHE_LEASE_OPS = 0x0000000000000400
RUNTIME_FEATURE_SCHEMA_REGISTRY_HANDLES = 0x0000000000000800
RUNTIME_FEATURE_BUFFER_HANDLES = 0x0000000000001000
RUNTIME_FEATURE_EXECUTABLE_RESUME = 0x0000000000002000
RUNTIME_FEATURE_CLIENT_COMPLETION_HELPERS = 0x0000000000004000
RUNTIME_FEATURE_CLIENT_COARSE_RESULT_HELPERS = 0x0000000000008000
RUNTIME_FEATURE_CLIENT_COMPACT_RESULT_HELPERS = 0x0000000000010000


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
    CLIENT_COMPLETION_HELPERS = RUNTIME_FEATURE_CLIENT_COMPLETION_HELPERS
    CLIENT_COARSE_RESULT_HELPERS = RUNTIME_FEATURE_CLIENT_COARSE_RESULT_HELPERS
    CLIENT_COMPACT_RESULT_HELPERS = RUNTIME_FEATURE_CLIENT_COMPACT_RESULT_HELPERS


RUNTIME_CONTROL_FEATURE_FLAGS = (
    NativeRuntimeFeatureFlag.CLIENT_API
    | NativeRuntimeFeatureFlag.SERVER_API
    | NativeRuntimeFeatureFlag.EVENT_POLLING
    | NativeRuntimeFeatureFlag.CALLBACK_DISPATCH
    | NativeRuntimeFeatureFlag.BATCH_POLLING
    | NativeRuntimeFeatureFlag.CLIENT_COMPLETION_HELPERS
    | NativeRuntimeFeatureFlag.CLIENT_COARSE_RESULT_HELPERS
    | NativeRuntimeFeatureFlag.CLIENT_COMPACT_RESULT_HELPERS
)
RUNTIME_OBJECT_FEATURE_FLAGS = (
    NativeRuntimeFeatureFlag.CACHE_SCHEMA
    | NativeRuntimeFeatureFlag.TYPED_PAYLOAD
    | NativeRuntimeFeatureFlag.CACHE_LEASE_OPS
    | NativeRuntimeFeatureFlag.SCHEMA_REGISTRY_HANDLES
    | NativeRuntimeFeatureFlag.BUFFER_HANDLES
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
    NativeRuntimeFeatureFlag.CLIENT_COMPLETION_HELPERS: "client_completion_helpers",
    NativeRuntimeFeatureFlag.CLIENT_COARSE_RESULT_HELPERS: "client_coarse_result_helpers",
    NativeRuntimeFeatureFlag.CLIENT_COMPACT_RESULT_HELPERS: "client_compact_result_helpers",
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
RESULT_STATE_NONE = 0
RESULT_STATE_COMPLETED = 1
RESULT_STATE_PARTIAL = 2
RESULT_STATE_DEGRADED = 3
RESULT_STATE_STALE_REUSE = 4
RESULT_STATE_CANCELLED = 5
RESULT_STATE_FAILED = 6
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
    | RUNTIME_FEATURE_CLIENT_COMPLETION_HELPERS
    | RUNTIME_FEATURE_CLIENT_COARSE_RESULT_HELPERS
    | RUNTIME_FEATURE_CLIENT_COMPACT_RESULT_HELPERS
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
CONTROL_CODE_RESULT_HINT = 0x18
DEFAULT_ARTIFACT_ROOT_ENV = "NNRP_NATIVE_ARTIFACT_ROOT"
NATIVE_BINDING_MODE_ENV = "NNRP_NATIVE_BINDING_MODE"
_CallbackEventT = TypeVar("_CallbackEventT")


class NativeArtifactError(RuntimeError):
    """Raised when a native artifact cannot be resolved, loaded, or accepted."""


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
class NativeTransportProvider:
    name: str
    artifact_path: Path
    manifest_path: Path | None
    transport_slots: tuple[str, ...]
    enabled_features: tuple[str, ...]
    package: str | None = None
    transport_scope: str | None = None
    platform_tag: str | None = None
    cost: Mapping[str, Any] | None = None
    preference: Mapping[str, Any] | None = None
    limitations: tuple[str, ...] = ()


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
    provider_name: str
    transport_name: str
    elapsed_us: int
    rtt_us: int | None = None
    bytes_sent: int = 0
    bytes_received: int = 0
    timed_out: bool = False
    failed: bool = False


@dataclass(frozen=True)
class NativeTransportProbeScore:
    sample_count: int
    failure_count: int
    failure_rate: float
    median_rtt_us: int
    throughput_bytes_per_sec: int
    score: float


@dataclass(frozen=True)
class NativeTransportProbeCandidate:
    provider: NativeTransportProvider
    transport_name: str
    transport_id: TransportId
    probe_score: NativeTransportProbeScore


@dataclass(frozen=True)
class NativeTransportRejection:
    provider_name: str
    transport_name: str
    transport_id: TransportId
    reason: str
    diagnostic: str | None = None


@dataclass(frozen=True)
class NativeTransportSelection:
    selected_provider: NativeTransportProvider
    selected_transport_name: str
    selected_transport_id: TransportId
    policy: TransportPolicy
    available_providers: tuple[NativeTransportProvider, ...]
    rejected: tuple[NativeTransportRejection, ...] = ()
    probe_candidates: tuple[NativeTransportProbeCandidate, ...] = ()
    selected_probe_score: NativeTransportProbeScore | None = None
    diagnostic: str | None = None


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


class _NnrpEvent(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_uint32),
        ("connection", _NnrpHandle),
        ("session", _NnrpHandle),
        ("operation", _NnrpHandle),
        ("frame_id", ctypes.c_uint32),
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


class _NnrpCompactResult(ctypes.Structure):
    _fields_ = [
        ("status", _NnrpFfiStatus),
        ("has_result", ctypes.c_uint8),
        ("event_kind", ctypes.c_uint32),
        ("result_state", ctypes.c_uint32),
        ("operation", _NnrpHandle),
        ("operation_id", ctypes.c_uint64),
        ("frame_id", ctypes.c_uint32),
        ("payload", _NnrpBufferView),
        ("diagnostic", _NnrpFfiDiagnostic),
    ]


class _NnrpConnectionBootstrap(ctypes.Structure):
    _fields_ = [
        ("connection_id", ctypes.c_uint64),
        ("generation", ctypes.c_uint32),
        ("transport_id", ctypes.c_uint32),
    ]


class _NnrpClientConnectRequest(ctypes.Structure):
    _fields_ = [
        ("connection_id", ctypes.c_uint64),
        ("generation", ctypes.c_uint32),
        ("transport_id", ctypes.c_uint32),
    ]


class _NnrpServerBindRequest(ctypes.Structure):
    _fields_ = [
        ("server_id", ctypes.c_uint64),
        ("generation", ctypes.c_uint32),
        ("transport_id", ctypes.c_uint32),
    ]


class _NnrpSessionOpenRequest(ctypes.Structure):
    _fields_ = [
        ("connection", _NnrpHandle),
        ("requested_session_id", ctypes.c_uint32),
        ("generation", ctypes.c_uint32),
        ("profile_id", ctypes.c_uint16),
        ("schema_id", ctypes.c_uint32),
        ("schema_version", ctypes.c_uint32),
    ]


class _NnrpSubmitRequest(ctypes.Structure):
    _fields_ = [
        ("session", _NnrpHandle),
        ("operation_id", ctypes.c_uint64),
        ("frame_id", ctypes.c_uint32),
        ("payload", _NnrpBufferView),
    ]


class _NnrpClientCancelRequest(ctypes.Structure):
    _fields_ = [
        ("session", _NnrpHandle),
        ("frame_id", ctypes.c_uint32),
    ]


class _NnrpClientCompleteOperationRequest(ctypes.Structure):
    _fields_ = [
        ("operation", _NnrpHandle),
        ("payload", _NnrpBufferView),
    ]


class _NnrpClientDropOperationRequest(ctypes.Structure):
    _fields_ = [
        ("operation", _NnrpHandle),
    ]


class _NnrpClientSubmitResultRequest(ctypes.Structure):
    _fields_ = [
        ("session", _NnrpHandle),
        ("operation_id", ctypes.c_uint64),
        ("frame_id", ctypes.c_uint32),
        ("submit_payload", _NnrpBufferView),
        ("result_payload", _NnrpBufferView),
        ("max_events", ctypes.c_size_t),
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


class _NnrpServerAcceptRequest(ctypes.Structure):
    _fields_ = [
        ("server", _NnrpHandle),
        ("session_id", ctypes.c_uint32),
        ("generation", ctypes.c_uint32),
        ("profile_id", ctypes.c_uint16),
        ("schema_id", ctypes.c_uint32),
        ("schema_version", ctypes.c_uint32),
    ]


class _NnrpServerReceiveSubmitRequest(ctypes.Structure):
    _fields_ = [
        ("session", _NnrpHandle),
        ("operation_id", ctypes.c_uint64),
        ("frame_id", ctypes.c_uint32),
        ("payload", _NnrpBufferView),
    ]


class _NnrpServerSendResultRequest(ctypes.Structure):
    _fields_ = [
        ("operation", _NnrpHandle),
        ("payload", _NnrpBufferView),
    ]


class _NnrpServerFlowUpdateRequest(ctypes.Structure):
    _fields_ = [
        ("session", _NnrpHandle),
        ("frame_id", ctypes.c_uint32),
    ]


class _NnrpControlRequest(ctypes.Structure):
    _fields_ = [
        ("handle", _NnrpHandle),
        ("control_code", ctypes.c_uint32),
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
        ("descriptor_flags", ctypes.c_uint16),
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
        ("cache_key_hi", ctypes.c_uint32),
        ("cache_key_lo", ctypes.c_uint32),
        ("object_kind", ctypes.c_uint32),
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
        ("expires_at_ms", ctypes.c_uint64),
    ]


class _NnrpSessionResumeRequest(ctypes.Structure):
    _fields_ = [
        ("connection", _NnrpHandle),
        ("requested_session_id", ctypes.c_uint32),
        ("generation", ctypes.c_uint32),
        ("profile_id", ctypes.c_uint16),
        ("schema_id", ctypes.c_uint32),
        ("schema_version", ctypes.c_uint32),
        ("resume_token_bytes", ctypes.c_uint32),
    ]


class NativeRuntimeEntrypoints:
    """ctypes entrypoint table for the frozen Rust runtime ABI."""

    def __init__(
        self,
        library: Any,
        *,
        artifact_path: Path | None = None,
        cffi_submit_result_api: _NativeCffiSubmitResultApi | None = None,
    ) -> None:
        self.artifact_path = artifact_path
        self.cffi_submit_result_api = cffi_submit_result_api
        self.current_protocol_version = _bind_native_function(
            library, "nnrp_current_protocol_version", _NnrpProtocolVersion, []
        )
        self.runtime_capabilities = _bind_native_function(
            library, "nnrp_runtime_capabilities", _NnrpRuntimeCapabilities, []
        )
        self.connection_bootstrap = _bind_native_function(
            library,
            "nnrp_connection_bootstrap",
            _NnrpFfiStatus,
            [_NnrpConnectionBootstrap, ctypes.POINTER(_NnrpHandle)],
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
        self.client_complete_operation = _bind_native_function(
            library,
            "nnrp_client_complete_operation",
            _NnrpFfiStatus,
            [_NnrpClientCompleteOperationRequest],
        )
        self.client_drop_operation = _bind_native_function(
            library,
            "nnrp_client_drop_operation",
            _NnrpFfiStatus,
            [_NnrpClientDropOperationRequest],
        )
        self.client_submit_result = _bind_native_function(
            library,
            "nnrp_client_submit_result",
            _NnrpFfiStatus,
            [_NnrpClientSubmitResultRequest, ctypes.POINTER(_NnrpHandle), ctypes.POINTER(_NnrpPollResult)],
        )
        self.client_submit_result_compact = _bind_native_function(
            library,
            "nnrp_client_submit_result_compact",
            _NnrpFfiStatus,
            [_NnrpClientSubmitResultRequest, ctypes.POINTER(_NnrpCompactResult)],
        )
        self.client_send_flow_update = _bind_native_function(
            library, "nnrp_client_send_flow_update", _NnrpFfiStatus, [_NnrpServerFlowUpdateRequest]
        )
        self.client_send_result_hint = _bind_native_function(
            library, "nnrp_client_send_result_hint", _NnrpFfiStatus, [_NnrpControlRequest]
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
            [_NnrpHandle, ctypes.POINTER(_NnrpEvent), ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)],
        )
        self.server_bind = _bind_native_function(
            library,
            "nnrp_server_bind",
            _NnrpFfiStatus,
            [_NnrpServerBindRequest, ctypes.POINTER(_NnrpHandle)],
        )
        self.server_accept = _bind_native_function(
            library,
            "nnrp_server_accept",
            _NnrpFfiStatus,
            [_NnrpServerAcceptRequest, ctypes.POINTER(_NnrpHandle)],
        )
        self.server_receive_submit = _bind_native_function(
            library,
            "nnrp_server_receive_submit",
            _NnrpFfiStatus,
            [_NnrpServerReceiveSubmitRequest, ctypes.POINTER(_NnrpHandle)],
        )
        self.server_send_result = _bind_native_function(
            library, "nnrp_server_send_result", _NnrpFfiStatus, [_NnrpServerSendResultRequest]
        )
        self.server_send_flow_update = _bind_native_function(
            library, "nnrp_server_send_flow_update", _NnrpFfiStatus, [_NnrpServerFlowUpdateRequest]
        )
        self.server_close = _bind_native_function(library, "nnrp_server_close", _NnrpFfiStatus, [_NnrpHandle])
        self.control = _bind_native_function(library, "nnrp_control", _NnrpFfiStatus, [_NnrpControlRequest])
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

    @property
    def binding_mode(self) -> str:
        return "cffi_api" if self.cffi_submit_result_api is not None else "ctypes"


@dataclass(frozen=True)
class _NativeCffiSubmitResultApi:
    ffi: Any
    library: Any
    artifact_path_bytes: bytes

    @property
    def supports_max_events(self) -> bool:
        return hasattr(self.library, "nnrp_py_client_submit_result_compact_v2")

    def submit_result_compact(
        self,
        *,
        session: NativeHandle,
        operation_id: int,
        frame_id: int,
        payload_view: Any,
        payload_len: int,
        max_events: int,
        out_result: Any,
    ) -> int | None:
        if self.supports_max_events:
            return self.library.nnrp_py_client_submit_result_compact_v2(
                self.artifact_path_bytes,
                session.kind,
                session.id,
                session.generation,
                session.flags,
                operation_id,
                frame_id,
                payload_view,
                payload_len,
                max_events,
                out_result,
            )

        if max_events != 2:
            return None

        return self.library.nnrp_py_client_submit_result_compact(
            self.artifact_path_bytes,
            session.kind,
            session.id,
            session.generation,
            session.flags,
            operation_id,
            frame_id,
            payload_view,
            payload_len,
            out_result,
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
_NATIVE_STRUCTURED_DIAGNOSTIC_OK = NativeStructuredDiagnostic(status=_NATIVE_STATUS_OK)


class NativeRuntimeEvent:
    __slots__ = ("connection", "diagnostic", "frame_id", "kind", "operation", "payload", "session")

    def __init__(
        self,
        kind: int,
        connection: NativeHandle,
        session: NativeHandle,
        operation: NativeHandle,
        frame_id: int,
        payload: bytes,
        diagnostic: NativeRuntimeDiagnostic,
    ) -> None:
        self.kind = kind
        self.connection = connection
        self.session = session
        self.operation = operation
        self.frame_id = frame_id
        self.payload = payload
        self.diagnostic = diagnostic

    @classmethod
    def from_ffi(cls, event: _NnrpEvent) -> NativeRuntimeEvent:
        return cls(
            kind=int(event.kind),
            connection=_native_handle_from_trusted_ffi(event.connection),
            session=_native_handle_from_trusted_ffi(event.session),
            operation=_native_handle_from_trusted_ffi(event.operation),
            frame_id=int(event.frame_id),
            payload=_copy_buffer_view(event.payload),
            diagnostic=NativeRuntimeDiagnostic.from_ffi(event.diagnostic),
        )

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

    def to_credit_update(self) -> NativeCreditUpdateEvent:
        return NativeCreditUpdateEvent.from_event(self)


@dataclass(frozen=True)
class NativeCreditUpdateEvent:
    connection: NativeHandle
    session: NativeHandle
    operation: NativeHandle
    frame_id: int
    diagnostic: NativeStructuredDiagnostic

    @classmethod
    def from_event(cls, event: NativeRuntimeEvent) -> NativeCreditUpdateEvent:
        if not event.is_flow_update:
            raise NativeHandleError(f"expected native flow update event, got {event.kind_name}")
        return cls(
            connection=event.connection,
            session=event.session,
            operation=event.operation,
            frame_id=event.frame_id,
            diagnostic=NativeStructuredDiagnostic.from_runtime_diagnostic(event.diagnostic),
        )


@dataclass(frozen=True)
class NativeResultHintEvent:
    connection: NativeHandle
    session: NativeHandle
    operation: NativeHandle
    frame_id: int
    payload: bytes
    event: NativeRuntimeEvent
    diagnostic: NativeStructuredDiagnostic

    @classmethod
    def from_event(cls, event: NativeRuntimeEvent) -> NativeResultHintEvent:
        if event.kind != EVENT_KIND_RESULT_HINT:
            raise NativeHandleError(f"expected native result hint event, got {event.kind_name}")
        return cls(
            connection=event.connection,
            session=event.session,
            operation=event.operation,
            frame_id=event.frame_id,
            payload=event.payload,
            event=event,
            diagnostic=NativeStructuredDiagnostic.from_runtime_diagnostic(event.diagnostic),
        )

    @property
    def metadata(self) -> Any:
        from nnrp.core.messages.control import ResultHintMetadata

        return ResultHintMetadata.unpack(self.payload)


@dataclass(frozen=True)
class NativePayloadFamilyEvent:
    payload_family: str
    connection: NativeHandle
    session: NativeHandle
    operation: NativeHandle
    frame_id: int
    payload: bytes
    event: NativeRuntimeEvent
    diagnostic: NativeStructuredDiagnostic

    @classmethod
    def from_event(cls, event: NativeRuntimeEvent, *, payload_family: str) -> NativePayloadFamilyEvent:
        normalized_family = payload_family.strip().lower()
        if normalized_family not in _PAYLOAD_FAMILY_NAMES:
            raise NativeHandleError(f"unknown native payload family {payload_family!r}")
        if not event.is_result_event and event.kind != EVENT_KIND_CONTROL:
            raise NativeHandleError(f"expected native result/control event, got {event.kind_name}")
        return cls(
            payload_family=normalized_family,
            connection=event.connection,
            session=event.session,
            operation=event.operation,
            frame_id=event.frame_id,
            payload=event.payload,
            event=event,
            diagnostic=NativeStructuredDiagnostic.from_runtime_diagnostic(event.diagnostic),
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
NativeCreditUpdateCallback = Callable[[NativeCreditUpdateEvent], None]
NativeResultHintCallback = Callable[[NativeResultHintEvent], None]
NativePayloadFamilyCallback = Callable[[NativePayloadFamilyEvent], None]


@dataclass(frozen=True)
class NativeRuntimePollResult:
    status: NativeStatus
    event: NativeRuntimeEvent | None = None

    @classmethod
    def from_ffi(cls, result: _NnrpPollResult) -> NativeRuntimePollResult:
        status = NativeStatus.from_ffi(result.status)
        event = NativeRuntimeEvent.from_ffi(result.event) if result.has_event else None
        return cls(status, event)


class NativeOperationLifecycle(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    DEGRADED = "degraded"
    STALE_REUSE = "stale_reuse"
    CANCELLED = "cancelled"
    FAILED = "failed"


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


class NativeRuntimeResult:
    __slots__ = (
        "_event",
        "_event_connection",
        "_event_diagnostic",
        "_event_kind",
        "_event_operation_flags",
        "_event_operation_generation",
        "_event_operation_id",
        "_event_operation_kind",
        "_event_session",
        "diagnostic",
        "frame_id",
        "operation_id",
        "payload",
        "state",
    )

    def __init__(
        self,
        state: NativeOperationLifecycle,
        operation_id: int,
        frame_id: int,
        payload: bytes,
        event: NativeRuntimeEvent | None,
        diagnostic: NativeStructuredDiagnostic,
        *,
        event_kind: int = EVENT_KIND_RESULT_PUSHED,
        event_connection: NativeHandle | None = None,
        event_session: NativeHandle | None = None,
        event_operation_kind: int = HANDLE_KIND_OPERATION,
        event_operation_id: int | None = None,
        event_operation_generation: int = 0,
        event_operation_flags: int = 0,
        event_diagnostic: NativeRuntimeDiagnostic = _NATIVE_RUNTIME_DIAGNOSTIC_OK,
    ) -> None:
        self.state = state
        self.operation_id = operation_id
        self.frame_id = frame_id
        self.payload = payload
        self.diagnostic = diagnostic
        self._event = event
        self._event_kind = event.kind if event is not None else event_kind
        self._event_connection = event.connection if event is not None else event_connection
        self._event_session = event.session if event is not None else event_session
        self._event_operation_kind = event.operation.kind if event is not None else event_operation_kind
        self._event_operation_id = (
            event.operation.id
            if event is not None
            else (operation_id if event_operation_id is None else event_operation_id)
        )
        self._event_operation_generation = (
            event.operation.generation if event is not None else event_operation_generation
        )
        self._event_operation_flags = event.operation.flags if event is not None else event_operation_flags
        self._event_diagnostic = event.diagnostic if event is not None else event_diagnostic

    @property
    def event(self) -> NativeRuntimeEvent:
        event = self._event
        if event is not None:
            return event
        operation = object.__new__(NativeHandle)
        object.__setattr__(operation, "kind", self._event_operation_kind)
        object.__setattr__(operation, "id", self._event_operation_id)
        object.__setattr__(operation, "generation", self._event_operation_generation)
        object.__setattr__(operation, "flags", self._event_operation_flags)
        event = NativeRuntimeEvent(
            self._event_kind,
            self._event_connection,
            self._event_session,
            operation,
            self.frame_id,
            self.payload,
            self._event_diagnostic,
        )
        self._event = event
        return event

    @classmethod
    def from_event(
        cls,
        event: NativeRuntimeEvent,
        *,
        state: NativeOperationLifecycle | str | None = None,
    ) -> NativeRuntimeResult:
        selected_state = NativeOperationLifecycle(state) if state is not None else _infer_lifecycle_from_event(event)
        return cls(
            state=selected_state,
            operation_id=event.operation.id,
            frame_id=event.frame_id,
            payload=event.payload,
            event=event,
            diagnostic=NativeStructuredDiagnostic.from_runtime_diagnostic(event.diagnostic),
        )


def _submit_result_from_ffi_event(
    event: _NnrpEvent,
    *,
    connection: NativeHandle,
    session: NativeHandle,
    state: NativeOperationLifecycle | str | None,
) -> NativeRuntimeResult:
    kind = int(event.kind)
    payload = _copy_buffer_view(event.payload)
    raw_diagnostic = event.diagnostic
    status = NativeStatus.from_ffi(raw_diagnostic.status)
    if (
        status is _NATIVE_STATUS_OK
        and raw_diagnostic.related_connection_id == 0
        and raw_diagnostic.related_session_id == 0
        and raw_diagnostic.related_operation_id == 0
        and raw_diagnostic.related_frame_id == 0
    ):
        diagnostic = _NATIVE_RUNTIME_DIAGNOSTIC_OK
        structured_diagnostic = _NATIVE_STRUCTURED_DIAGNOSTIC_OK
    else:
        diagnostic = NativeRuntimeDiagnostic(
            status=status,
            related_connection_id=int(raw_diagnostic.related_connection_id),
            related_session_id=int(raw_diagnostic.related_session_id),
            related_operation_id=int(raw_diagnostic.related_operation_id),
            related_frame_id=int(raw_diagnostic.related_frame_id),
        )
        structured_diagnostic = NativeStructuredDiagnostic(
            status=status,
            related_connection_id=diagnostic.related_connection_id,
            related_session_id=diagnostic.related_session_id,
            related_operation_id=diagnostic.related_operation_id,
            related_frame_id=diagnostic.related_frame_id,
        )
    raw_operation = event.operation
    operation = object.__new__(NativeHandle)
    object.__setattr__(operation, "kind", int(raw_operation.kind))
    object.__setattr__(operation, "id", int(raw_operation.id))
    object.__setattr__(operation, "generation", int(raw_operation.generation))
    object.__setattr__(operation, "flags", int(raw_operation.flags))
    frame_id = int(event.frame_id)
    runtime_event = NativeRuntimeEvent(kind, connection, session, operation, frame_id, payload, diagnostic)
    if state is not None:
        selected_state = NativeOperationLifecycle(state)
    elif status.status_code == FFI_STATUS_OK and kind != EVENT_KIND_ERROR:
        selected_state = (
            NativeOperationLifecycle.CANCELLED
            if kind == EVENT_KIND_RESULT_DROPPED
            else NativeOperationLifecycle.COMPLETED
        )
    else:
        selected_state = NativeOperationLifecycle.FAILED
    return NativeRuntimeResult(selected_state, operation.id, frame_id, payload, runtime_event, structured_diagnostic)


def _submit_result_from_ok_result_pushed_ffi_event(
    event: _NnrpEvent,
    *,
    connection: NativeHandle,
    session: NativeHandle,
) -> NativeRuntimeResult:
    payload = _copy_buffer_view(event.payload)
    raw_operation = event.operation
    operation = object.__new__(NativeHandle)
    object.__setattr__(operation, "kind", int(raw_operation.kind))
    object.__setattr__(operation, "id", int(raw_operation.id))
    object.__setattr__(operation, "generation", int(raw_operation.generation))
    object.__setattr__(operation, "flags", int(raw_operation.flags))
    frame_id = int(event.frame_id)
    runtime_event = NativeRuntimeEvent(
        EVENT_KIND_RESULT_PUSHED,
        connection,
        session,
        operation,
        frame_id,
        payload,
        _NATIVE_RUNTIME_DIAGNOSTIC_OK,
    )
    return NativeRuntimeResult(
        NativeOperationLifecycle.COMPLETED,
        operation.id,
        frame_id,
        payload,
        runtime_event,
        _NATIVE_STRUCTURED_DIAGNOSTIC_OK,
    )


def _submit_result_from_compact_ffi_result(
    compact: _NnrpCompactResult,
    *,
    connection: NativeHandle,
    session: NativeHandle,
    state: NativeOperationLifecycle | str | None,
    result_payload: bytes | bytearray | memoryview,
) -> NativeRuntimeResult:
    raw_diagnostic = compact.diagnostic
    raw_status = raw_diagnostic.status
    status = NativeStatus.from_ffi(raw_status)
    if (
        status is _NATIVE_STATUS_OK
        and raw_diagnostic.related_connection_id == 0
        and raw_diagnostic.related_session_id == 0
        and raw_diagnostic.related_operation_id == 0
        and raw_diagnostic.related_frame_id == 0
    ):
        diagnostic = _NATIVE_RUNTIME_DIAGNOSTIC_OK
        structured_diagnostic = _NATIVE_STRUCTURED_DIAGNOSTIC_OK
    else:
        diagnostic = NativeRuntimeDiagnostic(
            status=status,
            related_connection_id=int(raw_diagnostic.related_connection_id),
            related_session_id=int(raw_diagnostic.related_session_id),
            related_operation_id=int(raw_diagnostic.related_operation_id),
            related_frame_id=int(raw_diagnostic.related_frame_id),
        )
        structured_diagnostic = NativeStructuredDiagnostic(
            status=status,
            related_connection_id=diagnostic.related_connection_id,
            related_session_id=diagnostic.related_session_id,
            related_operation_id=diagnostic.related_operation_id,
            related_frame_id=diagnostic.related_frame_id,
        )

    raw_operation = compact.operation
    operation_id = int(raw_operation.id or compact.operation_id)
    frame_id = int(compact.frame_id)
    payload = _compact_result_payload(compact.payload, result_payload)
    event_kind = int(compact.event_kind)
    return NativeRuntimeResult(
        _compact_result_state(compact, status=status, state=state),
        int(compact.operation_id or operation_id),
        frame_id,
        payload,
        None,
        structured_diagnostic,
        event_kind=event_kind,
        event_connection=connection,
        event_session=session,
        event_operation_kind=int(raw_operation.kind),
        event_operation_id=operation_id,
        event_operation_generation=int(raw_operation.generation),
        event_operation_flags=int(raw_operation.flags),
        event_diagnostic=diagnostic,
    )


def _submit_result_from_ok_compact_ffi_result(
    compact: _NnrpCompactResult,
    *,
    connection: NativeHandle,
    session: NativeHandle,
    result_payload: bytes | bytearray | memoryview,
) -> NativeRuntimeResult:
    raw_operation = compact.operation
    operation_id = int(raw_operation.id or compact.operation_id)
    frame_id = int(compact.frame_id)
    payload_view = compact.payload
    if isinstance(result_payload, bytes) and int(payload_view.len) == len(result_payload):
        payload = result_payload
    else:
        payload = _copy_buffer_view(payload_view)
    result = object.__new__(NativeRuntimeResult)
    result.state = NativeOperationLifecycle.COMPLETED
    result.operation_id = int(compact.operation_id or operation_id)
    result.frame_id = frame_id
    result.payload = payload
    result.diagnostic = _NATIVE_STRUCTURED_DIAGNOSTIC_OK
    result._event = None
    result._event_kind = EVENT_KIND_RESULT_PUSHED
    result._event_connection = connection
    result._event_session = session
    result._event_operation_kind = int(raw_operation.kind)
    result._event_operation_id = operation_id
    result._event_operation_generation = int(raw_operation.generation)
    result._event_operation_flags = int(raw_operation.flags)
    result._event_diagnostic = _NATIVE_RUNTIME_DIAGNOSTIC_OK
    return result


def _submit_result_from_cffi_api_result(
    compact: Any,
    *,
    connection: NativeHandle,
    session: NativeHandle,
    state: NativeOperationLifecycle | str | None,
    result_payload: bytes | bytearray | memoryview,
) -> NativeRuntimeResult:
    status = _status_from_cffi_api_result(compact)
    diagnostic = (
        _NATIVE_RUNTIME_DIAGNOSTIC_OK
        if status is _NATIVE_STATUS_OK
        else NativeRuntimeDiagnostic(
            status=status,
            related_connection_id=0,
            related_session_id=0,
            related_operation_id=int(compact.operation_id),
            related_frame_id=int(compact.frame_id),
        )
    )
    structured_diagnostic = (
        _NATIVE_STRUCTURED_DIAGNOSTIC_OK
        if diagnostic is _NATIVE_RUNTIME_DIAGNOSTIC_OK
        else NativeStructuredDiagnostic.from_runtime_diagnostic(diagnostic)
    )
    operation_id = int(compact.operation_id)
    frame_id = int(compact.frame_id)
    payload = _cffi_api_result_payload(int(compact.payload_len), result_payload)
    event_kind = int(compact.event_kind)
    return NativeRuntimeResult(
        _compact_result_state(compact, status=status, state=state),
        operation_id,
        frame_id,
        payload,
        None,
        structured_diagnostic,
        event_kind=event_kind,
        event_connection=connection,
        event_session=session,
        event_operation_kind=HANDLE_KIND_OPERATION,
        event_operation_id=operation_id,
        event_operation_generation=0,
        event_operation_flags=0,
        event_diagnostic=diagnostic,
    )


def _compact_result_payload(view: _NnrpBufferView, result_payload: bytes | bytearray | memoryview) -> bytes:
    if isinstance(result_payload, bytes) and int(view.len) == len(result_payload):
        return result_payload
    return _copy_buffer_view(view)


def _cffi_api_result_payload(length: int, result_payload: bytes | bytearray | memoryview) -> bytes:
    if isinstance(result_payload, bytes) and length == len(result_payload):
        return result_payload
    view = memoryview(result_payload)
    if length == view.nbytes:
        return view.tobytes()
    return view[:length].tobytes()


def _status_from_cffi_api_result(compact: Any) -> NativeStatus:
    if (
        int(compact.status_code) == FFI_STATUS_OK
        and int(compact.error_family) == ERROR_FAMILY_NONE
        and int(compact.protocol_error_code) == 0
        and int(compact.detail_code) == 0
    ):
        return _NATIVE_STATUS_OK
    return NativeStatus(
        int(compact.status_code),
        int(compact.error_family),
        int(compact.protocol_error_code),
        int(compact.detail_code),
    )


def _compact_result_state(
    compact: _NnrpCompactResult,
    *,
    status: NativeStatus,
    state: NativeOperationLifecycle | str | None,
) -> NativeOperationLifecycle:
    if state is not None:
        return NativeOperationLifecycle(state)
    try:
        return _RESULT_STATE_LIFECYCLE[int(compact.result_state)]
    except KeyError:
        if status.status_code == FFI_STATUS_OK and int(compact.event_kind) != EVENT_KIND_ERROR:
            return NativeOperationLifecycle.COMPLETED
        return NativeOperationLifecycle.FAILED


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

    def cancel(self) -> None:
        request = _NnrpClientCancelRequest(self.session.to_ffi(), self.frame_id)
        status = self.entrypoints.client_cancel(request)
        raise_for_native_status(status)

    def complete(self, payload: bytes | bytearray | memoryview = b"") -> None:
        payload_view, _payload_owner = _buffer_view_from_payload(payload)
        request = _NnrpClientCompleteOperationRequest(self.handle.to_ffi(), payload_view)
        status = self.entrypoints.client_complete_operation(request)
        raise_for_native_status(status)

    def drop(self) -> None:
        request = _NnrpClientDropOperationRequest(self.handle.to_ffi())
        status = self.entrypoints.client_drop_operation(request)
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
        converted = _cache_lease_result_from_ffi(result, owner_session_id=self.owner.id)
        if result.lease_handle.kind == HANDLE_KIND_CACHE_LEASE:
            self._lease_handles[_cache_identity_key(converted.identity)] = NativeCacheLeaseHandle.from_ffi(
                result.lease_handle
            )
        return converted


@runtime_checkable
class NativeRuntimeBackend(Protocol):
    def connect(self, *, connection_id: int, generation: int, transport_id: int) -> NativeRuntimeConnection: ...

    def bootstrap_connection(
        self,
        *,
        connection_id: int,
        generation: int,
        transport_id: int,
    ) -> NativeRuntimeConnection: ...


@dataclass(frozen=True)
class NativeRuntimeClient:
    entrypoints: NativeRuntimeEntrypoints

    def connect(self, *, connection_id: int, generation: int, transport_id: int) -> NativeRuntimeConnection:
        request = _NnrpClientConnectRequest(connection_id, generation, transport_id)
        out_connection = _NnrpHandle()
        status = self.entrypoints.client_connect(request, ctypes.byref(out_connection))
        raise_for_native_status(status)
        return NativeRuntimeConnection(self.entrypoints, NativeConnectionHandle.from_ffi(out_connection))

    def bootstrap_connection(
        self,
        *,
        connection_id: int,
        generation: int,
        transport_id: int,
    ) -> NativeRuntimeConnection:
        request = _NnrpConnectionBootstrap(connection_id, generation, transport_id)
        out_connection = _NnrpHandle()
        status = self.entrypoints.connection_bootstrap(request, ctypes.byref(out_connection))
        raise_for_native_status(status)
        return NativeRuntimeConnection(self.entrypoints, NativeConnectionHandle.from_ffi(out_connection))

    def bind_server(self, *, server_id: int, generation: int, transport_id: int) -> NativeRuntimeServer:
        request = _NnrpServerBindRequest(server_id, generation, transport_id)
        out_server = _NnrpHandle()
        status = self.entrypoints.server_bind(request, ctypes.byref(out_server))
        raise_for_native_status(status)
        return NativeRuntimeServer(self.entrypoints, NativeConnectionHandle.from_ffi(out_server))


@dataclass(frozen=True)
class NativeRuntimeServer:
    entrypoints: NativeRuntimeEntrypoints
    handle: NativeConnectionHandle
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    def accept_session(
        self,
        *,
        session_id: int,
        generation: int,
        profile_id: int,
        schema_id: int,
        schema_version: int,
    ) -> NativeRuntimeServerSession:
        self._ensure_open()
        request = _NnrpServerAcceptRequest(
            self.handle.to_ffi(),
            session_id,
            generation,
            profile_id,
            schema_id,
            schema_version,
        )
        out_session = _NnrpHandle()
        status = self.entrypoints.server_accept(request, ctypes.byref(out_session))
        raise_for_native_status(status)
        return NativeRuntimeServerSession(
            self.entrypoints,
            self.handle,
            NativeSessionHandle.from_ffi(out_session),
        )

    def close(self) -> None:
        self._ensure_open()
        status = self.entrypoints.client_close_connection(self.handle.to_ffi())
        raise_for_native_status(status)
        object.__setattr__(self, "_closed", True)

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
        generation: int,
        profile_id: int,
        schema_id: int,
        schema_version: int,
        priority_class: NativeSessionPriorityClass | str = NativeSessionPriorityClass.BALANCED,
    ) -> NativeRuntimeSession:
        self._ensure_open()
        selected_priority_class = NativeSessionPriorityClass(priority_class)
        request = _NnrpSessionOpenRequest(
            self.handle.to_ffi(),
            requested_session_id,
            generation,
            profile_id,
            schema_id,
            schema_version,
        )
        out_session = _NnrpHandle()
        status = self.entrypoints.client_open_session(request, ctypes.byref(out_session))
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
        requested_session_id: int,
        generation: int,
        profile_id: int,
        schema_id: int,
        schema_version: int,
        resume_token_bytes: int,
        priority_class: NativeSessionPriorityClass | str = NativeSessionPriorityClass.BALANCED,
    ) -> tuple[NativeRuntimeSession, NativeSessionRecoveryOutcome]:
        self._ensure_open()
        selected_priority_class = NativeSessionPriorityClass(priority_class)
        request = _NnrpSessionResumeRequest(
            self.handle.to_ffi(),
            requested_session_id,
            generation,
            profile_id,
            schema_id,
            schema_version,
            resume_token_bytes,
        )
        out_session = _NnrpHandle()
        out_outcome = _NnrpSessionRecoveryOutcome()
        status = self.entrypoints.client_resume_session(request, ctypes.byref(out_session), ctypes.byref(out_outcome))
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

    def await_event(self) -> NativeRuntimePollResult:
        self._ensure_open()
        result = _NnrpPollResult()
        status = self.entrypoints.client_await_event(self.handle.to_ffi(), ctypes.byref(result))
        raise_for_native_status(status)
        raise_for_native_status(result.status)
        return NativeRuntimePollResult.from_ffi(result)

    def poll_event(self) -> NativeRuntimeEvent | None:
        return self.await_event().event

    def poll_events(
        self,
        *,
        max_events: int | None = None,
        event_kind: int | None = None,
    ) -> tuple[NativeRuntimeEvent, ...]:
        if max_events is not None:
            return self.poll_events_batch(max_events=max_events, event_kind=event_kind)

        if event_kind is not None:
            _validate_u32("event_kind", event_kind)

        events: list[NativeRuntimeEvent] = []
        while True:
            event = self.poll_event()
            if event is None:
                break
            if event_kind is not None and event.kind != event_kind:
                continue
            events.append(event)
        return tuple(events)

    def poll_events_batch(
        self,
        *,
        max_events: int,
        event_kind: int | None = None,
    ) -> tuple[NativeRuntimeEvent, ...]:
        self._ensure_open()
        if max_events is not None and max_events < 0:
            raise ValueError("max_events must be non-negative")
        if max_events == 0:
            return ()
        if event_kind is not None:
            _validate_u32("event_kind", event_kind)

        event_buffer = (_NnrpEvent * max_events)()
        event_count = ctypes.c_size_t()
        status = self.entrypoints.client_await_events(
            self.handle.to_ffi(),
            event_buffer,
            max_events,
            ctypes.byref(event_count),
        )
        native_status = NativeStatus.from_ffi(status)
        if native_status.status_code == FFI_STATUS_WOULD_BLOCK:
            return ()
        raise_for_native_status(native_status)

        events: list[NativeRuntimeEvent] = []
        for index in range(int(event_count.value)):
            event = NativeRuntimeEvent.from_ffi(event_buffer[index])
            if event_kind is not None and event.kind != event_kind:
                continue
            events.append(event)
        return tuple(events)

    def poll_credit_updates(self, *, max_events: int | None = None) -> tuple[NativeCreditUpdateEvent, ...]:
        return tuple(
            event.to_credit_update()
            for event in self.poll_events(max_events=max_events, event_kind=EVENT_KIND_FLOW_UPDATED)
        )

    def poll_result_hints(self, *, max_events: int | None = None) -> tuple[NativeResultHintEvent, ...]:
        return tuple(
            NativeResultHintEvent.from_event(event)
            for event in self.poll_events(max_events=max_events, event_kind=EVENT_KIND_RESULT_HINT)
        )

    def poll_payload_family_events(
        self,
        payload_family: str,
        *,
        max_events: int | None = None,
        event_kind: int = EVENT_KIND_RESULT_PUSHED,
    ) -> tuple[NativePayloadFamilyEvent, ...]:
        return tuple(
            NativePayloadFamilyEvent.from_event(event, payload_family=payload_family)
            for event in self.poll_events(max_events=max_events, event_kind=event_kind)
        )

    def poll_structured_events(self, *, max_events: int | None = None) -> tuple[NativePayloadFamilyEvent, ...]:
        return self.poll_payload_family_events("structured_event", max_events=max_events)

    def poll_tool_deltas(self, *, max_events: int | None = None) -> tuple[NativePayloadFamilyEvent, ...]:
        return self.poll_payload_family_events("tool_delta", max_events=max_events)

    def poll_workflow_states(self, *, max_events: int | None = None) -> tuple[NativePayloadFamilyEvent, ...]:
        return self.poll_payload_family_events("workflow_state", max_events=max_events)

    def dispatch_events(
        self,
        callback: NativeRuntimeEventCallback,
        *,
        max_events: int | None = None,
        event_kind: int | None = None,
    ) -> int:
        return _dispatch_callback_batch(
            self.poll_events(max_events=max_events, event_kind=event_kind),
            callback,
        )

    def dispatch_credit_updates(
        self,
        callback: NativeCreditUpdateCallback,
        *,
        max_events: int | None = None,
    ) -> int:
        return _dispatch_callback_batch(self.poll_credit_updates(max_events=max_events), callback)

    def dispatch_result_hints(
        self,
        callback: NativeResultHintCallback,
        *,
        max_events: int | None = None,
    ) -> int:
        return _dispatch_callback_batch(self.poll_result_hints(max_events=max_events), callback)

    def dispatch_payload_family_events(
        self,
        payload_family: str,
        callback: NativePayloadFamilyCallback,
        *,
        max_events: int | None = None,
        event_kind: int = EVENT_KIND_RESULT_PUSHED,
    ) -> int:
        return _dispatch_callback_batch(
            self.poll_payload_family_events(payload_family, max_events=max_events, event_kind=event_kind),
            callback,
        )

    def dispatch_structured_events(
        self,
        callback: NativePayloadFamilyCallback,
        *,
        max_events: int | None = None,
    ) -> int:
        return self.dispatch_payload_family_events("structured_event", callback, max_events=max_events)

    def dispatch_tool_deltas(
        self,
        callback: NativePayloadFamilyCallback,
        *,
        max_events: int | None = None,
    ) -> int:
        return self.dispatch_payload_family_events("tool_delta", callback, max_events=max_events)

    def dispatch_workflow_states(
        self,
        callback: NativePayloadFamilyCallback,
        *,
        max_events: int | None = None,
    ) -> int:
        return self.dispatch_payload_family_events("workflow_state", callback, max_events=max_events)

    async def async_poll_event(self) -> NativeRuntimeEvent | None:
        return await asyncio.to_thread(self.poll_event)

    async def iter_events(
        self,
        *,
        max_events: int | None = None,
        event_kind: int | None = None,
    ) -> AsyncIterator[NativeRuntimeEvent]:
        for event in await asyncio.to_thread(self.poll_events, max_events=max_events, event_kind=event_kind):
            yield event

    async def iter_credit_updates(self, *, max_events: int | None = None) -> AsyncIterator[NativeCreditUpdateEvent]:
        for update in await asyncio.to_thread(self.poll_credit_updates, max_events=max_events):
            yield update

    async def iter_result_hints(self, *, max_events: int | None = None) -> AsyncIterator[NativeResultHintEvent]:
        for hint in await asyncio.to_thread(self.poll_result_hints, max_events=max_events):
            yield hint

    async def iter_payload_family_events(
        self,
        payload_family: str,
        *,
        max_events: int | None = None,
        event_kind: int = EVENT_KIND_RESULT_PUSHED,
    ) -> AsyncIterator[NativePayloadFamilyEvent]:
        events = await asyncio.to_thread(
            self.poll_payload_family_events,
            payload_family,
            max_events=max_events,
            event_kind=event_kind,
        )
        for event in events:
            yield event

    async def iter_structured_events(self, *, max_events: int | None = None) -> AsyncIterator[NativePayloadFamilyEvent]:
        async for event in self.iter_payload_family_events("structured_event", max_events=max_events):
            yield event

    async def iter_tool_deltas(self, *, max_events: int | None = None) -> AsyncIterator[NativePayloadFamilyEvent]:
        async for event in self.iter_payload_family_events("tool_delta", max_events=max_events):
            yield event

    async def iter_workflow_states(self, *, max_events: int | None = None) -> AsyncIterator[NativePayloadFamilyEvent]:
        async for event in self.iter_payload_family_events("workflow_state", max_events=max_events):
            yield event

    def control(self, *, control_code: int, payload: bytes | bytearray | memoryview = b"") -> None:
        self._ensure_open()
        payload_view, _payload_owner = _buffer_view_from_payload(payload)
        request = _NnrpControlRequest(self.handle.to_ffi(), control_code, payload_view)
        status = self.entrypoints.control(request)
        raise_for_native_status(status)

    def close(self) -> None:
        self._ensure_open()
        status = self.entrypoints.client_close_connection(self.handle.to_ffi())
        raise_for_native_status(status)
        object.__setattr__(self, "_closed", True)

    def _ensure_open(self) -> None:
        if self._closed:
            raise NativeInvalidStateError(NativeStatus(FFI_STATUS_INVALID_STATE), "native runtime connection is closed")


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
    _submit_result_request: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_out_operation: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_poll_result: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_compact_result: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_out_operation_ref: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_poll_result_ref: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_compact_result_ref: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_session_handle: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_payload_cache: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_payload_view: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_payload_owner: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_result_payload_cache: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_result_payload_view: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_result_payload_owner: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_client_submit_result: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_client_submit_result_compact: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_max_events: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_connection_handle: Any = field(default=None, init=False, repr=False, compare=False)
    _submit_result_native_session_handle: Any = field(default=None, init=False, repr=False, compare=False)
    _cffi_submit_result_out: Any = field(default=None, init=False, repr=False, compare=False)
    _cffi_submit_result_payload_cache: Any = field(default=None, init=False, repr=False, compare=False)
    _cffi_submit_result_payload_view: Any = field(default=None, init=False, repr=False, compare=False)

    def submit(
        self,
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview = b"",
    ) -> NativeOperationHandle:
        self._ensure_open()
        return self.submit_operation(operation_id=operation_id, frame_id=frame_id, payload=payload).handle

    def submit_operation(
        self,
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview = b"",
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
        scheduling_hint: NativeOperationSchedulingHint | None = None,
    ) -> NativeRuntimeOperation:
        self._ensure_open()
        selected_scheduling_hint = _coerce_operation_scheduling_hint(
            scheduling_hint,
            parent_operation_id=parent_operation_id,
            operation_group_id=operation_group_id,
        )
        payload_view, _payload_owner = _buffer_view_from_payload(payload)
        request = _NnrpSubmitRequest(self.handle.to_ffi(), operation_id, frame_id, payload_view)
        out_operation = _NnrpHandle()
        status = self.entrypoints.client_submit(request, ctypes.byref(out_operation))
        raise_for_native_status(status)
        return NativeRuntimeOperation(
            entrypoints=self.entrypoints,
            session=self.handle,
            handle=NativeOperationHandle.from_ffi(out_operation),
            operation_id=operation_id,
            frame_id=frame_id,
            scheduling_hint=selected_scheduling_hint,
            parent_operation_id=selected_scheduling_hint.parent_operation_id,
            operation_group_id=selected_scheduling_hint.operation_group_id,
        )

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
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview = b"",
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
        scheduling_hint: NativeOperationSchedulingHint | None = None,
    ) -> NativeRuntimeOperation:
        try:
            return await asyncio.to_thread(
                self.submit_operation,
                operation_id=operation_id,
                frame_id=frame_id,
                payload=payload,
                parent_operation_id=parent_operation_id,
                operation_group_id=operation_group_id,
                scheduling_hint=scheduling_hint,
            )
        except asyncio.CancelledError:
            self.cancel(frame_id=frame_id)
            raise

    def poll_result(
        self,
        operation: NativeRuntimeOperation,
        *,
        state: NativeOperationLifecycle | str | None = None,
        max_events: int | None = None,
    ) -> NativeRuntimeResult:
        self._ensure_open()
        if max_events is not None and max_events < 0:
            raise ValueError("max_events must be non-negative")
        if max_events is not None and max_events > 1:
            result = self._poll_result_batch(operation, state=state, max_events=max_events)
            if result is not None:
                return result
            raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))

        seen_events = 0
        while max_events is None or seen_events < max_events:
            result = _NnrpPollResult()
            status = self.entrypoints.client_await_event(self.connection.to_ffi(), ctypes.byref(result))
            raise_for_native_status(status)
            raise_for_native_status(result.status)
            poll_result = NativeRuntimePollResult.from_ffi(result)
            event = poll_result.event
            if event is None:
                break
            seen_events += 1
            if _event_is_result_event(event) and _event_matches_operation(event, operation):
                return NativeRuntimeResult.from_event(event, state=state)

        raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))

    def _poll_result_batch(
        self,
        operation: NativeRuntimeOperation,
        *,
        state: NativeOperationLifecycle | str | None,
        max_events: int,
    ) -> NativeRuntimeResult | None:
        event_buffer, event_count = self._borrow_poll_event_buffer(max_events)
        status = self.entrypoints.client_await_events(
            self.connection.to_ffi(),
            event_buffer,
            max_events,
            ctypes.byref(event_count),
        )
        native_status = NativeStatus.from_ffi(status)
        if native_status.status_code == FFI_STATUS_WOULD_BLOCK:
            return None
        raise_for_native_status(native_status)

        for index in range(int(event_count.value)):
            raw_event = event_buffer[index]
            if not _raw_event_is_result_event(raw_event) or not _raw_event_matches_operation(raw_event, operation):
                continue
            event = NativeRuntimeEvent.from_ffi(raw_event)
            return NativeRuntimeResult.from_event(event, state=state)
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

    def complete_operation(
        self,
        operation: NativeRuntimeOperation,
        payload: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._ensure_open()
        operation.complete(payload)

    def drop_operation(self, operation: NativeRuntimeOperation) -> None:
        self._ensure_open()
        operation.drop()

    def submit_result(
        self,
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview = b"",
        result_payload: bytes | bytearray | memoryview | None = None,
        state: NativeOperationLifecycle | str | None = None,
        max_events: int | None = None,
    ) -> NativeRuntimeResult:
        self._ensure_open()
        if max_events is not None and max_events < 0:
            raise ValueError("max_events must be non-negative")
        if isinstance(payload, bytes) and payload is self._submit_result_payload_cache:
            submit_payload_view = self._submit_result_payload_view
            _submit_payload_owner = self._submit_result_payload_owner
            assign_submit_payload = False
        else:
            submit_payload_view, _submit_payload_owner = _buffer_view_from_payload(payload)
            assign_submit_payload = True
            if isinstance(payload, bytes):
                object.__setattr__(self, "_submit_result_payload_cache", payload)
                object.__setattr__(self, "_submit_result_payload_view", submit_payload_view)
                object.__setattr__(self, "_submit_result_payload_owner", _submit_payload_owner)
        selected_result_payload = payload if result_payload is None else result_payload
        if selected_result_payload is payload:
            result_payload_view = submit_payload_view
            _result_payload_owner = _submit_payload_owner
            assign_result_payload = selected_result_payload is not self._submit_result_result_payload_cache
            if assign_result_payload and isinstance(selected_result_payload, bytes):
                object.__setattr__(self, "_submit_result_result_payload_cache", selected_result_payload)
                object.__setattr__(self, "_submit_result_result_payload_view", result_payload_view)
                object.__setattr__(self, "_submit_result_result_payload_owner", _result_payload_owner)
        elif (
            isinstance(selected_result_payload, bytes)
            and selected_result_payload is self._submit_result_result_payload_cache
        ):
            result_payload_view = self._submit_result_result_payload_view
            _result_payload_owner = self._submit_result_result_payload_owner
            assign_result_payload = False
        else:
            result_payload_view, _result_payload_owner = _buffer_view_from_payload(selected_result_payload)
            assign_result_payload = True
            if isinstance(selected_result_payload, bytes):
                object.__setattr__(self, "_submit_result_result_payload_cache", selected_result_payload)
                object.__setattr__(self, "_submit_result_result_payload_view", result_payload_view)
                object.__setattr__(self, "_submit_result_result_payload_owner", _result_payload_owner)
        request = self._submit_result_request
        assign_static_request_fields = request is None
        if request is None:
            request = _NnrpClientSubmitResultRequest()
            out_operation = _NnrpHandle()
            poll_result = _NnrpPollResult()
            compact_result = _NnrpCompactResult()
            object.__setattr__(self, "_submit_result_request", request)
            object.__setattr__(self, "_submit_result_out_operation", out_operation)
            object.__setattr__(self, "_submit_result_poll_result", poll_result)
            object.__setattr__(self, "_submit_result_compact_result", compact_result)
            object.__setattr__(self, "_submit_result_out_operation_ref", ctypes.byref(out_operation))
            object.__setattr__(self, "_submit_result_poll_result_ref", ctypes.byref(poll_result))
            object.__setattr__(self, "_submit_result_compact_result_ref", ctypes.byref(compact_result))
            object.__setattr__(self, "_submit_result_session_handle", self.handle.to_ffi())
            object.__setattr__(self, "_submit_result_client_submit_result", self.entrypoints.client_submit_result)
            object.__setattr__(
                self,
                "_submit_result_client_submit_result_compact",
                self.entrypoints.client_submit_result_compact,
            )
            object.__setattr__(self, "_submit_result_connection_handle", self.connection.handle)
            object.__setattr__(self, "_submit_result_native_session_handle", self.handle.handle)
        selected_max_events = 0 if max_events is None else max_events
        if assign_static_request_fields:
            request.session = self._submit_result_session_handle
        if assign_submit_payload:
            request.submit_payload = submit_payload_view
        if assign_result_payload:
            request.result_payload = result_payload_view
        if selected_max_events != self._submit_result_max_events:
            request.max_events = selected_max_events
            object.__setattr__(self, "_submit_result_max_events", selected_max_events)
        request.operation_id = operation_id
        request.frame_id = frame_id
        cffi_api = self.entrypoints.cffi_submit_result_api
        if cffi_api is not None:
            cffi_result = self._try_submit_result_cffi_api(
                cffi_api=cffi_api,
                operation_id=operation_id,
                frame_id=frame_id,
                payload=payload,
                selected_result_payload=selected_result_payload,
                state=state,
                max_events=selected_max_events,
            )
            if cffi_result is not None:
                return cffi_result
        compact_result = self._submit_result_compact_result
        status = self._submit_result_client_submit_result_compact(
            request,
            self._submit_result_compact_result_ref,
        )
        if status.status_code != FFI_STATUS_OK:
            _raise_for_native_ffi_status(status)
        compact_status = compact_result.status
        if compact_status.status_code != FFI_STATUS_OK:
            _raise_for_native_ffi_status(compact_status)
        if not compact_result.has_result:
            raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))
        raw_diagnostic = compact_result.diagnostic
        raw_diagnostic_status = raw_diagnostic.status
        if (
            state is None
            and compact_result.event_kind == EVENT_KIND_RESULT_PUSHED
            and compact_result.result_state == RESULT_STATE_COMPLETED
            and raw_diagnostic_status.status_code == FFI_STATUS_OK
            and raw_diagnostic_status.error_family == ERROR_FAMILY_NONE
            and raw_diagnostic_status.protocol_error_code == 0
            and raw_diagnostic_status.detail_code == 0
            and raw_diagnostic.related_connection_id == 0
            and raw_diagnostic.related_session_id == 0
            and raw_diagnostic.related_operation_id == 0
            and raw_diagnostic.related_frame_id == 0
        ):
            return _submit_result_from_ok_compact_ffi_result(
                compact_result,
                connection=self._submit_result_connection_handle,
                session=self._submit_result_native_session_handle,
                result_payload=selected_result_payload,
            )
        return _submit_result_from_compact_ffi_result(
            compact_result,
            connection=self._submit_result_connection_handle,
            session=self._submit_result_native_session_handle,
            state=state,
            result_payload=selected_result_payload,
        )
        poll_result = self._submit_result_poll_result
        status = self._submit_result_client_submit_result(
            request,
            self._submit_result_out_operation_ref,
            self._submit_result_poll_result_ref,
        )
        if status.status_code != FFI_STATUS_OK:
            _raise_for_native_ffi_status(status)
        poll_status = poll_result.status
        if poll_status.status_code != FFI_STATUS_OK:
            _raise_for_native_ffi_status(poll_status)
        if not poll_result.has_event:
            raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))
        raw_event = poll_result.event
        raw_diagnostic = raw_event.diagnostic
        raw_status = raw_diagnostic.status
        if (
            state is None
            and raw_event.kind == EVENT_KIND_RESULT_PUSHED
            and raw_status.status_code == FFI_STATUS_OK
            and raw_status.error_family == ERROR_FAMILY_NONE
            and raw_status.protocol_error_code == 0
            and raw_status.detail_code == 0
            and raw_diagnostic.related_connection_id == 0
            and raw_diagnostic.related_session_id == 0
            and raw_diagnostic.related_operation_id == 0
            and raw_diagnostic.related_frame_id == 0
        ):
            return _submit_result_from_ok_result_pushed_ffi_event(
                raw_event,
                connection=self._submit_result_connection_handle,
                session=self._submit_result_native_session_handle,
            )
        return _submit_result_from_ffi_event(
            raw_event,
            connection=self._submit_result_connection_handle,
            session=self._submit_result_native_session_handle,
            state=state,
        )

    def _try_submit_result_cffi_api(
        self,
        *,
        cffi_api: _NativeCffiSubmitResultApi,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview,
        selected_result_payload: bytes | bytearray | memoryview,
        state: NativeOperationLifecycle | str | None,
        max_events: int,
    ) -> NativeRuntimeResult | None:
        if selected_result_payload is not payload:
            return None

        if self._cffi_submit_result_out is None:
            object.__setattr__(
                self,
                "_cffi_submit_result_out",
                cffi_api.ffi.new("NnrpPyCompactResult *"),
            )

        if isinstance(payload, bytes) and payload is self._cffi_submit_result_payload_cache:
            payload_view = self._cffi_submit_result_payload_view
        else:
            payload_view = cffi_api.ffi.from_buffer(payload)
            if isinstance(payload, bytes):
                object.__setattr__(self, "_cffi_submit_result_payload_cache", payload)
                object.__setattr__(self, "_cffi_submit_result_payload_view", payload_view)

        wrapper_status = cffi_api.submit_result_compact(
            session=self.handle.handle,
            operation_id=operation_id,
            frame_id=frame_id,
            payload_view=payload_view,
            payload_len=memoryview(payload).nbytes,
            max_events=max_events,
            out_result=self._cffi_submit_result_out,
        )
        if wrapper_status is None:
            return None
        if wrapper_status != 0:
            raise NativeInternalError(
                NativeStatus(FFI_STATUS_INTERNAL_ERROR),
                f"native cffi API submit/result wrapper failed: {wrapper_status}",
            )

        result = self._cffi_submit_result_out
        status = _status_from_cffi_api_result(result)
        raise_for_native_status(status)
        if not result.has_result:
            raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))
        return _submit_result_from_cffi_api_result(
            result,
            connection=self._submit_result_connection_handle,
            session=self._submit_result_native_session_handle,
            state=state,
            result_payload=selected_result_payload,
        )

    def submit_and_poll_result(
        self,
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview = b"",
        result_payload: bytes | bytearray | memoryview | None = None,
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
        scheduling_hint: NativeOperationSchedulingHint | None = None,
        state: NativeOperationLifecycle | str | None = None,
        max_events: int | None = None,
    ) -> NativeRuntimeResult:
        if parent_operation_id is None and operation_group_id is None and scheduling_hint is None:
            return self.submit_result(
                operation_id=operation_id,
                frame_id=frame_id,
                payload=payload,
                result_payload=result_payload,
                state=state,
                max_events=max_events,
            )
        operation = self.submit_operation(
            operation_id=operation_id,
            frame_id=frame_id,
            payload=payload,
            parent_operation_id=parent_operation_id,
            operation_group_id=operation_group_id,
            scheduling_hint=scheduling_hint,
        )
        return self.poll_result(operation, state=state, max_events=max_events)

    async def async_submit_and_poll_result(
        self,
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview = b"",
        result_payload: bytes | bytearray | memoryview | None = None,
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
        scheduling_hint: NativeOperationSchedulingHint | None = None,
        state: NativeOperationLifecycle | str | None = None,
        max_events: int | None = None,
    ) -> NativeRuntimeResult:
        return await asyncio.to_thread(
            self.submit_and_poll_result,
            operation_id=operation_id,
            frame_id=frame_id,
            payload=payload,
            result_payload=result_payload,
            parent_operation_id=parent_operation_id,
            operation_group_id=operation_group_id,
            scheduling_hint=scheduling_hint,
            state=state,
            max_events=max_events,
        )

    def close(self) -> None:
        self._ensure_open()
        status = self.entrypoints.client_close(self.handle.to_ffi())
        raise_for_native_status(status)
        object.__setattr__(self, "_closed", True)

    def cancel(self, *, frame_id: int) -> None:
        self._ensure_open()
        request = _NnrpClientCancelRequest(self.handle.to_ffi(), frame_id)
        status = self.entrypoints.client_cancel(request)
        raise_for_native_status(status)

    def send_flow_update(self, *, frame_id: int) -> None:
        self._ensure_open()
        request = _NnrpServerFlowUpdateRequest(self.handle.to_ffi(), frame_id)
        status = self.entrypoints.client_send_flow_update(request)
        raise_for_native_status(status)

    def send_result_hint(self, payload: bytes | bytearray | memoryview = b"") -> None:
        self._ensure_open()
        payload_view, _payload_owner = _buffer_view_from_payload(payload)
        request = _NnrpControlRequest(self.handle.to_ffi(), CONTROL_CODE_RESULT_HINT, payload_view)
        status = self.entrypoints.client_send_result_hint(request)
        raise_for_native_status(status)

    def control(self, *, control_code: int, payload: bytes | bytearray | memoryview = b"") -> None:
        self._ensure_open()
        payload_view, _payload_owner = _buffer_view_from_payload(payload)
        request = _NnrpControlRequest(self.handle.to_ffi(), control_code, payload_view)
        status = self.entrypoints.control(request)
        raise_for_native_status(status)

    def _ensure_open(self) -> None:
        if self._closed:
            raise NativeInvalidStateError(NativeStatus(FFI_STATUS_INVALID_STATE), "native runtime session is closed")


@dataclass(frozen=True)
class NativeRuntimeServerOperation:
    entrypoints: NativeRuntimeEntrypoints
    session: NativeSessionHandle
    handle: NativeOperationHandle
    operation_id: int
    frame_id: int

    def send_result(self, payload: bytes | bytearray | memoryview = b"") -> None:
        payload_view, _payload_owner = _buffer_view_from_payload(payload)
        request = _NnrpServerSendResultRequest(self.handle.to_ffi(), payload_view)
        status = self.entrypoints.server_send_result(request)
        raise_for_native_status(status)


@dataclass(frozen=True)
class NativeRuntimeServerSession:
    entrypoints: NativeRuntimeEntrypoints
    server: NativeConnectionHandle
    handle: NativeSessionHandle
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    def receive_submit(
        self,
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview = b"",
    ) -> NativeRuntimeServerOperation:
        self._ensure_open()
        payload_view, _payload_owner = _buffer_view_from_payload(payload)
        request = _NnrpServerReceiveSubmitRequest(self.handle.to_ffi(), operation_id, frame_id, payload_view)
        out_operation = _NnrpHandle()
        status = self.entrypoints.server_receive_submit(request, ctypes.byref(out_operation))
        raise_for_native_status(status)
        return NativeRuntimeServerOperation(
            self.entrypoints,
            self.handle,
            NativeOperationHandle.from_ffi(out_operation),
            operation_id,
            frame_id,
        )

    def send_flow_update(self, *, frame_id: int) -> None:
        self._ensure_open()
        request = _NnrpServerFlowUpdateRequest(self.handle.to_ffi(), frame_id)
        status = self.entrypoints.server_send_flow_update(request)
        raise_for_native_status(status)

    def control(self, *, control_code: int, payload: bytes | bytearray | memoryview = b"") -> None:
        self._ensure_open()
        payload_view, _payload_owner = _buffer_view_from_payload(payload)
        request = _NnrpControlRequest(self.handle.to_ffi(), control_code, payload_view)
        status = self.entrypoints.control(request)
        raise_for_native_status(status)

    def close(self) -> None:
        self._ensure_open()
        status = self.entrypoints.server_close(self.handle.to_ffi())
        raise_for_native_status(status)
        object.__setattr__(self, "_closed", True)

    def _ensure_open(self) -> None:
        if self._closed:
            raise NativeInvalidStateError(
                NativeStatus(FFI_STATUS_INVALID_STATE),
                "native runtime server session is closed",
            )


def _event_matches_operation(event: NativeRuntimeEvent, operation: NativeRuntimeOperation) -> bool:
    if event.session != operation.session.handle:
        return False
    return (
        event.operation.id == operation.handle.handle.id
        or event.operation.id == operation.operation_id
        or event.frame_id == operation.frame_id
    )


def _event_is_result_event(event: NativeRuntimeEvent) -> bool:
    return event.kind in {EVENT_KIND_RESULT_PUSHED, EVENT_KIND_RESULT_DROPPED, EVENT_KIND_ERROR}


def _raw_event_is_result_event(event: _NnrpEvent) -> bool:
    return int(event.kind) in {EVENT_KIND_RESULT_PUSHED, EVENT_KIND_RESULT_DROPPED, EVENT_KIND_ERROR}


def _raw_event_matches_operation(event: _NnrpEvent, operation: NativeRuntimeOperation) -> bool:
    session = operation.session.handle
    if (
        int(event.session.kind) != session.kind
        or int(event.session.id) != session.id
        or int(event.session.generation) != session.generation
        or int(event.session.flags) != session.flags
    ):
        return False
    return (
        int(event.operation.id) == operation.handle.handle.id
        or int(event.operation.id) == operation.operation_id
        or int(event.frame_id) == operation.frame_id
    )


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
    candidate_dirs = [platform_dir, *(platform_dir / scope for scope in NATIVE_TRANSPORT_SCOPES)]
    for candidate_dir in candidate_dirs:
        if not candidate_dir.is_dir():
            continue
        provider = _provider_from_artifact_dir(candidate_dir, selected_platform)
        if provider is not None:
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
    probe_samples: tuple[NativeTransportProbeSample, ...] | list[NativeTransportProbeSample] | None = None,
) -> NnrpEndpointSupport:
    endpoint = uri if isinstance(uri, NnrpEndpoint) else parse_nnrp_endpoint(uri)
    try:
        selection = select_native_transport_provider(
            policy,
            root=root,
            native_platform=native_platform,
            supported_transports=supported_transports,
            probe_samples=probe_samples,
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
    probe_samples: tuple[NativeTransportProbeSample, ...] | list[NativeTransportProbeSample] | None = None,
) -> NativeTransportSelection:
    resolved_policy = _normalize_native_transport_policy(policy)
    providers = discover_native_transport_providers(root, native_platform)
    supported = _normalize_supported_native_transports(supported_transports)
    candidates, rejected = _select_native_transport_candidates(providers, supported, resolved_policy)
    if probe_samples is not None:
        return _select_native_transport_provider_with_probe(
            resolved_policy,
            providers,
            candidates,
            rejected,
            tuple(probe_samples),
        )

    candidates.sort(
        key=lambda candidate: (
            _native_transport_preference_rank(resolved_policy, candidate[1]),
            candidate[0].name,
        )
    )
    if not candidates:
        raise _native_transport_selection_error(resolved_policy, tuple(rejected))
    selected_provider, selected_transport = candidates[0]
    diagnostic = (
        "single installed transport selected directly"
        if len(candidates) == 1 and not rejected and len(providers) == 1
        else "native transport selected by policy"
    )
    return NativeTransportSelection(
        selected_provider=selected_provider,
        selected_transport_name=selected_transport,
        selected_transport_id=NATIVE_TRANSPORT_ID_BY_NAME[selected_transport],
        policy=resolved_policy,
        available_providers=providers,
        rejected=tuple(rejected),
        diagnostic=diagnostic,
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


def _select_native_transport_provider_with_probe(
    policy: TransportPolicy,
    providers: tuple[NativeTransportProvider, ...],
    candidates: list[tuple[NativeTransportProvider, str]],
    rejected: list[NativeTransportRejection],
    probe_samples: tuple[NativeTransportProbeSample, ...],
) -> NativeTransportSelection:
    scored: list[NativeTransportProbeCandidate] = []
    for provider, transport_name in candidates:
        provider_samples = tuple(_matching_native_probe_samples(provider, transport_name, probe_samples))
        if not provider_samples:
            rejected.append(
                _native_transport_rejection(
                    provider,
                    transport_name,
                    "probe_missing",
                    "native transport probe sample is missing",
                )
            )
            continue
        probe_score = _score_native_transport_probe(provider, transport_name, provider_samples, policy)
        if probe_score.failure_rate >= 1.0:
            rejected.append(
                _native_transport_rejection(
                    provider,
                    transport_name,
                    "probe_failed",
                    "all native transport probe samples failed",
                )
            )
            continue
        scored.append(
            NativeTransportProbeCandidate(
                provider=provider,
                transport_name=transport_name,
                transport_id=NATIVE_TRANSPORT_ID_BY_NAME[transport_name],
                probe_score=probe_score,
            )
        )

    scored.sort(
        key=lambda candidate: (
            candidate.probe_score.score,
            _native_transport_preference_rank(policy, candidate.transport_name),
            candidate.provider.name,
        )
    )
    if not scored:
        raise _native_transport_selection_error(policy, tuple(rejected))
    selected = scored[0]
    return NativeTransportSelection(
        selected_provider=selected.provider,
        selected_transport_name=selected.transport_name,
        selected_transport_id=selected.transport_id,
        policy=policy,
        available_providers=providers,
        rejected=tuple(rejected),
        probe_candidates=tuple(scored),
        selected_probe_score=selected.probe_score,
        diagnostic="native transport selected by probe score",
    )


def _select_native_transport_candidates(
    providers: tuple[NativeTransportProvider, ...],
    supported_transports: frozenset[str],
    policy: TransportPolicy,
) -> tuple[list[tuple[NativeTransportProvider, str]], list[NativeTransportRejection]]:
    candidates: list[tuple[NativeTransportProvider, str]] = []
    rejected: list[NativeTransportRejection] = []
    for provider in providers:
        for transport_name in provider.transport_slots:
            if not _native_transport_policy_allows(policy, transport_name):
                rejected.append(
                    _native_transport_rejection(
                        provider,
                        transport_name,
                        "policy_disallowed",
                        "native transport was disallowed by transport policy",
                    )
                )
            elif transport_name not in supported_transports:
                rejected.append(
                    _native_transport_rejection(
                        provider,
                        transport_name,
                        "remote_unsupported",
                        "native transport was not declared by the remote endpoint",
                    )
                )
            else:
                candidates.append((provider, transport_name))
    return candidates, rejected


def _score_native_transport_probe(
    provider: NativeTransportProvider,
    transport_name: str,
    probe_samples: tuple[NativeTransportProbeSample, ...],
    policy: TransportPolicy,
) -> NativeTransportProbeScore:
    failure_count = sum(1 for sample in probe_samples if sample.failed or sample.timed_out or sample.rtt_us is None)
    sample_count = len(probe_samples)
    failure_rate = failure_count / sample_count
    rtts = sorted(sample.rtt_us for sample in probe_samples if sample.rtt_us is not None)
    median_rtt_us = rtts[len(rtts) // 2] if rtts else 10_000_000
    elapsed_us = sum(sample.elapsed_us for sample in probe_samples)
    transferred = sum(sample.bytes_sent + sample.bytes_received for sample in probe_samples)
    throughput_bytes_per_sec = transferred * 1_000_000 // elapsed_us if elapsed_us else 0
    throughput_bonus = min(throughput_bytes_per_sec // 1_000, 500)
    policy_penalty = _native_transport_preference_rank(policy, transport_name) * 1_000.0
    score = median_rtt_us + failure_rate * 10_000_000.0 + policy_penalty - throughput_bonus
    return NativeTransportProbeScore(
        sample_count=sample_count,
        failure_count=failure_count,
        failure_rate=failure_rate,
        median_rtt_us=median_rtt_us,
        throughput_bytes_per_sec=throughput_bytes_per_sec,
        score=score,
    )


def _matching_native_probe_samples(
    provider: NativeTransportProvider,
    transport_name: str,
    samples: tuple[NativeTransportProbeSample, ...],
) -> tuple[NativeTransportProbeSample, ...]:
    return tuple(
        sample
        for sample in samples
        if sample.provider_name == provider.name
        and _normalize_native_transport_scope(sample.transport_name) == transport_name
    )


def _native_transport_selection_error(
    policy: TransportPolicy,
    rejected: tuple[NativeTransportRejection, ...],
) -> NativeArtifactError:
    forced_transport = _forced_native_transport_name(policy)
    if forced_transport is not None:
        return NativeArtifactError(f"forced native transport is not available: {forced_transport}")
    if rejected:
        reasons = ", ".join(f"{entry.provider_name}/{entry.transport_name}:{entry.reason}" for entry in rejected)
        return NativeArtifactError(f"no viable native transport provider after applying policy: {reasons}")
    return NativeArtifactError("no native transport providers are advertised by the native artifact")


def _native_transport_rejection(
    provider: NativeTransportProvider,
    transport_name: str,
    reason: str,
    diagnostic: str,
) -> NativeTransportRejection:
    return NativeTransportRejection(
        provider_name=provider.name,
        transport_name=transport_name,
        transport_id=NATIVE_TRANSPORT_ID_BY_NAME[transport_name],
        reason=reason,
        diagnostic=diagnostic,
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


def _native_transport_preference_rank(policy: TransportPolicy, transport_name: str) -> int:
    if policy is TransportPolicy.AUTO:
        return {"ipc": 0, "quic": 1, "tcp": 2, "websocket": 3}[transport_name]
    if policy is TransportPolicy.PREFER_QUIC:
        return {"quic": 0, "tcp": 1, "ipc": 2, "websocket": 2}[transport_name]
    if policy is TransportPolicy.PREFER_TCP:
        return {"tcp": 0, "quic": 1, "ipc": 2, "websocket": 2}[transport_name]
    if policy is TransportPolicy.PREFER_IPC:
        return 0 if transport_name == "ipc" else 1
    if policy is TransportPolicy.PREFER_WEBSOCKET:
        return 0 if transport_name == "websocket" else 1
    forced_transport = _forced_native_transport_name(policy)
    return 0 if forced_transport == transport_name else 255


def load_native_library(artifact_path: Path | str) -> ctypes.CDLL:
    try:
        return ctypes.CDLL(str(artifact_path))
    except OSError as error:
        raise NativeArtifactError(f"failed to load native artifact {artifact_path}: {error}") from error


def _load_native_cffi_submit_result_api(artifact_path: Path) -> _NativeCffiSubmitResultApi | None:
    mode = os.environ.get(NATIVE_BINDING_MODE_ENV, "auto").strip().lower().replace("-", "_")
    if mode in {"", "auto"}:
        required = False
    elif mode in {"ctypes", "ctypes_abi"}:
        return None
    elif mode in {"cffi", "cffi_api"}:
        required = True
    else:
        raise NativeArtifactError(f"unsupported native binding mode: {mode}")

    try:
        try:
            module = importlib.import_module("nnrp._nnrp_cffi_api_submit_result")
        except ImportError:
            module = importlib.import_module("_nnrp_cffi_api_submit_result")
        ffi = module.ffi
        library = module.lib
        if not hasattr(library, "nnrp_py_client_submit_result_compact") and not hasattr(
            library,
            "nnrp_py_client_submit_result_compact_v2",
        ):
            raise NativeArtifactError("native cffi API module does not expose submit/result compact wrapper")
        return _NativeCffiSubmitResultApi(ffi, library, os.fsencode(artifact_path))
    except (ImportError, AttributeError, OSError, RuntimeError, NativeArtifactError) as error:
        if required:
            raise NativeArtifactError(f"native cffi API binding is unavailable: {error}") from error
        return None


def load_native_runtime(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    transport: str | None = None,
    library: Any | None = None,
    cffi_submit_result_api: _NativeCffiSubmitResultApi | None = None,
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
    resolved_cffi_api = cffi_submit_result_api
    if resolved_cffi_api is None and library is None:
        resolved_cffi_api = _load_native_cffi_submit_result_api(resolved_path)
    return NativeRuntimeEntrypoints(
        loaded_library,
        artifact_path=resolved_path,
        cffi_submit_result_api=resolved_cffi_api,
    )


def load_native_client(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    transport: str | None = None,
    library: Any | None = None,
    cffi_submit_result_api: _NativeCffiSubmitResultApi | None = None,
) -> NativeRuntimeClient:
    return NativeRuntimeClient(
        load_native_runtime(
            artifact_path,
            root=root,
            native_platform=native_platform,
            transport=transport,
            library=library,
            cffi_submit_result_api=cffi_submit_result_api,
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
    if capabilities.abi_major != EXPECTED_ABI_MAJOR or capabilities.abi_minor < MINIMUM_ABI_MINOR:
        raise NativeArtifactError(
            "native artifact ABI mismatch: "
            f"expected {EXPECTED_ABI_MAJOR}.{MINIMUM_ABI_MINOR}.x, "
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
    manifest = _load_native_artifact_manifest(manifest_path) if manifest_path.is_file() else {}
    scope = _manifest_transport_scope(manifest, artifact_dir)
    slots = _manifest_transport_slots(manifest, scope)
    if scope != "all" and scope not in slots:
        raise NativeArtifactError(f"native artifact manifest scope {scope!r} is not listed in transport_slots")
    name = scope if scope != "all" else "tcp"
    return NativeTransportProvider(
        name=name,
        artifact_path=library_path,
        manifest_path=manifest_path if manifest_path.is_file() else None,
        transport_slots=slots,
        enabled_features=_manifest_string_tuple(manifest, "enabled_features"),
        package=_manifest_optional_string(manifest, "package"),
        transport_scope=scope,
        platform_tag=native_platform.tag,
        cost=_manifest_optional_mapping(manifest, "provider_cost"),
        preference=_manifest_optional_mapping(manifest, "provider_preference"),
        limitations=_manifest_string_tuple(manifest, "platform_limitations"),
    )


def _required_transport_slots_for_artifact(artifact_path: Path, transport: str | None) -> int:
    if transport is not None:
        return NATIVE_TRANSPORT_SLOT_BY_NAME[_normalize_native_transport_scope(transport)]
    manifest = _load_native_artifact_manifest(artifact_path.with_name("manifest.json"))
    scope = _manifest_transport_scope(manifest, artifact_path.parent)
    if scope == "all":
        return REQUIRED_TRANSPORT_SLOTS
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
        inferred = artifact_dir.name.lower()
        return inferred if inferred in NATIVE_TRANSPORT_SCOPES else "all"
    if not isinstance(raw_scope, str):
        raise NativeArtifactError("native artifact manifest transport_scope must be a string")
    scope = raw_scope.strip().lower().replace("_", "-")
    if scope == "all" or scope in NATIVE_TRANSPORT_SCOPES:
        return scope
    raise NativeArtifactError(f"unsupported native transport scope: {raw_scope}")


def _manifest_transport_slots(manifest: Mapping[str, Any], scope: str) -> tuple[str, ...]:
    raw_slots = manifest.get("transport_slots")
    if raw_slots is None:
        return NATIVE_TRANSPORT_SCOPES if scope == "all" else (scope,)
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


def _manifest_optional_mapping(manifest: Mapping[str, Any], field_name: str) -> Mapping[str, Any] | None:
    value = manifest.get(field_name)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise NativeArtifactError(f"native artifact manifest {field_name} must be an object")
    return value


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
    from nnrp.schema import Preview3TypedPayloadDescriptor

    return Preview3TypedPayloadDescriptor(
        profile_id=int(descriptor.profile_id),
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
        int(identity.namespace),
        int(identity.key_hi),
        int(identity.key_lo),
        int(identity.object_kind),
    )


def _cache_identity_key(identity: Any) -> tuple[int, int, int, int]:
    return (int(identity.namespace), int(identity.key_hi), int(identity.key_lo), int(identity.object_kind))


def _cache_identity_from_ffi(object_id: _NnrpCacheObjectId) -> Any:
    from nnrp.cache import CacheObjectIdentity

    return CacheObjectIdentity(
        namespace=int(object_id.cache_namespace),
        object_kind=int(object_id.object_kind),
        key_hi=int(object_id.cache_key_hi),
        key_lo=int(object_id.cache_key_lo),
    )


def _cache_lease_result_from_ffi(result: _NnrpCacheLeaseResult, *, owner_session_id: int) -> Any:
    from nnrp.cache import CacheLeaseDescriptor, CacheLeaseResult, CacheObjectVersion

    identity = _cache_identity_from_ffi(result.object_id)
    outcome = _CACHE_LEASE_OUTCOME_BY_CODE.get(int(result.outcome_code))
    if outcome is None:
        raise NativeHandleError(f"unknown native cache lease outcome {int(result.outcome_code)}")

    lease = None
    object_version = None
    if result.lease_id != 0 or result.expires_at_ms != 0:
        lease = CacheLeaseDescriptor(
            identity=identity,
            owner_session_id=int(owner_session_id),
            lease_epoch=int(result.lease_id),
            expires_at_ms=int(result.expires_at_ms),
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


def _infer_lifecycle_from_event(event: NativeRuntimeEvent) -> NativeOperationLifecycle:
    if not event.diagnostic.status.succeeded or event.kind == EVENT_KIND_ERROR:
        return NativeOperationLifecycle.FAILED
    if event.kind == EVENT_KIND_RESULT_DROPPED:
        return NativeOperationLifecycle.CANCELLED
    return NativeOperationLifecycle.COMPLETED


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

_RESULT_STATE_LIFECYCLE = {
    RESULT_STATE_COMPLETED: NativeOperationLifecycle.COMPLETED,
    RESULT_STATE_PARTIAL: NativeOperationLifecycle.PARTIAL,
    RESULT_STATE_DEGRADED: NativeOperationLifecycle.DEGRADED,
    RESULT_STATE_STALE_REUSE: NativeOperationLifecycle.STALE_REUSE,
    RESULT_STATE_CANCELLED: NativeOperationLifecycle.CANCELLED,
    RESULT_STATE_FAILED: NativeOperationLifecycle.FAILED,
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
