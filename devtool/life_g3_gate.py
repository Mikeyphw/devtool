from __future__ import annotations

"""LIFE-G3 final fixed-point proof.

This is gate tooling, not a second lifecycle engine. It verifies the final
source/feature dispositions, composes the retained LIFE authorities, and runs a
fresh-process lifecycle chain using the real repository-pack and cleanup
primitives without mutating the Devtool repository under qualification.
"""

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from .cleanup_lifecycle import (
    GeneratedOutputDescriptor,
    apply_cleanup_plan,
    build_cleanup_ownership_view,
    compile_cleanup_plan,
)
from .lifecycle_convergence import (
    cleanup_projection,
    delivery_projection,
    federation_pack_selection,
    remote_lifecycle_envelope,
    repository_pack_projection,
    signing_projection,
    transaction_projection,
)
from .repository_pack import (
    RepositoryPackCompression,
    RepositoryPackMode,
    RepositoryPackRequest,
    create_repository_pack,
    plan_repository_pack,
)

ROOT = Path(__file__).resolve().parents[1]


class LifeG3Error(RuntimeError):
    pass


def _require(value: bool, message: str) -> None:
    if not value:
        raise LifeG3Error(message)


def _require_cleanup_chain_truth(plan: Any, receipt: Any, generated: Path, resource_id: str) -> None:
    """Qualify the *target* cleanup without pretending retained evidence was deleted.

    A repository-pack publication registers a referenced RUN evidence receipt.
    The canonical cleanup planner correctly protects that receipt, so the
    aggregate result is ``partial`` even when its disposable generated output
    was successfully deleted.  Verify individual actions and postconditions;
    never normalize or weaken the canonical cleanup receipt.
    """
    selected = tuple(plan.selected)
    protected = tuple(plan.protected)
    expected_ids = {item.candidate_id for item in plan.candidates}
    by_id = {item.action_id: item for item in receipt.results}
    _require(not plan.opaque_actions, "LIFE-G3 fixture unexpectedly required opaque provider cleanup")
    _require(
        receipt.plan_id == plan.plan_id
        and receipt.repository_id == plan.repository_id
        and len(by_id) == len(receipt.results)
        and set(by_id) == expected_ids,
        "LIFE-G3 cleanup receipt identity or action inventory mismatch",
    )
    _require(
        sum(item.resource_id == resource_id and Path(item.path) == generated for item in selected) == 1,
        "LIFE-G3 disposable output was not uniquely selected for cleanup",
    )
    for item in selected:
        result = by_id[item.candidate_id]
        _require(
            result.resource_id == item.resource_id
            and result.status == "applied"
            and result.postcondition == "absent"
            and not Path(item.path).exists(),
            "LIFE-G3 managed-output cleanup did not remove an authorized selected resource",
        )
    for item in protected:
        result = by_id[item.candidate_id]
        _require(
            result.resource_id == item.resource_id
            and result.status == "protected"
            and (not item.source_snapshot.get("exists") or Path(item.path).exists()),
            "LIFE-G3 cleanup failed to preserve referenced evidence",
        )
    _require(
        receipt.status == ("partial" if protected else "applied") and not generated.exists(),
        "LIFE-G3 aggregate cleanup receipt contradicted individual postconditions",
    )


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _git_status() -> str:
    cp = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return cp.stdout if cp.returncode == 0 else ""


def _is_termux() -> bool:
    prefix = str(os.environ.get("PREFIX") or "")
    return prefix.startswith("/data/data/com.termux/") or "/com.termux/" in sys.executable


def _verify_evidence(rows: list[dict[str, Any]], *, id_field: str) -> tuple[str, int]:
    items: list[str] = []
    for row in rows:
        refs = [str(item) for item in (row.get("evidence") or [])]
        bindings = dict(row.get("evidence_sha256") or {})
        _require(refs, f"LIFE-G3 evidence missing for {row.get(id_field)}")
        _require(set(refs) == set(bindings), f"LIFE-G3 evidence binding incomplete for {row.get(id_field)}")
        for rel in refs:
            path = ROOT / rel
            _require(path.is_file(), f"LIFE-G3 evidence path missing: {rel}")
            digest = _hash(path)
            _require(digest == bindings[rel], f"LIFE-G3 evidence drift: {rel}")
            items.append(f"{row[id_field]}\0{rel}\0{digest}")
    root = sha256(("\n".join(sorted(items)) + "\n").encode()).hexdigest()
    return root, len(items)


