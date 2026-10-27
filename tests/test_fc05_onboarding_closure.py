"""FC05 end-to-end reviewed whole-repository onboarding, no project execution."""
import hashlib
import json
from pathlib import Path
import subprocess
import pytest
from devtool.onboarding import inventory, apply, OnboardingBlocked
from tests.test_rm20_onboarding import repo, reviewed, git
from devtool.config import load_project_config
from devtool.workflow_runtime import plan_workspace_targets


def mixed(tmp_path, *, ci=False):
    entries={'go/go.mod':'module example.org/mygo\n',
             'desktop/Main.csproj':'<Project />',
             'native/CMakeLists.txt':'cmake_minimum_required(VERSION 3.25)',
             'README.md':'shared source'}
    if ci:
        entries['.github/workflows/custom.yml']='name: untouched\non: push\njobs: {}\n'
    root=repo(tmp_path, entries)
    return root


def test_inventory_refuses_stub_and_supplies_review_starter(tmp_path):
    root=mixed(tmp_path,ci=True)
    report=inventory(root)
    assert report['status']=='REVIEW_REQUIRED'
    assert report['safe_to_apply'] is False
    assert set(t['runner'] for t in report['targets'])=={'go','dotnet','cmake'}
    assert 'full job/DAG' in ' '.join(report['review_issues'])
    assert report['review_template']['source']==report['source']
    assert not (root/'.devtool.toml').exists()
    assert git(root,'status','--porcelain','--untracked-files=all')==''


def test_reviewed_mixed_repo_ci_retained_and_graphs_valid(tmp_path):
    root=mixed(tmp_path,ci=True); before=git(root,'rev-parse','HEAD')
    original=(root/'.github/workflows/custom.yml').read_bytes()
    spec=reviewed(root)
    plan=inventory(root,review_spec=spec)
    assert plan['status']=='READY' and plan['safe_to_apply']
    assert len(plan['validated_dag'])==3
    assert all(info['node_count']>0 for info in plan['validated_dag'].values())
    assert plan['existing_actions']['policy']=='retain-existing'
    assert set(plan['inventory']['unowned_paths'])=={'README.md','.github/workflows/custom.yml'}
    assert plan['authority']['signing']=='PENDING'
    result=apply(root,reviewed_sha256=plan['review_sha256'],review_spec=spec,yes=True)
    assert result['status']=='APPLIED' and result['commit_count']==1
    assert git(root,'rev-parse','HEAD^')==before
    assert set(git(root,'show','--format=','--name-only','HEAD').splitlines())=={'.devtool.toml','devtoolw','devtoolw.cmd'}
    assert (root/'.github/workflows/custom.yml').read_bytes()==original
    assert git(root,'status','--porcelain','--untracked-files=all')==''
    cfg=load_project_config(str(root),str(root/'.devtool.toml'),allow_parent_config=False,allow_ambiguous_target=True)
    assert set(cfg.targets)=={'go','desktop','native'}
    for name in cfg.targets:
        assert plan_workspace_targets(cfg,[name],'ci').nodes
    assert not inventory(root,review_spec=spec)['safe_to_apply']
    with pytest.raises(OnboardingBlocked):
        apply(root,reviewed_sha256=plan['review_sha256'],review_spec=spec,yes=True)


def test_review_spec_digest_and_source_are_bound(tmp_path):
    root=mixed(tmp_path); spec=reviewed(root)
    plan=inventory(root,review_spec=spec)
    data=json.loads(spec.read_text());data['decisions'].append('Additional reviewer note')
    spec.write_text(json.dumps(data))
    assert inventory(root,review_spec=spec)['review_sha256']!=plan['review_sha256']
    with pytest.raises(OnboardingBlocked,match='reviewed plan drift'):
        apply(root,reviewed_sha256=plan['review_sha256'],review_spec=spec,yes=True)
    git(root,'commit','--allow-empty','-qm','concurrent')
    with pytest.raises(OnboardingBlocked,match='source.*drift'):
        inventory(root,review_spec=spec)


@pytest.mark.parametrize('mutate,reason',[
    (lambda d:d.update({'unowned_paths':[]}), 'ownership'),
    (lambda d:d['config_toml'].replace('unused','x'), None),
])
def test_review_declared_ownership_changes_refused(tmp_path,mutate,reason):
    root=mixed(tmp_path);spec=reviewed(root)
    data=json.loads(spec.read_text())
    mutate(data)
    # second mutation is a neutral control proving a complete spec can still plan
    spec.write_text(json.dumps(data))
    if reason:
        with pytest.raises(OnboardingBlocked,match=reason):inventory(root,review_spec=spec)
    else:
        assert inventory(root,review_spec=spec)['safe_to_apply']


