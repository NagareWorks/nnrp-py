from __future__ import annotations

import asyncio
import ctypes
import time
from pathlib import Path

import pytest

from nnrp.native import (
    DEFAULT_ARTIFACT_ROOT_ENV,
    ERROR_FAMILY_CACHE,
    FFI_STATUS_CALLBACK_REJECTED,
    FFI_STATUS_INTERNAL_ERROR,
    FFI_STATUS_INVALID_ARGUMENT,
    FFI_STATUS_INVALID_HANDLE,
    FFI_STATUS_INVALID_STATE,
    FFI_STATUS_OK,
    FFI_STATUS_PROTOCOL_ERROR,
    FFI_STATUS_WOULD_BLOCK,
    HANDLE_KIND_BUFFER,
    HANDLE_KIND_CONNECTION,
    HANDLE_KIND_EVENT_PUMP,
    HANDLE_KIND_OPERATION,
    HANDLE_KIND_SESSION,
    REQUIRED_RUNTIME_FEATURES,
    TRANSPORT_SLOT_TCP,
    NativeArtifactError,
    NativeBufferHandle,
    NativeBufferView,
    NativeCallbackRejectedError,
    NativeConnectionHandle,
    NativeEventPumpHandle,
    NativeHandle,
    NativeHandleError,
    NativeInternalError,
    NativeInvalidArgumentError,
    NativeInvalidHandleError,
    NativeInvalidStateError,
    NativeMutableBufferView,
    NativeOperationHandle,
    NativeOperationLifecycle,
    NativePlatform,
    NativeProtocolError,
    NativeRuntimeBackend,
    NativeRuntimeClient,
    NativeRuntimeConnection,
    NativeRuntimeDiagnostic,
    NativeRuntimeEntrypoints,
    NativeRuntimeEvent,
    NativeRuntimeOperation,
    NativeRuntimePollResult,
    NativeRuntimeResult,
    NativeRuntimeSession,
    NativeSessionHandle,
    NativeStatus,
    NativeWouldBlockError,
    _NnrpBufferView,
    _NnrpBufferViewMut,
    _NnrpCallbackSink,
    _NnrpClientCancelRequest,
    _NnrpClientConnectRequest,
    _NnrpConnectionBootstrap,
    _NnrpControlRequest,
    _NnrpEvent,
    _NnrpFfiStatus,
    _NnrpHandle,
    _NnrpPollResult,
    _NnrpProtocolVersion,
    _NnrpRuntimeCapabilities,
    _NnrpServerAcceptRequest,
    _NnrpServerBindRequest,
    _NnrpServerFlowUpdateRequest,
    _NnrpServerReceiveSubmitRequest,
    _NnrpServerSendResultRequest,
    _NnrpSessionOpenRequest,
    _NnrpSubmitRequest,
    _normalize_arch,
    current_native_platform,
    default_artifact_root,
    load_native_client,
    load_native_library,
    load_native_runtime,
    native_library_name,
    probe_native_artifact,
    raise_for_native_status,
    resolve_native_artifact,
    select_native_runtime_backend,
)


class FakeLibrary:
    def __init__(
        self,
        *,
        abi_major: int = 1,
        abi_minor: int = 0,
        abi_patch: int = 0,
        protocol_major: int = 1,
        wire_format: int = 0,
        transport_slots: int = TRANSPORT_SLOT_TCP,
        feature_flags: int = REQUIRED_RUNTIME_FEATURES,
    ) -> None:
        self._capabilities = _NnrpRuntimeCapabilities(
            abi_major,
            abi_minor,
            abi_patch,
            0,
            _NnrpProtocolVersion(protocol_major, wire_format),
            1,
            0,
            0,
            3,
            1,
            0,
            transport_slots,
            feature_flags,
        )

    def nnrp_runtime_capabilities(self) -> _NnrpRuntimeCapabilities:
        return self._capabilities


class InvalidCapabilitiesLibrary:
    def nnrp_runtime_capabilities(self) -> object:
        return object()


class FakeFunction:
    def __init__(self, value: object | None = None, handler: object | None = None) -> None:
        self.value = value
        self.handler = handler
        self.calls: list[tuple[object, ...]] = []
        self.restype: object | None = None
        self.argtypes: list[object] | None = None

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        if self.handler is not None:
            return self.handler(*args)
        return self.value


class FakeEntrypointLibrary:
    def __init__(self, *, missing_symbol: str | None = None) -> None:
        for symbol in RUNTIME_ENTRYPOINT_SYMBOLS:
            if symbol != missing_symbol:
                setattr(self, symbol, FakeFunction())


