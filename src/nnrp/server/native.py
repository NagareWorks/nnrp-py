"""Server-facing native role lifecycle helpers."""

from __future__ import annotations

import asyncio
import atexit
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from inspect import iscoroutine
from types import MappingProxyType
from typing import Protocol

from nnrp._native_routes import (
    apply_host_rejection,
    forced_transport_name,
    normalize_provider_routes,
    normalize_transport_policy,
    ordered_candidates,
    policy_allows,
    provider_order_key,
    selection_error,
    unavailable_candidate,
)
from nnrp.core import SessionOpenMetadata, TransportPolicy
from nnrp.native import (
    FFI_STATUS_WOULD_BLOCK,
    NativeArtifactError,
    NativeRuntimeServer,
    NativeRuntimeServerSession,
    NativeStatus,
    NativeTransportBinding,
    NativeTransportCandidateDiagnostic,
    NativeTransportEndpoint,
    NativeTransportProbeState,
    NativeTransportRejectionReason,
    NativeTransportSelectionError,
    NativeTransportSelectionErrorCode,
    NativeTransportServerSecurity,
    NativeWouldBlockError,
    NnrpEndpoint,
    _allocate_native_handle_id,
    discover_native_transport_providers,
    load_native_transport_binding,
    parse_nnrp_endpoint,
    resolve_native_transport_endpoint,
)
from nnrp.schema import SchemaRegistryCatalog, StandardProfile, token_delta_schema_descriptor

_DEFAULT_ACCEPT_TIMEOUT_MS = 5_000
_ACCEPT_POLL_SLICE_MS = 1
_SESSION_POLICY_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="nnrp-session-policy")
atexit.register(_SESSION_POLICY_EXECUTOR.shutdown, wait=True, cancel_futures=True)


@dataclass(frozen=True, slots=True)
class NativeServerSessionPolicyDecision:
    accepted: bool
    session_error_code: int
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.session_error_code <= 0xFFFFFFFF:
            raise ValueError("session_error_code must fit in u32")
        if self.accepted and self.session_error_code != 0:
            raise ValueError("accepted policy decisions require session_error_code 0")
        if not self.accepted and self.session_error_code == 0:
            raise ValueError("rejected policy decisions require a non-zero session_error_code")

    @classmethod
    def accept(cls) -> NativeServerSessionPolicyDecision:
        return cls(True, 0)

    @classmethod
    def reject(cls, session_error_code: int, diagnostic: str | None = None) -> NativeServerSessionPolicyDecision:
        return cls(False, session_error_code, diagnostic)


class NativeServerSessionPolicy(Protocol):
    async def evaluate(self, open: SessionOpenMetadata) -> NativeServerSessionPolicyDecision: ...


class _AcceptValidSessionsPolicy:
    async def evaluate(self, open: SessionOpenMetadata) -> NativeServerSessionPolicyDecision:
        del open
        return NativeServerSessionPolicyDecision.accept()


_ACCEPT_VALID_SESSIONS = _AcceptValidSessionsPolicy()


def _evaluate_session_policy(
    policy: NativeServerSessionPolicy,
    open_metadata: SessionOpenMetadata,
) -> NativeServerSessionPolicyDecision:
    evaluation = policy.evaluate(open_metadata)
    if not iscoroutine(evaluation):
        raise TypeError("application_policy.evaluate(open) must return a coroutine")
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(evaluation)
    return _SESSION_POLICY_EXECUTOR.submit(asyncio.run, evaluation).result()


def _standard_schema_registry() -> SchemaRegistryCatalog:
    return SchemaRegistryCatalog((token_delta_schema_descriptor(),))


