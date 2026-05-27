"""Native artifact discovery and ABI probe helpers for Rust-backed NNRP runtimes."""

from __future__ import annotations

import asyncio
import ctypes
import os
import platform
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, TypeVar, runtime_checkable

EXPECTED_PROTOCOL_MAJOR = 1
EXPECTED_PROTOCOL_WIRE_FORMAT = 0
EXPECTED_ABI_MAJOR = 1
MINIMUM_ABI_MINOR = 1
TRANSPORT_SLOT_QUIC = 0x00000001
TRANSPORT_SLOT_TCP = 0x00000002
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
HANDLE_KIND_INVALID = 0
HANDLE_KIND_CONNECTION = 1
HANDLE_KIND_SESSION = 2
HANDLE_KIND_OPERATION = 3
HANDLE_KIND_EVENT_PUMP = 4
HANDLE_KIND_BUFFER = 5
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
DEFAULT_ARTIFACT_ROOT_ENV = "NNRP_NATIVE_ARTIFACT_ROOT"
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


class NativeHandleError(ValueError):
    """Raised when an FFI handle or buffer view violates the native ABI contract."""


@dataclass(frozen=True)
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
        return cls(FFI_STATUS_OK)

    @classmethod
    def from_ffi(cls, status: _NnrpFfiStatus) -> NativeStatus:
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
    def is_protocol_error(self) -> bool:
        return self.status_code == FFI_STATUS_PROTOCOL_ERROR

    def to_ffi(self) -> _NnrpFfiStatus:
        return _NnrpFfiStatus(self.status_code, self.error_family, self.protocol_error_code, self.detail_code)


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


@dataclass(frozen=True)
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


class NativeRuntimeEntrypoints:
    """ctypes entrypoint table for the frozen Rust runtime ABI."""

    def __init__(self, library: Any) -> None:
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
        self.poll_empty = _bind_native_function(
            library, "nnrp_poll_empty", _NnrpFfiStatus, [ctypes.POINTER(_NnrpPollResult)]
        )
        self.dispatch_event = _bind_native_function(
            library,
            "nnrp_dispatch_event",
            _NnrpFfiStatus,
            [_NnrpCallbackSink, ctypes.POINTER(_NnrpEvent)],
        )


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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
    def failed(self) -> bool:
        return not self.status.succeeded

    def to_report(self) -> dict[str, int | str | bool]:
        return {
            "status_code": self.status.status_code,
            "status_name": self.status_name,
            "error_family": self.status.error_family,
            "error_family_name": self.error_family_name,
            "protocol_error_code": self.status.protocol_error_code,
            "detail_code": self.status.detail_code,
            "failed": self.failed,
            "related_connection_id": self.related_connection_id,
            "related_session_id": self.related_session_id,
            "related_operation_id": self.related_operation_id,
            "related_frame_id": self.related_frame_id,
        }


@dataclass(frozen=True)
class NativeRuntimeEvent:
    kind: int
    connection: NativeHandle
    session: NativeHandle
    operation: NativeHandle
    frame_id: int
    payload: bytes
    diagnostic: NativeRuntimeDiagnostic

    @classmethod
    def from_ffi(cls, event: _NnrpEvent) -> NativeRuntimeEvent:
        return cls(
            kind=int(event.kind),
            connection=NativeHandle.from_ffi(event.connection),
            session=NativeHandle.from_ffi(event.session),
            operation=NativeHandle.from_ffi(event.operation),
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


@dataclass(frozen=True)
class NativeRuntimeResult:
    state: NativeOperationLifecycle
    operation_id: int
    frame_id: int
    payload: bytes
    event: NativeRuntimeEvent
    diagnostic: NativeStructuredDiagnostic

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


@runtime_checkable
class NativeRuntimeBackend(Protocol):
    def connect(self, *, connection_id: int, generation: int, transport_id: int) -> NativeRuntimeConnection:
        ...

    def bootstrap_connection(
        self,
        *,
        connection_id: int,
        generation: int,
        transport_id: int,
    ) -> NativeRuntimeConnection:
        ...


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
            raise NativeInvalidStateError(
                NativeStatus(FFI_STATUS_INVALID_STATE), "native runtime connection is closed"
            )


@dataclass(frozen=True)
class NativeRuntimeSession:
    entrypoints: NativeRuntimeEntrypoints
    connection: NativeConnectionHandle
    handle: NativeSessionHandle
    priority_class: NativeSessionPriorityClass = NativeSessionPriorityClass.BALANCED
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

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
            if _event_matches_operation(event, operation):
                return NativeRuntimeResult.from_event(event, state=state)

        raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))

    def submit_and_poll_result(
        self,
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview = b"",
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
        scheduling_hint: NativeOperationSchedulingHint | None = None,
        state: NativeOperationLifecycle | str | None = None,
        max_events: int | None = None,
    ) -> NativeRuntimeResult:
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

    def control(self, *, control_code: int, payload: bytes | bytearray | memoryview = b"") -> None:
        self._ensure_open()
        payload_view, _payload_owner = _buffer_view_from_payload(payload)
        request = _NnrpControlRequest(self.handle.to_ffi(), control_code, payload_view)
        status = self.entrypoints.control(request)
        raise_for_native_status(status)

    def _ensure_open(self) -> None:
        if self._closed:
            raise NativeInvalidStateError(NativeStatus(FFI_STATUS_INVALID_STATE), "native runtime session is closed")


