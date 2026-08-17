from __future__ import annotations

import pytest

from nnrp.core import SessionPriorityClass
from nnrp.lifecycle import (
    ConnectionLifecycleSnapshot,
    ConnectionLifecycleState,
    SessionLifecycleSnapshot,
    SessionLifecycleState,
)


def session(session_id: int, state: SessionLifecycleState) -> SessionLifecycleSnapshot:
    return SessionLifecycleSnapshot(
        session_id=session_id,
        state=state,
        profile_id=2,
        priority_class=SessionPriorityClass.BALANCED,
        schema_id=0x1001,
        schema_version=3,
        max_in_flight_operations=4,
        route_scope_id=7,
        last_operation_id=11,
        session_error_code=0,
    )


def test_connection_snapshot_orders_sessions_and_preserves_resumed_state() -> None:
    snapshot = ConnectionLifecycleSnapshot(
        state=ConnectionLifecycleState.OPEN,
        sessions=(
            session(43, SessionLifecycleState.OPEN),
            session(42, SessionLifecycleState.RESUMED),
        ),
    )

    assert [item.session_id for item in snapshot.sessions] == [42, 43]
    assert snapshot.sessions[0].state is SessionLifecycleState.RESUMED
    assert snapshot.sessions[0].accepts_session_scoped_messages
    assert snapshot.sessions[0].accepts_new_operations


@pytest.mark.parametrize(
    ("state", "accepts_scoped", "accepts_new"),
    [
        (SessionLifecycleState.OPEN, True, True),
        (SessionLifecycleState.RESUMED, True, True),
        (SessionLifecycleState.CLOSING, True, False),
        (SessionLifecycleState.DRAINING, True, False),
        (SessionLifecycleState.CLOSED, False, False),
    ],
)
def test_session_lifecycle_state_capabilities(
    state: SessionLifecycleState,
    accepts_scoped: bool,
    accepts_new: bool,
) -> None:
    snapshot = session(42, state)

    assert snapshot.accepts_session_scoped_messages is accepts_scoped
    assert snapshot.accepts_new_operations is accepts_new


def test_closed_connection_requires_closed_sessions() -> None:
    with pytest.raises(ValueError, match="every session to be closed"):
        ConnectionLifecycleSnapshot(
            state=ConnectionLifecycleState.CLOSED,
            sessions=(session(42, SessionLifecycleState.RESUMED),),
        )

    snapshot = ConnectionLifecycleSnapshot(
        state=ConnectionLifecycleState.CLOSED,
        sessions=(session(42, SessionLifecycleState.CLOSED),),
    )
    assert snapshot.state is ConnectionLifecycleState.CLOSED


def test_connection_snapshot_rejects_duplicate_or_invalid_sessions() -> None:
    duplicate = session(42, SessionLifecycleState.OPEN)
    with pytest.raises(ValueError, match="duplicate session_id"):
        ConnectionLifecycleSnapshot(
            state=ConnectionLifecycleState.OPEN,
            sessions=(duplicate, duplicate),
        )
    with pytest.raises(TypeError, match="SessionLifecycleSnapshot"):
        ConnectionLifecycleSnapshot(  # type: ignore[arg-type]
            state=ConnectionLifecycleState.OPEN,
            sessions=(object(),),
        )
    with pytest.raises(TypeError, match="state must be ConnectionLifecycleState"):
        ConnectionLifecycleSnapshot(state="open")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("session_id", 0, ValueError),
        ("profile_id", 1 << 16, ValueError),
        ("schema_id", -1, ValueError),
        ("last_operation_id", 1 << 64, ValueError),
        ("session_error_code", True, TypeError),
    ],
)
def test_session_snapshot_validates_wire_widths(field: str, value: int, error: type[Exception]) -> None:
    values = {
        "session_id": 42,
        "state": SessionLifecycleState.OPEN,
        "profile_id": 2,
        "priority_class": SessionPriorityClass.BALANCED,
        "schema_id": 0x1001,
        "schema_version": 3,
        "max_in_flight_operations": 4,
        "route_scope_id": 7,
        "last_operation_id": 11,
        "session_error_code": 0,
    }
    values[field] = value

    with pytest.raises(error):
        SessionLifecycleSnapshot(**values)  # type: ignore[arg-type]


def test_snapshot_requires_frozen_enum_types() -> None:
    with pytest.raises(TypeError, match="state must be SessionLifecycleState"):
        SessionLifecycleSnapshot(
            session_id=42,
            state="open",  # type: ignore[arg-type]
            profile_id=2,
            priority_class=SessionPriorityClass.BALANCED,
            schema_id=0x1001,
            schema_version=3,
            max_in_flight_operations=4,
            route_scope_id=7,
            last_operation_id=0,
            session_error_code=0,
        )
    with pytest.raises(TypeError, match="priority_class must be SessionPriorityClass"):
        SessionLifecycleSnapshot(
            session_id=42,
            state=SessionLifecycleState.OPEN,
            profile_id=2,
            priority_class=1,  # type: ignore[arg-type]
            schema_id=0x1001,
            schema_version=3,
            max_in_flight_operations=4,
            route_scope_id=7,
            last_operation_id=0,
            session_error_code=0,
        )
