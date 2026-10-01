from __future__ import annotations

import json
import os
from importlib import metadata
from pathlib import Path
import tomllib
from urllib.parse import unquote, urlparse

from devtool.artifacts import artifact_finalize_phases

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_dist_r01_records_the_historical_p100_deviation_without_rewriting_it():
    audit = _load("DIST_R01_HISTORICAL_ARTIFACT_AUDIT.json")
    assert audit["promise"] == "DIST-P100"
    assert audit["historical_deviation_confirmed"] is True
    rows = {row["roadmap_id"]: row for row in audit["rows"]}
    assert set(rows) == {"DIST-X01", "DIST-X02"}
    expected_hashes = {
        "DIST-X01": (
            "02e1575119189258844be7a78ad9f9a1e9271003ca059c0f82b0ff325294104e",
            "e05d34c2f5fd5efcddb9d3cd0ce65e7c7df7b68efd43109f86995c6422a5a05e",
        ),
        "DIST-X02": (
            "3e84ca0fca044f4631d6506196c2daaf5c08e177819e022375e765cec2e873aa",
            "1e75c5aaed024e0044780bb6c6b13c9e128fca9b8de2ad09b35245b6e045b911",
        ),
    }
    for roadmap_id, row in rows.items():
        assert row["required_validation"] is True
        assert row["finalize_present"] is False
        assert row["before_commit_present"] is False
        assert (row["artifact_sha256"], row["manifest_sha256"]) == expected_hashes[roadmap_id]


def test_dist_r01_p100_ledger_requires_remediated_not_originally_satisfied_status():
    ledger = _load("DIST_R01_PROMISE_LEDGER.json")
    assert ledger["promise_id"] == "DIST-P100"
    assert ledger["original_promise"] == "Every DIST implementation overlay retains the before-commit editable-install refresh hook so subsequent overlays and gates exercise the transaction candidate Devtool rather than a stale installed CLI."
    assert ledger["historical_status"].startswith("VIOLATED_")
    assert ledger["remediation_status"] == "CURRENT_CAMPAIGN_TWO_PHASE_INVARIANT_RESTORED"
    contract = ledger["remediation_contract"]
    assert contract["finalize_phase"] == "before_commit"
    assert contract["candidate_runtime_environment"] == "DEVTOOL_TRANSACTION_REPO_ROOT"
    assert contract["primary_root_environment"] == "DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT"
    assert contract["candidate_install"] == "python -m pip --disable-pip-version-check install --no-index --no-build-isolation --no-deps -e ."
    assert "direct_url.json" in contract["candidate_binding_proof"]
    assert "DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT" in contract["promotion_rebind"]
    assert "--no-build-isolation --no-deps -e" in contract["editable_install"]
    assert "REMEDIATED_AFTER_HISTORICAL_DEVIATION" in ledger["closure_rule"]


def test_devtool_artifact_lifecycle_supports_the_required_before_commit_phase():
    manifest = {"finalize": {"phases": ["before_commit"], "script": {"path": ".devtool-artifact/finalize.sh"}}}
    assert artifact_finalize_phases(manifest) == ["before_commit"]
    cli = (ROOT / "devtool" / "cli.py").read_text(encoding="utf-8")
    assert '"DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT"' in cli
    assert 'artifact-finalize-before_commit' in cli


def test_dist_r01_candidate_editable_binding_points_at_transaction_root():
    # Devtool deliberately sanitizes transaction-control environment variables
    # from child test processes.  During artifact validation ROOT itself is the
    # transaction execution checkout, so installed distribution metadata must
    # bind to that exact checkout rather than to a stale primary/install path.
    expected = ROOT.resolve()
    raw_root = os.environ.get("DEVTOOL_TRANSACTION_REPO_ROOT", "").strip()
    if raw_root:
        assert Path(raw_root).expanduser().resolve() == expected
    dist = metadata.distribution("devtool")
    raw = dist.read_text("direct_url.json") or ""
    payload = json.loads(raw) if raw else {}
    info = payload.get("dir_info") if isinstance(payload, dict) else {}
    assert isinstance(info, dict) and info.get("editable") is True
    parsed = urlparse(str(payload.get("url") or ""))
    assert parsed.scheme == "file"
    actual = Path(unquote(parsed.path)).resolve()
    assert actual == expected, (actual, expected)


def test_dist_r01_wrapper_workflow_is_explicit_and_pre_g3():
    cfg = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    command = cfg["wrapper"]["commands"]["dist-r01"]
    assert command["workflow"] == "roadmap-dist-r01"
    assert command["target"] == "devtool"
    jobs = cfg["targets"]["devtool"]["jobs"]
    install = jobs["dist-r01-candidate-editable-install"]
    assert install["runner"] == "command"
    assert install["command"] == [
        "python", "-m", "pip", "--disable-pip-version-check", "install", "--no-index",
        "--no-build-isolation", "--no-deps", "-e", "."
    ]
    assert install["metadata"]["transaction_root_aware"] is True
    assert install["metadata"]["roadmap_id"] == "DIST-R01"
    workflow = cfg["targets"]["devtool"]["workflows"]["roadmap-dist-r01"]
    ids = [row["id"] for row in workflow]
    assert ids == [
        "syntax", "candidate-install", "candidate-binding", "remediation-contract",
        "artifact-finalize", "transaction-self-refresh", "exo-hook-convergence",
    ]
    assert workflow[2]["depends_on"] == ["candidate-install"]
    assert workflow[-1]["depends_on"] == ["transaction-self-refresh"]


def test_dist_r01_exports_unrelated_overlay_check_defect_to_owning_boundary():
    issue = _load("DIST_R01_EXPORTED_UPSTREAM_ISSUES.json")
    assert issue["issue_id"] == "DEVTOOL-OVERLAY-CHECK-SHA256-FILE-NAMEERROR"
    assert issue["owner"] == "CORE/ARTIFACT"
    assert issue["status"] == "EXPORTED_PENDING_OWNER"
    assert issue["dist_g3_blocking"] is False
    assert issue["source_path"] == "devtool/overlay/conflicts.py"
    source = ROOT / issue["source_path"]
    assert source.is_file()
    import hashlib
    assert hashlib.sha256(source.read_bytes()).hexdigest() == issue["source_sha256"]
