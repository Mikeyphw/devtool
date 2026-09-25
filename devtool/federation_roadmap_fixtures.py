from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .config import load_project_config
from .federation_interfaces import build_interface_report
from .federation_plan import build_federated_plan


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


def main(argv: list[str] | None=None) -> int:
    p=argparse.ArgumentParser(prog='python -m devtool.federation_roadmap_fixtures')
    p.add_argument('fixture', choices=['interface','public-contract'])
    ns=p.parse_args(argv)
    payload=interface_fixture() if ns.fixture=='interface' else public_contract_fixture()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