@dataclass(frozen=True, slots=True)
class NativeServerSessionOptions:
    supported_profiles: tuple[int, ...] = (int(StandardProfile.TOKEN),)
    supported_cache_objects: tuple[int, ...] = ()
    max_cache_objects: int = 0
    max_cache_object_bytes: int = 0
    schema_registry: SchemaRegistryCatalog = field(default_factory=_standard_schema_registry)
    resume_token_bytes: int = 24
    max_in_flight_operations: int = 4
    granted_operation_credit: int = 2
    lease_ttl_ms: int = 30_000
    resume_window_ms: int = 120_000
    application_policy: NativeServerSessionPolicy = _ACCEPT_VALID_SESSIONS

    def __post_init__(self) -> None:
        profiles = tuple(int(profile) for profile in self.supported_profiles)
        cache_objects = tuple(int(object_kind) for object_kind in self.supported_cache_objects)
        if not profiles or any(not 0 <= profile <= 0xFFFF for profile in profiles):
            raise ValueError("supported_profiles must contain u16 profile ids")
        if any(not 0 <= object_kind <= 0xFFFFFFFF for object_kind in cache_objects):
            raise ValueError("supported_cache_objects values must fit in u32")
        if not 0 <= self.max_cache_objects <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("max_cache_objects must fit in u64")
        for name, value in (
            ("max_cache_object_bytes", self.max_cache_object_bytes),
            ("resume_token_bytes", self.resume_token_bytes),
            ("lease_ttl_ms", self.lease_ttl_ms),
            ("resume_window_ms", self.resume_window_ms),
        ):
            if not 0 <= value <= 0xFFFFFFFF:
                raise ValueError(f"{name} must fit in u32")
        if not 1 <= self.max_in_flight_operations <= 0xFFFF:
            raise ValueError("max_in_flight_operations must be a non-zero u16")
        if not 0 <= self.granted_operation_credit <= self.max_in_flight_operations:
            raise ValueError("granted_operation_credit must not exceed max_in_flight_operations")
        if not isinstance(self.schema_registry, SchemaRegistryCatalog):
            raise TypeError("schema_registry must be SchemaRegistryCatalog")
        if not callable(getattr(self.application_policy, "evaluate", None)):
            raise TypeError("application_policy must implement evaluate(open)")
        object.__setattr__(self, "supported_profiles", profiles)
        object.__setattr__(self, "supported_cache_objects", cache_objects)


@dataclass(frozen=True, slots=True)
class NativeServerAcceptOptions:
    timeout_ms: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.timeout_ms <= 0xFFFFFFFF:
            raise ValueError("timeout_ms must fit in u32")


@dataclass(frozen=True, slots=True)
class NativeServerProviderRoute:
    provider_endpoint: str | NativeTransportEndpoint | None = None
    security: NativeTransportServerSecurity | None = None


@dataclass(frozen=True, slots=True)
class NativeServerBootstrapOptions:
    endpoint: str | NnrpEndpoint
    provider_routes: Mapping[str, NativeServerProviderRoute] = field(default_factory=dict)
    transport_policy: TransportPolicy = TransportPolicy.AUTO
    session_defaults: NativeServerSessionOptions = field(default_factory=NativeServerSessionOptions)

    def __post_init__(self) -> None:
        endpoint = self.endpoint if isinstance(self.endpoint, NnrpEndpoint) else parse_nnrp_endpoint(self.endpoint)
        routes = MappingProxyType(dict(self.provider_routes))
        if any(not isinstance(route, NativeServerProviderRoute) for route in routes.values()):
            raise TypeError("provider_routes values must be NativeServerProviderRoute")
        if not isinstance(self.session_defaults, NativeServerSessionOptions):
            raise TypeError("session_defaults must be NativeServerSessionOptions")
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "provider_routes", routes)
        object.__setattr__(self, "transport_policy", TransportPolicy(self.transport_policy))


@dataclass(frozen=True, slots=True)
class _ResolvedServerRoute:
    binding: NativeTransportBinding
    endpoint: NativeTransportEndpoint
    security: NativeTransportServerSecurity | None


