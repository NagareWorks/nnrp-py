import pytest

from nnrp import (
    NativeProtocolError,
    NativeSessionRecoveryOutcome,
    NativeStatus,
    RecoveryCodec,
    SessionRecoveryReport,
    should_replay_frame_after_migration,
    validate_migration_recovery,
    validate_session_recovery_ack,
    validate_session_recovery_request,
)
from nnrp.native import FFI_STATUS_PROTOCOL_ERROR


class FakeRecoveryCodec:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.failure: BaseException | None = None

    def validate_session_recovery_request(self, session_open_metadata: bytes | bytearray | memoryview) -> None:
        self.calls.append(("request", bytes(session_open_metadata)))
        if self.failure is not None:
            raise self.failure

    def validate_session_recovery_ack(
        self,
        session_open_metadata: bytes | bytearray | memoryview,
        session_open_ack_metadata: bytes | bytearray | memoryview,
    ) -> NativeSessionRecoveryOutcome:
        self.calls.append(("ack", (bytes(session_open_metadata), bytes(session_open_ack_metadata))))
        if self.failure is not None:
            raise self.failure
        return NativeSessionRecoveryOutcome(2, 250)

    def validate_migration_recovery(
        self,
        session_migrate_metadata: bytes | bytearray | memoryview,
        session_migrate_ack_metadata: bytes | bytearray | memoryview,
    ) -> None:
        self.calls.append(("migration", (bytes(session_migrate_metadata), bytes(session_migrate_ack_metadata))))
        if self.failure is not None:
            raise self.failure

    def should_replay_frame_after_migration(
        self,
        session_migrate_ack_metadata: bytes | bytearray | memoryview,
        frame_id: int,
    ) -> bool:
        self.calls.append(("replay", (bytes(session_migrate_ack_metadata), frame_id)))
        if self.failure is not None:
            raise self.failure
        return frame_id >= 42


def test_recovery_public_helpers_delegate_to_native_codec() -> None:
    codec: RecoveryCodec = FakeRecoveryCodec()

    validate_session_recovery_request(codec, b"open")
    report = validate_session_recovery_ack(codec, b"open", b"ack")
    validate_migration_recovery(codec, b"migrate", b"migrate-ack")
    assert should_replay_frame_after_migration(codec, b"migrate-ack", 42) is True
    assert should_replay_frame_after_migration(codec, b"migrate-ack", 41) is False

    assert isinstance(report, SessionRecoveryReport)
    assert report.outcome_code == 2
    assert report.outcome_name == "resumed"
    assert report.resume_window_ms == 250
    assert report.resumed is True
    assert report.resume_enabled is False
    assert report.to_report() == {
        "outcome_code": 2,
        "outcome_name": "resumed",
        "resume_window_ms": 250,
        "is_fresh": False,
        "resume_enabled": False,
        "resumed": True,
        "resume_rejected": False,
    }
    assert codec.calls == [
        ("request", b"open"),
        ("ack", (b"open", b"ack")),
        ("migration", (b"migrate", b"migrate-ack")),
        ("replay", (b"migrate-ack", 42)),
        ("replay", (b"migrate-ack", 41)),
    ]


def test_recovery_public_helpers_preserve_native_status_fields() -> None:
    codec = FakeRecoveryCodec()
    codec.failure = NativeProtocolError(
        NativeStatus(FFI_STATUS_PROTOCOL_ERROR, error_family=1, protocol_error_code=0x1006, detail_code=0x44)
    )

    with pytest.raises(NativeProtocolError) as captured:
        validate_session_recovery_request(codec, b"bad-open")

    assert captured.value.status.error_family_name == "session"
    assert captured.value.status.protocol_error_code == 0x1006
    assert captured.value.status.detail_code == 0x44


def test_session_recovery_report_does_not_parse_token_payloads() -> None:
    report = SessionRecoveryReport.from_native(NativeSessionRecoveryOutcome(3, 0))

    assert report.resume_rejected is True
    assert report.to_report()["outcome_name"] == "resume_rejected"
    assert not hasattr(report, "token")
    assert not hasattr(report, "cursor")
