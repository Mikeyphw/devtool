from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests import w3_validation_support as w
from tests.w3_native_validate import TESTS

ROOT=Path(__file__).resolve().parents[1]


def _git(repo,*args):
    return subprocess.check_output(['git','-C',str(repo),*args],stderr=subprocess.STDOUT).decode().strip()


def _repo(tmp):
    repo=tmp/'repo';repo.mkdir();_git(repo,'init','-q');_git(repo,'config','user.name','Fixture');_git(repo,'config','user.email','fixture@invalid.example')
    (repo/'source.py').write_text('x=1\n');_git(repo,'add','.');_git(repo,'commit','-qm','base');return repo


def test_full_inventory_preserves_previous_gates_and_hosted_ci24():
    old=set()
    for prefix in ('ga00','ga01','ga02','w2'):
        tree=ast.parse((ROOT/'tests'/f'{prefix}_native_validate.py').read_text())
        old.update(ast.literal_eval(next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TESTS' for t in n.targets))))
    old.update(('tests/test_ga03_checks.py','tests/test_dt25_absent_push_recovery.py','tests/test_g0b_delivery_fork_and_provider.py','tests/test_native_delivery_continuity_m3.py','tests/test_native_delivery_contract_v3.py','tests/test_core_x04_generated_surfaces.py','tests/test_complete_feature_guides.py'))
    assert old<=set(TESTS) and len(TESTS)==len(set(TESTS))
    workflow=(ROOT/'.github/workflows/devtool-ga-source-acceptance.yml').read_text()
    assert workflow.count('tests/w3_native_validate.py --profile full')==1
    assert all(f'tests/{p}_native_validate.py' not in workflow for p in ('ga00','ga01','ga02','w2'))
    ci24=(ROOT/'.github/workflows/devtool-full-repository-pytest.yml').read_text()
    assert '--shards 24' in ci24 and 'summarize' in ci24


def test_targeted_includes_direct_and_semantic_dependencies_and_mandatory():
    selected,profile=w.select_modules(ROOT,TESTS,{'devtool/delivery_cli.py','devtool/m6_live.py','devtool/ga00_p16_selection.py','devtool/overlay/recovery.py','tests/test_ga00_p16_selection.py'})
    assert profile=='targeted'
    stems={Path(p).stem for p in selected}
    assert set(w.MANDATORY)<=stems
    assert {'test_g0c2_operator_status','test_dt_hotfix_m6','test_m6_live','test_ga00_p16_selection','test_overlay_r06d_recovery_plan'}<=stems
    assert len(selected)<len(TESTS)


def test_unknown_shared_change_expands_and_empty_is_not_success():
    for changes in ({'devtool/cli.py'},set(),{'pyproject.toml'}):
        selected,profile=w.select_modules(ROOT,TESTS,changes)
        assert profile=='standard-expanded' and set(TESTS)<=set(selected)
    with pytest.raises(RuntimeError,match='MISSING'):
        w.select_modules(ROOT,(*TESTS,'tests/test_missing_w3.py'),{'devtool/delivery_cli.py'})


def test_source_changes_invalidate_even_untracked_and_mode(tmp_path):
    repo=_repo(tmp_path);s=w.SourceIdentity(repo);a=s.digest(fresh=True)
    assert s.digest()==a
    file=repo/'source.py';file.write_text('x=2\n');assert s.digest()!=a
    file.write_text('x=1\n');assert s.digest()==a
    (repo/'extra.py').write_text('x=1\n');assert s.digest()!=a
    (repo/'extra.py').unlink();assert s.digest()==a
    file.chmod(0o755);assert s.digest()!=a


def test_checkpoint_strict_identity_and_corruption(tmp_path):
    path=tmp_path/'receipt.json';row={'schema':w.SCHEMA,'key':'a','module':'tests/test_x.py','status':'PASS','exit_code':0,'counts':{'tests':3,'failures':0,'errors':0,'skipped':1},'seconds':0.2}
    w._write(path,row);assert w._read_pass(path,'a',row['module'])==row
    assert w._read_pass(path,'b',row['module']) is None
    path.write_text(path.read_text().replace('"tests": 3','"tests": 4'));assert w._read_pass(path,'a',row['module']) is None
    row['counts']['skipped']=3;w._write(path,row);assert w._read_pass(path,'a',row['module']) is None
    path.unlink();path.symlink_to(tmp_path/'missing');assert w._read_pass(path,'a',row['module']) is None


def test_module_receipts_survive_failure_and_reuse_exact_source(tmp_path,monkeypatch):
    repo=_repo(tmp_path);monkeypatch.setenv('DEVTOOL_XDG_DIR',str(tmp_path/'state'))
    monkeypatch.delenv('DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT',raising=False)
    names=['tests/test_a.py','tests/test_b.py'];monkeypatch.setattr(w,'select_modules',lambda *a,**k:(names,'targeted'))
    monkeypatch.setattr(w,'runtime_identity',lambda env:'runtime');calls=[];fail=True
    def run(root,name,scratch,fd):
        calls.append(name);bad=fail and name==names[1]
        return (1 if bad else 0),{'tests':2,'failures':int(bad),'errors':0,'skipped':0},0.1
    monkeypatch.setattr(w,'_run_module',run)
    assert w.run_local(repo,names)==1 and calls==names
    fail=False;calls.clear();assert w.run_local(repo,names)==0 and calls==names[1:]
    calls.clear();assert w.run_local(repo,names)==0 and calls==[]
    (repo/'source.py').write_text('x=2\n');assert w.run_local(repo,names)==0 and calls==names


def test_source_drift_during_module_never_checkpoints(tmp_path,monkeypatch):
    repo=_repo(tmp_path);monkeypatch.setenv('DEVTOOL_XDG_DIR',str(tmp_path/'state'));monkeypatch.delenv('DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT',raising=False)
    names=['tests/test_a.py'];monkeypatch.setattr(w,'select_modules',lambda *a,**k:(names,'targeted'));monkeypatch.setattr(w,'runtime_identity',lambda env:'runtime')
    def mutate(*a):
        (repo/'source.py').write_text('x=9\n');return 0,{'tests':2,'failures':0,'errors':0,'skipped':0},0.1
    monkeypatch.setattr(w,'_run_module',mutate)
    with pytest.raises(RuntimeError,match='SOURCE_DRIFT'):w.run_local(repo,names)
    assert not list((tmp_path/'state').rglob('*.json'))


def test_surviving_child_keeps_validation_lease(tmp_path):
    folder=tmp_path/'private'/'repo';proc=None
    try:
        with w.lease(folder) as fd:
            proc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],pass_fds=(fd,))
        with pytest.raises(RuntimeError,match='WORKER_STILL_ACTIVE'):
            with w.lease(folder):pass
    finally:
        if proc:proc.terminate();proc.wait(timeout=5)
    with w.lease(folder):pass


