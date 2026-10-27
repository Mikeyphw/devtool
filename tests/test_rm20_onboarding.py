"""RM20 real Git acceptance: in-place, review-bound, no guessed execution."""
from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

from devtool.onboarding import inventory, apply, OnboardingBlocked
from devtool.config import load_project_config
from devtool.affected import analyze_affected_targets
from devtool.cli import main


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def repo(tmp, entries=None):
    root = tmp/'consumer'; root.mkdir()
    git(root,'init','-q');git(root,'config','user.name','Test');git(root,'config','user.email','test@example.org')
    for path, content in (entries or {'backend/pyproject.toml':'[project]\nname="api"\nversion="1"\n',
                         'frontend/package.json':'{"name":"web","version":"1"}',
                         'README.md':'shared\n'}).items():
        target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content)
    git(root,'add','.');git(root,'commit','-qm','initial')
    return root




def reviewed(root, *, config_text=None):
    """Human-reviewed fully populated plan fixture, never executable discovery."""
    import json,hashlib
    base=inventory(root)
    rows=base['targets']
    raw=['[project]', f'runner = "{rows[0]["runner"]}"', f'platform = "{rows[0]["platform"]}"',
         '[workspace]', f'default_target = "{rows[0]["name"]}"']
    tracked=base['inventory']['all_tracked_paths']
    for row in rows:
        name=row['name']; first=next((x for x in tracked if row['root']=='.' or x.startswith(row['root']+'/')),None)
        assert first
        raw += [f'[jobs.validate-{name}]','runner = "command"','command = ["echo", "reviewed-fixture"]',
                f'inputs = ["{first}"]', 'output_scopes = [{kind="run-temp",path="fixture-output"}]']
    for row in rows:
        name=row['name']; raw += [f'[targets.{name}]', f'runner = "{row["runner"]}"',
            f'platform = "{row["platform"]}"', f'root = "{row["root"]}"',
            f'[targets.{name}.workflows]','ci = ["job:validate-'+name+'"]']
    for row in rows:
        name=row['name']; raw += [f'[wrapper.commands.ci-{name}]', 'workflow = "ci"',f'target = "{name}"']
    spec=base['review_template'];spec['config_toml']=config_text or '\n'.join(raw)+'\n'
    spec['decisions']=['Reviewed fixture command and explicit preservation of existing CI workflows']
    spec['host_toolchains']={row['name']:{'host':'linux','toolchain':'reviewed-fixture','qualification':'PENDING'} for row in rows}
    spec['existing_actions']['files']={x:hashlib.sha256((root/x).read_bytes()).hexdigest() for x in tracked if x.startswith('.github/workflows/') and x.endswith(('.yaml','.yml'))}
    review=root.parent/(root.name+'-review.json');review.write_text(json.dumps(spec))
    return review

def test_inplace_discovery_ownership_and_source_bound_commit(tmp_path):
    root=repo(tmp_path);before=git(root,'rev-parse','HEAD');spec=reviewed(root);plan=inventory(root,review_spec=spec)
    assert plan['status']=='READY'
    assert len(plan['targets'])==2
    assert plan['inventory']['unowned_paths']==['README.md']
    assert plan['inventory']['ownership']['backend/pyproject.toml']==['backend']
    assert plan['inventory']['ownership']['frontend/package.json']==['frontend']
    assert not (root/'.devtool.toml').exists()
    result=apply(root,reviewed_sha256=plan['review_sha256'],yes=True,review_spec=spec)
    assert result['status']=='APPLIED' and result['commit_count']==1
    assert git(root,'rev-parse','HEAD^')==before
    assert git(root,'status','--porcelain')==''
    assert set(git(root,'show','--format=','--name-only','HEAD').splitlines())=={'.devtool.toml','devtoolw','devtoolw.cmd'}
    cfg=load_project_config(str(root),str(root/'.devtool.toml'),allow_ambiguous_target=True,allow_parent_config=False)
    assert set(cfg.targets)=={'backend','frontend'}
    affected=analyze_affected_targets(cfg,changed_paths=['README.md'])
    assert set(affected.targets)=={'backend','frontend'}
    assert not inventory(root)['safe_to_apply']
    with pytest.raises(OnboardingBlocked,match='reviewed plan drift|review specification source'):apply(root,reviewed_sha256=plan['review_sha256'],yes=True,review_spec=spec)


def test_changed_head_refuses_stale_review(tmp_path):
    root=repo(tmp_path);spec=reviewed(root);plan=inventory(root,review_spec=spec)
    (root/'LICENSE').write_text('new');git(root,'add','.');git(root,'commit','-qm','unrelated')
    with pytest.raises(OnboardingBlocked,match='reviewed plan drift|review specification source'):
        apply(root,reviewed_sha256=plan['review_sha256'],yes=True,review_spec=spec)
    assert not (root/'.devtool.toml').exists()


def test_dirty_untracked_or_modified_refuses(tmp_path):
    root=repo(tmp_path);spec=reviewed(root);plan=inventory(root,review_spec=spec)
    (root/'scratch').write_text('do not alter')
    with pytest.raises(OnboardingBlocked,match='clean Git'): apply(root,reviewed_sha256=plan['review_sha256'],yes=True,review_spec=spec)
    assert (root/'scratch').read_text()=='do not alter'
    (root/'scratch').unlink();(root/'README.md').write_text('dirty')
    with pytest.raises(OnboardingBlocked,match='clean Git'):apply(root,reviewed_sha256=plan['review_sha256'],yes=True,review_spec=spec)


