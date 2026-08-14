from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nnrp._native_routes import official_provider_metadata
from nnrp.native import (
    NATIVE_TRANSPORT_ID_BY_NAME,
    NativeArtifactError,
    NativeTransportBinding,
    NativeTransportCandidateDiagnostic,
    NativeTransportProbeState,
    NativeTransportProvider,
    NativeTransportProviderKind,
    NativeTransportRejectionReason,
    NativeTransportSelectionError,
    NativeTransportSelectionErrorCode,
)
from nnrp.tools import host_route_target


def route(
    transport: str = "tcp",
    *,
    provider_id: str | None = None,
    failures: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "transport": transport,
        "provider_id": provider_id or f"nnrp.transport.{transport}.native",
        "locator": f"suite://allocate/{transport}/test",
        "security": {"mode": "plain", "credential_owner": "none"},
        "injected_failures": list(failures),
    }


def candidate(
    transport: str,
    *,
    provider_id: str | None = None,
    rejection: NativeTransportRejectionReason | None = None,
) -> NativeTransportCandidateDiagnostic:
    metadata = official_provider_metadata(transport)
    if provider_id is not None:
        metadata = replace(metadata, id=provider_id)
    return NativeTransportCandidateDiagnostic(
        transport_id=NATIVE_TRANSPORT_ID_BY_NAME[transport],
        provider=metadata,
        local_available=rejection is not NativeTransportRejectionReason.LOCAL_UNAVAILABLE,
        peer_supported=True,
        within_limits=True,
        probe_state=NativeTransportProbeState.NOT_RUN,
        rejection_reason=rejection,
    )


def provider(transport: str, tmp_path: Path) -> NativeTransportProvider:
    return NativeTransportProvider(
        name=f"nnrp-ffi-transport-{transport}",
        version="4.4.0",
        transport_id=NATIVE_TRANSPORT_ID_BY_NAME[transport],
        kind=NativeTransportProviderKind.NATIVE_DYNAMIC,
        available=True,
        library_path=str(tmp_path / "libnnrp_ffi"),
        metadata=official_provider_metadata(transport),
    )


def scenario(
    role: str,
    routes: list[dict[str, object]],
    *,
    case_id: str = "wire.host-route.test",
) -> dict[str, object]:
    return {
        "id": case_id,
        "host_route": {
            "role": role,
            "application_endpoint": "nnrp://host-route.test/runtime/default",
            "routes": routes,
        },
    }


def test_validated_routes_rejects_duplicate_provider_identity() -> None:
    fixture = {"routes": [route("tcp"), route("ipc", provider_id="nnrp.transport.tcp.native")]}

    with pytest.raises(ValueError, match="repeats"):
        host_route_target._validated_routes(fixture)


def test_client_evidence_preserves_unavailable_provider_identity() -> None:
    provider_id = "example.transport.quic.uninstalled"
    unavailable_route = route("quic", provider_id=provider_id)

    evidence = host_route_target._client_evidence(
        {"application_endpoint": "nnrp://host-route.test"},
        (unavailable_route,),
        (
            candidate(
                "quic",
                provider_id=provider_id,
                rejection=NativeTransportRejectionReason.LOCAL_UNAVAILABLE,
            ),
        ),
        selected_provider=None,
    )

    assert evidence["candidates"] == [
        {
            "transport": "quic",
            "provider_id": provider_id,
            "requested_locator": "suite://allocate/quic/test",
            "locator_resolved": True,
            "security_satisfied": True,
            "selected": False,
            "rejection_reason": "local-unavailable",
        }
    ]


def test_client_locator_injects_transport_mismatch_for_unresolved_route() -> None:
    resolved = {"locator": "tcp://127.0.0.1:12345"}

    assert host_route_target._client_locator(route("tcp", failures=("route_unresolved",)), resolved).startswith(
        "unix://"
    )
    assert host_route_target._client_locator(route("ipc", failures=("route_unresolved",)), resolved).startswith(
        "tcp://"
    )


