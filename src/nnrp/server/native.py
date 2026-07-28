"""Server-facing native role lifecycle helpers."""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType

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
from nnrp.core import TransportPolicy
from nnrp.native import (
    FFI_STATUS_WOULD_BLOCK,
    NATIVE_TRANSPORT_ID_BY_NAME,
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
    discover_native_transport_providers,
    load_native_transport_binding,
    parse_nnrp_endpoint,
    resolve_native_transport_endpoint,
)

_DEFAULT_ACCEPT_TIMEOUT_MS = 5_000
_ACCEPT_POLL_SLICE_MS = 1


@dataclass(frozen=True, slots=True)
class NativeServerOptions:
    server_id: int = 1
    server_generation: int = 1


@dataclass(frozen=True, slots=True)
class NativeServerAcceptOptions:
    session_handle_id: int = 1
    session_generation: int = 1
    timeout_ms: int = 0


@dataclass(frozen=True, slots=True)
class NativeServerProviderRoute:
    provider_endpoint: str | NativeTransportEndpoint | None = None
    security: NativeTransportServerSecurity | None = None


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
    _closed: bool = field(default=False, init=False, repr=False)

    def accept(self, options: NativeServerAcceptOptions | None = None) -> NativeRuntimeServerSession:
        self._ensure_open()
        resolved = options or NativeServerAcceptOptions()
        timeout_ms = resolved.timeout_ms or _DEFAULT_ACCEPT_TIMEOUT_MS
        deadline = time.monotonic() + timeout_ms / 1_000
        while True:
            for _transport_name, server in self._servers:
                try:
                    session = server.accept_session(
                        session_handle_id=resolved.session_handle_id,
                        generation=resolved.session_generation,
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
                self._sessions.append(session)
                return session
            if time.monotonic() >= deadline:
                raise NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))

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
        transport_name=binding.provider.name,
        transport_id=NATIVE_TRANSPORT_ID_BY_NAME[binding.provider.name],
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
    providers_by_name = {provider.name: provider for provider in providers}
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
            resolved_routes.append(
                _ResolvedServerRoute(binding, carrier_endpoint, route.security)
            )
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
            load_native_transport_binding(provider.name)
            for provider in discover_native_transport_providers()
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
    endpoint: str | NnrpEndpoint,
    *,
    provider_routes: Mapping[str, NativeServerProviderRoute] | None = None,
    transports: Sequence[NativeTransportBinding] | None = None,
    transport_policy: TransportPolicy | str | int = TransportPolicy.AUTO,
    options: NativeServerOptions | None = None,
    require_native: bool = False,
) -> Iterator[NativeServer]:
    del require_native
    application_endpoint = endpoint if isinstance(endpoint, NnrpEndpoint) else parse_nnrp_endpoint(endpoint)
    routes = _resolve_server_routes(application_endpoint, provider_routes, transport_policy, transports)
    resolved_options = options or NativeServerOptions()
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
                    server_id=resolved_options.server_id,
                    generation=resolved_options.server_generation,
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
