"""Client-facing native runtime session helpers."""

from __future__ import annotations

import asyncio
import struct
import threading
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Any, Literal, cast

from nnrp._native_routes import (
    apply_host_rejection,
    forced_transport_name,
    normalize_provider_routes,
    normalize_transport_policy,
    ordered_candidates,
    policy_allows,
    selection_error,
    unavailable_candidate,
)
from nnrp.client.transport import SubmitRequest
from nnrp.core import MessageType, TransportPolicy
from nnrp.native import (
    FFI_STATUS_WOULD_BLOCK,
    NATIVE_TRANSPORT_ID_BY_NAME,
    NativeArtifactError,
    NativePlatform,
    NativeRuntimeBackend,
    NativeRuntimeConnection,
    NativeRuntimeOperation,
    NativeRuntimeResult,
    NativeRuntimeSession,
    NativeSessionPriorityClass,
    NativeStatus,
    NativeTransportBinding,
    NativeTransportCandidateDiagnostic,
    NativeTransportCandidateReadiness,
    NativeTransportClientSecurity,
    NativeTransportEndpoint,
    NativeTransportProbeObservation,
    NativeTransportSelection,
    NativeTransportSelectionError,
    NativeTransportSelectionErrorCode,
    NativeWouldBlockError,
    NnrpEndpoint,
    _allocate_native_handle_id,
    _select_native_transport_provider_from_providers,
    discover_native_transport_providers,
    load_native_transport_binding,
    parse_nnrp_endpoint,
    resolve_native_transport_endpoint,
    select_native_runtime_backend,
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
)
from nnrp.runtime.types import _FixedRuntimeMetadata
from nnrp.schema import TOKEN_DELTA_SCHEMA_ID, TOKEN_DELTA_SCHEMA_VERSION, StandardProfile

NativeControlTarget = NativeRuntimeSession

_MAX_CANCELLED_RESULT_SUPPRESSIONS_PER_SESSION = 4096
_RECOVERY_TICKET_PREFIX = struct.Struct("<4sHHIIIQ")
_RECOVERY_TICKET_MAGIC = b"NRTK"
_RECOVERY_TICKET_VERSION = 1
_RECOVERY_TICKET_OPERATION_PRESENT = 0x0001


@dataclass(frozen=True, slots=True, init=False)
class NativeSessionRecoveryTicket:
    session_id: int
    resume_token: bytes
    resume_from_operation_id: int | None
    resume_window_ms: int

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("recovery tickets are created only by the runtime or from_bytes()")

    @classmethod
    def _create(
        cls,
        *,
        session_id: int,
        resume_token: bytes,
        resume_from_operation_id: int | None,
        resume_window_ms: int,
    ) -> NativeSessionRecoveryTicket:
        if not 1 <= session_id <= 0xFFFFFFFF:
            raise ValueError("session_id must be a non-zero u32")
        if not resume_token:
            raise ValueError("resume_token must be non-empty")
        if len(resume_token) > 0xFFFFFFFF:
            raise ValueError("resume_token length must fit in u32")
        if resume_from_operation_id is not None and not 1 <= resume_from_operation_id <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("resume_from_operation_id must be a non-zero u64 when present")
        if not 0 <= resume_window_ms <= 0xFFFFFFFF:
            raise ValueError("resume_window_ms must fit in u32")
        ticket = object.__new__(cls)
        object.__setattr__(ticket, "session_id", session_id)
        object.__setattr__(ticket, "resume_token", bytes(resume_token))
        object.__setattr__(ticket, "resume_from_operation_id", resume_from_operation_id)
        object.__setattr__(ticket, "resume_window_ms", resume_window_ms)
        return ticket

    def to_bytes(self) -> bytes:
        flags = _RECOVERY_TICKET_OPERATION_PRESENT if self.resume_from_operation_id is not None else 0
        return (
            _RECOVERY_TICKET_PREFIX.pack(
                _RECOVERY_TICKET_MAGIC,
                _RECOVERY_TICKET_VERSION,
                flags,
                self.session_id,
                len(self.resume_token),
                self.resume_window_ms,
                self.resume_from_operation_id or 0,
            )
            + self.resume_token
        )

    @classmethod
    def from_bytes(cls, encoded: bytes | bytearray | memoryview) -> NativeSessionRecoveryTicket:
        payload = bytes(encoded)
        if len(payload) < _RECOVERY_TICKET_PREFIX.size:
            raise ValueError("recovery ticket is truncated")
        magic, version, flags, session_id, token_bytes, window_ms, operation_id = _RECOVERY_TICKET_PREFIX.unpack_from(
            payload
        )
        if magic != _RECOVERY_TICKET_MAGIC:
            raise ValueError("recovery ticket magic is invalid")
        if version != _RECOVERY_TICKET_VERSION:
            raise ValueError("recovery ticket version is unsupported")
        if flags & ~_RECOVERY_TICKET_OPERATION_PRESENT:
            raise ValueError("recovery ticket contains reserved flags")
        expected_bytes = _RECOVERY_TICKET_PREFIX.size + token_bytes
        if len(payload) != expected_bytes:
            raise ValueError("recovery ticket length does not match its token length")
        if flags & _RECOVERY_TICKET_OPERATION_PRESENT:
            resume_from_operation_id: int | None = operation_id
        else:
            if operation_id != 0:
                raise ValueError("recovery ticket carries an operation id without its presence flag")
            resume_from_operation_id = None
        return cls._create(
            session_id=session_id,
            resume_token=payload[_RECOVERY_TICKET_PREFIX.size :],
            resume_from_operation_id=resume_from_operation_id,
            resume_window_ms=window_ms,
        )


