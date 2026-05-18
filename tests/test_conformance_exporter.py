import pytest

from nnrp.tools.conformance import build_conformance_vector_manifest


def test_build_conformance_vector_manifest_preview2() -> None:
    manifest = build_conformance_vector_manifest("nnrp-1-preview2")

    assert manifest["protocol_version"] == "nnrp-1-preview2"
    assert manifest["generator"] == "nnrp-py"
    assert len(manifest["vectors"]) == 12
    assert manifest["vectors"][0]["name"] == "current.header.frame_submit_ack_required_keyframe"


def test_build_conformance_vector_manifest_rejects_unknown_protocol() -> None:
    with pytest.raises(ValueError, match="unsupported protocol version"):
        build_conformance_vector_manifest("nnrp-1-preview3")