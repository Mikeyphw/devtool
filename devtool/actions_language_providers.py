"""NEXT-03: allowlisted hosted-language provider contracts over Devtool's native DAG.

No dynamic plugins, no new scheduler, dispatcher, artifact authority or credential
access.  Adapters admit *declarations*, not authenticate remote execution.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Protocol

from .actions_adoption import AdoptionError, _git, _inside_repo, _safe_slug, _sha, _yaml, _atomic
from .config import load_wrapper_project_config
from .evidence_kernel import graph_host_admission
from .workflow_graph import graph_from_workflow, WorkflowGraphError

SCHEMA_VERSION = 1
IDENTITY = 'devtool.actions.language-providers'
SETUP_PYTHON_SHA = 'a26af69be951a213d495a4c3e4e4022e16d87065'  # v5.6.0
MAX_NODES = 32
MAX_ARGV = 48
_ALLOWED_ARG = re.compile(r'[A-Za-z0-9_./:+,=@%^-]{1,160}\Z')
_GO_VERSION = re.compile(r'(?m)^go\s+(1\.(?:2[0-9]|[3-9][0-9])(?:\.\d+)?)\s*$')


class ProviderError(AdoptionError):
    """Host admission or generated workflow refused without mutation."""


class LanguageProvider(Protocol):
    api_version: int
    provider_id: str
    def inspect(self, root: Path, job: Any, argv: list[str]) -> dict[str, Any]: ...


def _tracked(root: Path, rel: str) -> bool:
    p = Path(rel)
    if not rel or p.is_absolute() or '\\' in rel or any(part in ('..', '.', '') for part in p.parts):
        return False
    try:
        candidate = _inside_repo(root, rel)
        if not candidate.is_file():
            return False
        proc = subprocess.run(['git', '-C', str(root), 'ls-files', '--error-unmatch', '--', rel],
                              capture_output=True, timeout=10)
        return proc.returncode == 0
    except (OSError, AdoptionError, subprocess.TimeoutExpired):
        return False


def _argv(argv: list[str]) -> None:
    if not (1 <= len(argv) <= MAX_ARGV) or any(not isinstance(a, str) or not _ALLOWED_ARG.fullmatch(a) for a in argv):
        raise ProviderError('NEXT-03 refuses unsafe or unbounded job argv (no shell expressions/expansion)')
    if any(a in ('-c', '-lc', '--exec', '-e') for a in argv[1:]):
        raise ProviderError('NEXT-03 refuses inline code, arbitrary shell or eval flags')


@dataclass(frozen=True)
class GoProvider:
    api_version: int = SCHEMA_VERSION
    provider_id: str = 'go'

    def inspect(self, root: Path, job: Any, argv: list[str]) -> dict[str, Any]:
        _argv(argv)
        if Path(argv[0]).name != 'go' or len(argv) < 2 or argv[1] not in ('test', 'vet', 'build'):
            raise ProviderError('Go adapter admits only go test/vet/build argv, no shell wrapper or go run')
        if not _tracked(root, 'go.mod'):
            raise ProviderError('Go adapter requires a tracked, non-symlink go.mod at repository root')
        if (root / 'go.work').exists():
            raise ProviderError('Go workspace support is deferred to NEXT-04')
        raw = (root / 'go.mod').read_text(encoding='utf-8')
        version = _GO_VERSION.search(raw)
        if not version:
            raise ProviderError('Go toolchain version absent or unsupported in go.mod')
        # Don't infer CGO or native target compatibility from hosted x86_64.
        if any(a.startswith(('GOOS=', 'GOARCH=', 'CGO_ENABLED=')) for a in argv):
            raise ProviderError('Go cross-target/CGO admission belongs to NEXT-04')
        return {'provider': 'go', 'api_version': self.api_version, 'toolchain': 'go',
                'version': version.group(1), 'version_source': 'go.mod',
                'lockfiles': ['go.mod'] + (['go.sum'] if _tracked(root, 'go.sum') else []),
                'cache_enabled': False, 'report_adapter': 'go-json',
                'output_role': 'source-only-unverified', 'release_qualified': False}


@dataclass(frozen=True)
class PythonShellProvider:
    api_version: int = SCHEMA_VERSION
    provider_id: str = 'python-shell'

    def inspect(self, root: Path, job: Any, argv: list[str]) -> dict[str, Any]:
        _argv(argv)
        if Path(argv[0]).name not in ('python', 'python3'):
            raise ProviderError('Python adapter refuses shell scripts and unreviewed external programs')
        if len(argv) < 3 or argv[1] != '-m' or argv[2] not in ('pytest', 'unittest', 'compileall', 'ruff'):
            raise ProviderError('Python adapter admits only python -m pytest/unittest/compileall/ruff')
        if 'pytest' in argv and 'compileall' in argv:
            raise ProviderError('Ambiguous Python module argv')
        lockfiles = [name for name in ('uv.lock', 'poetry.lock', 'requirements.txt', 'pdm.lock') if _tracked(root, name)]
        # Toolchain is installed by a pinned GitHub Action; full package build/dependency
        # resolution and lock enforcement are NEXT-04, so no tests/release assertions.
        return {'provider': 'python-shell', 'api_version': self.api_version,
                'toolchain': 'python', 'version': '3.12',
                'version_source': 'bounded-next03-host-profile',
                'lockfiles': lockfiles, 'cache_enabled': False,
                'report_adapter': 'pytest-junit', 'output_role': 'source-only-unverified',
                'release_qualified': False}


# The only provider registry: immutable built-ins, no entry-point/network/plugin loading.
PROVIDERS: dict[str, LanguageProvider] = {'go': GoProvider(), 'python-shell': PythonShellProvider()}


def _digest(row: Any) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _provider_for(job: Any) -> LanguageProvider:
    meta = job.metadata or {}
    flag = meta.get('ci_toolchain')
    argv = list(job.command or [])
    name = Path(argv[0]).name if argv else ''
    go = (flag == 'go' or job.runner == 'go' or name == 'go' or 'go.mod' in job.inputs)
    if flag not in (None, 'go', 'python-shell'):
        raise ProviderError('No reviewed language provider for ci_toolchain ' + str(flag)[:50])
    if go and flag == 'python-shell':
        raise ProviderError('Go source declaration conflicts with Python toolchain')
    if not go and flag == 'go':
        raise ProviderError('Go toolchain requires a direct go command')
    return PROVIDERS['go' if go else 'python-shell']


def provider_plan(root: Path, *, target: str, workflow: str, devtool_sha: str,
                  expect_plan_sha: str = '') -> dict[str, Any]:
    root = root.resolve()
    target = _safe_slug(target, role='Target')
    workflow = _safe_slug(workflow, role='Workflow')
    devtool_sha = _sha(devtool_sha)
    if not root.is_dir() or _git(root, 'rev-parse', '--is-inside-work-tree') != 'true':
        raise ProviderError('Provider export requires a Git worktree')
    try:
        cfg = load_wrapper_project_config(str(root), None)
    except (ValueError, SystemExit) as exc:
        raise ProviderError('Invalid consumer Devtool contract: ' + str(exc)[:160]) from None
    t = cfg.targets.get(target)
    if t is None or workflow not in t.workflows:
        raise ProviderError('Target and named workflow must be explicitly configured')
    try:
        graph = graph_from_workflow(target, t.workflows[workflow])
    except WorkflowGraphError as exc:
        raise ProviderError(str(exc)) from None
    if not (1 <= len(graph.nodes) <= MAX_NODES):
        raise ProviderError('NEXT-03 DAG size must be between 1 and 32 nodes')
    admission = graph_host_admission(cfg, graph, host_os='linux', host_arch='x86_64',
                                     toolchains=('go', 'python-shell'))
    if not admission['all_eligible']:
        reasons = [r['node']+':'+','.join(r['eligibility']['reasons']) for r in admission['nodes'] if not r['eligibility']['eligible']]
        raise ProviderError('Hosted node admission blocked: ' + '; '.join(reasons)[:320])
    rows: list[dict[str, Any]] = []
    for node in graph.topological_order():
        if node.ref.kind != 'job' or node.ref.resolution_scope != 'target':
            raise ProviderError('NEXT-03 accepts only direct target-owned jobs, not nested/cross-repo/runner workflows')
        local = node.ref.resolution_local_name or node.ref.name
        job = t.jobs.get(local)
        if job is None:
            raise ProviderError('Native DAG node has no target-owned job')
        meta = job.metadata or {}
        if job.runner not in ('command','python','go') or job.shell_command or job.action or job.gradle_tasks:
            raise ProviderError('Only explicit argv target-local command/python/go jobs are reviewable')
        if job.env or job.execution_environment not in ('auto', 'native'):
            raise ProviderError('NEXT-03 refuses custom environment variables or native/remote execution selectors')
        if any(meta.get(key) is True for key in ('device_required','root_required','signing_required','release_signed','termux_native_required')):
            raise ProviderError('Device, root, Termux and signing cannot be hosted')
        argv = list(job.command or [])
        provider = _provider_for(job)
        declaration = provider.inspect(root, job, argv)
        if job.root not in ('', '.', str(root), str(root.resolve())):
            raise ProviderError('NEXT-03 only exports jobs rooted at the repository (subdirectory roots need review)')
        if any(not isinstance(v, str) or v.startswith('/') or '..' in Path(v).parts or '\\' in v for v in job.outputs):
            raise ProviderError('Job output path is not bounded')
        rows.append({'node':node.key, 'job':local, 'depends_on':list(node.depends_on),
                     'argv_sha256': _digest(argv), 'inputs_sha256':_digest(list(job.inputs)),
                     'provider':declaration})
    providers = sorted({r['provider']['provider'] for r in rows})
    head, tree = _git(root, 'rev-parse', 'HEAD'), _git(root, 'rev-parse', 'HEAD^{tree}')
    # A generated workflow is itself a new Git commit: binding the frozen review
    # digest to the *pre-export* HEAD would make the first CI run fail. Instead
    # bind reviewed config, provider lockfiles and selected native job graph.
    tracked_sources = ['.devtool.toml'] + sorted({p for row in rows for p in row['provider']['lockfiles']})
    source_digests = {p: hashlib.sha256((root / p).read_bytes()).hexdigest()
                      for p in tracked_sources if _tracked(root, p)}
    if '.devtool.toml' not in source_digests:
        raise ProviderError('Provider review requires tracked .devtool.toml')
    bound = {'source_digests':source_digests, 'target':target,
             'workflow':workflow, 'runtime_sha':devtool_sha, 'nodes':rows,
             'host':{'os':'ubuntu-24.04','arch':'x86_64'}, 'providers':providers}
    digest = _digest(bound)
    if expect_plan_sha and expect_plan_sha != digest:
        raise ProviderError('Selected source/DAG/toolchain differs from frozen provider plan')
    return {'schema_version':SCHEMA_VERSION,'kind':IDENTITY,'status':'LOCALLY_ELIGIBLE',
            'read_only':True,'provider_api_version':SCHEMA_VERSION,'providers':providers,
            'consumer_commit':head,'consumer_tree':tree,'target':target,'workflow':workflow,
            'runtime_sha':devtool_sha,'reviewed_source_digests':source_digests,'nodes':rows,'native_dag_order':[r['node'] for r in rows],
            'plan_sha256':digest,'export_job_strategy':'single-hosted-job-native-dag',
            'host_matrix':[{'os':'ubuntu-24.04','arch':'x86_64'}],
            'cache':'disabled-until-reviewed-lock-policy',
            'changed_target_selection':'run-full-dag-unless-authoritatively-proven',
            'test_evidence':'not_verified','remote_dispatched':False,
            'artifact_authenticated':False,'release_qualified':False}


def _workflow_yaml(plan: dict[str, Any]) -> str:
    from .actions_adoption import _CHECKOUT_SHA, _UPLOAD_SHA, _SETUP_GO_SHA
    target, workflow, pin = plan['target'], plan['workflow'], plan['runtime_sha']
    namespace = f'devtool-{target}-{workflow}-providers'
    # All dynamic strings are slug/SHA constants checked by provider_plan.
    go_setup = (f'''      - name: Pinned Go toolchain
        uses: actions/setup-go@{_SETUP_GO_SHA}
        with:
          go-version-file: go.mod
          cache: false
''' if 'go' in plan['providers'] else '')
    return f'''# Generated by Devtool NEXT-03: native DAG remains the execution authority.
# Provider plan SHA-256: {plan['plan_sha256']}
name: {namespace}
on:
  workflow_dispatch:
permissions:
  contents: read
concurrency:
  group: {namespace}-${{{{ github.ref }}}}
  cancel-in-progress: false
jobs:
  native-dag:
    strategy:
      fail-fast: false
      matrix:
        include:
          - os: ubuntu-24.04
            arch: x86_64
    runs-on: ${{{{ matrix.os }}}}
    timeout-minutes: 60
    env:
      DEVTOOL_SOURCE_SHA: {pin}
      PYTHONDONTWRITEBYTECODE: "1"
    steps:
      - name: Checkout exact consumer source
        uses: actions/checkout@{_CHECKOUT_SHA}
        with:
          persist-credentials: false
      - name: Verify consumer HEAD before build
        shell: bash
        run: |
          set -euo pipefail
          test "$(git rev-parse HEAD)" = "$GITHUB_SHA"
          test -f .devtool.toml
      - name: Pinned Python host interpreter
        uses: actions/setup-python@{SETUP_PYTHON_SHA}
        with:
          python-version: '3.12'
{go_setup}      - name: Acquire frozen Devtool runtime
        shell: bash
        run: |
          set -euo pipefail
          git clone --no-checkout https://github.com/Mikeyphw/devtool.git "$RUNNER_TEMP/devtool-source"
          git -C "$RUNNER_TEMP/devtool-source" checkout --detach "$DEVTOOL_SOURCE_SHA"
          test "$(git -C "$RUNNER_TEMP/devtool-source" rev-parse HEAD)" = "$DEVTOOL_SOURCE_SHA"
          python3 -m venv "$RUNNER_TEMP/devtool-venv"
          "$RUNNER_TEMP/devtool-venv/bin/python" -m pip install --disable-pip-version-check "$RUNNER_TEMP/devtool-source"
      - name: Verify frozen provider and native DAG plan
        shell: bash
        run: |
          set -euo pipefail
          "$RUNNER_TEMP/devtool-venv/bin/python" -m devtool --repo "$GITHUB_WORKSPACE" actions provider-plan --target {target} --workflow {workflow} --devtool-sha {pin} --expect-plan-sha {plan['plan_sha256']} --json > "$RUNNER_TEMP/devtool-provider-plan.json"
          "$RUNNER_TEMP/devtool-venv/bin/python" -m devtool --repo "$GITHUB_WORKSPACE" plan --target {target} {workflow} --json > "$RUNNER_TEMP/devtool-plan.json"
          "$RUNNER_TEMP/devtool-venv/bin/python" -m devtool --repo "$GITHUB_WORKSPACE" preflight --target {target} --workflow {workflow} --json > "$RUNNER_TEMP/devtool-preflight.json"
      - name: Run native DAG (one owner, no duplicated GitHub scheduler)
        shell: bash
        run: |
          set -euo pipefail
          "$RUNNER_TEMP/devtool-venv/bin/python" -m devtool --repo "$GITHUB_WORKSPACE" --machine-file "$RUNNER_TEMP/devtool-invoke.json" invoke --target {target} {workflow}
      - name: Record source-bound evidence, never release authority
        if: always()
        shell: bash
        run: |
          set -euo pipefail
          mkdir -p "$RUNNER_TEMP/devtool-provider-evidence"
          printf '%s\\n' "$GITHUB_SHA" > "$RUNNER_TEMP/devtool-provider-evidence/consumer-source.txt"
          printf '%s\\n' "$DEVTOOL_SOURCE_SHA" > "$RUNNER_TEMP/devtool-provider-evidence/devtool-source.txt"
          printf '%s\\n' "$GITHUB_RUN_ID/$GITHUB_RUN_ATTEMPT" > "$RUNNER_TEMP/devtool-provider-evidence/github-run-attempt.txt"
          for f in devtool-provider-plan.json devtool-plan.json devtool-preflight.json devtool-invoke.json; do
            if test -f "$RUNNER_TEMP/$f"; then
              cp "$RUNNER_TEMP/$f" "$RUNNER_TEMP/devtool-provider-evidence/$f"
            fi
          done
          (cd "$RUNNER_TEMP/devtool-provider-evidence" && sha256sum ./* > SHA256SUMS)
      - name: Upload review evidence, not a qualified distribution
        if: always()
        uses: actions/upload-artifact@{_UPLOAD_SHA}
        with:
          name: {namespace}-evidence
          path: ${{{{ runner.temp }}}}/devtool-provider-evidence/
          if-no-files-found: error
          retention-days: 14
'''


def provider_export(root: Path, *, target: str, workflow: str, devtool_sha: str,
                    write: bool = False, verify: bool = False) -> dict[str, Any]:
    if write and verify:
        raise ProviderError('Export --yes and --verify are mutually exclusive')
    plan = provider_plan(root,target=target,workflow=workflow,devtool_sha=devtool_sha)
    root = root.resolve()
    dest = f'.github/workflows/devtool-{target}-{workflow}-providers.yml'
    try:
        path = _inside_repo(root, dest)
    except AdoptionError as exc:
        raise ProviderError(str(exc)) from None
    content = _workflow_yaml(plan).encode()
    digest = hashlib.sha256(content).hexdigest()
    result = {**plan, 'workflow_path':dest, 'workflow_sha256':digest,
              'writes_performed':False,'remote_dispatch':False}
    if verify:
        if not path.is_file() or path.is_symlink() or path.read_bytes() != content:
            raise ProviderError('Generated provider workflow missing or changed')
        result['status'] = 'LOCAL_WORKFLOW_BYTES_VERIFIED_NOT_REMOTE'
        return result
    if path.exists() or path.is_symlink():
        raise ProviderError('Refusing to overwrite an existing workflow, even if identical; use --verify')
    if not write:
        result['status'] = 'REVIEW_ONLY'
        return result
    if _git(root,'status','--porcelain','--untracked-files=all'):
        raise ProviderError('Provider export requires clean Git source')
    if _git(root,'rev-parse','HEAD') != plan['consumer_commit'] or _git(root,'rev-parse','HEAD^{tree}') != plan['consumer_tree']:
        raise ProviderError('Source identity changed after planning')
    if provider_plan(root,target=target,workflow=workflow,devtool_sha=devtool_sha)['plan_sha256'] != plan['plan_sha256']:
        raise ProviderError('Provider plan changed after review')
    _atomic(path, content)
    result['status'] = 'LOCAL_WORKFLOW_WRITTEN_NOT_REMOTE'
    result['writes_performed'] = True
    return result


def provider_doctor(root: Path, *, target: str = '') -> dict[str, Any]:
    root = root.resolve()
    cfg = load_wrapper_project_config(str(root), None)
    if target and target not in cfg.targets:
        raise ProviderError('Unknown configured target')
    results=[]
    for name,t in sorted(cfg.targets.items()):
        if target and target != name: continue
        for wf in sorted(t.workflows):
            try:
                # Doctor only needs a syntactically valid runtime pin for review.
                entry = provider_plan(root,target=name,workflow=wf,devtool_sha='0'*40)
                results.append({'target':name,'workflow':wf,'state':'locally_eligible',
                                'providers':entry['providers'],'node_count':len(entry['nodes'])})
            except (ProviderError, ValueError) as exc:
                results.append({'target':name,'workflow':wf,'state':'blocked',
                                'reason':str(exc)[:280]})
    return {'schema_version':SCHEMA_VERSION, 'kind':IDENTITY+'.doctor',
            'read_only':True,'registered_providers':[{'id':key,'api_version':provider.api_version}
                             for key,provider in sorted(PROVIDERS.items())],
            'workflows':results,'source_attested':False,'host_executed':False,
            'release_qualified':False}