@dataclass(frozen=True, slots=True)
class NativeClientSessionOptions:
    requested_session_id: int = 0
    profile_id: int = int(StandardProfile.TOKEN)
    schema_id: int = TOKEN_DELTA_SCHEMA_ID
    schema_version: int = TOKEN_DELTA_SCHEMA_VERSION
    priority_class: NativeSessionPriorityClass = NativeSessionPriorityClass.BALANCED
    default_deadline_ms: int = 500
    max_in_flight_operations: int = 4
    lease_ttl_hint_ms: int = 30_000
    allow_resume: bool = False
    resume_token_bytes: int = 0
    cache_hints: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.requested_session_id <= 0xFFFFFFFF:
            raise ValueError("requested_session_id must fit in u32")
        if not 0 <= self.profile_id <= 0xFFFF:
            raise ValueError("profile_id must fit in u16")
        for name, value in (
            ("schema_id", self.schema_id),
            ("schema_version", self.schema_version),
            ("default_deadline_ms", self.default_deadline_ms),
            ("lease_ttl_hint_ms", self.lease_ttl_hint_ms),
            ("resume_token_bytes", self.resume_token_bytes),
        ):
            if not 0 <= value <= 0xFFFFFFFF:
                raise ValueError(f"{name} must fit in u32")
        if not 1 <= self.max_in_flight_operations <= 0xFFFF:
            raise ValueError("max_in_flight_operations must be a non-zero u16")
        if not self.allow_resume and self.resume_token_bytes != 0:
            raise ValueError("resume_token_bytes requires allow_resume")
        normalized_hints = tuple(int(hint) for hint in self.cache_hints)
        if any(not 0 <= hint <= 0xFFFFFFFF for hint in normalized_hints):
            raise ValueError("cache_hints values must fit in u32")
        object.__setattr__(self, "priority_class", NativeSessionPriorityClass(self.priority_class))
        object.__setattr__(self, "cache_hints", normalized_hints)


@dataclass(frozen=True, slots=True)
class NativeClientProviderRoute:
    provider_endpoint: str | NativeTransportEndpoint | None = None
    security: NativeTransportClientSecurity | None = None


@dataclass(frozen=True, slots=True)
class NativeClientOptions:
    endpoint: str | NnrpEndpoint
    provider_routes: Mapping[str, NativeClientProviderRoute] = field(default_factory=dict)
    transport_policy: TransportPolicy = TransportPolicy.AUTO
    session_defaults: NativeClientSessionOptions = field(default_factory=NativeClientSessionOptions)

    def __post_init__(self) -> None:
        endpoint = self.endpoint if isinstance(self.endpoint, NnrpEndpoint) else parse_nnrp_endpoint(self.endpoint)
        routes = MappingProxyType(dict(self.provider_routes))
        if any(not isinstance(route, NativeClientProviderRoute) for route in routes.values()):
            raise TypeError("provider_routes values must be NativeClientProviderRoute")
        if not isinstance(self.session_defaults, NativeClientSessionOptions):
            raise TypeError("session_defaults must be NativeClientSessionOptions")
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "provider_routes", routes)
        object.__setattr__(self, "transport_policy", TransportPolicy(self.transport_policy))


