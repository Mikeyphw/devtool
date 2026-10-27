"""RM-10 promises: real graph/data, executable contract and negative controls."""
import io
import json
import subprocess
import tarfile
import struct
from pathlib import Path
from types import SimpleNamespace
import pytest
from devtool.actions_rm10_cargo import (cargo_workspace,inspect_cargo_job,verify_rust_binary,
                                          verify_crate,verify_cargo_json,CargoError)
from devtool.actions_language_providers import provider_plan,provider_export,ProviderError
from devtool.actions_graph_export import compile_graph,workflow_yaml,run_node
from tests.test_next03_language_providers import PIN


def git(root,*cmd):
    return subprocess.run(['git','-C',str(root),*cmd],check=True,capture_output=True,text=True).stdout.strip()


def fixture(tmp_path):
    root=tmp_path/'repo';root.mkdir()
    (root/'rust-toolchain.toml').write_text('[toolchain]\nchannel = "1.85.1"\ncomponents = ["rustfmt", "clippy"]\ntargets = ["x86_64-unknown-linux-gnu"]\n')
    (root/'Cargo.toml').write_text('[workspace]\nmembers = ["crates/*"]\nresolver="2"\n')
    for name in ('core','app'):
        folder=root/'crates'/name;folder.mkdir(parents=True)
        (folder/'src').mkdir();(folder/'src/lib.rs').write_text('pub fn test_value() -> u8 { 7 }\n')
        dep='[dependencies]\ncore = { path = "../core" }\n' if name=='app' else ''
        (folder/'Cargo.toml').write_text(f'[package]\nname="{name}"\nversion="0.1.0"\nedition="2021"\n{dep}[features]\nextra=[]\n')
    (root/'Cargo.lock').write_text('version = 3\n\n[[package]]\nname = "app"\nversion = "0.1.0"\ndependencies = ["core"]\n\n[[package]]\nname = "core"\nversion = "0.1.0"\n')
    (root/'.devtool.toml').write_text('''[project]
runner = "python"
[workspace]
default_target = "app"
[wrapper]
schema = 2
[targets.app]
runner = "python"
root = "."
[targets.app.jobs.fmt]
runner="command"
command=["cargo","fmt","--all","--","--check"]
[targets.app.jobs.clippy]
runner="command"
command=["cargo","clippy","--workspace","--all-targets","--offline","--locked","--","-D","warnings"]
[targets.app.jobs.test]
runner="command"
command=["cargo","test","--workspace","--offline","--locked"]
[targets.app.jobs.build]
runner="command"
command=["cargo","build","--workspace","--release","--offline","--locked"]
[targets.app.jobs.package]
runner="command"
command=["cargo","package","-p","app","--offline","--locked"]
outputs=["target/package/app-0.1.0.crate"]
[targets.app.workflows]
ci = [{id="fmt",ref="job:fmt",depends_on=[]},{id="clippy",ref="job:clippy",depends_on=["fmt"]},{id="test",ref="job:test",depends_on=["clippy"]},{id="build",ref="job:build",depends_on=["test"]},{id="package",ref="job:package",depends_on=["build"]}]
''')
    (root/'devtoolw').write_text('#!/bin/sh\n')
    git(root,'init','-q');git(root,'add','.');git(root,'-c','user.name=Fixture','-c','user.email=fixture@local','commit','-qm','fixture')
    return root


def test_workspace_graph_lock_source_features_and_cycle(tmp_path):
    root=fixture(tmp_path)
    plan=cargo_workspace(root)
    assert plan['topological_members']==['crates/core','crates/app']
    assert plan['edges']['crates/app']==['crates/core']
    assert len(plan['source_sha256'])==4
    assert set(plan['features'])=={'app','core'}
    with pytest.raises(CargoError,match='feature'):
        inspect_cargo_job(root,['cargo','test','--workspace','--offline','--locked'],{'ci_rust_features':'missing'})
    p=root/'crates/core/Cargo.toml';p.write_text(p.read_text()+'\n[dependencies]\napp={path="../app"}\n')
    with pytest.raises(CargoError,match='cycle'):cargo_workspace(root)


def test_script_toolchain_network_and_escape_refusals(tmp_path):
    root=fixture(tmp_path);app=root/'crates/app/Cargo.toml'
    with pytest.raises(CargoError,match='unreviewed'):
        inspect_cargo_job(root,['cargo','publish'],{})
    with pytest.raises(CargoError,match='args'):
        inspect_cargo_job(root,['cargo','clippy','--workspace'],{})
    with pytest.raises(CargoError,match='cross'):
        inspect_cargo_job(root,['cargo','build','--workspace','--release','--offline','--locked'],{'ci_rust_target':'aarch64-unknown-linux-gnu'})
    with pytest.raises(CargoError,match='toolchain matrix'):
        inspect_cargo_job(root,['cargo','test','--workspace','--offline','--locked'],{'ci_rust_versions':'stable'})
    app.write_text(app.read_text().replace('path = "../core"','path = "../../../outside"'))
    with pytest.raises(CargoError,match='escapes'):cargo_workspace(root)
    app.write_text(app.read_text().replace('../../../outside','../core')+'\n[package.metadata.fake]\n')
    (root/'crates/app/build.rs').write_text('fn main() {}')
    git(root,'add','.')
    with pytest.raises(CargoError,match='build.rs'):cargo_workspace(root)


