import json
import sys

import pytest

from nnrp.tools.wire_conformance import (
    WireCaseResult,
    WireObservedFrame,
    WireTargetTransport,
    WireTargetTransportSecurity,
    build_wire_case_results_report,
    build_wire_skipped_results_from_plan,
    build_wire_target_manifest,
    main,
    parse_wire_target_security,
    parse_wire_target_transport,
    run_wire_harness_plan,
    validate_wire_case_results_against_plan,
    write_wire_case_results_report,
    write_wire_evidence_files,
)


def test_build_wire_target_manifest_uses_preview4_schema_and_explicit_capabilities() -> None:
    security = WireTargetTransportSecurity(
        server_name="localhost",
        trusted_certificate_der_path="certs/server.der",
        certificate_der_path="certs/server.der",
        private_key_pkcs8_der_path="certs/server-key.der",
    )
    manifest = build_wire_target_manifest(
        target_name="nnrp-py-dev",
        suite_version="0.1.0",
        modes=["suite_as_client", "suite_as_server", "suite_as_client"],
        transports=[
            WireTargetTransport("tcp", "127.0.0.1:19091"),
            WireTargetTransport("websocket", "wss://localhost/nnrp", tls=True, security=security),
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
            {
                "name": "websocket",
                "endpoint": "wss://localhost/nnrp",
                "tls": True,
                "security": {
                    "server_name": "localhost",
                    "trusted_certificate_der_path": "certs/server.der",
                    "certificate_der_path": "certs/server.der",
                    "private_key_pkcs8_der_path": "certs/server-key.der",
                },
            },
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


def test_parse_wire_target_security_reads_frozen_fields() -> None:
    transport, security = parse_wire_target_security(
        json.dumps(
            {
                "transport": "quic",
                "server_name": "localhost",
                "trusted_certificate_der_path": "certs/server.der",
                "certificate_der_path": "certs/server.der",
                "private_key_pkcs8_der_path": "certs/server-key.der",
            }
        )
    )
    assert transport == "quic"
    assert security == WireTargetTransportSecurity(
        server_name="localhost",
        trusted_certificate_der_path="certs/server.der",
        certificate_der_path="certs/server.der",
        private_key_pkcs8_der_path="certs/server-key.der",
    )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("{", "must be a JSON object"),
        ("[]", "must be a JSON object"),
        (
            json.dumps(
                {
                    "transport": "quic",
                    "server_name": "localhost",
                    "trusted_certificate_der_path": "trust.der",
                    "certificate_der_path": "server.der",
                    "private_key_pkcs8_der_path": "server-key.der",
                    "unexpected": True,
                }
            ),
            "contains unknown fields",
        ),
        (json.dumps({"transport": "quic"}), "is missing fields"),
    ],
)
def test_parse_wire_target_security_rejects_incomplete_or_ambiguous_values(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_wire_target_security(value)


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
        (
            {
                "modes": ["suite_as_client"],
                "transports": [WireTargetTransport("quic", "quic+tls://127.0.0.1:1", tls=True)],
                "capabilities": ["control.cancel_abort"],
            },
            "requires security material",
        ),
        (
            {
                "modes": ["suite_as_client"],
                "transports": [WireTargetTransport("quic", "127.0.0.1:1")],
                "capabilities": ["control.cancel_abort"],
            },
            "requires TLS",
        ),
        (
            {
                "modes": ["suite_as_client"],
                "transports": [
                    WireTargetTransport(
                        "tcp",
                        "127.0.0.1:1",
                        security=WireTargetTransportSecurity("localhost", "trust", "cert", "key"),
                    )
                ],
                "capabilities": ["control.cancel_abort"],
            },
            "must not declare security material",
        ),
        (
            {
                "modes": ["suite_as_client"],
                "transports": [WireTargetTransport("tcp", "127.0.0.1:1", tls=True)],
                "capabilities": ["control.cancel_abort"],
            },
            "does not use TLS",
        ),
        (
            {
                "modes": ["suite_as_client"],
                "transports": [
                    WireTargetTransport(
                        "websocket",
                        "ws://127.0.0.1:1/nnrp",
                        tls=True,
                        security=WireTargetTransportSecurity("localhost", "trust", "cert", "key"),
                    )
                ],
                "capabilities": ["control.cancel_abort"],
            },
            "TLS flag must match",
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


def test_wire_target_manifest_cli_accepts_manifest_subcommand(tmp_path) -> None:
    output_path = tmp_path / "target.json"

    assert main(
        [
            "manifest",
            "--target-name",
            "nnrp-py-local",
            "--mode",
            "suite_as_server",
            "--transport",
            "tcp=127.0.0.1:19091",
            "--capability",
            "control.progress_partial_result",
            "--output",
            str(output_path),
        ]
    ) == 0

    manifest = json.loads(output_path.read_text(encoding="utf-8"))
    assert manifest["wire_conformance"]["modes"] == ["suite_as_server"]


def test_wire_target_manifest_cli_writes_tls_security(tmp_path) -> None:
    output_path = tmp_path / "target.json"
    security = json.dumps(
        {
            "transport": "quic",
            "server_name": "localhost",
            "trusted_certificate_der_path": "certs/server.der",
            "certificate_der_path": "certs/server.der",
            "private_key_pkcs8_der_path": "certs/server-key.der",
        }
    )

    assert main(
        [
            "manifest",
            "--mode",
            "suite_as_client",
            "--transport",
            "quic=quic+tls://localhost:19092",
            "--transport-security",
            security,
            "--capability",
            "control.cancel_abort",
            "--output",
            str(output_path),
        ]
    ) == 0

    transport = json.loads(output_path.read_text(encoding="utf-8"))["wire_conformance"]["transports"][0]
    assert transport["security"]["server_name"] == "localhost"


@pytest.mark.parametrize(
    ("security_values", "message"),
    [
        (
            [
                {
                    "transport": "quic",
                    "server_name": "localhost",
                    "trusted_certificate_der_path": "trust.der",
                    "certificate_der_path": "server.der",
                    "private_key_pkcs8_der_path": "server-key.der",
                },
                {
                    "transport": "quic",
                    "server_name": "localhost",
                    "trusted_certificate_der_path": "other-trust.der",
                    "certificate_der_path": "other-server.der",
                    "private_key_pkcs8_der_path": "other-key.der",
                },
            ],
            "duplicate transport security",
        ),
        (
            [
                {
                    "transport": "websocket",
                    "server_name": "localhost",
                    "trusted_certificate_der_path": "trust.der",
                    "certificate_der_path": "server.der",
                    "private_key_pkcs8_der_path": "server-key.der",
                }
            ],
            "references undeclared transport",
        ),
    ],
)
def test_wire_target_manifest_cli_rejects_duplicate_or_unknown_security(
    tmp_path,
    security_values: list[dict[str, object]],
    message: str,
) -> None:
    arguments = [
        "manifest",
        "--mode",
        "suite_as_client",
        "--transport",
        "quic=quic+tls://localhost:19092",
        "--capability",
        "control.cancel_abort",
        "--output",
        str(tmp_path / "target.json"),
    ]
    for security in security_values:
        arguments.extend(("--transport-security", json.dumps(security)))

    with pytest.raises(ValueError, match=message):
        main(arguments)


def test_build_wire_case_results_report_uses_preview4_schema_and_observed_frames() -> None:
    report = build_wire_case_results_report(
        target_name="nnrp-py-local",
        suite_version="0.1.0",
        results=[
            WireCaseResult(
                id="wire.control.cancel-abort.client",
                outcome="passed",
                terminal="cancelled",
                observed_frames=[
                    WireObservedFrame("sent", "CANCEL", {"operation_id": "op-1"}, timestamp_us=10),
                    WireObservedFrame("received", "RESULT_DROP_REASON", {"reason": "superseded"}, timestamp_us=20),
                ],
                evidence_paths=["artifacts/wire-evidence/cancel-abort.jsonl"],
            )
        ],
    )

    assert report["$schema"].endswith("/schemas/wire-conformance-case-results.schema.json")
    assert report["protocol_version"] == "nnrp-1-preview4"
    assert report["target_name"] == "nnrp-py-local"
    assert report["results"] == [
        {
            "id": "wire.control.cancel-abort.client",
            "outcome": "passed",
            "terminal": "cancelled",
            "observed_frames": [
                {
                    "direction": "sent",
                    "frame": "CANCEL",
                    "payload": {"operation_id": "op-1"},
                    "timestamp_us": 10,
                },
                {
                    "direction": "received",
                    "frame": "RESULT_DROP_REASON",
                    "payload": {"reason": "superseded"},
                    "timestamp_us": 20,
                },
            ],
            "evidence_paths": ["artifacts/wire-evidence/cancel-abort.jsonl"],
        }
    ]


def test_write_wire_case_results_report_and_evidence_files(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    report = build_wire_case_results_report(
        results=[
            WireCaseResult(
                id="wire.control.progress-backpressure.server",
                outcome="passed",
                terminal="success",
                observed_frames=[WireObservedFrame("sent", "PROGRESS", {"stage": "prefill"})],
                evidence_paths=["artifacts/wire-evidence/progress.jsonl"],
            )
        ],
    )

    report_path = tmp_path / "artifacts" / "wire-results.json"
    write_wire_case_results_report(report_path, report)
    write_wire_evidence_files(report)

    assert json.loads(report_path.read_text(encoding="utf-8"))["results"][0]["id"] == (
        "wire.control.progress-backpressure.server"
    )
    evidence = json.loads((tmp_path / "artifacts" / "wire-evidence" / "progress.jsonl").read_text(encoding="utf-8"))
    assert evidence["observed_frames"] == [{"direction": "sent", "frame": "PROGRESS", "payload": {"stage": "prefill"}}]


def test_build_wire_skipped_results_from_plan_keeps_skips_explicit() -> None:
    plan = {
        "protocol_version": "nnrp-1-preview4",
        "suite_version": "0.1.0",
        "target_name": "nnrp-py-local",
        "scenarios": [
            {
                "id": "wire.control.cancel-abort.client",
                "expect": {"terminal": "cancelled", "frames": ["RESULT_DROP_REASON"]},
            }
        ],
    }

    report = build_wire_skipped_results_from_plan(plan, message="native tcp provider is not installed")

    assert report["results"] == [
        {
            "id": "wire.control.cancel-abort.client",
            "outcome": "skipped",
            "terminal": "error",
            "message": "native tcp provider is not installed",
        }
    ]


def test_run_wire_harness_plan_filters_mode_and_writes_evidence(tmp_path) -> None:
    plan_path = tmp_path / "wire-plan.json"
    output_path = tmp_path / "wire-results.json"
    evidence_dir = tmp_path / "wire-evidence"
    plan_path.write_text(
        json.dumps(
            {
                "protocol_version": "nnrp-1-preview4",
                "suite_version": "0.1.0",
                "target_name": "nnrp-py-local",
                "scenarios": [
                    {
                        "id": "wire.control.cancel-abort.client",
                        "mode": "suite_as_client",
                        "expect": {"terminal": "cancelled", "frames": ["RESULT_DROP_REASON"]},
                    },
                    {
                        "id": "wire.control.progress.server",
                        "mode": "suite_as_server",
                        "expect": {"terminal": "success", "frames": ["PROGRESS"]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    report = run_wire_harness_plan(
        plan_path,
        output_path,
        mode="suite_as_client",
        evidence_dir=evidence_dir,
        skip_message="live suite-as-client endpoint is not configured",
    )

    assert report["results"] == [
        {
            "id": "wire.control.cancel-abort.client",
            "outcome": "skipped",
            "terminal": "error",
            "message": "live suite-as-client endpoint is not configured",
            "evidence_paths": [str(evidence_dir / "wire-control-cancel-abort-client.jsonl")],
        }
    ]
    assert json.loads(output_path.read_text(encoding="utf-8")) == report
    evidence = json.loads((evidence_dir / "wire-control-cancel-abort-client.jsonl").read_text(encoding="utf-8"))
    assert evidence["message"] == "live suite-as-client endpoint is not configured"


def test_wire_conformance_run_plan_cli_writes_skipped_results(tmp_path) -> None:
    plan_path = tmp_path / "wire-plan.json"
    output_path = tmp_path / "wire-results.json"
    plan_path.write_text(
        json.dumps(
            {
                "suite_version": "0.1.0",
                "target_name": "nnrp-py-local",
                "scenarios": [
                    {
                        "id": "wire.control.progress.server",
                        "mode": "suite_as_server",
                        "expect": {"terminal": "success", "frames": ["PROGRESS"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert main(["run-plan", "--plan", str(plan_path), "--mode", "suite_as_server", "--output", str(output_path)]) == 0

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["results"][0]["outcome"] == "skipped"
    assert report["results"][0]["message"] == (
        "Python wire harness suite_as_server is registered; live endpoint execution is not enabled."
    )


def test_wire_conformance_main_reads_sys_argv_when_argv_is_omitted(tmp_path, monkeypatch) -> None:
    output_path = tmp_path / "target.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nnrp-wire-conformance",
            "manifest",
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
        ],
    )

    assert main() == 0
    assert json.loads(output_path.read_text(encoding="utf-8"))["target_name"] == "nnrp-py-local"


def test_wire_conformance_manifest_cli_uses_invocation_program_name(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["nnrp-wire-conformance", "manifest"])

    with pytest.raises(SystemExit):
        main()

    stderr = capsys.readouterr().err
    assert "nnrp-wire-conformance" in stderr
    assert "nnrp-wire-target-manifest" not in stderr


def test_run_wire_harness_plan_rejects_missing_mode_scenarios(tmp_path) -> None:
    plan_path = tmp_path / "wire-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "suite_version": "0.1.0",
                "target_name": "nnrp-py-local",
                "scenarios": [{"id": "wire.proxy", "mode": "suite_as_proxy"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="contains no scenarios for mode"):
        run_wire_harness_plan(plan_path, tmp_path / "wire-results.json", mode="suite_as_client")


@pytest.mark.parametrize(
    ("plan_text", "match"),
    [
        ("{", "invalid JSON"),
        ("[]", "must be a JSON object"),
        (
            json.dumps(
                {
                    "suite_version": "0.1.0",
                    "target_name": "nnrp-py-local",
                    "scenarios": [],
                }
            ),
            "wire execution plan must contain scenarios",
        ),
        (
            json.dumps(
                {
                    "suite_version": "0.1.0",
                    "target_name": "nnrp-py-local",
                    "scenarios": [7],
                }
            ),
            "wire execution plan scenarios must be objects",
        ),
    ],
)
def test_run_wire_harness_plan_rejects_invalid_plan_file(tmp_path, plan_text: str, match: str) -> None:
    plan_path = tmp_path / "wire-plan.json"
    plan_path.write_text(plan_text, encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        run_wire_harness_plan(plan_path, tmp_path / "wire-results.json", mode="suite_as_client")


def test_run_wire_harness_plan_rejects_missing_plan_file(tmp_path) -> None:
    with pytest.raises(ValueError, match="wire execution plan was not found"):
        run_wire_harness_plan(tmp_path / "missing.json", tmp_path / "wire-results.json", mode="suite_as_client")


def test_validate_wire_case_results_against_plan_accepts_matching_result() -> None:
    plan = {
        "scenarios": [
            {
                "id": "wire.control.cancel-abort.client",
                "expect": {"terminal": "cancelled", "frames": ["TRACE_CONTEXT", "RESULT_DROP_REASON"]},
            }
        ]
    }
    report = build_wire_case_results_report(
        results=[
            WireCaseResult(
                id="wire.control.cancel-abort.client",
                outcome="passed",
                terminal="cancelled",
                observed_frames=[
                    WireObservedFrame("received", "TRACE_CONTEXT"),
                    WireObservedFrame("received", "RESULT_DROP_REASON"),
                ],
            )
        ]
    )

    validate_wire_case_results_against_plan(plan, report)


def test_validate_wire_case_results_against_plan_rejects_missing_expected_frame() -> None:
    plan = {
        "scenarios": [
            {
                "id": "wire.control.cancel-abort.client",
                "expect": {"terminal": "cancelled", "frames": ["TRACE_CONTEXT", "RESULT_DROP_REASON"]},
            }
        ]
    }
    report = build_wire_case_results_report(
        results=[
            WireCaseResult(
                id="wire.control.cancel-abort.client",
                outcome="passed",
                terminal="cancelled",
                observed_frames=[WireObservedFrame("received", "TRACE_CONTEXT")],
            )
        ]
    )

    with pytest.raises(ValueError, match="missing expected frame"):
        validate_wire_case_results_against_plan(plan, report)


def test_validate_wire_case_results_against_plan_rejects_missing_scenario_result() -> None:
    plan = {
        "scenarios": [
            {"id": "wire.control.cancel-abort.client", "expect": {"terminal": "cancelled"}},
            {"id": "wire.control.progress-backpressure.server", "expect": {"terminal": "success"}},
        ]
    }
    report = build_wire_case_results_report(
        results=[
            WireCaseResult(
                id="wire.control.cancel-abort.client",
                outcome="skipped",
                terminal="error",
                message="transport not available",
            )
        ]
    )

    with pytest.raises(ValueError, match="missing scenario ids"):
        validate_wire_case_results_against_plan(plan, report)


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (
            WireCaseResult(
                id="",
                outcome="passed",
                terminal="success",
                observed_frames=[WireObservedFrame("sent", "REQUEST")],
            ),
            "id must be non-empty",
        ),
        (
            WireCaseResult(id="case", outcome="unknown", terminal="success"),
            "unsupported wire result outcome",
        ),
        (
            WireCaseResult(id="case", outcome="passed", terminal="unknown"),
            "unsupported wire result terminal",
        ),
        (
            WireCaseResult(id="case", outcome="passed", terminal="success"),
            "passed wire result must include observed frames",
        ),
        (
            WireCaseResult(id="case", outcome="skipped", terminal="error"),
            "skipped wire result must include a message",
        ),
        (
            WireCaseResult(
                id="case",
                outcome="passed",
                terminal="success",
                observed_frames=[WireObservedFrame("unknown", "REQUEST")],
            ),
            "unsupported wire frame direction",
        ),
        (
            WireCaseResult(
                id="case",
                outcome="passed",
                terminal="success",
                observed_frames=[WireObservedFrame("sent", "")],
            ),
            "observed frame name must be non-empty",
        ),
        (
            WireCaseResult(
                id="case",
                outcome="passed",
                terminal="success",
                observed_frames=[WireObservedFrame("sent", "REQUEST", timestamp_us=-1)],
            ),
            "timestamp_us must be non-negative",
        ),
    ],
)
def test_build_wire_case_results_report_rejects_invalid_results(result: WireCaseResult, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build_wire_case_results_report(results=[result])


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"target_name": "", "results": [WireCaseResult("case", "skipped", "error", message="skip")]},
            "target_name must be non-empty",
        ),
        (
            {"suite_version": "", "results": [WireCaseResult("case", "skipped", "error", message="skip")]},
            "suite_version must be non-empty",
        ),
        (
            {"results": []},
            "results must not be empty",
        ),
        (
            {
                "results": [
                    WireCaseResult("case", "skipped", "error", message="skip"),
                    WireCaseResult("case", "skipped", "error", message="skip again"),
                ]
            },
            "duplicate wire result id",
        ),
        (
            {
                "results": [
                    WireCaseResult(
                        "case",
                        "failed",
                        "error",
                        message="",
                    )
                ]
            },
            "wire result message must be non-empty",
        ),
    ],
)
def test_build_wire_case_results_report_rejects_invalid_report_inputs(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_wire_case_results_report(**kwargs)


@pytest.mark.parametrize(
    ("plan", "message", "match"),
    [
        (
            {"target_name": "target", "suite_version": "0.1.0", "scenarios": [{"id": "case"}]},
            "",
            "skip message must be non-empty",
        ),
        (
            {"target_name": "target", "suite_version": "0.1.0", "scenarios": []},
            "skip",
            "wire execution plan must contain scenarios",
        ),
        (
            {"target_name": "target", "suite_version": "0.1.0", "scenarios": ["case"]},
            "skip",
            "wire execution plan scenarios must be objects",
        ),
        (
            {"target_name": "target", "suite_version": "0.1.0", "scenarios": [{"id": ""}]},
            "skip",
            "wire execution plan scenario id must be non-empty",
        ),
        (
            {"target_name": "", "suite_version": "0.1.0", "scenarios": [{"id": "case"}]},
            "skip",
            "target_name must be non-empty",
        ),
    ],
)
def test_build_wire_skipped_results_from_plan_rejects_invalid_plans(
    plan: dict[str, object],
    message: str,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        build_wire_skipped_results_from_plan(plan, message=message)


@pytest.mark.parametrize(
    ("report", "match"),
    [
        (
            {"results": []},
            "wire result report must contain results",
        ),
        (
            {"results": ["case"]},
            "wire result entries must be objects",
        ),
        (
            {"results": [{"id": "unexpected", "outcome": "skipped", "terminal": "error"}]},
            "unexpected scenario id",
        ),
        (
            {
                "results": [
                    {"id": "case", "outcome": "skipped", "terminal": "error"},
                    {"id": "case", "outcome": "skipped", "terminal": "error"},
                ]
            },
            "duplicate scenario id",
        ),
        (
            {"results": [{"id": "case", "outcome": "unknown", "terminal": "error"}]},
            "unsupported wire result outcome",
        ),
    ],
)
def test_validate_wire_case_results_against_plan_rejects_invalid_reports(
    report: dict[str, object],
    match: str,
) -> None:
    plan = {"scenarios": [{"id": "case", "expect": {"terminal": "success"}}]}

    with pytest.raises(ValueError, match=match):
        validate_wire_case_results_against_plan(plan, report)


@pytest.mark.parametrize(
    ("plan", "report", "match"),
    [
        (
            {"scenarios": []},
            {"results": [{"id": "case", "outcome": "skipped", "terminal": "error"}]},
            "wire execution plan must contain scenarios",
        ),
        (
            {"scenarios": ["case"]},
            {"results": [{"id": "case", "outcome": "skipped", "terminal": "error"}]},
            "wire execution plan scenarios must be objects",
        ),
        (
            {"scenarios": [{"id": "case"}, {"id": "case"}]},
            {"results": [{"id": "case", "outcome": "skipped", "terminal": "error"}]},
            "duplicate scenario id",
        ),
        (
            {"scenarios": [{"id": "case", "expect": "success"}]},
            {
                "results": [
                    {
                        "id": "case",
                        "outcome": "passed",
                        "terminal": "success",
                        "observed_frames": [{"direction": "received", "frame": "TRACE_CONTEXT"}],
                    }
                ]
            },
            "expect must be an object",
        ),
        (
            {"scenarios": [{"id": "case", "expect": {"terminal": "cancelled"}}]},
            {
                "results": [
                    {
                        "id": "case",
                        "outcome": "passed",
                        "terminal": "success",
                        "observed_frames": [{"direction": "received", "frame": "TRACE_CONTEXT"}],
                    }
                ]
            },
            "terminal mismatch",
        ),
        (
            {"scenarios": [{"id": "case", "expect": {"terminal": "success", "frames": "TRACE_CONTEXT"}}]},
            {
                "results": [
                    {
                        "id": "case",
                        "outcome": "passed",
                        "terminal": "success",
                        "observed_frames": [{"direction": "received", "frame": "TRACE_CONTEXT"}],
                    }
                ]
            },
            "expect.frames must be a list",
        ),
        (
            {"scenarios": [{"id": "case", "expect": {"terminal": "success", "frames": ["TRACE_CONTEXT"]}}]},
            {"results": [{"id": "case", "outcome": "passed", "terminal": "success", "observed_frames": "frame"}]},
            "observed_frames must be a list",
        ),
    ],
)
def test_validate_wire_case_results_against_plan_rejects_invalid_plan_or_passed_results(
    plan: dict[str, object],
    report: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        validate_wire_case_results_against_plan(plan, report)


@pytest.mark.parametrize(
    ("report", "match"),
    [
        (
            {"results": "case"},
            "wire result report must contain results",
        ),
        (
            {"results": ["case"]},
            "wire result entries must be objects",
        ),
        (
            {"results": [{"id": "case", "outcome": "skipped", "terminal": "error", "evidence_paths": "path"}]},
            "evidence_paths must be a list",
        ),
        (
            {"results": [{"id": "case", "outcome": "skipped", "terminal": "error", "evidence_paths": [""]}]},
            "evidence path must be non-empty",
        ),
    ],
)
def test_write_wire_evidence_files_rejects_invalid_reports(report: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        write_wire_evidence_files(report)
