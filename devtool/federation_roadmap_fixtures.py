from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import subprocess
import shutil
from pathlib import Path
from unittest.mock import patch

from .config import load_project_config
from .federation_interfaces import build_interface_report
from .federation_plan import build_federated_plan
from .git_ownership import GitFederatedOwnershipError, resolve_federated_git_owner
from .federation_lock import FederationSourceLock, compare_federation_source_locks, write_federation_source_lock
from .federation_git_lineage import finalize_federated_git_lineage, planned_federated_git_lineage
from .artifact_registry import register_paths
from .federation_execution import run_federated_workflow
from .federation_resume import build_resume_plan, load_checkpoint, checkpoint_path
from .git_federation_integration import integrate_federation_checkpoint
from .federation_sync import federation_source_management


def _load(root: Path):
    return load_project_config(root, root / '.devtool.toml', target_name='', allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def interface_fixture() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix='devtool-frx-y06-') as td:
        root=Path(td); parent=root/'parent'; child=root/'child'; parent.mkdir(); child.mkdir()
        (child/'.devtool.toml').write_text('''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\noutputs=["out/app.apk"]\n[targets.app.jobs.check]\nrunner="command"\ncommand=["echo","ok"]\n[targets.app.workflows]\nvalidate=["job:check"]\npackage=["job:check"]\n[wrapper.commands.build]\ndevtool="build"\ntarget="app"\n''')
        (parent/'.devtool.toml').write_text('''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n[federation.interfaces."android-app/v1"]\nrequired_workflows=["validate","package"]\nrequired_wrapper_commands=["build"]\nrequired_artifacts=["apk"]\n[federation.groups.apps]\nrepositories=["child"]\ninterface="android-app/v1"\n''')
        report=build_interface_report(_load(parent), group_name='apps')
        if not report.ok:
            raise SystemExit('FRX-Y06 interface fixture did not conform')
        return report.as_dict()


def public_contract_fixture() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix='devtool-frx-y07-') as td:
        root=Path(td); parent=root/'parent'; child=root/'child'; parent.mkdir(); child.mkdir()
        (child/'.devtool.toml').write_text('''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.lint]\nrunner="command"\ncommand=["echo","lint"]\n[wrapper.commands.build]\ndevtool="build"\ntarget="app"\n''')
        (parent/'.devtool.toml').write_text('''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n[federation.workflows.release]\nsteps=[{id="lint",ref="child::app::job:lint"},{id="build",ref="child::app::wrapper:build",depends_on=["lint"]}]\n''')
        plan=build_federated_plan(_load(parent), 'release')
        direct=[n for n in plan.nodes if not n.synthetic]
        kinds=[str(n.metadata.get('contract_type') or '') for n in direct]
        if kinds != ['job','wrapper']:
            raise SystemExit(f'FRX-Y07 fixture produced unexpected contract types: {kinds}')
        return {'schema_version':1,'workflow':'release','nodes':[n.as_dict() for n in direct]}



def _init_git(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=10)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.invalid"], check=True, timeout=10)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Devtool Fixture"], check=True, timeout=10)
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, timeout=10)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True, timeout=10)


def repository_ownership_fixture() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="devtool-git-frx07-") as td:
        root = Path(td)
        parent = root / "parent"
        child = root / "child"
        nested = child / "nested-unenrolled"
        outside = root / "outside"
        parent.mkdir()
        child.mkdir()
        outside.mkdir()
        (child / ".devtool.toml").write_text('[project]\nrunner="command"\n', encoding="utf-8")
        _init_git(child)
        nested.mkdir()
        _init_git(nested)
        (parent / ".devtool.toml").write_text(
            '[project]\nrunner="command"\n[federation]\nschema=1\n'
            '[federation.repositories.child]\npath="../child"\n',
            encoding="utf-8",
        )
        config = _load(parent)
        evidence = resolve_federated_git_owner(config, child / "README.md")
        if evidence is None or evidence.repository != "child":
            raise SystemExit("GIT-FRX-07 fixture did not resolve enrolled child ownership")
        nested_rejected = False
        try:
            resolve_federated_git_owner(config, nested / "README.md")
        except GitFederatedOwnershipError:
            nested_rejected = True
        if not nested_rejected:
            raise SystemExit("GIT-FRX-07 fixture inherited an unenrolled nested Git checkout")
        outside_rejected = False
        try:
            resolve_federated_git_owner(config, outside / "file.txt")
        except GitFederatedOwnershipError:
            outside_rejected = True
        if not outside_rejected:
            raise SystemExit("GIT-FRX-07 fixture accepted an outside path")
        return {
            "schema_version": 1,
            "owner": evidence.as_dict(),
            "nested_unenrolled_rejected": nested_rejected,
            "outside_rejected": outside_rejected,
        }


