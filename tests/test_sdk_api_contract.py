from __future__ import annotations

import ast
import copy
import importlib.util
import json
import runpy
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_sdk_api_contract.py"
FROZEN_SERVER_OPERATION_INVARIANTS = [
    "submit.header.message_type is frame_submit",
    "submit.metadata is the frame_submit metadata variant",
    "operation_id equals submit.metadata.operation_id",
    "frame_id equals submit.header.frame_id",
    "the reply capability remains valid until exactly one terminal outcome is sent or the session closes",
]
FROZEN_RESULT_SUCCESS_RULE = (
    "A successful result has terminal_state success and an event whose message type is result_push "
    "and whose metadata variant is result_push."
)
FROZEN_RESULT_NON_SUCCESS_RULE = (
    "Cancelled, dropped, and error results preserve the terminal protocol or lifecycle event that "
    "established the state; SDKs do not synthesize RESULT_PUSH metadata for them."
)


def load_checker():
    spec = importlib.util.spec_from_file_location("check_sdk_api_contract", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frozen_contract() -> dict[str, object]:
    checker = load_checker()
    return {
        "contractVersion": 15,
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
        "wireLayouts": {
            "BodyRegionPrelude": {
                "size": 32,
                "validation": copy.deepcopy(checker.EXPECTED_BODY_REGION_VALIDATION),
            }
        },
        "types": {
            "TransportSelectionOptions": {
                "fields": [
                    {"name": name, "type": field_type, "required": required}
                    for name, field_type, required in checker.EXPECTED_TRANSPORT_SELECTION_OPTIONS
                ],
                "peerSupportedTransportsSemantics": (
                    "set; duplicates have no effect and input order is not semantically significant"
                ),
                "requestedMaxFrameBytesZeroRule": (
                    "zero is a valid requested size and must not be rejected or treated as absent"
                ),
            },
            "TransportProbeObservation": {
                "stateConstraint": ["succeeded", "failed"],
            },
            "TransportProviderDescriptor": {
                "nameSemantics": (
                    "provider-owned package or display name; protocol transport identity is transport_id and "
                    "selection must not derive it from name"
                ),
            },
            "ResultPushMetadata": {
                "validation": copy.deepcopy(checker.EXPECTED_RESULT_PUSH_VALIDATION),
            },
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
                ],
                "successRule": FROZEN_RESULT_SUCCESS_RULE,
                "nonSuccessRule": FROZEN_RESULT_NON_SUCCESS_RULE,
            },
            "ServerOperation": {
                "fields": [
                    {"name": "operation_id", "type": "u64", "required": True},
                    {"name": "frame_id", "type": "u32", "required": True},
                    {"name": "submit", "type": "RuntimeEvent", "required": True},
                ],
                "terminalMethods": ["send_result", "send_result_drop"],
                "streamingMethods": ["send_progress", "send_partial_result"],
                "invariants": FROZEN_SERVER_OPERATION_INVARIANTS.copy(),
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
            "python": copy.deepcopy(checker.EXPECTED_PYTHON_PROJECTIONS),
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
            "clientSubmitWait": {
                "scopeRule": (
                    "These rules apply when an SDK exposes a cancellable or time-bounded submit-and-wait convenience."
                ),
                "preDispatchCancellationRule": (
                    "Cancellation before FRAME_SUBMIT dispatch fails the local wait and emits no submit "
                    "or cancellation frame."
                ),
                "postDispatchCancellationRule": (
                    "Cancellation after FRAME_SUBMIT dispatch fails the local wait with the language-native "
                    "cancellation error and sends CANCEL for the submitted operation."
                ),
                "timeoutRule": (
                    "A time-bounded submit wait sends DEADLINE before dispatch; expiry fails the local wait "
                    "with the language-native timeout error and sends CANCEL for the submitted operation."
                ),
                "lifecycleRule": (
                    "The local lifecycle event produced by caller cancellation or wait expiry remains "
                    "observable through the client event pump and must not race the same submit wait into a "
                    "successful NnrpResult return. A terminal lifecycle initiated independently by the peer "
                    "may complete the submit wait as NnrpResult evidence."
                ),
            },
            "serverEventPump": {
                "canonicalOperation": "server_session.next_event",
                "submitConvenience": "server_session.receive_submit",
                "orderingRule": "next_event delivers every server event in per-session wire order without filtering",
                "submitRule": (
                    "receive_submit is a selective convenience that may skip non-submit events only by retaining "
                    "them in the same session queue; it must never discard, decode-and-forget, or acknowledge them"
                ),
                "ownershipRule": (
                    "a FRAME_SUBMIT event becomes one ServerOperation before it is exposed to the application, "
                    "so consuming the canonical event pump never loses the reply capability"
                ),
                "concurrencyRule": (
                    "one session has one serialized receive source; concurrent receive calls are rejected or "
                    "serialized and never race the native event queue"
                ),
            },
            "traceContextCorrelation": copy.deepcopy(checker.EXPECTED_TRACE_CONTEXT_CORRELATION),
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


def test_rejects_contract_version_and_role_surface_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["contractVersion"] = 14
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="expected SDK contract version 15"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)

    contract = frozen_contract()
    contract["roleSurfaces"] = None
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="SDK role surfaces must be an object"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)

    for surface, diagnostic in (
        ("clientSubmitWait", "client submit-wait contract must be an object"),
        ("serverEventPump", "server event-pump contract must be an object"),
        ("traceContextCorrelation", "trace-context correlation contract must be an object"),
    ):
        contract = frozen_contract()
        contract["roleSurfaces"][surface] = None
        with (
            tempfile.TemporaryDirectory() as directory,
            pytest.raises(SystemExit, match=diagnostic),
        ):
            checker.check_contract(write_contract(Path(directory), contract), ROOT)

    contract = frozen_contract()
    contract["roleSurfaces"]["clientSubmitWait"]["postDispatchCancellationRule"] = "cancel only the local task"
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="client submit-wait semantics drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)

    contract = frozen_contract()
    contract["roleSurfaces"]["serverEventPump"]["orderingRule"] = "may reorder events"
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="server event-pump semantics drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)

    contract = frozen_contract()
    contract["roleSurfaces"]["traceContextCorrelation"]["operationFrameRule"] = "allocate a fresh frame id"
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="trace-context correlation semantics drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