class FakeRuntimeLibrary(FakeEntrypointLibrary):
    def __init__(self, *, status: _NnrpFfiStatus | None = None, event_payload: bytes = b"") -> None:
        super().__init__()
        self.status = status or NativeStatus.ok().to_ffi()
        self._event_payload_owner = (
            ctypes.create_string_buffer(event_payload, len(event_payload)) if event_payload else None
        )
        self.nnrp_runtime_capabilities.value = FakeLibrary().nnrp_runtime_capabilities()
        self.nnrp_client_connect.handler = self._client_connect
        self.nnrp_connection_bootstrap.handler = self._connection_bootstrap
        self.nnrp_client_open_session.handler = self._open_session
        self.nnrp_client_submit.handler = self._submit
        self.nnrp_client_close.handler = self._close
        self.nnrp_client_close_connection.handler = self._close_connection
        self.nnrp_client_cancel.handler = self._cancel
        self.nnrp_client_await_event.handler = self._await_event
        self.nnrp_control.handler = self._control

    def _client_connect(self, request: _NnrpClientConnectRequest, out_handle: object) -> _NnrpFfiStatus:
        _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_CONNECTION, request.connection_id, request.generation, 0))
        return self.status

    def _connection_bootstrap(self, request: _NnrpConnectionBootstrap, out_handle: object) -> _NnrpFfiStatus:
        _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_CONNECTION, request.connection_id, request.generation, 0))
        return self.status

    def _open_session(self, request: _NnrpSessionOpenRequest, out_handle: object) -> _NnrpFfiStatus:
        _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_SESSION, request.requested_session_id, request.generation, 0))
        return self.status

    def _submit(self, request: _NnrpSubmitRequest, out_handle: object) -> _NnrpFfiStatus:
        _write_handle(out_handle, _NnrpHandle(HANDLE_KIND_OPERATION, request.operation_id, 1, 0))
        return self.status

    def _close(self, handle: _NnrpHandle) -> _NnrpFfiStatus:
        return self.status if handle.kind == HANDLE_KIND_SESSION else _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)

    def _close_connection(self, handle: _NnrpHandle) -> _NnrpFfiStatus:
        return (
            self.status
            if handle.kind == HANDLE_KIND_CONNECTION
            else _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        )

    def _cancel(self, request: _NnrpClientCancelRequest) -> _NnrpFfiStatus:
        if request.session.kind != HANDLE_KIND_SESSION:
            return _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)
        return self.status

    def _control(self, request: _NnrpControlRequest) -> _NnrpFfiStatus:
        return self.status if request.handle.kind != 0 else _NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 0, 0, 0)

    def _await_event(self, handle: _NnrpHandle, out_result: object) -> _NnrpFfiStatus:
        target = getattr(out_result, "_obj", None)
        if target is None:
            target = ctypes.cast(out_result, ctypes.POINTER(_NnrpPollResult)).contents
        target.status = NativeStatus.ok().to_ffi()
        target.has_event = 1 if self._event_payload_owner is not None else 0
        if self._event_payload_owner is not None:
            target.event.kind = 6
            target.event.connection = handle
            target.event.session = _NnrpHandle(HANDLE_KIND_SESSION, 41, 3, 0)
            target.event.operation = _NnrpHandle(HANDLE_KIND_OPERATION, 99, 1, 0)
            target.event.frame_id = 7
            target.event.payload = _NnrpBufferView(
                ctypes.cast(self._event_payload_owner, ctypes.c_void_p),
                len(self._event_payload_owner.raw),
            )
            target.event.diagnostic.status = NativeStatus.ok().to_ffi()
        return self.status


def _write_handle(out_handle: object, handle: _NnrpHandle) -> None:
    target = getattr(out_handle, "_obj", None)
    if target is not None:
        target.kind = handle.kind
        target.id = handle.id
        target.generation = handle.generation
        target.flags = handle.flags
        return

    ctypes.cast(out_handle, ctypes.POINTER(_NnrpHandle)).contents = handle


class SlowSubmitRuntimeLibrary(FakeRuntimeLibrary):
    def _submit(self, request: _NnrpSubmitRequest, out_handle: object) -> _NnrpFfiStatus:
        time.sleep(0.2)
        return super()._submit(request, out_handle)


