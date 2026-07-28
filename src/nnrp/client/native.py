"""Client-facing native runtime session helpers."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
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
from nnrp.core import FrameSubmitMetadata, MessageType, TransportPolicy
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
class NativeClientProviderRoute:
    provider_endpoint: str | NativeTransportEndpoint | None = None
    security: NativeTransportClientSecurity | None = None


@dataclass(frozen=True, slots=True)
class _ResolvedClientRoute:
    endpoint: NativeTransportEndpoint | None
    security: NativeTransportClientSecurity | None
    route_resolved: bool
    security_satisfied: bool


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
    _sessions: list[NativeRuntimeSession] = field(default_factory=list, init=False, repr=False)
    _cancelled_frames: dict[int, dict[int, None]] = field(default_factory=dict, init=False, repr=False)
    _cancelled_operations: dict[int, dict[int, None]] = field(default_factory=dict, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def active_transport_name(self) -> str:
        return self.transport_selection.selected_transport_name

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
            provider_availability={
                binding.provider.metadata.id: binding.local_available for binding in bindings
            },
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
    endpoint: str | NnrpEndpoint,
    *,
    provider_routes: Mapping[str, NativeClientProviderRoute] | None = None,
    transports: Sequence[NativeTransportBinding] | None = None,
    transport_policy: TransportPolicy | str | int = TransportPolicy.AUTO,
    artifact_path: Path | str | None = None,
    root: Path | str | None = None,
    native_platform: NativePlatform | None = None,
    library: Any | None = None,
    fallback: NativeRuntimeBackend | None = None,
    require_native: bool = False,
    options: NativeClientConnectionOptions | None = None,
) -> Iterator[NativeClientConnection]:
    application_endpoint = endpoint if isinstance(endpoint, NnrpEndpoint) else parse_nnrp_endpoint(endpoint)
    resolved_options = options or NativeClientConnectionOptions()
    selection, route, binding = _select_client_transport(
        application_endpoint,
        provider_routes=provider_routes,
        transport_policy=transport_policy,
        artifact_path=artifact_path,
        root=root,
        native_platform=native_platform,
        library=library,
        transports=transports,
    )
    if route.endpoint is None:
        raise AssertionError("selected client route must have a resolved endpoint")
    carrier = binding._connect(route.endpoint, route.security, 0, 0)
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
    client_connection = NativeClientConnection(connection, selection)
    try:
        yield client_connection
    finally:
        client_connection.close()


@contextmanager
def connect_native_client_session(
    endpoint: str | NnrpEndpoint,
    *,
    provider_routes: Mapping[str, NativeClientProviderRoute] | None = None,
    transports: Sequence[NativeTransportBinding] | None = None,
    transport_policy: TransportPolicy | str | int = TransportPolicy.AUTO,
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
        provider_routes=provider_routes,
        transports=transports,
        transport_policy=transport_policy,
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
