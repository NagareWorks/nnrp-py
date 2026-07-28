from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

import nnrp.server.native as server_native_module
from nnrp._native_routes import official_provider_metadata
from nnrp.native import (
    FFI_STATUS_WOULD_BLOCK,
    NativeArtifactError,
    NativeStatus,
    NativeTransportRejectionReason,
    NativeTransportSelectionError,
    NativeTransportSelectionErrorCode,
    NativeWouldBlockError,
    parse_native_transport_endpoint,
)
from nnrp.server import (
    NativeServerAcceptOptions,
    NativeServerOptions,
    NativeServerProviderRoute,
    listen_native_server,
)


class FakeRuntimeServerSession:
    def __init__(self, transport_name: str) -> None:
        self.active_transport_name = transport_name
        self._closed = False
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self._closed = True


class FakeRuntimeServer:
    def __init__(self, transport_name: str) -> None:
        self.transport_name = transport_name
        self._closed = False
        self.close_calls = 0
        self.accept_calls: list[tuple[int, int, int]] = []
        self.sessions: list[FakeRuntimeServerSession] = []
        self.accept_error: Exception | None = None

    def accept_session(
        self,
        *,
        session_handle_id: int,
        generation: int,
        timeout_ms: int,
    ) -> FakeRuntimeServerSession:
        self.accept_calls.append((session_handle_id, generation, timeout_ms))
        if self.accept_error is not None:
            raise self.accept_error
        session = FakeRuntimeServerSession(self.transport_name)
        self.sessions.append(session)
        return session

    def close(self) -> None:
        self.close_calls += 1
        self._closed = True


class FakeListener:
    def __init__(self, endpoint) -> None:
        self.endpoint = endpoint
        self.closed = False

    def _close(self) -> None:
        self.closed = True


class FakeServerBinding:
    def __init__(
        self,
        transport_name: str,
        *,
        adoption_error: Exception | None = None,
        local_available: bool = True,
        diagnostic: str | None = None,
        provider_id: str | None = None,
    ) -> None:
        self.transport_name = transport_name
        self.adoption_error = adoption_error
        self._local_available = local_available
        self._diagnostic = diagnostic
        self._provider_id = provider_id
        self.listen_calls: list[tuple[object, object, int, int]] = []
        self.adopt_calls: list[tuple[int, int]] = []
        self.listeners: list[FakeListener] = []
        self.runtime_server = FakeRuntimeServer(transport_name)

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

    def _listen(self, endpoint, security, accept_timeout_ms: int, io_timeout_ms: int) -> FakeListener:
        assert self.local_available
        self.listen_calls.append((endpoint, security, accept_timeout_ms, io_timeout_ms))
        listener = FakeListener(endpoint)
        self.listeners.append(listener)
        return listener

    def adopt_server(self, listener: FakeListener, *, server_id: int, generation: int) -> FakeRuntimeServer:
        assert listener is self.listeners[-1]
        self.adopt_calls.append((server_id, generation))
        if self.adoption_error is not None:
            raise self.adoption_error
        return self.runtime_server


def fake_provider(name: str):
    return SimpleNamespace(name=name, metadata=official_provider_metadata(name))


def install_bindings(monkeypatch: pytest.MonkeyPatch, *names: str) -> dict[str, FakeServerBinding]:
    bindings = {name: FakeServerBinding(name) for name in names}
    monkeypatch.setattr(
        server_native_module,
        "discover_native_transport_providers",
        lambda: tuple(fake_provider(name) for name in names),
    )
    monkeypatch.setattr(server_native_module, "load_native_transport_binding", bindings.__getitem__)
    return bindings


def test_listen_native_server_owns_multi_listener_role_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    bindings = install_bindings(monkeypatch, "ipc", "tcp")
    bindings["ipc"].runtime_server.accept_error = NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))

    with listen_native_server(
        "nnrp://localhost:4433/runtime",
        provider_routes={
            "ipc": NativeServerProviderRoute(provider_endpoint="unix:///tmp/nnrp-render.sock"),
        },
        options=NativeServerOptions(server_id=7, server_generation=2),
    ) as server:
        assert dict(server.bound_provider_endpoints) == {
            "ipc": parse_native_transport_endpoint("unix:///tmp/nnrp-render.sock"),
            "tcp": parse_native_transport_endpoint("tcp://localhost:4433"),
        }
        session = server.accept(
            NativeServerAcceptOptions(session_handle_id=11, session_generation=3, timeout_ms=250)
        )
        assert session.active_transport_name == "tcp"

    assert bindings["ipc"].adopt_calls == [(7, 2)]
    assert bindings["tcp"].adopt_calls == [(7, 2)]
    assert bindings["ipc"].runtime_server.accept_calls == [(11, 3, 1)]
    assert bindings["tcp"].runtime_server.accept_calls == [(11, 3, 1)]
    assert session._closed is True
    assert session.close_calls == 1
    assert all(binding.runtime_server._closed for binding in bindings.values())
    assert all(binding.runtime_server.close_calls == 1 for binding in bindings.values())


