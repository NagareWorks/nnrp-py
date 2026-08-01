from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import nnrp.client.native as client_native_module
from nnrp._native_routes import official_provider_metadata
from nnrp.client import (
    NativeClientConnectionOptions,
    NativeClientOperationScope,
    NativeClientProviderRoute,
    NativeClientSessionOpenOptions,
    NativeClientSessionOptions,
    SubmitIdentity,
    SubmitPolicy,
    SubmitRequest,
    TokenChunk,
    TokenSubmitInput,
    select_client_native_backend,
)
from nnrp.client import (
    connect_native_client_connection as _connect_native_client_connection,
)
from nnrp.client import (
    connect_native_client_session as _connect_native_client_session,
)
from nnrp.core import MessageType
from nnrp.native import (
    HANDLE_KIND_TRANSPORT_CONNECTION,
    NATIVE_TRANSPORT_ID_BY_NAME,
    NativeArtifactError,
    NativeHandle,
    NativeStatus,
    NativeTransportCandidateDiagnostic,
    NativeTransportClientSecurity,
    NativeTransportConnection,
    NativeTransportProbeMetrics,
    NativeTransportProbeState,
    NativeTransportRejectionReason,
    NativeTransportSelection,
    NativeTransportSelectionError,
    NativeTransportSelectionErrorCode,
    NativeWouldBlockError,
    parse_native_transport_endpoint,
)
from nnrp.runtime import (
    BudgetMetadata,
    CapabilityMetadata,
    ControlRequestMetadata,
    ResultDropReasonCode,
    RouteHintMetadata,
    RuntimeRole,
    SchedulingMetadata,
    SupersedeMetadata,
    decode_runtime_control_metadata,
    encode_runtime_control_metadata,
)
from nnrp.schema import TOKEN_DELTA_SCHEMA_ID, TOKEN_DELTA_SCHEMA_VERSION, StandardProfile


class FakeTransportEntrypoints:
    def __init__(self) -> None:
        self.closed_handles: list[int] = []

    def close(self, handle) -> object:
        self.closed_handles.append(int(handle.id))
        return NativeStatus.ok().to_ffi()


class FakeTransportBinding:
    def __init__(
        self,
        transport_name: str = "tcp",
        *,
        local_available: bool = True,
        diagnostic: str | None = None,
        provider_id: str | None = None,
    ) -> None:
        self.transport_name = transport_name
        self._local_available = local_available
        self._diagnostic = diagnostic
        self._provider_id = provider_id
        self.entrypoints = FakeTransportEntrypoints()
        self.connections: list[NativeTransportConnection] = []
        self.connect_security: list[object] = []

    @property
    def kind(self) -> str:
        return self.transport_name

    @property
    def provider(self):
        provider = fake_provider(self.transport_name)
        if self._provider_id is not None:
            provider.metadata = replace(provider.metadata, id=self._provider_id)
        return provider

    @property
    def local_available(self) -> bool:
        return self._local_available

    @property
    def diagnostic(self) -> str | None:
        return self._diagnostic

    def _probe(self, *_args) -> NativeTransportProbeMetrics:
        assert self.local_available
        return NativeTransportProbeMetrics(1, 1, 4096, 12)

    def _connect(self, endpoint, security, connect_timeout_ms: int, io_timeout_ms: int) -> NativeTransportConnection:
        assert self.local_available
        assert (connect_timeout_ms, io_timeout_ms) == (0, 0)
        self.connect_security.append(security)
        connection = NativeTransportConnection(
            self.entrypoints,
            SimpleNamespace(name=self.transport_name),
            endpoint,
            NativeHandle(HANDLE_KIND_TRANSPORT_CONNECTION, len(self.connections) + 1, 1, 0),
        )
        self.connections.append(connection)
        return connection


@contextmanager
def connect_native_client_connection(*, backend: FakeBackend, options=None):
    binding = FakeTransportBinding()
    selection = native_selection("tcp")
    route = SimpleNamespace(
        endpoint=parse_native_transport_endpoint("tcp://localhost:4433"),
        security=None,
    )
    with (
        patch.object(client_native_module, "_select_client_transport", return_value=(selection, route, binding)),
        _connect_native_client_connection(
            "nnrp://localhost",
            fallback=backend,
            options=options,
        ) as connection,
    ):
        yield connection


@contextmanager
def connect_native_client_session(*, backend: FakeBackend, options=None):
    binding = FakeTransportBinding()
    selection = native_selection("tcp")
    route = SimpleNamespace(
        endpoint=parse_native_transport_endpoint("tcp://localhost:4433"),
        security=None,
    )
    with (
        patch.object(client_native_module, "_select_client_transport", return_value=(selection, route, binding)),
        _connect_native_client_session(
            "nnrp://localhost",
            fallback=backend,
            options=options,
        ) as session,
    ):
        yield session


class FakeBackend:
    def __init__(self) -> None:
        self.connections: list[FakeConnection] = []

    def connect(
        self,
        *,
        connection_id: int,
        generation: int,
        transport_connection: NativeTransportConnection,
    ) -> FakeConnection:
        connection = FakeConnection(connection_id, generation, transport_connection)
        self.connections.append(connection)
        return connection


