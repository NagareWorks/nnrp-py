from __future__ import annotations

from types import SimpleNamespace

import pytest

import nnrp.server.native as server_native_module
from nnrp.native import NativeArtifactError, parse_native_transport_endpoint
from nnrp.server import NativeServerAcceptOptions, NativeServerOptions, listen_native_server


class FakeRuntimeServerSession:
    def __init__(self) -> None:
        self._closed = False

    def close(self) -> None:
        self._closed = True


class FakeRuntimeServer:
    def __init__(self) -> None:
        self._closed = False
        self.accept_calls: list[tuple[int, int, int]] = []
        self.sessions: list[FakeRuntimeServerSession] = []

    def accept_session(
        self,
        *,
        session_handle_id: int,
        generation: int,
        timeout_ms: int,
    ) -> FakeRuntimeServerSession:
        self.accept_calls.append((session_handle_id, generation, timeout_ms))
        session = FakeRuntimeServerSession()
        self.sessions.append(session)
        return session

    def close(self) -> None:
        self._closed = True


class FakeListener:
    def __init__(self) -> None:
        self.closed = False

    def _close(self) -> None:
        self.closed = True


class FakeServerBinding:
    def __init__(self, *, adoption_error: Exception | None = None) -> None:
        self.adoption_error = adoption_error
        self.listen_calls: list[tuple[object, object, int, int]] = []
        self.adopt_calls: list[tuple[int, int]] = []
        self.listener = FakeListener()
        self.runtime_server = FakeRuntimeServer()

    def _listen(self, endpoint, security, accept_timeout_ms: int, io_timeout_ms: int) -> FakeListener:
        self.listen_calls.append((endpoint, security, accept_timeout_ms, io_timeout_ms))
        return self.listener

    def adopt_server(self, listener: FakeListener, *, server_id: int, generation: int) -> FakeRuntimeServer:
        assert listener is self.listener
        self.adopt_calls.append((server_id, generation))
        if self.adoption_error is not None:
            raise self.adoption_error
        return self.runtime_server


def test_listen_native_server_owns_listener_and_role_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    binding = FakeServerBinding()
    monkeypatch.setattr(server_native_module, "_select_server_transport", lambda **_kwargs: "ipc")
    monkeypatch.setattr(server_native_module, "load_native_transport_binding", lambda _name: binding)

    with listen_native_server(
        "nnrp://render-worker",
        provider_endpoint="unix:///tmp/nnrp-render.sock",
        transport="ipc",
        options=NativeServerOptions(server_id=7, server_generation=2),
    ) as server:
        session = server.accept(
            NativeServerAcceptOptions(
                session_handle_id=11,
                session_generation=3,
                timeout_ms=250,
            )
        )
        assert session._closed is False
        assert binding.listener.closed is False

    endpoint, security, accept_timeout_ms, io_timeout_ms = binding.listen_calls[0]
    assert endpoint == parse_native_transport_endpoint("unix:///tmp/nnrp-render.sock")
    assert security is None
    assert (accept_timeout_ms, io_timeout_ms) == (0, 0)
    assert binding.adopt_calls == [(7, 2)]
    assert binding.runtime_server.accept_calls == [(11, 3, 250)]
    assert session._closed is True
    assert binding.runtime_server._closed is True
    assert binding.listener.closed is False


def test_listen_native_server_closes_listener_when_role_adoption_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    binding = FakeServerBinding(adoption_error=NativeArtifactError("role adoption failed"))
    monkeypatch.setattr(server_native_module, "_select_server_transport", lambda **_kwargs: "tcp")
    monkeypatch.setattr(server_native_module, "load_native_transport_binding", lambda _name: binding)

    with pytest.raises(NativeArtifactError, match="role adoption failed"):
        with listen_native_server("nnrp://localhost", transport="tcp"):
            pass

    assert binding.listener.closed is True
    assert binding.runtime_server._closed is False