def test_existing_ci_inventory_must_be_complete_and_hash_match(tmp_path):
    root=mixed(tmp_path,ci=True);spec=reviewed(root)
    data=json.loads(spec.read_text())
    data['existing_actions']['files']={}
    spec.write_text(json.dumps(data))
    with pytest.raises(OnboardingBlocked,match='workflows must be reviewed'):
        inventory(root,review_spec=spec)
    data['existing_actions']['files']={'.github/workflows/custom.yml':'0'*64}
    spec.write_text(json.dumps(data))
    with pytest.raises(OnboardingBlocked,match='workflow bytes changed'):
        inventory(root,review_spec=spec)


def test_no_job_no_scope_no_wrapper_no_workflow_refused(tmp_path):
    root=mixed(tmp_path);spec=reviewed(root)
    data=json.loads(spec.read_text())
    config=data['config_toml']
    for modified in (config.replace('output_scopes = [{kind="run-temp",path="fixture-output"}]',''),
                     config.replace('ci = ["job:validate-go"]','ci = []'),
                     config.split('[wrapper.commands.ci-go]')[0]):
        data['config_toml']=modified;spec.write_text(json.dumps(data))
        with pytest.raises(OnboardingBlocked): inventory(root,review_spec=spec)


def test_review_symlink_and_unsupported_marker_fail_closed(tmp_path):
    root=mixed(tmp_path);spec=reviewed(root)
    alias=root.parent/'alias.json'; alias.symlink_to(spec)
    with pytest.raises(OnboardingBlocked,match='non-symlink'):
        inventory(root,review_spec=alias)
    (root/'unknown').mkdir();(root/'unknown/build.sbt').write_text('name := "blocked"')
    git(root,'add','.');git(root,'commit','-qm','unknown project')
    plan=inventory(root)
    assert plan['status']=='REVIEW_REQUIRED' and not plan['safe_to_apply']
    assert inventory(root,review_spec=spec)["safe_to_apply"] is False


def test_concurrent_wrapper_creation_kept_and_apply_does_not_claim_success(tmp_path,monkeypatch):
    root=mixed(tmp_path);spec=reviewed(root);plan=inventory(root,review_spec=spec)
    old=git(root,'rev-parse','HEAD')
    from devtool import onboarding as on
    original=on.os.open
    def race(path,flags,*args,**kwargs):
        if Path(path)==root/'devtoolw' and flags & on.os.O_EXCL:
            (root/'devtoolw').write_text('foreign wrapper\n')
        return original(path,flags,*args,**kwargs)
    monkeypatch.setattr(on.os,'open',race)
    with pytest.raises(OnboardingBlocked,match='concurrent config or wrapper'):
        apply(root,reviewed_sha256=plan['review_sha256'],review_spec=spec,yes=True)
    assert (root/'devtoolw').read_text()=='foreign wrapper\n'
    assert git(root,'rev-parse','HEAD')==old
    assert (root/'.devtool.toml').exists()  # explicit partial, never hidden cleanup


def test_existing_reviewed_wrapper_pair_can_be_retained(tmp_path):
    root=mixed(tmp_path)
    from devtool.wrapper_launcher import expected_launcher_texts
    for n,b in expected_launcher_texts().items():
        (root/n).write_text(b)
        if n=='devtoolw': (root/n).chmod(0o755)
    git(root,'add','.');git(root,'commit','-qm','existing wrapper pair')
    spec=reviewed(root);data=json.loads(spec.read_text())
    data['wrapper_launchers']['policy']='retain-existing';spec.write_text(json.dumps(data))
    plan=inventory(root,review_spec=spec)
    assert plan['operations']==['add .devtool.toml']
    apply(root,reviewed_sha256=plan['review_sha256'],review_spec=spec,yes=True)
    assert git(root,'show','--format=','--name-only','HEAD')=='.devtool.toml'


def test_review_does_not_execute_declared_commands(tmp_path):
    root=mixed(tmp_path);spec=reviewed(root)
    data=json.loads(spec.read_text())
    data['config_toml']=data['config_toml'].replace('["echo", "reviewed-fixture"]',
                            '["sh", "-c", "touch EXECUTED_FROM_CONFIG"]')
    spec.write_text(json.dumps(data))
    plan=inventory(root,review_spec=spec)
    assert plan['safe_to_apply']
    assert not (root/'EXECUTED_FROM_CONFIG').exists()
    assert git(root,'status','--porcelain')==''