def test_pinned_multi_job_and_full_execution_constraints(tmp_path):
    root=fixture(tmp_path)
    plan=provider_plan(root,target='app',workflow='ci',devtool_sha=PIN)
    assert plan['providers']==['rust-cargo'] and len(plan['nodes'])==5
    compiled=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    assert len(compiled['jobs'])==5 and compiled['jobs'][-1]['needs']==[compiled['jobs'][-2]['id']]
    yaml=workflow_yaml(compiled)
    assert 'RUSTUP_AUTO_INSTALL: "0"' in yaml and 'rustup run 1.85.1 cargo --version' in yaml
    assert 'CARGO_NET_OFFLINE: "true"' in yaml
    with pytest.raises(ProviderError,match='multi-job'):
        provider_export(root,target='app',workflow='ci',devtool_sha=PIN)
    assert provider_export(root,target='app',workflow='ci',devtool_sha=PIN,mode='multi-job')['status']=='REVIEW_ONLY'
    assert not git(root,'status','--porcelain')


def _fake_crate(file, *, manifest=b'[package]\nname="app"\nversion="0.1.0"\n'):
    with tarfile.open(file,'w:gz') as t:
        data=tarfile.TarInfo('app-0.1.0/Cargo.toml');data.size=len(manifest)
        t.addfile(data,io.BytesIO(manifest))


def test_crate_structure_and_identity_and_binary_triple(tmp_path):
    path=tmp_path/'app-0.1.0.crate';_fake_crate(path)
    assert verify_crate(path,name='app',version='0.1.0')['structure_verified']
    _fake_crate(path,manifest=b'[package]\nname="wrong"\nversion="0.1.0"\n')
    with pytest.raises(CargoError,match='identity'):verify_crate(path,name='app',version='0.1.0')
    elf=bytearray(64);elf[:4]=b'\x7fELF';elf[4]=2;elf[5]=1;elf[18:20]=struct.pack('<H',62)
    binary=tmp_path/'a';binary.write_bytes(elf)
    assert verify_rust_binary(binary,target='x86_64-unknown-linux-gnu')['format'].startswith('ELF64')
    elf[18:20]=struct.pack('<H',183);binary.write_bytes(elf)
    with pytest.raises(CargoError,match='architecture'):verify_rust_binary(binary,target='x86_64-unknown-linux-gnu')


def test_cargo_json_failures_not_promoted(tmp_path):
    p=tmp_path/'cargo.json';p.write_text('{"reason":"compiler-artifact"}\n{"reason":"build-finished","success":true}\n')
    assert not verify_cargo_json(p)['is_test_pass']
    p.write_text('{"reason":"test","event":"failed"}\n{"reason":"build-finished","success":true}\n')
    with pytest.raises(CargoError,match='failed'):verify_cargo_json(p)
    p.write_text('{"reason":"build-finished","success":false}\n')
    with pytest.raises(CargoError,match='failed'):verify_cargo_json(p)


def test_native_rust_job_handoff_and_invalid_crate_refusal(tmp_path,monkeypatch):
    from devtool import runners
    root=fixture(tmp_path)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','9021');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    def run(cfg,specs,**kwargs):
        assert specs[0].env['CARGO_NET_OFFLINE']=='true'
        assert specs[0].env['RUSTUP_TOOLCHAIN']=='1.85.1'
        artifact=root/'target/package/app-0.1.0.crate';artifact.parent.mkdir(parents=True,exist_ok=True)
        _fake_crate(artifact)
        return [SimpleNamespace(returncode=0,skipped=False)],''
    monkeypatch.setattr(runners,'run_specs',run)
    selected=graph['jobs'][-1]
    # Package requires verified upstream transfer. With no handoff outputs,
    # it can execute after the native needs edge without importing other bytes.
    result=run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
                    job_id=selected['id'],inputs=tmp_path/'inputs',outputs=tmp_path/'out')
    assert result['status']=='LOCAL_JOB_SUCCEEDED' and result['rust_receipts'][0]['structure_verified']
    assert (tmp_path/'out'/selected['id']/'payload/target/package/app-0.1.0.crate').is_file()


