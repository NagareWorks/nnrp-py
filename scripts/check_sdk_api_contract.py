from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

EXPECTED_CONTRACT_VERSION = 12
EXPECTED_OPERATION_STATES = {
    "ACCEPTED": 0,
    "RUNNING": 1,
    "PARTIAL": 2,
    "WAITING_TOOL": 3,
    "SUPERSEDED": 4,
    "CANCELLED": 5,
    "FAILED": 6,
    "COMPLETED": 7,
}
EXPECTED_TERMINAL_STATES = {"SUCCESS": 0, "CANCELLED": 1, "DROPPED": 2, "ERROR": 3}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def field_shape(type_contract: dict[str, Any]) -> list[tuple[str, str, bool]]:
    return [(field["name"], field["type"], field.get("required", False)) for field in type_contract["fields"]]


def parse_module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def class_definition(module: ast.Module, name: str) -> ast.ClassDef:
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise SystemExit(f"Python SDK is missing {name}")


def annotated_fields(class_node: ast.ClassDef) -> list[str]:
    return [
        node.target.id
        for node in class_node.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]


def public_annotated_fields(class_node: ast.ClassDef) -> list[str]:
    return [name for name in annotated_fields(class_node) if not name.startswith("_")]


def enum_values(class_node: ast.ClassDef) -> dict[str, int]:
    values: dict[str, int] = {}
    for node in class_node.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if isinstance(node.value, ast.Constant) and type(node.value.value) is int:
            values[node.targets[0].id] = node.value.value
    return values


def string_enum_values(class_node: ast.ClassDef) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in class_node.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            values[node.targets[0].id] = node.value.value
    return values