class FakeBackend:
    def __init__(self) -> None:
        self.connections: list[tuple[int, int, int]] = []

    def connect(self, *, connection_id: int, generation: int, transport_id: int) -> NativeRuntimeConnection:
        self.connections.append((connection_id, generation, transport_id))
        raise NotImplementedError("fixture connect")

    def bootstrap_connection(
        self,
        *,
        connection_id: int,
        generation: int,
        transport_id: int,
    ) -> NativeRuntimeConnection:
        self.connections.append((connection_id, generation, transport_id))
        raise NotImplementedError("fixture bootstrap")


RUNTIME_ENTRYPOINT_SYMBOLS = [
    "nnrp_current_protocol_version",
    "nnrp_runtime_capabilities",
    "nnrp_connection_bootstrap",
    "nnrp_client_connect",
    "nnrp_session_open",
    "nnrp_client_open_session",
    "nnrp_submit",
    "nnrp_client_submit",
    "nnrp_session_close",
    "nnrp_client_close",
    "nnrp_client_close_connection",
    "nnrp_client_cancel",
    "nnrp_client_await_event",
    "nnrp_server_bind",
    "nnrp_server_accept",
    "nnrp_server_receive_submit",
    "nnrp_server_send_result",
    "nnrp_server_send_flow_update",
    "nnrp_server_close",
    "nnrp_control",
    "nnrp_poll_empty",
    "nnrp_dispatch_event",
]


def test_current_native_platform_normalizes_host_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "aarch64")

    assert current_native_platform() == NativePlatform("macos", "arm64")


def test_default_artifact_root_prefers_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEFAULT_ARTIFACT_ROOT_ENV, str(tmp_path))

    assert default_artifact_root() == tmp_path


def test_default_artifact_root_falls_back_to_package_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DEFAULT_ARTIFACT_ROOT_ENV, raising=False)

    assert default_artifact_root().name == "native_artifacts"


def test_native_library_name_matches_supported_platforms() -> None:
    assert native_library_name("windows") == "nnrp_ffi.dll"
    assert native_library_name("linux") == "libnnrp_ffi.so"
    assert native_library_name("android") == "libnnrp_ffi.so"
    assert native_library_name("darwin") == "libnnrp_ffi.dylib"
    assert native_library_name("ios") == "libnnrp_ffi.dylib"


def test_native_platform_rejects_unsupported_values() -> None:
    with pytest.raises(NativeArtifactError, match="unsupported native artifact OS"):
        native_library_name("plan9")
    with pytest.raises(NativeArtifactError, match="unsupported native artifact architecture"):
        _normalize_arch("sparc")


