from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "smoke_native_transport_artifacts.py"
_SPEC = spec_from_file_location("smoke_native_transport_artifacts", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

NativeTransportSmokeResult = _MODULE.NativeTransportSmokeResult
smoke_native_transport_artifacts = _MODULE.smoke_native_transport_artifacts
smoke_native_transport_artifacts_main = _MODULE.main


@dataclass(frozen=True)
class FakeEndpoint:
    uri: str


class FakeConnection:
    def __init__(self, received: tuple[bytes, ...]) -> None:
        self.received = received
        self.sent: list[bytes] = []
        self.closed = False

    async def send(self, packet: bytes) -> None:
        self.sent.append(packet)

    async def receive(self, *, max_packets: int, timeout_ms: int) -> tuple[bytes, ...]:
        assert max_packets == 1
        assert timeout_ms == 10_000
        return self.received

    async def close(self) -> None:
        self.closed = True


class FakeListener:
    def __init__(self, endpoint: str, connection: FakeConnection) -> None:
        self.endpoint = FakeEndpoint(endpoint)
        self.connection = connection
        self.closed = False

    async def accept(self, *, timeout_ms: int) -> FakeConnection:
        assert timeout_ms == 10_000
        return self.connection

    async def close(self) -> None:
        self.closed = True


class FakeReachableBinding:
    kind = "websocket"

    def __init__(self, *, client_received: tuple[bytes, ...], server_received: tuple[bytes, ...]) -> None:
        self.client = FakeConnection(client_received)
        self.server = FakeConnection(server_received)
        self.listener = FakeListener("ws://127.0.0.1:43123/nnrp", self.server)

    async def listen(self, endpoint: str, *, timeout_ms: int) -> FakeListener:
        assert endpoint == "ws://127.0.0.1:0/nnrp"
        assert timeout_ms == 10_000
        return self.listener

    async def connect(self, endpoint: FakeEndpoint, *, timeout_ms: int) -> FakeConnection:
        assert endpoint == self.listener.endpoint
        assert timeout_ms == 10_000
        return self.client


class FakeConnectFailureBinding(FakeReachableBinding):
    async def connect(self, endpoint: FakeEndpoint, *, timeout_ms: int) -> FakeConnection:
        await super().connect(endpoint, timeout_ms=timeout_ms)
        await asyncio.sleep(0)
        raise _MODULE.NativeArtifactError("connect failed")


def test_smoke_native_transport_artifacts_routes_ipc_and_websocket(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[tuple[str, Path | None]] = []

    class FakeBinding:
        def __init__(self, kind: str) -> None:
            self.kind = kind

    def fake_load_native_transport_binding(transport: str, *, root=None):
        loaded.append((transport, root))
        return FakeBinding(transport)

    async def fake_smoke_binding(binding, endpoint: str, index: int):
        return NativeTransportSmokeResult(binding.kind, f"{endpoint}/{index}", 2)

    monkeypatch.setattr(_MODULE, "load_native_transport_binding", fake_load_native_transport_binding)
    monkeypatch.setattr(_MODULE, "_smoke_endpoint", lambda transport, _index: f"{transport}://loopback")
    monkeypatch.setattr(_MODULE, "_smoke_binding", fake_smoke_binding)

    results = smoke_native_transport_artifacts(root=Path("native-root"))

    assert results == (
        NativeTransportSmokeResult("ipc", "ipc://loopback/1", 2),
        NativeTransportSmokeResult("websocket", "websocket://loopback/2", 2),
    )
    assert loaded == [("ipc", Path("native-root")), ("websocket", Path("native-root"))]


def test_smoke_native_transport_artifacts_rejects_unknown_transport() -> None:
    with pytest.raises(_MODULE.NativeArtifactError, match="unsupported native transport smoke target"):
        smoke_native_transport_artifacts(transports=["stdio"])


@pytest.mark.asyncio
async def test_smoke_binding_exchanges_packets_and_closes_all_handles() -> None:
    ping = _MODULE.build_ping_packet(session_id=7, trace_id=7).pack()
    pong = _MODULE.build_pong_packet(session_id=7, trace_id=7).pack()
    binding = FakeReachableBinding(client_received=(pong,), server_received=(ping,))

    result = await _MODULE._smoke_binding(binding, "ws://127.0.0.1:0/nnrp", 7)

    assert result == NativeTransportSmokeResult("websocket", "ws://127.0.0.1:43123/nnrp", 2)
    assert binding.client.sent == [ping]
    assert binding.server.sent == [pong]
    assert binding.client.closed
    assert binding.server.closed
    assert binding.listener.closed


@pytest.mark.asyncio
async def test_smoke_binding_rejects_mismatched_packet_and_still_closes() -> None:
    binding = FakeReachableBinding(client_received=(), server_received=(b"wrong-packet",))

    with pytest.raises(_MODULE.NativeArtifactError, match="server received a different"):
        await _MODULE._smoke_binding(binding, "ws://127.0.0.1:0/nnrp", 8)

    assert binding.client.closed
    assert binding.server.closed
    assert binding.listener.closed


@pytest.mark.asyncio
async def test_smoke_binding_rejects_mismatched_client_packet_and_still_closes() -> None:
    ping = _MODULE.build_ping_packet(session_id=9, trace_id=9).pack()
    binding = FakeReachableBinding(client_received=(b"wrong-packet",), server_received=(ping,))

    with pytest.raises(_MODULE.NativeArtifactError, match="client received a different"):
        await _MODULE._smoke_binding(binding, "ws://127.0.0.1:0/nnrp", 9)

    assert binding.client.closed
    assert binding.server.closed
    assert binding.listener.closed


@pytest.mark.asyncio
async def test_smoke_binding_closes_an_accepted_connection_when_connect_fails() -> None:
    binding = FakeConnectFailureBinding(client_received=(), server_received=())

    with pytest.raises(_MODULE.NativeArtifactError, match="connect failed"):
        await _MODULE._smoke_binding(binding, "ws://127.0.0.1:0/nnrp", 10)

    assert not binding.client.closed
    assert binding.server.closed
    assert binding.listener.closed


def test_smoke_endpoint_selects_websocket_and_platform_ipc_schemes(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _MODULE._smoke_endpoint("websocket", 1) == "ws://127.0.0.1:0/nnrp"

    monkeypatch.setattr(_MODULE, "current_native_platform", lambda: SimpleNamespace(os_name="windows"))
    windows_endpoint = _MODULE._smoke_endpoint("ipc", 2)
    assert windows_endpoint.startswith("npipe://nnrp-py-smoke-")
    assert windows_endpoint.endswith("-2")

    monkeypatch.setattr(_MODULE, "current_native_platform", lambda: SimpleNamespace(os_name="linux"))
    unix_endpoint = _MODULE._smoke_endpoint("ipc", 3)
    assert unix_endpoint.startswith("unix://")
    assert unix_endpoint.endswith("-3.sock")

    with pytest.raises(_MODULE.NativeArtifactError, match="does not define a loopback endpoint"):
        _MODULE._smoke_endpoint("tcp", 4)


def test_smoke_native_transport_artifacts_cli_uses_default_transports(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        _MODULE,
        "smoke_native_transport_artifacts",
        lambda root, transports: (
            NativeTransportSmokeResult(transports[0], "npipe://smoke", 2),
            NativeTransportSmokeResult(transports[1], "ws://127.0.0.1:1/nnrp", 2),
        ),
    )
    monkeypatch.setattr(sys, "argv", ["smoke_native_transport_artifacts.py", "--root", "native-root"])

    assert smoke_native_transport_artifacts_main() == 0

    output = capsys.readouterr().out
    assert "smoked native transport ipc: endpoint=npipe://smoke packets=2" in output
    assert "smoked native transport websocket: endpoint=ws://127.0.0.1:1/nnrp packets=2" in output