def test_bind_failure_proxy_never_invokes_wrapped_listener() -> None:
    wrapped = SimpleNamespace(
        kind="tcp",
        provider=SimpleNamespace(metadata=SimpleNamespace(id="nnrp.transport.tcp.native")),
        local_available=True,
        diagnostic=None,
        _listen=lambda *_args: (_ for _ in ()).throw(AssertionError("wrapped listener invoked")),
    )
    binding = host_route_target._BindFailureBinding(wrapped)

    with pytest.raises(NativeArtifactError, match="injected bind failure"):
        binding._listen("tcp://127.0.0.1:0", None, 0, 0)


def test_terminal_failure_runtime_server_closes_wrapped_server() -> None:
    wrapped = SimpleNamespace(close_calls=0)

    def close() -> None:
        wrapped.close_calls += 1

    wrapped.close = close
    server = host_route_target._TerminalFailureRuntimeServer(wrapped, "nnrp.transport.tcp.native")

    with pytest.raises(NativeArtifactError, match="terminal listener failure"):
        server.accept_session()
    server.close()
    server.close()

    assert wrapped.close_calls == 1


def test_ready_report_is_written_with_actual_bound_endpoints(tmp_path: Path) -> None:
    output = tmp_path / "target-ready.json"

    host_route_target._write_ready_report(
        {"id": "wire.host-route.server.multi-listener"},
        (route("tcp"),),
        {"tcp": "tcp://127.0.0.1:43210"},
        output,
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["listeners"] == [
        {
            "transport": "tcp",
            "provider_id": "nnrp.transport.tcp.native",
            "bound_endpoint": "tcp://127.0.0.1:43210",
        }
    ]


def test_binding_proxy_delegates_public_and_private_operations() -> None:
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def record(name: str):
        def invoke(*args: Any, **kwargs: Any) -> str:
            calls.append((name, args, kwargs))
            return name

        return invoke

    wrapped = SimpleNamespace(
        kind="tcp",
        provider=SimpleNamespace(metadata=SimpleNamespace(id="nnrp.transport.tcp.native")),
        local_available=True,
        diagnostic=None,
        _probe=record("probe"),
        _connect=record("connect"),
        _listen=record("listen"),
        adopt_client=record("adopt_client"),
        adopt_server=record("adopt_server"),
    )
    binding = host_route_target._BindingProxy(wrapped)

    assert binding.kind == "tcp"
    assert binding.provider is wrapped.provider
    assert binding.local_available is True
    assert binding.diagnostic is None
    assert binding._probe(1) == "probe"
    assert binding._connect(2) == "connect"
    assert binding._listen(3) == "listen"
    assert binding.adopt_client(4, generation=5) == "adopt_client"
    assert binding.adopt_server(6, generation=7) == "adopt_server"
    assert [call[0] for call in calls] == ["probe", "connect", "listen", "adopt_client", "adopt_server"]


def test_terminal_failure_binding_wraps_adopted_server() -> None:
    server = SimpleNamespace(close=lambda: None)
    wrapped = SimpleNamespace(
        provider=SimpleNamespace(metadata=SimpleNamespace(id="nnrp.transport.tcp.native")),
        adopt_server=lambda *_args, **_kwargs: server,
    )

    adopted = host_route_target._TerminalFailureBinding(wrapped).adopt_server(1, generation=2)

    assert isinstance(adopted, host_route_target._TerminalFailureRuntimeServer)
    with pytest.raises(NativeArtifactError, match="terminal listener failure"):
        adopted.accept_session()


def test_main_writes_success_and_failure_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = scenario("client", [route("tcp")])
    scenario_path = tmp_path / "scenario.json"
    resolved_path = tmp_path / "resolved.json"
    output_path = tmp_path / "result.json"
    ready_path = tmp_path / "ready.json"
    scenario_path.write_text(json.dumps(case), encoding="utf-8")
    resolved_path.write_text(json.dumps(case), encoding="utf-8")
    expected = {"id": case["id"], "outcome": "passed", "terminal": "success"}
    monkeypatch.setattr(host_route_target, "_run_case", lambda *_args, **_kwargs: expected)
    arguments = [
        "--scenario",
        str(scenario_path),
        "--resolved-scenario",
        str(resolved_path),
        "--output",
        str(output_path),
        "--ready-output",
        str(ready_path),
        "--artifacts",
        str(tmp_path),
        "--suite-version",
        "preview4-test",
        "--target-name",
        "nnrp-py-test",
    ]

    assert host_route_target.main(arguments) == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["results"] == [expected]
    assert report["suite_version"] == "preview4-test"

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("injected target failure")

    monkeypatch.setattr(host_route_target, "_run_case", fail)
    assert host_route_target.main(arguments) == 0
    failed = json.loads(output_path.read_text(encoding="utf-8"))["results"][0]
    assert failed == {
        "id": case["id"],
        "outcome": "failed",
        "terminal": "error",
        "message": "injected target failure",
    }


def test_run_case_dispatches_roles_and_rejects_identity_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_case = scenario("client", [route("tcp")])
    server_case = scenario("server", [route("tcp")])
    monkeypatch.setattr(host_route_target, "_run_client_case", lambda *_args: {"role": "client"})
    monkeypatch.setattr(host_route_target, "_run_server_case", lambda *_args: {"role": "server"})

    assert host_route_target._run_case(
        client_case, client_case, artifacts=tmp_path, ready_output=tmp_path / "ready.json"
    ) == {"role": "client"}
    assert host_route_target._run_case(
        server_case, server_case, artifacts=tmp_path, ready_output=tmp_path / "ready.json"
    ) == {"role": "server"}

    changed_id = dict(client_case, id="wire.host-route.changed")
    with pytest.raises(ValueError, match="changes the scenario id"):
        host_route_target._run_case(client_case, changed_id, artifacts=tmp_path, ready_output=tmp_path / "ready.json")
    changed_provider = scenario("client", [route("tcp", provider_id="example.transport.tcp")])
    with pytest.raises(ValueError, match="changes provider identities"):
        host_route_target._run_case(
            client_case, changed_provider, artifacts=tmp_path, ready_output=tmp_path / "ready.json"
        )
    unsupported = scenario("proxy", [route("tcp")])
    with pytest.raises(ValueError, match="unsupported host-route role"):
        host_route_target._run_case(unsupported, unsupported, artifacts=tmp_path, ready_output=tmp_path / "ready.json")


def test_run_client_case_reports_selection_and_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = scenario("client", [route("tcp")])
    fixture = case["host_route"]
    assert isinstance(fixture, dict)
    routes = (route("tcp"),)
    resolved_routes = (dict(route("tcp"), locator="tcp://127.0.0.1:43210"),)
    selected_candidate = candidate("tcp")
    selection = SimpleNamespace(
        candidates=(selected_candidate,),
        selected_provider=SimpleNamespace(metadata=SimpleNamespace(id="nnrp.transport.tcp.native")),
    )
    session = SimpleNamespace(close=lambda: None)

    async def open_session() -> object:
        return session

    connection = SimpleNamespace(transport_selection=selection, open_session=open_session)

    class Scope:
        def __enter__(self) -> Any:
            return connection

        def __exit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(host_route_target, "_binding_for_route", lambda _route: SimpleNamespace())
    monkeypatch.setattr(host_route_target, "connect_native_client_connection", lambda *_args, **_kwargs: Scope())

    result = host_route_target._run_client_case(case, fixture, routes, resolved_routes, tmp_path)
    assert result["outcome"] == "passed"
    assert result["terminal"] == "success"
    assert result["route_evidence"]["accepted_sessions"][0]["active_transport"] == "tcp"

    rejection = candidate("tcp", rejection=NativeTransportRejectionReason.ROUTE_UNRESOLVED)

    def reject(*_args: Any, **_kwargs: Any) -> None:
        raise NativeTransportSelectionError(
            NativeTransportSelectionErrorCode.NO_VIABLE_TRANSPORT,
            "no route",
            candidates=(rejection,),
        )

    monkeypatch.setattr(host_route_target, "connect_native_client_connection", reject)
    rejected = host_route_target._run_client_case(case, fixture, routes, resolved_routes, tmp_path)
    assert rejected["terminal"] == "error"
    assert rejected["route_evidence"]["candidates"][0]["rejection_reason"] == "route-unresolved"


def test_run_server_case_reports_success_terminal_failure_and_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(host_route_target, "_binding_for_route", lambda _route: SimpleNamespace())
    success_case = scenario("server", [route("tcp")])
    success_fixture = success_case["host_route"]
    assert isinstance(success_fixture, dict)
    routes = (route("tcp"),)
    resolved_routes = (dict(route("tcp"), locator="tcp://127.0.0.1:0"),)
    session = SimpleNamespace(active_transport_name="tcp", close=lambda: None)
    async def accept(_opts: object) -> object:
        return session

    server = SimpleNamespace(bound_provider_endpoints={"tcp": "tcp://127.0.0.1:43210"}, accept=accept)

    @contextmanager
    def listen(*_args: Any, **_kwargs: Any):
        yield server

    monkeypatch.setattr(host_route_target, "listen_native_server", listen)
    result = host_route_target._run_server_case(
        success_case,
        success_fixture,
        routes,
        resolved_routes,
        tmp_path,
        tmp_path / "ready-success.json",
    )
    assert result["terminal"] == "success"
    assert result["route_evidence"]["accepted_sessions"][0]["provider_id"] == "nnrp.transport.tcp.native"

    terminal_routes = (route("tcp", failures=("terminal_listener_failure",)),)
    terminal_case = scenario("server", list(terminal_routes))
    terminal_fixture = terminal_case["host_route"]
    assert isinstance(terminal_fixture, dict)
    async def terminal_accept(_opts: object) -> object:
        raise NativeArtifactError("terminal closed")

    terminal_server = SimpleNamespace(
        bound_provider_endpoints={"tcp": "tcp://127.0.0.1:43211"},
        accept=terminal_accept,
    )

    @contextmanager
    def listen_terminal(*_args: Any, **_kwargs: Any):
        yield terminal_server

    monkeypatch.setattr(host_route_target, "listen_native_server", listen_terminal)
    terminal = host_route_target._run_server_case(
        terminal_case,
        terminal_fixture,
        terminal_routes,
        resolved_routes,
        tmp_path,
        tmp_path / "ready-terminal.json",
    )
    assert terminal["terminal"] == "error"
    assert terminal["route_evidence"]["logical_set_closed"] is True

    rollback_routes = (route("tcp"), route("ipc", failures=("bind_failure",)))
    rollback_case = scenario("server", list(rollback_routes))
    rollback_fixture = rollback_case["host_route"]
    assert isinstance(rollback_fixture, dict)

    def fail_listen(*_args: Any, **_kwargs: Any) -> None:
        raise NativeArtifactError("bind failed")

    monkeypatch.setattr(host_route_target, "listen_native_server", fail_listen)
    rollback = host_route_target._run_server_case(
        rollback_case,
        rollback_fixture,
        rollback_routes,
        (
            dict(route("tcp"), locator="tcp://127.0.0.1:0"),
            dict(route("ipc"), locator="npipe://nnrp-test"),
        ),
        tmp_path,
        tmp_path / "ready-rollback.json",
    )
    assert rollback["route_evidence"]["atomic_rollback"] is True
    assert [item["state"] for item in rollback["route_evidence"]["listeners"]] == [
        "rolled_back",
        "failed",
    ]


def test_binding_and_security_helpers_preserve_frozen_provider_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quic_provider = provider("quic", tmp_path)
    monkeypatch.setattr(host_route_target, "discover_native_transport_providers", lambda: (quic_provider,))
    unavailable = host_route_target._binding_for_route(route("quic", provider_id="example.transport.quic.uninstalled"))
    assert unavailable.local_available is False
    assert unavailable.provider.metadata.id == "example.transport.quic.uninstalled"

    tcp_provider = provider("tcp", tmp_path)
    loaded = NativeTransportBinding.unavailable(tcp_provider, "test binding")
    monkeypatch.setattr(host_route_target, "load_native_transport_binding", lambda _transport: loaded)
    assert host_route_target._binding_for_route(route("tcp")) is loaded
    drifted = NativeTransportBinding.unavailable(
        replace(tcp_provider, metadata=replace(tcp_provider.metadata, id="example.transport.tcp")),
        "test binding",
    )
    monkeypatch.setattr(host_route_target, "load_native_transport_binding", lambda _transport: drifted)
    with pytest.raises(ValueError, match="identity drifted"):
        host_route_target._binding_for_route(route("tcp"))
    with pytest.raises(ValueError, match="unsupported Python host-route provider"):
        host_route_target._binding_for_route(route("tcp", provider_id="example.transport.unsupported"))

    (tmp_path / "server.der").write_bytes(b"certificate")
    (tmp_path / "server-key.der").write_bytes(b"private-key")
    tls_route = route("tcp")
    tls_route["security"] = {"mode": "tls_server_auth"}
    assert host_route_target._client_security(tls_route, tmp_path) is not None
    assert host_route_target._server_security(tls_route, tmp_path) is not None
    incompatible = dict(tls_route, injected_failures=["security_incompatible"])
    assert host_route_target._client_security(incompatible, tmp_path) is None
    invalid = dict(tls_route, security={"mode": "invalid"})
    with pytest.raises(ValueError, match="unsupported client security mode"):
        host_route_target._client_security(invalid, tmp_path)
    with pytest.raises(ValueError, match="unsupported server security mode"):
        host_route_target._server_security(invalid, tmp_path)


def test_transport_binding_availability_state_cannot_be_ambiguous(tmp_path: Path) -> None:
    tcp_provider = provider("tcp", tmp_path)

    with pytest.raises(ValueError, match="require a diagnostic"):
        NativeTransportBinding(None, tcp_provider)
    with pytest.raises(ValueError, match="must not declare"):
        NativeTransportBinding(object(), tcp_provider, unavailable_diagnostic="unexpected")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ({}, "requires routes"),
        ({"routes": "tcp"}, "requires routes"),
        ({"routes": ["tcp"]}, "entries must be objects"),
    ],
)
def test_validated_routes_rejects_invalid_shapes(fixture: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        host_route_target._validated_routes(fixture)


def test_input_and_evidence_helpers_reject_malformed_documents(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="host_route fixture"):
        host_route_target._host_route_fixture({})
    with pytest.raises(ValueError, match="security must be an object"):
        host_route_target._security_mode(route("tcp") | {"security": "plain"})
    with pytest.raises(ValueError, match="string array"):
        host_route_target._injected_failures(route("tcp") | {"injected_failures": "bind_failure"})
    with pytest.raises(ValueError, match="non-empty string"):
        host_route_target._required_string({}, "id")
    with pytest.raises(ValueError, match="non-empty URI"):
        host_route_target._endpoint_uri(SimpleNamespace(uri=""))
    with pytest.raises(ValueError, match="missing candidate evidence"):
        host_route_target._client_evidence(
            {"application_endpoint": "nnrp://host-route.test"},
            (route("tcp"),),
            (),
            selected_provider=None,
        )

    array_path = tmp_path / "array.json"
    array_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        host_route_target._read_json(array_path)

    assert (
        host_route_target._rollback_policy((route("websocket"), route("tcp", failures=("bind_failure",)))) is not None
    )
