"""Client-facing native runtime session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any

from nnrp.native import (
    NativeCreditUpdateCallback,
    NativePayloadFamilyCallback,
    NativePlatform,
    NativeRuntimeBackend,
    NativeRuntimeConnection,
    NativeRuntimeEventCallback,
    NativeRuntimeOperation,
    NativeRuntimeResult,
    NativeRuntimeSession,
    select_native_runtime_backend,
)


@dataclass(frozen=True, slots=True)
class NativeClientSessionOptions:
    connection_id: int = 1
    connection_generation: int = 1
    transport_id: int = 1
    requested_session_id: int = 1
    session_generation: int = 1
    profile_id: int = 0
    schema_id: int = 0
    schema_version: int = 0


@dataclass(frozen=True, slots=True)
class NativeClientConnectionOptions:
    connection_id: int = 1
    connection_generation: int = 1
    transport_id: int = 1


@dataclass(frozen=True, slots=True)
class NativeClientSessionOpenOptions:
    requested_session_id: int = 1
    session_generation: int = 1
    profile_id: int = 0
    schema_id: int = 0
    schema_version: int = 0


@dataclass(slots=True)
class NativeClientOperationScope:
    operation: NativeRuntimeOperation
    cancel_on_error: bool = True
    _closed: bool = field(default=False, init=False, repr=False)

    def __enter__(self) -> NativeRuntimeOperation:
        return self.operation

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.close(cancel=exc_type is not None and self.cancel_on_error)
        return False

    def close(self, *, cancel: bool = False) -> None:
        if self._closed:
            return
        if cancel:
            self.operation.cancel()
        self._closed = True


@dataclass(slots=True)
class NativeClientConnection:
    connection: NativeRuntimeConnection
    _sessions: list[NativeRuntimeSession] = field(default_factory=list, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def open_session(self, options: NativeClientSessionOpenOptions | None = None) -> NativeRuntimeSession:
        self._ensure_open()
        resolved_options = options or NativeClientSessionOpenOptions()
        session = self.connection.open_session(
            requested_session_id=resolved_options.requested_session_id,
            generation=resolved_options.session_generation,
            profile_id=resolved_options.profile_id,
            schema_id=resolved_options.schema_id,
            schema_version=resolved_options.schema_version,
        )
        self._sessions.append(session)
        return session

    def poll_result(
        self,
        session: NativeRuntimeSession,
        operation: NativeRuntimeOperation,
        *,
        max_events: int | None = None,
    ) -> NativeRuntimeResult:
        self._ensure_open()
        return session.poll_result(operation, max_events=max_events)

    def dispatch_events(
        self,
        callback: NativeRuntimeEventCallback,
        *,
        max_events: int | None = None,
        event_kind: int | None = None,
    ) -> int:
        self._ensure_open()
        return self.connection.dispatch_events(callback, max_events=max_events, event_kind=event_kind)

    def dispatch_credit_updates(
        self,
        callback: NativeCreditUpdateCallback,
        *,
        max_events: int | None = None,
    ) -> int:
        self._ensure_open()
        return self.connection.dispatch_credit_updates(callback, max_events=max_events)

    def dispatch_payload_family_events(
        self,
        payload_family: str,
        callback: NativePayloadFamilyCallback,
        *,
        max_events: int | None = None,
        event_kind: int | None = None,
    ) -> int:
        self._ensure_open()
        if event_kind is None:
            return self.connection.dispatch_payload_family_events(
                payload_family,
                callback,
                max_events=max_events,
            )
        return self.connection.dispatch_payload_family_events(
            payload_family,
            callback,
            max_events=max_events,
            event_kind=event_kind,
        )

    def dispatch_structured_events(
        self,
        callback: NativePayloadFamilyCallback,
        *,
        max_events: int | None = None,
    ) -> int:
        self._ensure_open()
        return self.connection.dispatch_structured_events(callback, max_events=max_events)

    def dispatch_tool_deltas(
        self,
        callback: NativePayloadFamilyCallback,
        *,
        max_events: int | None = None,
    ) -> int:
        self._ensure_open()
        return self.connection.dispatch_tool_deltas(callback, max_events=max_events)

    def dispatch_workflow_states(
        self,
        callback: NativePayloadFamilyCallback,
        *,
        max_events: int | None = None,
    ) -> int:
        self._ensure_open()
        return self.connection.dispatch_workflow_states(callback, max_events=max_events)

    def submit_and_poll_result(
        self,
        session: NativeRuntimeSession,
        *,
        operation_id: int,
        frame_id: int,
        payload: bytes | bytearray | memoryview = b"",
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
        max_events: int | None = None,
    ) -> NativeRuntimeResult:
        self._ensure_open()
        operation = session.submit_operation(
            operation_id=operation_id,
            frame_id=frame_id,
            payload=payload,
            parent_operation_id=parent_operation_id,
            operation_group_id=operation_group_id,
        )
        return self.poll_result(session, operation, max_events=max_events)

    def cancel_operation(self, operation: NativeRuntimeOperation) -> None:
        self._ensure_open()
        operation.cancel()

    def operation_scope(
        self,
        operation: NativeRuntimeOperation,
        *,
        cancel_on_error: bool = True,
    ) -> NativeClientOperationScope:
        self._ensure_open()
        return NativeClientOperationScope(operation, cancel_on_error=cancel_on_error)

    def cancel_frame(self, session: NativeRuntimeSession, *, frame_id: int) -> None:
        self._ensure_open()
        session.cancel(frame_id=frame_id)

    def send_control(
        self,
        target: NativeRuntimeConnection | NativeRuntimeSession,
        *,
        control_code: int,
        payload: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._ensure_open()
        target.control(control_code=control_code, payload=payload)

    def close(self) -> None:
        if self._closed:
            return
        if hasattr(self.connection, "close"):
            self.connection.close()
        else:
            for session in reversed(self._sessions):
                if not getattr(session, "_closed", False):
                    session.close()
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("native client connection is closed")


def select_client_native_backend(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    library: Any | None = None,
    fallback: NativeRuntimeBackend | None = None,
    require_native: bool = False,
) -> NativeRuntimeBackend:
    return select_native_runtime_backend(
        artifact_path,
        root=root,
        native_platform=native_platform,
        library=library,
        fallback=fallback,
        require_native=require_native,
    )


@contextmanager
def connect_native_client_connection(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    library: Any | None = None,
    backend: NativeRuntimeBackend | None = None,
    fallback: NativeRuntimeBackend | None = None,
    require_native: bool = False,
    options: NativeClientConnectionOptions | None = None,
) -> Iterator[NativeClientConnection]:
    resolved_options = options or NativeClientConnectionOptions()
    resolved_backend = backend or select_client_native_backend(
        artifact_path,
        root=root,
        native_platform=native_platform,
        library=library,
        fallback=fallback,
        require_native=require_native,
    )
    connection = resolved_backend.connect(
        connection_id=resolved_options.connection_id,
        generation=resolved_options.connection_generation,
        transport_id=resolved_options.transport_id,
    )
    client_connection = NativeClientConnection(connection)
    try:
        yield client_connection
    finally:
        client_connection.close()


@contextmanager
def connect_native_client_session(
    artifact_path: Path | str | None = None,
    *,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    library: Any | None = None,
    backend: NativeRuntimeBackend | None = None,
    fallback: NativeRuntimeBackend | None = None,
    require_native: bool = False,
    options: NativeClientSessionOptions | None = None,
) -> Iterator[NativeRuntimeSession]:
    resolved_options = options or NativeClientSessionOptions()
    connection_options = NativeClientConnectionOptions(
        connection_id=resolved_options.connection_id,
        connection_generation=resolved_options.connection_generation,
        transport_id=resolved_options.transport_id,
    )
    session_options = NativeClientSessionOpenOptions(
        requested_session_id=resolved_options.requested_session_id,
        session_generation=resolved_options.session_generation,
        profile_id=resolved_options.profile_id,
        schema_id=resolved_options.schema_id,
        schema_version=resolved_options.schema_version,
    )
    with connect_native_client_connection(
        artifact_path,
        root=root,
        native_platform=native_platform,
        library=library,
        backend=backend,
        fallback=fallback,
        require_native=require_native,
        options=connection_options,
    ) as client_connection:
        session = client_connection.open_session(session_options)
        yield session