@dataclass(frozen=True, slots=True)
class _ResolvedClientRoute:
    endpoint: NativeTransportEndpoint | None
    security: NativeTransportClientSecurity | None
    route_resolved: bool
    security_satisfied: bool


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
    ) -> Literal[False]:
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
    transport_selection: NativeTransportSelection
    session_defaults: NativeClientSessionOptions = field(default_factory=NativeClientSessionOptions)
    _sessions: list[NativeRuntimeSession] = field(default_factory=list, init=False, repr=False)
    _cancelled_frames: dict[int, dict[int, None]] = field(default_factory=dict, init=False, repr=False)
    _cancelled_operations: dict[int, dict[int, None]] = field(default_factory=dict, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _role_executor: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=1, thread_name_prefix="nnrp-client-role"),
        init=False,
        repr=False,
    )
    _close_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def active_transport_name(self) -> str:
        return self.transport_selection.selected_transport_name

    async def open_session(self, options: NativeClientSessionOptions | None = None) -> NativeRuntimeSession:
        with self._close_lock:
            self._ensure_open()
            pending = self._role_executor.submit(self._open_session, options)
        return await asyncio.wrap_future(pending)

    def _open_session(self, options: NativeClientSessionOptions | None = None) -> NativeRuntimeSession:
        self._ensure_open()
        resolved_options = options or self.session_defaults
        session = self.connection.open_session(
            requested_session_id=resolved_options.requested_session_id,
            profile_id=resolved_options.profile_id,
            schema_id=resolved_options.schema_id,
            schema_version=resolved_options.schema_version,
            priority_class=resolved_options.priority_class,
            default_deadline_ms=resolved_options.default_deadline_ms,
            max_in_flight_operations=resolved_options.max_in_flight_operations,
            lease_ttl_hint_ms=resolved_options.lease_ttl_hint_ms,
            allow_resume=resolved_options.allow_resume,
            resume_token_bytes=resolved_options.resume_token_bytes,
            cache_hints=resolved_options.cache_hints,
        )
        self._sessions.append(session)
        return session

    async def resume_session(
        self,
        ticket: NativeSessionRecoveryTicket,
        options: NativeClientSessionOptions | None = None,
    ) -> NativeRuntimeSession:
        with self._close_lock:
            self._ensure_open()
            pending = self._role_executor.submit(self._resume_session, ticket, options)
        return await asyncio.wrap_future(pending)

    def _resume_session(
        self,
        ticket: NativeSessionRecoveryTicket,
        options: NativeClientSessionOptions | None = None,
    ) -> NativeRuntimeSession:
        self._ensure_open()
        if not isinstance(ticket, NativeSessionRecoveryTicket):
            raise TypeError("ticket must be NativeSessionRecoveryTicket")
        resolved_options = options or self.session_defaults
        session, _outcome = self.connection.resume_session(
            recovery_ticket=ticket.to_bytes(),
            requested_session_id=ticket.session_id,
            profile_id=resolved_options.profile_id,
            schema_id=resolved_options.schema_id,
            schema_version=resolved_options.schema_version,
            priority_class=resolved_options.priority_class,
            default_deadline_ms=resolved_options.default_deadline_ms,
            max_in_flight_operations=resolved_options.max_in_flight_operations,
            lease_ttl_hint_ms=resolved_options.lease_ttl_hint_ms,
            resume_token_bytes=max(resolved_options.resume_token_bytes, len(ticket.resume_token)),
            cache_hints=resolved_options.cache_hints,
        )
        self._sessions.append(session)
        return session

    def poll_result(
        self,
        session: NativeRuntimeSession,
        operation: NativeRuntimeOperation,
        *,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> NativeRuntimeResult:
        self._ensure_open()
        if self._is_cancelled_result(session, operation_id=operation.operation_id, frame_id=operation.frame_id):
            raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))
        result = session.poll_result(operation, max_events=max_events, timeout_ms=timeout_ms)
        runtime_event = result.event.as_runtime()
        result_frame_id = operation.frame_id if runtime_event is None else runtime_event.header.frame_id
        if self._is_cancelled_result(session, operation_id=result.operation_id, frame_id=result_frame_id):
            raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))
        return result

    def submit_and_poll_result(
        self,
        session: NativeRuntimeSession,
        request: SubmitRequest,
        *,
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> NativeRuntimeResult:
        self._ensure_open()
        operation = session.submit_operation(
            request,
            parent_operation_id=parent_operation_id,
            operation_group_id=operation_group_id,
        )
        return self.poll_result(session, operation, max_events=max_events, timeout_ms=timeout_ms)

    def cancel_operation(self, operation: NativeRuntimeOperation) -> None:
        self._ensure_open()
        operation.cancel()
        session_id = self._operation_session_identity(operation)
        if session_id is not None:
            self._remember_cancelled_frame_by_handle(session_id, operation.frame_id)
            self._remember_cancelled_operation_by_handle(session_id, operation.operation_id)

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
        self._remember_cancelled_frame(session, frame_id)

    def cancel_runtime_operation(
        self,
        target: NativeControlTarget,
        *,
        operation_id: int,
        control_sequence: int,
        reason_code: int = 0,
        source_role: RuntimeRole | int = RuntimeRole.CLIENT,
        diagnostic: bytes | bytearray | memoryview = b"",
        flags: int = 0,
    ) -> None:
        self._send_runtime_control_request(
            target,
            MessageType.CANCEL,
            operation_id=operation_id,
            control_sequence=control_sequence,
            reason_code=reason_code,
            source_role=source_role,
            diagnostic=diagnostic,
            flags=flags,
        )
        self._remember_cancelled_operation(target, operation_id)

    def abort_runtime_operation(
        self,
        target: NativeControlTarget,
        *,
        operation_id: int,
        control_sequence: int,
        reason_code: int = 0,
        source_role: RuntimeRole | int = RuntimeRole.CLIENT,
        diagnostic: bytes | bytearray | memoryview = b"",
        flags: int = 0,
    ) -> None:
        self._send_runtime_control_request(
            target,
            MessageType.ABORT,
            operation_id=operation_id,
            control_sequence=control_sequence,
            reason_code=reason_code,
            source_role=source_role,
            diagnostic=diagnostic,
            flags=flags,
        )

    def update_runtime_priority(
        self,
        target: NativeControlTarget,
        *,
        operation_id: int,
        control_sequence: int,
        priority_class: int,
        priority_delta: int = 0,
        flags: int = 0,
    ) -> None:
        self._send_runtime_scheduling(
            target,
            MessageType.PRIORITY_UPDATE,
            operation_id=operation_id,
            control_sequence=control_sequence,
            priority_class=priority_class,
            priority_delta=priority_delta,
            deadline_unix_ms=0,
            flags=flags,
        )

    def update_runtime_deadline(
        self,
        target: NativeControlTarget,
        *,
        operation_id: int,
        control_sequence: int,
        deadline_unix_ms: int,
        priority_class: int = 0,
        priority_delta: int = 0,
        flags: int = 0,
    ) -> None:
        self._send_runtime_scheduling(
            target,
            MessageType.DEADLINE,
            operation_id=operation_id,
            control_sequence=control_sequence,
            priority_class=priority_class,
            priority_delta=priority_delta,
            deadline_unix_ms=deadline_unix_ms,
            flags=flags,
        )

    def expire_runtime_operation_at(
        self,
        target: NativeControlTarget,
        *,
        operation_id: int,
        control_sequence: int,
        expire_at_unix_ms: int,
        priority_class: int = 0,
        priority_delta: int = 0,
        flags: int = 0,
    ) -> None:
        self._send_runtime_scheduling(
            target,
            MessageType.EXPIRE_AT,
            operation_id=operation_id,
            control_sequence=control_sequence,
            priority_class=priority_class,
            priority_delta=priority_delta,
            deadline_unix_ms=expire_at_unix_ms,
            flags=flags,
        )

    def supersede_runtime_operation(
        self,
        target: NativeControlTarget,
        *,
        old_operation_id: int,
        new_operation_id: int,
        control_sequence: int,
        drop_reason_code: ResultDropReasonCode | int = ResultDropReasonCode.SUPERSEDED,
        diagnostic: bytes | bytearray | memoryview = b"",
        flags: int = 0,
    ) -> None:
        metadata = SupersedeMetadata(
            old_operation_id=old_operation_id,
            new_operation_id=new_operation_id,
            control_sequence=control_sequence,
            drop_reason_code=int(drop_reason_code),
            flags=flags,
            diagnostic_bytes=memoryview(diagnostic).nbytes,
        )
        self._send_runtime_control(target, MessageType.SUPERSEDE, metadata, tail=diagnostic)

    def update_runtime_budget(
        self,
        target: NativeControlTarget,
        *,
        operation_id: int,
        compute_budget_units: int = 0,
        memory_budget_bytes: int = 0,
        bandwidth_budget_bytes: int = 0,
        token_budget: int = 0,
        flags: int = 0,
    ) -> None:
        metadata = BudgetMetadata(
            operation_id=operation_id,
            compute_budget_units=compute_budget_units,
            memory_budget_bytes=memory_budget_bytes,
            bandwidth_budget_bytes=bandwidth_budget_bytes,
            token_budget=token_budget,
            flags=flags,
        )
        self._send_runtime_control(target, MessageType.BUDGET_UPDATE, metadata)

    def send_runtime_route_hint(
        self,
        target: NativeControlTarget,
        *,
        operation_id: int,
        route_id: int,
        executor_class: int = 0,
        affinity_class: int = 0,
        deadline_unix_ms: int = 0,
        body: bytes | bytearray | memoryview = b"",
        flags: int = 0,
    ) -> None:
        self._send_runtime_route_control(
            target,
            MessageType.ROUTE_HINT,
            operation_id=operation_id,
            route_id=route_id,
            executor_class=executor_class,
            affinity_class=affinity_class,
            deadline_unix_ms=deadline_unix_ms,
            body=body,
            flags=flags,
        )

    def send_runtime_execution_hint(
        self,
        target: NativeControlTarget,
        *,
        operation_id: int,
        route_id: int,
        executor_class: int = 0,
        affinity_class: int = 0,
        deadline_unix_ms: int = 0,
        body: bytes | bytearray | memoryview = b"",
        flags: int = 0,
    ) -> None:
        self._send_runtime_route_control(
            target,
            MessageType.EXECUTION_HINT,
            operation_id=operation_id,
            route_id=route_id,
            executor_class=executor_class,
            affinity_class=affinity_class,
            deadline_unix_ms=deadline_unix_ms,
            body=body,
            flags=flags,
        )

    def negotiate_runtime_capabilities(
        self,
        target: NativeControlTarget,
        *,
        profile_id: int,
        capability_count: int = 0,
        cost_model_id: int = 0,
        preference_rank: int = 0,
        limit_bytes: int = 0,
        limit_units: int = 0,
        body: bytes | bytearray | memoryview = b"",
        flags: int = 0,
    ) -> None:
        self._send_runtime_capability_control(
            target,
            MessageType.CAPABILITY_NEGOTIATION,
            profile_id=profile_id,
            capability_count=capability_count,
            cost_model_id=cost_model_id,
            preference_rank=preference_rank,
            limit_bytes=limit_bytes,
            limit_units=limit_units,
            body=body,
            flags=flags,
        )

    def degrade_runtime_profile(
        self,
        target: NativeControlTarget,
        *,
        profile_id: int,
        capability_count: int = 0,
        cost_model_id: int = 0,
        preference_rank: int = 0,
        limit_bytes: int = 0,
        limit_units: int = 0,
        body: bytes | bytearray | memoryview = b"",
        flags: int = 0,
    ) -> None:
        self._send_runtime_capability_control(
            target,
            MessageType.DEGRADE_PROFILE,
            profile_id=profile_id,
            capability_count=capability_count,
            cost_model_id=cost_model_id,
            preference_rank=preference_rank,
            limit_bytes=limit_bytes,
            limit_units=limit_units,
            body=body,
            flags=flags,
        )

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            try:
                self._role_executor.submit(self._close_role_resources).result()
            finally:
                self._role_executor.shutdown(wait=True)

    def _close_role_resources(self) -> None:
        try:
            try:
                for session in reversed(self._sessions):
                    if not getattr(session, "_closed", False):
                        session.close()
            finally:
                self.connection.close()
        finally:
            self._cancelled_frames.clear()
            self._cancelled_operations.clear()
            self._sessions.clear()
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("native client connection is closed")

    def _session_identity(self, session: object) -> int | None:
        nested_handle = getattr(getattr(session, "handle", None), "handle", None)
        if nested_handle is not None:
            return int(nested_handle.id)
        handle = getattr(session, "handle", None)
        if hasattr(handle, "id"):
            return int(cast(Any, handle).id)
        requested_session_id = getattr(session, "requested_session_id", None)
        if requested_session_id is not None:
            return int(requested_session_id)
        return None

    def _operation_session_identity(self, operation: object) -> int | None:
        session = getattr(operation, "session", None)
        if session is None:
            session_id = getattr(operation, "session_id", None)
            return None if session_id is None else int(session_id)
        return self._session_identity(session)

    def _remember_cancelled_frame(self, session: object, frame_id: int) -> None:
        session_id = self._session_identity(session)
        if session_id is not None:
            self._remember_cancelled_frame_by_handle(session_id, frame_id)

    def _remember_cancelled_frame_by_handle(self, session_id: int, frame_id: int) -> None:
        self._remember_cancelled_result_identity(self._cancelled_frames, session_id, frame_id)

    def _remember_cancelled_operation(self, session: object, operation_id: int) -> None:
        session_id = self._session_identity(session)
        if session_id is not None:
            self._remember_cancelled_operation_by_handle(session_id, operation_id)

    def _remember_cancelled_operation_by_handle(self, session_id: int, operation_id: int) -> None:
        self._remember_cancelled_result_identity(self._cancelled_operations, session_id, operation_id)

    def _remember_cancelled_result_identity(
        self,
        suppressions: dict[int, dict[int, None]],
        session_id: int,
        value: int,
    ) -> None:
        values = suppressions.setdefault(int(session_id), {})
        values[int(value)] = None
        while len(values) > _MAX_CANCELLED_RESULT_SUPPRESSIONS_PER_SESSION:
            values.pop(next(iter(values)))

    def _is_cancelled_result(self, session: object, *, operation_id: int, frame_id: int) -> bool:
        session_id = self._session_identity(session)
        if session_id is None:
            return False
        return int(frame_id) in self._cancelled_frames.get(session_id, {}) or int(
            operation_id,
        ) in self._cancelled_operations.get(session_id, {})

    def _send_runtime_control_request(
        self,
        target: NativeControlTarget,
        message_type: MessageType,
        *,
        operation_id: int,
        control_sequence: int,
        reason_code: int,
        source_role: RuntimeRole | int,
        diagnostic: bytes | bytearray | memoryview,
        flags: int,
    ) -> None:
        metadata = ControlRequestMetadata(
            operation_id=operation_id,
            control_sequence=control_sequence,
            reason_code=reason_code,
            source_role=source_role,
            flags=flags,
            diagnostic_bytes=memoryview(diagnostic).nbytes,
        )
        self._send_runtime_control(target, message_type, metadata, tail=diagnostic)

    def _send_runtime_scheduling(
        self,
        target: NativeControlTarget,
        message_type: MessageType,
        *,
        operation_id: int,
        control_sequence: int,
        priority_class: int,
        priority_delta: int,
        deadline_unix_ms: int,
        flags: int,
    ) -> None:
        metadata = SchedulingMetadata(
            operation_id=operation_id,
            control_sequence=control_sequence,
            priority_class=priority_class,
            priority_delta=priority_delta,
            deadline_unix_ms=deadline_unix_ms,
            flags=flags,
        )
        self._send_runtime_control(target, message_type, metadata)

    def _send_runtime_route_control(
        self,
        target: NativeControlTarget,
        message_type: MessageType,
        *,
        operation_id: int,
        route_id: int,
        executor_class: int,
        affinity_class: int,
        deadline_unix_ms: int,
        body: bytes | bytearray | memoryview,
        flags: int,
    ) -> None:
        metadata = RouteHintMetadata(
            operation_id=operation_id,
            route_id=route_id,
            executor_class=executor_class,
            affinity_class=affinity_class,
            deadline_unix_ms=deadline_unix_ms,
            body_bytes=memoryview(body).nbytes,
            flags=flags,
        )
        self._send_runtime_control(target, message_type, metadata, tail=body)

    def _send_runtime_capability_control(
        self,
        target: NativeControlTarget,
        message_type: MessageType,
        *,
        profile_id: int,
        capability_count: int,
        cost_model_id: int,
        preference_rank: int,
        limit_bytes: int,
        limit_units: int,
        body: bytes | bytearray | memoryview,
        flags: int,
    ) -> None:
        metadata = CapabilityMetadata(
            profile_id=profile_id,
            capability_count=capability_count,
            cost_model_id=cost_model_id,
            preference_rank=preference_rank,
            limit_bytes=limit_bytes,
            limit_units=limit_units,
            body_bytes=memoryview(body).nbytes,
            flags=flags,
        )
        self._send_runtime_control(target, message_type, metadata, tail=body)

    def _send_runtime_control(
        self,
        target: NativeControlTarget,
        message_type: MessageType,
        metadata: _FixedRuntimeMetadata,
        *,
        tail: bytes | bytearray | memoryview = b"",
    ) -> None:
        self._ensure_open()
        target._send_runtime_frame(message_type, metadata, tail)


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


def _client_route_security_satisfied(
    endpoint: NnrpEndpoint,
    *,
    transport_name: str,
    provider_endpoint: NativeTransportEndpoint | None,
    security: NativeTransportClientSecurity | None,
) -> bool:
    if transport_name == "quic":
        return security is not None
    if transport_name == "websocket" and provider_endpoint is not None and provider_endpoint.secure:
        return security is not None
    if not endpoint.secure:
        return True
    if transport_name == "tcp":
        return security is not None
    if transport_name == "websocket":
        return provider_endpoint is not None and provider_endpoint.secure and security is not None
    return False


def _resolve_client_routes(
    endpoint: NnrpEndpoint,
    provider_routes: Mapping[str, NativeClientProviderRoute],
    transport_names: set[str],
) -> dict[str, _ResolvedClientRoute]:
    resolved: dict[str, _ResolvedClientRoute] = {}
    for transport_name in transport_names:
        route = provider_routes.get(transport_name, NativeClientProviderRoute())
        try:
            carrier_endpoint = resolve_native_transport_endpoint(
                endpoint,
                transport_name,
                provider_endpoint=route.provider_endpoint,
            )
        except (NativeArtifactError, ValueError):
            carrier_endpoint = None
        resolved[transport_name] = _ResolvedClientRoute(
            endpoint=carrier_endpoint,
            security=route.security,
            route_resolved=carrier_endpoint is not None,
            security_satisfied=_client_route_security_satisfied(
                endpoint,
                transport_name=transport_name,
                provider_endpoint=carrier_endpoint,
                security=route.security,
            ),
        )
    return resolved


def _client_candidate_diagnostics(
    *,
    policy: TransportPolicy,
    transport_names: set[str],
    providers_by_name: Mapping[str, Any],
    routes: Mapping[str, _ResolvedClientRoute],
    native_candidates: tuple[NativeTransportCandidateDiagnostic, ...],
) -> tuple[NativeTransportCandidateDiagnostic, ...]:
    native_by_name = {candidate.transport_name: candidate for candidate in native_candidates}
    diagnostics: dict[str, NativeTransportCandidateDiagnostic] = {}
    for transport_name in transport_names:
        candidate = native_by_name.get(transport_name)
        if candidate is not None:
            diagnostics[transport_name] = candidate
            continue
        candidate = unavailable_candidate(transport_name)
        route = routes[transport_name]
        diagnostics[transport_name] = apply_host_rejection(
            candidate,
            policy_allowed=policy_allows(policy, transport_name),
            local_available=transport_name in providers_by_name,
            peer_supported=candidate.peer_supported,
            within_limits=candidate.within_limits,
            route_resolved=route.route_resolved,
            security_satisfied=route.security_satisfied,
        )
    return ordered_candidates(diagnostics)


def _select_client_transport(
    endpoint: NnrpEndpoint,
    *,
    provider_routes: Mapping[str, NativeClientProviderRoute] | None,
    transport_policy: TransportPolicy | str | int,
    artifact_path: Path | str | None,
    root: Path | str | None,
    native_platform: NativePlatform | None,
    library: Any | None,
    transports: Sequence[NativeTransportBinding] | None,
) -> tuple[NativeTransportSelection, _ResolvedClientRoute, NativeTransportBinding]:
    policy = normalize_transport_policy(transport_policy)
    normalized_routes = normalize_provider_routes(provider_routes, NativeClientProviderRoute)
    bindings = _resolve_client_transport_bindings(
        transports,
        artifact_path=artifact_path,
        root=root,
        native_platform=native_platform,
        library=library,
    )
    providers = tuple(binding.provider for binding in bindings)
    providers_by_name = {provider.name: provider for provider in providers}
    bindings_by_name = {binding.kind: binding for binding in bindings}
    transport_names = set(providers_by_name) | set(normalized_routes) | {"tcp", "quic"}
    forced_transport = forced_transport_name(policy)
    if forced_transport is not None:
        transport_names.add(forced_transport)
    routes = _resolve_client_routes(endpoint, normalized_routes, transport_names)
    peer_supported_transports = {"tcp", "quic"} | set(normalized_routes)
    readiness = tuple(
        NativeTransportCandidateReadiness(
            transport_id=NATIVE_TRANSPORT_ID_BY_NAME[provider.name],
            provider_id=provider.metadata.id,
            route_resolved=routes[provider.name].route_resolved,
            security_satisfied=routes[provider.name].security_satisfied,
            diagnostic=(
                "provider route is unresolved"
                if not routes[provider.name].route_resolved
                else "provider route does not satisfy application security"
                if not routes[provider.name].security_satisfied
                else None
            ),
        )
        for provider in providers
    )
    eligible_provider_names = {
        provider.name
        for provider in providers
        if bindings_by_name[provider.name].local_available
        and policy_allows(policy, provider.name)
        and provider.name in peer_supported_transports
        and routes[provider.name].route_resolved
        and routes[provider.name].security_satisfied
    }
    observations: list[NativeTransportProbeObservation] = []
    for provider in providers:
        if len(eligible_provider_names) <= 1 or provider.name not in eligible_provider_names:
            continue
        route = routes[provider.name]
        if route.endpoint is None:
            continue
        binding = bindings_by_name[provider.name]
        try:
            metrics = binding._probe(route.endpoint, route.security, 3, 64, 0, 1_000)
        except Exception as error:
            observations.append(
                NativeTransportProbeObservation.failed(
                    provider,
                    f"transport probe failed: {error}",
                )
            )
            continue
        observations.append(NativeTransportProbeObservation.succeeded(provider, metrics))
    try:
        selection = _select_native_transport_provider_from_providers(
            providers,
            policy,
            supported_transports=tuple(sorted(peer_supported_transports)),
            candidate_readiness=readiness,
            probe_observations=observations,
            provider_availability={binding.provider.metadata.id: binding.local_available for binding in bindings},
            provider_diagnostics={binding.provider.metadata.id: binding.diagnostic for binding in bindings},
        )
    except NativeTransportSelectionError as native_error:
        if native_error.code is NativeTransportSelectionErrorCode.INVALID_EVIDENCE:
            raise
        diagnostics = _client_candidate_diagnostics(
            policy=policy,
            transport_names=transport_names,
            providers_by_name=providers_by_name,
            routes=routes,
            native_candidates=native_error.candidates,
        )
        raise selection_error(policy, diagnostics) from native_error
    selected_transport = selection.selected_transport_name
    return selection, routes[selected_transport], bindings_by_name[selected_transport]


def _resolve_client_transport_bindings(
    transports: Sequence[NativeTransportBinding] | None,
    *,
    artifact_path: Path | str | None,
    root: Path | str | None,
    native_platform: NativePlatform | None,
    library: Any | None,
) -> tuple[NativeTransportBinding, ...]:
    if transports is None:
        providers = discover_native_transport_providers(root, native_platform)
        bindings = tuple(
            load_native_transport_binding(
                provider.name,
                artifact_path=artifact_path,
                root=root,
                native_platform=native_platform,
                library=library,
            )
            for provider in providers
        )
    else:
        bindings = tuple(transports)
    kinds = [binding.kind for binding in bindings]
    provider_ids = [binding.provider.metadata.id for binding in bindings]
    if len(set(kinds)) != len(kinds) or len(set(provider_ids)) != len(provider_ids):
        raise NativeTransportSelectionError(
            NativeTransportSelectionErrorCode.INVALID_EVIDENCE,
            "transport bindings contain duplicate transport or provider identifiers",
        )
    return bindings


@contextmanager
def connect_native_client_connection(
    options: NativeClientOptions,
    *,
    _transports: Sequence[NativeTransportBinding] | None = None,
    _artifact_path: Path | str | None = None,
    _root: Path | str | None = None,
    _native_platform: NativePlatform | None = None,
    _library: Any | None = None,
    _fallback: NativeRuntimeBackend | None = None,
) -> Iterator[NativeClientConnection]:
    if not isinstance(options, NativeClientOptions):
        raise TypeError("options must be NativeClientOptions")
    application_endpoint = cast(NnrpEndpoint, options.endpoint)
    selection, route, binding = _select_client_transport(
        application_endpoint,
        provider_routes=options.provider_routes,
        transport_policy=options.transport_policy,
        artifact_path=_artifact_path,
        root=_root,
        native_platform=_native_platform,
        library=_library,
        transports=_transports,
    )
    if route.endpoint is None:
        raise AssertionError("selected client route must have a resolved endpoint")
    carrier = binding._connect(route.endpoint, route.security, 0, 0)
    try:
        connection = (
            _fallback.connect(
                connection_id=_allocate_native_handle_id(),
                generation=1,
                transport_connection=carrier,
            )
            if _fallback is not None
            else binding.adopt_client(
                carrier,
                connection_id=_allocate_native_handle_id(),
                generation=1,
            )
        )
    except BaseException:
        carrier._close()
        raise
    client_connection = NativeClientConnection(connection, selection, options.session_defaults)
    try:
        yield client_connection
    finally:
        client_connection.close()