@dataclass(slots=True)
class NativeServer:
    _servers: tuple[tuple[str, NativeRuntimeServer], ...]
    bound_provider_endpoints: Mapping[str, NativeTransportEndpoint]
    _sessions: list[NativeRuntimeServerSession] = field(default_factory=list, init=False, repr=False)
    _accept_session_handle_ids: list[int | None] = field(default_factory=list, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _accept_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    async def accept(self, options: NativeServerAcceptOptions | None = None) -> NativeRuntimeServerSession:
        return await asyncio.to_thread(self._accept, options)

    def _accept(self, options: NativeServerAcceptOptions | None = None) -> NativeRuntimeServerSession:
        with self._accept_lock:
            return self._accept_serialized(options)

    def _accept_serialized(self, options: NativeServerAcceptOptions | None = None) -> NativeRuntimeServerSession:
        self._ensure_open()
        resolved = options or NativeServerAcceptOptions()
        timeout_ms = resolved.timeout_ms or _DEFAULT_ACCEPT_TIMEOUT_MS
        deadline = time.monotonic() + timeout_ms / 1_000
        if not self._accept_session_handle_ids:
            self._accept_session_handle_ids.extend(None for _server in self._servers)
        while True:
            for index, (_transport_name, server) in enumerate(self._servers):
                session_handle_id = self._accept_session_handle_ids[index]
                if session_handle_id is None:
                    session_handle_id = _allocate_native_handle_id()
                    self._accept_session_handle_ids[index] = session_handle_id
                try:
                    session = server.accept_session(
                        session_handle_id=session_handle_id,
                        generation=1,
                        timeout_ms=_ACCEPT_POLL_SLICE_MS,
                    )
                except NativeWouldBlockError:
                    continue
                except BaseException:
                    try:
                        self.close()
                    except BaseException:
                        pass
                    raise
                self._accept_session_handle_ids[index] = None
                self._sessions.append(session)
                return session
            if time.monotonic() >= deadline:
                try:
                    self._release_pending_accept_tickets()
                except BaseException:
                    try:
                        self.close()
                    except BaseException:
                        pass
                    raise
                raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))

    def _release_pending_accept_tickets(self) -> None:
        first_error: BaseException | None = None
        for index, (_transport_name, server) in enumerate(self._servers):
            try:
                server._release_pending_accept_ticket()
            except BaseException as error:
                first_error = first_error or error
            finally:
                self._accept_session_handle_ids[index] = None
        if first_error is not None:
            raise first_error

    def close(self) -> None:
        if self._closed:
            return
        first_error: BaseException | None = None
        for session in reversed(self._sessions):
            if not session._closed:
                try:
                    session.close()
                except BaseException as error:
                    first_error = first_error or error
        for _transport_name, server in reversed(self._servers):
            if not server._closed:
                try:
                    server.close()
                except BaseException as error:
                    first_error = first_error or error
        self._closed = True
        if first_error is not None:
            raise first_error

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("native server is closed")


