import json

import pytest

from nnrp.tools.wire_conformance import (
    WireTargetTransport,
    build_wire_target_manifest,
    main,
    parse_wire_target_transport,
)


def test_build_wire_target_manifest_uses_preview4_schema_and_explicit_capabilities() -> None:
    manifest = build_wire_target_manifest(
        target_name="nnrp-py-dev",
        suite_version="0.1.0",
        modes=["suite_as_client", "suite_as_server", "suite_as_client"],
        transports=[
            WireTargetTransport("tcp", "127.0.0.1:19091"),
            WireTargetTransport("websocket", "wss://localhost/nnrp", tls=True),
        ],
        capabilities=["control.cancel_abort", "control.trace_context", "control.cancel_abort"],
        max_frame_bytes=4096,
        max_in_flight=8,
    )

    assert manifest["$schema"].endswith("/schemas/wire-conformance-target.schema.json")
    assert manifest["target_name"] == "nnrp-py-dev"
    assert manifest["protocol_version"] == "nnrp-1-preview4"
    assert manifest["suite_version"] == "0.1.0"
    assert manifest["wire_conformance"] == {
        "modes": ["suite_as_client", "suite_as_server"],
        "transports": [
            {"name": "tcp", "endpoint": "127.0.0.1:19091", "tls": False},
            {"name": "websocket", "endpoint": "wss://localhost/nnrp", "tls": True},
        ],
        "capabilities": ["control.cancel_abort", "control.trace_context"],
        "limits": {
            "max_frame_bytes": 4096,
            "max_in_flight": 8,
        },
    }


def test_parse_wire_target_transport_infers_tls_for_secure_websocket() -> None:
    assert parse_wire_target_transport("websocket=wss://localhost/nnrp") == WireTargetTransport(
        "websocket",
        "wss://localhost/nnrp",
        tls=True,
    )
    assert parse_wire_target_transport("quic=quic+tls://localhost:19092") == WireTargetTransport(
        "quic",
        "quic+tls://localhost:19092",
        tls=True,
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "target_name": "",
                "modes": ["suite_as_client"],
                "transports": [WireTargetTransport("tcp", "127.0.0.1:1")],
                "capabilities": ["control.cancel_abort"],
            },
            "target_name must be non-empty",
        ),
        (
            {
                "suite_version": "",
                "modes": ["suite_as_client"],
                "transports": [WireTargetTransport("tcp", "127.0.0.1:1")],
                "capabilities": ["control.cancel_abort"],
            },
            "suite_version must be non-empty",
        ),
        (
            {
                "modes": ["suite_as_client"],
                "transports": [WireTargetTransport("tcp", "127.0.0.1:1")],
                "capabilities": ["control.cancel_abort"],
                "max_frame_bytes": 0,
            },
            "max_frame_bytes must be positive",
        ),
        (
            {
                "modes": ["suite_as_client"],
                "transports": [WireTargetTransport("tcp", "127.0.0.1:1")],
                "capabilities": ["control.cancel_abort"],
                "max_in_flight": 0,
            },
            "max_in_flight must be positive",
        ),
        (
            {
                "modes": [""],
                "transports": [WireTargetTransport("tcp", "127.0.0.1:1")],
                "capabilities": ["control.cancel_abort"],
            },
            "modes entries must be non-empty",
        ),
        (
            {
                "modes": ["unknown"],
                "transports": [WireTargetTransport("tcp", "127.0.0.1:1")],
                "capabilities": ["control.cancel_abort"],
            },
            "unsupported wire conformance mode",
        ),
        (
            {
                "modes": ["suite_as_client"],
                "transports": [WireTargetTransport("udp", "127.0.0.1:1")],
                "capabilities": ["control.cancel_abort"],
            },
            "unsupported wire conformance transport",
        ),
        (
            {
                "modes": ["suite_as_client"],
                "transports": [WireTargetTransport("tcp", "127.0.0.1:1")],
                "capabilities": [],
            },
            "capabilities must not be empty",
        ),
        (
            {
                "modes": ["suite_as_client"],
                "transports": [],
                "capabilities": ["control.cancel_abort"],
            },
            "transports must not be empty",
        ),
        (
            {
                "modes": ["suite_as_client"],
                "transports": [WireTargetTransport("tcp", "")],
                "capabilities": ["control.cancel_abort"],
            },
            "transport endpoint must be non-empty",
        ),
        (
            {
                "modes": ["suite_as_client"],
                "transports": [
                    WireTargetTransport("tcp", "127.0.0.1:1"),
                    WireTargetTransport("tcp", "127.0.0.1:2"),
                ],
                "capabilities": ["control.cancel_abort"],
            },
            "duplicate wire conformance transport",
        ),
    ],
)
def test_build_wire_target_manifest_rejects_false_or_invalid_claims(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_wire_target_manifest(**kwargs)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("tcp", "transport must use name=endpoint form"),
        ("tcp=", "transport endpoint must be non-empty"),
    ],
)
def test_parse_wire_target_transport_rejects_invalid_values(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_wire_target_transport(value)


def test_wire_target_manifest_cli_writes_json(tmp_path) -> None:
    output_path = tmp_path / "target.json"

    assert main(
        [
            "--target-name",
            "nnrp-py-local",
            "--mode",
            "suite_as_client",
            "--transport",
            "tcp=127.0.0.1:19091",
            "--capability",
            "control.cancel_abort",
            "--output",
            str(output_path),
        ]
    ) == 0

    manifest = json.loads(output_path.read_text(encoding="utf-8"))
    assert manifest["target_name"] == "nnrp-py-local"
    assert manifest["wire_conformance"]["transports"] == [
        {"name": "tcp", "endpoint": "127.0.0.1:19091", "tls": False}
    ]
