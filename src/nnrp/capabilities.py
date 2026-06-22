"""Frozen NNRP capability and transport token catalogs."""

from __future__ import annotations

from enum import StrEnum


class Preview4ControlCapability(StrEnum):
    CANCEL_ABORT = "control.cancel_abort"
    SUPERSEDE = "control.supersede"
    PRIORITY_UPDATE = "control.priority_update"
    DEADLINE_EXPIRE = "control.deadline_expire"
    PROGRESS_PARTIAL = "control.progress_partial"
    CREDIT_BACKPRESSURE = "control.credit_backpressure"
    CAPABILITY_COSTS = "control.capability_costs"
    ROUTE_EXECUTION_HINT = "control.route_execution_hint"
    TRACE_CONTEXT = "control.trace_context"
    RESULT_DROP_REASON = "control.result_drop_reason"
    DEGRADE_PROFILE = "control.degrade_profile"
    BUDGET_UPDATE = "control.budget_update"
    RECOVERABLE_ERROR = "control.recoverable_error"
    RETRY_AFTER = "control.retry_after"


class Preview4ObjectCapability(StrEnum):
    OBJECT_LIFECYCLE = "object.lifecycle"
    OBJECT_DELTA = "object.delta"
    OBJECT_COST = "object.cost"
    OBJECT_OWNERSHIP = "object.ownership"
    CACHE_REFERENCE = "cache.reference"


class Preview4TransportName(StrEnum):
    TCP = "tcp"
    QUIC = "quic"
    IPC = "ipc"
    WEBSOCKET = "websocket"


PREVIEW4_CONTROL_CAPABILITY_TOKENS = tuple(capability.value for capability in Preview4ControlCapability)
PREVIEW4_OBJECT_CAPABILITY_TOKENS = tuple(capability.value for capability in Preview4ObjectCapability)
PREVIEW4_TRANSPORT_NAMES = tuple(transport.value for transport in Preview4TransportName)
PREVIEW4_CAPABILITY_TOKENS = PREVIEW4_CONTROL_CAPABILITY_TOKENS + PREVIEW4_OBJECT_CAPABILITY_TOKENS


__all__ = [
    "PREVIEW4_CAPABILITY_TOKENS",
    "PREVIEW4_CONTROL_CAPABILITY_TOKENS",
    "PREVIEW4_OBJECT_CAPABILITY_TOKENS",
    "PREVIEW4_TRANSPORT_NAMES",
    "Preview4ControlCapability",
    "Preview4ObjectCapability",
    "Preview4TransportName",
]
