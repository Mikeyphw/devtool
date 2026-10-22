"""RM-06: fail-closed native DAG -> multi-job Actions compilation and byte handoff.

The configured native DAG and the reviewed provider plan remain the authority.
GitHub `needs` is a projection, not a second mutable execution graph.
Downloaded artifact digests establish transfer integrity, not producer identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
from typing import Any

from .actions_language_providers import provider_plan, ProviderError, SETUP_PYTHON_SHA
from .toolchain_profiles import (select_profiles, validate_versions, cache_policy, ProfileError, MAX_TOTAL_JOBS)
from .actions_adoption import _CHECKOUT_SHA, _SETUP_GO_SHA, _UPLOAD_SHA, _safe_slug, _sha
from .config import load_wrapper_project_config

# Official download-artifact v4 action pin; never float a tag in generated YAML.
_DOWNLOAD_SHA = 'd3f86a106a0bac45b974a628896c90dbdf5c8093'
SCHEMA = 2
MAX_NODES = 24
MAX_TRANSFER_FILES = 64
MAX_TRANSFER_BYTES = 512 * 1024 * 1024


def _hash_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for piece in iter(lambda: f.read(1024*1024),b''):
            h.update(piece)
    return h.hexdigest()


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _path(rel: str) -> PurePosixPath:
    if (not isinstance(rel, str) or not rel or len(rel) > 240 or '\\' in rel
            or rel.startswith('/') or rel.endswith('/') or '//' in rel):
        raise ProviderError('RM-06 artifact path must be a bounded relative POSIX file path')
    p = PurePosixPath(rel)
    if any(piece in ('', '.', '..') for piece in rel.split('/')) or any(part.startswith('.') for part in p.parts):
        raise ProviderError('RM-06 artifact paths may not contain dot components')
    if any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./' for c in rel):
        raise ProviderError('RM-06 artifact path contains unreviewed characters')
    return p


def _regular(root: Path, rel: str, *, must_exist: bool = True) -> Path:
    p = _path(rel)
    here = root
    for part in p.parts:
        here = here / part
        if here.is_symlink():
            raise ProviderError('RM-06 refuses symlink in artifact path')
    if must_exist:
        if not here.is_file() or not stat.S_ISREG(here.stat().st_mode):
            raise ProviderError('RM-06 declared output is missing or not a regular file: ' + rel)
    return here


def compile_graph(root: Path, *, target: str, workflow: str, devtool_sha: str,
                  host_profiles: str = '', cache_locks: bool = False) -> dict[str, Any]:
    """Deterministic compiler; each reviewed host cell gets isolated jobs and handoffs."""
    plan = provider_plan(root, target=target, workflow=workflow, devtool_sha=devtool_sha)
    rows = plan['nodes']
    try:
        profiles=select_profiles(host_profiles)
        validate_versions([row['provider'] for row in rows],profiles)
    except ProfileError as exc: raise ProviderError(str(exc)) from None
    versions=rows[0]['provider']['matrix_versions']
    # Do not infer a shared toolchain from a mixed set of node profiles.
    if any(row['provider']['matrix_versions']!=versions for row in rows):
        if any(len(row['provider']['matrix_versions'])>1 for row in rows):
            raise ProviderError('RM-08 matrix versions differ across DAG nodes')
        versions=['']  # independent pinned node versions, no fanout
    elif len(versions)==1:
        versions=['']  # retain original graph/job IDs exactly
    if len(profiles)*len(rows)*len(versions)>MAX_TOTAL_JOBS:
        raise ProviderError('RM-07 matrix fanout exceeds total job cap')
    if not 1 <= len(rows) <= MAX_NODES:
        raise ProviderError('RM-06 bounded multi-job export accepts 1..24 nodes; use single-job fallback')
    cfg = load_wrapper_project_config(str(root.resolve()), None)
    native_jobs = cfg.targets[plan['target']].jobs
    # RM-07 must re-admit every selected host. The RM-06 provider plan is
    # Ubuntu-based and cannot be used as evidence for the other matrix cells.
    from .workflow_graph import graph_from_workflow
    from .evidence_kernel import graph_host_admission
    graph=graph_from_workflow(plan['target'],cfg.targets[plan['target']].workflows[plan['workflow']])
    for profile in profiles:
        admission=graph_host_admission(cfg,graph,host_os=profile.os,
                                      host_arch=profile.arch,toolchains=('go','python-shell'))
        if not admission['all_eligible']:
            failures=[item['node']+':'+','.join(item['eligibility']['reasons'])
                      for item in admission['nodes'] if not item['eligibility']['eligible']]
            raise ProviderError('RM-07 host profile '+profile.identifier+' refuses node: '+'; '.join(failures)[:320])
    node_to_id: dict[tuple[str,str],str] = {}
    jobs: list[dict[str, Any]] = []
    for profile in profiles:
      for version in versions:
        cell=profile.identifier+(':'+version if version else '')
        for index,row in enumerate(rows,1):
            jid=(f'node-{index:02d}' if len(profiles)==1 and profile.identifier=='ubuntu-24.04-x64' else f'node-{index:02d}-{profile.identifier.replace(".","-")}')
            if version:jid+='-v'+version.replace('.','-')
            node_to_id[(row['node'],cell)]=jid
            outs=list(row['outputs'])
            if len(outs)>MAX_TRANSFER_FILES or len(set(outs))!=len(outs):
                raise ProviderError('RM-07 node output count exceeds cap or has duplicates')
            for path in outs:
                _path(path)
                from .actions_language_providers import _tracked
                if _tracked(root,path):
                    raise ProviderError('RM-06 output must not overwrite tracked source: '+path)
            try:
                cache=cache_policy(root,{**row['provider'],'version':version or row['provider']['version']},profile,enabled=cache_locks)
            except ProfileError as exc: raise ProviderError(str(exc)) from None
            jobs.append({'id':jid,'node':row['node'],'job':row['job'],
                         'depends_on':list(row['depends_on']), 'profile_id':profile.identifier,
                         'matrix_cell':cell,'matrix_version':version,
                         'runner':profile.hosted_runner,'arch':profile.arch,
                         'timeout_minutes':profile.timeout_minutes, 'cache_policy':cache,
                         'outputs':sorted(outs),'inputs':list(native_jobs[row['job']].inputs),
                         'provider':row['provider']['provider'],
                         'toolchain_version':version or row['provider']['version'],
                         'go_manifest':row['provider'].get('version_source','go.mod'),
                         'race':('-race' in row['command']),
                         'python_package':row['provider']['provider']=='python-shell' and bool(row['provider'].get('project')),
                         'wheel_smoke':row['wheel_smoke'],'sdist_smoke':row['sdist_smoke'],
                         'reproducible_go':row['provider'].get('reproducible_go',False),
                         'go_command':list(row['command']) if row['provider']['provider']=='go' else [],
                         'go_env':dict(row['env']) if row['provider']['provider']=='go' else {},
                         'python_install_lock':(row['provider'].get('project') or {}).get('install_lock',''),
                         'auto_coverage':row['auto_coverage'],
                         'coverage_report':row['auto_coverage'] or (row['provider']['provider']=='python-shell' and row['command'][2:]==['coverage','xml','-o','dist/coverage.xml']),
                         'needs':[],'handoffs':[]})
    owners: dict[tuple[str,str],str] = {}
    for job in jobs:
        for output in job['outputs']:
            key=(job['matrix_cell'],output)
            if key in owners:
                raise ProviderError('RM-06 multiple DAG nodes produce one output: '+output)
            owners[key]=job['node']
    for job in jobs:
        profile=job['matrix_cell'];deps=list(job['depends_on'])
        if len(set(deps))!=len(deps) or any((dep,profile) not in node_to_id for dep in deps):
            raise ProviderError('RM-07 invalid native dependency projection')
        job['needs']=[node_to_id[(dep,profile)] for dep in deps]
        by_id={item['id']:item for item in jobs}
        job['handoffs']=[dep for dep in job['needs'] if by_id[dep]['outputs']]
        if len(job['handoffs'])>MAX_NODES:
            raise ProviderError('RM-06 handoff fanin exceeds cap')
        for inp in job['inputs']:
            if not isinstance(inp,str):raise ProviderError('RM-06 nonliteral node input')
            if any(c in inp for c in '*?[]') and owners:
                raise ProviderError('RM-06 cannot prove generated-input ownership through globs')
            key=(profile,inp)
            if key in owners and owners[key]!=job['node'] and owners[key] not in deps:
                raise ProviderError('RM-06 generated input requires direct producer dependency: '+inp)
        if job['race'] and job['runner']!='ubuntu-24.04':
            raise ProviderError('RM-08 Go race profile requires reviewed Linux amd64 runner')
        if job['provider'] not in ('go','python-shell'):
            raise ProviderError('RM-06 refuses unreviewed hosted provider')
    immutable = {'schema_version': SCHEMA, 'kind': 'devtool.actions.export-graph',
                 'provider_plan_sha256':plan['plan_sha256'], 'target':plan['target'],
                 'workflow':plan['workflow'], 'runtime_sha':plan['runtime_sha'],
                 'runner': profiles[0].hosted_runner if len(profiles)==1 else 'per-cell',
                 'matrix': [{'os':p.hosted_runner,'arch':p.arch} for p in profiles],
                 'toolchain_profiles':[p.identifier for p in profiles],
                 'language_versions':versions,
                 'cache': 'enabled-reviewed-go-only' if cache_locks else 'disabled', 'changed_targets':'full-dag', 'jobs':jobs}
    return {**immutable, 'graph_sha256':_digest(immutable), 'native_source_sha256':plan['plan_sha256'],
            'fallback':'single-job', 'read_only':True, 'authenticated_remote':False,
            'release_qualified':False, 'consumer_commit':plan['consumer_commit'],
            'consumer_tree':plan['consumer_tree'], 'provider_plan':plan}


def _artifact_name(namespace: str, jid: str) -> str:
    return f'{namespace}-{jid}-bytes'


def workflow_yaml(graph: dict[str, Any]) -> str:
    namespace=f"devtool-{graph['target']}-{graph['workflow']}-multi"
    target, workflow, pin, digest=graph['target'],graph['workflow'],graph['runtime_sha'],graph['graph_sha256']
    preamble = (f'# Generated by Devtool RM-06: multi-job projection of the native DAG.\n'
                f'# ExportGraphV2 SHA256: {digest}\n'
                f'name: {namespace}\n'
                'on:\n  workflow_dispatch:\n'
                'permissions:\n  contents: read\n'
                'concurrency:\n'
                f'  group: {namespace}-${{{{ github.ref }}}}\n'
                '  cancel-in-progress: false\n'
                'jobs:\n')
    entries=[]
    for job in graph['jobs']:
        jid=job['id']
        dep_str=('    needs: ['+', '.join(job['needs'])+']\n') if job['needs'] else ''
        go_selector=("go-version: '"+job["toolchain_version"]+"'" if job["matrix_version"] else f'go-version-file: {job["go_manifest"]}')
        setup_go=(f'      - name: Pinned Go toolchain\n'
                  f'        uses: actions/setup-go@{_SETUP_GO_SHA}\n'
                  f'        with:\n          {go_selector}\n          cache: false\n') if job['provider']=='go' else ''
        downloads=''
        for dep in job['handoffs']:
            downloads += (f'      - name: Download {dep} byte handoff\n'
                          f'        uses: actions/download-artifact@{_DOWNLOAD_SHA}\n'
                          '        with:\n'
                          f'          name: {_artifact_name(namespace,dep)}\n'
                          f'          path: ${{{{ runner.temp }}}}/rm06-inputs/{dep}\n')
        cache=job['cache_policy']
        cache_step=(f'      - name: Opt-in pinned Go build cache\n'
                    f'        uses: actions/cache@{cache["action_sha"]}\n'
                    '        with:\n'
                    f'          path: {cache["path"]}\n'
                    f'          key: {cache["key"]}\n') if cache['enabled'] else ''
        py_requirements=(('      - name: Install tracked hash-locked Python build requirements\n'
                          '        shell: bash\n        run: |\n'
                          '          set -euo pipefail\n'
                          f'          python3 -m pip install --disable-pip-version-check --require-hashes -r {job["python_install_lock"] or "requirements.txt"}\n')
                         if job['python_package'] else '')
        upload=''
        if job['outputs']:
            upload=(f'      - name: Upload {jid} declared bytes (not authenticated)\n'
                    '        if: success()\n'
                    f'        uses: actions/upload-artifact@{_UPLOAD_SHA}\n'
                    '        with:\n'
                    f'          name: {_artifact_name(namespace,jid)}\n'
                    f'          path: ${{{{ runner.temp }}}}/rm06-output/{jid}/\n'
                    '          include-hidden-files: false\n'
                    '          if-no-files-found: error\n'
                    '          retention-days: 7\n')
        # Runtime downloads pinned commit before any handoff touches the workspace.
        entry=(f'  {jid}:\n{dep_str}'
               f'    runs-on: {job["runner"]}\n    timeout-minutes: {job["timeout_minutes"]}\n'
               '    env:\n'
               f'      DEVTOOL_SOURCE_SHA: {pin}\n'
               '      PYTHONDONTWRITEBYTECODE: "1"\n'
               '      GOTOOLCHAIN: local\n'
               '    steps:\n'
               '      - name: Checkout consumer commit\n'
               f'        uses: actions/checkout@{_CHECKOUT_SHA}\n'
               '        with:\n          persist-credentials: false\n'
               '      - name: Pinned Python interpreter\n'
               f'        uses: actions/setup-python@{SETUP_PYTHON_SHA}\n'
               f"        with:\n          python-version: '{job["toolchain_version"] if job["provider"]=="python-shell" else "3.12"}'\n"
               f'{setup_go}{cache_step}'
               '      - name: Acquire immutable Devtool runtime\n'
               '        shell: bash\n        run: |\n'
               '          set -euo pipefail\n'
               '          test "$(git rev-parse HEAD)" = "$GITHUB_SHA"\n'
               '          git clone --no-checkout https://github.com/Mikeyphw/devtool.git "$RUNNER_TEMP/devtool-source"\n'
               '          git -C "$RUNNER_TEMP/devtool-source" checkout --detach "$DEVTOOL_SOURCE_SHA"\n'
               '          test "$(git -C "$RUNNER_TEMP/devtool-source" rev-parse HEAD)" = "$DEVTOOL_SOURCE_SHA"\n'
               '          python3 -m venv "$RUNNER_TEMP/devtool-venv"\n'
               '          "$RUNNER_TEMP/devtool-venv/bin/python" -m pip install --disable-pip-version-check "$RUNNER_TEMP/devtool-source"\n'
               f'{downloads}{py_requirements}'
               '      - name: Verify dependencies and run native target-local node\n'
               '        shell: bash\n        run: |\n'
               '          set -euo pipefail\n'
               '          "$RUNNER_TEMP/devtool-venv/bin/python" -m devtool.actions_graph_export run-node '
               f'--root "$GITHUB_WORKSPACE" --target {target} --workflow {workflow} '
               f'--devtool-sha {pin} --graph-sha {digest} --job-id {jid} '
               f' --host-profiles {",".join(graph.get("toolchain_profiles",[]))}'
               + (' --cache-locks' if graph['cache']!='disabled' else '')
               + ' --inputs "$RUNNER_TEMP/rm06-inputs" --outputs "$RUNNER_TEMP/rm06-output"\n'
               f'{upload}'
               '      - name: Record local run attempt (not authentication)\n'
               '        if: always()\n        shell: bash\n        run: |\n'
               '          set -euo pipefail\n'
               f'          mkdir -p "$RUNNER_TEMP/rm06-evidence/{jid}"\n'
               f'          printf "%s\\n" "$GITHUB_SHA/$GITHUB_RUN_ID/$GITHUB_RUN_ATTEMPT/{jid}" > "$RUNNER_TEMP/rm06-evidence/{jid}/source-run.txt"\n'
               '      - name: Upload review evidence\n'
               '        if: always()\n'
               f'        uses: actions/upload-artifact@{_UPLOAD_SHA}\n'
               '        with:\n'
               f'          name: {namespace}-{jid}-evidence\n'
               f'          path: ${{{{ runner.temp }}}}/rm06-evidence/{jid}/\n'
               '          if-no-files-found: error\n          retention-days: 7\n')
        entries.append(entry)
    return preamble+''.join(entries)


def _gha_context(root: Path) -> tuple[str, str, int]:
    from .actions_adoption import _git
    commit=_git(root, 'rev-parse', 'HEAD')
    requested=os.environ.get('GITHUB_SHA','')
    run=os.environ.get('GITHUB_RUN_ID','')
    attempt=os.environ.get('GITHUB_RUN_ATTEMPT','')
    if requested!=commit or not run.isdecimal() or not attempt.isdecimal() or int(run)<1 or int(attempt)<1:
        raise ProviderError('RM-06 GitHub source/run/attempt identity unavailable or wrong')
    return requested,run,int(attempt)


def _load_manifest(directory: Path, *, graph: dict[str, Any], parent: dict[str, Any], source: str, run: str, attempt: int) -> list[tuple[str,Path]]:
    if directory.is_symlink() or (directory/'payload').is_symlink():
        raise ProviderError('RM-06 symlink artifact handoff directory')
    manifest_path=directory/'receipt.json'
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size>32768:
        raise ProviderError('RM-06 missing/unsafe handoff receipt')
    try: receipt=json.loads(manifest_path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc: raise ProviderError('RM-06 malformed receipt') from exc
    exact={'schema_version':SCHEMA, 'kind':'devtool.actions.node-handoff',
           'graph_sha256':graph['graph_sha256'], 'workflow_sha256':hashlib.sha256(workflow_yaml(graph).encode()).hexdigest(), 'source_commit':source,
           'run_id':run, 'attempt':attempt, 'producer_node':parent['node'],
           'producer_job':parent['id']}
    if not isinstance(receipt,dict) or any(receipt.get(k)!=v for k,v in exact.items()):
        raise ProviderError('RM-06 artifact source/run/attempt/producer mismatch')
    files=receipt.get('files')
    if not isinstance(files,list) or len(files)!=len(parent['outputs']):
        raise ProviderError('RM-06 handoff files do not match declared outputs')
    checked=[]
    if [r.get('path') for r in files if isinstance(r,dict)]!=parent['outputs']:
        raise ProviderError('RM-06 handoff output list differs from frozen graph')
    for row in files:
        if not isinstance(row,dict) or set(row)!={'path','sha256','size'}:
            raise ProviderError('RM-06 malformed handoff file entry')
        path=row['path'];file=_regular(directory/'payload',path)
        if type(row['size']) is not int or row['size']<0 or row['size']>MAX_TRANSFER_BYTES or file.stat().st_size!=row['size']:
            raise ProviderError('RM-06 handoff file size mismatch')
        if not isinstance(row['sha256'],str) or len(row['sha256'])!=64 or _hash_file(file)!=row['sha256']:
            raise ProviderError('RM-06 handoff digest mismatch')
        checked.append((path,file))
    return checked


def consume_handoffs(root: Path, input_dir: Path, graph: dict[str, Any], job: dict[str, Any], source: str, run: str, attempt: int) -> None:
    by_id={item['id']:item for item in graph['jobs']}
    if input_dir.is_symlink():
        raise ProviderError('RM-06 symlink input handoff root')
    checked=[]
    for dep in job['handoffs']:
        checked.extend(_load_manifest(input_dir/dep,graph=graph,parent=by_id[dep],source=source,run=run,attempt=attempt))
    # Verify ALL bytes and destinations before writing ANY file.
    for rel,_ in checked:
        dest=_regular(root,rel,must_exist=False)
        if dest.exists() or dest.is_symlink():
            raise ProviderError('RM-06 handoff would overwrite a source/previous output: '+rel)
    copied=[]
    try:
        for rel,src in checked:
            dst=_regular(root,rel,must_exist=False)
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(src,dst)
            copied.append(dst)
    except BaseException:
        for item in copied:
            item.unlink(missing_ok=True)
        raise


def stage_outputs(root: Path, outdir: Path, graph: dict[str, Any], job: dict[str, Any], source: str, run: str, attempt: int) -> None:
    if not job['outputs']:
        return
    staging=outdir/job['id']
    if staging.exists() or staging.is_symlink():
        raise ProviderError('RM-06 refuses to reuse an output staging directory')
    checked=[];total=0
    for rel in job['outputs']:
        path=_regular(root,rel)
        size=path.stat().st_size;total+=size
        if size>MAX_TRANSFER_BYTES or total>MAX_TRANSFER_BYTES:
            raise ProviderError('RM-06 output handoff exceeds byte budget')
        checked.append((rel,path,size,_hash_file(path)))
    staging.mkdir(parents=True)
    for rel,src,size,digest in checked:
        dst=staging/'payload'/rel
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(src,dst)
        if _hash_file(dst)!=digest:
            raise ProviderError('RM-06 changed output during staging')
    receipt={'schema_version':SCHEMA,'kind':'devtool.actions.node-handoff','graph_sha256':graph['graph_sha256'],
             'source_commit':source,'workflow_sha256':hashlib.sha256(workflow_yaml(graph).encode()).hexdigest(),'run_id':run,'attempt':attempt,'producer_node':job['node'],
             'producer_job':job['id'], 'files':[{'path':rel,'size':size,'sha256':sha} for rel,_,size,sha in checked],
             'authenticated':False, 'release_qualified':False}
    (staging/'receipt.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')


def run_node(root: Path, *, target: str, workflow: str, devtool_sha: str, graph_sha: str,
             job_id: str, inputs: Path, outputs: Path, host_profiles: str = '', cache_locks: bool = False) -> dict[str, Any]:
    root=root.resolve()
    graph=compile_graph(root,target=target,workflow=workflow,devtool_sha=devtool_sha,
                        host_profiles=host_profiles,cache_locks=cache_locks)
    if graph_sha!=graph['graph_sha256']:
        raise ProviderError('RM-06 frozen graph digest changed before native node execution')
    from .actions_adoption import _git
    source,run,attempt=_gha_context(root)
    selected=next((item for item in graph['jobs'] if item['id']==job_id),None)
    if selected is None:
        raise ProviderError('RM-06 unknown compiled GitHub node')
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        expected={'ubuntu-24.04-x64':'Linux','macos-15-intel':'macOS'}[selected['profile_id']]
        if os.environ.get('RUNNER_OS')!=expected or os.environ.get('RUNNER_ARCH')!='X64':
            raise ProviderError('RM-07 selected hosted profile does not match runner OS/architecture')
    consume_handoffs(root,inputs,graph,selected,source,run,attempt)
    for path in selected['outputs']:
        out=_regular(root,path,must_exist=False)
        if out.exists() or out.is_symlink():
            raise ProviderError('RM-06 node output already exists before execution: '+path)
    cfg=load_wrapper_project_config(str(root),None)
    from .workflow_runtime import build_direct_job_specs
    from .runners import run_specs
    specs=build_direct_job_specs(cfg, f'{target}:job:{selected["job"]}',target_name=target)
    if not specs:
        raise ProviderError('RM-06 native job resolver returned no executable specs')
    if selected['auto_coverage']:
        # Instrument the same reviewed native target-local job, rather than
        # running an extra unowned test command from the hosted exporter.
        from dataclasses import replace
        from tempfile import TemporaryDirectory
        if len(specs)!=1 or specs[0].argv[:3] not in (['python','-m','pytest'],['python3','-m','pytest']):
            raise ProviderError('RM-08 coverage native resolver diverged from reviewed pytest job')
        report_path=_regular(root,'dist/coverage.xml',must_exist=False)
        if report_path.exists() or report_path.is_symlink():
            raise ProviderError('RM-08 coverage output exists before instrumentation')
        report_path.parent.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(prefix='devtool-coverage-') as temp:
            env=dict(specs[0].env)
            env['COVERAGE_FILE']=str(Path(temp)/'.coverage')
            original=specs[0]
            tests=replace(original,name=original.name+' [coverage tests]',
                          argv=[original.argv[0],'-m','coverage','run','-m','pytest',*original.argv[3:]],env=env)
            xml=replace(original,name=original.name+' [coverage report]',
                        argv=[original.argv[0],'-m','coverage','xml','-o','dist/coverage.xml'],env=env)
            results,_=run_specs(cfg,[tests],verbose=True,dry_run=False,continue_on_error=False)
            if any(r.returncode!=0 or r.skipped for r in results):
                raise ProviderError('RM-08 instrumented native pytest job failed; no coverage report accepted')
            reports,_=run_specs(cfg,[xml],verbose=True,dry_run=False,continue_on_error=False)
            results+=reports
    else:
        results,_=run_specs(cfg,specs,verbose=True,dry_run=False,continue_on_error=False)
    if any(result.returncode!=0 or result.skipped for result in results):
        raise ProviderError('RM-06 native target-local job did not succeed')
    smoke=[]
    if selected['sdist_smoke']:
        from .actions_distributions import distribution_contract, _python_source
        from .actions_rm08_projects import smoke_sdist
        contract=distribution_contract(root,target=target,workflow=workflow)
        sd=next((item['expected'] for item in contract['entries']
                 if item['expected']['path'] in selected['outputs'] and item['kind']=='sdist'),None)
        if sd is None:raise ProviderError('RM-08 sdist smoke lost owned archive identity')
        scripts=_python_source(root)['entrypoints']
        if len(scripts)!=1:raise ProviderError('RM-08 sdist smoke requires exactly one console script')
        smoke.append(smoke_sdist(root,sd['path'],name=sd['name'],version=sd['version'],entrypoint=scripts[0]))
    if selected['wheel_smoke']:
        from .actions_distributions import distribution_contract
        from .actions_rm08_projects import smoke_wheel
        contract=distribution_contract(root,target=target,workflow=workflow)
        wheel=next((item['expected'] for item in contract['entries']
                    if item['expected']['path'] in selected['outputs'] and item['kind']=='wheel'),None)
        if wheel is None:
            raise ProviderError('RM-08 wheel smoke lost owned distribution contract')
        smoke.append(smoke_wheel(root,wheel['path'],name=wheel['name'],
                                 version=wheel['version'],entrypoint=wheel['entrypoints'][0]))
    repeat_build=[]
    if selected['reproducible_go']:
        from .actions_rm08_projects import verify_go_repeat_build
        repeat_build.append(verify_go_repeat_build(root,selected['go_command'],selected['go_env']))
    coverage=[]
    if selected['coverage_report']:
        from .actions_rm08_projects import merge_coverage_xml
        if 'dist/coverage.xml' not in selected['outputs']:
            raise ProviderError('RM-08 coverage reporter must declare its output')
        coverage.append(merge_coverage_xml(root,['dist/coverage.xml']))
    stage_outputs(root,outputs,graph,selected,source,run,attempt)
    return {'status':'LOCAL_JOB_SUCCEEDED', 'node':selected['node'], 'job_id':job_id,
            'graph_sha256':graph_sha, 'workflow_sha256':hashlib.sha256(workflow_yaml(graph).encode()).hexdigest(), 'github_source':source,'run_id':run,'attempt':attempt,
            'wheel_smoke':smoke,'coverage':coverage,'repeat_build':repeat_build,'uploaded_artifacts_authenticated':False,'release_qualified':False}


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description='Internal RM-06 reviewed multi-job runner; no auth/qualification claim')
    parser.add_argument('action',choices=['run-node'])
    for flag in ('root','target','workflow','devtool-sha','graph-sha','job-id','inputs','outputs'):
        parser.add_argument('--'+flag,required=True)
    parser.add_argument('--host-profiles',default='')
    parser.add_argument('--cache-locks',action='store_true')
    ns=parser.parse_args(argv)
    try:
        result=run_node(Path(ns.root),target=ns.target,workflow=ns.workflow,devtool_sha=ns.devtool_sha,
                        graph_sha=ns.graph_sha,job_id=ns.job_id,inputs=Path(ns.inputs),outputs=Path(ns.outputs),
                        host_profiles=ns.host_profiles,cache_locks=ns.cache_locks)
    except (ProviderError, ValueError, OSError) as exc:
        print(f'RM06_REFUSED: {exc}',file=sys.stderr)
        return 2
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
