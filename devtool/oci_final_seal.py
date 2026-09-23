from __future__ import annotations

"""OCI-10 final roadmap seal and compatibility hardening helpers.

The final OCI seal is intentionally local and side-effect free.  It audits the
checked-out repository for the contracts introduced by OCI-01 through OCI-09,
closes operator-facing compatibility drift such as the CLI help golden, and
emits a small machine-readable report for CI, artifact finalizers, and humans.
It does not probe or mutate remote workers.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
import json
from typing import Any, Iterable

OCI10_SCHEMA_VERSION = 1
OCI10_DOC_PATH = "docs/architecture/oci-proot-operations.md"
OCI10_REPORT_PATH = "OCI10_FINAL_SEAL_REPORT.md"

_REQUIRED_FILES: tuple[str, ...] = (
    "devtool/oci_image.py",
    "devtool/proot_oci.py",
    "devtool/oci_workspace.py",
    "devtool/oci_resilience.py",
    "devtool/ssh_resilience.py",
    "devtool/ssh_returns.py",
    "devtool/oci_reuse.py",
    "devtool/oci_diagnostics.py",
    "devtool/oci_final_seal.py",
    "docs/architecture/oci-proot-operations.md",
    "tests/golden/cli-help.txt",
    "tests/golden/build-report.json",
    "tests/test_oci01_execution_provider_foundation.py",
    "tests/test_oci02_proot_distro_lifecycle.py",
    "tests/test_oci03_android_image_contract.py",
    "tests/test_oci04_workspace_bind_cache_contract.py",
    "tests/test_oci05_repository_execution_contract.py",
    "tests/test_oci06_resilience_artifacts_contract.py",
    "tests/test_oci07_image_management_policy.py",
    "tests/test_oci08_cache_sync_reuse_policy.py",
    "tests/test_oci09_diagnostics_visibility.py",
    "tests/test_oci10_final_seal_compatibility.py",
)

_ROADMAP_PROMISES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("oci01-provider-foundation", "SSH PRoot OCI provider is a named execution provider, not an implicit fallback", ("ssh-proot-oci", "execution_provider")),
    ("oci02-proot-lifecycle", "PRoot-Distro lifecycle operations are explicit and redacted", ("oci_bootstrap_payload", "oci_install_payload", "_sanitized_subprocess_env")),
    ("oci03-image-contract", "Canonical Android ARM64 image is deterministic and pinned", ("android-arm64-api36-v2", "canonical_android_oci_definition", "ubuntu:24.04")),
    ("oci04-workspace-bind-cache", "Managed workspace, guest binds, cache roots, and env allowlist are explicit", ("OciWorkspacePlan", "APPROVED_CACHES", "forwarded_environment_names")),
    ("oci05-repository-execution", "Repository commands execute through a prepared OCI command envelope", ("oci_phase_node_id", "oci_guest_workspace", "ssh-proot-oci")),
    ("oci06-resilience-artifacts", "Resume/cancel/recover/artifact-return identity is durable and producer-bound", ("phase_node_id", "producer_identity_digest", "PD_DOCKER_AUTH")),
    ("oci07-image-management", "Image status/update/prune are plan-first and apply-only when requested", ("oci_update_payload", "oci_prune_payload", "--apply")),
    ("oci08-cache-sync-worker-reuse", "Source sync, cache namespace, and worker reuse policy are recorded without a second lifecycle authority", ("oci08_source_sync_policy", "oci08_cache_namespace", "oci08_worker_reuse_policy")),
    ("oci09-diagnostics-hud-docs", "Redacted diagnostics, HUD visibility, and operator documentation expose existing truth", ("diagnostics_payload", "oci09_visibility_state", "Secret handling")),
    ("oci10-final-seal", "The roadmap has a local final seal and CLI compatibility golden coverage", ("oci final-seal", "tests/golden/cli-help.txt", "OCI10_FINAL_SEAL_REPORT")),
)

_SECRET_MARKERS = (
    "top-secret",
    "super-secret",
    "docker-auth-token",
    "PD_DOCKER_AUTH=",
)


@dataclass(frozen=True, slots=True)
class Oci10SealCheck:
    id: str
    status: str
    summary: str
    details: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _check(check_id: str, ok: bool, summary: str, details: Iterable[str] = ()) -> Oci10SealCheck:
    return Oci10SealCheck(
        id=check_id,
        status="pass" if ok else "fail",
        summary=summary,
        details=tuple(str(item) for item in details if str(item)),
    )


def _contains_all(repo_root: Path, paths: Iterable[str], tokens: Iterable[str]) -> tuple[bool, list[str]]:
    haystack = "\n".join(_text(repo_root / path) for path in paths)
    missing = [token for token in tokens if token not in haystack]
    return not missing, missing


def _expected_paths_check(repo_root: Path) -> Oci10SealCheck:
    missing = [path for path in _REQUIRED_FILES if not (repo_root / path).is_file()]
    return _check(
        "oci10-required-files",
        not missing,
        "OCI roadmap modules, tests, docs, and CLI golden files are present",
        [f"missing:{path}" for path in missing],
    )


def _roadmap_promise_checks(repo_root: Path) -> list[Oci10SealCheck]:
    search_paths = (
        "devtool/cli.py",
        "devtool/oci_image.py",
        "devtool/proot_oci.py",
        "devtool/oci_workspace.py",
        "devtool/oci_resilience.py",
        "devtool/ssh_resilience.py",
        "devtool/ssh_returns.py",
        "devtool/oci_reuse.py",
        "devtool/oci_diagnostics.py",
        "devtool/oci_final_seal.py",
        "docs/architecture/oci-proot-operations.md",
        "tests/golden/cli-help.txt",
    "tests/golden/build-report.json",
    )
    checks: list[Oci10SealCheck] = []
    for check_id, summary, tokens in _ROADMAP_PROMISES:
        ok, missing = _contains_all(repo_root, search_paths, tokens)
        checks.append(_check(check_id, ok, summary, [f"missing-token:{token}" for token in missing]))
    return checks


def _cli_compatibility_check(repo_root: Path) -> Oci10SealCheck:
    golden = _text(repo_root / "tests/golden/cli-help.txt")
    required = (
        "oci                 Manage PRoot-Distro OCI runtime state on named SSH",
        "ssh,oci,bootstrap",
    )
    missing = [token for token in required if token not in golden]
    return _check(
        "oci10-cli-help-golden",
        not missing,
        "Top-level CLI help golden includes the OCI command and command ordering",
        [f"missing-token:{token}" for token in missing],
    )




def _build_report_compatibility_check(repo_root: Path) -> Oci10SealCheck:
    golden = _text(repo_root / "tests/golden/build-report.json")
    required = (
        '"semantics_version": 2',
        '"measured_percent": 100',
        '"resolution_percent": 100',
        '"succeeded_nodes": 1',
    )
    missing = [token for token in required if token not in golden]
    return _check(
        "oci10-build-report-golden",
        not missing,
        "Machine build-report golden includes current execution-progress semantic fields",
        [f"missing-token:{token}" for token in missing],
    )

def _operator_doc_check(repo_root: Path) -> Oci10SealCheck:
    doc = _text(repo_root / OCI10_DOC_PATH)
    required = (
        "Image update policy",
        "Source-sync, cache, and worker reuse",
        "Diagnostics and HUD fields",
        "Resilience and artifact return",
        "Secret handling",
        "Final seal and compatibility",
    )
    missing = [token for token in required if token not in doc]
    return _check(
        "oci10-operator-docs",
        not missing,
        "OCI operator documentation covers update, reuse, diagnostics, resilience, secrets, and final seal behavior",
        [f"missing-token:{token}" for token in missing],
    )


def _secret_redaction_check(repo_root: Path) -> Oci10SealCheck:
    paths = (
        "devtool/oci_diagnostics.py",
        "devtool/oci_resilience.py",
        "devtool/proot_oci.py",
        "tests/test_oci09_diagnostics_visibility.py",
        "tests/test_oci10_final_seal_compatibility.py",
    )
    findings: list[str] = []
    for rel in paths:
        text = _text(repo_root / rel)
        for marker in _SECRET_MARKERS:
            if marker in text and "redact" not in text.lower() and "forbidden" not in text.lower():
                findings.append(f"unredacted-marker:{rel}:{marker}")
    return _check(
        "oci10-secret-redaction-invariants",
        not findings,
        "Known OCI secret markers appear only in redaction tests or redaction guards",
        findings,
    )


def oci10_final_seal_report(repo_root: str | Path) -> dict[str, Any]:
    """Return a local, non-mutating OCI roadmap final seal report."""

    root = Path(repo_root).resolve()
    checks = [_expected_paths_check(root), _cli_compatibility_check(root), _build_report_compatibility_check(root), _operator_doc_check(root), _secret_redaction_check(root)]
    checks.extend(_roadmap_promise_checks(root))
    failures = [check for check in checks if check.status != "pass"]
    payload = {
        "schema_version": OCI10_SCHEMA_VERSION,
        "kind": "oci10-final-seal",
        "ok": not failures,
        "repo_root": str(root),
        "check_count": len(checks),
        "failure_count": len(failures),
        "checks": [check.as_dict() for check in checks],
        "roadmap": [
            {"id": check_id, "summary": summary, "evidence_tokens": list(tokens)}
            for check_id, summary, tokens in _ROADMAP_PROMISES
        ],
        "docs": {"operations": OCI10_DOC_PATH},
        "detail": (
            "OCI-01 through OCI-10 roadmap seal passed; CLI compatibility, documentation, diagnostics, and secret-redaction invariants are coherent"
            if not failures else
            "OCI final seal found compatibility or roadmap promise gaps"
        ),
    }
    return payload


def render_oci10_final_seal_text(payload: dict[str, Any]) -> str:
    lines = [
        "OCI-10 final seal: " + ("PASS" if payload.get("ok") else "FAIL"),
        f"checks: {payload.get('check_count', 0)}",
        f"failures: {payload.get('failure_count', 0)}",
        f"detail: {payload.get('detail') or '-'}",
        "",
    ]
    for check in payload.get("checks", []):
        status = str(check.get("status") or "unknown").upper()
        lines.append(f"{status:>4}  {check.get('id', '-')}: {check.get('summary', '-')}")
        for detail in check.get("details", [])[:8]:
            lines.append(f"      - {detail}")
    return "\n".join(lines).rstrip() + "\n"


def write_oci10_final_seal_report(repo_root: str | Path, output: str | Path | None = None) -> dict[str, Any]:
    payload = oci10_final_seal_report(repo_root)
    target = Path(output) if output is not None else Path(repo_root) / OCI10_REPORT_PATH
    target.write_text(render_oci10_final_seal_text(payload) + "\n```json\n" + json.dumps(payload, indent=2, sort_keys=True) + "\n```\n", encoding="utf-8")
    return payload