def test_rejects_result_contract_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["types"]["NnrpResult"]["fields"][2]["type"] = "RuntimeEvent"
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="NnrpResult field contract drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)

    contract = frozen_contract()
    contract["types"]["NnrpResult"]["successRule"] = "accept any terminal event"
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="NnrpResult terminal evidence rules drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)

    contract = frozen_contract()
    contract["types"]["NnrpResult"]["nonSuccessRule"] = "fabricate result_push metadata"
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="NnrpResult terminal evidence rules drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


def test_rejects_python_projection_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["languageProjections"]["python"]["terminalEvent"] = "nnrp.LegacyEvent"
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="Python SDK projection map drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)

    contract = frozen_contract()
    del contract["languageProjections"]["python"]["baselineMetadataCodecs"]["ObjectReferenceBlock"]
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="Python SDK projection map drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


def test_rejects_missing_python_baseline_metadata_codec_surface() -> None:
    checker = load_checker()
    valid_module = ast.parse(
        """
class _FixedWidthMetadata:
    def pack(self): ...
    @classmethod
    def unpack(cls, payload): ...

class ExampleMetadata(_FixedWidthMetadata):
    def pack(self): ...
"""
    )
    checker.require_python_baseline_metadata_codec(valid_module, "ExampleMetadata")

    invalid_module = ast.parse(
        """
class _FixedWidthMetadata:
    def pack(self): ...
    @classmethod
    def unpack(cls, payload): ...

class ExampleMetadata(_FixedWidthMetadata):
    pass
"""
    )
    with pytest.raises(
        SystemExit,
        match="Python baseline metadata codec ExampleMetadata.pack is missing",
    ):
        checker.require_python_baseline_metadata_codec(invalid_module, "ExampleMetadata")


def test_rejects_frozen_data_plane_validation_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["wireLayouts"]["BodyRegionPrelude"]["validation"]["objectReferenceBlockBytesMultiple"] = 16
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="BodyRegionPrelude validation contract drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)

    contract = frozen_contract()
    contract["types"]["ResultPushMetadata"]["validation"]["tensorCoverageRule"] = "best effort"
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="ResultPushMetadata validation contract drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)

    contract = frozen_contract()
    contract["languageProjections"]["python"]["transportSelectionOptions"] = "nnrp.LegacySelectionOptions"
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="Python SDK projection map drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


def test_rejects_server_operation_invariant_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["types"]["ServerOperation"]["invariants"][-1] = "peer cancellation releases the reply capability"
    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="ServerOperation invariants drifted"),
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


def test_every_frozen_python_projection_target_is_publicly_resolvable() -> None:
    checker = load_checker()

    checker.require_python_projection_targets(
        copy.deepcopy(checker.EXPECTED_PYTHON_PROJECTIONS),
        ROOT,
    )


def test_python_projection_target_gate_rejects_missing_public_type() -> None:
    checker = load_checker()
    projection = copy.deepcopy(checker.EXPECTED_PYTHON_PROJECTIONS)
    projection["transportSelectionOptions"] = "nnrp.native.MissingTransportSelectionOptions"

    with pytest.raises(SystemExit, match="MissingTransportSelectionOptions is not publicly resolvable"):
        checker.require_python_projection_targets(projection, ROOT)


def test_rejects_transport_selection_options_field_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["types"]["TransportSelectionOptions"]["fields"].pop()

    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="TransportSelectionOptions field contract drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


def test_rejects_transport_selection_peer_set_semantics_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["types"]["TransportSelectionOptions"]["peerSupportedTransportsSemantics"] = "ordered list"

    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="TransportSelectionOptions peer transport semantics drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


def test_rejects_transport_selection_zero_frame_rule_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["types"]["TransportSelectionOptions"]["requestedMaxFrameBytesZeroRule"] = "zero means absent"

    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="TransportSelectionOptions zero frame-size rule drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


def test_rejects_transport_probe_observation_state_constraint_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["types"]["TransportProbeObservation"]["stateConstraint"].append("not-run")

    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="TransportProbeObservation state constraint drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


def test_rejects_transport_provider_name_semantics_drift() -> None:
    checker = load_checker()
    contract = frozen_contract()
    contract["types"]["TransportProviderDescriptor"]["nameSemantics"] = "transport identity"

    with (
        tempfile.TemporaryDirectory() as directory,
        pytest.raises(SystemExit, match="TransportProviderDescriptor name semantics drifted"),
    ):
        checker.check_contract(write_contract(Path(directory), contract), ROOT)


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
    assert checker.annotated_field_types(example) == [("value", "int")]


def test_cli_entrypoint_checks_the_frozen_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as directory:
        contract_path = write_contract(Path(directory), frozen_contract())
        monkeypatch.setattr(
            sys,
            "argv",
            [str(SCRIPT), "--contract", str(contract_path), "--source-root", str(ROOT)],
        )
        runpy.run_path(str(SCRIPT), run_name="__main__")