def source_lock_fixture(*, mutate: bool = False) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="devtool-git-frx08-") as td:
        root = Path(td)
        parent = root / "parent"
        child = root / "child"
        parent.mkdir(); child.mkdir()
        (child / ".devtool.toml").write_text(
            '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n'
            '[targets.app.jobs.check]\nrunner="command"\ncommand=["echo","ok"]\n[targets.app.workflows]\nvalidate=["job:check"]\n',
            encoding="utf-8",
        )
        (child / "source.txt").write_text("base\n", encoding="utf-8")
        _init_git(child)
        (parent / ".devtool.toml").write_text(
            '[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n'
            '[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n'
            '[federation.workflows.release]\nsteps=[{id="child",ref="child::app::workflow:validate"}]\n',
            encoding="utf-8",
        )
        config = _load(parent)
        first = FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock)
        if not mutate:
            entry = first.repositories[0]
            if not entry.start_commit or not entry.index_tree or not first.lock_digest:
                raise SystemExit("GIT-FRX-08 source lock fixture did not freeze Git state")
            return first.as_dict()
        (child / "source.txt").write_text("drift\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(child), "add", "."], check=True, timeout=10)
        subprocess.run(["git", "-C", str(child), "commit", "-qm", "drift"], check=True, timeout=10)
        second = FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock)
        comparison = compare_federation_source_locks(first, second, policy="fail")
        if comparison.get("matched"):
            raise SystemExit("GIT-FRX-08 drift fixture failed to detect source mutation")
        return comparison



def git_lineage_fixture() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="devtool-git-frx09-") as td:
        root = Path(td)
        parent = root / "parent"
        child = root / "child"
        parent.mkdir(); child.mkdir()
        (child / ".devtool.toml").write_text(
            '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n'
            '[targets.app.jobs.check]\nrunner="command"\ncommand=["echo","ok"]\n[targets.app.workflows]\nvalidate=["job:check"]\n',
            encoding="utf-8",
        )
        _init_git(child)
        (parent / ".devtool.toml").write_text(
            '[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n'
            '[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n'
            '[federation.workflows.release]\nsteps=[{id="child",ref="child::app::workflow:validate"}]\n',
            encoding="utf-8",
        )
        config = _load(parent)
        plan = build_federated_plan(config, "release")
        source = next(item for item in plan.source_lock.get("repositories", []) if item.get("repository") == "child")
        planned = planned_federated_git_lineage(
            source_lock=plan.source_lock, repository="child", parent_run_id="fixture-parent",
            parent_node_id="federation:release:child", child_run_id="fixture-child", target="app",
            contract_type="workflow", contract_name="validate", contract_ref="child::app::validate",
            federation_workflow="release", federation_step="child",
        )
        finalized = finalize_federated_git_lineage(child, planned, terminal_status="succeeded")
        if finalized.get("start_commit") != source.get("start_commit") or finalized.get("result_commit") != source.get("start_commit"):
            raise SystemExit("GIT-FRX-09 lineage fixture did not preserve start/result Git identity")
        if not finalized.get("result_ref") or not finalized.get("rollback_ref"):
            raise SystemExit("GIT-FRX-09 lineage fixture did not materialize private result/rollback refs")
        return finalized


