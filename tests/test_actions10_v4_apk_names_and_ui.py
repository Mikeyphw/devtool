"""ACTIONS-10 V4 regressions: actual AGP-compatible ZIP naming and remote final UX."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import zipfile
import warnings
from types import SimpleNamespace

import pytest

from devtool.actions_unsigned import _no_apk_signatures
from devtool.actions_live_progress import encode_observation
from devtool.github_actions import GitHubActionsError
from devtool.textual_execution_host import ExecutionTextualHostSource


def candidate(tmp_path: Path, pairs: list[tuple[str, bytes]]) -> Path:
    path = tmp_path / 'candidate.apk'
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('AndroidManifest.xml', b'manifest')
            for name, data in pairs:
                archive.writestr(name, data)
    return path


def test_distinct_case_sensitive_assets_are_allowed(tmp_path):
    apk = candidate(tmp_path, [
        ('assets/Logo.svg', b'first'), ('assets/logo.svg', b'second'),
        ('res/raw/CONFIG.dat', b'first'), ('res/raw/config.dat', b'second'),
        ('classes.dex', b'dex'),
    ])
    _no_apk_signatures(apk)


def test_identical_names_still_rejected(tmp_path):
    apk = candidate(tmp_path, [('assets/logo.svg', b'first'), ('assets/logo.svg', b'second')])
    with pytest.raises(GitHubActionsError, match='duplicate exact'):
        _no_apk_signatures(apk)


@pytest.mark.parametrize('names', [
    [('androidmanifest.xml', b'shadow')],
    [('classes.dex', b'a'), ('Classes.dex', b'b')],
    [('META-INF/FILE.RSA', b'a'), ('meta-inf/file.rsa', b'b')],
    [('resources.arsc', b'a'), ('Resources.arsc', b'b')],
])
def test_security_sensitive_casefold_collisions_still_rejected(tmp_path, names):
    apk = candidate(tmp_path, names)
    with pytest.raises(GitHubActionsError, match='ambiguous security-sensitive'):
        _no_apk_signatures(apk)


@pytest.mark.parametrize('path', [
    '../outside', '/absolute', 'assets/../hidden', 'assets//empty',
    'assets\\backslash', 'assets/a:stream', './relative',
])
def test_zip_member_paths_fail_closed(tmp_path, path):
    apk = candidate(tmp_path, [(path, b'junk')])
    with pytest.raises(GitHubActionsError, match='unsafe member path'):
        _no_apk_signatures(apk)


def test_signed_jar_marker_still_rejected(tmp_path):
    apk = candidate(tmp_path, [('META-INF/PROD.RSA', b'sig')])
    with pytest.raises(GitHubActionsError, match='JAR signing'):
        _no_apk_signatures(apk)


def test_missing_manifest_still_rejected(tmp_path):
    path = tmp_path / 'candidate.apk'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('assets/icon.png', b'x')
    with pytest.raises(GitHubActionsError, match='lacks AndroidManifest'):
        _no_apk_signatures(path)


def test_remote_final_snapshot_retains_observed_run_job_and_failure_step(tmp_path):
    event = {'remote': {'id': 38319546064, 'repository':'Mikeyphw/devtool',
                        'status':'completed','conclusion':'failure','completed':True,
                        'head_sha':'d'*40, 'run_attempt':1},
             'jobs': [{'name':'unsigned-candidate', 'status':'completed',
                       'conclusion':'failure', 'steps':[
                 {'name':'Build unsigned APK', 'status':'completed', 'conclusion':'success'},
                 {'name':'Inspect unsigned APK', 'status':'completed', 'conclusion':'failure'}]}],
             'source_binding':'matching'}
    line=encode_observation(event)
    script='import sys;print(sys.argv[1],flush=True);sys.exit(1)'
    def spawn(argv, **opts):
        return subprocess.Popen([sys.executable, '-c', script, line], **opts)
    host=ExecutionTextualHostSource([], SimpleNamespace(command='actions'), cwd=tmp_path,
                                     popen_factory=spawn)
    host.start()
    assert host.wait(timeout=10)==1
    host._bridge.join(timeout=10)
    assert not host._bridge.is_alive()
    out=[]
    while not host.presenter._queue.empty():
        out.append(host.presenter._queue.get_nowait())
    final=out[-1][1]
    assert out[-1][0]=='snapshot'
    assert final['completion_status']=='failed'
    assert final['command']=='GitHub Actions'
    assert final['phase_name']=='Run #38319546064'
    assert 'unsigned-candidate' in final['detail']
    assert any('Inspect unsigned APK' in item['name'] and item['status']=='failure'
               for item in final['stages'])
    assert final['percent'] is None


def test_remote_failure_not_inferred_from_observation(tmp_path):
    # Even an API observation of success must not turn a failing child into
    # an accepted signed-release result or a successful monitoring command.
    event = {'remote': {'id':11,'repository':'Mikeyphw/devtool',
                        'status':'completed','conclusion':'success','completed':True},
             'jobs': [], 'source_binding':'matching'}
    line=encode_observation(event)
    def spawn(argv, **opts):
        return subprocess.Popen([sys.executable, '-c',
                                 'import sys;print(sys.argv[1],flush=True);sys.exit(2)', line], **opts)
    host=ExecutionTextualHostSource([], SimpleNamespace(command='actions'), cwd=tmp_path,
                                     popen_factory=spawn)
    host.start()
    assert host.wait(timeout=10)==2
    host._bridge.join(timeout=10)
    out=[]
    while not host.presenter._queue.empty():out.append(host.presenter._queue.get_nowait())
    assert out[-1][1]['completion_status']=='failed'
    assert 'monitor exited 2' in out[-1][1]['detail']


def test_finished_compact_ui_never_claims_still_working():
    from devtool.job_ui import compact_job_projection
    for status, expected in [('failed','FAILED'), ('ok','COMPLETED'), ('cancelled','CANCELLED')]:
        view=compact_job_projection({'completion_status':status, 'percent':None})
        assert view['finished']
        assert view['counter']==expected


def test_late_failed_matrix_job_gets_visible_step():
    from devtool.actions_live_progress import project_observation
    jobs=[{'name':f'passed-{i}', 'status':'completed', 'conclusion':'success', 'steps':[]}
          for i in range(6)]
    jobs.append({'name':'failed-last','status':'completed','conclusion':'failure',
                 'steps':[{'name':'APK inspect','status':'completed','conclusion':'failure'}]})
    state, lines=project_observation({'remote':{'id':8,'status':'completed','conclusion':'failure'},'jobs':jobs})
    assert any(stage['name']=='failed-last' for stage in state['stages'])
    assert any('APK inspect' in stage['name'] for stage in state['stages'])
    assert 'FAILED GitHub step: failed-last / APK inspect' in lines
