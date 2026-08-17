"""Frozen Preview4 connection and session lifecycle snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from nnrp.core import SessionPriorityClass


class ConnectionLifecycleState(StrEnum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


class SessionLifecycleState(StrEnum):
    OPEN = "open"
    RESUMED = "resumed"
    CLOSING = "closing"
    DRAINING = "draining"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class SessionLifecycleSnapshot:
    session_id: int
    state: SessionLifecycleState
    profile_id: int
    priority_class: SessionPriorityClass
    schema_id: int
    schema_version: int
    max_in_flight_operations: int
    route_scope_id: int
    last_operation_id: int
    session_error_code: int

    def __post_init__(self) -> None:
        _require_unsigned("session_id", self.session_id, 32, nonzero=True)
        _require_unsigned("profile_id", self.profile_id, 16)
        _require_unsigned("schema_id", self.schema_id, 32)
        _require_unsigned("schema_version", self.schema_version, 32)
        _require_unsigned("max_in_flight_operations", self.max_in_flight_operations, 16)
        _require_unsigned("route_scope_id", self.route_scope_id, 32)
        _require_unsigned("last_operation_id", self.last_operation_id, 64)
        _require_unsigned("session_error_code", self.session_error_code, 32)
        if not isinstance(self.state, SessionLifecycleState):
            raise TypeError("state must be SessionLifecycleState")
        if not isinstance(self.priority_class, SessionPriorityClass):
            raise TypeError("priority_class must be SessionPriorityClass")

    @property
    def accepts_session_scoped_messages(self) -> bool:
        return self.state in {
            SessionLifecycleState.OPEN,
            SessionLifecycleState.RESUMED,
            SessionLifecycleState.CLOSING,
            SessionLifecycleState.DRAINING,
        }

    @property
    def accepts_new_operations(self) -> bool:
        return self.state in {SessionLifecycleState.OPEN, SessionLifecycleState.RESUMED}


@dataclass(frozen=True, slots=True)
class ConnectionLifecycleSnapshot:
    state: ConnectionLifecycleState
    sessions: tuple[SessionLifecycleSnapshot, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.state, ConnectionLifecycleState):
            raise TypeError("state must be ConnectionLifecycleState")
        sessions = tuple(self.sessions)
        if any(not isinstance(session, SessionLifecycleSnapshot) for session in sessions):
            raise TypeError("sessions must contain SessionLifecycleSnapshot values")
        ordered = tuple(sorted(sessions, key=lambda session: session.session_id))
        if len({session.session_id for session in ordered}) != len(ordered):
            raise ValueError("sessions must not contain duplicate session_id values")
        if self.state is ConnectionLifecycleState.CLOSED and any(
            session.state is not SessionLifecycleState.CLOSED for session in ordered
        ):
            raise ValueError("closed connection snapshots require every session to be closed")
        object.__setattr__(self, "sessions", ordered)


def _require_unsigned(name: str, value: int, bits: int, *, nonzero: bool = False) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be int")
    minimum = 1 if nonzero else 0
    if not minimum <= value <= (1 << bits) - 1:
        qualifier = "non-zero " if nonzero else ""
        raise ValueError(f"{name} must fit in {qualifier}u{bits}")


__all__ = [
    "ConnectionLifecycleSnapshot",
    "ConnectionLifecycleState",
    "SessionLifecycleSnapshot",
    "SessionLifecycleState",
]
