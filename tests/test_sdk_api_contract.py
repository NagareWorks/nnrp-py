from __future__ import annotations

import importlib.util
import json
import runpy
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_sdk_api_contract.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("check_sdk_api_contract", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frozen_contract() -> dict[str, object]:
    return {
        "contractVersion": 9,
        "enums": {
            "OperationState": {
                "values": {
                    "accepted": 0,
                    "running": 1,
                    "partial": 2,
                    "waiting_tool": 3,
                    "superseded": 4,
                    "cancelled": 5,
                    "failed": 6,
                    "completed": 7,
                }
            },
            "ResultTerminalState": {"values": {"success": 0, "cancelled": 1, "dropped": 2, "error": 3}},
        },
        "types": {
            "OperationLifecycleEvent": {
                "fields": [
                    {"name": "operation_id", "type": "u64", "required": True},
                    {"name": "state", "type": "OperationState", "required": True},
                ],
                "terminalMapping": {
                    "completed": "success",
                    "cancelled": "cancelled",
                    "superseded": "dropped",
                    "failed": "error",
                },
            },
            "TerminalEvent": {
                "representation": "tagged-union",
                "variants": ["runtime", "lifecycle"],
                "variantTypes": {"runtime": "RuntimeEvent", "lifecycle": "OperationLifecycleEvent"},
            },
            "NnrpResult": {
                "fields": [
                    {"name": "operation_id", "type": "u64", "required": True},
                    {"name": "terminal_state", "type": "ResultTerminalState", "required": True},
                    {"name": "event", "type": "TerminalEvent", "required": True},
                ]
            },
            "SessionRecoveryTicket": {
                "fields": [
                    {"name": "session_id", "type": "u32", "required": True},
                    {"name": "resume_token", "type": "bytes", "required": True},
                    {"name": "resume_from_operation_id", "type": "u64?", "required": False},
                    {"name": "resume_window_ms", "type": "u32", "required": True},
                ],
                "opaqueEncoding": {
                    "name": "NRTK",
                    "version": 1,
                    "byteOrder": "little-endian",
                    "fixedPrefixBytes": 28,
                    "reservedFlagsMask": 65_534,
                    "tail": "resume_token[resume_token_bytes]",
                },
            },
        },
        "languageProjections": {
            "python": {
                "operationLifecycleEvent": "nnrp.runtime.OperationLifecycleEvent",
                "terminalEvent": "nnrp.runtime.NativeTerminalEvent",
                "result": "nnrp.NativeRuntimeResult",
                "clientBootstrapOptions": "nnrp.client.NativeClientOptions",
                "clientSessionOptions": "nnrp.client.NativeClientSessionOptions",
                "sessionRecoveryTicket": "nnrp.client.NativeSessionRecoveryTicket",
                "sessionRecoveryTicketEncode": "NativeSessionRecoveryTicket.to_bytes",
                "sessionRecoveryTicketDecode": "NativeSessionRecoveryTicket.from_bytes",
                "serverBootstrapOptions": "nnrp.server.NativeServerBootstrapOptions",
                "serverSessionOptions": "nnrp.server.NativeServerSessionOptions",
                "serverAcceptOptions": "nnrp.server.NativeServerAcceptOptions",
                "serverSessionPolicy": "nnrp.server.NativeServerSessionPolicy",
            }
        },
        "roleOperations": {
            "client.open_session": {"returns": "ClientSession", "async": True},
            "client.resume_session": {"returns": "ClientSession", "async": True},
            "client_session.recovery_ticket": {"returns": "SessionRecoveryTicket?", "async": False},
        },
    }


def write_contract(directory: Path, contract: dict[str, object]) -> Path:
    path = directory / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path


def test_accepts_frozen_contract_and_current_python_surface() -> None:
    checker = load_checker()
    with tempfile.TemporaryDirectory() as directory:
        checker.check_contract(write_contract(Path(directory), frozen_contract()), ROOT)


def test_rejects_result_contract_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["types"]["NnrpResult"]["fields"][2]["type"] = "RuntimeEvent"
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="NnrpResult field contract drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


def test_rejects_python_projection_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["languageProjections"]["python"]["terminalEvent"] = "nnrp.LegacyEvent"
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="Python NativeTerminalEvent projection drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


def test_current_modules_export_the_frozen_terminal_surface() -> None:
    checker = load_checker()
    runtime_module = checker.parse_module(ROOT / "src" / "nnrp" / "runtime" / "__init__.py")
    root_module = checker.parse_module(ROOT / "src" / "nnrp" / "__init__.py")

    assert {
        "OperationLifecycleEvent",
        "NativeTerminalEvent",
        "OperationState",
        "ResultTerminalState",
    } <= checker.exported_names(runtime_module)
    assert "NativeRuntimeResult" in checker.exported_names(root_module)
    assert "NativeOperationLifecycle" not in checker.exported_names(root_module)


def test_ast_helpers_reject_missing_contract_symbols() -> None:
    checker = load_checker()
    module = checker.ast.parse("class Example:\n    value: int\n    def method(self):\n        pass\n")
    example = checker.class_definition(module, "Example")

    assert checker.enum_values(example) == {}
    with pytest.raises(SystemExit, match="missing Missing"):
        checker.class_definition(module, "Missing")
    with pytest.raises(SystemExit, match="missing Example.missing"):
        checker.method_parameters(example, "missing")
    with pytest.raises(SystemExit, match="missing Example.missing"):
        checker.method_return_annotation(example, "missing")
    with pytest.raises(SystemExit, match="missing Example.missing"):
        checker.method_is_async(example, "missing")
    with pytest.raises(SystemExit, match="static __all__"):
        checker.exported_names(module)


def test_cli_entrypoint_checks_the_frozen_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as directory:
        contract_path = write_contract(Path(directory), frozen_contract())
        monkeypatch.setattr(
            sys,
            "argv",
            [str(SCRIPT), "--contract", str(contract_path), "--source-root", str(ROOT)],
        )
        runpy.run_path(str(SCRIPT), run_name="__main__")