def resume_invalidation_fixture() -> dict[str, object]:
    """Exercise FRX-Y02 selective invalidation without touching real user state."""
    from unittest.mock import patch

    from .federation_execution import FederatedChildRun, FederatedRunReport
    from .federation_resume import build_resume_plan, checkpoint_report, load_checkpoint

    with tempfile.TemporaryDirectory(prefix="devtool-frx-y02-") as td:
        root = Path(td)
        parent = root / "parent"
        child_a = root / "a"
        child_b = root / "b"
        state = root / "state"
        parent.mkdir(); child_a.mkdir(); child_b.mkdir()
        child_config = (
            '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n'
            '[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\n'
            'command=["python","-c","print(\'ok\')"]\nmetadata={read_only=true}\n'
            '[targets.app.workflows]\nvalidate=["job:check"]\n'
        )
        for child in (child_a, child_b):
            (child / ".devtool.toml").write_text(child_config, encoding="utf-8")
            (child / "source.txt").write_text("base\n", encoding="utf-8")
            _init_git(child)
        (parent / ".devtool.toml").write_text(
            '[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n'
            '[federation]\nschema=1\n[federation.repositories.a]\npath="../a"\n'
            '[federation.repositories.a.exports]\ncapabilities=["contracts/v1"]\n'
            '[federation.repositories.b]\npath="../b"\n'
            '[federation.repositories.b.requires]\ncapabilities=["contracts/v1"]\n'
            '[federation.workflows.release]\n'
            'steps=[{id="a",ref="a::app::workflow:validate"},{id="b",ref="b::app::workflow:validate",depends_on=["a"]}]\n',
            encoding="utf-8",
        )
        config = _load(parent)
        plan = build_federated_plan(config, "release")
        children = []
        for step in config.federation.workflows["release"].steps:
            node = next(
                item for item in plan.nodes
                if not item.synthetic and item.metadata.get("federation_step") == step.step_id
            )
            children.append(FederatedChildRun(
                step_id=step.step_id, repository=step.repository, workflow="validate", target="app",
                run_id=f"fixture-child-{step.step_id}", parent_node_id=f"federation:release:{step.step_id}",
                status="succeeded", exit_code=0, repository_identity=dict(node.metadata.get("repository_identity") or {}),
                run_lineage={"parent_run_id": "fixture-parent"}, operational_ownership={"references": []},
                federation_ref=f"{step.repository}::app::validate", requested_ref=step.ref,
                child_contract="workflow:validate", contract_ref=f"{step.repository}::app::workflow:validate",
                git_lineage={}, lifecycle="executed",
            ))
        with patch("devtool.federation_resume.state_dir", lambda: state), patch(
            "devtool.federation_git_lineage.state_dir", lambda: state
        ):
            checkpoint_report(
                config,
                FederatedRunReport("release", "fixture-parent", "succeeded", 0, tuple(children), plan),
            )
            # Devtool-owned runtime state is expected and must not invalidate source reuse.
            (child_a / ".devtool").mkdir(exist_ok=True)
            (child_a / ".devtool" / "artifacts").mkdir(exist_ok=True)
            (child_a / ".devtool" / "artifacts" / "registry.lock").write_text("runtime\n", encoding="utf-8")
            unchanged = build_resume_plan(config, load_checkpoint(config, "fixture-parent"), mode="resume")
            if [row.action for row in unchanged.decisions] != ["reused", "reused"]:
                raise SystemExit("FRX-Y02 fixture self-invalidated on Devtool runtime state")

            (child_a / "source.txt").write_text("drift\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(child_a), "add", "source.txt"], check=True, timeout=10)
            subprocess.run(["git", "-C", str(child_a), "commit", "-qm", "drift"], check=True, timeout=10)
            changed = _load(parent)
            resume = build_resume_plan(changed, load_checkpoint(changed, "fixture-parent"), mode="retry")
        actions = [row.action for row in resume.decisions]
        if actions != ["rerun_due_to_drift", "rerun_due_to_drift"]:
            raise SystemExit(f"FRX-Y02 fixture produced unexpected invalidation actions: {actions}")
        if "frozen Git/tree/config state changed" not in resume.decisions[0].reasons:
            raise SystemExit("FRX-Y02 fixture did not identify source drift")
        if not any(reason == "upstream child result changed: a" for reason in resume.decisions[1].reasons):
            raise SystemExit("FRX-Y02 fixture did not propagate upstream invalidation")
        return {
            "schema_version": 1,
            "roadmap_id": "FRX-Y02",
            "unchanged_actions": [row.action for row in unchanged.decisions],
            "drift_actions": actions,
            "decisions": [
                {"step_id": row.step_id, "action": row.action, "reasons": list(row.reasons)}
                for row in resume.decisions
            ],
            "checkpoint_predicates": [
                "plan", "repository", "source", "contract", "upstream-results",
                "capability-interface", "artifact-inputs", "terminal-evidence",
            ],
            "ssh_history_authority": "referenced-not-copied",
        }


def artifact_edge_fixture() -> dict[str, object]:
    """Exercise FRX-Y08 immutable producer -> consumer artifact edges."""
    with tempfile.TemporaryDirectory(prefix="devtool-frx-y08-") as td:
        root = Path(td)
        parent = root / "parent"
        producer = root / "producer"
        consumer = root / "consumer"
        state = root / "state"
        parent.mkdir(); producer.mkdir(); consumer.mkdir()
        child_toml = (
            '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n'
            '[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\n'
            'command=["echo","ok"]\n[targets.app.workflows]\nvalidate=["job:check"]\n'
        )
        for child in (producer, consumer):
            (child / ".devtool.toml").write_text(child_toml, encoding="utf-8")
            (child / "source.txt").write_text("base\n", encoding="utf-8")
            _init_git(child)
        (parent / ".devtool.toml").write_text(
            '[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n'
            '[federation]\nschema=1\n[federation.repositories.platform]\npath="../producer"\n'
            '[federation.repositories.app]\npath="../consumer"\n[federation.workflows.integration]\n'
            'steps=[{id="contracts",ref="platform::app::workflow:validate"},'
            '{id="app",ref="app::app::workflow:validate",depends_on=["contracts"],'
            'inputs={contracts={ref="contracts:artifact:schemas.json",type="json",schema="contracts/v1"}}}]\n',
            encoding="utf-8",
        )
        config = _load(parent)
        observed: dict[str, object] = {}

        def reenter(argv: list[str]) -> int:
            repo = Path(argv[argv.index("--repo") + 1]).resolve()
            run_id = os.environ["DEVTOOL_RUN_ID"]
            if repo == producer.resolve():
                artifact = producer / ".devtool" / "build-artifacts" / "schemas.json"
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(b'{"schema":1}\n')
                register_paths(
                    producer, [artifact],
                    metadata={
                        "run_id": run_id, "type": "json",
                        "artifact_schema": "contracts/v1", "producer": "frx-y08-fixture",
                    },
                )
                observed["producer_digest"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
            elif repo == consumer.resolve():
                payload = json.loads(os.environ["DEVTOOL_FEDERATION_ARTIFACT_INPUTS"])
                edge = payload["contracts"]
                if str(producer.resolve()) in json.dumps(payload, sort_keys=True):
                    raise SystemExit("FRX-Y08 fixture leaked producer filesystem path")
                materialized = consumer / str(edge["path"])
                if hashlib.sha256(materialized.read_bytes()).hexdigest() != edge["sha256"]:
                    raise SystemExit("FRX-Y08 fixture materialization digest mismatch")
                observed["consumer_edge"] = dict(edge)
            return 0

        with patch("devtool.federation_resume.state_dir", lambda: state), patch(
            "devtool.federation_git_lineage.state_dir", lambda: state
        ):
            first = run_federated_workflow(
                config, "integration", reenter=reenter, snapshot_lookup=lambda _run_id: None
            )
            if not first.ok or "consumer_edge" not in observed:
                raise SystemExit("FRX-Y08 artifact edge fixture did not complete successfully")
            checkpoint = load_checkpoint(config, first.parent_run_id)
            artifact = producer / ".devtool" / "build-artifacts" / "schemas.json"
            artifact.write_bytes(b'{"schema":2,"mutated":true}\n')
            resume = build_resume_plan(config, checkpoint, mode="resume")

        decisions = {row.step_id: row for row in resume.decisions}
        producer_decision = decisions.get("contracts")
        consumer_decision = decisions.get("app")
        if producer_decision is None or producer_decision.action != "reused":
            raise SystemExit("FRX-Y08 fixture did not preserve reusable producer")
        if consumer_decision is None or consumer_decision.action != "rerun_due_to_drift":
            raise SystemExit("FRX-Y08 fixture did not invalidate artifact consumer")
        if "consumed artifact digest changed: contracts" not in consumer_decision.reasons:
            raise SystemExit("FRX-Y08 fixture did not attribute artifact digest drift")
        child = next(row for row in first.children if row.step_id == "app")
        edge = dict(child.artifact_inputs.get("contracts") or {})
        if not edge.get("registry_id") or edge.get("sha256") != observed.get("producer_digest"):
            raise SystemExit("FRX-Y08 fixture did not preserve immutable registry identity")
        return {
            "schema_version": 1,
            "roadmap_id": "FRX-Y08",
            "edge": {
                "registry_id": edge.get("registry_id"),
                "sha256": edge.get("sha256"),
                "type": edge.get("type"),
                "schema": edge.get("schema"),
                "remote_transfer_authority": "SSH-02",
            },
            "producer_action": producer_decision.action,
            "consumer_action": consumer_decision.action,
            "consumer_reasons": list(consumer_decision.reasons),
            "producer_path_exposed": False,
            "consumer_materialization": "managed-relative-path",
        }



def git_frx10_integration_fixture() -> dict[str, object]:
    """Exercise GIT-FRX-10 integration policies and prepare/publish barrier."""
    with tempfile.TemporaryDirectory(prefix="devtool-git-frx10-") as td:
        root = Path(td)
        parent = root / "parent"
        a = root / "a"
        b = root / "b"
        publisher = root / "publisher"
        state = root / "state"
        for path in (parent, a, b, publisher):
            path.mkdir()
        child_toml = (
            '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n'
            '[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\n'
            'command=["echo","ok"]\n[targets.app.workflows]\nvalidate=["job:check"]\npublish=["job:check"]\n'
        )
        for child in (a, b, publisher):
            (child / ".devtool.toml").write_text(child_toml, encoding="utf-8")
            _init_git(child)
        (parent / ".devtool.toml").write_text(
            '[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n'
            '[federation]\nschema=1\n'
            '[federation.repositories.a]\npath="../a"\n'
            '[federation.repositories.b]\npath="../b"\n'
            '[federation.repositories.publisher]\npath="../publisher"\n'
            '[federation.workflows.release]\nintegration_policy="dependency-group"\npublish_barrier=true\n'
            'steps=[{id="library",ref="a::app::workflow:validate",stage="prepare"},'
            '{id="consumer",ref="b::app::workflow:validate",stage="prepare",depends_on=["library"]},'
            '{id="publish",ref="publisher::app::workflow:publish",stage="publish"}]\n'
            '[federation.workflows.integrate]\nintegration_policy="all-or-nothing-pointer-update"\n'
            'steps=[{id="a",ref="a::app::workflow:validate"},'
            '{id="b",ref="b::app::workflow:validate",depends_on=["a"]}]\n',
            encoding="utf-8",
        )
        config = _load(parent)
        calls: list[str] = []
        def reenter(argv: list[str]) -> int:
            calls.append(" ".join(argv))
            return 1 if len(calls) == 2 else 0
        with patch("devtool.federation_resume.state_dir", lambda: state), patch(
            "devtool.federation_git_lineage.state_dir", lambda: state
        ), patch("devtool.git_federation_integration.state_dir", lambda: state):
            release = run_federated_workflow(config, "release", reenter=reenter, snapshot_lookup=lambda _run_id: None)
            barrier = dict(release.lifecycle.get("publish_barrier") or {})
            if barrier.get("state") != "closed" or len(calls) != 2:
                raise SystemExit("GIT-FRX-10 fixture did not close publish barrier after failed prepare consumer")
            children = {child.step_id: child for child in release.children}
            if children.get("publish") is None or children["publish"].status != "blocked":
                raise SystemExit("GIT-FRX-10 fixture scheduled child-owned publish while barrier was closed")

            result_rows: list[dict[str, object]] = []
            starts: dict[str, str] = {}
            results: dict[str, str] = {}
            for step_id, repo_name, repo_root in (("a", "a", a), ("b", "b", b)):
                start = subprocess.check_output(["git", "-C", str(repo_root), "rev-parse", "HEAD"], text=True).strip()
                ref = subprocess.check_output(["git", "-C", str(repo_root), "symbolic-ref", "HEAD"], text=True).strip()
                (repo_root / "README.md").write_text(f"{repo_name}-result\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(repo_root), "add", "README.md"], check=True, timeout=10)
                subprocess.run(["git", "-C", str(repo_root), "commit", "-qm", f"{repo_name} result"], check=True, timeout=10)
                result = subprocess.check_output(["git", "-C", str(repo_root), "rev-parse", "HEAD"], text=True).strip()
                subprocess.run(["git", "-C", str(repo_root), "reset", "--hard", "-q", start], check=True, timeout=10)
                starts[repo_name] = start; results[repo_name] = result
                result_rows.append({
                    "step_id": step_id, "repository": repo_name, "workflow": "validate", "target": "app",
                    "run_id": f"fixture-{step_id}", "status": "succeeded", "exit_code": 0,
                    "git_lineage": {"start_commit": start, "result_commit": result, "planned_head_ref": ref},
                })
            cp = checkpoint_path(config, "fixture-integration")
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps({
                "schema_version": 1, "kind": "devtool.federation.resume-checkpoint",
                "parent_run_id": "fixture-integration", "workflow": "integrate", "group": "",
                "status": "succeeded", "exit_code": 0, "children": result_rows,
            }), encoding="utf-8")
            planned = integrate_federation_checkpoint(config, "fixture-integration", dry_run=True)
            if planned.status != "planned" or any(row.status != "planned" for row in planned.pointers):
                raise SystemExit("GIT-FRX-10 fixture did not produce all-or-nothing integration plan")
            applied = integrate_federation_checkpoint(config, "fixture-integration", dry_run=False)
            if applied.status != "succeeded" or any(row.status != "integrated" for row in applied.pointers):
                raise SystemExit("GIT-FRX-10 fixture did not integrate validated result pointers")
            for repo_name, repo_root in (("a", a), ("b", b)):
                head = subprocess.check_output(["git", "-C", str(repo_root), "rev-parse", "HEAD"], text=True).strip()
                if head != results[repo_name]:
                    raise SystemExit(f"GIT-FRX-10 fixture pointer integration mismatch for {repo_name}")
        return {
            "schema_version": 1,
            "roadmap_id": "GIT-FRX-10",
            "publish_barrier": barrier,
            "integration_policy": applied.policy,
            "integration_status": applied.status,
            "integrated_repositories": [row.repository for row in applied.pointers if row.status == "integrated"],
            "automatic_push": applied.automatic_push,
            "published_release_rollback_owned": applied.published_release_rollback_owned,
        }


def git_frx14_cancellation_fixture() -> dict[str, object]:
    from types import SimpleNamespace
    from .federation_recovery import cancellation_recovery_evidence

    def child(step: str, status: str, exit_code: int, run_id: str = ""):
        return SimpleNamespace(
            step_id=step,
            repository=step,
            status=status,
            exit_code=exit_code,
            run_id=run_id,
            git_lineage={"rollback_ref": f"refs/devtool/rollback/{run_id}" if run_id else ""},
            operational_ownership={"recovery": "child-owned"},
        )

    report = SimpleNamespace(
        parent_run_id="git-frx14-fixture",
        status="cancelled",
        children=(
            child("prepared", "succeeded", 0, "child-prepared"),
            child("cancelled", "cancelled", 130, "child-cancelled"),
            child("pending", "cancelled", 130, ""),
        ),
    )
    payload = cancellation_recovery_evidence(report)
    if payload["reusable_steps"] != ["prepared"]:
        raise SystemExit("GIT-FRX-14 fixture did not preserve reusable succeeded child")
    if {row["step_id"] for row in payload["invalidated_steps"]} != {"cancelled", "pending"}:
        raise SystemExit("GIT-FRX-14 fixture did not invalidate cancelled/pending children")
    if payload["automatic_git_rollback"] or payload["rollback_ownership"] != "child-repository-owned":
        raise SystemExit("GIT-FRX-14 fixture violated child-owned rollback boundary")
    return {"schema_version": 1, "roadmap_id": "GIT-FRX-14", **payload}


def provider_budget_fixture() -> dict[str, object]:
    from .federation_provider import FederationProviderCapability
    from .federation_scheduler import build_federated_schedule

    with tempfile.TemporaryDirectory(prefix="devtool-frx-y09-") as td:
        root = Path(td)
        parent = root / "parent"
        parent.mkdir()
        child_config = '''[project]
runner="command"
[workspace]
default_target="app"
[jobs.one]
runner="command"
command=["true"]
outputs=["out/**"]
[targets.app]
runner="command"
execution_environment="ssh"
root="."
[targets.app.metadata]
federation_preferred_worker="tablet"
[targets.app.metadata.federation_resources]
"port:adb"=1
[targets.app.workflows]
work=["job:one"]
'''
        for name in ("alpha", "beta"):
            child = root / name
            child.mkdir()
            (child / ".devtool.toml").write_text(child_config, encoding="utf-8")
        (parent / ".devtool.toml").write_text(
            '''[project]
runner="command"
[workspace.scheduler]
max_parallel_nodes=4
[workspace.scheduler.provider_budgets]
"ssh:tablet"=1
[workspace.scheduler.resource_budgets]
"port:adb"=1
[federation]
schema=1
[federation.repositories.alpha]
path="../alpha"
[federation.repositories.beta]
path="../beta"
[federation.workflows.release]
steps=[{id="alpha",ref="alpha::work"},{id="beta",ref="beta::work"}]
''',
            encoding="utf-8",
        )
        config = _load(parent)
        plan = build_federated_plan(config, "release")
        provider = FederationProviderCapability(
            provider_id="ssh:tablet",
            kind="ssh-native",
            profile="tablet",
            execution_environments=("ssh",),
            healthy=True,
            evidence_source="ssh-doctor",
            evidence_digest="fixture-doctor",
            metadata={"qualified_target": "app", "worker": {"platform": "termux"}},
        )
        schedule = build_federated_schedule(
            config.federation.workflows["release"],
            plan,
            parallel_requested=True,
            max_parallel=4,
            provider_capabilities=(provider,),
            provider_budgets=config.workspace.scheduler.provider_budgets,
            resource_budgets=config.workspace.scheduler.resource_budgets,
        )
        if [list(wave.nodes) for wave in schedule.waves] != [["alpha"], ["beta"]]:
            raise SystemExit("FRX-Y09 fixture did not serialize provider/resource budget")
        if not schedule.by_step["beta"].queue_reasons:
            raise SystemExit("FRX-Y09 fixture omitted queue reason")
        return {
            "schema_version": 1,
            "roadmap_id": "FRX-Y09",
            "waves": [wave.as_dict() for wave in schedule.waves],
            "placements": {node.step_id: node.as_dict()["placement"] for node in schedule.nodes},
            "queue_reasons": {node.step_id: list(node.queue_reasons) for node in schedule.nodes},
            "remote_probe_owned_by": "ssh-01/ssh-03",
        }


def hierarchy_fixture() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="devtool-frx-y10-") as td:
        root = Path(td); master = root / "master"; platform = root / "platform"; leaf = root / "leaf"
        master.mkdir(); platform.mkdir(); leaf.mkdir()
        (leaf / ".devtool.toml").write_text(
            '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n'
            '[targets.app.jobs.check]\nrunner="command"\ncommand=["true"]\n[targets.app.workflows]\nvalidate=["job:check"]\n',
            encoding="utf-8",
        )
        (platform / ".devtool.toml").write_text(
            '[project]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.app]\npath="../leaf"\n'
            '[federation.workflows.release]\nsteps=[{id="app",ref="app::app::validate"}]\n',
            encoding="utf-8",
        )
        (master / ".devtool.toml").write_text(
            '[project]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.platform]\npath="../platform"\n'
            '[federation.workflows.ship]\nsteps=[{id="platform",ref="platform::@federation::release"}]\n',
            encoding="utf-8",
        )
        config = _load(master)
        plan = build_federated_plan(config, "ship")
        direct = [node for node in plan.nodes if not node.synthetic]
        if len(direct) != 1 or direct[0].key != "platform::@federation::release":
            raise SystemExit("FRX-Y10 fixture flattened or lost the direct child federation")
        calls: list[list[str]] = []
        report = run_federated_workflow(
            config, "ship", reenter=lambda argv: calls.append(list(argv)) or 0,
            publish=lambda _event: None, snapshot_lookup=lambda _run_id: None,
            parent_run_id="frx-y10-fixture-parent",
        )
        if not report.ok or len(calls) != 1 or calls[0][-3:] != ["federation", "run", "release"]:
            raise SystemExit("FRX-Y10 fixture did not re-enter exactly one child federation")
        lineage = dict(report.children[0].run_lineage or {})
        return {
            "schema_version": 1,
            "roadmap_id": "FRX-Y10",
            "parent_nodes": [node.key for node in direct],
            "flattened_grandchildren": False,
            "reentry_argv_tail": calls[0][-3:],
            "lineage": {
                "relationship": str(lineage.get("relationship") or ""),
                "parent_run_id": str(lineage.get("parent_run_id") or ""),
                "child_run_id": str(lineage.get("child_run_id") or ""),
                "child_contract": str(lineage.get("child_contract") or ""),
                "contract_ref": str(lineage.get("contract_ref") or ""),
                "hierarchy_depth": int(lineage.get("hierarchy_depth") or 1),
            },
        }


def bootstrap_sync_fixture() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="devtool-frx-y11-") as td:
        root = Path(td); seed = root / "seed"; bare = root / "origin.git"; child = root / "child"; parent = root / "parent"
        seed.mkdir(); parent.mkdir()
        (seed / "source.txt").write_text("fixture\n", encoding="utf-8")
        (seed / ".devtool.toml").write_text(
            '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n'
            '[targets.app.jobs.check]\nrunner="command"\ncommand=["true"]\n[targets.app.workflows]\nvalidate=["job:check"]\n',
            encoding="utf-8",
        )
        _init_git(seed)
        subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True, timeout=20)
        subprocess.run(["git", "clone", "-q", str(bare), str(child)], check=True, timeout=20)
        (parent / ".devtool.toml").write_text(
            '[project]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n'
            '[federation.workflows.release]\nsteps=[{id="child",ref="child::app::workflow:validate"}]\n',
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(root / "state")}, clear=False):
            config = _load(parent)
            frozen = FederationSourceLock.from_dict(build_federated_plan(config, "release").source_lock)
            write_federation_source_lock(config, frozen)
            shutil.rmtree(child)
            missing = load_project_config(
                parent, parent / ".devtool.toml", target_name="", allow_ambiguous_target=True,
                allow_ambiguous_project_kind=True, allow_missing_federation_repositories=True,
            )
            preview = federation_source_management(missing, operation="bootstrap", apply=False)
            applied = federation_source_management(missing, operation="bootstrap", apply=True)
            synced = federation_source_management(missing, operation="sync", apply=False)
        if not preview.ok or not applied.ok or not synced.ok:
            raise SystemExit("FRX-Y11 fixture failed lock-driven bootstrap/sync")
        if applied.lock_updated or synced.lock_updated:
            raise SystemExit("FRX-Y11 fixture mutated the frozen lock as a source-management side effect")
        return {
            "schema_version": 1,
            "roadmap_id": "FRX-Y11",
            "lock_digest": frozen.lock_digest,
            "source_locator_digest": frozen.repositories[0].remote_locator_digest,
            "bootstrap_preview": {
                "status": preview.status,
                "states": [item.state for item in preview.actions],
                "mutations": [item.mutation for item in preview.actions],
            },
            "bootstrap_apply": {
                "status": applied.status,
                "states": [item.state for item in applied.actions],
                "verified": [item.verified for item in applied.actions],
            },
            "sync_preview": {
                "status": synced.status,
                "states": [item.state for item in synced.actions],
            },
            "lock_updated": False,
        }

