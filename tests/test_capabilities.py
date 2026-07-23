from nnrp import (
    PREVIEW4_CAPABILITY_TOKENS,
    PREVIEW4_CONTROL_CAPABILITY_TOKENS,
    PREVIEW4_OBJECT_CAPABILITY_TOKENS,
    PREVIEW4_TRANSPORT_NAMES,
    Preview4ControlCapability,
    Preview4ObjectCapability,
    Preview4TransportName,
)
from nnrp.tools.wire_conformance import build_wire_target_manifest, parse_wire_target_transport


def test_preview4_control_capability_tokens_match_frozen_catalog() -> None:
    assert PREVIEW4_CONTROL_CAPABILITY_TOKENS == (
        "control.cancel_abort",
        "control.supersede",
        "control.priority_update",
        "control.deadline_expire",
        "control.progress_partial",
        "control.credit_backpressure",
        "control.capability_costs",
        "control.route_execution_hint",
        "control.trace_context",
        "control.result_drop_reason",
        "control.degrade_profile",
        "control.budget_update",
        "control.recoverable_error",
    )
    assert Preview4ControlCapability.RESULT_DROP_REASON == "control.result_drop_reason"
    assert "control.retry_after" not in PREVIEW4_CONTROL_CAPABILITY_TOKENS


def test_preview4_object_capability_and_transport_tokens_match_frozen_catalog() -> None:
    assert PREVIEW4_OBJECT_CAPABILITY_TOKENS == (
        "object.lifecycle",
        "object.delta",
        "object.cost",
        "object.ownership",
        "cache.reference",
    )
    assert PREVIEW4_TRANSPORT_NAMES == ("tcp", "quic", "ipc", "websocket")
    assert PREVIEW4_CAPABILITY_TOKENS == PREVIEW4_CONTROL_CAPABILITY_TOKENS + PREVIEW4_OBJECT_CAPABILITY_TOKENS
    assert Preview4ObjectCapability.CACHE_REFERENCE == "cache.reference"
    assert Preview4TransportName.WEBSOCKET == "websocket"


def test_wire_target_manifest_uses_shared_preview4_transport_catalog() -> None:
    manifest = build_wire_target_manifest(
        modes=["suite_as_client"],
        transports=[parse_wire_target_transport("ipc=npipe://nnrp-test")],
        capabilities=[Preview4ControlCapability.CANCEL_ABORT],
    )

    assert manifest["wire_conformance"]["transports"] == [
        {"name": "ipc", "endpoint": "npipe://nnrp-test", "tls": False}
    ]
