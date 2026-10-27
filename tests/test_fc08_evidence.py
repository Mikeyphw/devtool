"""FC-08: negative authority tests and independent provenance verification."""
import importlib.util
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('fc08_evidence', ROOT/'tools/fc08_evidence.py')
assert SPEC and SPEC.loader
fc=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fc)
SHA='a'*40
TREE='b'*40
REPO='Mikeyphw/devtool'
RUN_ID=1234
JOB_ID=5678
ATTEMPT=2
WORKFLOW='.github/workflows/devtool-fc08-host-evidence.yml'


def fake_api(*, run_success=True, job_attempt=ATTEMPT, artifact_digest='sha256:'+'c'*64,
             job_os='windows-2022', tree=TREE, extra_path=False):
    run={'head_sha':SHA,'run_attempt':ATTEMPT,'status':'completed',
         'conclusion':'success' if run_success else 'failure',
         'repository':{'full_name':REPO},'path':WORKFLOW+'@refs/heads/feature/test'}
    job={'id':JOB_ID,'run_id':RUN_ID,'run_attempt':job_attempt,'status':'completed',
         'conclusion':'success','labels':[job_os], 'runner_name':'GitHub Actions 1'}
    art={'name':'fc08-windows-dotnet-msvc-attempt2','id':9,'expired':False,'digest':artifact_digest,
         'workflow_run':{'head_sha':SHA}}
    data={f'/repos/{REPO}/actions/runs/{RUN_ID}':run,
          f'/repos/{REPO}/commits/{SHA}':{'commit':{'tree':{'sha':tree}}},
          f'/repos/{REPO}/actions/runs/{RUN_ID}/jobs?per_page=100&filter=all':{'jobs':[job],'total_count':1},
          f'/repos/{REPO}/actions/runs/{RUN_ID}/artifacts?per_page=100':{'artifacts':[art],'total_count':1}}
    return lambda uri:data[uri]


def verify(fetch, lane='windows-dotnet-msvc'):
    return fc.verify_github_metadata(fetch,repo=REPO,run_id=RUN_ID,job_id=JOB_ID,
            commit=SHA,tree=TREE,attempt=ATTEMPT,workflow_path=WORKFLOW,lane=lane,
            artifact_name='fc08-windows-dotnet-msvc-attempt2')


def test_g1_plan_requires_real_evidence_without_waivers():
    p=fc.plan(ROOT)
    assert p['status']=='PENDING_REAL_EVIDENCE' and not p['release_qualified']
    assert set(x['id'] for x in p['lanes'])==set(fc.LANES)
    assert all(x['status']=='NOT_RUN' and x['qualified'] is False for x in p['lanes'])


def test_authenticated_host_metadata_remains_unqualified_without_receipt_body():
    result=verify(fake_api())
    assert result['status']=='HOSTED_METADATA_VERIFIED_ONLY'
    assert result['provider_authenticated'] is True
    assert result['artifact_contents_verified'] is False
    assert result['execution_semantics_qualified'] is False
    assert result['release_qualified'] is False


@pytest.mark.parametrize('change',[
    {'run_success':False}, {'job_attempt':1}, {'artifact_digest':''},
    {'artifact_digest':'sha256:not-a-valid-sha256'},
    {'job_os':'ubuntu-24.04'}, {'tree':'d'*40},
])
def test_wrong_provider_provenance_refused(change):
    assert verify(fake_api(**change))['status']=='REFUSED'


def test_github_cannot_certify_device_signing_or_consumer():
    for lane in ('termux-arm64','android-signing','consumer-adoption'):
        assert verify(fake_api(),lane=lane)['status']=='REFUSED'


def test_termux_probe_refuses_linux_simulation(monkeypatch):
    monkeypatch.setattr(fc.sys,'prefix','/usr')
    with pytest.raises(ValueError,match='Termux'):
        fc.termux_probe(ROOT)


def test_host_probe_rejects_wrong_os_and_never_promotes_tools(monkeypatch):
    # Simulate *only* the unit-test platform; never claim the physical Android
    # host is a hosted Linux runner. Production host_probe remains unchanged.
    monkeypatch.setattr(fc.platform, 'system', lambda: 'Linux')
    with pytest.raises(ValueError, match='wrong physical'):
        fc.host_probe(ROOT, 'windows-dotnet-msvc', 'Windows')
    with pytest.raises(ValueError, match='runner OS claim contradicts'):
        fc.host_probe(ROOT, 'linux-polyglot', 'Windows')
    seen = fc.host_probe(ROOT, 'linux-polyglot', 'Linux',
                         command_runner=lambda argv: {'command': argv, 'status': 'PASS'})
    assert seen['observed_os'] == 'Linux'
    assert seen['status'] == 'OBSERVED_NOT_EXTERNALLY_VERIFIED'
    assert seen['claims_checked_against_provider'] is False
    assert seen['release_qualified'] is False and seen['execution_semantics_qualified'] is False


def test_host_probe_android_cannot_impersonate_hosted_linux(monkeypatch):
    monkeypatch.setattr(fc.platform, 'system', lambda: 'Android')
    called = []
    with pytest.raises(ValueError, match='wrong physical'):
        fc.host_probe(ROOT, 'linux-polyglot', 'Linux',
                      command_runner=lambda argv: called.append(argv))
    assert called == []  # Reject before executing any hosted-lane command.


def test_host_probe_macos_claim_is_only_local_observation(monkeypatch):
    monkeypatch.setattr(fc.platform, 'system', lambda: 'Darwin')
    seen = fc.host_probe(ROOT, 'macos-native', 'macOS',
                         command_runner=lambda argv: {'command': argv, 'status': 'PASS'})
    assert seen['observed_os'] == 'Darwin'
    assert seen['status'] == 'OBSERVED_NOT_EXTERNALLY_VERIFIED'
    assert seen['release_qualified'] is False
    with pytest.raises(ValueError, match='runner OS claim contradicts'):
        fc.host_probe(ROOT, 'macos-native', 'Linux')


def test_manual_hosted_workflow_not_release_gate():
    workflow=(ROOT/'.github/workflows/devtool-fc08-host-evidence.yml').read_text()
    assert 'workflow_dispatch:' in workflow
    assert 'upload-artifact@v4' in workflow
    assert 'push:' not in workflow
    assert 'not qualification' in workflow
