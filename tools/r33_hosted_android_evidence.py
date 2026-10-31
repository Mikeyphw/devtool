"""R33 live hosted Android fixture: Devtool-only, non-release evidence.

The fixture is committed into a deterministic *ephemeral* Git repository after
obtaining an official, independently hash-checked Gradle wrapper JAR. Its Git
HEAD differs from the GitHub Devtool checkout SHA by design. The temporary
GITHUB_SHA substitution is confined to the RM-06 runner subprocess; the
origin SHA is always preserved and provenance is never marked as release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

FIXTURE_SUBDIR = 'tests/fixtures/r33_hosted_android'
JAR_SHA256 = '2db75c40782f5e8ba1fc278a5574bab070adccb2d21ca5a6e5ed840888448046'
DISTRIBUTION_SHA256 = 'f397b287023acdba1e9f6fc5ea72d22dd63669d59ed4a289a29b1a76eee151c6'
PACKAGE = 'org.devtool.r33.fixture'
OUTPUT = 'app/build/outputs/apk/debug/app-debug.apk'
ROOT_ALLOWED = {
    '.gitignore', '.devtool.toml', 'gradlew', 'gradle-wrapper.properties',
    'settings.gradle', 'build.gradle', 'app/build.gradle',
    'app/src/main/AndroidManifest.xml',
    'app/src/main/java/org/devtool/r33/fixture/Fixture.java',
}


def refuse(message: str) -> None:
    raise SystemExit('R33_REFUSED: ' + message)


def run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(argv, cwd=cwd, env=env, check=True, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.strip()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def origin(repo: Path) -> str:
    if not (repo / '.git').exists():
        refuse('Devtool origin must be a Git checkout')
    commit = run(['git', 'rev-parse', 'HEAD'], cwd=repo)
    if os.environ.get('GITHUB_ACTIONS') == 'true' and commit != os.environ.get('GITHUB_SHA'):
        refuse('GitHub Devtool origin SHA mismatch')
    if not re.fullmatch('[0-9a-f]{40}', commit):
        refuse('invalid Devtool origin SHA')
    return commit


def prepare(repo: Path, fixture: Path, wrapper_jar: Path) -> dict[str, Any]:
    devtool_sha = origin(repo)
    template = repo / FIXTURE_SUBDIR
    if not template.is_dir() or template.is_symlink() or fixture.exists():
        refuse('missing/symlinked template or non-new fixture root')
    if not wrapper_jar.is_file() or wrapper_jar.is_symlink() or sha(wrapper_jar) != JAR_SHA256:
        refuse('official Gradle 8.11.1 wrapper JAR SHA-256 mismatch')
    names = {str(p.relative_to(template)) for p in template.rglob('*') if p.is_file()}
    if names != ROOT_ALLOWED:
        refuse('fixture file inventory changed or includes unexpected files')
    for name in sorted(names):
        entry = template / name
        if entry.is_symlink() or entry.stat().st_size > 32768:
            refuse('symlink/oversized fixture source refused')
        relative = str(entry.relative_to(repo))
        if run(['git', 'ls-files', '--error-unmatch', '--', relative],cwd=repo) == '':
            refuse('fixture source must be Git-tracked: ' + name)
        original = subprocess.run(['git','show','HEAD:' + relative], cwd=repo, check=True, capture_output=True).stdout
        if original != entry.read_bytes():
            refuse('fixture source bytes differ from GitHub Devtool HEAD: ' + name)
    if DISTRIBUTION_SHA256 not in (template / 'gradle-wrapper.properties').read_text():
        refuse('Gradle distribution must be SHA-pinned')
    for p in sorted(template.rglob('*')):
        relative=p.relative_to(template)
        target=fixture/relative
        if p.is_dir():
            target.mkdir(parents=True,exist_ok=True)
        elif p.is_file():
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(p,target)
            target.chmod(p.stat().st_mode & 0o777)
    wrapper_dir=fixture/'gradle/wrapper'
    wrapper_dir.mkdir(parents=True)
    shutil.copyfile(wrapper_jar,wrapper_dir/'gradle-wrapper.jar')
    shutil.move(str(fixture/'gradle-wrapper.properties'),str(wrapper_dir/'gradle-wrapper.properties'))
    # No git inherited from origin. Fixed identity ensures the fixture commit is
    # stable across *different* GitHub Actions jobs in the same workflow run.
    run(['git','init','-q','-b','main'],cwd=fixture)
    run(['git','add','-A'],cwd=fixture)
    env=dict(os.environ)
    env.update(GIT_AUTHOR_NAME='Devtool R33 Test Fixture',GIT_AUTHOR_EMAIL='r33@example.invalid',
               GIT_COMMITTER_NAME='Devtool R33 Test Fixture',GIT_COMMITTER_EMAIL='r33@example.invalid',
               GIT_AUTHOR_DATE='2001-01-01T00:00:00+0000',GIT_COMMITTER_DATE='2001-01-01T00:00:00+0000')
    run(['git','commit','-qm','devtool-r33-fixture-v1'],cwd=fixture,env=env)
    fixture_sha=run(['git','rev-parse','HEAD'],cwd=fixture)
    if run(['git','status','--porcelain'],cwd=fixture):
        refuse('fixture Git worktree dirty after preparation')
    return {'devtool_sha':devtool_sha,'fixture_sha':fixture_sha,
            'fixture_tree':run(['git','rev-parse','HEAD^{tree}'],cwd=fixture),
            'wrapper_jar_sha256':sha(wrapper_dir/'gradle-wrapper.jar'),
            'distribution_sha256':DISTRIBUTION_SHA256,'fixture_kind':'temporary-owned-devtool-lab-not-consumer'}


def graph_plan(fixture: Path, devtool_sha: str) -> dict[str, Any]:
    from devtool.actions_graph_export import compile_graph, workflow_yaml
    graph=compile_graph(fixture,target='r33',workflow='ci',devtool_sha=devtool_sha)
    if len(graph['jobs']) != 2:
        refuse('R33 fixture requires precisely two native graph nodes')
    lint,build=graph['jobs']
    if not (lint['provider']==build['provider']=='android-gradle'
            and lint['id']=='node-01' and build['id']=='node-02'
            and not lint['needs'] and build['needs']==['node-01']
            and lint['android_review']['task']==':app:lintDebug'
            and build['android_review']['task']==':app:assembleDebug'
            and lint['outputs']==[] and build['outputs']==[OUTPUT]):
        refuse('native DAG no longer matches reviewed lint -> debug build contract')
    yml=workflow_yaml(graph)
    if ('setup-android@' not in yml or 'setup-gradle@' not in yml
            or 'run-node' not in yml or 'node-01' not in yml or 'node-02' not in yml):
        refuse('exported multi-job runner lacks native Android provisioning')
    return graph


def execute(fixture: Path, job: str, report_path: Path, *, prior: Path | None) -> None:
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_OS') != 'Linux':
        refuse('live fixture execution requires real GitHub-hosted Linux')
    github_sha=os.environ.get('GITHUB_SHA','')
    github_run=os.environ.get('GITHUB_RUN_ID','')
    github_attempt=os.environ.get('GITHUB_RUN_ATTEMPT','')
    if not re.fullmatch('[0-9a-f]{40}',github_sha) or not github_run.isdecimal() or not github_attempt.isdecimal():
        refuse('missing actual GitHub source/run/attempt')
    fixture_sha=run(['git','rev-parse','HEAD'],cwd=fixture)
    if run(['git','status','--porcelain'],cwd=fixture):
        refuse('fixture source dirty before native execution')
    graph=graph_plan(fixture,github_sha)
    jid={'lint':'node-01','build':'node-02'}.get(job)
    if jid is None:
        refuse('unsupported R33 node')
    if job=='build':
        if prior is None or not prior.is_file():
            refuse('build must consume lint evidence from preceding hosted job')
        receipt=json.loads(prior.read_text())
        checks={'kind':'devtool.r33.live-node.v1','node':'lint','devtool_sha':github_sha,
                'fixture_sha':fixture_sha,'graph_sha256':graph['graph_sha256'],
                'github_run_id':github_run,'github_run_attempt':int(github_attempt),
                'status':'PASS'}
        if any(receipt.get(k)!=v for k,v in checks.items()):
            refuse('upstream hosted lint evidence or source/run/attempt mismatched')
    # RM-06 matches GITHUB_SHA with the checked-out Git HEAD. The ephemeral
    # source is a separate Git checkout, therefore the native runner *only*
    # receives the fixture SHA. The actual Devtool Actions SHA stays in the
    # separately validated report and is NEVER claimed to be the fixture SHA.
    process_env=dict(os.environ)
    process_env['GITHUB_SHA']=fixture_sha
    inputs_dir=report_path.parent/'native-inputs'
    outputs_dir=report_path.parent/'native-outputs'
    inputs_dir.mkdir(parents=True,exist_ok=True)
    outputs_dir.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,'-m','devtool.actions_graph_export','run-node',
             '--root',str(fixture),'--target','r33','--workflow','ci',
             '--devtool-sha',github_sha,'--graph-sha',graph['graph_sha256'],
             '--job-id',jid,'--inputs',str(inputs_dir),
             '--outputs',str(outputs_dir)]
    result=subprocess.run(command,cwd=fixture,env=process_env,text=True,capture_output=True)
    if result.returncode:
        print(result.stdout[-10000:],file=sys.stderr)
        print(result.stderr[-10000:],file=sys.stderr)
        refuse('real Devtool native node returned '+str(result.returncode))
    try:
        native=json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):
        refuse('Devtool native node did not produce its own JSON result')
    if native.get('status')!='LOCAL_JOB_SUCCEEDED' or native.get('graph_sha256')!=graph['graph_sha256'] or native.get('github_source')!=fixture_sha or native.get('job_id')!=jid:
        refuse('Devtool native result does not match frozen job and fixture source')
    report={'kind':'devtool.r33.live-node.v1','node':job,'status':'PASS',
            'devtool_sha':github_sha,'fixture_sha':fixture_sha,
            'graph_sha256':graph['graph_sha256'],'provider':'android-gradle',
            'github_run_id':github_run,'github_run_attempt':int(github_attempt),
            'actual_gradle_execution':True,'native_dag_executed':True,
            'consumer_repo_modified':False,'release_qualified':False,
            'device_qualified':False,'artifact_registry_authenticated':False,
            'node_result':native}
    if job=='build':
        apk=fixture/OUTPUT
        output_receipt=outputs_dir/'node-02/receipt.json'
        if not apk.is_file() or not output_receipt.is_file():
            refuse('build passed but reviewed APK/staging receipt missing')
        saved=json.loads(output_receipt.read_text())
        if saved.get('source_commit') != fixture_sha or saved.get('graph_sha256')!=graph['graph_sha256'] or saved.get('attempt')!=int(github_attempt) or saved.get('run_id')!=github_run:
            refuse('native staging receipt mismatch')
        listed=saved.get('files',[])
        if len(listed)!=1 or listed[0].get('path')!=OUTPUT or listed[0].get('sha256')!=sha(apk) or listed[0].get('size')!=apk.stat().st_size:
            refuse('native staging receipt does not match actual APK bytes')
        buildtools=Path(os.environ.get('ANDROID_HOME',''))/'build-tools/35.0.0'
        aapt=buildtools/'aapt'; apksigner=buildtools/'apksigner'
        if not aapt.is_file() or not apksigner.is_file():
            refuse('SDK APK verification tools missing')
        badging=run([str(aapt),'dump','badging',str(apk)],cwd=fixture)
        match=re.search(r"^package: name='([^']+)'",badging,re.M)
        if not match or match.group(1)!=PACKAGE:
            refuse('real APK package identity differs from Devtool fixture contract')
        signing=run([str(apksigner),'verify','--verbose','--print-certs',str(apk)],cwd=fixture)
        signer=re.search(r'Signer #1 certificate SHA-256 digest:\s*([a-fA-F0-9]{64})',signing)
        if not signer:
            refuse('debug APK signature verification unavailable or failed')
        report['apk']={'path':OUTPUT,'sha256':sha(apk),'size':apk.stat().st_size,
                       'package':PACKAGE,'variant':'debug','debug_signed':True,
                       'signer_certificate_sha256':signer.group(1).lower(),
                       'signing_release_authorized':False}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print('PASS_R33_DEVTOOL_NATIVE_HOSTED_'+job.upper()+'_NON_RELEASE')
    print(json.dumps({k:v for k,v in report.items() if k not in ('node_result',)},sort_keys=True))


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('operation',choices=['prepare','plan','execute'])
    parser.add_argument('--repo',type=Path,default=Path.cwd())
    parser.add_argument('--fixture',type=Path,required=True)
    parser.add_argument('--jar',type=Path)
    parser.add_argument('--job',choices=['lint','build'])
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--prior',type=Path)
    ns=parser.parse_args()
    if ns.operation=='prepare':
        if ns.jar is None:
            refuse('prepare requires independently downloaded official wrapper JAR')
        result=prepare(ns.repo.resolve(),ns.fixture.resolve(),ns.jar.resolve())
        ns.out.parent.mkdir(parents=True,exist_ok=True)
        ns.out.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
        print('PASS_R33_DETERMINISTIC_FIXTURE_SOURCE')
    elif ns.operation=='plan':
        graph=graph_plan(ns.fixture.resolve(),origin(ns.repo.resolve()))
        record={'graph_sha256':graph['graph_sha256'],
                'jobs':[{k:j[k] for k in ('id','node','needs','provider','outputs')} for j in graph['jobs']],
                'release_qualified':False,'device_qualified':False,
                'fixture_sha':run(['git','rev-parse','HEAD'],cwd=ns.fixture.resolve()),
                'devtool_sha':origin(ns.repo.resolve())}
        ns.out.parent.mkdir(parents=True,exist_ok=True)
        ns.out.write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
        print('PASS_R33_NATIVE_ANDROID_DAG_PLAN_NON_RELEASE')
    else:
        if not ns.job:
            refuse('execute requires --job')
        execute(ns.fixture.resolve(),ns.job,ns.out.resolve(),prior=ns.prior)


if __name__=='__main__':
    main()
