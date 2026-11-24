"""DT-19/20: scope based on actual commit range; no live GitHub mutations."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import subprocess
import pytest

from devtool import delivery_github as github
from devtool.github_app import _PERMISSIONS, _check_git_argv, GitHubAppError


def git(root: Path, *args: str) -> str:
    p=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,check=True)
    return p.stdout.strip()


def make_repo(tmp_path:Path) -> tuple[Path,str]:
    root=tmp_path/'repo';root.mkdir()
    git(root,'init','-q')
    (root/'README.md').write_text('base\n')
    git(root,'add','.')
    git(root,'-c','user.name=Test','-c','user.email=test@example.test','commit','-qm','base')
    return root,git(root,'rev-parse','HEAD')


def commit(root:Path, path:str, content:str) -> str:
    p=root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
    git(root,'add','--',path)
    git(root,'-c','user.name=Test','-c','user.email=test@example.test','commit','-qm',path)
    return git(root,'rev-parse','HEAD')


def test_workflow_capability_is_exactly_contents_plus_workflows():
    assert _PERMISSIONS['branch_write_workflows']=={'contents':'write','workflows':'write'}
    assert 'workflows' not in _PERMISSIONS['branch_write']


def test_new_write_scope_allows_only_exact_ref_push_and_read_probe():
    ref='refs/heads/feature/guard'
    _check_git_argv(['push','--porcelain','origin',ref+':'+ref],'branch_write_workflows')
    _check_git_argv(['ls-remote','--exit-code','origin',ref],'branch_write_workflows')
    with pytest.raises(GitHubAppError):
        _check_git_argv(['push','--porcelain','origin',ref+':refs/heads/main'],'branch_write_workflows')
    with pytest.raises(GitHubAppError):
        _check_git_argv(['fetch','--prune','origin'],'branch_write_workflows')
    with pytest.raises(GitHubAppError):
        _check_git_argv(['push','--porcelain','origin',ref+':'+ref],'repository_read')


def test_multicommit_workflow_diff_requires_scope_even_if_last_commit_unrelated(tmp_path):
    root,base=make_repo(tmp_path)
    workflow=commit(root,'.github/workflows/check.yml','name: check\n')
    newest=commit(root,'readme-other.txt','not a workflow\n')
    assert github._workflow_changes(str(root),base,newest) is True
    assert github._workflow_changes(str(root),workflow,newest) is False
    assert github._workflow_changes(str(root),None,newest) is True


def test_nonworkflow_range_does_not_request_extra_scope(tmp_path):
    root,base=make_repo(tmp_path)
    newest=commit(root,'src/data.txt','content\n')
    assert github._workflow_changes(str(root),base,newest) is False
    assert github._workflow_changes(str(root),None,newest) is False


def test_wrong_source_ref_is_not_taken_from_caller_and_no_intent_on_preflight(monkeypatch,tmp_path):
    root,base=make_repo(tmp_path)
    target=commit(root,'.github/workflows/ci.yml','name: check\n')
    observed=[]
    class Provider:
        def issue(self,repo,capability):
            observed.append(capability)
            if capability=='branch_write_workflows':
                raise GitHubAppError('APP_INSTALLATION_PERMISSION_MISSING')
            return SimpleNamespace(repository=repo,capability=capability,expires_at=4_000_000_000)
    from devtool import github_app
    monkeypatch.setattr(github_app.AppInstallationProvider,'from_environment',lambda : Provider())
    monkeypatch.setattr(github_app,'run_git_authenticated',lambda repo,argv,access,timeout=150:
        SimpleNamespace(returncode=0,stdout=base+'\trefs/heads/feature/hotfix\n',stderr=''))
    monkeypatch.setattr(github,'_checked',lambda sid,state_root=None:{'repo_root':str(root),
        'repository':'Example/repo','head':'feature/hotfix','head_sha':target})
    monkeypatch.setattr(github,'record_intent',lambda *a,**kw:pytest.fail('journal must not be written'))
    with pytest.raises(GitHubAppError,match='APP_INSTALLATION_PERMISSION_MISSING'):
        github.push('example-session',yes=True,auth='app')
    assert observed==['repository_read','branch_write_workflows']
