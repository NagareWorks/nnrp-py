import pytest

from nnrp import (
    DESCRIPTOR_FLAGS_KNOWN_MASK,
    SCHEMA_DESCRIPTOR_HEADER_LENGTH,
    TOKEN_DELTA_SCHEMA_HASH,
    TOKEN_DELTA_SCHEMA_ID,
    TOKEN_DELTA_SCHEMA_VERSION,
    TYPED_PAYLOAD_DESCRIPTOR_LENGTH,
    PayloadKind,
    SchemaDescriptorHeader,
    StandardProfile,
    StreamSemantics,
    TypedPayloadDescriptor,
    TypedPayloadDescriptorFlags,
    pack_schema_descriptor,
    pack_typed_payload_descriptor,
    tensor_payload_descriptor,
    token_delta_payload_descriptor,
    token_delta_schema_descriptor,
    unpack_schema_descriptor,
    unpack_typed_payload_descriptor,
    unspecified_payload_descriptor,
    validate_typed_payload_binding,
)
from nnrp.native import ERROR_FAMILY_SCHEMA, FFI_STATUS_PROTOCOL_ERROR, NativeProtocolError, NativeStatus


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


def test_typed_payload_descriptor_round_trips_current_layout() -> None:
    descriptor = TypedPayloadDescriptor(
        profile_id=StandardProfile.TOKEN,
        payload_kind=PayloadKind.TOKEN_CHUNK,
        descriptor_flags=TypedPayloadDescriptorFlags.PARTIAL,
        schema_id=TOKEN_DELTA_SCHEMA_ID,
        schema_version=TOKEN_DELTA_SCHEMA_VERSION,
        stream_semantics=StreamSemantics.APPEND,
        offset=8,
        length=13,
    )

    encoded = descriptor.pack()
    decoded = TypedPayloadDescriptor.unpack(encoded)

    assert len(encoded) == TYPED_PAYLOAD_DESCRIPTOR_LENGTH
    assert decoded == descriptor
    assert decoded.is_partial is True
    assert decoded.is_terminal is False
    assert encoded[0:2] == b"\x02\x00"
    assert encoded[14:16] == b"\x00\x00"


