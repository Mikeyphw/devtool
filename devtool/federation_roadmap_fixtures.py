from __future__ import annotations

import argparse
import json
import tempfile
import subprocess
from pathlib import Path

from .config import load_project_config
from .federation_interfaces import build_interface_report
from .federation_plan import build_federated_plan
from .git_ownership import GitFederatedOwnershipError, resolve_federated_git_owner
from .federation_lock import FederationSourceLock, compare_federation_source_locks
from .federation_git_lineage import finalize_federated_git_lineage, planned_federated_git_lineage


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
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Devtool Fixture"], check=True)
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)


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
        subprocess.run(["git", "-C", str(child), "add", "."], check=True)
        subprocess.run(["git", "-C", str(child), "commit", "-qm", "drift"], check=True)
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

def main(argv: list[str] | None=None) -> int:
    p=argparse.ArgumentParser(prog='python -m devtool.federation_roadmap_fixtures')
    p.add_argument('fixture', choices=['interface','public-contract','repository-ownership','source-lock','source-drift','git-lineage'])
    ns=p.parse_args(argv)
    payload=(interface_fixture() if ns.fixture=='interface' else public_contract_fixture() if ns.fixture=='public-contract' else repository_ownership_fixture() if ns.fixture=='repository-ownership' else git_lineage_fixture() if ns.fixture=='git-lineage' else source_lock_fixture(mutate=ns.fixture=='source-drift'))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