def test_pinned_features_and_version_fanout(tmp_path):
    root=fixture(tmp_path)
    argv=['cargo','test','--workspace','--features','app/extra','--offline','--locked']
    row=inspect_cargo_job(root,argv,{'ci_rust_features':'app/extra','ci_rust_versions':'1.85.1,1.86.0'})
    assert row['selected_features']==['app/extra'] and len(row['matrix_versions'])==2
    with pytest.raises(CargoError,match='matching explicit'):
        inspect_cargo_job(root,argv,{'ci_rust_features':'core/extra'})
    with pytest.raises(CargoError,match='metadata'):
        inspect_cargo_job(root,argv,{})
    (root/'.cargo').mkdir();(root/'.cargo/config.toml').write_text('[alias]\nevil = "run --example evil"\n')
    git(root,'add','.')
    with pytest.raises(CargoError,match='config/runner'):
        cargo_workspace(root)


def test_r1_workspace_inheritance_local_patch_and_safe_cross_elf(tmp_path):
    root=fixture(tmp_path)
    root_toml=root/'Cargo.toml'
    root_toml.write_text(root_toml.read_text()+'''\n[workspace.package]\nversion="0.1.0"\n[workspace.dependencies]\ncore = { path="crates/core" }\n[patch.crates-io]\ncore = { path="crates/core" }\n''')
    app=root/'crates/app/Cargo.toml'
    app.write_text(app.read_text().replace('version="0.1.0"','version={workspace=true}').replace('core = { path = "../core" }','core = { workspace = true }'))
    git(root,'add','.')
    reviewed=cargo_workspace(root)
    assert reviewed['members']['crates/app']['version']=='0.1.0'
    assert reviewed['edges']['crates/app']==['crates/core']
    root_toml.write_text(root_toml.read_text().replace('path="crates/core"','git="https://evil.invalid/x"'))
    with pytest.raises(CargoError):cargo_workspace(root)
    root_toml.write_text(root_toml.read_text().replace('git="https://evil.invalid/x"','path="crates/core"'))
    tool=root/'rust-toolchain.toml'
    tool.write_text(tool.read_text().replace('targets = ["x86_64-unknown-linux-gnu"]','targets = ["x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu"]'))
    arg=['cargo','build','--workspace','--release','--offline','--locked','--target','aarch64-unknown-linux-gnu']
    row=inspect_cargo_job(root,arg,{'ci_rust_target':'aarch64-unknown-linux-gnu'})
    assert row['target_triple']=='aarch64-unknown-linux-gnu'
    binary=bytearray(64);binary[:4]=b'\x7fELF';binary[4]=2;binary[5]=1;binary[18:20]=struct.pack('<H',183)
    elf=root/'aarch64-test';elf.write_bytes(binary)
    assert verify_rust_binary(elf,target=row['target_triple'])['format']=='ELF64-little-aarch64'
    with pytest.raises(CargoError,match='architecture'):verify_rust_binary(elf,target='x86_64-unknown-linux-gnu')


def test_r1_feature_matrix_multi_job_isolation(tmp_path,monkeypatch):
    from devtool.actions_rm10_cargo import feature_cell_argv
    root=fixture(tmp_path)
    cfg=root/'.devtool.toml'
    text=cfg.read_text()
    # Reviewed metadata belongs to EACH native Cargo job; one absent cell
    # invalidates the deterministic fanout instead of mixing producer outputs.
    text=text.replace('runner="command"', 'runner="command"\nmetadata={ci_rust_feature_sets="none;app/extra;core/extra",ci_rust_versions="1.85.1,1.86.0"}')
    cfg.write_text(text)
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    assert len(graph['jobs'])==30
    assert len({row['id'] for row in graph['jobs']})==30
    assert len({row['matrix_cell'] for row in graph['jobs']})==6
    for cell in {x['matrix_cell'] for x in graph['jobs']}:
        scoped={row['id'] for row in graph['jobs'] if row['matrix_cell']==cell}
        assert all(set(row['needs'])<=scoped for row in graph['jobs'] if row['matrix_cell']==cell)
    assert feature_cell_argv(['cargo','test','--workspace','--offline','--locked'],'app/extra')==['cargo','test','--workspace','--features','app/extra','--offline','--locked']
    with pytest.raises(CargoError,match='feature cell'):
        feature_cell_argv(['cargo','test','--workspace','--offline','--locked'],'../../escape')
    cfg.write_text(text.replace('ci_rust_feature_sets="none;app/extra;core/extra"','ci_rust_feature_sets="none;unknown/extra"'))
    with pytest.raises(ProviderError):compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)