def test_real_module_execution_and_counts(tmp_path):
    repo=_repo(tmp_path);(repo/'test_ok.py').write_text('def test_ok():\n    assert True\n')
    (repo/'conftest.py').write_text('def pytest_addoption(parser):\n    parser.addoption("--devtool-lane")\n')
    scratch=tmp_path/'scratch';scratch.mkdir()
    with w.lease(tmp_path/'private'/'repo') as fd:
        rc,counts,seconds=w._run_module(repo,'test_ok.py',scratch,fd)
    assert rc==0 and counts['tests']==1 and counts['failures']==0 and seconds>=0


def test_junit_local_parser_fails_closed_and_counts_all_outcomes(tmp_path):
    report = tmp_path/'report.xml'
    assert w.junit_summary(report)['errors'] == 1
    for invalid in ('', '<broken', '<unrelated><testcase/></unrelated>'):
        report.write_text(invalid)
        assert w.junit_summary(report)['errors'] == 1
    report.write_text('<testsuites><testsuite><testcase/><testcase><failure/></testcase>'
                      '<testcase><error/></testcase><testcase><skipped/></testcase>'
                      '<testcase><error/><failure/><skipped/></testcase></testsuite></testsuites>')
    assert w.junit_summary(report) == dict(tests=5, failures=2, errors=2, skipped=2)
    report.write_text('<testsuite><testcase/></testsuite>')
    assert w.junit_summary(report) == dict(tests=1, failures=0, errors=0, skipped=0)
    report.write_text('<testsuite/>')
    assert w.junit_summary(report)['tests'] == 0


