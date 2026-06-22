from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

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


class FakeClient:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self.calls = calls

    def bind_server(self, *, server_id: int, generation: int, transport_id: int):
        self.calls.append(("bind", (server_id, generation, transport_id)))
        return FakeServer(self.calls, server_id)


class FakeServer:
    def __init__(self, calls: list[tuple[str, object]], server_id: int) -> None:
        self.calls = calls
        self.server_id = server_id

    def accept_session(
        self,
        *,
        session_id: int,
        generation: int,
        profile_id: int,
        schema_id: int,
        schema_version: int,
    ):
        self.calls.append(("accept", (self.server_id, session_id, generation, profile_id, schema_id, schema_version)))
        return FakeSession(self.calls, session_id)

    def close(self) -> None:
        self.calls.append(("close_server", self.server_id))


class FakeSession:
    def __init__(self, calls: list[tuple[str, object]], session_id: int) -> None:
        self.calls = calls
        self.session_id = session_id

    def receive_submit(self, *, operation_id: int, frame_id: int, payload: bytes):
        self.calls.append(("receive_submit", (self.session_id, operation_id, frame_id, payload)))
        return FakeOperation(self.calls, operation_id)

    def send_flow_update(self, *, frame_id: int) -> None:
        self.calls.append(("send_flow_update", (self.session_id, frame_id)))

    def close(self) -> None:
        self.calls.append(("close_session", self.session_id))


class FakeOperation:
    def __init__(self, calls: list[tuple[str, object]], operation_id: int) -> None:
        self.calls = calls
        self.operation_id = operation_id

    def send_result(self, payload: bytes) -> None:
        self.calls.append(("send_result", (self.operation_id, payload)))


def test_smoke_native_transport_artifacts_routes_ipc_and_websocket(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []
    loaded: list[tuple[Path | None, str]] = []

    def fake_load_native_client(*, root=None, transport: str):
        loaded.append((root, transport))
        return FakeClient(calls)

    monkeypatch.setattr(_MODULE, "load_native_client", fake_load_native_client)

    results = smoke_native_transport_artifacts(root=Path("native-root"))

    assert results == (
        NativeTransportSmokeResult("ipc", 40_100, 40_101, 40_102),
        NativeTransportSmokeResult("websocket", 40_200, 40_201, 40_202),
    )
    assert loaded == [(Path("native-root"), "ipc"), (Path("native-root"), "websocket")]
    assert calls[0] == ("bind", (40_100, 1, _MODULE.TRANSPORT_SLOT_IPC))
    assert calls[1] == ("accept", (40_100, 40_101, 1, 2, 0x1001, 1))
    assert calls[2] == ("receive_submit", (40_101, 40_102, 1, b"preview4-native-transport-smoke"))
    assert calls[3] == ("send_result", (40_102, b"preview4-native-transport-smoke"))
    assert calls[4] == ("send_flow_update", (40_101, 1))
    assert calls[5] == ("close_session", 40_101)
    assert calls[6] == ("close_server", 40_100)
    assert calls[7] == ("bind", (40_200, 1, _MODULE.TRANSPORT_SLOT_WEBSOCKET))


def test_smoke_native_transport_artifacts_rejects_unknown_transport() -> None:
    with pytest.raises(_MODULE.NativeArtifactError, match="unsupported native transport smoke target"):
        smoke_native_transport_artifacts(transports=["stdio"])


def test_smoke_native_transport_artifacts_cli_uses_default_transports(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        _MODULE,
        "smoke_native_transport_artifacts",
        lambda root, transports: (
            NativeTransportSmokeResult(transports[0], 1, 2, 3),
            NativeTransportSmokeResult(transports[1], 4, 5, 6),
        ),
    )
    monkeypatch.setattr(sys, "argv", ["smoke_native_transport_artifacts.py", "--root", "native-root"])

    assert smoke_native_transport_artifacts_main() == 0

    output = capsys.readouterr().out
    assert "smoked native transport ipc: server=1 session=2 operation=3" in output
    assert "smoked native transport websocket: server=4 session=5 operation=6" in output