def test_r1_cargo_native_json_normalization_and_failure(tmp_path):
    from devtool.actions_rm10_cargo import verify_native_cargo_output
    source={'Cargo.lock':'a'*64}
    raw='\n'.join([json.dumps({'reason':'compiler-artifact','filenames':['target/debug/demo'],'features':['extra']}),
        json.dumps({'reason':'build-finished','success':True}),
        'test result: ok. 2 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.00s'])+'\n'
    kwargs=dict(operation='test',source_sha256=source,version='1.85.1',run_id='17',attempt='2',expected_features=['app/extra'],target='x86_64-unknown-linux-gnu')
    report=verify_native_cargo_output(raw,**kwargs)
    assert report['test_passed']==2 and report['run_id']=='17' and report['attempt']=='2'
    assert report['release_qualified'] is False
    with pytest.raises(CargoError,match='no passing tests'):
        verify_native_cargo_output(raw.replace('2 passed','0 passed'),**kwargs)
    with pytest.raises(CargoError,match='Rust test failure'):
        verify_native_cargo_output(raw.replace('ok. 2 passed; 0 failed','FAILED. 1 passed; 1 failed'),**kwargs)
    with pytest.raises(CargoError,match='missing successful'):
        verify_native_cargo_output(raw.replace('{"reason": "build-finished", "success": true}',''),**kwargs)


def test_r1_native_cargo_json_execution_and_attempt_lineage(tmp_path,monkeypatch):
    from devtool import runners
    root=fixture(tmp_path)
    cfg=root/'.devtool.toml'
    cfg.write_text(cfg.read_text().replace('[targets.app.jobs.test]\nrunner="command"\ncommand=["cargo","test","--workspace","--offline","--locked"]',
        '[targets.app.jobs.test]\nrunner="command"\nmetadata={ci_rust_cargo_json=true}\ncommand=["cargo","test","--workspace","--offline","--locked","--message-format=json"]'))
    graph=compile_graph(root,target='app',workflow='ci',devtool_sha=PIN)
    selected=next(j for j in graph['jobs'] if j['rust_command']=='test')
    assert selected['rust_json_capture'] is True
    monkeypatch.setenv('GITHUB_SHA',git(root,'rev-parse','HEAD'))
    monkeypatch.setenv('GITHUB_RUN_ID','9876');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','3')
    emitted=[json.dumps({'reason':'compiler-artifact','filenames':['target/debug/app'],'features':[]}),
             json.dumps({'reason':'build-finished','success':True}),
             'test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s']
    def passed(config,specs,**kwargs):
        assert specs[0].env['RUSTFLAGS']==''
        assert specs[0].env['RUSTUP_TOOLCHAIN']=='1.85.1'
        assert specs[0].argv[-1]=='--message-format=json'
        return [SimpleNamespace(returncode=0,skipped=False,output='\n'.join(emitted))],''
    monkeypatch.setattr(runners,'run_specs',passed)
    result=run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
        job_id=selected['id'],inputs=tmp_path/'inputs',outputs=tmp_path/'outputs')
    assert result['rust_test_evidence'][0]['test_passed']==3
    assert result['rust_test_evidence'][0]['run_id']=='9876'
    assert result['rust_test_evidence'][0]['attempt']==3
    def forged(config,specs,**kwargs):
        return [SimpleNamespace(returncode=0,skipped=False,output='\n'.join(emitted).replace('3 passed','0 passed'))],''
    monkeypatch.setattr(runners,'run_specs',forged)
    with pytest.raises(ProviderError,match='no passing tests'):
        run_node(root,target='app',workflow='ci',devtool_sha=PIN,graph_sha=graph['graph_sha256'],
            job_id=selected['id'],inputs=tmp_path/'inputs',outputs=tmp_path/'outputs')


def test_r1_cargo_feature_graph_does_not_allow_undocumented_resolution(tmp_path):
    root=fixture(tmp_path)
    cargo=root/'crates/core/Cargo.toml'
    cargo.write_text(cargo.read_text().replace('extra=[]','extra=["missing"]'))
    with pytest.raises(CargoError,match='unknown Cargo feature'):cargo_workspace(root)
    cargo.write_text(cargo.read_text().replace('extra=["missing"]','extra=["helper"]\nhelper=["extra"]'))
    with pytest.raises(CargoError,match='cycle'):cargo_workspace(root)


def test_r1_source_attested_json_mode_and_unreviewed_arg_refused(tmp_path):
    root=fixture(tmp_path)
    argv=['cargo','test','--workspace','--offline','--locked','--message-format=json']
    assert inspect_cargo_job(root,argv,{'ci_rust_cargo_json':True})['cargo_json_capture']
    with pytest.raises(CargoError,match='reviewed offline'):
        inspect_cargo_job(root,argv,{})
    with pytest.raises(CargoError,match='explicit compiler JSON'):
        inspect_cargo_job(root,argv[:-1],{'ci_rust_cargo_json':True})
    with pytest.raises(CargoError,match='feature matrix'):
        inspect_cargo_job(root,argv[:-1],{'ci_rust_feature_sets':'none;app/extra;core/extra;app/extra'})