def _server_route_security_satisfied(
    endpoint: NnrpEndpoint,
    *,
    transport_name: str,
    provider_endpoint: NativeTransportEndpoint | None,
    security: NativeTransportServerSecurity | None,
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


def _base_server_candidate(binding: NativeTransportBinding) -> NativeTransportCandidateDiagnostic:
    return NativeTransportCandidateDiagnostic(
        transport_id=binding.provider.transport_id,
        provider=binding.provider.metadata,
        local_available=binding.local_available,
        peer_supported=True,
        within_limits=True,
        probe_state=NativeTransportProbeState.NOT_RUN,
        diagnostic=binding.diagnostic,
    )


def _resolve_server_routes(
    endpoint: NnrpEndpoint,
    provider_routes: Mapping[str, NativeServerProviderRoute] | None,
    transport_policy: TransportPolicy | str | int,
    transports: Sequence[NativeTransportBinding] | None,
) -> tuple[_ResolvedServerRoute, ...]:
    policy = normalize_transport_policy(transport_policy)
    normalized_routes = normalize_provider_routes(provider_routes, NativeServerProviderRoute)
    bindings = _resolve_server_transport_bindings(transports)
    providers = tuple(binding.provider for binding in bindings)
    providers_by_name = {provider.transport_name: provider for provider in providers}
    bindings_by_name = {binding.kind: binding for binding in bindings}
    transport_names = set(providers_by_name) | set(normalized_routes)
    forced_transport = forced_transport_name(policy)
    if forced_transport is not None:
        transport_names.add(forced_transport)
    diagnostics: dict[str, NativeTransportCandidateDiagnostic] = {}
    resolved_routes: list[_ResolvedServerRoute] = []
    required_failure = False

    for transport_name in transport_names:
        provider = providers_by_name.get(transport_name)
        binding = bindings_by_name.get(transport_name)
        route = normalized_routes.get(transport_name, NativeServerProviderRoute())
        carrier_endpoint: NativeTransportEndpoint | None = None
        if provider is not None:
            try:
                carrier_endpoint = resolve_native_transport_endpoint(
                    endpoint,
                    transport_name,
                    provider_endpoint=route.provider_endpoint,
                )
            except (NativeArtifactError, ValueError):
                pass
        security_satisfied = _server_route_security_satisfied(
            endpoint,
            transport_name=transport_name,
            provider_endpoint=carrier_endpoint,
            security=route.security,
        )
        candidate = _base_server_candidate(binding) if binding is not None else unavailable_candidate(transport_name)
        candidate = apply_host_rejection(
            candidate,
            policy_allowed=policy_allows(policy, transport_name),
            local_available=binding is not None and binding.local_available,
            peer_supported=True,
            within_limits=True,
            route_resolved=carrier_endpoint is not None,
            security_satisfied=security_satisfied,
        )
        diagnostics[transport_name] = candidate

        if candidate.rejection_reason is None:
            assert binding is not None and carrier_endpoint is not None
            resolved_routes.append(_ResolvedServerRoute(binding, carrier_endpoint, route.security))
        elif policy_allows(policy, transport_name) and (
            candidate.rejection_reason is NativeTransportRejectionReason.ROUTE_UNRESOLVED
            or (
                transport_name in normalized_routes
                and candidate.rejection_reason is NativeTransportRejectionReason.LOCAL_UNAVAILABLE
            )
        ):
            required_failure = True

    candidates = ordered_candidates(diagnostics)
    if required_failure or not resolved_routes:
        raise selection_error(policy, candidates)
    resolved_routes.sort(
        key=lambda route: provider_order_key(route.binding.kind, route.binding.provider.metadata, policy)
    )
    return tuple(resolved_routes)


def _resolve_server_transport_bindings(
    transports: Sequence[NativeTransportBinding] | None,
) -> tuple[NativeTransportBinding, ...]:
    if transports is None:
        bindings = tuple(
            load_native_transport_binding(provider.transport_name) for provider in discover_native_transport_providers()
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
def listen_native_server(
    options: NativeServerBootstrapOptions,
    *,
    _transports: Sequence[NativeTransportBinding] | None = None,
) -> Iterator[NativeServer]:
    if not isinstance(options, NativeServerBootstrapOptions):
        raise TypeError("options must be NativeServerBootstrapOptions")
    application_endpoint = options.endpoint
    routes = _resolve_server_routes(
        application_endpoint,
        options.provider_routes,
        options.transport_policy,
        _transports,
    )
    session_options = options.session_defaults

    def evaluate_policy(open_metadata: SessionOpenMetadata) -> tuple[bool, int, str | None]:
        decision = _evaluate_session_policy(session_options.application_policy, open_metadata)
        if not isinstance(decision, NativeServerSessionPolicyDecision):
            raise TypeError("application policy must return NativeServerSessionPolicyDecision")
        return decision.accepted, decision.session_error_code, decision.diagnostic

    policy_callback = None if session_options.application_policy is _ACCEPT_VALID_SESSIONS else evaluate_policy
    adopted: list[tuple[str, NativeRuntimeServer]] = []
    bound_endpoints: dict[str, NativeTransportEndpoint] = {}
    try:
        for route in routes:
            binding = route.binding
            listener = binding._listen(route.endpoint, route.security, 0, 0)
            bound_endpoint = listener.endpoint
            try:
                runtime_server = binding.adopt_server(
                    listener,
                    server_id=_allocate_native_handle_id(),
                    generation=1,
                    supported_profiles=session_options.supported_profiles,
                    supported_cache_objects=session_options.supported_cache_objects,
                    max_cache_objects=session_options.max_cache_objects,
                    max_cache_object_bytes=session_options.max_cache_object_bytes,
                    resume_token_bytes=session_options.resume_token_bytes,
                    max_in_flight_operations=session_options.max_in_flight_operations,
                    granted_operation_credit=session_options.granted_operation_credit,
                    lease_ttl_ms=session_options.lease_ttl_ms,
                    resume_window_ms=session_options.resume_window_ms,
                    schema_descriptors=session_options.schema_registry.descriptors(),
                    application_policy=policy_callback,
                )
            except BaseException:
                listener._close()
                raise
            adopted.append((binding.kind, runtime_server))
            bound_endpoints[binding.kind] = bound_endpoint
    except BaseException:
        for _transport_name, runtime_server in reversed(adopted):
            if not runtime_server._closed:
                runtime_server.close()
        raise

    server = NativeServer(tuple(adopted), MappingProxyType(bound_endpoints))
    try:
        yield server
    finally:
        server.close()
