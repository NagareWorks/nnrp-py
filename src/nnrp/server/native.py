"""Server-facing native role lifecycle helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from nnrp.core import TransportPolicy
from nnrp.native import (
    NativeArtifactError,
    NativeRuntimeServer,
    NativeRuntimeServerSession,
    NativeTransportEndpoint,
    NativeTransportServerSecurity,
    NnrpEndpoint,
    load_native_transport_binding,
    parse_native_transport_endpoint,
    parse_nnrp_endpoint,
    resolve_native_transport_endpoint,
    resolve_native_transport_provider,
    select_native_transport_provider,
)


@dataclass(frozen=True, slots=True)
class NativeServerOptions:
    server_id: int = 1
    server_generation: int = 1


@dataclass(frozen=True, slots=True)
class NativeServerAcceptOptions:
    session_handle_id: int = 1
    session_generation: int = 1
    timeout_ms: int = 0


@dataclass(slots=True)
class NativeServer:
    server: NativeRuntimeServer
    _sessions: list[NativeRuntimeServerSession] = field(default_factory=list, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def accept(self, options: NativeServerAcceptOptions | None = None) -> NativeRuntimeServerSession:
        self._ensure_open()
        resolved = options or NativeServerAcceptOptions()
        session = self.server.accept_session(
            session_handle_id=resolved.session_handle_id,
            generation=resolved.session_generation,
            timeout_ms=resolved.timeout_ms,
        )
        self._sessions.append(session)
        return session

    def close(self) -> None:
        if self._closed:
            return
        for session in reversed(self._sessions):
            if not session._closed:
                session.close()
        if not self.server._closed:
            self.server.close()
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("native server is closed")


def _select_server_transport(
    *,
    provider_endpoint: NativeTransportEndpoint | None,
    transport_policy: TransportPolicy | str | int,
    transport: str | None,
) -> str:
    if transport is not None:
        provider = resolve_native_transport_provider(transport)
        select_native_transport_provider(
            transport_policy,
            supported_transports=(provider.name,),
        )
        if provider_endpoint is not None and provider_endpoint.transport_name != provider.name:
            raise NativeArtifactError(
                f"{provider.name} provider cannot use {provider_endpoint.transport_name} carrier endpoint"
            )
        return provider.name

    supported = {provider_endpoint.transport_name} if provider_endpoint is not None else {"tcp", "quic"}
    try:
        return select_native_transport_provider(
            transport_policy,
            supported_transports=tuple(supported),
        ).selected_transport_name
    except NativeArtifactError as error:
        eligible = tuple(
            candidate
            for candidate in error.candidates
            if candidate.transport_name in supported
            and candidate.rejection_reason is not None
            and candidate.rejection_reason.value == "probe-missing"
        )
        if not eligible:
            raise
        return min(
            eligible,
            key=lambda candidate: (
                candidate.provider.preference_rank,
                int(candidate.transport_id),
                candidate.provider.id,
            ),
        ).transport_name


@contextmanager
def listen_native_server(
    endpoint: str | NnrpEndpoint,
    *,
    provider_endpoint: str | NativeTransportEndpoint | None = None,
    transport_policy: TransportPolicy | str | int = TransportPolicy.AUTO,
    transport: str | None = None,
    security: NativeTransportServerSecurity | None = None,
    options: NativeServerOptions | None = None,
    require_native: bool = False,
) -> Iterator[NativeServer]:
    del require_native
    application_endpoint = endpoint if isinstance(endpoint, NnrpEndpoint) else parse_nnrp_endpoint(endpoint)
    carrier_override = (
        provider_endpoint
        if isinstance(provider_endpoint, NativeTransportEndpoint)
        else parse_native_transport_endpoint(provider_endpoint)
        if provider_endpoint is not None
        else None
    )
    transport_name = _select_server_transport(
        provider_endpoint=carrier_override,
        transport_policy=transport_policy,
        transport=transport,
    )
    carrier_endpoint = resolve_native_transport_endpoint(
        application_endpoint,
        transport_name,
        provider_endpoint=carrier_override,
    )
    binding = load_native_transport_binding(transport_name)
    listener = binding._listen(carrier_endpoint, security, 0, 0)
    resolved_options = options or NativeServerOptions()
    try:
        runtime_server = binding.adopt_server(
            listener,
            server_id=resolved_options.server_id,
            generation=resolved_options.server_generation,
        )
    except BaseException:
        listener._close()
        raise
    server = NativeServer(runtime_server)
    try:
        yield server
    finally:
        server.close()