def test_explicit_server_transport_bindings_are_authoritative(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeServerBinding("tcp")
    monkeypatch.setattr(
        server_native_module,
        "discover_native_transport_providers",
        lambda: (_ for _ in ()).throw(AssertionError("explicit transports must bypass discovery")),
    )

    resolved = server_native_module._resolve_server_transport_bindings((transport,))

    assert resolved == (transport,)


def test_explicit_server_transport_bindings_reject_duplicate_kind() -> None:
    with pytest.raises(NativeTransportSelectionError) as caught:
        server_native_module._resolve_server_transport_bindings(
            (FakeServerBinding("tcp"), FakeServerBinding("tcp"))
        )

    assert caught.value.code is NativeTransportSelectionErrorCode.INVALID_EVIDENCE


def test_unavailable_server_binding_preserves_provider_identity_without_listen() -> None:
    provider_id = "example.transport.quic.uninstalled"
    binding = FakeServerBinding(
        "quic",
        local_available=False,
        diagnostic="provider package is not installed",
        provider_id=provider_id,
    )

    with pytest.raises(NativeTransportSelectionError) as caught:
        with listen_native_server(
            "nnrp://localhost",
            transports=(binding,),
            transport_policy="force_quic",
        ):
            pass

    candidate = next(value for value in caught.value.candidates if value.provider.id == provider_id)
    assert candidate.local_available is False
    assert candidate.rejection_reason is NativeTransportRejectionReason.LOCAL_UNAVAILABLE
    assert candidate.diagnostic == "provider package is not installed"
    assert binding.listen_calls == []


def test_listen_native_server_rolls_back_adopted_servers_after_later_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings = install_bindings(monkeypatch, "ipc", "tcp")
    bindings["tcp"].adoption_error = NativeArtifactError("role adoption failed")

    with pytest.raises(NativeArtifactError, match="role adoption failed"):
        with listen_native_server(
            "nnrp://localhost",
            provider_routes={"ipc": NativeServerProviderRoute(provider_endpoint="unix:///tmp/nnrp.sock")},
        ):
            pass

    assert bindings["ipc"].runtime_server._closed is True
    assert bindings["tcp"].listeners[0].closed is True


def test_listen_native_server_requires_route_for_installed_ipc(monkeypatch: pytest.MonkeyPatch) -> None:
    install_bindings(monkeypatch, "ipc", "tcp")

    with pytest.raises(NativeArtifactError) as caught:
        with listen_native_server("nnrp://localhost"):
            pass

    ipc = next(candidate for candidate in caught.value.candidates if candidate.transport_name == "ipc")
    assert ipc.rejection_reason is NativeTransportRejectionReason.ROUTE_UNRESOLVED


def test_listen_native_server_reports_configured_uninstalled_route(monkeypatch: pytest.MonkeyPatch) -> None:
    install_bindings(monkeypatch, "tcp")

    with pytest.raises(NativeArtifactError) as caught:
        with listen_native_server(
            "nnrp://localhost",
            provider_routes={"ipc": NativeServerProviderRoute(provider_endpoint="unix:///tmp/nnrp.sock")},
        ):
            pass

    ipc = next(candidate for candidate in caught.value.candidates if candidate.transport_name == "ipc")
    assert ipc.rejection_reason is NativeTransportRejectionReason.LOCAL_UNAVAILABLE


def test_listen_native_server_filters_security_unsatisfied_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    bindings = install_bindings(monkeypatch, "ipc", "tcp")

    with pytest.raises(NativeArtifactError) as caught:
        with listen_native_server(
            "nnrps://localhost",
            provider_routes={"ipc": NativeServerProviderRoute(provider_endpoint="unix:///tmp/nnrp.sock")},
        ):
            pass

    reasons = {candidate.transport_name: candidate.rejection_reason for candidate in caught.value.candidates}
    assert reasons == {
        "tcp": NativeTransportRejectionReason.SECURITY_UNSATISFIED,
        "ipc": NativeTransportRejectionReason.SECURITY_UNSATISFIED,
    }
    assert all(not binding.listen_calls for binding in bindings.values())


def test_listen_native_server_keeps_security_isolated_per_route(monkeypatch: pytest.MonkeyPatch) -> None:
    bindings = install_bindings(monkeypatch, "tcp", "quic")
    tcp_security = server_native_module.NativeTransportServerSecurity(b"tcp-cert", b"tcp-key")
    quic_security = server_native_module.NativeTransportServerSecurity(b"quic-cert", b"quic-key")

    with listen_native_server(
        "nnrps://localhost",
        provider_routes={
            "tcp": NativeServerProviderRoute(security=tcp_security),
            "quic": NativeServerProviderRoute(security=quic_security),
        },
    ):
        pass

    assert bindings["tcp"].listen_calls[0][1] is tcp_security
    assert bindings["quic"].listen_calls[0][1] is quic_security


def test_native_server_closes_complete_set_after_terminal_listener_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings = install_bindings(monkeypatch, "tcp", "quic")
    bindings["quic"].runtime_server.accept_error = NativeArtifactError("listener failed")
    bindings["tcp"].runtime_server.accept_error = NativeWouldBlockError(NativeStatus(FFI_STATUS_WOULD_BLOCK))

    with listen_native_server(
        "nnrp://localhost",
        provider_routes={
            "quic": NativeServerProviderRoute(
                security=server_native_module.NativeTransportServerSecurity(b"cert", b"key")
            )
        },
    ) as server:
        with pytest.raises(NativeArtifactError, match="listener failed"):
            server.accept(NativeServerAcceptOptions(timeout_ms=20))
        assert server._closed is True
        assert all(binding.runtime_server._closed for binding in bindings.values())


def test_native_server_rejects_accept_after_close(monkeypatch: pytest.MonkeyPatch) -> None:
    install_bindings(monkeypatch, "tcp")

    with listen_native_server("nnrp://localhost") as server:
        server.close()
        with pytest.raises(RuntimeError, match="native server is closed"):
            server.accept()