def test_validator_bootstrap_with_shadowing_regular_tools_package(tmp_path):
    shadow = tmp_path/'tools'; shadow.mkdir()
    (shadow/'__init__.py').write_text('MARKER = "unrelated installed package"\n')
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT), str(tmp_path))))
    result = subprocess.run([sys.executable, '-B', '-c',
        'import tools; assert tools.MARKER == "unrelated installed package"; '
        'from tests.w3_validation_support import junit_summary; '
        'assert callable(junit_summary)'], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_reviewed_package_scope_requires_exact_changed_set(tmp_path,monkeypatch):
    repo=_repo(tmp_path);monkeypatch.setenv('DEVTOOL_XDG_DIR',str(tmp_path/'state'));monkeypatch.delenv('DEVTOOL_TRANSACTION_PRIMARY_REPO_ROOT',raising=False)
    (repo/'source.py').write_text('x=2\n');names=['tests/test_a.py'];calls=[]
    def select(root,inventory,changes,**kwargs):
        calls.append(set(changes));return names,'standard-expanded'
    monkeypatch.setattr(w,'select_modules',select);monkeypatch.setattr(w,'runtime_identity',lambda env:'runtime')
    monkeypatch.setattr(w,'_run_module',lambda *a:(0,{'tests':1,'failures':0,'errors':0,'skipped':0},.1))
    assert w.run_local(repo,names,reviewed_changes={'source.py'})==0 and calls[-1]==set()
    (repo/'extra.py').write_text('x=3\n')
    assert w.run_local(repo,names,reviewed_changes={'source.py'})==0 and calls[-1]=={'source.py','extra.py'}


# Hosted source gates must not silently lose optional-but-required test tools.
# Check structured steps and exact dependency tokens, not text in comments.
HOSTED_LANES = (
    ('devtool-ga-source-acceptance.yml', 'ga-source-contracts', 'Unified full W3 source acceptance', '.'),
    ('devtool-full-repository-pytest.yml', 'pytest', 'Run assigned modules with bounded subprocesses and live output', '.'),
    ('devtool-rm15-regression-source-gate.yml', 'rm15-strict-regression', 'Verify exact source-bound audit regressions', '.'),
    ('devtool-rm17-polyglot-source-gate.yml', 'rm17-source', 'Provider, DAG and hostile source fixtures (no local Gradle build)', '.'),
    ('devtool-rm21-hosted-polyglot-acceptance.yml', 'hosted-fixture', 'Pure source, archive, and hostile provider regressions', '.'),
    ('devtool-rm21-hosted-polyglot-acceptance.yml', 'independent-consumer', 'Native exact-SHA external consumer acceptance (no release)', './tool'),
    ('devtool-rm22-hosted-env-source.yml', 'source-regression', 'No-execution hosted environment and RM21 regressions', '.'),
)
HOSTED_REQUIREMENTS = {'setuptools>=75,<82', 'wheel', 'coverage>=7,<9',
                       'cryptography>=43,<47', 'pytest', 'pexpect'}
GA_PREFLIGHT = 'Verify hosted test dependencies before acceptance'
GA_IMPORTS = 'import coverage, cryptography, pexpect, pytest, textual'


def _hosted_install_tokens(line):
    import shlex
    if not line.lstrip().startswith(("python ", "python3 ")):
        return set()
    try:
        tokens = shlex.split(line.strip(), comments=True)
    except ValueError:
        return set()
    if not tokens or tokens[0] not in {'python', 'python3'}:
        return set()
    if tokens[1:2] == ['-B']:
        tokens.pop(1)
    if tokens[1:4] != ['-m', 'pip', 'install']:
        return set()
    # The reviewed invocations are standalone commands (no shell control flow).
    if any(t in {';', '&&', '||', '|', '&'} for t in tokens):
        return set()
    return set(tokens[4:])


def _assert_hosted_dependency_parity(steps, test_name, project):
    import shlex
    matches = [i for i, step in enumerate(steps) if step.get('name') == test_name]
    assert len(matches) == 1, 'missing or duplicate hosted acceptance step'
    test_index = matches[0]
    required = HOSTED_REQUIREMENTS | {project + '[textual]'}
    observed = set()
    for i, step in enumerate(steps[:test_index + 1]):
        if step.get('if') or step.get('continue-on-error'):
            continue
        for line in step.get('run', '').splitlines():
            # RM22 has setup and pytest in one step. Never credit a later install.
            if i == test_index and ('-m pytest' in line or 'tests/w3_native_validate.py' in line or 'tools/actions_full_pytest_ci.py run' in line):
                break
            observed.update(_hosted_install_tokens(line))
    assert required <= observed, f'missing hosted dependencies: {sorted(required - observed)}'
    if test_name == 'Unified full W3 source acceptance':
        preflights = [i for i, step in enumerate(steps) if step.get('name') == GA_PREFLIGHT]
        assert len(preflights) == 1 and preflights[0] < test_index
        preflight = steps[preflights[0]]
        assert not preflight.get('if') and not preflight.get('continue-on-error')
        assert shlex.split(preflight['run']) == ['python3', '-B', '-c', GA_IMPORTS]
        # Verify the prerequisites are present before the import preflight, too.
        _assert_hosted_dependency_parity(steps[:preflights[0] + 1], GA_PREFLIGHT, project)
        complement = [i for i, step in enumerate(steps) if 'tests/w4_native_validate.py --hosted-extra' in step.get('run', '')]
        assert len(complement) == 1 and complement[0] > test_index
        assert not steps[complement[0]].get('if') and not steps[complement[0]].get('continue-on-error')


@pytest.mark.parametrize('workflow,job,test_name,project', HOSTED_LANES)
def test_hosted_source_dependency_parity(workflow, job, test_name, project):
    import yaml
    jobs = yaml.safe_load((ROOT / '.github/workflows' / workflow).read_text())['jobs']
    _assert_hosted_dependency_parity(jobs[job]['steps'], test_name, project)


@pytest.mark.parametrize('fault', ['missing-coverage', 'missing-textual', 'conditional-install',
                                  'ignored-install-error', 'late-install', 'missing-preflight',
                                  'conditional-preflight', 'late-preflight', 'comment-only-coverage'])
def test_ga_dependency_parity_refuses_incomplete_setup(fault):
    import yaml
    steps = yaml.safe_load((ROOT / '.github/workflows/devtool-ga-source-acceptance.yml').read_text())['jobs']['ga-source-contracts']['steps']
    install = next(s for s in steps if 'pip install' in s.get('run', ''))
    preflight = next(s for s in steps if s.get('name') == GA_PREFLIGHT)
    if fault == 'missing-coverage':
        install['run'] = install['run'].replace("'coverage>=7,<9'", '')
    elif fault == 'missing-textual':
        install['run'] = install['run'].replace("'.[textual]'", '.')
    elif fault == 'conditional-install':
        install['if'] = 'false'
    elif fault == 'ignored-install-error':
        install['continue-on-error'] = True
    elif fault == 'late-install':
        steps.remove(install); steps.append(install)
    elif fault == 'missing-preflight':
        steps.remove(preflight)
    elif fault == 'conditional-preflight':
        preflight['if'] = 'false'
    elif fault == 'late-preflight':
        steps.remove(preflight); steps.append(preflight)
    elif fault == 'comment-only-coverage':
        install['run'] = install['run'].replace("'coverage>=7,<9'", '') + "\n# python -m pip install 'coverage>=7,<9'\n"
    with pytest.raises(AssertionError):
        _assert_hosted_dependency_parity(steps, 'Unified full W3 source acceptance', '.')
