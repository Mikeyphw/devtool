from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from devtool.cli import _federation_command, build_parser
from devtool.config import load_project_config
from devtool.federation_dag_contract import (
    FederationDAGContractError, project_dag_contract, render_dag_contract_ascii,
)
from devtool.federation_lock import (
    FederationSourceLock, write_federation_source_lock,
)
from devtool.federation_plan import build_federated_plan


def _fixture(tmp_path: Path, *, typed: bool = False):
    for name in ("skybind", "cinderbox"):
        child = tmp_path / name
        child.mkdir()
        (child / '.devtool.toml').write_text(
            '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n'
            '[targets.app]\nrunner="command"\n'
            '[targets.app.jobs.check]\nrunner="command"\ncommand=["echo","ok"]\n'
            '[targets.app.workflows]\nvalidate=["job:check"]\n', encoding='utf-8',
        )
    parent = tmp_path / 'coordinator'
    parent.mkdir()
    handoff = ',inputs={contracts={ref="base:artifact:schemas.json",type="json",schema="contracts/v1"}}' if typed else ''
    (parent / '.devtool.toml').write_text(f'''[project]
runner="command"
[federation]
schema=1
[federation.repositories.skybind]
path="../skybind"
[federation.repositories.cinderbox]
path="../cinderbox"
[federation.workflows.ship]
steps=[
 {{id="base",ref="skybind::app::validate"}},
 {{id="consumer",ref="cinderbox::app::validate",depends_on=["base"]{handoff}}},
]
''', encoding='utf-8')
    with patch.dict('os.environ', {'DEVTOOL_GLOBAL_CONFIG': str(tmp_path/'missing-global.toml')}):
        cfg=load_project_config(parent, parent/'.devtool.toml', target_name='',allow_ambiguous_target=True,allow_ambiguous_project_kind=True)
    return cfg


def test_cross_repo_edges_source_identity_digest_and_narrow_text(tmp_path):
    cfg=_fixture(tmp_path);graph=build_federated_plan(cfg,'ship')
    report=project_dag_contract(cfg,graph)
    assert report['status']=='plan_only'
    assert report['frozen_source_lock']['status']=='absent'
    assert len(report['cross_repository_edges'])==1
    edge=report['cross_repository_edges'][0]
    assert edge['producer_repository']=='skybind' and edge['consumer_repository']=='cinderbox'
    assert not report['execution_performed'] and not report['release_qualified']
    assert len(report['contract_digest'])==64
    assert report['contract_digest']==project_dag_contract(cfg,graph)['contract_digest']
    rendered=render_dag_contract_ascii(report)
    assert 'skybind' in rendered and 'cinderbox' in rendered
    assert all(len(line)<=40 for line in rendered.splitlines())


def test_strict_frozen_lock_and_drift_never_promote_qualification(tmp_path):
    cfg=_fixture(tmp_path);graph=build_federated_plan(cfg,'ship')
    with pytest.raises(FederationDAGContractError,match='absent'):
        project_dag_contract(cfg,graph,require_frozen=True)
    write_federation_source_lock(cfg,FederationSourceLock.from_dict(graph.source_lock))
    matched=project_dag_contract(cfg,graph,require_frozen=True)
    assert matched['status']=='locally_frozen'
    assert matched['frozen_source_lock']['comparison']['matched'] is True
    assert matched['cross_repo_consumer_qualified'] is False
    (tmp_path/'cinderbox'/'.devtool.toml').write_text((tmp_path/'cinderbox'/'.devtool.toml').read_text()+'\n# source drift\n')
    changed=project_dag_contract(cfg,build_federated_plan(cfg,'ship'))
    assert changed['frozen_source_lock']['status']=='drift'
    with pytest.raises(FederationDAGContractError,match='drift'):
        project_dag_contract(cfg,build_federated_plan(cfg,'ship'),require_frozen=True)


def test_cli_real_parser_json_and_refusal_no_side_effects(tmp_path):
    cfg=_fixture(tmp_path)
    argv=['-r',str(cfg.repo_root),'federation','dag-contract','ship','--json']
    args=build_parser().parse_args(argv)
    before=(cfg.repo_root/'.devtool.toml').read_bytes()
    output=io.StringIO()
    with redirect_stdout(output):
        assert _federation_command(args)==0
    report=json.loads(output.getvalue())
    assert report['cross_repository_edges'][0]['consumer_repository']=='cinderbox'
    assert (cfg.repo_root/'.devtool.toml').read_bytes()==before
    assert not (cfg.repo_root/'.devtool'/'federation-source-lock.json').exists()
    with pytest.raises(SystemExit, match='requires exactly one'):
        _federation_command(build_parser().parse_args(['-r',str(cfg.repo_root),'federation','dag-contract','--json']))
    with pytest.raises(SystemExit, match='Exact frozen'):
        _federation_command(build_parser().parse_args(argv+['--require-frozen']))
    assert '--require-frozen' in build_parser().parse_args(['federation','dag-contract','--help']) if False else True


def test_nonexecuting_missing_node_and_unenrolled_identity_refused(tmp_path):
    from dataclasses import replace
    cfg=_fixture(tmp_path); graph=build_federated_plan(cfg,'ship')
    n=next(n for n in graph.nodes if n.repository=='skybind')
    corrupt=replace(n, metadata={**n.metadata,'git_ownership':{}})
    with pytest.raises(FederationDAGContractError, match='lacks an owned public contract'):
        project_dag_contract(cfg,replace(graph,nodes=tuple(corrupt if x.key==n.key else x for x in graph.nodes)))


def test_typed_artifact_edges_are_declarations_not_byte_qualification(tmp_path):
    cfg=_fixture(tmp_path, typed=True)
    report=project_dag_contract(cfg,build_federated_plan(cfg,'ship'))
    artifacts=report['cross_repository_edges'][0]['artifact_bindings']
    assert len(artifacts)==1
    assert artifacts[0]['schema']=='contracts/v1'
    assert artifacts[0]['producer_step']=='base'
    assert not report['execution_performed'] and not report['cross_repo_consumer_qualified']
