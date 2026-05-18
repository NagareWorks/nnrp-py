"""NNRP fixed-width header helpers."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from nnrp.core.enums import HeaderFlags, MessageType, WireFormat

HEADER_MAGIC = b"NNRP"
HEADER_STRUCT = struct.Struct("<4sBBBBIIIIIHHQ")
HEADER_LENGTH = HEADER_STRUCT.size


@dataclass(slots=True)
class NnrpHeader:
    version_major: int
    wire_format: WireFormat
    msg_type: MessageType
    flags: HeaderFlags
    meta_len: int
    body_len: int
    session_id: int
    frame_id: int
    view_id: int
    route_id: int
    trace_id: int
    header_len: int = HEADER_LENGTH

    def pack(self) -> bytes:
        if self.header_len != HEADER_LENGTH:
            raise ValueError(f"header_len must be {HEADER_LENGTH}")

        return HEADER_STRUCT.pack(
            HEADER_MAGIC,
            self.version_major,
            int(self.wire_format),
            int(self.msg_type),
            self.header_len,
            int(self.flags),
            self.meta_len,
            self.body_len,
            self.session_id,
            self.frame_id,
            self.view_id,
            self.route_id,
            self.trace_id,
        )

    @classmethod
    def unpack(
        cls,
        payload: bytes,
        *,
        expected_wire_format: WireFormat | None = None,
    ) -> NnrpHeader:
        if len(payload) != HEADER_LENGTH:
            raise ValueError(f"expected {HEADER_LENGTH} bytes, got {len(payload)}")

        (
            magic,
            version_major,
            wire_format,
            msg_type,
            header_len,
            flags,
            meta_len,
            body_len,
            session_id,
            frame_id,
            view_id,
            route_id,
            trace_id,
        ) = HEADER_STRUCT.unpack(payload)

        if magic != HEADER_MAGIC:
            raise ValueError(f"unexpected magic: {magic!r}")
        if header_len != HEADER_LENGTH:
            raise ValueError(f"unexpected header_len: {header_len}")

        header = cls(
            version_major=version_major,
            wire_format=WireFormat(wire_format),
            msg_type=MessageType(msg_type),
            flags=HeaderFlags(flags),
            meta_len=meta_len,
            body_len=body_len,
            session_id=session_id,
            frame_id=frame_id,
            view_id=view_id,
            route_id=route_id,
            trace_id=trace_id,
            header_len=header_len,
        )
        if expected_wire_format is not None and header.wire_format is not expected_wire_format:
            raise ValueError(f"unexpected wire_format: {int(header.wire_format)} != {int(expected_wire_format)}")
        return header
