from __future__ import annotations

import json
from pathlib import Path

from devtool.cli import build_parser
from devtool.oci_final_seal import (
    OCI10_DOC_PATH,
    oci10_final_seal_report,
    render_oci10_final_seal_text,
)

ROOT = Path(__file__).resolve().parents[1]


def test_oci10_final_seal_passes_on_repository_tree() -> None:
    payload = oci10_final_seal_report(ROOT)
    assert payload["ok"] is True, payload
    assert payload["failure_count"] == 0
    assert payload["check_count"] >= 14
    assert {check["id"] for check in payload["checks"]} >= {
        "oci10-cli-help-golden",
        "oci10-build-report-golden",
        "oci10-operator-docs",
        "oci10-secret-redaction-invariants",
        "oci10-final-seal",
    }
    json.dumps(payload)


def test_oci10_parser_surface_is_local_and_strict_capable() -> None:
    args = build_parser().parse_args(["oci", "final-seal", "--format", "json", "--strict"])
    assert args.command == "oci"
    assert args.oci_command == "final-seal"
    assert args.format == "json"
    assert args.oci_strict is True
    alias = build_parser().parse_args(["oci", "seal"])
    assert alias.oci_command == "seal"


def test_oci10_final_seal_text_renderer_is_stable() -> None:
    payload = oci10_final_seal_report(ROOT)
    text = render_oci10_final_seal_text(payload)
    assert text.startswith("OCI-10 final seal: PASS")
    assert "oci10-cli-help-golden" in text
    assert "oci10-build-report-golden" in text
    assert "oci10-secret-redaction-invariants" in text


def test_oci10_operator_docs_reference_final_seal_surface() -> None:
    doc = (ROOT / OCI10_DOC_PATH).read_text(encoding="utf-8")
    assert "Final seal and compatibility" in doc
    assert "devtool oci final-seal" in doc
    assert "--strict" in doc
