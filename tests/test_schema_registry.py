import pytest

from nnrp import (
    DESCRIPTOR_FLAGS_KNOWN_MASK,
    SCHEMA_DESCRIPTOR_HEADER_LENGTH,
    TOKEN_DELTA_SCHEMA_HASH,
    TOKEN_DELTA_SCHEMA_ID,
    TOKEN_DELTA_SCHEMA_VERSION,
    TYPED_PAYLOAD_DESCRIPTOR_V3_LENGTH,
    PayloadKind,
    Preview3TypedPayloadDescriptor,
    SchemaDescriptorHeader,
    StandardProfile,
    StreamSemantics,
    TypedPayloadDescriptorFlags,
    tensor_payload_descriptor,
    token_delta_payload_descriptor,
    token_delta_schema_descriptor,
    unspecified_payload_descriptor,
)


def test_schema_descriptor_header_round_trips_frozen_preview3_layout() -> None:
    descriptor = SchemaDescriptorHeader(
        schema_id=TOKEN_DELTA_SCHEMA_ID,
        schema_version=TOKEN_DELTA_SCHEMA_VERSION,
        profile_id=StandardProfile.TOKEN,
        min_version_major=1,
        max_version_major=1,
        body_bytes=64,
        dependency_count=2,
        default_stream_semantics=StreamSemantics.APPEND,
        schema_hash=TOKEN_DELTA_SCHEMA_HASH,
    )

    encoded = descriptor.pack()
    decoded = SchemaDescriptorHeader.unpack(encoded)

    assert len(encoded) == SCHEMA_DESCRIPTOR_HEADER_LENGTH
    assert decoded == descriptor
    assert encoded[8:10] == b"\x02\x00"
    assert encoded[14:16] == b"\x00\x00"
    assert encoded[22:24] == b"\x02\x00"


def test_schema_descriptor_header_rejects_unknown_flags_and_reserved_bytes() -> None:
    with pytest.raises(ValueError, match="schema_flags contains unknown bits"):
        SchemaDescriptorHeader(
            schema_id=1,
            schema_version=1,
            profile_id=StandardProfile.TENSOR,
            schema_flags=0x0010,
        )

    encoded = bytearray(token_delta_schema_descriptor().pack())
    encoded[14:16] = b"\x01\x00"

    with pytest.raises(ValueError, match="reserved0"):
        SchemaDescriptorHeader.unpack(encoded)


def test_typed_payload_descriptor_round_trips_frozen_preview3_layout() -> None:
    descriptor = Preview3TypedPayloadDescriptor(
        profile_id=StandardProfile.TOKEN,
        descriptor_flags=TypedPayloadDescriptorFlags.PARTIAL,
        schema_id=TOKEN_DELTA_SCHEMA_ID,
        schema_version=TOKEN_DELTA_SCHEMA_VERSION,
        stream_semantics=StreamSemantics.APPEND,
        offset=8,
        length=13,
    )

    encoded = descriptor.pack()
    decoded = Preview3TypedPayloadDescriptor.unpack(encoded)

    assert len(encoded) == TYPED_PAYLOAD_DESCRIPTOR_V3_LENGTH
    assert decoded == descriptor
    assert decoded.is_partial is True
    assert decoded.is_terminal is False
    assert encoded[0:2] == b"\x02\x00"
    assert encoded[14:16] == b"\x00\x00"


def test_typed_payload_descriptor_rejects_unknown_flags_reserved_bytes_and_short_buffers() -> None:
    with pytest.raises(ValueError, match="descriptor_flags contains unknown bits"):
        Preview3TypedPayloadDescriptor(
            profile_id=StandardProfile.TOKEN,
            descriptor_flags=DESCRIPTOR_FLAGS_KNOWN_MASK + 1,
            schema_id=TOKEN_DELTA_SCHEMA_ID,
            schema_version=TOKEN_DELTA_SCHEMA_VERSION,
            stream_semantics=StreamSemantics.APPEND,
            offset=0,
            length=1,
        )

    encoded = bytearray(token_delta_payload_descriptor(offset=0, length=1).pack())
    encoded[14:16] = b"\x01\x00"

    with pytest.raises(ValueError, match="reserved0"):
        Preview3TypedPayloadDescriptor.unpack(encoded)

    with pytest.raises(ValueError, match="expected 24 bytes"):
        Preview3TypedPayloadDescriptor.unpack(encoded[:-1])


def test_standard_profile_helpers_keep_unspecified_tensor_and_token_distinct() -> None:
    unspecified = unspecified_payload_descriptor(offset=0, length=5)
    tensor = tensor_payload_descriptor(offset=5, length=7)
    token = token_delta_payload_descriptor(offset=12, length=9, terminal=True, partial=False)

    assert unspecified.profile_id == StandardProfile.UNSPECIFIED
    assert tensor.profile_id == StandardProfile.TENSOR
    assert token.profile_id == StandardProfile.TOKEN
    assert token.is_terminal is True
    assert token.is_partial is False
    assert StandardProfile.UNSPECIFIED != StandardProfile.TENSOR
    assert token_delta_schema_descriptor().default_stream_semantics == StreamSemantics.APPEND


def test_structured_event_and_tool_delta_remain_payload_families_not_standard_profiles() -> None:
    standard_profile_values = {int(profile) for profile in StandardProfile}

    assert int(PayloadKind.STRUCTURED_EVENT) == 0x00000010
    assert int(PayloadKind.TOOL_DELTA) == 0x00000020
    assert int(PayloadKind.STRUCTURED_EVENT) not in standard_profile_values
    assert int(PayloadKind.TOOL_DELTA) not in standard_profile_values