def _run_fresh_lifecycle_chain() -> dict[str, Any]:
    if _is_termux():
        _require(
            os.environ.get("DEVTOOL_LIFE_G3_ALLOW_TEST_HOST") != "1",
            "LIFE-G3 construction-host bypass must not be enabled on native Termux",
        )
    else:
        _require(
            os.environ.get("DEVTOOL_LIFE_G3_ALLOW_TEST_HOST") == "1",
            "LIFE-G3 authoritative proof requires native Termux",
        )

    with tempfile.TemporaryDirectory(prefix="devtool-life-g3-") as td:
        root = Path(td)
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "life-g3@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "LIFE G3"], check=True)
        (repo / "tracked.txt").write_text("qualified lifecycle\n", encoding="utf-8")
        (repo / "unicode name-ç.txt").write_text("unicode\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)

        pack_path = root / "life-g3-pack.tar.gz"
        request = RepositoryPackRequest(
            repositories=(str(repo),),
            output=str(pack_path),
            mode=RepositoryPackMode.WORKTREE,
            compression=RepositoryPackCompression.TAR_GZ,
            pin=True,
            retention="final-seal",
        )
        pack_plan = plan_repository_pack(request)
        pack_receipt = create_repository_pack(pack_plan)
        _require(pack_receipt.verified and pack_receipt.published and pack_path.is_file(), "LIFE-G3 pack publication failed")

        pack_projection = repository_pack_projection(pack_receipt.as_dict())
        sign_projection = signing_projection({
            "identity_id": "life-g3-signing",
            "provider": "private-file",
            "key_reference": "signing:key/life-g3",
            "certificate_sha256": sha256(b"life-g3-cert").hexdigest(),
            "secret_provider": "private-file-ref",
            "lifecycle": "durable",
        })
        delivery = delivery_projection({
            "artifact": {
                "artifact_id": "life-g3-artifact",
                "assurance": "strict",
                "identities": {"semantic_sha256": pack_receipt.sha256},
            },
            "qualification": {"qualified": True, "receipt_id": "life-g3-qualification"},
            "executable": True,
        })
        transaction = transaction_projection({
            "transaction_id": "life-g3-transaction",
            "mutation_started": True,
            "changed_paths": ["generated-output/payload"],
            "checkpoints": ["G0", "validated"],
            "resume_target_identity": "life-g3-target",
        })

        generated = repo / "generated-output"
        generated.mkdir()
        (generated / "payload").write_text("rebuildable\n", encoding="utf-8")
        descriptor = GeneratedOutputDescriptor.create(
            generated,
            owner="life-g3",
            provider="fixture",
            category="generated",
            producer_id="life-g3-producer",
            lifecycle="disposable",
            rebuild_cost="low",
        )
        ownership = build_cleanup_ownership_view(repo, [descriptor], policy_generation="life-g3")
        cleanup_plan = compile_cleanup_plan(ownership)
        _require(bool(cleanup_plan.selected), "LIFE-G3 cleanup plan did not select managed output")
        cleanup_receipt = apply_cleanup_plan(cleanup_plan, ownership=ownership, confirm=True)
        _require_cleanup_chain_truth(cleanup_plan, cleanup_receipt, generated, descriptor.resource_id)
        cleanup = cleanup_projection(cleanup_plan.as_dict())

        remote = remote_lifecycle_envelope(
            repository_identity=pack_plan.repositories[0].identity.fingerprint,
            operation="repository-pack-publish",
            projection=pack_projection,
            signing_identity_id=sign_projection.identity,
            secret_provider_ref="secret-provider:life-g3",
        )
        federation = federation_pack_selection((
            {"repository_identity": pack_plan.repositories[0].identity.fingerprint, "root": str(repo)},
            {"repository_identity": "life-g3-peer", "root": "/remote/peer", "selection": "tracked"},
        ))
        _require(remote["projection"]["status"] == "published", "LIFE-G3 remote projection lost publication truth")
        _require(federation["identity_flattening"] is False, "LIFE-G3 federation flattened repository identity")
        _require(pack_path.exists(), "LIFE-G3 cleanup deleted published repository pack")

        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "from devtool.lifecycle_convergence import dogfood_matrix; print(len(dogfood_matrix()))",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        _require(child.returncode == 0 and child.stdout.strip().isdigit(), "LIFE-G3 fresh-process lifecycle import failed")

        return {
            "pack_plan_id": pack_plan.plan_id,
            "pack_receipt_id": pack_receipt.receipt_id,
            "pack_sha256": pack_receipt.sha256,
            "signing_identity_id": sign_projection.identity,
            "delivery_status": delivery.status,
            "transaction_status": transaction.status,
            "cleanup_receipt_id": cleanup_receipt.receipt_id,
            "cleanup_status": cleanup_receipt.status,
            "remote_envelope_id": remote["envelope_id"],
            "federation_selection_id": federation["selection_id"],
            "fresh_process_dogfood_rows": int(child.stdout.strip()),
        }


def run_gate() -> dict[str, Any]:
    """Retired historical attestation gate; never infer release status."""
    raise RuntimeError("LIFE-G3 historical seal retired; use G3R v3 current-source evidence and native receipts")


def main() -> int:
    try:
        print(json.dumps(run_gate(), sort_keys=True))
        return 0
    except Exception as exc:
        print(f"LIFE-G3 failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