def test_listen_native_server_requires_provider_endpoint_for_ipc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_native_module, "_select_server_transport", lambda **_kwargs: "ipc")

    with pytest.raises(NativeArtifactError, match="ipc requires an explicit provider_endpoint"):
        with listen_native_server("nnrp://render-worker", transport="ipc"):
            pass


def test_native_server_rejects_accept_after_close(monkeypatch: pytest.MonkeyPatch) -> None:
    binding = FakeServerBinding()
    monkeypatch.setattr(server_native_module, "_select_server_transport", lambda **_kwargs: "tcp")
    monkeypatch.setattr(server_native_module, "load_native_transport_binding", lambda _name: binding)

    with listen_native_server("nnrp://localhost", transport="tcp") as server:
        server.close()
        with pytest.raises(RuntimeError, match="native server is closed"):
            server.accept()


def test_select_server_transport_rejects_provider_endpoint_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        server_native_module,
        "resolve_native_transport_provider",
        lambda _name: SimpleNamespace(name="tcp"),
    )
    monkeypatch.setattr(
        server_native_module,
        "select_native_transport_provider",
        lambda *_args, **_kwargs: SimpleNamespace(selected_transport_name="tcp"),
    )

    with pytest.raises(NativeArtifactError, match="tcp provider cannot use websocket carrier endpoint"):
        server_native_module._select_server_transport(
            provider_endpoint=parse_native_transport_endpoint("ws://localhost/nnrp"),
            transport_policy="auto",
            transport="tcp",
        )


def test_select_server_transport_validates_explicit_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    selections: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        server_native_module,
        "resolve_native_transport_provider",
        lambda _name: SimpleNamespace(name="quic"),
    )
    monkeypatch.setattr(
        server_native_module,
        "select_native_transport_provider",
        lambda *_args, **kwargs: selections.append(kwargs["supported_transports"]),
    )

    selected = server_native_module._select_server_transport(
        provider_endpoint=None,
        transport_policy="auto",
        transport="quic",
    )

    assert selected == "quic"
    assert selections == [("quic",)]


def test_select_server_transport_returns_direct_auto_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        server_native_module,
        "select_native_transport_provider",
        lambda *_args, **_kwargs: SimpleNamespace(selected_transport_name="tcp"),
    )

    assert (
        server_native_module._select_server_transport(
            provider_endpoint=None,
            transport_policy="auto",
            transport=None,
        )
        == "tcp"
    )


def test_select_server_transport_falls_back_deterministically_without_probe_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = (
        SimpleNamespace(
            transport_name="quic",
            transport_id=2,
            provider=SimpleNamespace(preference_rank=20, id="quic-provider"),
            rejection_reason=SimpleNamespace(value="probe-missing"),
        ),
        SimpleNamespace(
            transport_name="tcp",
            transport_id=1,
            provider=SimpleNamespace(preference_rank=10, id="tcp-provider"),
            rejection_reason=SimpleNamespace(value="probe-missing"),
        ),
        SimpleNamespace(
            transport_name="websocket",
            transport_id=4,
            provider=SimpleNamespace(preference_rank=1, id="websocket-provider"),
            rejection_reason=SimpleNamespace(value="probe-missing"),
        ),
    )
    monkeypatch.setattr(
        server_native_module,
        "select_native_transport_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            NativeArtifactError("probe samples required", candidates=candidates)
        ),
    )

    selected = server_native_module._select_server_transport(
        provider_endpoint=None,
        transport_policy="auto",
        transport=None,
    )

    assert selected == "tcp"


def test_select_server_transport_preserves_error_without_probe_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server_native_module,
        "select_native_transport_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            NativeArtifactError(
                "provider rejected",
                candidates=(
                    SimpleNamespace(
                        transport_name="tcp",
                        rejection_reason=SimpleNamespace(value="unsupported"),
                    ),
                ),
            )
        ),
    )

    with pytest.raises(NativeArtifactError, match="provider rejected"):
        server_native_module._select_server_transport(
            provider_endpoint=None,
            transport_policy="auto",
            transport=None,
        )
