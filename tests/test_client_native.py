from __future__ import annotations

from pathlib import Path

import pytest

import nnrp.client.native as client_native_module
from nnrp.client import (
    NativeClientConnectionOptions,
    NativeClientOperationScope,
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


class LegacyFakeBackend(FakeBackend):
    def connect(self, *, connection_id: int, generation: int, transport_id: int) -> LegacyFakeConnection:
        connection = LegacyFakeConnection(connection_id, generation, transport_id)
        self.connections.append(connection)
        return connection


class FakeConnection:
    def __init__(self, connection_id: int, generation: int, transport_id: int) -> None:
        self.connection_id = connection_id
        self.generation = generation
        self.transport_id = transport_id
        self.sessions: list[FakeSession] = []
        self.control_calls: list[tuple[int, bytes | bytearray | memoryview]] = []
        self.dispatch_calls: list[tuple[str, int | None, int | None]] = []
        self.closed = False

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

    def control(self, *, control_code: int, payload: bytes | bytearray | memoryview = b"") -> None:
        self.control_calls.append((control_code, payload))

    def dispatch_events(self, callback, *, max_events: int | None = None, event_kind: int | None = None) -> int:
        self.dispatch_calls.append(("events", max_events, event_kind))
        callback("event")
        return 1

    def dispatch_credit_updates(self, callback, *, max_events: int | None = None) -> int:
        self.dispatch_calls.append(("credit_updates", max_events, None))
        callback("credit")
        return 1

    def dispatch_payload_family_events(
        self,
        payload_family: str,
        callback,
        *,
        max_events: int | None = None,
        event_kind: int | None = None,
    ) -> int:
        self.dispatch_calls.append((payload_family, max_events, event_kind))
        callback(payload_family)
        return 1

    def dispatch_structured_events(self, callback, *, max_events: int | None = None) -> int:
        return self.dispatch_payload_family_events("structured_event", callback, max_events=max_events)

    def dispatch_tool_deltas(self, callback, *, max_events: int | None = None) -> int:
        return self.dispatch_payload_family_events("tool_delta", callback, max_events=max_events)

    def dispatch_workflow_states(self, callback, *, max_events: int | None = None) -> int:
        return self.dispatch_payload_family_events("workflow_state", callback, max_events=max_events)

    def close(self) -> None:
        self.closed = True
        for session in self.sessions:
            session.closed = True


class LegacyFakeConnection:
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
        self.control_calls: list[tuple[int, bytes | bytearray | memoryview]] = []

    def close(self) -> None:
        self.closed = True

    def submit_operation(
        self,
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes = b"",
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
    ) -> FakeOperation:
        operation = FakeOperation(
            session_id=self.requested_session_id,
            operation_id=operation_id,
            frame_id=frame_id,
            payload=payload,
        )
        operation.parent_operation_id = parent_operation_id
        operation.operation_group_id = operation_group_id
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

    def control(self, *, control_code: int, payload: bytes | bytearray | memoryview = b"") -> None:
        self.control_calls.append((control_code, payload))


class FakeOperation:
    def __init__(self, *, session_id: int, operation_id: int, frame_id: int, payload: bytes) -> None:
        self.session_id = session_id
        self.operation_id = operation_id
        self.frame_id = frame_id
        self.payload = payload
        self.cancelled = False
        self.parent_operation_id: int | None = None
        self.operation_group_id: int | None = None

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
    assert backend.connections[0].closed is True
    assert first.closed is True
    assert second.closed is True


def test_native_client_connection_rejects_use_after_close() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        pass

    with pytest.raises(RuntimeError, match="closed"):
        connection.open_session()


def test_native_client_connection_falls_back_to_session_close_without_connection_close() -> None:
    backend = LegacyFakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        first = connection.open_session(NativeClientSessionOpenOptions(requested_session_id=10))
        second = connection.open_session(NativeClientSessionOpenOptions(requested_session_id=11))

    assert first.closed is True
    assert second.closed is True


def test_native_client_connection_delegates_callback_dispatch() -> None:
    backend = FakeBackend()
    callbacks: list[object] = []

    with connect_native_client_connection(backend=backend) as connection:
        assert connection.dispatch_events(callbacks.append, max_events=2, event_kind=6) == 1
        assert connection.dispatch_credit_updates(callbacks.append, max_events=3) == 1
        assert connection.dispatch_structured_events(callbacks.append, max_events=4) == 1
        assert connection.dispatch_tool_deltas(callbacks.append, max_events=5) == 1
        assert connection.dispatch_workflow_states(callbacks.append, max_events=6) == 1
        assert (
            connection.dispatch_payload_family_events(
                "structured_event",
                callbacks.append,
                max_events=7,
                event_kind=9,
            )
            == 1
        )

    assert callbacks == [
        "event",
        "credit",
        "structured_event",
        "tool_delta",
        "workflow_state",
        "structured_event",
    ]
    assert backend.connections[0].dispatch_calls == [
        ("events", 2, 6),
        ("credit_updates", 3, None),
        ("structured_event", 4, None),
        ("tool_delta", 5, None),
        ("workflow_state", 6, None),
        ("structured_event", 7, 9),
    ]


def test_native_client_connection_supports_operation_cancellation() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = connection.open_session()
        operation = session.submit_operation(operation_id=100, frame_id=7, payload=b"payload")

        connection.cancel_operation(operation)
        connection.cancel_frame(session, frame_id=7)

        assert operation.cancelled is True
        assert session.cancelled_frames == [7]


def test_native_client_connection_operation_scope_cancels_on_error() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = connection.open_session()
        operation = session.submit_operation(operation_id=100, frame_id=7, payload=b"payload")
        scope = connection.operation_scope(operation)

        assert isinstance(scope, NativeClientOperationScope)

        with pytest.raises(RuntimeError, match="boom"):
            with scope as scoped_operation:
                assert scoped_operation is operation
                raise RuntimeError("boom")

        assert operation.cancelled is True


def test_native_client_connection_submits_and_polls_result() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = connection.open_session()

        result = connection.submit_and_poll_result(
            session,
            operation_id=100,
            frame_id=7,
            payload=b"payload",
            parent_operation_id=99,
            operation_group_id=1234,
            max_events=4,
        )

        assert result.session_id == 1
        assert result.operation_id == 100
        assert result.frame_id == 7
        assert result.payload == b"payload"
        assert result.max_events == 4
        assert session.operations[0].parent_operation_id == 99
        assert session.operations[0].operation_group_id == 1234


def test_native_client_connection_sends_control_to_connection_and_session() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as client_connection:
        session = client_connection.open_session()

        client_connection.send_control(client_connection.connection, control_code=10, payload=b"connection")
        client_connection.send_control(session, control_code=11, payload=b"session")

        assert backend.connections[0].control_calls == [(10, b"connection")]
        assert session.control_calls == [(11, b"session")]


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
