from __future__ import annotations

from pathlib import Path

import pytest

import nnrp.client.native as client_native_module
from nnrp.client import (
    NativeClientConnectionOptions,
    NativeClientSessionOpenOptions,
    NativeClientSessionOptions,
    connect_native_client_connection,
    connect_native_client_session,
    select_client_native_backend,
)
from nnrp.native import NativeArtifactError


class FakeBackend:
    def __init__(self) -> None:
        self.connections: list[FakeConnection] = []

    def connect(self, *, connection_id: int, generation: int, transport_id: int) -> FakeConnection:
        connection = FakeConnection(connection_id, generation, transport_id)
        self.connections.append(connection)
        return connection

    def bootstrap_connection(self, *, connection_id: int, generation: int, transport_id: int) -> FakeConnection:
        return self.connect(connection_id=connection_id, generation=generation, transport_id=transport_id)


class FakeConnection:
    def __init__(self, connection_id: int, generation: int, transport_id: int) -> None:
        self.connection_id = connection_id
        self.generation = generation
        self.transport_id = transport_id
        self.sessions: list[FakeSession] = []

    def open_session(
        self,
        *,
        requested_session_id: int,
        generation: int,
        profile_id: int,
        schema_id: int,
        schema_version: int,
    ) -> FakeSession:
        session = FakeSession(
            requested_session_id=requested_session_id,
            generation=generation,
            profile_id=profile_id,
            schema_id=schema_id,
            schema_version=schema_version,
        )
        self.sessions.append(session)
        return session


class FakeSession:
    def __init__(
        self,
        *,
        requested_session_id: int,
        generation: int,
        profile_id: int,
        schema_id: int,
        schema_version: int,
    ) -> None:
        self.requested_session_id = requested_session_id
        self.generation = generation
        self.profile_id = profile_id
        self.schema_id = schema_id
        self.schema_version = schema_version
        self.closed = False
        self.operations: list[FakeOperation] = []
        self.cancelled_frames: list[int] = []

    def close(self) -> None:
        self.closed = True

    def submit_operation(self, *, operation_id: int, frame_id: int, payload: bytes = b"") -> FakeOperation:
        operation = FakeOperation(
            session_id=self.requested_session_id,
            operation_id=operation_id,
            frame_id=frame_id,
            payload=payload,
        )
        self.operations.append(operation)
        return operation

    def poll_result(self, operation: FakeOperation, *, max_events: int | None = None) -> FakeResult:
        return FakeResult(
            session_id=self.requested_session_id,
            operation_id=operation.operation_id,
            frame_id=operation.frame_id,
            payload=operation.payload,
            max_events=max_events,
        )

    def cancel(self, *, frame_id: int) -> None:
        self.cancelled_frames.append(frame_id)


class FakeOperation:
    def __init__(self, *, session_id: int, operation_id: int, frame_id: int, payload: bytes) -> None:
        self.session_id = session_id
        self.operation_id = operation_id
        self.frame_id = frame_id
        self.payload = payload
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeResult:
    def __init__(
        self,
        *,
        session_id: int,
        operation_id: int,
        frame_id: int,
        payload: bytes,
        max_events: int | None,
    ) -> None:
        self.session_id = session_id
        self.operation_id = operation_id
        self.frame_id = frame_id
        self.payload = payload
        self.max_events = max_events


def test_connect_native_client_session_opens_and_closes_host_session() -> None:
    backend = FakeBackend()
    options = NativeClientSessionOptions(
        connection_id=7,
        connection_generation=2,
        transport_id=1,
        requested_session_id=8,
        session_generation=3,
        profile_id=4,
        schema_id=5,
        schema_version=6,
    )

    with connect_native_client_session(backend=backend, options=options) as session:
        assert session.requested_session_id == 8
        assert session.profile_id == 4
        assert session.closed is False

    assert backend.connections[0].connection_id == 7
    assert backend.connections[0].generation == 2
    assert backend.connections[0].sessions[0].closed is True


def test_connect_native_client_connection_routes_results_for_multiple_sessions() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(
        backend=backend,
        options=NativeClientConnectionOptions(connection_id=7, connection_generation=2),
    ) as connection:
        first = connection.open_session(NativeClientSessionOpenOptions(requested_session_id=10))
        second = connection.open_session(NativeClientSessionOpenOptions(requested_session_id=11))
        first_operation = first.submit_operation(operation_id=100, frame_id=1, payload=b"first")
        second_operation = second.submit_operation(operation_id=101, frame_id=2, payload=b"second")

        first_result = connection.poll_result(first, first_operation, max_events=4)
        second_result = connection.poll_result(second, second_operation, max_events=4)

        assert first_result.session_id == 10
        assert first_result.payload == b"first"
        assert first_result.max_events == 4
        assert second_result.session_id == 11
        assert second_result.payload == b"second"

    assert backend.connections[0].connection_id == 7
    assert first.closed is True
    assert second.closed is True


def test_native_client_connection_rejects_use_after_close() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        pass

    with pytest.raises(RuntimeError, match="closed"):
        connection.open_session()


def test_native_client_connection_supports_operation_cancellation() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = connection.open_session()
        operation = session.submit_operation(operation_id=100, frame_id=7, payload=b"payload")

        operation.cancel()
        session.cancel(frame_id=7)

        assert operation.cancelled is True
        assert session.cancelled_frames == [7]


def test_select_client_native_backend_can_require_native(tmp_path: Path) -> None:
    with pytest.raises(NativeArtifactError):
        select_client_native_backend(tmp_path / "missing.dll", fallback=FakeBackend(), require_native=True)


def test_select_client_native_backend_uses_fallback_when_artifact_missing(tmp_path: Path) -> None:
    fallback = FakeBackend()

    backend = select_client_native_backend(tmp_path / "missing.dll", fallback=fallback)

    assert backend is fallback


def test_connect_native_client_connection_uses_fallback_when_artifact_missing(tmp_path: Path) -> None:
    fallback = FakeBackend()

    with connect_native_client_connection(tmp_path / "missing.dll", fallback=fallback) as connection:
        session = connection.open_session(NativeClientSessionOpenOptions(requested_session_id=12))

        assert session.requested_session_id == 12

    assert fallback.connections[0].sessions[0].closed is True


def test_connect_native_client_session_can_require_native_artifact(tmp_path: Path) -> None:
    with pytest.raises(NativeArtifactError):
        with connect_native_client_session(
            tmp_path / "missing.dll",
            fallback=FakeBackend(),
            require_native=True,
        ):
            pass


def test_select_client_native_backend_prefers_native_artifact_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    native_backend = FakeBackend()
    fallback_backend = FakeBackend()

    def select_backend(
        artifact_path: Path | str | None = None,
        *,
        root: Path | str | None = None,
        native_platform: object | None = None,
        library: object | None = None,
        fallback: object | None = None,
        require_native: bool = False,
    ) -> FakeBackend:
        assert artifact_path == "nnrp_ffi.dll"
        assert root is None
        assert native_platform is None
        assert library is None
        assert fallback is fallback_backend
        assert require_native is False
        return native_backend

    monkeypatch.setattr(client_native_module, "select_native_runtime_backend", select_backend)

    backend = select_client_native_backend("nnrp_ffi.dll", fallback=fallback_backend)

    assert backend is native_backend
    assert fallback_backend.connections == []
