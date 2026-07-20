"""Client-facing native runtime session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any

from nnrp.core import FrameSubmitMetadata, MessageType, TransportPolicy
from nnrp.native import (
    FFI_STATUS_WOULD_BLOCK,
    NativeArtifactError,
    NativePlatform,
    NativeRuntimeBackend,
    NativeRuntimeConnection,
    NativeRuntimeOperation,
    NativeRuntimeResult,
    NativeRuntimeSession,
    NativeStatus,
    NativeTransportClientSecurity,
    NativeTransportEndpoint,
    NativeTransportProbeSample,
    NativeWouldBlockError,
    NnrpEndpoint,
    discover_native_transport_providers,
    load_native_transport_binding,
    parse_native_transport_endpoint,
    parse_nnrp_endpoint,
    resolve_native_transport_endpoint,
    resolve_native_transport_provider,
    select_native_runtime_backend,
    select_native_transport_provider,
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


@dataclass(frozen=True, slots=True)
class NativeClientSessionOptions:
    connection_id: int = 1
    connection_generation: int = 1
    requested_session_id: int = 1
    session_generation: int = 1
    profile_id: int = int(StandardProfile.TOKEN)
    schema_id: int = TOKEN_DELTA_SCHEMA_ID
    schema_version: int = TOKEN_DELTA_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class NativeClientConnectionOptions:
    connection_id: int = 1
    connection_generation: int = 1


@dataclass(frozen=True, slots=True)
class NativeClientSessionOpenOptions:
    requested_session_id: int = 1
    session_generation: int = 1
    profile_id: int = int(StandardProfile.TOKEN)
    schema_id: int = TOKEN_DELTA_SCHEMA_ID
    schema_version: int = TOKEN_DELTA_SCHEMA_VERSION


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
    _cancelled_frames: dict[int, dict[int, None]] = field(default_factory=dict, init=False, repr=False)
    _cancelled_operations: dict[int, dict[int, None]] = field(default_factory=dict, init=False, repr=False)
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
        timeout_ms: int = 0,
    ) -> NativeRuntimeResult:
        self._ensure_open()
        if self._is_cancelled_result(session, operation_id=operation.operation_id, frame_id=operation.frame_id):
            raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))
        result = session.poll_result(operation, max_events=max_events, timeout_ms=timeout_ms)
        if self._is_cancelled_result(session, operation_id=result.operation_id, frame_id=result.frame_id):
            raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))
        return result

    def submit_and_poll_result(
        self,
        session: NativeRuntimeSession,
        *,
        operation_id: int,
        frame_id: int,
        metadata: FrameSubmitMetadata | None = None,
        body: bytes | bytearray | memoryview = b"",
        parent_operation_id: int | None = None,
        operation_group_id: int | None = None,
        max_events: int | None = None,
        timeout_ms: int = 0,
    ) -> NativeRuntimeResult:
        self._ensure_open()
        operation = session.submit_operation(
            operation_id=operation_id,
            frame_id=frame_id,
            metadata=metadata,
            body=body,
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
        if self._closed:
            return
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
            return int(handle.id)
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


def _select_client_transport(
    endpoint: NnrpEndpoint,
    *,
    provider_endpoint: NativeTransportEndpoint | None,
    transport_policy: TransportPolicy | str | int,
    transport: str | None,
    security: NativeTransportClientSecurity | None,
    artifact_path: Path | str | None,
    root: Path | str | None,
    native_platform: NativePlatform | None,
    library: Any | None,
) -> str:
    if transport is not None:
        provider = resolve_native_transport_provider(transport, root=root, native_platform=native_platform)
        select_native_transport_provider(
            transport_policy,
            root=root,
            native_platform=native_platform,
            supported_transports=(provider.name,),
        )
        if provider_endpoint is not None and provider_endpoint.transport_name != provider.name:
            raise NativeArtifactError(
                f"{provider.name} provider cannot use {provider_endpoint.transport_name} carrier endpoint"
            )
        return provider.name

    supported = (
        {provider_endpoint.transport_name}
        if provider_endpoint is not None
        else {"tcp", "quic"}
    )
    try:
        return select_native_transport_provider(
            transport_policy,
            root=root,
            native_platform=native_platform,
            supported_transports=tuple(supported),
        ).selected_transport_name
    except NativeArtifactError as selection_error:
        if not selection_error.candidates or not any(
            candidate.rejection_reason is not None and candidate.rejection_reason.value == "probe-missing"
            for candidate in selection_error.candidates
        ):
            raise

    samples: list[NativeTransportProbeSample] = []
    for provider in discover_native_transport_providers(root, native_platform):
        if provider.name not in supported:
            continue
        carrier_endpoint = resolve_native_transport_endpoint(
            endpoint,
            provider.name,
            provider_endpoint=provider_endpoint,
        )
        binding = load_native_transport_binding(
            provider.name,
            artifact_path=artifact_path,
            root=root,
            native_platform=native_platform,
            library=library,
        )
        try:
            metrics = binding._probe(carrier_endpoint, security, 3, 64, 0, 1_000)
        except Exception:
            samples.append(
                NativeTransportProbeSample(
                    provider_id=provider.metadata.id,
                    transport_name=provider.name,
                    elapsed_us=1,
                    failed=True,
                )
            )
            continue
        successful_samples = max(1, int(metrics.success_count))
        for _ in range(successful_samples):
            samples.append(
                NativeTransportProbeSample(
                    provider_id=provider.metadata.id,
                    transport_name=provider.name,
                    elapsed_us=1_000_000,
                    rtt_us=metrics.median_rtt_us,
                    bytes_received=metrics.median_throughput_bytes_per_sec,
                )
            )
        for _ in range(max(0, int(metrics.sample_count) - successful_samples)):
            samples.append(
                NativeTransportProbeSample(
                    provider_id=provider.metadata.id,
                    transport_name=provider.name,
                    elapsed_us=1,
                    failed=True,
                )
            )
    return select_native_transport_provider(
        transport_policy,
        root=root,
        native_platform=native_platform,
        supported_transports=tuple(supported),
        probe_samples=samples,
    ).selected_transport_name


@contextmanager
def connect_native_client_connection(
    endpoint: str | NnrpEndpoint,
    *,
    provider_endpoint: str | NativeTransportEndpoint | None = None,
    transport_policy: TransportPolicy | str | int = TransportPolicy.AUTO,
    transport: str | None = None,
    security: NativeTransportClientSecurity | None = None,
    artifact_path: Path | str | None = None,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    library: Any | None = None,
    fallback: NativeRuntimeBackend | None = None,
    require_native: bool = False,
    options: NativeClientConnectionOptions | None = None,
) -> Iterator[NativeClientConnection]:
    application_endpoint = endpoint if isinstance(endpoint, NnrpEndpoint) else parse_nnrp_endpoint(endpoint)
    carrier_override = (
        provider_endpoint
        if isinstance(provider_endpoint, NativeTransportEndpoint)
        else parse_native_transport_endpoint(provider_endpoint)
        if provider_endpoint is not None
        else None
    )
    resolved_options = options or NativeClientConnectionOptions()
    transport_name = _select_client_transport(
        application_endpoint,
        provider_endpoint=carrier_override,
        transport_policy=transport_policy,
        transport=transport,
        security=security,
        artifact_path=artifact_path,
        root=root,
        native_platform=native_platform,
        library=library,
    )
    carrier_endpoint = resolve_native_transport_endpoint(
        application_endpoint,
        transport_name,
        provider_endpoint=carrier_override,
    )
    binding = load_native_transport_binding(
        transport_name,
        artifact_path=artifact_path,
        root=root,
        native_platform=native_platform,
        library=library,
    )
    carrier = binding._connect(carrier_endpoint, security, 0, 0)
    try:
        connection = (
            fallback.connect(
                connection_id=resolved_options.connection_id,
                generation=resolved_options.connection_generation,
                transport_connection=carrier,
            )
            if fallback is not None and not require_native
            else binding.adopt_client(
                carrier,
                connection_id=resolved_options.connection_id,
                generation=resolved_options.connection_generation,
            )
        )
    except BaseException:
        carrier._close()
        raise
    client_connection = NativeClientConnection(connection)
    try:
        yield client_connection
    finally:
        client_connection.close()


@contextmanager
def connect_native_client_session(
    endpoint: str | NnrpEndpoint,
    *,
    provider_endpoint: str | NativeTransportEndpoint | None = None,
    transport_policy: TransportPolicy | str | int = TransportPolicy.AUTO,
    transport: str | None = None,
    security: NativeTransportClientSecurity | None = None,
    artifact_path: Path | str | None = None,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    library: Any | None = None,
    fallback: NativeRuntimeBackend | None = None,
    require_native: bool = False,
    options: NativeClientSessionOptions | None = None,
) -> Iterator[NativeRuntimeSession]:
    resolved_options = options or NativeClientSessionOptions()
    connection_options = NativeClientConnectionOptions(
        connection_id=resolved_options.connection_id,
        connection_generation=resolved_options.connection_generation,
    )
    session_options = NativeClientSessionOpenOptions(
        requested_session_id=resolved_options.requested_session_id,
        session_generation=resolved_options.session_generation,
        profile_id=resolved_options.profile_id,
        schema_id=resolved_options.schema_id,
        schema_version=resolved_options.schema_version,
    )
    with connect_native_client_connection(
        endpoint,
        provider_endpoint=provider_endpoint,
        transport_policy=transport_policy,
        transport=transport,
        security=security,
        artifact_path=artifact_path,
        root=root,
        native_platform=native_platform,
        library=library,
        fallback=fallback,
        require_native=require_native,
        options=connection_options,
    ) as client_connection:
        session = client_connection.open_session(session_options)
        yield session
