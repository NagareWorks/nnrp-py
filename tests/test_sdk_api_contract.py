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
        "contractVersion": 14,
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
        "semanticEnums": {
            "ConnectionLifecycleState": ["open", "closing", "closed"],
            "SessionLifecycleState": ["open", "resumed", "closing", "draining", "closed"],
        },
        "types": {
            "SessionLifecycleSnapshot": {
                "fields": [
                    {"name": "session_id", "type": "u32", "required": True},
                    {"name": "state", "type": "SessionLifecycleState", "required": True},
                    {"name": "profile_id", "type": "u16", "required": True},
                    {"name": "priority_class", "type": "SessionPriorityClass", "required": True},
                    {"name": "schema_id", "type": "u32", "required": True},
                    {"name": "schema_version", "type": "u32", "required": True},
                    {"name": "max_in_flight_operations", "type": "u16", "required": True},
                    {"name": "route_scope_id", "type": "u32", "required": True},
                    {"name": "last_operation_id", "type": "u64", "required": True},
                    {"name": "session_error_code", "type": "u32", "required": True},
                ]
            },
            "ConnectionLifecycleSnapshot": {
                "fields": [
                    {"name": "state", "type": "ConnectionLifecycleState", "required": True},
                    {"name": "sessions", "type": "SessionLifecycleSnapshot[]", "required": True},
                ]
            },
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
                "nativeEventProjection": {
                    "eventKind": "operation_lifecycle",
                    "eventKindCode": 14,
                    "headerPresent": 0,
                    "payloadBytes": 1,
                    "payloadLayout": [
                        {
                            "name": "state",
                            "type": "OperationState",
                            "wireType": "u8",
                            "offset": 0,
                        }
                    ],
                    "operationIdentity": (
                        "diagnostic.related_operation_id and the operation handle, when the handle remains live"
                    ),
                    "ownership": (
                        "the one-byte state payload follows the same payload_owner lifetime as wire-event payloads"
                    ),
                },
            },
            "ClientEvent": {
                "representation": "tagged-union",
                "variants": ["runtime", "lifecycle"],
                "variantTypes": {"runtime": "RuntimeEvent", "lifecycle": "OperationLifecycleEvent"},
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
            "ServerOperation": {
                "fields": [
                    {"name": "operation_id", "type": "u64", "required": True},
                    {"name": "frame_id", "type": "u32", "required": True},
                    {"name": "submit", "type": "RuntimeEvent", "required": True},
                ],
                "terminalMethods": ["send_result", "send_result_drop"],
                "streamingMethods": ["send_progress", "send_partial_result"],
            },
            "ServerEvent": {
                "representation": "tagged-union",
                "variants": ["submit", "runtime", "lifecycle"],
                "variantTypes": {
                    "submit": "ServerOperation",
                    "runtime": "RuntimeEvent",
                    "lifecycle": "OperationLifecycleEvent",
                },
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
                "clientEvent": "nnrp.runtime.NativeClientEvent",
                "terminalEvent": "nnrp.runtime.NativeTerminalEvent",
                "result": "nnrp.NativeRuntimeResult",
                "serverEvent": "nnrp.server.NativeServerEvent",
                "serverOperation": "nnrp.NativeRuntimeServerOperation",
                "roleMethods": {
                    "client.open_session": "open_session",
                    "client.resume_session": "resume_session",
                    "client_session.recovery_ticket": "recovery_ticket",
                    "client_session.next_event": "next_event",
                    "server.accept": "accept",
                    "server_session.next_event": "next_event",
                    "server_session.receive_submit": "receive_submit",
                    "server_operation.send_result": "send_result",
                    "server_operation.send_result_drop": "send_result_drop",
                    "server_operation.send_progress": "send_progress",
                    "server_operation.send_partial_result": "send_partial_result",
                },
                "connectionLifecycle": "nnrp.lifecycle.ConnectionLifecycleSnapshot",
                "sessionLifecycle": "nnrp.lifecycle.SessionLifecycleSnapshot",
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
            "client_session.next_event": {
                "returns": "ClientEvent",
                "async": True,
            },
            "server.accept": {"returns": "ServerSession", "async": True},
            "server_session.next_event": {"returns": "ServerEvent", "async": True},
            "server_session.receive_submit": {
                "returns": "ServerOperation",
                "async": True,
                "selective": True,
                "retainsSkippedEvents": True,
            },
            "server_operation.send_result": {
                "parameters": [
                    {"name": "metadata", "type": "ResultPushMetadata", "required": True},
                    {"name": "body", "type": "bytes", "required": False},
                ],
                "returns": "void",
                "async": True,
                "terminal": True,
            },
            "server_operation.send_result_drop": {
                "parameters": [
                    {"name": "metadata", "type": "ResultDropReasonMetadata", "required": True},
                    {"name": "diagnostic", "type": "bytes", "required": False},
                ],
                "returns": "void",
                "async": True,
                "terminal": True,
            },
            "server_operation.send_progress": {
                "parameters": [
                    {"name": "metadata", "type": "ProgressMetadata", "required": True},
                    {"name": "body", "type": "bytes", "required": False},
                ],
                "returns": "void",
                "async": True,
                "terminal": False,
            },
            "server_operation.send_partial_result": {
                "parameters": [
                    {"name": "metadata", "type": "PartialResultMetadata", "required": True},
                    {"name": "body", "type": "bytes", "required": False},
                ],
                "returns": "void",
                "async": True,
                "terminal": False,
            },
        },
        "roleSurfaces": {
            "serverEventPump": {
                "canonicalOperation": "server_session.next_event",
                "submitConvenience": "server_session.receive_submit",
                "submitRule": "receive_submit may skip only by retaining them in the same session queue",
                "concurrencyRule": "one session has one serialized receive source",
            }
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


def test_rejects_client_event_union_or_operation_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["types"]["ClientEvent"]["variantTypes"]["lifecycle"] = "RuntimeEvent"
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="ClientEvent variant types drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)

    contract = frozen_contract()
    contract["roleOperations"]["client_session.next_event"]["returns"] = "RuntimeEvent"
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="client next_event role operation drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


def test_rejects_server_event_union_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["types"]["ServerEvent"]["variants"] = ["runtime", "lifecycle"]
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="ServerEvent variants drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


def test_rejects_operation_lifecycle_native_projection_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["types"]["OperationLifecycleEvent"]["nativeEventProjection"]["eventKindCode"] = 6
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="OperationLifecycleEvent native projection drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


def test_rejects_server_receive_submit_retention_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["roleOperations"]["server_session.receive_submit"]["retainsSkippedEvents"] = False
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="server receive_submit role operation drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


def test_rejects_synchronous_server_accept_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["roleOperations"]["server.accept"]["async"] = False
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="server event role operations drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


def test_current_modules_export_the_frozen_client_event_and_terminal_surface() -> None:
    checker = load_checker()
    runtime_module = checker.parse_module(ROOT / "src" / "nnrp" / "runtime" / "__init__.py")
    root_module = checker.parse_module(ROOT / "src" / "nnrp" / "__init__.py")

    assert {
        "NativeClientEvent",
        "OperationLifecycleEvent",
        "NativeTerminalEvent",
        "OperationState",
        "ResultTerminalState",
    } <= checker.exported_names(runtime_module)
    assert {"NativeClientEvent", "NativeRuntimeResult"} <= checker.exported_names(root_module)
    assert "NativeOperationLifecycle" not in checker.exported_names(root_module)


def test_ast_helpers_reject_missing_contract_symbols() -> None:
    checker = load_checker()
    module = checker.ast.parse("class Example:\n    value: int\n    def method(self):\n        pass\n")
    example = checker.class_definition(module, "Example")

    assert checker.enum_values(example) == {}
    assert checker.string_enum_values(example) == {}
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
    with pytest.raises(SystemExit, match="missing MissingAlias"):
        checker.assignment_value(module, "MissingAlias")


def test_cli_entrypoint_checks_the_frozen_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as directory:
        contract_path = write_contract(Path(directory), frozen_contract())
        monkeypatch.setattr(
            sys,
            "argv",
            [str(SCRIPT), "--contract", str(contract_path), "--source-root", str(ROOT)],
        )
        runpy.run_path(str(SCRIPT), run_name="__main__")
