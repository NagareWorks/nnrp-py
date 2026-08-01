from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

EXPECTED_CONTRACT_VERSION = 8
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


def enum_values(class_node: ast.ClassDef) -> dict[str, int]:
    values: dict[str, int] = {}
    for node in class_node.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if isinstance(node.value, ast.Constant) and type(node.value.value) is int:
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
        {name.upper(): value for name, value in enums["OperationState"]["values"].items()}
        == EXPECTED_OPERATION_STATES,
        "OperationState contract drifted",
    )
    require(
        {name.upper(): value for name, value in enums["ResultTerminalState"]["values"].items()}
        == EXPECTED_TERMINAL_STATES,
        "ResultTerminalState contract drifted",
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

    runtime_module = parse_module(source_root / "src" / "nnrp" / "runtime" / "types.py")
    runtime_public_module = parse_module(source_root / "src" / "nnrp" / "runtime" / "__init__.py")
    native_module = parse_module(source_root / "src" / "nnrp" / "native.py")
    root_module = parse_module(source_root / "src" / "nnrp" / "__init__.py")
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
    runtime_exports = exported_names(runtime_public_module)
    require(
        {"OperationLifecycleEvent", "NativeTerminalEvent", "OperationState", "ResultTerminalState"}
        <= runtime_exports,
        "nnrp.runtime is missing frozen terminal API exports",
    )
    root_exports = exported_names(root_module)
    require("NativeRuntimeResult" in root_exports, "nnrp is missing the frozen NativeRuntimeResult export")
    require("NativeOperationLifecycle" not in root_exports, "nnrp still exports legacy NativeOperationLifecycle")
    native_class_names = {node.name for node in native_module.body if isinstance(node, ast.ClassDef)}
    require("NativeOperationLifecycle" not in native_class_names, "legacy NativeOperationLifecycle remains public")
    session = class_definition(native_module, "NativeRuntimeSession")
    for method_name in ("poll_result", "submit_and_poll_result", "async_submit_and_poll_result"):
        require(
            "state" not in method_parameters(session, method_name),
            f"{method_name} still allows terminal-state fabrication",
        )
        require(
            method_return_annotation(session, method_name) == "NativeRuntimeResult",
            f"{method_name} return type drifted from NativeRuntimeResult",
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    check_contract(args.contract, args.source_root)


if __name__ == "__main__":
    main()