def main(argv: list[str] | None=None) -> int:
    p=argparse.ArgumentParser(prog='python -m devtool.federation_roadmap_fixtures')
    p.add_argument('fixture', choices=['interface','public-contract','repository-ownership','source-lock','source-drift','git-lineage','resume-invalidation','artifact-edge','git-frx10-integration','git-frx14-cancellation','provider-budget','hierarchy','bootstrap-sync'])
    ns=p.parse_args(argv)
    payload=(interface_fixture() if ns.fixture=='interface' else public_contract_fixture() if ns.fixture=='public-contract' else repository_ownership_fixture() if ns.fixture=='repository-ownership' else git_lineage_fixture() if ns.fixture=='git-lineage' else resume_invalidation_fixture() if ns.fixture=='resume-invalidation' else artifact_edge_fixture() if ns.fixture=='artifact-edge' else git_frx10_integration_fixture() if ns.fixture=='git-frx10-integration' else git_frx14_cancellation_fixture() if ns.fixture=='git-frx14-cancellation' else provider_budget_fixture() if ns.fixture=='provider-budget' else hierarchy_fixture() if ns.fixture=='hierarchy' else bootstrap_sync_fixture() if ns.fixture=='bootstrap-sync' else source_lock_fixture(mutate=ns.fixture=='source-drift'))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