def test_typed_payload_descriptor_rejects_unknown_flags_reserved_bytes_and_short_buffers() -> None:
    with pytest.raises(ValueError, match="descriptor_flags contains unknown bits"):
        TypedPayloadDescriptor(
            profile_id=StandardProfile.TOKEN,
            payload_kind=PayloadKind.TOKEN_CHUNK,
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
        TypedPayloadDescriptor.unpack(encoded)

    with pytest.raises(ValueError, match="expected 24 bytes"):
        TypedPayloadDescriptor.unpack(encoded[:-1])


def test_schema_codec_helpers_delegate_to_selected_native_codec() -> None:
    schema = token_delta_schema_descriptor()
    descriptor = token_delta_payload_descriptor(offset=8, length=13)
    codec = FakeSchemaCodec(schema=schema, descriptor=descriptor)

    assert pack_schema_descriptor(schema, codec=codec) == b"native-schema"
    assert unpack_schema_descriptor(b"native-schema", codec=codec) == schema
    assert pack_typed_payload_descriptor(descriptor, codec=codec) == b"native-typed"
    assert unpack_typed_payload_descriptor(b"native-typed", codec=codec) == descriptor
    validate_typed_payload_binding((schema,), descriptor, codec=codec)

    assert codec.calls == [
        ("write_schema", schema),
        ("parse_schema", b"native-schema"),
        ("write_typed", descriptor),
        ("parse_typed", b"native-typed"),
        ("validate_binding", ((schema,), descriptor)),
    ]


def test_schema_codec_helpers_keep_python_fixture_fallback() -> None:
    schema = token_delta_schema_descriptor()
    descriptor = token_delta_payload_descriptor(offset=8, length=13)

    assert unpack_schema_descriptor(pack_schema_descriptor(schema)) == schema
    assert unpack_typed_payload_descriptor(pack_typed_payload_descriptor(descriptor)) == descriptor
    validate_typed_payload_binding((schema,), descriptor)
    validate_typed_payload_binding((), unspecified_payload_descriptor(offset=0, length=1))

    with pytest.raises(ValueError, match="does not match"):
        validate_typed_payload_binding((), descriptor)


def test_public_schema_binding_helper_preserves_native_mismatch_status() -> None:
    schema = token_delta_schema_descriptor()
    descriptor = token_delta_payload_descriptor(offset=8, length=13)
    codec = NativeMismatchSchemaCodec(schema=schema, descriptor=descriptor)

    with pytest.raises(NativeProtocolError) as mismatch:
        validate_typed_payload_binding((schema,), descriptor, codec=codec)

    assert mismatch.value.status.error_family == ERROR_FAMILY_SCHEMA
    assert mismatch.value.status.protocol_error_code == 0x3001
    assert codec.calls == [("validate_binding", ((schema,), descriptor))]


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


def test_public_typed_payload_helper_defaults_do_not_treat_unspecified_as_tensor() -> None:
    unspecified = unspecified_payload_descriptor(offset=0, length=1)
    tensor = tensor_payload_descriptor(offset=1, length=1)
    token = token_delta_payload_descriptor(offset=2, length=1)

    assert unspecified.profile_id == StandardProfile.UNSPECIFIED
    assert unspecified.schema_id == 0
    assert unspecified.schema_version == 0
    assert unspecified.stream_semantics == StreamSemantics.UNSPECIFIED
    assert unspecified.pack()[0:2] == int(StandardProfile.UNSPECIFIED).to_bytes(2, "little")
    assert tensor.profile_id == StandardProfile.TENSOR
    assert token.profile_id == StandardProfile.TOKEN
    assert {unspecified.profile_id, tensor.profile_id, token.profile_id} == {
        StandardProfile.UNSPECIFIED,
        StandardProfile.TENSOR,
        StandardProfile.TOKEN,
    }


def test_structured_event_and_tool_delta_remain_payload_families_not_standard_profiles() -> None:
    standard_profile_values = {int(profile) for profile in StandardProfile}

    assert int(PayloadKind.STRUCTURED_EVENT) == 0x00000010
    assert int(PayloadKind.TOOL_DELTA) == 0x00000020
    assert int(PayloadKind.STRUCTURED_EVENT) not in standard_profile_values
    assert int(PayloadKind.TOOL_DELTA) not in standard_profile_values


class FakeSchemaCodec:
    def __init__(self, *, schema: SchemaDescriptorHeader, descriptor: TypedPayloadDescriptor) -> None:
        self.schema = schema
        self.descriptor = descriptor
        self.calls: list[tuple[str, object]] = []

    def parse_schema_descriptor(self, payload: bytes | bytearray | memoryview) -> SchemaDescriptorHeader:
        self.calls.append(("parse_schema", bytes(payload)))
        return self.schema

    def write_schema_descriptor(self, descriptor: SchemaDescriptorHeader) -> bytes:
        self.calls.append(("write_schema", descriptor))
        return b"native-schema"

    def parse_typed_payload_descriptor(
        self,
        payload: bytes | bytearray | memoryview,
    ) -> TypedPayloadDescriptor:
        self.calls.append(("parse_typed", bytes(payload)))
        return self.descriptor

    def write_typed_payload_descriptor(self, descriptor: TypedPayloadDescriptor) -> bytes:
        self.calls.append(("write_typed", descriptor))
        return b"native-typed"

    def validate_typed_payload_binding(
        self,
        schemas: tuple[SchemaDescriptorHeader, ...],
        descriptor: TypedPayloadDescriptor,
    ) -> None:
        self.calls.append(("validate_binding", (schemas, descriptor)))


class NativeMismatchSchemaCodec(FakeSchemaCodec):
    def validate_typed_payload_binding(
        self,
        schemas: tuple[SchemaDescriptorHeader, ...],
        descriptor: TypedPayloadDescriptor,
    ) -> None:
        self.calls.append(("validate_binding", (schemas, descriptor)))
        raise NativeProtocolError(NativeStatus(FFI_STATUS_PROTOCOL_ERROR, ERROR_FAMILY_SCHEMA, 0x3001, 0x41))

