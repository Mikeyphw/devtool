"""Unsigned-first Android releases, reviewed profile overrides, local LIFE preservation."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

import pytest

from devtool.actions_android import android_plan
from devtool.cli import build_parser
from devtool.github_actions import GitHubActionsError
from tests.test_actions_05_android_builds import project

HERE = Path(__file__).resolve().parents[1]
WORKFLOW = HERE/'.github/workflows/devtool-android-variants.yml'


def _git(root, *args):
    return subprocess.check_output(['git','-C',str(root),*args], text=True).strip()


def _dispatch(monkeypatch, root):
    calls=[]
    def stub(_client, _slug, workflow, _ref, inputs, *, execute):
        calls.append((workflow, dict(inputs), execute))
        return {'pre_dispatch_sha':_git(root,'rev-parse','HEAD'),'state':'validated'}
    monkeypatch.setattr('devtool.actions_android.dispatch_workflow',stub)
    return calls


def _script(step_name):
    lines=WORKFLOW.read_text().splitlines()
    marker='      - name: '+step_name
    start=next(i for i,line in enumerate(lines) if line==marker)
    script_start=next(i for i in range(start+1,len(lines)) if lines[i]=='        run: |')
    buf=[]
    for line in lines[script_start+1:]:
        if line and not line.startswith('          '):break
        buf.append(line[10:] if line else '')
    return '\n'.join(buf)+'\n'


def _run(script, *, cwd, env):
    return subprocess.run(['bash','-c',script],cwd=cwd,env=env,text=True,
                          capture_output=True,timeout=20,check=False)


def test_cli_defaults_local_with_explicit_hosted_option():
    parser=build_parser()
    for command in ('plan','run'):
        a=parser.parse_args(['actions','android',command,'--ref','main','--variant','embeddedRelease'])
        assert a.signing_mode=='local' and a.variant=='embeddedRelease'
        b=parser.parse_args(['actions','android',command,'--ref','main','--signing-mode','github'])
        assert b.signing_mode=='github'
        with pytest.raises(SystemExit):parser.parse_args(['actions','android',command,'--ref','main','--signing-mode','automatic'])


def test_release_planning_uses_local_unsigned_no_secrets_by_default(tmp_path,monkeypatch):
    project(tmp_path)
    calls=_dispatch(monkeypatch,tmp_path)
    plan=android_plan(object(),'owner/repo',tmp_path,module='app',variant='release',fmt='apk',ref='main')
    assert plan['signing_mode']=='local' and plan['signing_state']=='unsigned-candidate'
    assert plan['workflow']=='devtool-android-variants.yml' and not plan['qualified_release']
    assert calls[-1][1]['signing_mode']=='local'
    hosted=android_plan(object(),'owner/repo',tmp_path,module='app',variant='release',fmt='apk',ref='main',signing_mode='github')
    assert hosted['signing_state']=='hosted-signing-requested'
    assert hosted['workflow']=='devtool-android-build.yml'
    assert 'signing_mode' not in calls[-1][1]
    with pytest.raises(GitHubActionsError,match='Local signing requires unsigned-capable'):
        android_plan(object(),'owner/repo',tmp_path,module='app',variant='release',fmt='apk',ref='main',workflow='signed-only.yml')
    with pytest.raises(GitHubActionsError,match='signing mode'):
        android_plan(object(),'owner/repo',tmp_path,module='app',variant='release',fmt='apk',ref='main',signing_mode='hidden')


def test_explicit_reviewed_android_variant_profile_overrides_static_limit(tmp_path,monkeypatch):
    project(tmp_path)
    config=tmp_path/'.devtool.toml'
    config.write_text('''[project]\nrunner="android"\n[android.variants.oddRelease]\nbuild_type="release"\nflavor="odd"\nmodule="app"\nsigning="release"\n''')
    subprocess.run(['git','-C',str(tmp_path),'add','.devtool.toml'],check=True)
    subprocess.run(['git','-C',str(tmp_path),'commit','-qm','register profile'],check=True)
    calls=_dispatch(monkeypatch,tmp_path)
    p=android_plan(object(),'owner/repo',tmp_path,module='app',variant='oddRelease',fmt='apk',ref='main')
    assert (p['build_type'],p['flavor'],p['variant_source'])==('release','odd','reviewed-profile')
    assert calls[-1][0]=='devtool-android-variants.yml'
    assert calls[-1][1]['signing_mode']=='local'
    config.write_text(config.read_text().replace('flavor="odd"','flavor="other"'))
    with pytest.raises(GitHubActionsError,match='mismatches'):
        android_plan(object(),'owner/repo',tmp_path,module='app',variant='oddRelease',fmt='apk',ref='main')
    config.write_text(config.read_text().replace('flavor="other"','flavor="odd"').replace('module="app"','module="other"'))
    with pytest.raises(GitHubActionsError,match='mismatches'):
        android_plan(object(),'owner/repo',tmp_path,module='app',variant='oddRelease',fmt='apk',ref='main')
    config.unlink()
    with pytest.raises(GitHubActionsError,match='Unknown or unreviewed'):
        android_plan(object(),'owner/repo',tmp_path,module='app',variant='oddRelease',fmt='apk',ref='main')


def test_workflow_bash_syntax_and_secret_free_release_task(tmp_path):
    text=WORKFLOW.read_text()
    assert 'default: local' in text and 'options: [local, github]' in text
    assert "inputs.signing_mode == 'local'" in text
    for step in ('Validate requested Gradle task','Build Android release output without GitHub signing secrets',
                 'Build Android signed release output','Select output and sign release'):
        code=_script(step)
        assert subprocess.run(['bash','-n','-c',code],capture_output=True).returncode==0,step
    task=_script('Build Android release output without GitHub signing secrets')
    assert 'DT_KEYSTORE' not in task and 'secrets.' not in task
    env=dict(os.environ,TASK_PROJECT=str(tmp_path),TASK_MODULE='app',TASK_NAME='assembleLiteRelease')
    gradlew=tmp_path/'gradlew'
    gradlew.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > gradle-invoked.txt\n');gradlew.chmod(0o755)
    result=_run(task,cwd=tmp_path,env=env)
    assert result.returncode==0,result.stderr
    assert (tmp_path/'gradle-invoked.txt').read_text().strip()=='--no-daemon -Pdevtool.unsignedReleaseCandidate=true :app:assembleLiteRelease'


def _candidate_harness(tmp_path,mode='local',*,already_signed=False):
    sdk=tmp_path/'sdk/build-tools/35.0.0';sdk.mkdir(parents=True)
    marker=tmp_path/'verify-calls'
    # apksigner simulated only for exercise of workflow branch and negative checks.
    apksigner=sdk/'apksigner'
    apksigner.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n'+
                          ('exit 0\n' if already_signed else 'exit 1\n'))
    apksigner.chmod(0o755)
    repo=tmp_path/'repo';outdir=repo/'app/build/outputs/apk/lite/release';outdir.mkdir(parents=True)
    with zipfile.ZipFile(outdir/'app-lite-release-unsigned.apk','w') as z:
        z.writestr('AndroidManifest.xml',b'fixture')
    temp=tmp_path/'runner';temp.mkdir()
    env=dict(os.environ,TASK_PROJECT=str(repo),TASK_MODULE='app',TASK_VARIANT='liteRelease',
         TASK_BUILD_TYPE='release',TASK_FORMAT='apk',DT_SIGNING_MODE=mode,
         RUNNER_TEMP=str(temp),ANDROID_HOME=str(tmp_path/'sdk'),CALLS=str(marker),
         GITHUB_OUTPUT=str(tmp_path/'output'),GITHUB_SHA='a'*40,GITHUB_RUN_ATTEMPT='2',
         DT_KEYSTORE_B64='',DT_STORE_PASSWORD='',DT_KEY_ALIAS='',DT_KEY_PASSWORD='')
    return repo,env,temp


def test_real_shell_local_unsigned_candidate_requires_no_secrets(tmp_path):
    repo,env,temp=_candidate_harness(tmp_path)
    out=_run(_script('Select output and sign release'),cwd=repo,env=env)
    assert out.returncode==0,(out.stdout,out.stderr)
    manifest=json.loads((temp/'devtool-android-output/android-variant-manifest.json').read_text())
    assert manifest['signing_state']=='unsigned' and manifest['signing_mode']=='local'
    assert manifest['variant']=='liteRelease' and manifest['run_attempt']==2
    assert not manifest['qualified_release']
    assert manifest['sha256']==hashlib.sha256((temp/'devtool-android-output/unsigned.apk').read_bytes()).hexdigest()


def test_local_unsigned_workflow_refuses_pre_signed_gradle_output(tmp_path):
    repo,env,temp=_candidate_harness(tmp_path,already_signed=True)
    out=_run(_script('Select output and sign release'),cwd=repo,env=env)
    assert out.returncode!=0 and 'unexpectedly signed' in out.stderr
    assert not (temp/'devtool-android-output/android-variant-manifest.json').exists()


def test_explicit_github_signing_requires_existing_secrets(tmp_path):
    repo,env,temp=_candidate_harness(tmp_path,mode='github')
    out=_run(_script('Select output and sign release'),cwd=repo,env=env)
    assert out.returncode!=0 and 'Explicit GitHub signing requires' in out.stderr
    assert not (temp/'devtool-android-output/android-variant-manifest.json').exists()


def test_unprotected_ref_allowed_for_unsigned_but_not_hosted_signing(tmp_path):
    gradle=tmp_path/'gradle';gradle.mkdir()
    script=_script('Validate requested Gradle task')
    common=dict(os.environ,DT_PROJECT='gradle',DT_MODULE='app',DT_VARIANT='liteRelease',
                DT_FORMAT='apk',GITHUB_REF_PROTECTED='false',GITHUB_ENV=str(tmp_path/'env'))
    unsigned=_run(script,cwd=tmp_path,env=dict(common,DT_SIGNING_MODE='local'))
    assert unsigned.returncode==0,(unsigned.stdout,unsigned.stderr)
    assert 'TASK_BUILD_TYPE=release' in (tmp_path/'env').read_text()
    protected=_run(script,cwd=tmp_path,env=dict(common,DT_SIGNING_MODE='github'))
    assert protected.returncode!=0 and 'protected GitHub ref' in protected.stderr
    malicious=_run(script,cwd=tmp_path,env=dict(common,DT_SIGNING_MODE='other'))
    assert malicious.returncode!=0


def test_native_life_signing_still_does_not_access_github_secrets():
    from devtool.actions_unsigned import unsigned_local_sign
    import inspect
    source=inspect.getsource(unsigned_local_sign)
    assert '_signing_profile' in source and '_signer_material' in source
    assert '_local_signing_password' in source and 'local_verify' in source
    assert 'GITHUB_RELEASE' not in source and 'DEVTOOL_RELEASE_KEYSTORE_B64' not in source
    assert 'consumer_contract_sha256' in source and 'qualified_release' in source


def test_untracked_reviewed_profile_cannot_override_gradle(tmp_path):
    project(tmp_path)
    (tmp_path/'.gitignore').write_text('.devtool.toml\n')
    (tmp_path/'.devtool.toml').write_text('''[android.variants.oddRelease]\nbuild_type="release"\nflavor="odd"\nmodule="app"\n''')
    with pytest.raises(GitHubActionsError,match='tracked repository document'):
        android_plan(object(),'owner/repo',tmp_path,module='app',variant='oddRelease',fmt='apk',ref='main')


def test_explicit_hosted_signing_still_uses_16k_and_reports_nonqualified(tmp_path):
    import base64
    repo,env,temp=_candidate_harness(tmp_path,mode='github')
    sdk=tmp_path/'sdk/build-tools/35.0.0'
    align=sdk/'zipalign'
    align.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
[ "$1" = '-c' ] && exit 0
before='';last=''
for item in "$@"; do before="$last"; last="$item"; done
cp "$before" "$last"
''')
    align.chmod(0o755)
    sign=sdk/'apksigner'
    sign.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
[ "$1" = 'verify' ] && exit 0
[ "$1" = 'sign' ] || exit 3
out='';previous='';last=''
for item in "$@"; do
  if [ "$previous" = '--out' ]; then out="$item"; fi
  previous="$item"; last="$item"
done
cp "$last" "$out"
''')
    sign.chmod(0o755)
    env.update(DT_KEYSTORE_B64=base64.b64encode(b'fake-key-not-a-credential').decode(),
               DT_STORE_PASSWORD='mock',DT_KEY_ALIAS='mock',DT_KEY_PASSWORD='mock')
    result=_run(_script('Select output and sign release'),cwd=repo,env=env)
    assert result.returncode==0,(result.stdout,result.stderr)
    manifest=json.loads((temp/'devtool-android-output/android-variant-manifest.json').read_text())
    assert manifest['signing_state']=='signed' and manifest['signing_mode']=='github'
    assert manifest['qualified_release'] is False
    calls=(tmp_path/'verify-calls').read_text()
    assert '-P 16 -f 4' in calls and '-c -P 16 4' in calls and 'sign --ks' in calls