def test_resolve_native_artifact_uses_platform_tag_and_library_name(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "linux-x86_64"
    artifact_dir.mkdir()
    artifact = artifact_dir / "libnnrp_ffi.so"
    artifact.write_bytes(b"not-a-real-shared-library")

    assert resolve_native_artifact(tmp_path, NativePlatform("linux", "x86_64")) == artifact


def test_resolve_native_artifact_uses_current_platform_when_not_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    artifact_dir = tmp_path / "windows-x86_64"
    artifact_dir.mkdir()
    artifact = artifact_dir / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    assert resolve_native_artifact(tmp_path) == artifact


def test_resolve_native_artifact_rejects_missing_artifact(tmp_path: Path) -> None:
    with pytest.raises(NativeArtifactError, match="native artifact was not found"):
        resolve_native_artifact(tmp_path, NativePlatform("linux", "x86_64"))


def test_load_native_library_surfaces_loader_errors(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"not-a-real-dll")

    with pytest.raises(NativeArtifactError, match="failed to load native artifact"):
        load_native_library(artifact)


def test_probe_native_artifact_accepts_matching_protocol(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    result = probe_native_artifact(artifact, library=FakeLibrary())

    assert result.artifact_path == artifact
    assert result.abi_major == 1
    assert result.abi_minor == 0
    assert result.abi_patch == 0
    assert result.protocol_major == 1
    assert result.protocol_wire_format == 0
    assert result.sdk_channel == 3
    assert result.sdk_revision == 1
    assert result.transport_slots == TRANSPORT_SLOT_TCP
    assert result.feature_flags == REQUIRED_RUNTIME_FEATURES


def test_probe_native_artifact_resolves_path_from_root(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "linux-arm64"
    artifact_dir.mkdir()
    artifact = artifact_dir / "libnnrp_ffi.so"
    artifact.write_bytes(b"fake")

    result = probe_native_artifact(
        root=tmp_path,
        native_platform=NativePlatform("linux", "arm64"),
        library=FakeLibrary(),
    )

    assert result.artifact_path == artifact


def test_probe_native_artifact_rejects_protocol_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="protocol mismatch"):
        probe_native_artifact(artifact, library=FakeLibrary(protocol_major=2, wire_format=0))


def test_probe_native_artifact_rejects_abi_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="ABI mismatch"):
        probe_native_artifact(artifact, library=FakeLibrary(abi_major=2))


def test_probe_native_artifact_rejects_missing_required_feature(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="required runtime feature flags"):
        probe_native_artifact(artifact, library=FakeLibrary(feature_flags=REQUIRED_RUNTIME_FEATURES & ~1))


def test_probe_native_artifact_rejects_missing_tcp_transport_slot(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="required transport slots"):
        probe_native_artifact(artifact, library=FakeLibrary(transport_slots=0))


def test_probe_native_artifact_rejects_missing_probe_symbol(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="missing nnrp_runtime_capabilities"):
        probe_native_artifact(artifact, library=object())


def test_probe_native_artifact_rejects_invalid_probe_shape(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")

    with pytest.raises(NativeArtifactError, match="invalid runtime capabilities shape"):
        probe_native_artifact(artifact, library=InvalidCapabilitiesLibrary())


def test_load_native_runtime_validates_probe_before_binding_entrypoints(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeEntrypointLibrary()
    library.nnrp_runtime_capabilities.value = FakeLibrary().nnrp_runtime_capabilities()

    runtime = load_native_runtime(artifact, library=library)

    assert runtime.submit is library.nnrp_submit


def test_load_native_runtime_rejects_probe_mismatch_before_binding_entrypoints(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeEntrypointLibrary(missing_symbol="nnrp_submit")
    library.nnrp_runtime_capabilities.value = FakeLibrary(abi_major=2).nnrp_runtime_capabilities()

    with pytest.raises(NativeArtifactError, match="ABI mismatch"):
        load_native_runtime(artifact, library=library)


def test_select_native_runtime_backend_prefers_valid_native_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    fallback = FakeBackend()
    library = FakeRuntimeLibrary()

    backend = select_native_runtime_backend(artifact, library=library, fallback=fallback)

    assert isinstance(backend, NativeRuntimeClient)
    assert isinstance(backend, NativeRuntimeBackend)
    assert fallback.connections == []


def test_select_native_runtime_backend_uses_fallback_when_native_missing(tmp_path: Path) -> None:
    fallback = FakeBackend()

    backend = select_native_runtime_backend(tmp_path / "missing.dll", fallback=fallback)

    assert backend is fallback


def test_select_native_runtime_backend_can_require_native(tmp_path: Path) -> None:
    with pytest.raises(NativeArtifactError, match="failed to load native artifact|was not found"):
        select_native_runtime_backend(tmp_path / "missing.dll", fallback=FakeBackend(), require_native=True)


def test_native_runtime_client_runs_connection_session_submit_close_roundtrip(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()

    client = load_native_client(artifact, library=library)
    connection = client.connect(connection_id=11, generation=2, transport_id=TRANSPORT_SLOT_TCP)
    session = connection.open_session(
        requested_session_id=41,
        generation=3,
        profile_id=4,
        schema_id=5,
        schema_version=6,
    )
    operation = session.submit(operation_id=99, frame_id=7, payload=b"payload")
    operation_scope = session.submit_operation(
        operation_id=100,
        frame_id=8,
        payload=b"payload",
        parent_operation_id=99,
        operation_group_id=1234,
    )
    connection.control(control_code=10, payload=b"connection-control")
    operation_scope.cancel()
    session.cancel(frame_id=7)
    session.control(control_code=11, payload=b"session-control")
    session.close()

    assert isinstance(client, NativeRuntimeClient)
    assert isinstance(connection, NativeRuntimeConnection)
    assert isinstance(session, NativeRuntimeSession)
    assert connection.handle.handle.id == 11
    assert session.connection.handle.id == 11
    assert session.handle.handle.id == 41
    assert operation.handle.id == 99
    assert isinstance(operation_scope, NativeRuntimeOperation)
    assert operation_scope.operation_id == 100
    assert operation_scope.frame_id == 8
    assert operation_scope.parent_operation_id == 99
    assert operation_scope.operation_group_id == 1234
    submit_request = library.nnrp_client_submit.calls[0][0]
    assert submit_request.frame_id == 7
    assert submit_request.payload.len == 7
    assert library.nnrp_control.calls[0][0].control_code == 10
    assert library.nnrp_control.calls[0][0].payload.len == len(b"connection-control")
    assert library.nnrp_client_cancel.calls[0][0].frame_id == 8
    assert library.nnrp_client_cancel.calls[1][0].frame_id == 7
    assert library.nnrp_control.calls[1][0].control_code == 11
    assert library.nnrp_control.calls[1][0].payload.len == len(b"session-control")


def test_native_runtime_connection_can_open_multiple_sessions(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()

    connection = load_native_client(artifact, library=library).connect(
        connection_id=11,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    first_session = connection.open_session(
        requested_session_id=41,
        generation=3,
        profile_id=4,
        schema_id=5,
        schema_version=6,
    )
    second_session = connection.open_session(
        requested_session_id=42,
        generation=4,
        profile_id=4,
        schema_id=5,
        schema_version=6,
    )

    first_operation = first_session.submit_operation(operation_id=99, frame_id=7)
    second_operation = second_session.submit_operation(operation_id=100, frame_id=8)

    assert first_session.connection == second_session.connection == connection.handle
    assert first_session.handle.handle.id == 41
    assert second_session.handle.handle.id == 42
    assert first_operation.session == first_session.handle
    assert second_operation.session == second_session.handle
    assert library.nnrp_client_open_session.calls[0][0].requested_session_id == 41
    assert library.nnrp_client_open_session.calls[1][0].requested_session_id == 42
    assert library.nnrp_client_submit.calls[0][0].session.id == 41
    assert library.nnrp_client_submit.calls[1][0].session.id == 42


def test_native_runtime_client_bootstraps_and_awaits_empty_event(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()

    connection = load_native_client(artifact, library=library).bootstrap_connection(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    result = connection.await_event()

    assert connection.handle.handle.id == 12
    assert isinstance(result, NativeRuntimePollResult)
    assert result.event is None


def test_native_runtime_event_snapshot_copies_payload(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")

    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    result = connection.await_event()

    assert result.event is not None
    assert isinstance(result.event, NativeRuntimeEvent)
    assert result.event.kind == 6
    assert result.event.payload == b"result"
    assert result.event.connection.id == 12
    assert result.event.session.id == 41
    assert result.event.operation.id == 99
    assert result.event.diagnostic.status.succeeded is True


def test_native_runtime_event_snapshot_survives_native_buffer_reuse(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")

    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    result = connection.await_event()
    assert result.event is not None

    assert library._event_payload_owner is not None
    library._event_payload_owner.value = b"reuse!"

    assert result.event.payload == b"result"


def test_native_runtime_result_preserves_lifecycle_surface(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )
    event = connection.poll_event()

    assert event is not None
    result = NativeRuntimeResult.from_event(event)
    partial = NativeRuntimeResult.from_event(event, state=NativeOperationLifecycle.PARTIAL)
    degraded = NativeRuntimeResult.from_event(event, state=NativeOperationLifecycle.DEGRADED)
    stale = NativeRuntimeResult.from_event(event, state=NativeOperationLifecycle.STALE_REUSE)

    assert result.state is NativeOperationLifecycle.COMPLETED
    assert result.operation_id == 99
    assert result.frame_id == 7
    assert result.payload == b"result"
    assert partial.state is NativeOperationLifecycle.PARTIAL
    assert degraded.state is NativeOperationLifecycle.DEGRADED
    assert stale.state is NativeOperationLifecycle.STALE_REUSE


def test_native_runtime_result_maps_error_and_drop_events() -> None:
    base_event = NativeRuntimeEvent(
        kind=10,
        connection=NativeHandle(HANDLE_KIND_CONNECTION, 12, 2),
        session=NativeHandle(HANDLE_KIND_SESSION, 41, 3),
        operation=NativeHandle(HANDLE_KIND_OPERATION, 99, 1),
        frame_id=7,
        payload=b"",
        diagnostic=NativeRuntimeDiagnostic(NativeStatus(FFI_STATUS_INTERNAL_ERROR), 12, 41, 99, 7),
    )
    drop_event = NativeRuntimeEvent(
        kind=7,
        connection=NativeHandle(HANDLE_KIND_CONNECTION, 12, 2),
        session=NativeHandle(HANDLE_KIND_SESSION, 41, 3),
        operation=NativeHandle(HANDLE_KIND_OPERATION, 99, 1),
        frame_id=7,
        payload=b"",
        diagnostic=NativeRuntimeDiagnostic(NativeStatus.ok(), 12, 41, 99, 7),
    )

    assert NativeRuntimeResult.from_event(base_event).state is NativeOperationLifecycle.FAILED
    assert NativeRuntimeResult.from_event(drop_event).state is NativeOperationLifecycle.CANCELLED


def test_native_runtime_connection_polls_event_delivery_model(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )

    event = connection.poll_event()
    events = connection.poll_events(max_events=1)
    async_event = asyncio.run(connection.async_poll_event())
    async_events = asyncio.run(_collect_async_events(connection))

    assert event is not None
    assert event.payload == b"result"
    assert [polled.payload for polled in events] == [b"result"]
    assert async_event is not None
    assert async_event.payload == b"result"
    assert [polled.payload for polled in async_events] == [b"result"]

    with pytest.raises(ValueError, match="max_events"):
        connection.poll_events(max_events=-1)


def test_native_runtime_connection_rejects_use_after_close(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")
    connection = load_native_client(artifact, library=library).connect(
        connection_id=12,
        generation=2,
        transport_id=TRANSPORT_SLOT_TCP,
    )

    connection.close()

    assert library.nnrp_client_close_connection.calls[0][0].id == 12
    with pytest.raises(NativeInvalidStateError, match="connection is closed"):
        connection.open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    with pytest.raises(NativeInvalidStateError, match="connection is closed"):
        connection.poll_event()
    with pytest.raises(NativeInvalidStateError, match="connection is closed"):
        connection.control(control_code=10)
    with pytest.raises(NativeInvalidStateError, match="connection is closed"):
        connection.close()


def test_native_runtime_session_submits_and_polls_result(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    result = session.submit_and_poll_result(
        operation_id=99,
        frame_id=7,
        payload=b"payload",
        state=NativeOperationLifecycle.PARTIAL,
        max_events=1,
    )
    async_result = asyncio.run(
        session.async_submit_and_poll_result(operation_id=99, frame_id=7, payload=b"payload", max_events=1)
    )

    assert result.state is NativeOperationLifecycle.PARTIAL
    assert result.operation_id == 99
    assert result.frame_id == 7
    assert result.payload == b"result"
    assert async_result.state is NativeOperationLifecycle.COMPLETED
    assert async_result.payload == b"result"


def test_native_runtime_session_raises_when_result_is_not_available(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    with pytest.raises(NativeWouldBlockError):
        session.submit_and_poll_result(operation_id=99, frame_id=7, payload=b"payload", max_events=1)

    with pytest.raises(ValueError, match="max_events"):
        session.poll_result(session.submit_operation(operation_id=99, frame_id=7), max_events=-1)


def test_native_runtime_session_ignores_result_for_different_session(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=42,
            generation=4,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    operation = session.submit_operation(operation_id=99, frame_id=7)

    with pytest.raises(NativeWouldBlockError):
        session.poll_result(operation, max_events=1)


def test_native_runtime_session_rejects_use_after_close(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(event_payload=b"result")
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )
    operation = session.submit_operation(operation_id=99, frame_id=7)

    session.close()

    with pytest.raises(NativeInvalidStateError, match="closed"):
        session.submit(operation_id=100, frame_id=8)
    with pytest.raises(NativeInvalidStateError, match="closed"):
        session.poll_result(operation, max_events=1)
    with pytest.raises(NativeInvalidStateError, match="closed"):
        session.cancel(frame_id=7)
    with pytest.raises(NativeInvalidStateError, match="closed"):
        session.control(control_code=11)
    with pytest.raises(NativeInvalidStateError, match="closed"):
        session.close()


def test_native_runtime_async_submit_cancels_native_frame(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = SlowSubmitRuntimeLibrary()
    session = (
        load_native_client(artifact, library=library)
        .connect(connection_id=12, generation=2, transport_id=TRANSPORT_SLOT_TCP)
        .open_session(
            requested_session_id=41,
            generation=3,
            profile_id=4,
            schema_id=5,
            schema_version=6,
        )
    )

    asyncio.run(_cancel_async_submit(session))

    assert library.nnrp_client_cancel.calls
    assert library.nnrp_client_cancel.calls[0][0].frame_id == 9


def test_native_runtime_client_raises_mapped_status_errors(tmp_path: Path) -> None:
    artifact = tmp_path / "nnrp_ffi.dll"
    artifact.write_bytes(b"fake")
    library = FakeRuntimeLibrary(status=_NnrpFfiStatus(FFI_STATUS_INVALID_STATE, 0, 0, 0))

    with pytest.raises(NativeInvalidStateError):
        load_native_client(artifact, library=library).connect(
            connection_id=11,
            generation=2,
            transport_id=TRANSPORT_SLOT_TCP,
        )


async def _collect_async_events(connection: NativeRuntimeConnection) -> list[NativeRuntimeEvent]:
    return [event async for event in connection.iter_events(max_events=1)]


async def _cancel_async_submit(session: NativeRuntimeSession) -> None:
    task = asyncio.create_task(session.async_submit_operation(operation_id=101, frame_id=9, payload=b"payload"))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_native_handle_roundtrips_ffi_shape() -> None:
    handle = NativeHandle(HANDLE_KIND_CONNECTION, 7, 2, 0)

    ffi = handle.to_ffi()
    decoded = NativeHandle.from_ffi(ffi)

    assert (ffi.kind, ffi.id, ffi.generation, ffi.flags) == (HANDLE_KIND_CONNECTION, 7, 2, 0)
    assert decoded == handle
    assert decoded.is_valid is True


def test_native_handle_invalid_shape_is_zero_only() -> None:
    assert NativeHandle.invalid().to_ffi().kind == 0

    with pytest.raises(NativeHandleError, match="invalid handles"):
        NativeHandle(0, 1, 0)


def test_native_handle_requires_valid_kind_id_and_generation() -> None:
    with pytest.raises(NativeHandleError, match="uint32"):
        NativeHandle(-1, 1, 1)
    with pytest.raises(NativeHandleError, match="non-zero id"):
        NativeHandle(HANDLE_KIND_SESSION, 0, 1)
    with pytest.raises(NativeHandleError, match="non-zero id"):
        NativeHandle(HANDLE_KIND_SESSION, 1, 0)


@pytest.mark.parametrize(
    ("wrapper_type", "kind"),
    [
        (NativeConnectionHandle, HANDLE_KIND_CONNECTION),
        (NativeSessionHandle, HANDLE_KIND_SESSION),
        (NativeOperationHandle, HANDLE_KIND_OPERATION),
        (NativeEventPumpHandle, HANDLE_KIND_EVENT_PUMP),
        (NativeBufferHandle, HANDLE_KIND_BUFFER),
    ],
)
def test_typed_native_handles_accept_only_matching_kind(wrapper_type: type, kind: int) -> None:
    wrapper = wrapper_type.from_ffi(_NnrpHandle(kind, 11, 3, 0))

    assert wrapper.to_ffi().kind == kind

    with pytest.raises(NativeHandleError, match="expected native handle kind"):
        mismatched_kind = HANDLE_KIND_CONNECTION if kind != HANDLE_KIND_CONNECTION else HANDLE_KIND_SESSION
        wrapper_type(NativeHandle(mismatched_kind, 11, 3))


def test_native_buffer_views_roundtrip_ffi_shape() -> None:
    view = NativeBufferView(0x1000, 64)
    mutable_view = NativeMutableBufferView(0x2000, 128)

    assert NativeBufferView.from_ffi(view.to_ffi()) == view
    assert NativeMutableBufferView.from_ffi(mutable_view.to_ffi()) == mutable_view
    assert NativeBufferView.empty().to_ffi().ptr is None
    assert NativeMutableBufferView.empty().to_ffi().ptr is None
    assert NativeBufferView.from_ffi(_NnrpBufferView(None, 0)) == NativeBufferView.empty()
    assert NativeMutableBufferView.from_ffi(_NnrpBufferViewMut(None, 0)) == NativeMutableBufferView.empty()


def test_native_buffer_views_reject_non_empty_null_pointer() -> None:
    with pytest.raises(NativeHandleError, match="non-null pointer"):
        NativeBufferView(0, 1)
    with pytest.raises(NativeHandleError, match="non-null pointer"):
        NativeMutableBufferView(0, 1)


def test_native_status_roundtrips_ffi_shape() -> None:
    status = NativeStatus(FFI_STATUS_PROTOCOL_ERROR, ERROR_FAMILY_CACHE, 0x22, 0x33)

    ffi = status.to_ffi()
    decoded = NativeStatus.from_ffi(ffi)

    assert (ffi.status_code, ffi.error_family, ffi.protocol_error_code, ffi.detail_code) == (
        FFI_STATUS_PROTOCOL_ERROR,
        ERROR_FAMILY_CACHE,
        0x22,
        0x33,
    )
    assert decoded == status
    assert decoded.succeeded is False
    assert NativeStatus.ok().succeeded is True


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (FFI_STATUS_INVALID_ARGUMENT, NativeInvalidArgumentError),
        (FFI_STATUS_INVALID_HANDLE, NativeInvalidHandleError),
        (FFI_STATUS_INVALID_STATE, NativeInvalidStateError),
        (FFI_STATUS_PROTOCOL_ERROR, NativeProtocolError),
        (FFI_STATUS_WOULD_BLOCK, NativeWouldBlockError),
        (FFI_STATUS_CALLBACK_REJECTED, NativeCallbackRejectedError),
        (FFI_STATUS_INTERNAL_ERROR, NativeInternalError),
    ],
)
def test_raise_for_native_status_maps_stable_status_codes(status_code: int, error_type: type[Exception]) -> None:
    status = NativeStatus(status_code, ERROR_FAMILY_CACHE, 7, 9)

    with pytest.raises(error_type) as captured:
        raise_for_native_status(status)

    assert captured.value.status == status
    assert "status_code=" in str(captured.value)


def test_raise_for_native_status_accepts_ffi_status_and_ignores_ok() -> None:
    raise_for_native_status(NativeStatus.ok())
    raise_for_native_status(_NnrpFfiStatus(FFI_STATUS_OK, 0, 0, 0))

    with pytest.raises(NativeInvalidHandleError):
        raise_for_native_status(_NnrpFfiStatus(FFI_STATUS_INVALID_HANDLE, 5, 0, 2))


def test_raise_for_native_status_maps_unknown_status_to_internal_error() -> None:
    with pytest.raises(NativeInternalError):
        raise_for_native_status(NativeStatus(0x1234))


def test_native_runtime_entrypoints_bind_frozen_symbol_table() -> None:
    library = FakeEntrypointLibrary()

    entrypoints = NativeRuntimeEntrypoints(library)

    assert entrypoints.current_protocol_version is library.nnrp_current_protocol_version
    assert library.nnrp_current_protocol_version.restype is _NnrpProtocolVersion
    assert library.nnrp_current_protocol_version.argtypes == []
    assert library.nnrp_runtime_capabilities.restype is _NnrpRuntimeCapabilities
    assert library.nnrp_connection_bootstrap.argtypes == [
        _NnrpConnectionBootstrap,
        ctypes.POINTER(_NnrpHandle),
    ]
    assert library.nnrp_client_connect.argtypes == [
        _NnrpClientConnectRequest,
        ctypes.POINTER(_NnrpHandle),
    ]
    assert library.nnrp_session_open.argtypes == [
        _NnrpSessionOpenRequest,
        ctypes.POINTER(_NnrpHandle),
    ]
    assert library.nnrp_client_open_session.argtypes == [
        _NnrpSessionOpenRequest,
        ctypes.POINTER(_NnrpHandle),
    ]
    assert library.nnrp_submit.argtypes == [_NnrpSubmitRequest, ctypes.POINTER(_NnrpHandle)]
    assert library.nnrp_client_submit.argtypes == [_NnrpSubmitRequest, ctypes.POINTER(_NnrpHandle)]
    assert library.nnrp_session_close.argtypes == [_NnrpHandle]
    assert library.nnrp_client_close.argtypes == [_NnrpHandle]
    assert library.nnrp_client_cancel.argtypes == [_NnrpClientCancelRequest]
    assert library.nnrp_client_await_event.argtypes == [
        _NnrpHandle,
        ctypes.POINTER(_NnrpPollResult),
    ]
    assert library.nnrp_server_bind.argtypes == [_NnrpServerBindRequest, ctypes.POINTER(_NnrpHandle)]
    assert library.nnrp_server_accept.argtypes == [
        _NnrpServerAcceptRequest,
        ctypes.POINTER(_NnrpHandle),
    ]
    assert library.nnrp_server_receive_submit.argtypes == [
        _NnrpServerReceiveSubmitRequest,
        ctypes.POINTER(_NnrpHandle),
    ]
    assert library.nnrp_server_send_result.argtypes == [_NnrpServerSendResultRequest]
    assert library.nnrp_server_send_flow_update.argtypes == [_NnrpServerFlowUpdateRequest]
    assert library.nnrp_server_close.argtypes == [_NnrpHandle]
    assert library.nnrp_control.argtypes == [_NnrpControlRequest]
    assert library.nnrp_poll_empty.argtypes == [ctypes.POINTER(_NnrpPollResult)]
    assert library.nnrp_dispatch_event.argtypes == [_NnrpCallbackSink, ctypes.POINTER(_NnrpEvent)]

    for symbol in RUNTIME_ENTRYPOINT_SYMBOLS[2:]:
        assert getattr(library, symbol).restype is _NnrpFfiStatus


def test_native_runtime_entrypoints_reject_missing_symbol() -> None:
    library = FakeEntrypointLibrary(missing_symbol="nnrp_submit")

    with pytest.raises(NativeArtifactError, match="missing nnrp_submit"):
        NativeRuntimeEntrypoints(library)
