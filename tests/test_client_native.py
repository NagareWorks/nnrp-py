from __future__ import annotations

from pathlib import Path

import pytest

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


class FakeOperation:
    def __init__(self, *, session_id: int, operation_id: int, frame_id: int, payload: bytes) -> None:
        self.session_id = session_id
        self.operation_id = operation_id
        self.frame_id = frame_id
        self.payload = payload


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


def test_select_client_native_backend_can_require_native(tmp_path: Path) -> None:
    with pytest.raises(NativeArtifactError):
        select_client_native_backend(tmp_path / "missing.dll", fallback=FakeBackend(), require_native=True)
