"""Independent Preview4 host-route conformance target for the public Python APIs."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from nnrp.client import NativeClientOptions, NativeClientProviderRoute, connect_native_client_connection
from nnrp.core import MessageType, TransportPolicy
from nnrp.native import (
    NativeArtifactError,
    NativeRuntimeError,
    NativeTransportBinding,
    NativeTransportCandidateDiagnostic,
    NativeTransportClientSecurity,
    NativeTransportProvider,
    NativeTransportSelectionError,
    NativeTransportServerSecurity,
    NativeWouldBlockError,
    discover_native_transport_providers,
    load_native_transport_binding,
)
from nnrp.server import (
    NativeServerAcceptOptions,
    NativeServerBootstrapOptions,
    NativeServerProviderRoute,
    listen_native_server,
)

_RESULT_SCHEMA = "https://github.com/NagareWorks/nnrp-conformance/schemas/wire-conformance-case-results.schema.json"
_READY_SCHEMA = "https://github.com/NagareWorks/nnrp-conformance/schemas/wire-host-route-ready.schema.json"
_PROTOCOL_VERSION = "nnrp-1-preview4"
_OFFICIAL_PROVIDER_IDS = {
    "tcp": "nnrp.transport.tcp.native",
    "quic": "nnrp.transport.quic.native",
    "ipc": "nnrp.transport.ipc.native",
    "websocket": "nnrp.transport.websocket.native",
}


class _BindingProxy:
    def __init__(self, binding: NativeTransportBinding) -> None:
        self._binding = binding

    @property
    def kind(self) -> str:
        return self._binding.kind

    @property
    def provider(self) -> NativeTransportProvider:
        return self._binding.provider

    @property
    def local_available(self) -> bool:
        return self._binding.local_available

    @property
    def diagnostic(self) -> str | None:
        return self._binding.diagnostic

    def _probe(self, *args: Any) -> Any:
        return self._binding._probe(*args)

    def _connect(self, *args: Any) -> Any:
        return self._binding._connect(*args)

    def _listen(self, *args: Any) -> Any:
        return self._binding._listen(*args)

    def adopt_client(self, *args: Any, **kwargs: Any) -> Any:
        return self._binding.adopt_client(*args, **kwargs)

    def adopt_server(self, *args: Any, **kwargs: Any) -> Any:
        return self._binding.adopt_server(*args, **kwargs)


class _BindFailureBinding(_BindingProxy):
    def _listen(self, *_args: Any) -> Any:
        raise NativeArtifactError(f"injected bind failure for {self.provider.metadata.id}")


class _TerminalFailureRuntimeServer:
    def __init__(self, server: Any, provider_id: str) -> None:
        self._server = server
        self._provider_id = provider_id
        self._closed = False

    def accept_session(self, **_kwargs: Any) -> Any:
        raise NativeArtifactError(f"injected terminal listener failure for {self._provider_id}")

    def close(self) -> None:
        if self._closed:
            return
        self._server.close()
        self._closed = True


class _TerminalFailureBinding(_BindingProxy):
    def adopt_server(self, *args: Any, **kwargs: Any) -> _TerminalFailureRuntimeServer:
        server = self._binding.adopt_server(*args, **kwargs)
        return _TerminalFailureRuntimeServer(server, self.provider.metadata.id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nnrp-wire-host-route-target")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--resolved-scenario", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ready-output", required=True)
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--suite-version", required=True)
    parser.add_argument("--target-name", required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)

    scenario = _read_json(Path(args.scenario))
    resolved = _read_json(Path(args.resolved_scenario))
    try:
        result = _run_case(
            scenario,
            resolved,
            artifacts=Path(args.artifacts),
            ready_output=Path(args.ready_output),
        )
    except Exception as error:
        result = {
            "id": _required_string(scenario, "id"),
            "outcome": "failed",
            "terminal": "error",
            "message": str(error),
        }
    _write_json(
        Path(args.output),
        {
            "$schema": _RESULT_SCHEMA,
            "protocol_version": _PROTOCOL_VERSION,
            "suite_version": args.suite_version,
            "target_name": args.target_name,
            "results": [result],
        },
    )
    return 0


def _run_case(
    scenario: Mapping[str, Any],
    resolved: Mapping[str, Any],
    *,
    artifacts: Path,
    ready_output: Path,
) -> dict[str, Any]:
    fixture = _host_route_fixture(scenario)
    resolved_fixture = _host_route_fixture(resolved)
    if scenario.get("id") != resolved.get("id"):
        raise ValueError("resolved host-route scenario changes the scenario id")
    routes = _validated_routes(fixture)
    resolved_routes = _validated_routes(resolved_fixture)
    if [(route["transport"], route["provider_id"]) for route in routes] != [
        (route["transport"], route["provider_id"]) for route in resolved_routes
    ]:
        raise ValueError("resolved host-route scenario changes provider identities")
    role = _required_string(fixture, "role")
    if role == "client":
        return _run_client_case(scenario, fixture, routes, resolved_routes, artifacts)
    if role == "server":
        return _run_server_case(scenario, fixture, routes, resolved_routes, artifacts, ready_output)
    raise ValueError(f"unsupported host-route role: {role}")


def _run_client_case(
    scenario: Mapping[str, Any],
    fixture: Mapping[str, Any],
    routes: Sequence[Mapping[str, Any]],
    resolved_routes: Sequence[Mapping[str, Any]],
    artifacts: Path,
) -> dict[str, Any]:
    provider_routes: dict[str, NativeClientProviderRoute] = {}
    bindings = []
    for route, resolved in zip(routes, resolved_routes, strict=True):
        transport = _required_string(route, "transport")
        provider_routes[transport] = NativeClientProviderRoute(
            provider_endpoint=_client_locator(route, resolved),
            security=_client_security(route, artifacts),
        )
        bindings.append(_binding_for_route(route))

    application_endpoint = _required_string(fixture, "application_endpoint")
    try:
        connection_scope = connect_native_client_connection(
            NativeClientOptions(
                endpoint=application_endpoint,
                provider_routes=provider_routes,
                transport_policy=TransportPolicy.AUTO,
            ),
            transports=tuple(bindings),
        )
        connection = connection_scope.__enter__()
        try:
            selection = connection.transport_selection
            session = asyncio.run(connection.open_session())
            try:
                session.close()
            except (NativeArtifactError, NativeRuntimeError):
                pass
        finally:
            try:
                connection_scope.__exit__(None, None, None)
            except (NativeArtifactError, NativeRuntimeError):
                pass
        evidence = _client_evidence(
            fixture,
            routes,
            selection.candidates,
            selected_provider=selection.selected_provider.metadata.id,
        )
        return _passed_result(scenario, "success", evidence)
    except NativeTransportSelectionError as error:
        evidence = _client_evidence(fixture, routes, error.candidates, selected_provider=None)
        return _passed_result(scenario, "error", evidence)


def _run_server_case(
    scenario: Mapping[str, Any],
    fixture: Mapping[str, Any],
    routes: Sequence[Mapping[str, Any]],
    resolved_routes: Sequence[Mapping[str, Any]],
    artifacts: Path,
    ready_output: Path,
) -> dict[str, Any]:
    provider_routes: dict[str, NativeServerProviderRoute] = {}
    bindings = []
    bind_failure = False
    terminal_provider: str | None = None
    for route, resolved in zip(routes, resolved_routes, strict=True):
        transport = _required_string(route, "transport")
        provider_routes[transport] = NativeServerProviderRoute(
            provider_endpoint=_required_string(resolved, "locator"),
            security=_server_security(route, artifacts),
        )
        binding: Any = _binding_for_route(route)
        failures = _injected_failures(route)
        if "bind_failure" in failures:
            binding = _BindFailureBinding(binding)
            bind_failure = True
        elif "terminal_listener_failure" in failures:
            binding = _TerminalFailureBinding(binding)
            terminal_provider = _required_string(route, "provider_id")
        bindings.append(binding)

    policy = _rollback_policy(routes) if bind_failure else TransportPolicy.AUTO
    application_endpoint = _required_string(fixture, "application_endpoint")
    try:
        with listen_native_server(
            NativeServerBootstrapOptions(
                endpoint=application_endpoint,
                provider_routes=provider_routes,
                transport_policy=policy,
            ),
            transports=tuple(bindings),
        ) as server:
            _write_ready_report(scenario, routes, server.bound_provider_endpoints, ready_output)
            if terminal_provider is not None:
                try:
                    asyncio.run(_accept_server_transport_names(server, count=1, timeout_ms=2_000))
                except NativeArtifactError as error:
                    evidence = _server_evidence(
                        fixture,
                        routes,
                        server.bound_provider_endpoints,
                        accepted=(),
                        listener_state="closed",
                        logical_set_closed=True,
                        terminal_failure=terminal_provider,
                    )
                    return _passed_result(scenario, "error", evidence, message=str(error))
                raise AssertionError("terminal listener injection accepted a session")

            accepted = asyncio.run(_accept_server_transport_names(server, count=len(routes), timeout_ms=3_000))
            evidence = _server_evidence(
                fixture,
                routes,
                server.bound_provider_endpoints,
                accepted=accepted,
                listener_state="accepted",
            )
            return _passed_result(scenario, "success", evidence)
    except Exception as error:
        if not bind_failure:
            raise
        return _passed_result(
            scenario,
            "error",
            _rollback_evidence(fixture, routes),
            message=f"listener bind failed and prior listeners rolled back: {error}",
        )


async def _accept_server_transport_names(server: Any, *, count: int, timeout_ms: int) -> list[str]:
    accepted = []
    for _index in range(count):
        session = await server.accept(NativeServerAcceptOptions(timeout_ms=timeout_ms))
        accepted.append(session.active_transport_name)
        await _finish_peer_close(session, timeout_ms=timeout_ms)
    return accepted


async def _finish_peer_close(session: Any, *, timeout_ms: int) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (timeout_ms / 1_000)
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError("timed out waiting for the peer SESSION_CLOSE event")
        try:
            event = await session.next_event(timeout=min(remaining, 0.05))
        except NativeWouldBlockError:
            continue
        runtime_event = event.as_runtime()
        if runtime_event is not None and runtime_event.header.message_type is MessageType.SESSION_CLOSE:
            break
    session.close()


def _binding_for_route(route: Mapping[str, Any]) -> NativeTransportBinding:
    transport = _required_string(route, "transport")
    provider_id = _required_string(route, "provider_id")
    if provider_id == "example.transport.quic.uninstalled":
        provider = next(
            provider
            for provider in discover_native_transport_providers()
            if provider.transport_name == transport
        )
        provider = replace(provider, metadata=replace(provider.metadata, id=provider_id))
        return NativeTransportBinding.unavailable(provider, "provider package is not installed")
    expected = _OFFICIAL_PROVIDER_IDS.get(transport)
    if provider_id != expected:
        raise ValueError(f"unsupported Python host-route provider: {provider_id}")
    binding = load_native_transport_binding(transport)
    if binding.provider.metadata.id != provider_id:
        raise ValueError(f"loaded provider identity drifted from scenario: {provider_id}")
    return binding


def _client_locator(route: Mapping[str, Any], resolved: Mapping[str, Any]) -> str:
    if "route_unresolved" not in _injected_failures(route):
        return _required_string(resolved, "locator")
    return "tcp://127.0.0.1:9" if route["transport"] == "ipc" else "unix:///nnrp-route-unresolved"


def _client_security(route: Mapping[str, Any], artifacts: Path) -> NativeTransportClientSecurity | None:
    if "security_incompatible" in _injected_failures(route):
        return None
    mode = _security_mode(route)
    if mode in {"tls_server_auth", "mutual_tls", "wss"}:
        return NativeTransportClientSecurity("localhost", (artifacts / "server.der").read_bytes())
    if mode in {"plain", "browser_host"}:
        return None
    raise ValueError(f"unsupported client security mode: {mode}")


def _server_security(route: Mapping[str, Any], artifacts: Path) -> NativeTransportServerSecurity | None:
    mode = _security_mode(route)
    if mode in {"tls_server_auth", "mutual_tls", "wss"}:
        return NativeTransportServerSecurity(
            (artifacts / "server.der").read_bytes(),
            (artifacts / "server-key.der").read_bytes(),
        )
    if mode in {"plain", "browser_host"}:
        return None
    raise ValueError(f"unsupported server security mode: {mode}")


def _client_evidence(
    fixture: Mapping[str, Any],
    routes: Sequence[Mapping[str, Any]],
    candidates: Sequence[NativeTransportCandidateDiagnostic],
    *,
    selected_provider: str | None,
) -> dict[str, Any]:
    by_identity = {(candidate.transport_name, candidate.provider.id): candidate for candidate in candidates}
    candidate_evidence = []
    for route in routes:
        identity = (_required_string(route, "transport"), _required_string(route, "provider_id"))
        candidate = by_identity.get(identity)
        if candidate is None:
            raise ValueError(f"missing candidate evidence for {identity[1]}")
        rejection = candidate.rejection_reason.value if candidate.rejection_reason is not None else None
        item = {
            "transport": identity[0],
            "provider_id": identity[1],
            "requested_locator": _required_string(route, "locator"),
            "locator_resolved": rejection != "route-unresolved",
            "security_satisfied": rejection != "security-unsatisfied",
            "selected": selected_provider == identity[1],
        }
        if rejection is not None:
            item["rejection_reason"] = rejection
        candidate_evidence.append(item)
    accepted = []
    if selected_provider is not None:
        route = next(route for route in routes if route["provider_id"] == selected_provider)
        transport = _required_string(route, "transport")
        accepted.append({"transport": transport, "provider_id": selected_provider, "active_transport": transport})
    return {
        "application_endpoint": _required_string(fixture, "application_endpoint"),
        "candidates": candidate_evidence,
        "listeners": [],
        "accepted_sessions": accepted,
        "atomic_rollback": False,
        "logical_set_closed": False,
    }


def _server_evidence(
    fixture: Mapping[str, Any],
    routes: Sequence[Mapping[str, Any]],
    bound: Mapping[str, Any],
    *,
    accepted: Sequence[str],
    listener_state: str,
    atomic_rollback: bool = False,
    logical_set_closed: bool = False,
    terminal_failure: str | None = None,
) -> dict[str, Any]:
    evidence = {
        "application_endpoint": _required_string(fixture, "application_endpoint"),
        "candidates": [_server_candidate(route) for route in routes],
        "listeners": [
            {
                "transport": _required_string(route, "transport"),
                "provider_id": _required_string(route, "provider_id"),
                "requested_locator": _required_string(route, "locator"),
                "bound_endpoint": _endpoint_uri(bound[_required_string(route, "transport")]),
                "state": listener_state,
            }
            for route in routes
        ],
        "accepted_sessions": [
            {
                "transport": transport,
                "provider_id": next(
                    _required_string(route, "provider_id") for route in routes if route["transport"] == transport
                ),
                "active_transport": transport,
            }
            for transport in accepted
        ],
        "atomic_rollback": atomic_rollback,
        "logical_set_closed": logical_set_closed,
    }
    if terminal_failure is not None:
        evidence["terminal_failure"] = terminal_failure
    return evidence


def _rollback_evidence(fixture: Mapping[str, Any], routes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    listeners = []
    for route in routes:
        failed = "bind_failure" in _injected_failures(route)
        item = {
            "transport": _required_string(route, "transport"),
            "provider_id": _required_string(route, "provider_id"),
            "requested_locator": _required_string(route, "locator"),
            "state": "failed" if failed else "rolled_back",
        }
        listeners.append(item)
    return {
        "application_endpoint": _required_string(fixture, "application_endpoint"),
        "candidates": [_server_candidate(route) for route in routes],
        "listeners": listeners,
        "accepted_sessions": [],
        "atomic_rollback": True,
        "logical_set_closed": True,
    }


def _server_candidate(route: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "transport": _required_string(route, "transport"),
        "provider_id": _required_string(route, "provider_id"),
        "requested_locator": _required_string(route, "locator"),
        "locator_resolved": True,
        "security_satisfied": True,
        "selected": False,
    }


def _write_ready_report(
    scenario: Mapping[str, Any],
    routes: Sequence[Mapping[str, Any]],
    bound: Mapping[str, Any],
    path: Path,
) -> None:
    _write_json_atomic(
        path,
        {
            "$schema": _READY_SCHEMA,
            "protocol_version": _PROTOCOL_VERSION,
            "scenario_id": _required_string(scenario, "id"),
            "listeners": [
                {
                    "transport": transport,
                    "provider_id": next(
                        _required_string(route, "provider_id") for route in routes if route["transport"] == transport
                    ),
                    "bound_endpoint": _endpoint_uri(endpoint),
                }
                for transport, endpoint in bound.items()
            ],
        },
    )


def _passed_result(
    scenario: Mapping[str, Any],
    terminal: str,
    evidence: Mapping[str, Any],
    *,
    message: str = "independent host-route target executed the public Python SDK host API",
) -> dict[str, Any]:
    return {
        "id": _required_string(scenario, "id"),
        "outcome": "passed",
        "terminal": terminal,
        "observed_frames": [],
        "route_evidence": dict(evidence),
        "message": message,
        "evidence_paths": [],
    }


def _rollback_policy(routes: Sequence[Mapping[str, Any]]) -> TransportPolicy:
    opened = next(route for route in routes if "bind_failure" not in _injected_failures(route))
    return {
        "tcp": TransportPolicy.PREFER_TCP,
        "quic": TransportPolicy.PREFER_QUIC,
        "ipc": TransportPolicy.PREFER_IPC,
        "websocket": TransportPolicy.PREFER_WEBSOCKET,
    }[_required_string(opened, "transport")]


def _host_route_fixture(scenario: Mapping[str, Any]) -> Mapping[str, Any]:
    fixture = scenario.get("host_route")
    if not isinstance(fixture, Mapping):
        raise ValueError("host-route target requires a host_route fixture")
    return fixture


def _validated_routes(fixture: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw_routes = fixture.get("routes")
    if not isinstance(raw_routes, Sequence) or isinstance(raw_routes, str) or not raw_routes:
        raise ValueError("host-route fixture requires routes")
    routes = tuple(route for route in raw_routes if isinstance(route, Mapping))
    if len(routes) != len(raw_routes):
        raise ValueError("host-route entries must be objects")
    transports = [_required_string(route, "transport") for route in routes]
    provider_ids = [_required_string(route, "provider_id") for route in routes]
    if len(set(transports)) != len(transports) or len(set(provider_ids)) != len(provider_ids):
        raise ValueError("host-route fixture repeats a transport or provider id")
    return routes


def _security_mode(route: Mapping[str, Any]) -> str:
    security = route.get("security")
    if not isinstance(security, Mapping):
        raise ValueError("host-route security must be an object")
    return _required_string(security, "mode")


def _injected_failures(route: Mapping[str, Any]) -> frozenset[str]:
    raw = route.get("injected_failures", [])
    if not isinstance(raw, Sequence) or isinstance(raw, str) or any(not isinstance(value, str) for value in raw):
        raise ValueError("injected_failures must be a string array")
    return frozenset(raw)


def _required_string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{key} must be a non-empty string")
    return result


def _endpoint_uri(endpoint: Any) -> str:
    uri = getattr(endpoint, "uri", endpoint)
    if not isinstance(uri, str) or not uri:
        raise ValueError("bound endpoint must expose a non-empty URI")
    return uri


def _read_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON document must be an object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(value, indent=2)}\n", encoding="utf-8")


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(f"{json.dumps(value, indent=2)}\n", encoding="utf-8")
    os.replace(temporary, path)


if __name__ == "__main__":
    sys.exit(main())
