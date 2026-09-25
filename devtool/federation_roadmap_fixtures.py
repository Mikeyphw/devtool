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

def main(argv: list[str] | None=None) -> int:
    p=argparse.ArgumentParser(prog='python -m devtool.federation_roadmap_fixtures')
    p.add_argument('fixture', choices=['interface','public-contract','repository-ownership'])
    ns=p.parse_args(argv)
    payload=(interface_fixture() if ns.fixture=='interface' else public_contract_fixture() if ns.fixture=='public-contract' else repository_ownership_fixture())
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