class FakeConnection:
    def __init__(
        self,
        connection_id: int,
        generation: int,
        transport_connection: NativeTransportConnection,
    ) -> None:
        self.connection_id = connection_id
        self.generation = generation
        self.transport_connection = transport_connection
        self.sessions: list[FakeSession] = []
        self.control_calls: list[tuple[int, bytes | bytearray | memoryview]] = []
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

    def _send_runtime_frame(self, message_type, metadata, tail=b"") -> None:
        self.control_calls.append(
            (int(message_type), encode_runtime_control_metadata(message_type, metadata, tail=bytes(tail)))
        )

    def close(self) -> None:
        self.closed = True
        self.transport_connection._close()
        for session in self.sessions:
            session.closed = True


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
        self.flow_updates: list[int] = []
        self.result_hints: list[bytes | bytearray | memoryview] = []
        self.dispatch_calls: list[tuple[str, int | None, int | None]] = []
        self.submit_operation_calls = 0
        self.submit_result_calls = 0
        self.poll_result_calls = 0

    def close(self) -> None:
        self.closed = True

    def submit_operation(
        self,
        request: SubmitRequest,
        *,
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
    ) -> FakeOperation:
        self.submit_operation_calls += 1
        operation = FakeOperation(
            session_id=self.requested_session_id,
            operation_id=request.operation_id,
            frame_id=request.frame_id,
            body=request.body,
        )
        operation.parent_operation_id = parent_operation_id
        operation.operation_group_id = operation_group_id
        self.operations.append(operation)
        return operation

    def poll_result(
        self,
        operation: FakeOperation,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> FakeResult:
        self.poll_result_calls += 1
        return FakeResult(
            session_id=self.requested_session_id,
            operation_id=operation.operation_id,
            frame_id=operation.frame_id,
            body=operation.body,
            max_events=max_events,
            timeout_ms=timeout_ms,
        )

    def cancel(self, *, frame_id: int) -> None:
        self.cancelled_frames.append(frame_id)

    def _send_runtime_frame(self, message_type, metadata, tail=b"") -> None:
        self.control_calls.append(
            (int(message_type), encode_runtime_control_metadata(message_type, metadata, tail=bytes(tail)))
        )

    def dispatch_events(self, callback, *, max_events: int | None = None, event_kind: int | None = None) -> int:
        self.dispatch_calls.append(("events", max_events, event_kind))
        callback("event")
        return 1

    def dispatch_credit_updates(self, callback, *, max_events: int | None = None) -> int:
        self.dispatch_calls.append(("credit_updates", max_events, None))
        callback("credit")
        return 1

    def dispatch_result_hints(self, callback, *, max_events: int | None = None) -> int:
        self.dispatch_calls.append(("result_hints", max_events, None))
        callback("hint")
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


class FakeOperation:
    def __init__(self, *, session_id: int, operation_id: int, frame_id: int, body: bytes) -> None:
        self.session_id = session_id
        self.operation_id = operation_id
        self.frame_id = frame_id
        self.body = body
        self.cancelled = False
        self.completed_payloads: list[bytes | bytearray | memoryview] = []
        self.dropped = False
        self.parent_operation_id: int | None = None
        self.operation_group_id: int | None = None

    def cancel(self) -> None:
        self.cancelled = True

    def complete(self, payload: bytes | bytearray | memoryview = b"") -> None:
        self.completed_payloads.append(payload)

    def drop(self) -> None:
        self.dropped = True


class FakeResult:
    def __init__(
        self,
        *,
        session_id: int,
        operation_id: int,
        frame_id: int,
        body: bytes,
        max_events: int | None,
        timeout_ms: int = 0,
    ) -> None:
        self.session_id = session_id
        self.operation_id = operation_id
        runtime_event = SimpleNamespace(header=SimpleNamespace(frame_id=frame_id), tail=SimpleNamespace(body=body))
        self.event = SimpleNamespace(as_runtime=lambda: runtime_event)
        self.max_events = max_events
        self.timeout_ms = timeout_ms


class FakeNativeHandle:
    def __init__(self, id: int) -> None:
        self.id = id


class FakeNativeHandleOwner:
    def __init__(self, id: int) -> None:
        self.handle = FakeNativeHandle(id)


class FakeNativeSessionIdentity:
    def __init__(self, id: int) -> None:
        self.handle = FakeNativeHandleOwner(id)


class FakeNativeOperationIdentity:
    def __init__(self, *, session_id: int, operation_id: int, frame_id: int) -> None:
        self.session = FakeNativeSessionIdentity(session_id)
        self.operation_id = operation_id
        self.frame_id = frame_id
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeDirectHandleSession:
    def __init__(self, id: int) -> None:
        self.handle = FakeNativeHandle(id)
        self.cancelled_frames: list[int] = []

    def cancel(self, *, frame_id: int) -> None:
        self.cancelled_frames.append(frame_id)


class FakeAnonymousSession:
    def __init__(self) -> None:
        self.poll_result_calls = 0

    def poll_result(
        self,
        operation: FakeOperation,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> FakeResult:
        self.poll_result_calls += 1
        return FakeResult(
            session_id=0,
            operation_id=operation.operation_id,
            frame_id=operation.frame_id,
            body=operation.body,
            max_events=max_events,
            timeout_ms=timeout_ms,
        )


def _submit_request(
    operation_id: int,
    frame_id: int,
    body: bytes = b"payload",
) -> SubmitRequest:
    return SubmitRequest.token(
        TokenSubmitInput(
            identity=SubmitIdentity(operation_id=operation_id, frame_id=frame_id),
            policy=SubmitPolicy(),
            chunks=(TokenChunk(body),),
        )
    )


def test_connect_native_client_session_opens_and_closes_host_session() -> None:
    backend = FakeBackend()
    options = NativeClientSessionOptions(
        connection_id=7,
        connection_generation=2,
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


def test_native_session_open_defaults_match_rust_token_profile() -> None:
    backend = FakeBackend()

    with connect_native_client_session(backend=backend) as session:
        assert session.profile_id == StandardProfile.TOKEN
        assert session.schema_id == TOKEN_DELTA_SCHEMA_ID
        assert session.schema_version == TOKEN_DELTA_SCHEMA_VERSION

    with connect_native_client_connection(backend=backend) as connection:
        session = connection.open_session()
        assert session.profile_id == StandardProfile.TOKEN
        assert session.schema_id == TOKEN_DELTA_SCHEMA_ID
        assert session.schema_version == TOKEN_DELTA_SCHEMA_VERSION


def test_connect_native_client_connection_routes_results_for_multiple_sessions() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(
        backend=backend,
        options=NativeClientConnectionOptions(connection_id=7, connection_generation=2),
    ) as connection:
        first = connection.open_session(NativeClientSessionOpenOptions(requested_session_id=10))
        second = connection.open_session(NativeClientSessionOpenOptions(requested_session_id=11))
        first_request = _submit_request(100, 1, b"first")
        second_request = _submit_request(101, 2, b"second")
        first_operation = first.submit_operation(first_request)
        second_operation = second.submit_operation(second_request)

        first_result = connection.poll_result(first, first_operation, max_events=4)
        second_result = connection.poll_result(second, second_operation, max_events=4)

        assert first_result.session_id == 10
        assert first_result.event.as_runtime().tail.body == first_request.body
        assert first_result.max_events == 4
        assert second_result.session_id == 11
        assert second_result.event.as_runtime().tail.body == second_request.body

    assert backend.connections[0].connection_id == 7
    assert backend.connections[0].closed is True
    assert first.closed is True
    assert second.closed is True


def test_connect_native_client_connection_passes_carrier_ownership_to_backend() -> None:
    backend = FakeBackend()

    with connect_native_client_connection(backend=backend) as connection:
        carrier = backend.connections[0].transport_connection
        assert carrier.connected is True
        assert connection.active_transport_name == "tcp"
        assert connection.transport_selection.selected_provider.name == "tcp"

    assert carrier.connected is False


def test_native_client_connection_rejects_use_after_close() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        pass

    with pytest.raises(RuntimeError, match="closed"):
        connection.open_session()


def test_native_client_session_owns_callback_dispatch() -> None:
    backend = FakeBackend()
    callbacks: list[object] = []

    with connect_native_client_connection(backend=backend) as connection:
        session = connection.open_session()
        assert not hasattr(connection, "dispatch_events")
        assert session.dispatch_events(callbacks.append, max_events=2, event_kind=6) == 1
        assert session.dispatch_credit_updates(callbacks.append, max_events=3) == 1
        assert session.dispatch_result_hints(callbacks.append, max_events=8) == 1
        assert session.dispatch_structured_events(callbacks.append, max_events=4) == 1
        assert session.dispatch_tool_deltas(callbacks.append, max_events=5) == 1
        assert session.dispatch_workflow_states(callbacks.append, max_events=6) == 1
        assert (
            session.dispatch_payload_family_events(
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
        "hint",
        "structured_event",
        "tool_delta",
        "workflow_state",
        "structured_event",
    ]
    assert backend.connections[0].sessions[0].dispatch_calls == [
        ("events", 2, 6),
        ("credit_updates", 3, None),
        ("result_hints", 8, None),
        ("structured_event", 4, None),
        ("tool_delta", 5, None),
        ("workflow_state", 6, None),
        ("structured_event", 7, 9),
    ]


def test_native_client_connection_supports_operation_cancellation() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = connection.open_session()
        operation = session.submit_operation(_submit_request(100, 7))

        connection.cancel_operation(operation)
        connection.cancel_frame(session, frame_id=7)

        assert operation.cancelled is True
        assert session.cancelled_frames == [7]


def test_native_client_connection_suppresses_cancelled_operation_results() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = connection.open_session()
        operation = session.submit_operation(_submit_request(100, 7))

        connection.cancel_operation(operation)

        with pytest.raises(NativeWouldBlockError):
            connection.poll_result(session, operation)

        assert operation.cancelled is True
        assert session.poll_result_calls == 0


def test_native_client_connection_suppresses_runtime_cancelled_operation_results() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = connection.open_session()
        operation = session.submit_operation(_submit_request(201, 9))

        connection.cancel_runtime_operation(session, operation_id=201, control_sequence=1)

        with pytest.raises(NativeWouldBlockError):
            connection.poll_result(session, operation)

        assert session.control_calls[0][0] == int(MessageType.CANCEL)
        assert session.poll_result_calls == 0


def test_native_client_connection_suppresses_cancelled_frame_results() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = connection.open_session()
        operation = session.submit_operation(_submit_request(100, 7))

        connection.cancel_frame(session, frame_id=7)

        with pytest.raises(NativeWouldBlockError):
            connection.poll_result(session, operation)

        assert session.cancelled_frames == [7]
        assert session.poll_result_calls == 0


def test_native_client_connection_bounds_cancelled_result_suppressions(monkeypatch) -> None:
    monkeypatch.setattr(client_native_module, "_MAX_CANCELLED_RESULT_SUPPRESSIONS_PER_SESSION", 2)
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = connection.open_session()

        connection.cancel_frame(session, frame_id=1)
        connection.cancel_frame(session, frame_id=2)
        connection.cancel_frame(session, frame_id=3)
        connection.cancel_runtime_operation(session, operation_id=10, control_sequence=1)
        connection.cancel_runtime_operation(session, operation_id=11, control_sequence=2)
        connection.cancel_runtime_operation(session, operation_id=12, control_sequence=3)

        assert list(connection._cancelled_frames[session.requested_session_id]) == [2, 3]
        assert list(connection._cancelled_operations[session.requested_session_id]) == [11, 12]


def test_native_client_connection_clears_cancelled_result_suppressions_on_close() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = connection.open_session()
        connection.cancel_frame(session, frame_id=7)
        connection.cancel_runtime_operation(session, operation_id=100, control_sequence=1)

        assert connection._cancelled_frames
        assert connection._cancelled_operations
        connection.close()

        assert connection._cancelled_frames == {}
        assert connection._cancelled_operations == {}


def test_native_client_connection_closes_sessions_before_connection() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = connection.open_session()
        close_connection = connection.connection.close

        def assert_session_closed_before_connection() -> None:
            assert session.closed
            close_connection()

        connection.connection.close = assert_session_closed_before_connection


def test_native_client_connection_suppresses_late_result_after_poll() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = connection.open_session()
        operation = session.submit_operation(_submit_request(100, 7))

        def poll_late_result(
            _operation: FakeOperation,
            *,
            max_events: int | None = None,
            timeout_ms: int = 0,
        ) -> FakeResult:
            session.poll_result_calls += 1
            return FakeResult(
                session_id=session.requested_session_id,
                operation_id=101,
                frame_id=8,
                body=b"late",
                max_events=max_events,
                timeout_ms=timeout_ms,
            )

        session.poll_result = poll_late_result
        connection.cancel_frame(session, frame_id=8)

        with pytest.raises(NativeWouldBlockError):
            connection.poll_result(session, operation)

        assert session.poll_result_calls == 1


def test_native_client_connection_tracks_native_handle_cancel_identity() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = FakeNativeSessionIdentity(77)
        operation = FakeNativeOperationIdentity(session_id=77, operation_id=100, frame_id=7)

        connection.cancel_operation(operation)

        assert operation.cancelled is True
        assert connection._is_cancelled_result(session, operation_id=100, frame_id=7)


def test_native_client_connection_tracks_direct_handle_cancel_identity() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = FakeDirectHandleSession(88)

        connection.cancel_frame(session, frame_id=7)

        assert session.cancelled_frames == [7]
        assert connection._is_cancelled_result(session, operation_id=100, frame_id=7)


def test_native_client_connection_allows_unknown_session_identity_results() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = FakeAnonymousSession()
        operation = FakeOperation(session_id=0, operation_id=100, frame_id=7, body=b"payload")

        result = connection.poll_result(session, operation)

        assert result.event.as_runtime().tail.body == b"payload"
        assert session.poll_result_calls == 1


def test_native_client_connection_operation_scope_cancels_on_error() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as connection:
        session = connection.open_session()
        operation = session.submit_operation(_submit_request(100, 7))
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

        request = _submit_request(100, 7)
        result = connection.submit_and_poll_result(
            session,
            request,
            parent_operation_id=99,
            operation_group_id=1234,
            max_events=4,
        )

        assert result.session_id == 1
        assert result.operation_id == 100
        assert result.event.as_runtime().header.frame_id == 7
        assert result.event.as_runtime().tail.body == request.body
        assert result.max_events == 4
        assert session.operations[0].parent_operation_id == 99
        assert session.operations[0].operation_group_id == 1234


def test_native_client_connection_does_not_expose_raw_control() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as client_connection:
        session = client_connection.open_session()

        assert not hasattr(client_connection, "send_control")
        assert not hasattr(session, "control")


def test_native_client_connection_sends_runtime_control_helpers() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as client_connection:
        session = client_connection.open_session()

        client_connection.cancel_runtime_operation(
            session,
            operation_id=100,
            control_sequence=1,
            reason_code=7,
            source_role=RuntimeRole.CLIENT,
            diagnostic=b"cancel",
            flags=0x03,
        )
        client_connection.abort_runtime_operation(
            session,
            operation_id=100,
            control_sequence=2,
            reason_code=8,
            source_role=RuntimeRole.SCHEDULER,
        )
        client_connection.update_runtime_priority(
            session,
            operation_id=100,
            control_sequence=3,
            priority_class=2,
            priority_delta=-4,
            flags=0x01,
        )
        client_connection.update_runtime_deadline(
            session,
            operation_id=100,
            control_sequence=4,
            deadline_unix_ms=1_800_000_000_000,
            priority_class=3,
        )
        client_connection.expire_runtime_operation_at(
            session,
            operation_id=100,
            control_sequence=5,
            expire_at_unix_ms=1_800_000_010_000,
        )
        client_connection.supersede_runtime_operation(
            session,
            old_operation_id=100,
            new_operation_id=101,
            control_sequence=6,
            diagnostic=b"replace",
        )
        client_connection.update_runtime_budget(
            session,
            operation_id=101,
            compute_budget_units=11,
            memory_budget_bytes=22,
            bandwidth_budget_bytes=33,
            token_budget=44,
            flags=0x02,
        )
        client_connection.send_runtime_route_hint(
            session,
            operation_id=101,
            route_id=55,
            executor_class=6,
            affinity_class=7,
            deadline_unix_ms=1_800_000_020_000,
            body=b"route",
        )
        client_connection.send_runtime_execution_hint(
            session,
            operation_id=101,
            route_id=56,
            executor_class=8,
            affinity_class=9,
            body=b"exec",
            flags=0x01,
        )
        client_connection.negotiate_runtime_capabilities(
            session,
            profile_id=3,
            capability_count=2,
            cost_model_id=4,
            preference_rank=1,
            limit_bytes=99,
            limit_units=88,
            body=b"profiles",
        )
        client_connection.degrade_runtime_profile(
            session,
            profile_id=3,
            capability_count=1,
            cost_model_id=5,
            preference_rank=9,
            limit_bytes=77,
            limit_units=66,
            body=b"degrade",
            flags=0x02,
        )

    assert [control_code for control_code, _ in session.control_calls] == [
        int(MessageType.CANCEL),
        int(MessageType.ABORT),
        int(MessageType.PRIORITY_UPDATE),
        int(MessageType.DEADLINE),
        int(MessageType.EXPIRE_AT),
        int(MessageType.SUPERSEDE),
        int(MessageType.BUDGET_UPDATE),
        int(MessageType.ROUTE_HINT),
        int(MessageType.EXECUTION_HINT),
        int(MessageType.CAPABILITY_NEGOTIATION),
        int(MessageType.DEGRADE_PROFILE),
    ]
    cancel = decode_runtime_control_metadata(MessageType.CANCEL, session.control_calls[0][1])
    assert cancel.metadata == ControlRequestMetadata(100, 1, 7, RuntimeRole.CLIENT, 0x03, 6)
    assert cancel.tail == b"cancel"
    abort = decode_runtime_control_metadata(MessageType.ABORT, session.control_calls[1][1])
    assert abort.metadata == ControlRequestMetadata(100, 2, 8, RuntimeRole.SCHEDULER, 0, 0)
    priority = decode_runtime_control_metadata(MessageType.PRIORITY_UPDATE, session.control_calls[2][1])
    assert priority.metadata == SchedulingMetadata(100, 3, 2, -4, 0, 0x01)
    deadline = decode_runtime_control_metadata(MessageType.DEADLINE, session.control_calls[3][1])
    assert deadline.metadata == SchedulingMetadata(100, 4, 3, 0, 1_800_000_000_000, 0)
    expire_at = decode_runtime_control_metadata(MessageType.EXPIRE_AT, session.control_calls[4][1])
    assert expire_at.metadata == SchedulingMetadata(100, 5, 0, 0, 1_800_000_010_000, 0)
    supersede = decode_runtime_control_metadata(MessageType.SUPERSEDE, session.control_calls[5][1])
    assert supersede.metadata == SupersedeMetadata(
        100,
        101,
        6,
        ResultDropReasonCode.SUPERSEDED,
        0,
        7,
    )
    assert supersede.tail == b"replace"
    budget = decode_runtime_control_metadata(MessageType.BUDGET_UPDATE, session.control_calls[6][1])
    assert budget.metadata == BudgetMetadata(101, 11, 22, 33, 44, 0x02)

    route = decode_runtime_control_metadata(MessageType.ROUTE_HINT, session.control_calls[7][1])
    assert route.metadata == RouteHintMetadata(101, 55, 6, 7, 1_800_000_020_000, 5, 0)
    assert route.tail == b"route"
    execution = decode_runtime_control_metadata(
        MessageType.EXECUTION_HINT,
        session.control_calls[8][1],
    )
    assert execution.metadata == RouteHintMetadata(101, 56, 8, 9, 0, 4, 0x01)
    assert execution.tail == b"exec"
    capabilities = decode_runtime_control_metadata(
        MessageType.CAPABILITY_NEGOTIATION,
        session.control_calls[9][1],
    )
    assert capabilities.metadata == CapabilityMetadata(3, 2, 4, 1, 99, 88, 8, 0)
    assert capabilities.tail == b"profiles"
    degrade = decode_runtime_control_metadata(MessageType.DEGRADE_PROFILE, session.control_calls[10][1])
    assert degrade.metadata == CapabilityMetadata(3, 1, 5, 9, 77, 66, 7, 0x02)
    assert degrade.tail == b"degrade"


def test_native_client_connection_keeps_runtime_controls_adjacent() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as client_connection:
        session = client_connection.open_session()

        client_connection.cancel_runtime_operation(
            session,
            operation_id=42,
            control_sequence=7,
            reason_code=ResultDropReasonCode.PEER_CANCELLED,
            source_role=RuntimeRole.CLIENT,
            diagnostic=b"cancelled",
        )

    cancel = decode_runtime_control_metadata(MessageType.CANCEL, session.control_calls[0][1])

    assert [control_code for control_code, _ in session.control_calls] == [int(MessageType.CANCEL)]
    assert cancel.metadata == ControlRequestMetadata(
        42,
        7,
        ResultDropReasonCode.PEER_CANCELLED,
        RuntimeRole.CLIENT,
        0,
        9,
    )
    assert cancel.tail == b"cancelled"


def test_native_client_connection_keeps_hot_paths_on_coarse_runtime_calls() -> None:
    backend = FakeBackend()
    with connect_native_client_connection(backend=backend) as client_connection:
        session = client_connection.open_session()

        client_connection.submit_and_poll_result(
            session,
            _submit_request(200, 1, b"submit"),
            timeout_ms=25,
        )
        operation = session.submit_operation(_submit_request(201, 2, b"submit"))
        client_connection.poll_result(session, operation)
        client_connection.cancel_runtime_operation(session, operation_id=201, control_sequence=1)

    assert session.submit_operation_calls == 2
    assert session.poll_result_calls == 2
    assert len(session.control_calls) == 1
    assert session.control_calls[0][0] == int(MessageType.CANCEL)


def test_select_client_native_backend_can_require_native(tmp_path: Path) -> None:
    with pytest.raises(NativeArtifactError):
        select_client_native_backend(tmp_path / "missing.dll", fallback=FakeBackend(), require_native=True)


def test_select_client_native_backend_uses_fallback_when_artifact_missing(tmp_path: Path) -> None:
    fallback = FakeBackend()

    backend = select_client_native_backend(tmp_path / "missing.dll", fallback=fallback)

    assert backend is fallback


def test_connect_native_client_connection_does_not_replace_missing_carrier_with_role_fallback(tmp_path: Path) -> None:
    fallback = FakeBackend()

    with pytest.raises(NativeArtifactError):
        with _connect_native_client_connection(
            "nnrp://localhost",
            transport_policy="force_tcp",
            root=tmp_path,
            fallback=fallback,
        ):
            pass

    assert fallback.connections == []


def test_connect_native_client_session_can_require_native_artifact(tmp_path: Path) -> None:
    with pytest.raises(NativeArtifactError):
        with _connect_native_client_session(
            "nnrp://localhost",
            transport_policy="force_tcp",
            root=tmp_path,
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


def native_candidate(
    transport_name: str,
    rejection_reason: NativeTransportRejectionReason | None = None,
) -> NativeTransportCandidateDiagnostic:
    return NativeTransportCandidateDiagnostic(
        transport_name=transport_name,
        transport_id=NATIVE_TRANSPORT_ID_BY_NAME[transport_name],
        provider=official_provider_metadata(transport_name),
        local_available=True,
        peer_supported=True,
        within_limits=True,
        probe_state=(
            NativeTransportProbeState.MISSING
            if rejection_reason is NativeTransportRejectionReason.PROBE_MISSING
            else NativeTransportProbeState.NOT_RUN
        ),
        rejection_reason=rejection_reason,
    )


def fake_provider(transport_name: str):
    return SimpleNamespace(
        name=transport_name,
        metadata=official_provider_metadata(transport_name),
    )


def native_selection(transport_name: str) -> NativeTransportSelection:
    provider = fake_provider(transport_name)
    return NativeTransportSelection(
        selected_provider=provider,
        candidates=(native_candidate(transport_name),),
        policy=client_native_module.TransportPolicy.AUTO,
    )


def test_select_client_transport_returns_resolved_single_route(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SimpleNamespace(name="tcp", metadata=official_provider_metadata("tcp"))
    transport = FakeTransportBinding()
    monkeypatch.setattr(client_native_module, "discover_native_transport_providers", lambda *_args: (provider,))
    monkeypatch.setattr(
        client_native_module,
        "_select_native_transport_provider_from_providers",
        lambda *_args, **_kwargs: SimpleNamespace(
            selected_transport_name="tcp",
            candidates=(native_candidate("tcp"),),
        ),
    )

    selection, route, binding = client_native_module._select_client_transport(
        client_native_module.parse_nnrp_endpoint("nnrp://localhost"),
        provider_routes=None,
        transport_policy="auto",
        artifact_path=None,
        root=None,
        native_platform=None,
        library=None,
        transports=(transport,),
    )

    assert selection.selected_transport_name == "tcp"
    assert binding.kind == "tcp"
    assert route.endpoint == parse_native_transport_endpoint("tcp://localhost:4433")


def test_explicit_client_transport_bindings_are_authoritative(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeTransportBinding()
    monkeypatch.setattr(
        client_native_module,
        "discover_native_transport_providers",
        lambda *_args: (_ for _ in ()).throw(AssertionError("explicit transports must bypass discovery")),
    )

    resolved = client_native_module._resolve_client_transport_bindings(
        (transport,),
        artifact_path=None,
        root=None,
        native_platform=None,
        library=None,
    )

    assert resolved == (transport,)


def test_explicit_client_transport_bindings_reject_duplicate_kind() -> None:
    with pytest.raises(NativeTransportSelectionError) as caught:
        client_native_module._resolve_client_transport_bindings(
            (FakeTransportBinding(), FakeTransportBinding()),
            artifact_path=None,
            root=None,
            native_platform=None,
            library=None,
        )

    assert caught.value.code is NativeTransportSelectionErrorCode.INVALID_EVIDENCE


def test_unavailable_client_binding_preserves_provider_identity_without_probe() -> None:
    provider_id = "example.transport.quic.uninstalled"
    binding = FakeTransportBinding(
        "quic",
        local_available=False,
        diagnostic="provider package is not installed",
        provider_id=provider_id,
    )

    with pytest.raises(NativeTransportSelectionError) as caught:
        client_native_module._select_client_transport(
            client_native_module.parse_nnrp_endpoint("nnrp://localhost"),
            provider_routes=None,
            transport_policy="force_quic",
            artifact_path=None,
            root=None,
            native_platform=None,
            library=None,
            transports=(binding,),
        )

    candidate = next(value for value in caught.value.candidates if value.provider.id == provider_id)
    assert candidate.local_available is False
    assert candidate.rejection_reason is NativeTransportRejectionReason.LOCAL_UNAVAILABLE
    assert candidate.diagnostic == "provider package is not installed"


def test_installed_client_binding_does_not_imply_peer_transport_support() -> None:
    binding = FakeTransportBinding("ipc")

    with pytest.raises(NativeTransportSelectionError) as caught:
        client_native_module._select_client_transport(
            client_native_module.parse_nnrp_endpoint("nnrp://localhost"),
            provider_routes=None,
            transport_policy="force_ipc",
            artifact_path=None,
            root=None,
            native_platform=None,
            library=None,
            transports=(binding,),
        )

    candidate = next(value for value in caught.value.candidates if value.transport_name == "ipc")
    assert candidate.local_available is True
    assert candidate.peer_supported is False
    assert candidate.rejection_reason is NativeTransportRejectionReason.PEER_UNSUPPORTED


def test_explicit_client_provider_route_declares_peer_transport_support() -> None:
    binding = FakeTransportBinding("ipc")

    selection, route, selected_binding = client_native_module._select_client_transport(
        client_native_module.parse_nnrp_endpoint("nnrp://localhost"),
        provider_routes={"ipc": NativeClientProviderRoute(provider_endpoint="unix:///tmp/nnrp.sock")},
        transport_policy="force_ipc",
        artifact_path=None,
        root=None,
        native_platform=None,
        library=None,
        transports=(binding,),
    )

    candidate = next(value for value in selection.candidates if value.transport_name == "ipc")
    assert candidate.peer_supported is True
    assert route.endpoint == parse_native_transport_endpoint("unix:///tmp/nnrp.sock")
    assert selected_binding is binding


def test_connect_client_keeps_security_on_its_selected_route(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SimpleNamespace(name="tcp", metadata=official_provider_metadata("tcp"))
    binding = FakeTransportBinding()
    security = NativeTransportClientSecurity("localhost", b"trusted-cert")
    monkeypatch.setattr(client_native_module, "discover_native_transport_providers", lambda *_args: (provider,))
    monkeypatch.setattr(
        client_native_module,
        "_select_native_transport_provider_from_providers",
        lambda *_args, **_kwargs: SimpleNamespace(
            selected_transport_name="tcp",
            candidates=(native_candidate("tcp"),),
        ),
    )
    monkeypatch.setattr(client_native_module, "load_native_transport_binding", lambda *_args, **_kwargs: binding)

    with _connect_native_client_connection(
        "nnrps://localhost",
        provider_routes={"tcp": NativeClientProviderRoute(security=security)},
        transport_policy="force_tcp",
        fallback=FakeBackend(),
    ):
        pass

    assert binding.connect_security == [security]


def test_select_client_transport_probes_every_eligible_route(monkeypatch: pytest.MonkeyPatch) -> None:
    providers = tuple(
        SimpleNamespace(name=name, metadata=official_provider_metadata(name)) for name in ("tcp", "websocket")
    )
    selected_observations = []
    selected_readiness = []

    def select_provider(*_args, **kwargs):
        selected_readiness.extend(kwargs["candidate_readiness"])
        selected_observations.extend(kwargs["probe_observations"])
        return SimpleNamespace(
            selected_transport_name="tcp",
            candidates=(native_candidate("tcp"), native_candidate("websocket")),
        )

    class FakeProbeBinding:
        def __init__(self, transport_name: str) -> None:
            self.transport_name = transport_name

        @property
        def kind(self) -> str:
            return self.transport_name

        @property
        def provider(self):
            return fake_provider(self.transport_name)

        @property
        def local_available(self) -> bool:
            return True

        @property
        def diagnostic(self) -> None:
            return None

        def _probe(self, *_args):
            return NativeTransportProbeMetrics(
                success_count=2,
                sample_count=3,
                median_rtt_us=12,
                median_throughput_bytes_per_sec=4096,
            )

    monkeypatch.setattr(client_native_module, "_select_native_transport_provider_from_providers", select_provider)
    monkeypatch.setattr(client_native_module, "discover_native_transport_providers", lambda *_args: providers)
    monkeypatch.setattr(
        client_native_module,
        "load_native_transport_binding",
        lambda transport_name, **_kwargs: FakeProbeBinding(transport_name),
    )

    selection, _route, _binding = client_native_module._select_client_transport(
        client_native_module.parse_nnrp_endpoint("nnrp://localhost"),
        provider_routes={
            "websocket": NativeClientProviderRoute(provider_endpoint="ws://localhost/nnrp"),
        },
        transport_policy="auto",
        artifact_path=None,
        root=None,
        native_platform=None,
        library=None,
        transports=tuple(FakeProbeBinding(provider.name) for provider in providers),
    )

    assert selection.selected_transport_name == "tcp"
    assert [(record.transport_id, record.route_resolved) for record in selected_readiness] == [
        (NATIVE_TRANSPORT_ID_BY_NAME["tcp"], True),
        (NATIVE_TRANSPORT_ID_BY_NAME["websocket"], True),
    ]
    assert [(observation.transport_id, observation.state) for observation in selected_observations] == [
        (NATIVE_TRANSPORT_ID_BY_NAME["tcp"], NativeTransportProbeState.SUCCEEDED),
        (NATIVE_TRANSPORT_ID_BY_NAME["websocket"], NativeTransportProbeState.SUCCEEDED),
    ]


def test_select_client_transport_records_probe_failure_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    providers = tuple(
        SimpleNamespace(name=name, metadata=official_provider_metadata(name)) for name in ("tcp", "websocket")
    )
    selected_observations = []

    def select_provider(*_args, **kwargs):
        selected_observations.extend(kwargs["probe_observations"])
        return SimpleNamespace(selected_transport_name="websocket", candidates=())

    class FakeProbeBinding:
        def __init__(self, transport_name: str) -> None:
            self.transport_name = transport_name

        @property
        def kind(self) -> str:
            return self.transport_name

        @property
        def provider(self):
            return fake_provider(self.transport_name)

        @property
        def local_available(self) -> bool:
            return True

        @property
        def diagnostic(self) -> None:
            return None

        def _probe(self, *_args):
            if self.transport_name == "tcp":
                raise RuntimeError("connection refused")
            return NativeTransportProbeMetrics(1, 1, 4096, 12)

    monkeypatch.setattr(client_native_module, "_select_native_transport_provider_from_providers", select_provider)
    monkeypatch.setattr(client_native_module, "discover_native_transport_providers", lambda *_args: providers)
    monkeypatch.setattr(
        client_native_module,
        "load_native_transport_binding",
        lambda transport_name, **_kwargs: FakeProbeBinding(transport_name),
    )

    selection, _route, _binding = client_native_module._select_client_transport(
        client_native_module.parse_nnrp_endpoint("nnrp://localhost"),
        provider_routes={"websocket": NativeClientProviderRoute(provider_endpoint="ws://localhost/nnrp")},
        transport_policy="auto",
        artifact_path=None,
        root=None,
        native_platform=None,
        library=None,
        transports=tuple(FakeProbeBinding(provider.name) for provider in providers),
    )

    assert selection.selected_transport_name == "websocket"
    assert selected_observations[0].state is NativeTransportProbeState.FAILED
    assert selected_observations[0].diagnostic == "transport probe failed: connection refused"


def test_select_client_transport_preserves_invalid_evidence_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SimpleNamespace(name="tcp", metadata=official_provider_metadata("tcp"))
    transport = FakeTransportBinding()
    expected = NativeTransportSelectionError(
        NativeTransportSelectionErrorCode.INVALID_EVIDENCE,
        "duplicate readiness",
    )
    monkeypatch.setattr(client_native_module, "discover_native_transport_providers", lambda *_args: (provider,))
    monkeypatch.setattr(
        client_native_module,
        "_select_native_transport_provider_from_providers",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(expected),
    )

    with pytest.raises(NativeTransportSelectionError) as caught:
        client_native_module._select_client_transport(
            client_native_module.parse_nnrp_endpoint("nnrp://localhost"),
            provider_routes=None,
            transport_policy="auto",
            artifact_path=None,
            root=None,
            native_platform=None,
            library=None,
            transports=(transport,),
        )

    assert caught.value is expected


@pytest.mark.parametrize(
    ("endpoint", "policy", "routes", "expected_reason"),
    [
        (
            "nnrp://localhost",
            "force_ipc",
            None,
            NativeTransportRejectionReason.LOCAL_UNAVAILABLE,
        ),
        (
            "nnrps://localhost",
            "force_tcp",
            None,
            NativeTransportRejectionReason.SECURITY_UNSATISFIED,
        ),
        (
            "nnrps://localhost",
            "force_websocket",
            None,
            NativeTransportRejectionReason.ROUTE_UNRESOLVED,
        ),
    ],
)
def test_select_client_transport_reports_host_route_rejection(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    policy: str,
    routes,
    expected_reason: NativeTransportRejectionReason,
) -> None:
    installed_names = ("tcp", "websocket") if policy != "force_ipc" else ()
    providers = tuple(SimpleNamespace(name=name, metadata=official_provider_metadata(name)) for name in installed_names)
    monkeypatch.setattr(client_native_module, "discover_native_transport_providers", lambda *_args: providers)
    monkeypatch.setattr(
        client_native_module,
        "_select_native_transport_provider_from_providers",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            NativeTransportSelectionError(
                NativeTransportSelectionErrorCode.NO_VIABLE_TRANSPORT,
                "no route",
                candidates=(native_candidate(policy.removeprefix("force_"), expected_reason),)
                if installed_names
                else (),
            )
        ),
    )

    with pytest.raises(NativeArtifactError) as caught:
        client_native_module._select_client_transport(
            client_native_module.parse_nnrp_endpoint(endpoint),
            provider_routes=routes,
            transport_policy=policy,
            artifact_path=None,
            root=None,
            native_platform=None,
            library=None,
            transports=tuple(FakeTransportBinding(name) for name in installed_names),
        )

    forced_name = policy.removeprefix("force_")
    candidate = next(value for value in caught.value.candidates if value.transport_name == forced_name)
    assert candidate.rejection_reason is expected_reason