def method_parameters(class_node: ast.ClassDef, name: str) -> list[str]:
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return [argument.arg for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
    raise SystemExit(f"Python SDK is missing {class_node.name}.{name}")


def method_return_annotation(class_node: ast.ClassDef, name: str) -> str | None:
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return None if node.returns is None else ast.unparse(node.returns)
    raise SystemExit(f"Python SDK is missing {class_node.name}.{name}")


def method_is_async(class_node: ast.ClassDef, name: str) -> bool:
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return isinstance(node, ast.AsyncFunctionDef)
    raise SystemExit(f"Python SDK is missing {class_node.name}.{name}")


def exported_names(module: ast.Module) -> set[str]:
    for node in module.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            return {
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
    raise SystemExit("Python SDK module is missing a static __all__ declaration")


def check_contract(contract_path: Path, source_root: Path) -> None:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    require(
        contract.get("contractVersion") == EXPECTED_CONTRACT_VERSION,
        f"expected SDK contract version {EXPECTED_CONTRACT_VERSION}",
    )

    enums = contract["enums"]
    require(
        {name.upper(): value for name, value in enums["OperationState"]["values"].items()} == EXPECTED_OPERATION_STATES,
        "OperationState contract drifted",
    )
    require(
        {name.upper(): value for name, value in enums["ResultTerminalState"]["values"].items()}
        == EXPECTED_TERMINAL_STATES,
        "ResultTerminalState contract drifted",
    )
    semantic_enums = contract["semanticEnums"]
    expected_connection_lifecycle_states = {"OPEN": "open", "CLOSING": "closing", "CLOSED": "closed"}
    expected_session_lifecycle_states = {
        "OPEN": "open",
        "RESUMED": "resumed",
        "CLOSING": "closing",
        "DRAINING": "draining",
        "CLOSED": "closed",
    }
    require(
        {value.upper(): value for value in semantic_enums["ConnectionLifecycleState"]}
        == expected_connection_lifecycle_states,
        "ConnectionLifecycleState contract drifted",
    )
    require(
        {value.upper(): value for value in semantic_enums["SessionLifecycleState"]}
        == expected_session_lifecycle_states,
        "SessionLifecycleState contract drifted",
    )

    types = contract["types"]
    lifecycle = types["OperationLifecycleEvent"]
    require(
        field_shape(lifecycle) == [("operation_id", "u64", True), ("state", "OperationState", True)],
        "OperationLifecycleEvent field contract drifted",
    )
    require(
        lifecycle.get("terminalMapping")
        == {"completed": "success", "cancelled": "cancelled", "superseded": "dropped", "failed": "error"},
        "OperationLifecycleEvent terminal mapping drifted",
    )

    terminal = types["TerminalEvent"]
    require(terminal.get("representation") == "tagged-union", "TerminalEvent is no longer a tagged union")
    require(terminal.get("variants") == ["runtime", "lifecycle"], "TerminalEvent variants drifted")
    require(
        terminal.get("variantTypes") == {"runtime": "RuntimeEvent", "lifecycle": "OperationLifecycleEvent"},
        "TerminalEvent variant types drifted",
    )

    result = types["NnrpResult"]
    require(
        field_shape(result)
        == [
            ("operation_id", "u64", True),
            ("terminal_state", "ResultTerminalState", True),
            ("event", "TerminalEvent", True),
        ],
        "NnrpResult field contract drifted",
    )

    server_operation = types["ServerOperation"]
    require(
        field_shape(server_operation)
        == [
            ("operation_id", "u64", True),
            ("frame_id", "u32", True),
            ("submit", "RuntimeEvent", True),
        ],
        "ServerOperation field contract drifted",
    )
    require(
        server_operation.get("terminalMethods") == ["send_result", "send_result_drop"]
        and server_operation.get("streamingMethods") == ["send_progress", "send_partial_result"],
        "ServerOperation method contract drifted",
    )

    server_event = types["ServerEvent"]
    require(server_event.get("representation") == "tagged-union", "ServerEvent is no longer a tagged union")
    require(server_event.get("variants") == ["submit", "runtime", "lifecycle"], "ServerEvent variants drifted")
    require(
        server_event.get("variantTypes")
        == {
            "submit": "ServerOperation",
            "runtime": "RuntimeEvent",
            "lifecycle": "OperationLifecycleEvent",
        },
        "ServerEvent variant types drifted",
    )

    lifecycle_projection = types["OperationLifecycleEvent"].get("nativeEventProjection", {})
    require(
        lifecycle_projection
        == {
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
            "ownership": "the one-byte state payload follows the same payload_owner lifetime as wire-event payloads",
        },
        "OperationLifecycleEvent native projection drifted",
    )

    require(
        field_shape(types["SessionLifecycleSnapshot"])
        == [
            ("session_id", "u32", True),
            ("state", "SessionLifecycleState", True),
            ("profile_id", "u16", True),
            ("priority_class", "SessionPriorityClass", True),
            ("schema_id", "u32", True),
            ("schema_version", "u32", True),
            ("max_in_flight_operations", "u16", True),
            ("route_scope_id", "u32", True),
            ("last_operation_id", "u64", True),
            ("session_error_code", "u32", True),
        ],
        "SessionLifecycleSnapshot field contract drifted",
    )
    require(
        field_shape(types["ConnectionLifecycleSnapshot"])
        == [
            ("state", "ConnectionLifecycleState", True),
            ("sessions", "SessionLifecycleSnapshot[]", True),
        ],
        "ConnectionLifecycleSnapshot field contract drifted",
    )

    recovery_ticket = types["SessionRecoveryTicket"]
    require(
        field_shape(recovery_ticket)
        == [
            ("session_id", "u32", True),
            ("resume_token", "bytes", True),
            ("resume_from_operation_id", "u64?", False),
            ("resume_window_ms", "u32", True),
        ],
        "SessionRecoveryTicket field contract drifted",
    )
    encoding = recovery_ticket.get("opaqueEncoding")
    require(
        encoding is not None
        and encoding.get("name") == "NRTK"
        and encoding.get("version") == 1
        and encoding.get("byteOrder") == "little-endian"
        and encoding.get("fixedPrefixBytes") == 28
        and encoding.get("reservedFlagsMask") == 65_534
        and encoding.get("tail") == "resume_token[resume_token_bytes]",
        "SessionRecoveryTicket opaque encoding drifted",
    )

    python_projection = contract["languageProjections"]["python"]
    require(
        python_projection.get("operationLifecycleEvent") == "nnrp.runtime.OperationLifecycleEvent",
        "Python OperationLifecycleEvent projection drifted",
    )
    require(
        python_projection.get("terminalEvent") == "nnrp.runtime.NativeTerminalEvent",
        "Python NativeTerminalEvent projection drifted",
    )
    require(
        python_projection.get("result") == "nnrp.NativeRuntimeResult",
        "Python NativeRuntimeResult projection drifted",
    )
    require(
        python_projection.get("serverEvent") == "nnrp.server.NativeServerEvent"
        and python_projection.get("serverOperation") == "nnrp.NativeRuntimeServerOperation",
        "Python server event ownership projections drifted",
    )
    require(
        python_projection.get("connectionLifecycle") == "nnrp.lifecycle.ConnectionLifecycleSnapshot"
        and python_projection.get("sessionLifecycle") == "nnrp.lifecycle.SessionLifecycleSnapshot",
        "Python lifecycle projections drifted",
    )
    expected_recovery_projections = {
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
    require(
        all(python_projection.get(name) == value for name, value in expected_recovery_projections.items()),
        "Python recovery and role option projections drifted",
    )

    role_operations = contract.get("roleOperations", {})
    require(
        role_operations.get("client.open_session", {}).get("returns") == "ClientSession"
        and role_operations.get("client.resume_session", {}).get("returns") == "ClientSession"
        and role_operations.get("client_session.recovery_ticket", {}).get("returns") == "SessionRecoveryTicket?",
        "recovery role operations drifted",
    )
    require(
        role_operations.get("client.open_session", {}).get("async") is True
        and role_operations.get("client.resume_session", {}).get("async") is True
        and role_operations.get("client_session.recovery_ticket", {}).get("async") is False,
        "recovery role operation async semantics drifted",
    )
    require(
        role_operations.get("client_session.next_event", {}).get("returns")
        == "RuntimeEvent|OperationLifecycleEvent"
        and role_operations.get("client_session.next_event", {}).get("async") is True,
        "client next_event role operation drifted",
    )
    require(
        role_operations.get("server.accept", {}).get("returns") == "ServerSession"
        and role_operations.get("server.accept", {}).get("async") is True
        and role_operations.get("server_session.next_event", {}).get("returns") == "ServerEvent"
        and role_operations.get("server_session.next_event", {}).get("async") is True,
        "server event role operations drifted",
    )
    receive_submit = role_operations.get("server_session.receive_submit", {})
    require(
        receive_submit.get("returns") == "ServerOperation"
        and receive_submit.get("async") is True
        and receive_submit.get("selective") is True
        and receive_submit.get("retainsSkippedEvents") is True,
        "server receive_submit role operation drifted",
    )
    server_event_pump = contract.get("roleSurfaces", {}).get("serverEventPump", {})
    require(
        server_event_pump.get("canonicalOperation") == "server_session.next_event"
        and server_event_pump.get("submitConvenience") == "server_session.receive_submit"
        and "retaining them" in server_event_pump.get("submitRule", "")
        and "serialized receive source" in server_event_pump.get("concurrencyRule", ""),
        "server event pump retention rules drifted",
    )

    runtime_module = parse_module(source_root / "src" / "nnrp" / "runtime" / "types.py")
    runtime_public_module = parse_module(source_root / "src" / "nnrp" / "runtime" / "__init__.py")
    native_module = parse_module(source_root / "src" / "nnrp" / "native.py")
    client_module = parse_module(source_root / "src" / "nnrp" / "client" / "native.py")
    client_public_module = parse_module(source_root / "src" / "nnrp" / "client" / "__init__.py")
    server_module = parse_module(source_root / "src" / "nnrp" / "server" / "native.py")
    server_public_module = parse_module(source_root / "src" / "nnrp" / "server" / "__init__.py")
    root_module = parse_module(source_root / "src" / "nnrp" / "__init__.py")
    lifecycle_module = parse_module(source_root / "src" / "nnrp" / "lifecycle.py")
    require(
        enum_values(class_definition(runtime_module, "OperationState")) == EXPECTED_OPERATION_STATES,
        "Python OperationState implementation drifted",
    )
    require(
        enum_values(class_definition(runtime_module, "ResultTerminalState")) == EXPECTED_TERMINAL_STATES,
        "Python ResultTerminalState implementation drifted",
    )
    require(
        annotated_fields(class_definition(runtime_module, "OperationLifecycleEvent")) == ["operation_id", "state"],
        "Python OperationLifecycleEvent implementation fields drifted",
    )
    require(
        annotated_fields(class_definition(runtime_module, "NativeTerminalEvent")) == ["kind", "value"],
        "Python NativeTerminalEvent implementation is not a closed tagged union",
    )
    require(
        annotated_fields(class_definition(native_module, "NativeRuntimeResult"))
        == ["operation_id", "terminal_state", "event"],
        "Python NativeRuntimeResult implementation fields drifted",
    )
    server_operation_class = class_definition(native_module, "NativeRuntimeServerOperation")
    require(
        public_annotated_fields(server_operation_class) == ["operation_id", "frame_id", "submit"],
        "Python NativeRuntimeServerOperation public fields drifted",
    )
    for method_name in ("send_result", "send_result_drop", "send_progress", "send_partial_result"):
        method_parameters(server_operation_class, method_name)
    server_event_class = class_definition(native_module, "NativeServerEvent")
    require(
        annotated_fields(server_event_class) == ["kind", "value"],
        "Python NativeServerEvent implementation is not a closed tagged union",
    )
    require(
        string_enum_values(class_definition(native_module, "NativeServerEventKind"))
        == {"SUBMIT": "submit", "RUNTIME": "runtime", "LIFECYCLE": "lifecycle"},
        "Python NativeServerEventKind drifted",
    )
    runtime_exports = exported_names(runtime_public_module)
    require(
        {"OperationLifecycleEvent", "NativeTerminalEvent", "OperationState", "ResultTerminalState"} <= runtime_exports,
        "nnrp.runtime is missing frozen terminal API exports",
    )
    root_exports = exported_names(root_module)
    require("NativeRuntimeResult" in root_exports, "nnrp is missing the frozen NativeRuntimeResult export")
    require("NativeOperationLifecycle" not in root_exports, "nnrp still exports legacy NativeOperationLifecycle")
    native_class_names = {node.name for node in native_module.body if isinstance(node, ast.ClassDef)}
    native_constants = {
        node.targets[0].id: node.value.value
        for node in native_module.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, int)
    }
    require(
        native_constants.get("EVENT_KIND_OPERATION_LIFECYCLE") == 14,
        "Python operation lifecycle native event kind drifted",
    )
    require("NativeOperationLifecycle" not in native_class_names, "legacy NativeOperationLifecycle remains public")
    session = class_definition(native_module, "NativeRuntimeSession")
    require(
        method_parameters(session, "next_event") == ["self", "timeout"]
        and method_is_async(session, "next_event")
        and method_return_annotation(session, "next_event")
        == "NativeRuntimeEvent | OperationLifecycleEvent",
        "NativeRuntimeSession.next_event drifted",
    )
    for method_name in ("poll_result", "submit_and_poll_result", "async_submit_and_poll_result"):
        require(
            "state" not in method_parameters(session, method_name),
            f"{method_name} still allows terminal-state fabrication",
        )
        require(
            method_return_annotation(session, method_name) == "NativeRuntimeResult",
            f"{method_name} return type drifted from NativeRuntimeResult",
        )
    require(
        method_parameters(session, "recovery_ticket") == ["self"],
        "NativeRuntimeSession.recovery_ticket signature drifted",
    )
    require(
        method_return_annotation(session, "recovery_ticket") == "NativeSessionRecoveryTicket | None",
        "NativeRuntimeSession.recovery_ticket return type drifted",
    )

    require(
        annotated_fields(class_definition(client_module, "NativeClientSessionOptions"))
        == [
            "requested_session_id",
            "profile_id",
            "schema_id",
            "schema_version",
            "priority_class",
            "default_deadline_ms",
            "max_in_flight_operations",
            "lease_ttl_hint_ms",
            "allow_resume",
            "resume_token_bytes",
            "cache_hints",
        ],
        "NativeClientSessionOptions fields drifted",
    )
    require(
        annotated_fields(class_definition(client_module, "NativeClientOptions"))
        == ["endpoint", "provider_routes", "transport_policy", "session_defaults"],
        "NativeClientOptions fields drifted",
    )
    require(
        annotated_fields(class_definition(client_module, "NativeSessionRecoveryTicket"))
        == ["session_id", "resume_token", "resume_from_operation_id", "resume_window_ms"],
        "NativeSessionRecoveryTicket fields drifted",
    )
    client_connection = class_definition(client_module, "NativeClientConnection")
    require(
        method_parameters(client_connection, "open_session") == ["self", "options"],
        "NativeClientConnection.open_session signature drifted",
    )
    require(
        method_parameters(client_connection, "resume_session") == ["self", "ticket", "options"],
        "NativeClientConnection.resume_session signature drifted",
    )
    require(
        method_is_async(client_connection, "open_session") and method_is_async(client_connection, "resume_session"),
        "NativeClientConnection open and resume operations must be async",
    )

    require(
        annotated_fields(class_definition(server_module, "NativeServerSessionOptions"))
        == [
            "supported_profiles",
            "supported_cache_objects",
            "max_cache_objects",
            "max_cache_object_bytes",
            "schema_registry",
            "resume_token_bytes",
            "max_in_flight_operations",
            "granted_operation_credit",
            "lease_ttl_ms",
            "resume_window_ms",
            "application_policy",
        ],
        "NativeServerSessionOptions fields drifted",
    )
    require(
        annotated_fields(class_definition(server_module, "NativeServerBootstrapOptions"))
        == ["endpoint", "provider_routes", "transport_policy", "session_defaults"],
        "NativeServerBootstrapOptions fields drifted",
    )
    require(
        annotated_fields(class_definition(server_module, "NativeServerAcceptOptions")) == ["timeout_ms"],
        "NativeServerAcceptOptions fields drifted",
    )
    require(
        annotated_fields(class_definition(server_module, "NativeServerSessionPolicyDecision"))
        == ["accepted", "session_error_code", "diagnostic"],
        "NativeServerSessionPolicyDecision fields drifted",
    )
    native_server = class_definition(server_module, "NativeServer")
    require(
        method_parameters(native_server, "accept") == ["self", "options"]
        and method_is_async(native_server, "accept")
        and method_return_annotation(native_server, "accept") == "NativeRuntimeServerSession",
        "NativeServer.accept drifted",
    )
    native_server_session = class_definition(native_module, "NativeRuntimeServerSession")
    require(
        method_parameters(native_server_session, "next_event") == ["self", "timeout"]
        and method_is_async(native_server_session, "next_event")
        and method_return_annotation(native_server_session, "next_event") == "NativeServerEvent",
        "NativeRuntimeServerSession.next_event drifted",
    )
    require(
        method_parameters(native_server_session, "receive_submit") == ["self", "timeout"]
        and method_is_async(native_server_session, "receive_submit")
        and method_return_annotation(native_server_session, "receive_submit") == "NativeRuntimeServerOperation",
        "NativeRuntimeServerSession.receive_submit drifted",
    )

    client_exports = exported_names(client_public_module)
    require(
        {"NativeClientOptions", "NativeClientSessionOptions", "NativeSessionRecoveryTicket"} <= client_exports,
        "nnrp.client is missing frozen v9 exports",
    )
    require(
        {"NativeClientConnectionOptions", "NativeClientSessionOpenOptions", "connect_native_client_session"}.isdisjoint(
            client_exports
        ),
        "nnrp.client still exports legacy role options",
    )
    server_exports = exported_names(server_public_module)
    require(
        {
            "NativeServerBootstrapOptions",
            "NativeServerSessionOptions",
            "NativeServerAcceptOptions",
            "NativeServerSessionPolicy",
            "NativeServerSessionPolicyDecision",
            "NativeServerEvent",
            "NativeServerEventKind",
        }
        <= server_exports,
        "nnrp.server is missing frozen role-event exports",
    )
    require("NativeServerOptions" not in server_exports, "nnrp.server still exports legacy NativeServerOptions")

    require(
        string_enum_values(class_definition(lifecycle_module, "ConnectionLifecycleState"))
        == expected_connection_lifecycle_states,
        "Python ConnectionLifecycleState implementation drifted",
    )
    require(
        string_enum_values(class_definition(lifecycle_module, "SessionLifecycleState"))
        == expected_session_lifecycle_states,
        "Python SessionLifecycleState implementation drifted",
    )
    require(
        annotated_fields(class_definition(lifecycle_module, "SessionLifecycleSnapshot"))
        == [
            "session_id",
            "state",
            "profile_id",
            "priority_class",
            "schema_id",
            "schema_version",
            "max_in_flight_operations",
            "route_scope_id",
            "last_operation_id",
            "session_error_code",
        ],
        "Python SessionLifecycleSnapshot implementation fields drifted",
    )
    require(
        annotated_fields(class_definition(lifecycle_module, "ConnectionLifecycleSnapshot")) == ["state", "sessions"],
        "Python ConnectionLifecycleSnapshot implementation fields drifted",
    )
    require(
        {
            "ConnectionLifecycleSnapshot",
            "ConnectionLifecycleState",
            "SessionLifecycleSnapshot",
            "SessionLifecycleState",
        }
        == exported_names(lifecycle_module),
        "nnrp.lifecycle exports drifted",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    check_contract(args.contract, args.source_root)


if __name__ == "__main__":
    main()
