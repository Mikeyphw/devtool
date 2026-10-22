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


def compile_graph(root: Path, *, target: str, workflow: str, devtool_sha: str) -> dict[str, Any]:
    """Pure, deterministic compiler. Single pinned Ubuntu profile; RM-07 owns matrix/profile expansion."""
    plan = provider_plan(root, target=target, workflow=workflow, devtool_sha=devtool_sha)
    rows = plan['nodes']
    if not 1 <= len(rows) <= MAX_NODES:
        raise ProviderError('RM-06 bounded multi-job export accepts 1..24 nodes; use single-job fallback')
    cfg = load_wrapper_project_config(str(root.resolve()), None)
    native_jobs = cfg.targets[plan['target']].jobs
    node_to_id: dict[str, str] = {}
    jobs: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        # Position is stable within the native graph; never interpolate user-provided node keys in YAML identifiers.
        jid = f'node-{index:02d}'
        node_to_id[row['node']] = jid
        outs = list(row['outputs'])
        if len(outs) > MAX_TRANSFER_FILES:
            raise ProviderError('RM-06 node output count exceeds transfer cap')
        if len(set(outs)) != len(outs):
            raise ProviderError('RM-06 duplicate output path in one node')
        for path in outs:
            _path(path)
            from .actions_language_providers import _tracked
            if _tracked(root, path):
                raise ProviderError('RM-06 output must not overwrite tracked source: '+path)
        jobs.append({'id':jid, 'node':row['node'], 'job':row['job'], 'depends_on':list(row['depends_on']),
                     'outputs':sorted(outs), 'inputs':list(native_jobs[row['job']].inputs),
                     'provider':row['provider']['provider'], 'needs':[], 'handoffs':[]})
    owners: dict[str, str] = {}
    for job in jobs:
        for output in job['outputs']:
            if output in owners:
                raise ProviderError('RM-06 multiple DAG nodes produce one output: ' + output)
            owners[output] = job['node']
    # Reject shared writes to one output or consumed generated inputs without explicit direct dependencies.
    for job,row in zip(jobs,rows):
        deps = list(job['depends_on'])
        if len(set(deps)) != len(deps) or any(dep not in node_to_id for dep in deps):
            raise ProviderError('RM-06 invalid native dependency projection')
        job['needs'] = [node_to_id[dep] for dep in deps]
        job['handoffs'] = [node_to_id[dep] for dep in deps if jobs[int(node_to_id[dep].split('-')[1])-1]['outputs']]
        if len(job['handoffs']) > MAX_NODES:
            raise ProviderError('RM-06 handoff fanin exceeds cap')
        for inp in job['inputs']:  # native declared input paths, not just language locks
            if any(c in str(inp) for c in '*?[]') and owners:
                raise ProviderError('RM-06 cannot prove generated-input ownership through globs')
            if not isinstance(inp, str):
                raise ProviderError('RM-06 nonliteral node input')
            if inp in owners and owners[inp] != job['node'] and owners[inp] not in deps:
                raise ProviderError('RM-06 generated input requires direct producer dependency: ' + inp)
        # Future runner/OS toolchains belong to RM-07; no implicit cross-host or matrix fan-out.
        if job['provider'] not in ('go', 'python-shell'):
            raise ProviderError('RM-06 refuses unreviewed hosted provider')
    immutable = {'schema_version': SCHEMA, 'kind': 'devtool.actions.export-graph',
                 'provider_plan_sha256':plan['plan_sha256'], 'target':plan['target'],
                 'workflow':plan['workflow'], 'runtime_sha':plan['runtime_sha'],
                 'runner': 'ubuntu-24.04', 'matrix': [{'os':'ubuntu-24.04','arch':'x86_64'}],
                 'cache': 'disabled', 'changed_targets':'full-dag', 'jobs':jobs}
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
        setup_go=(f'      - name: Pinned Go toolchain\n'
                  f'        uses: actions/setup-go@{_SETUP_GO_SHA}\n'
                  '        with:\n          go-version-file: go.mod\n          cache: false\n') if job['provider']=='go' else ''
        downloads=''
        for dep in job['handoffs']:
            downloads += (f'      - name: Download {dep} byte handoff\n'
                          f'        uses: actions/download-artifact@{_DOWNLOAD_SHA}\n'
                          '        with:\n'
                          f'          name: {_artifact_name(namespace,dep)}\n'
                          f'          path: ${{{{ runner.temp }}}}/rm06-inputs/{dep}\n')
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
               '    runs-on: ubuntu-24.04\n    timeout-minutes: 60\n'
               '    env:\n'
               f'      DEVTOOL_SOURCE_SHA: {pin}\n'
               '      PYTHONDONTWRITEBYTECODE: "1"\n'
               '    steps:\n'
               '      - name: Checkout consumer commit\n'
               f'        uses: actions/checkout@{_CHECKOUT_SHA}\n'
               '        with:\n          persist-credentials: false\n'
               '      - name: Pinned Python interpreter\n'
               f'        uses: actions/setup-python@{SETUP_PYTHON_SHA}\n'
               "        with:\n          python-version: '3.12'\n"
               f'{setup_go}'
               '      - name: Acquire immutable Devtool runtime\n'
               '        shell: bash\n        run: |\n'
               '          set -euo pipefail\n'
               '          test "$(git rev-parse HEAD)" = "$GITHUB_SHA"\n'
               '          git clone --no-checkout https://github.com/Mikeyphw/devtool.git "$RUNNER_TEMP/devtool-source"\n'
               '          git -C "$RUNNER_TEMP/devtool-source" checkout --detach "$DEVTOOL_SOURCE_SHA"\n'
               '          test "$(git -C "$RUNNER_TEMP/devtool-source" rev-parse HEAD)" = "$DEVTOOL_SOURCE_SHA"\n'
               '          python3 -m venv "$RUNNER_TEMP/devtool-venv"\n'
               '          "$RUNNER_TEMP/devtool-venv/bin/python" -m pip install --disable-pip-version-check "$RUNNER_TEMP/devtool-source"\n'
               f'{downloads}'
               '      - name: Verify dependencies and run native target-local node\n'
               '        shell: bash\n        run: |\n'
               '          set -euo pipefail\n'
               '          "$RUNNER_TEMP/devtool-venv/bin/python" -m devtool.actions_graph_export run-node '
               f'--root "$GITHUB_WORKSPACE" --target {target} --workflow {workflow} '
               f'--devtool-sha {pin} --graph-sha {digest} --job-id {jid} '
               ' --inputs "$RUNNER_TEMP/rm06-inputs" --outputs "$RUNNER_TEMP/rm06-output"\n'
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
             job_id: str, inputs: Path, outputs: Path) -> dict[str, Any]:
    root=root.resolve()
    graph=compile_graph(root,target=target,workflow=workflow,devtool_sha=devtool_sha)
    if graph_sha!=graph['graph_sha256']:
        raise ProviderError('RM-06 frozen graph digest changed before native node execution')
    from .actions_adoption import _git
    source,run,attempt=_gha_context(root)
    selected=next((item for item in graph['jobs'] if item['id']==job_id),None)
    if selected is None:
        raise ProviderError('RM-06 unknown compiled GitHub node')
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
    results,_=run_specs(cfg,specs,verbose=True,dry_run=False,continue_on_error=False)
    if any(result.returncode!=0 or result.skipped for result in results):
        raise ProviderError('RM-06 native target-local job did not succeed')
    stage_outputs(root,outputs,graph,selected,source,run,attempt)
    return {'status':'LOCAL_JOB_SUCCEEDED', 'node':selected['node'], 'job_id':job_id,
            'graph_sha256':graph_sha, 'workflow_sha256':hashlib.sha256(workflow_yaml(graph).encode()).hexdigest(), 'github_source':source,'run_id':run,'attempt':attempt,
            'uploaded_artifacts_authenticated':False,'release_qualified':False}


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description='Internal RM-06 reviewed multi-job runner; no auth/qualification claim')
    parser.add_argument('action',choices=['run-node'])
    for flag in ('root','target','workflow','devtool-sha','graph-sha','job-id','inputs','outputs'):
        parser.add_argument('--'+flag,required=True)
    ns=parser.parse_args(argv)
    try:
        result=run_node(Path(ns.root),target=ns.target,workflow=ns.workflow,devtool_sha=ns.devtool_sha,
                        graph_sha=ns.graph_sha,job_id=ns.job_id,inputs=Path(ns.inputs),outputs=Path(ns.outputs))
    except (ProviderError, ValueError, OSError) as exc:
        print(f'RM06_REFUSED: {exc}',file=sys.stderr)
        return 2
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
