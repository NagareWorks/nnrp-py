import pytest

from nnrp.core.enums import HeaderFlags, MessageType, WireFormat
from nnrp.core.header import HEADER_LENGTH, NnrpHeader


def test_header_pack_roundtrip() -> None:
    header = NnrpHeader(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.FRAME_SUBMIT,
        flags=HeaderFlags.ACK_REQUIRED | HeaderFlags.KEYFRAME,
        meta_len=48,
        body_len=4096,
        session_id=7,
        frame_id=11,
        view_id=2,
        route_id=0,
        trace_id=123456789,
    )

    payload = header.pack()

    assert len(payload) == HEADER_LENGTH
    assert NnrpHeader.unpack(payload) == header


def test_header_rejects_wrong_magic() -> None:
    bad = b"FAIL" + b"\x00" * (HEADER_LENGTH - 4)

    try:
        NnrpHeader.unpack(bad)
    except ValueError as exc:
        assert "unexpected magic" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_header_rejects_wrong_header_len() -> None:
    header = NnrpHeader(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.FRAME_SUBMIT,
        flags=HeaderFlags.NONE,
        meta_len=0,
        body_len=0,
        session_id=0,
        frame_id=0,
        view_id=0,
        route_id=0,
        trace_id=0,
    )
    payload = bytearray(header.pack())
    payload[7] = HEADER_LENGTH - 1

    try:
        NnrpHeader.unpack(bytes(payload))
    except ValueError as exc:
        assert "unexpected header_len" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_header_pack_roundtrip_current_transport_probe() -> None:
    header = NnrpHeader(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.TRANSPORT_PROBE,
        flags=HeaderFlags.NONE,
        meta_len=16,
        body_len=24,
        session_id=9,
        frame_id=0,
        view_id=0,
        route_id=3,
        trace_id=42,
    )

    payload = header.pack()

    assert len(payload) == HEADER_LENGTH
    assert NnrpHeader.unpack(payload) == header


def test_current_header_keeps_fixed_40_byte_shape() -> None:
    header = NnrpHeader(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.RESULT_HINT,
        flags=HeaderFlags.ACK_REQUIRED,
        meta_len=16,
        body_len=0,
        session_id=15,
        frame_id=22,
        view_id=0,
        route_id=1,
        trace_id=99,
    )

    payload = header.pack()
    decoded = NnrpHeader.unpack(payload, expected_wire_format=WireFormat.CURRENT)

    assert len(payload) == 40
    assert HEADER_LENGTH == 40
    assert decoded.header_len == 40
    assert decoded.meta_len == 16
    assert decoded.body_len == 0


def test_header_unpack_accepts_current_under_strict_stage() -> None:
    header = NnrpHeader(
        version_major=1,
        wire_format=WireFormat.CURRENT,
        msg_type=MessageType.TRANSPORT_PROBE,
        flags=HeaderFlags.NONE,
        meta_len=0,
        body_len=0,
        session_id=0,
        frame_id=0,
        view_id=0,
        route_id=0,
        trace_id=0,
    )

    decoded = NnrpHeader.unpack(header.pack(), expected_wire_format=WireFormat.CURRENT)
    assert decoded == header


def test_header_unpack_rejects_unexpected_stage_value() -> None:
    payload = bytearray(
        NnrpHeader(
            version_major=1,
            wire_format=WireFormat.CURRENT,
            msg_type=MessageType.PING,
            flags=HeaderFlags.NONE,
            meta_len=0,
            body_len=0,
            session_id=0,
            frame_id=0,
            view_id=0,
            route_id=0,
            trace_id=0,
        ).pack()
    )
    payload[5] = 2

    with pytest.raises(ValueError, match="2 is not a valid WireFormat"):
        NnrpHeader.unpack(bytes(payload), expected_wire_format=WireFormat.CURRENT)