def test_requires_yes_and_exact_review(tmp_path):
    root=repo(tmp_path);spec=reviewed(root);plan=inventory(root,review_spec=spec)
    with pytest.raises(OnboardingBlocked,match='--yes'):apply(root,reviewed_sha256=plan['review_sha256'],yes=False)
    with pytest.raises(OnboardingBlocked,match='reviewed plan drift|review specification source'):apply(root,reviewed_sha256='1'*64,yes=True)
    assert git(root,'status','--porcelain')==''


def test_conflicting_root_and_nested_markers_review_only(tmp_path):
    root=repo(tmp_path,{'pyproject.toml':'[project]\n','apps/a/package.json':'{}'})
    plan=inventory(root)
    assert plan['status']=='REVIEW_REQUIRED' and not plan['proposal_text']
    assert any('overlaps' in issue for issue in plan['review_issues'])
    with pytest.raises(OnboardingBlocked):apply(root,reviewed_sha256='1'*64,yes=True)


def test_multiple_languages_same_root_require_review(tmp_path):
    root=repo(tmp_path,{'backend/pyproject.toml':'x','backend/go.mod':'module example.org/a\n'})
    assert inventory(root)['status']=='REVIEW_REQUIRED'


def test_symlink_and_submodule_mode_flagged(tmp_path):
    root=repo(tmp_path)
    (root/'alias').symlink_to('README.md');git(root,'add','alias');git(root,'commit','-qm','add symlink')
    plan=inventory(root)
    assert plan['status']=='REVIEW_REQUIRED'
    assert plan['inventory']['unsupported_git_modes']==['alias']


def test_existing_config_never_overwritten(tmp_path):
    root=repo(tmp_path)
    p=root/'.devtool.toml';p.write_text('[project]\nrunner="python"\nplatform="server"\n[workspace]\ndefault_target="backend"\n[targets.backend]\nrunner="python"\nroot="backend"\n')
    git(root,'add','.');git(root,'commit','-qm','configured')
    report=inventory(root)
    assert report['status']=='CONFIGURED' and not report['safe_to_apply']
    assert report['inventory']['unowned_paths']


def test_no_marker_reviews_instead_of_inventing_runner(tmp_path):
    root=repo(tmp_path,{'README.md':'hi'})
    assert inventory(root)['status']=='REVIEW_REQUIRED'


def test_cli_plan_and_apply(tmp_path,capsys):
    root=repo(tmp_path); spec=reviewed(root)
    assert main(['-r',str(root),'onboard','plan','--review-spec',str(spec),'--json'])==0
    captured=capsys.readouterr().out
    import json
    payload=json.loads(captured).get("data", json.loads(captured))
    assert payload['status']=='READY'
    assert main(['-r',str(root),'onboard','apply','--review-spec',str(spec),'--reviewed-sha256',payload['review_sha256'],'--yes','--json'])==0
    assert json.loads(capsys.readouterr().out)['status']=='APPLIED'


def test_cli_inventory_does_not_create_private_devtool_state(tmp_path,capsys):
    root=repo(tmp_path)
    assert main(['-r',str(root),'onboard','inventory'])==0
    import json
    assert json.loads(capsys.readouterr().out)['status']=='REVIEW_REQUIRED'
    assert git(root,'status','--porcelain','--untracked-files=all')==''
    assert not (root/'.devtool').exists()


def test_git_identity_failure_rolls_back_file_and_index(tmp_path):
    root=repo(tmp_path);spec=reviewed(root);plan=inventory(root,review_spec=spec)
    # No author/committer identity available; no new commit is allowed.
    git(root,'config','--unset','user.name');git(root,'config','--unset','user.email')
    import os
    keep={key:os.environ.get(key) for key in ['GIT_AUTHOR_NAME','GIT_AUTHOR_EMAIL','GIT_COMMITTER_NAME','GIT_COMMITTER_EMAIL']}
    for key in keep:
        os.environ.pop(key,None)
    original=git(root,'rev-parse','HEAD')
    try:
        with pytest.raises(OnboardingBlocked,match='Author identity unknown'):
            apply(root,reviewed_sha256=plan['review_sha256'],yes=True,review_spec=spec)
    finally:
        for key,value in keep.items():
            if value is not None:os.environ[key]=value
    assert git(root,'rev-parse','HEAD')==original
    assert git(root,'status','--porcelain','--untracked-files=all')==''
    assert not (root/'.devtool.toml').exists()


def test_ignored_config_refuses_without_creating_file(tmp_path):
    root=repo(tmp_path)
    (root/'.gitignore').write_text('.devtool.toml\n')
    git(root,'add','.gitignore');git(root,'commit','-qm','ignore config')
    spec=reviewed(root);plan=inventory(root,review_spec=spec)
    with pytest.raises(OnboardingBlocked,match='ignored'):
        apply(root,reviewed_sha256=plan['review_sha256'],yes=True,review_spec=spec)
    assert git(root,'status','--porcelain')==''
    assert not (root/'.devtool.toml').exists()


def test_detached_head_refused(tmp_path):
    root=repo(tmp_path);git(root,'checkout','--detach','-q')
    with pytest.raises(OnboardingBlocked,match='named branch'):inventory(root)


def test_existing_untracked_config_refuses_without_writing(tmp_path):
    root=repo(tmp_path)
    (root/'.devtool.toml').write_text('[project]\nkind="python"\n')
    with pytest.raises(OnboardingBlocked,match='clean Git'):
        apply(root,reviewed_sha256='2'*64,yes=True)
    assert (root/'.devtool.toml').read_text()=='[project]\nkind="python"\n'
