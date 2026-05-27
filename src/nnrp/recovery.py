"""Recovery validation helpers backed by the native runtime contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from nnrp.native import NativeSessionRecoveryOutcome


class RecoveryCodec(Protocol):
    def validate_session_recovery_request(self, session_open_metadata: bytes | bytearray | memoryview) -> None:
        ...

    def validate_session_recovery_ack(
        self,
        session_open_metadata: bytes | bytearray | memoryview,
        session_open_ack_metadata: bytes | bytearray | memoryview,
    ) -> NativeSessionRecoveryOutcome:
        ...

    def validate_migration_recovery(
        self,
        session_migrate_metadata: bytes | bytearray | memoryview,
        session_migrate_ack_metadata: bytes | bytearray | memoryview,
    ) -> None:
        ...

    def should_replay_frame_after_migration(
        self,
        session_migrate_ack_metadata: bytes | bytearray | memoryview,
        frame_id: int,
    ) -> bool:
        ...


@dataclass(frozen=True, slots=True)
class SessionRecoveryReport:
    outcome_code: int
    outcome_name: str
    resume_window_ms: int

    @classmethod
    def from_native(cls, outcome: NativeSessionRecoveryOutcome) -> SessionRecoveryReport:
        return cls(
            outcome_code=outcome.outcome_code,
            outcome_name=outcome.outcome_name,
            resume_window_ms=outcome.resume_window_ms,
        )

    @property
    def is_fresh(self) -> bool:
        return self.outcome_name == "fresh"

    @property
    def resume_enabled(self) -> bool:
        return self.outcome_name == "resume_enabled"

    @property
    def resumed(self) -> bool:
        return self.outcome_name == "resumed"

    @property
    def resume_rejected(self) -> bool:
        return self.outcome_name == "resume_rejected"

    def to_report(self) -> dict[str, int | str | bool]:
        return {
            "outcome_code": self.outcome_code,
            "outcome_name": self.outcome_name,
            "resume_window_ms": self.resume_window_ms,
            "is_fresh": self.is_fresh,
            "resume_enabled": self.resume_enabled,
            "resumed": self.resumed,
            "resume_rejected": self.resume_rejected,
        }


def validate_session_recovery_request(
    codec: RecoveryCodec,
    session_open_metadata: bytes | bytearray | memoryview,
) -> None:
    codec.validate_session_recovery_request(session_open_metadata)


def validate_session_recovery_ack(
    codec: RecoveryCodec,
    session_open_metadata: bytes | bytearray | memoryview,
    session_open_ack_metadata: bytes | bytearray | memoryview,
) -> SessionRecoveryReport:
    return SessionRecoveryReport.from_native(
        codec.validate_session_recovery_ack(session_open_metadata, session_open_ack_metadata)
    )


def validate_migration_recovery(
    codec: RecoveryCodec,
    session_migrate_metadata: bytes | bytearray | memoryview,
    session_migrate_ack_metadata: bytes | bytearray | memoryview,
) -> None:
    codec.validate_migration_recovery(session_migrate_metadata, session_migrate_ack_metadata)


def should_replay_frame_after_migration(
    codec: RecoveryCodec,
    session_migrate_ack_metadata: bytes | bytearray | memoryview,
    frame_id: int,
) -> bool:
    return codec.should_replay_frame_after_migration(session_migrate_ack_metadata, frame_id)


__all__ = [
    "RecoveryCodec",
    "SessionRecoveryReport",
    "should_replay_frame_after_migration",
    "validate_migration_recovery",
    "validate_session_recovery_ack",
    "validate_session_recovery_request",
]
