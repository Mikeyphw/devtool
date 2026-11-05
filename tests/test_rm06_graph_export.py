"""RM-06 graph/transfer hostile inputs and single-job compatibility."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import pytest

from devtool.actions_graph_export import (
    compile_graph, workflow_yaml, consume_handoffs, stage_outputs, _load_manifest, _path,
)
from devtool.actions_language_providers import ProviderError, provider_export
from tests.test_next03_language_providers import fixture, PIN, git


def compile_fixture(tmp_path, *, independent=False):
    tmp_path.mkdir(parents=True,exist_ok=True)
    root=fixture(tmp_path)
    if independent:
        config=root/'.devtool.toml'
        config.write_text(config.read_text().replace('depends_on=["go"]','depends_on=[]'))
    return root,compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)


def test_compiles_graph_edges_and_independent_siblings(tmp_path):
    root, plan=compile_fixture(tmp_path)
    a,b=plan['jobs']
    assert a['needs']==[] and b['needs']==[a['id']]
    assert plan['fallback']=='single-job' and not plan['authenticated_remote']
    assert plan['matrix']==[{'os':'ubuntu-24.04','arch':'x86_64'}]
    content=workflow_yaml(plan)
    assert '  node-01:' in content and '  node-02:\n    needs: [node-01]' in content
    assert content.count('actions/checkout@')==2
    assert content.count('actions/setup-python@')==2
    assert 'github.ref' in content and 'github.run_attempt' not in content
    assert 'release-qualified' not in content
    assert content==workflow_yaml(compile_graph(root,target='app',workflow='ci',devtool_sha=PIN))
    # B is independent of A when native config explicitly declares it.
    root, plan=compile_fixture(tmp_path/'second',independent=True)
    assert all(not job['needs'] for job in plan['jobs'])
    assert '    needs: [' not in workflow_yaml(plan)



def _rm06_job_env_blocks(workflow_text: str) -> list[str]:
    """Extract only jobs.<job_id>.env; ignore steps[].env and script bodies.

    A job's env is exactly four-space indented in our generated YAML. A
    subordinate step env has deeper indentation and must not be mistaken for
    an invalid pre-runner expression. This is intentionally conservative.
    """
    lines = workflow_text.splitlines()
    blocks: list[str] = []
    for index, line in enumerate(lines):
        if line != '    env:':
            continue
        items = []
        for value in lines[index + 1:]:
            if value.strip() and len(value) - len(value.lstrip(' ')) <= 4:
                break
            items.append(value)
        blocks.append('\n'.join(items))
    return blocks


def test_rm06_job_level_expression_checker_is_context_accurate():
    # A step can use runner.temp, while jobs.<job_id>.env cannot.
    good = ("jobs:\n  node-01:\n    env:\n      PIN: 'x'\n    steps:\n"
            "      - name: use runner context\n        env:\n          STORE: ${{ runner.temp }}\n")
    assert len(_rm06_job_env_blocks(good)) == 1
    assert all('${{ runner.' not in block for block in _rm06_job_env_blocks(good))
    bad = good.replace("      PIN: 'x'", '      PIN: ${{ runner.temp }}')
    assert any('${{ runner.' in block for block in _rm06_job_env_blocks(bad))


def test_generated_job_env_is_valid_before_runner_allocation(tmp_path):
    """GitHub rejects runner.temp in jobs.<job_id>.env before any jobs start."""
    root, graph = compile_fixture(tmp_path)
    yaml = workflow_yaml(graph)
    # Every job sets up temporary stores only after its runner has started.
    assert yaml.count('      - name: Bind runner-local tool stores\n') == len(graph['jobs'])
    assert yaml.count('NUGET_PACKAGES=%s') == len(graph['jobs'])
    assert yaml.count('PIP_CACHE_DIR=%s') == len(graph['jobs'])
    assert yaml.count('>> "$GITHUB_ENV"') == 2 * len(graph['jobs'])
    assert 'NUGET_PACKAGES: ${{ runner.temp }}' not in yaml
    assert 'PIP_CACHE_DIR: ${{ runner.temp }}' not in yaml
    job_envs = _rm06_job_env_blocks(yaml)
    assert len(job_envs) == len(graph['jobs'])
    for job_env in job_envs:
        assert '${{ runner.' not in job_env
    assert '    needs: [node-01]' in yaml


def test_generated_stores_are_not_in_consumer_source_tree(tmp_path):
    root, graph = compile_fixture(tmp_path)
    yaml = workflow_yaml(graph)
    assert '"$RUNNER_TEMP/devtool-nuget-store"' in yaml
    assert '"$RUNNER_TEMP/devtool-python-wheel-store"' in yaml
    assert '      CARGO_NET_OFFLINE: "true"\n' in yaml
    assert '      NUGET_PACKAGES: ' not in yaml
    assert '      PIP_CACHE_DIR: ' not in yaml


def test_multi_review_write_verify_and_frozen_digest(tmp_path):
    root, graph=compile_fixture(tmp_path)
    review=provider_export(root,target='app',workflow='ci',devtool_sha=PIN,mode='multi-job')
    assert review['status']=='REVIEW_ONLY'
    assert graph['graph_sha256']==review['graph_sha256']
    result=provider_export(root,target='app',workflow='ci',devtool_sha=PIN,mode='multi-job',write=True)
    assert result['writes_performed'] and result['workflow_path'].endswith('-multi.yml')
    assert provider_export(root,target='app',workflow='ci',devtool_sha=PIN,mode='multi-job',verify=True)['status'].startswith('LOCAL_WORKFLOW')
    with pytest.raises(ProviderError,match='existing'):
        provider_export(root,target='app',workflow='ci',devtool_sha=PIN,mode='multi-job',write=True)
    (root/result['workflow_path']).write_bytes(b'tampered bytes')
    with pytest.raises(ProviderError,match='changed'):
        provider_export(root,target='app',workflow='ci',devtool_sha=PIN,mode='multi-job',verify=True)


def test_refuses_noncausal_producer_output_and_path_reuse(tmp_path):
    root=fixture(tmp_path)
    config=root/'.devtool.toml'
    config.write_text(config.read_text().replace('inputs = ["go.mod"]','inputs = ["go.mod"]\noutputs = ["dist/item.bin"]'))
    # No overwrite of tracked paths, even in review-only.
    with pytest.raises(ProviderError,match='tracked source'):
        config.write_text(config.read_text().replace('dist/item.bin','go.mod'))
        compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    config.write_text(config.read_text().replace('go.mod"]\noutputs = ["go.mod"]','go.mod"]\noutputs = ["dist/item.bin"]'))
    content=config.read_text().replace('command = ["python3", "-m", "unittest"]','command = ["python3", "-m", "unittest"]\ninputs = ["dist/item.bin"]')
    content=content.replace('depends_on=["go"]','depends_on=[]')
    config.write_text(content)
    with pytest.raises(ProviderError,match='direct producer dependency'):
        compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)


@pytest.mark.parametrize('path', ['/etc/passwd','../e','a/../b','a/.git/f','a\\b','foo//bar','output:bad',''])
def test_untrusted_paths_refused(path):
    with pytest.raises(ProviderError):_path(path)


def _with_output(tmp_path):
    root=fixture(tmp_path)
    config=root/'.devtool.toml'
    config.write_text(config.read_text().replace('inputs = ["go.mod"]','inputs = ["go.mod"]\noutputs = ["dist/bin"]'))
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    dest=root/'dist/bin';dest.parent.mkdir();dest.write_bytes(b'producer bytes')
    return root,graph


def test_handoff_round_trip_and_tampered_bytes_fail_before_workspace_mutation(tmp_path):
    root, graph=_with_output(tmp_path)
    producer=graph['jobs'][0];consumer=graph['jobs'][1]
    source='a'*40;run='422';attempt=3
    staging=tmp_path/'staged'
    stage_outputs(root,staging,graph,producer,source,run,attempt)
    receipt_dir=staging/producer['id']
    files=_load_manifest(receipt_dir,graph=graph,parent=producer,source=source,run=run,attempt=attempt)
    assert files[0][0]=='dist/bin'
    with pytest.raises(ProviderError,match='run/attempt'):
        _load_manifest(receipt_dir,graph=graph,parent=producer,source=source,run=run,attempt=4)
    (receipt_dir/'payload/dist/bin').write_bytes(b'x'*len(b'producer bytes'))
    (root/'dist/bin').unlink()
    with pytest.raises(ProviderError,match='digest'):
        consume_handoffs(root,staging,graph,consumer,source,run,attempt)
    assert not (root/'dist/bin').exists()
    (receipt_dir/'payload/dist/bin').write_bytes(b'producer bytes')
    consume_handoffs(root,staging,graph,consumer,source,run,attempt)
    assert (root/'dist/bin').read_bytes()==b'producer bytes'
    with pytest.raises(ProviderError,match='overwrite'):
        consume_handoffs(root,staging,graph,consumer,source,run,attempt)


def test_handoff_bad_symlink_and_manifest_identity(tmp_path):
    root,graph=_with_output(tmp_path)
    job=graph['jobs'][0]
    out=tmp_path/'staging';stage_outputs(root,out,graph,job,'a'*40,'1',1)
    folder=out/job['id']
    receipt=json.loads((folder/'receipt.json').read_text())
    receipt['producer_job']='node-99'
    (folder/'receipt.json').write_text(json.dumps(receipt))
    with pytest.raises(ProviderError,match='mismatch'):
        _load_manifest(folder,graph=graph,parent=job,source='a'*40,run='1',attempt=1)
    receipt['producer_job']=job['id']
    (folder/'receipt.json').write_text(json.dumps(receipt))
    p=folder/'payload/dist/bin';p.unlink();p.symlink_to(root/'dist/bin')
    with pytest.raises(ProviderError,match='symlink'):
        _load_manifest(folder,graph=graph,parent=job,source='a'*40,run='1',attempt=1)


def test_native_direct_job_bridge_and_wrong_identity_refuse_before_execute(tmp_path,monkeypatch):
    from devtool.actions_graph_export import run_node
    from types import SimpleNamespace
    from devtool import runners
    root,graph=compile_fixture(tmp_path)
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','123456')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','2')
    calls=[]
    def direct(config,specs,**kw):
        calls.append((specs,kw))
        return [SimpleNamespace(returncode=0,skipped=False)],''
    monkeypatch.setattr(runners,'run_specs',direct)
    report=run_node(root,target='app',workflow='ci',devtool_sha=PIN,
                    graph_sha=graph['graph_sha256'],job_id=graph['jobs'][0]['id'],
                    inputs=tmp_path/'empty',outputs=tmp_path/'out')
    assert report['status']=='LOCAL_JOB_SUCCEEDED' and len(calls)==1
    assert report['uploaded_artifacts_authenticated'] is False
    assert calls[0][0][0].argv[:2]==['go','test']
    with pytest.raises(ProviderError,match='digest'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,
                 graph_sha='f'*64,job_id=graph['jobs'][0]['id'],inputs=tmp_path/'empty',outputs=tmp_path/'out')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','zero')
    with pytest.raises(ProviderError,match='identity'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,
                 graph_sha=graph['graph_sha256'],job_id=graph['jobs'][0]['id'],inputs=tmp_path/'empty',outputs=tmp_path/'out')
    assert len(calls)==1


def test_branching_dag_keeps_only_real_needs_edges(tmp_path):
    root=fixture(tmp_path)
    p=root/'.devtool.toml'
    text=p.read_text().replace('depends_on=["go"]','depends_on=[]')
    text=text.replace('[targets.app.workflows]', '[targets.app.jobs.python_lint]\nrunner = "python"\ncommand = ["python3", "-m", "compileall", "."]\n[targets.app.workflows]')
    text=text.replace('ref="job:py_tests", depends_on=[]}',
                      'ref="job:py_tests", depends_on=[]}, {id="lint", ref="job:python_lint", depends_on=["go"]}')
    p.write_text(text)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    assert [item['needs'] for item in graph['jobs']]==[[],[],['node-01']]
    yaml=workflow_yaml(graph)
    assert '  node-03:\n    needs: [node-01]' in yaml
    assert '  node-02:\n    runs-on: ubuntu-24.04' in yaml
    assert yaml.count('needs:')==1


def test_real_native_target_job_executes_and_zero_tests_do_not_pass(tmp_path,monkeypatch):
    """Run the true direct-job bridge, not only a mocked adapter."""
    from devtool.actions_graph_export import run_node
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    monkeypatch.setenv('XDG_CACHE_HOME',str(tmp_path/'cache'))
    root=fixture(tmp_path,go=False)
    source=git(root,'rev-parse','HEAD')
    monkeypatch.setenv('GITHUB_SHA',source)
    monkeypatch.setenv('GITHUB_RUN_ID','55133')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    with pytest.raises(ProviderError,match='did not succeed'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,
                 graph_sha=graph['graph_sha256'],job_id='node-01',
                 inputs=tmp_path/'none',outputs=tmp_path/'outputs')
    (root/'test_success.py').write_text('import unittest\nclass Smoke(unittest.TestCase):\n def test_one(self): self.assertEqual(2,1+1)\n')
    git(root,'add','test_success.py')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@example.org','commit','-qm','one real test')
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    result=run_node(root,target='app',workflow='ci',devtool_sha=PIN,
                    graph_sha=graph['graph_sha256'],job_id='node-01',
                    inputs=tmp_path/'none',outputs=tmp_path/'outputs')
    assert result['status']=='LOCAL_JOB_SUCCEEDED'
    assert result['github_source']==git(root,'rev-parse','HEAD')
    assert result['release_qualified'] is False