def _event_matches_operation(event: NativeRuntimeEvent, operation: NativeRuntimeOperation) -> bool:
    if event.session != operation.session.handle:
        return False
    return (
        event.operation.id == operation.handle.handle.id
        or event.operation.id == operation.operation_id
        or event.frame_id == operation.frame_id
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
) -> Path:
    selected_platform = native_platform or current_native_platform()
    artifact_root = Path(root) if root is not None else default_artifact_root()
    artifact_path = artifact_root / selected_platform.tag / native_library_name(selected_platform.os_name)
    if not artifact_path.is_file():
        raise NativeArtifactError(f"native artifact was not found: {artifact_path}")
    return artifact_path


def load_native_library(artifact_path: Path | str) -> ctypes.CDLL:
    try:
        return ctypes.CDLL(str(artifact_path))
    except OSError as error:
        raise NativeArtifactError(f"failed to load native artifact {artifact_path}: {error}") from error


def load_native_runtime(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    library: Any | None = None,
) -> NativeRuntimeEntrypoints:
    resolved_path = Path(artifact_path) if artifact_path is not None else resolve_native_artifact(root, native_platform)
    loaded_library = library if library is not None else load_native_library(resolved_path)
    capabilities = _call_runtime_capabilities(loaded_library)
    _validate_runtime_capabilities(capabilities)
    return NativeRuntimeEntrypoints(loaded_library)


def load_native_client(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    library: Any | None = None,
) -> NativeRuntimeClient:
    return NativeRuntimeClient(
        load_native_runtime(artifact_path, root=root, native_platform=native_platform, library=library)
    )


def select_native_runtime_backend(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    library: Any | None = None,
    fallback: NativeRuntimeBackend | None = None,
    require_native: bool = False,
) -> NativeRuntimeBackend:
    try:
        return load_native_client(artifact_path, root=root, native_platform=native_platform, library=library)
    except NativeArtifactError:
        if fallback is None or require_native:
            raise
        return fallback


def probe_native_artifact(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    library: Any | None = None,
) -> NativeProbeResult:
    resolved_path = Path(artifact_path) if artifact_path is not None else resolve_native_artifact(root, native_platform)
    loaded_library = library if library is not None else load_native_library(resolved_path)
    capabilities = _call_runtime_capabilities(loaded_library)
    _validate_runtime_capabilities(capabilities)
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


def _validate_runtime_capabilities(capabilities: _NnrpRuntimeCapabilities) -> None:
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
    missing_transport_slots = REQUIRED_TRANSPORT_SLOTS & ~int(capabilities.transport_slots)
    if missing_transport_slots:
        raise NativeArtifactError(
            f"native artifact is missing required transport slots: 0x{missing_transport_slots:08x}"
        )


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
    native_status = status if isinstance(status, NativeStatus) else NativeStatus.from_ffi(status)
    if native_status.succeeded:
        return

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
    view = memoryview(payload)
    if view.nbytes == 0:
        return _NnrpBufferView(None, 0), None
    if not view.contiguous:
        raise NativeHandleError("native submit payload must be contiguous")
    buffer = ctypes.create_string_buffer(view.tobytes(), view.nbytes)
    return _NnrpBufferView(ctypes.cast(buffer, ctypes.c_void_p), view.nbytes), buffer


def _copy_buffer_view(view: _NnrpBufferView) -> bytes:
    length = int(view.len)
    if length == 0:
        return b""
    if not view.ptr:
        raise NativeHandleError("native event payload has non-empty null pointer")
    return ctypes.string_at(view.ptr, length)


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
