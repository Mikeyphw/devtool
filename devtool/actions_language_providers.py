"""NEXT-03: allowlisted hosted-language provider contracts over Devtool's native DAG.

No dynamic plugins, no new scheduler, dispatcher, artifact authority or credential
access.  Adapters admit *declarations*, not authenticate remote execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Mapping, Protocol

from .actions_adoption import AdoptionError, _git, _inside_repo, _safe_slug, _sha, _yaml, _atomic
from .config import load_wrapper_project_config
from .evidence_kernel import graph_host_admission
from .contracts_v2 import ContractError, resolve_provider, reviewed_providers, host_eligibility_v2
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
        from .actions_rm08_projects import go_workspace_manifest, ProjectReviewError
        if Path(argv[0]).name != 'go':
            raise ProviderError('Go adapter requires the direct go executable')
        # `go -C module/path test ...` is a native Go command, not a shell
        # wrapper.  Both the directory and module must be tracked and reviewed.
        directory='.'
        if len(argv)>2 and argv[1]=='-C':
            directory=argv[2]
            command=argv[3:]
        else:
            command=argv[1:]
        if not command or command[0] not in ('test','vet','build'):
            raise ProviderError('Go adapter admits only direct test/vet/build commands')
        if any(token=='-C' for token in command):
            raise ProviderError('Go -C must precede the subcommand')
        try:
            reviewed=go_workspace_manifest(root,directory=directory)
        except (ProjectReviewError,UnicodeError,OSError,subprocess.CalledProcessError) as exc:
            raise ProviderError('RM-08 Go workspace: '+str(exc)[:240]) from None
        selected=reviewed['selected']
        meta=job.metadata or {}
        variants=_matrix_versions(meta.get('ci_go_versions'),selected['version'],kind='go')
        if '-race' in command:
            if command[0]!='test':
                raise ProviderError('Go race instrumentation is reviewed for tests only')
            # Cross-target/CGO admission is checked against declared job env
            # in provider_plan and against the actual hosted runner later.
            if job.env.get('CGO_ENABLED','0')!='1' or job.env.get('GOOS','linux')!='linux' or job.env.get('GOARCH','amd64')!='amd64':
                raise ProviderError('Go -race requires Linux amd64 with explicit CGO_ENABLED=1')
        if any(a.startswith(('GOOS=', 'GOARCH=', 'CGO_ENABLED=')) for a in argv):
            raise ProviderError('Go environment variables must be declared in job.env, not in argv')
        reproducible=meta.get('ci_go_reproducible',False)
        if type(reproducible) is not bool:raise ProviderError('RM-08 repeat-build flag must be boolean')
        if reproducible and (command[0]!='build' or '-trimpath' not in command or '-buildvcs=false' not in command or
                             '-o' not in command or job.env.get('CGO_ENABLED','0')!='0'):
            raise ProviderError('RM-08 repeat-build requires CGO-free go build -trimpath -buildvcs=false -o')
        if command[0]=='build' and '-o' in command and (
                command.index('-o')+1 >= len(command) or not command[command.index('-o')+1].startswith('dist/')):
            raise ProviderError('Go distributable outputs must be inside dist/')
        return {'provider': 'go', 'api_version': self.api_version, 'toolchain': 'go',
                'version': selected['version'], 'matrix_versions':variants, 'version_source': selected['manifest'],
                'workspace':reviewed,'reproducible_go':reproducible,
                'lockfiles': reviewed['source_files'],
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
        if len(argv) < 3 or argv[1] != '-m' or argv[2] not in ('pytest', 'unittest', 'compileall', 'ruff', 'build', 'coverage'):
            raise ProviderError('Python adapter admits only reviewed Python -m commands')
        if argv[2]=='coverage' and not (argv[3:6]==['run','-m','pytest'] or argv[3:]==['xml','-o','dist/coverage.xml']):
            raise ProviderError('RM-08 coverage only accepts native pytest instrumentation or XML report')
        if 'pytest' in argv and 'compileall' in argv:
            raise ProviderError('Ambiguous Python module argv')
        if argv[2] == 'build':
            # Clean Python distribution production uses pinned tooling and no
            # implicit build-environment downloads; native DAG remains owner.
            if not _tracked(root, 'pyproject.toml') or '--no-isolation' not in argv:
                raise ProviderError('Python build requires tracked pyproject and --no-isolation')
            if not any(_tracked(root, n) for n in ('requirements.txt','uv.lock','poetry.lock','pdm.lock')):
                raise ProviderError('Python build requires tracked hash-pinned requirements.txt')
            from .actions_distributions import _python_source, DistributionError
            try: _python_source(root)
            except DistributionError as exc: raise ProviderError(str(exc)) from None
            if argv.count('--outdir') != 1 or argv[argv.index('--outdir')+1:argv.index('--outdir')+2] != ['dist']:
                raise ProviderError('Python package build must use --outdir dist')
            if '--wheel' not in argv and '--sdist' not in argv:
                raise ProviderError('Python package build must select --wheel or --sdist')
        from .actions_rm08_projects import python_project_manifest, ProjectReviewError
        meta=job.metadata or {}
        version=meta.get('ci_python_version','3.12')
        if not isinstance(version,str) or version not in ('3.11','3.12','3.13'):
            raise ProviderError('RM-08 Python version must be a reviewed 3.11/3.12/3.13 pin')
        variants=_matrix_versions(meta.get('ci_python_versions'),version,kind='python')
        strict=meta.get('ci_python_policy', '')=='reviewed'
        if meta.get('ci_python_policy','') not in ('','reviewed'):
            raise ProviderError('RM-08 unknown Python dependency policy')
        try:
            project=python_project_manifest(root,require_package=strict) if strict else None
        except (ProjectReviewError,OSError,UnicodeError) as exc:
            raise ProviderError('RM-08 Python project: '+str(exc)[:200]) from None
        if strict and not project['dependency_install_supported']:
            raise ProviderError('RM-08 package lacks a reviewed hash installer policy')
        lockfiles=[name for name in ('uv.lock','poetry.lock','requirements.txt','pdm.lock') if _tracked(root,name)]
        if project and project['project_present']:
            lockfiles=project['source_files']
        elif argv[2]=='build':
            # Existing V1 package policies stay operational; they are not
            # silently called an RM-08 strict backend/installer qualification.
            lockfiles=sorted(set(lockfiles+(['pyproject.toml'] if _tracked(root,'pyproject.toml') else [])))
        return {'provider':'python-shell','api_version':self.api_version,
                'toolchain':'python','version':version,'matrix_versions':variants,
                'version_source':'native-job-ci_python_version' if 'ci_python_version' in meta else 'bounded-next03-host-profile',
                'project':project, 'lockfiles':lockfiles,'cache_enabled':False,
                'report_adapter':'pytest-junit','output_role':'source-only-unverified',
                'release_qualified':False}


@dataclass(frozen=True)
class DotNetProvider:
    api_version: int = SCHEMA_VERSION
    provider_id: str = 'dotnet'

    def inspect(self, root: Path, job: Any, argv: list[str]) -> dict[str, Any]:
        # The reviewed .NET logger contains a literal semicolon; generic Go/Python
        # argv grammar intentionally refuses that token. Use the dedicated parser.
        from .actions_rm09_dotnet import inspect_dotnet_job, DotNetError
        try: return {'api_version':self.api_version, **inspect_dotnet_job(root,argv,job.metadata or {})}
        except DotNetError as exc: raise ProviderError(str(exc)) from None


@dataclass(frozen=True)
class RustCargoProvider:
    api_version: int = SCHEMA_VERSION
    provider_id: str = 'rust-cargo'

    def inspect(self, root: Path, job: Any, argv: list[str]) -> dict[str, Any]:
        from .actions_rm10_cargo import inspect_cargo_job, CargoError
        try:return {'api_version':self.api_version, **inspect_cargo_job(root,argv,job.metadata or {})}
        except CargoError as exc:raise ProviderError(str(exc)) from None

@dataclass(frozen=True)
class NodeProvider:
    api_version: int = SCHEMA_VERSION
    provider_id: str = 'node'

    def inspect(self, root: Path, job: Any, argv: list[str]) -> dict[str, Any]:
        from .actions_rm11_node import inspect_node_job, NodeError
        try:return {'api_version':self.api_version, **inspect_node_job(root,argv,job.metadata or {})}
        except NodeError as exc:raise ProviderError(str(exc)) from None

@dataclass(frozen=True)
class JvmProvider:
    api_version: int = SCHEMA_VERSION
    provider_id: str = 'jvm'

    def inspect(self, root: Path, job: Any, argv: list[str]) -> dict[str, Any]:
        from .actions_rm12_jvm import inspect_jvm_job, JvmError
        try:return {'api_version':self.api_version, **inspect_jvm_job(root,argv,job.metadata or {})}
        except JvmError as exc:raise ProviderError(str(exc)) from None

# The only provider registry: immutable built-ins, no entry-point/network/plugin loading.
PROVIDERS: Mapping[str, LanguageProvider] = MappingProxyType({'go': GoProvider(), 'python-shell': PythonShellProvider(), 'dotnet': DotNetProvider(), 'rust-cargo': RustCargoProvider(), 'node': NodeProvider(), 'jvm': JvmProvider()})
# V1 adapter behavior is preserved; V2 review metadata must match the exact built-ins.
for _id, _adapter in PROVIDERS.items():
    resolve_provider(_id, api_version=_adapter.api_version)
if set(PROVIDERS) != set(reviewed_providers()):
    raise ProviderError('reviewed provider registry does not match executable built-ins')


def _matrix_versions(raw: Any, base: str, *, kind: str) -> list[str]:
    if raw is None:return [base]
    if not isinstance(raw,str) or len(raw)>96:
        raise ProviderError('RM-08 toolchain matrix must be bounded comma-separated text')
    values=raw.split(',')
    if not 1<=len(values)<=3 or len(set(values))!=len(values):
        raise ProviderError('RM-08 toolchain matrix must have 1..3 unique versions')
    allowed= ('3.11','3.12','3.13') if kind=='python' else None
    for v in values:
        if kind=='python':
            if v not in allowed:raise ProviderError('RM-08 unsupported Python matrix version')
        elif not re.fullmatch(r'1\.(?:2[0-9]|[3-9][0-9])(?:\.\d+)?',v):
            raise ProviderError('RM-08 unsupported Go matrix version')
        if kind=='go' and tuple(map(int,(v+'.0').split('.')[:2])) < tuple(map(int,(base+'.0').split('.')[:2])):
            raise ProviderError('RM-08 Go matrix version predates module requirement')
    if base not in values:
        raise ProviderError('RM-08 matrix must include declared primary toolchain version')
    return values


def _digest(row: Any) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _provider_for(job: Any) -> LanguageProvider:
    meta = job.metadata or {}
    flag = meta.get('ci_toolchain')
    argv = list(job.command or [])
    name = Path(argv[0]).name if argv else ''
    if flag == 'jvm' or name in ('mvn','gradle'):
        if name not in ('mvn','gradle') or flag not in (None,'jvm'):
            raise ProviderError('RM-12 JVM provider/argv mismatch')
        return PROVIDERS['jvm']
    if flag == 'node' or name in ('npm','pnpm','yarn','bun'):
        if name not in ('npm','pnpm','yarn','bun') or flag not in (None,'node'):
            raise ProviderError('RM-11 Node provider/argv mismatch')
        return PROVIDERS['node']
    if flag == 'rust-cargo' or name == 'cargo':
        if name!='cargo' or flag not in (None,'rust-cargo'):
            raise ProviderError('RM-10 Rust Cargo provider/argv mismatch')
        return PROVIDERS['rust-cargo']
    if flag == 'dotnet' or name == 'dotnet':
        if name!='dotnet' or flag not in (None,'dotnet'):
            raise ProviderError('RM-09 dotnet provider/argv mismatch')
        return PROVIDERS['dotnet']
    go = (flag == 'go' or job.runner == 'go' or name == 'go' or 'go.mod' in job.inputs)
    if flag not in (None, 'go', 'python-shell', 'dotnet', 'rust-cargo', 'node', 'jvm'):
        raise ProviderError('No reviewed language provider for ci_toolchain ' + str(flag)[:50])
    if go and flag == 'python-shell':
        raise ProviderError('Go source declaration conflicts with Python toolchain')
    if not go and flag == 'go':
        raise ProviderError('Go toolchain requires a direct go command')
    selected = 'go' if go else 'python-shell'
    try:
        resolve_provider(selected, api_version=PROVIDERS[selected].api_version)
    except ContractError as exc:
        raise ProviderError(str(exc)) from None
    return PROVIDERS[selected]


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
                                     toolchains=('go', 'python-shell','dotnet','rust-cargo','node','jvm'))
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
        if 'ci_host_requirements' in meta:
            try:
                host_result = host_eligibility_v2(meta['ci_host_requirements'],
                    host_os='linux', host_arch='x86_64', toolchains=('go','python-shell','dotnet','rust-cargo','node','jvm'))
            except ContractError as exc:
                raise ProviderError('Invalid ci_host_requirements: ' + str(exc)) from None
            if not host_result['eligible']:
                raise ProviderError('Hosted node requirement blocked: ' +
                                    ', '.join(host_result['reasons'])[:256])
        if job.runner not in ('command','python','go') or job.shell_command or job.action or job.gradle_tasks:
            raise ProviderError('Only explicit argv target-local command/python/go jobs are reviewable')
        if job.execution_environment not in ('auto', 'native'):
            raise ProviderError('NEXT-03 refuses custom environment variables or native/remote execution selectors')
        if any(meta.get(key) is True for key in ('device_required','root_required','signing_required','release_signed','termux_native_required')):
            raise ProviderError('Device, root, Termux and signing cannot be hosted')
        argv = list(job.command or [])
        provider = _provider_for(job)
        declaration = provider.inspect(root, job, argv)
        if job.env:
            if declaration['provider'] != 'go' or set(job.env)-{'GOOS','GOARCH','CGO_ENABLED'}:
                raise ProviderError('Only reviewed Go cross-target environment variables are admitted')
            goos=job.env.get('GOOS','linux'); goarch=job.env.get('GOARCH','amd64')
            cgo=job.env.get('CGO_ENABLED','0')
            if (goos,goarch) not in {('linux','amd64'),('linux','arm64'),('windows','amd64'),
                                     ('windows','arm64'),('darwin','amd64'),('darwin','arm64')} or cgo not in ('0','1'):
                raise ProviderError('Unsupported Go cross-target/CGO environment')
            if cgo=='1' and (goos,goarch)!=('linux','amd64'):
                raise ProviderError('CGO requires independently qualified cross-compiler')
            declaration['distribution_target']={'goos':goos,'goarch':goarch,'cgo_enabled':cgo=='1'}
        if job.root not in ('', '.', str(root), str(root.resolve())):
            raise ProviderError('NEXT-03 only exports jobs rooted at the repository (subdirectory roots need review)')
        if any(not isinstance(v, str) or v.startswith('/') or '..' in Path(v).parts or '\\' in v for v in job.outputs):
            raise ProviderError('Job output path is not bounded')
        sdist_smoke=meta.get('ci_sdist_smoke',False)
        if type(sdist_smoke) is not bool:
            raise ProviderError('RM-08 sdist smoke opt-in must be boolean')
        if sdist_smoke and (declaration['provider']!='python-shell' or len(argv)<3 or argv[2]!='build'
                            or not declaration.get('project') or not declaration['project'].get('dependency_install_supported')):
            raise ProviderError('RM-08 sdist smoke requires reviewed Python package build/dependency policy')
        wheel_smoke=meta.get('ci_wheel_smoke',False)
        if type(wheel_smoke) is not bool:
            raise ProviderError('RM-08 wheel smoke opt-in must be boolean')
        if wheel_smoke and (declaration['provider']!='python-shell' or len(argv)<3 or argv[2]!='build'
                            or not declaration.get('project') or not declaration['project'].get('dependency_install_supported')):
            raise ProviderError('RM-08 wheel smoke requires reviewed Python package build and pinned pip lock')
        auto_coverage=meta.get('ci_python_auto_coverage',False)
        if type(auto_coverage) is not bool:
            raise ProviderError('RM-08 automatic coverage opt-in must be boolean')
        if auto_coverage:
            if (declaration['provider']!='python-shell' or argv[:3] not in
                (['python','-m','pytest'],['python3','-m','pytest'])):
                raise ProviderError('RM-08 automatic coverage requires a native python -m pytest job')
            if 'dist/coverage.xml' not in job.outputs or len(job.outputs)!=1:
                raise ProviderError('RM-08 automatic coverage requires exclusive declared dist/coverage.xml output')
            if not declaration.get('project') or not declaration['project'].get('dependency_install_supported'):
                raise ProviderError('RM-08 automatic coverage requires reviewed Python dependency lock policy')
            if not re.search(r'(?m)^coverage==[0-9][A-Za-z0-9_.+!-]*\s+--hash=sha256:[a-f0-9]{64}\s*$',
                             (root/declaration['project']['install_lock']).read_text()):
                raise ProviderError('RM-08 automatic coverage requires hash-pinned coverage dependency')
        if declaration['provider']=='jvm':
            from .actions_rm12_jvm import JvmError
            m=declaration['project']['modules'][declaration['selected']]
            artifact=(m.get('coordinate','').split(':')[-1] if declaration['manager']=='maven' else
                      (declaration['selected'].split(':')[-1] if declaration['selected']!=':' else 'root'))
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,79}',artifact):
                raise ProviderError('RM-12 unsafe JVM artifact identity')
            expected=(['dist/jvm-tests.xml'] if declaration['command']=='test' else
                      ['dist/'+artifact+'.jar'] if declaration['command']=='package' else [])
            desktop=meta.get('ci_jvm_desktop_bundle',False)
            if type(desktop) is not bool:
                raise ProviderError('RM-12 desktop bundle opt-in must be boolean')
            if desktop:
                if declaration['command']!='package':
                    raise ProviderError('RM-12 desktop bundle requires package job')
                expected+=['dist/'+artifact+'.zip']
            if list(job.outputs)!=expected:
                raise ProviderError('RM-12 JVM owned native output must match '+repr(expected))
            declaration['artifact_name']=artifact
            declaration['desktop_bundle']=desktop
        if declaration['provider']=='node':
            kind=declaration['kind']
            outs=list(job.outputs)
            if kind=='test':
                want=('dist/test-results/shard-'+declaration['shard'].split('/')[0].zfill(2)+'.xml' if declaration['shard'] else
                      'dist/test-results.json' if declaration['script'].startswith('jest ') else 'dist/test-results.xml')
                if outs!=[want]:raise ProviderError('RM-11 JS test must own exact JUnit/Jest output')
            elif kind in ('typecheck','lint'):
                if outs:raise ProviderError('RM-11 typecheck/lint cannot claim generated outputs')
            elif kind=='build':
                if not outs or len(outs)>32 or any(not x.startswith('dist/') for x in outs):
                    raise ProviderError('RM-11 build needs bounded dist/ output files')
            elif kind=='pack':
                if len(outs)!=1 or not outs[0].startswith('dist/') or not outs[0].endswith('.tgz'):
                    raise ProviderError('RM-11 package requires exact dist/*.tgz output')
        if declaration['provider']=='rust-cargo':
            if declaration['command']=='package':
                pkg=next(x for x in declaration['workspace']['members'].values()
                         if x['name']==declaration['package_name'])
                expected=f"target/package/{pkg['name']}-{pkg['version']}.crate"
                if list(job.outputs)!=[expected]:
                    raise ProviderError('RM-10 package must declare exact owned .crate output')
            elif declaration['command']=='build':
                if len(job.outputs)>16 or any(not p.startswith('target/release/') for p in job.outputs):
                    raise ProviderError('RM-10 build outputs must be bounded target/release binaries')
            elif job.outputs:
                raise ProviderError('RM-10 fmt/Clippy/test must not claim unverified generated output')
        if declaration['provider']=='dotnet':
            operation=declaration['command']
            if operation=='test' and (len(job.outputs)!=1 or job.outputs[0]!=declaration['trx_path']):
                raise ProviderError('RM-09 test must declare exact owned TRX logger output')
            if operation=='pack':
                package=declaration['package']
                expected=package['package_id']+'.'+package['version']+'.nupkg'
                if len(job.outputs)!=1 or job.outputs[0] != 'dist/'+expected:
                    raise ProviderError('RM-09 pack must declare exact dist/package-id.version.nupkg output')
            if operation=='publish' and not job.outputs:
                raise ProviderError('RM-09 publish job must declare output manifest bytes')
        if declaration['provider']=='python-shell' and argv[2:]==['coverage','xml','-o','dist/coverage.xml'] and 'dist/coverage.xml' not in job.outputs:
            raise ProviderError('RM-08 coverage reporter must declare dist/coverage.xml output')
        rows.append({'node':node.key, 'job':local, 'depends_on':list(node.depends_on),
                     'argv_sha256': _digest(argv), 'inputs_sha256':_digest(list(job.inputs)),
                     'provider':declaration,'outputs':list(job.outputs), 'command':argv, 'env':dict(job.env),
                     'wheel_smoke':wheel_smoke,'sdist_smoke':sdist_smoke,
                     'auto_coverage':auto_coverage})
    # JVM reactor source dependencies cannot silently become a second scheduler.
    # When a dependency has a separate native owner, consumers must declare
    # an explicit direct native edge (or use reviewed Maven -am reactor builds).
    jvm_rows=[r for r in rows if r['provider']['provider']=='jvm']
    owners={}
    for r in jvm_rows:
        p=r['provider'];key=(p['manager'],p['selected'])
        if key in owners and owners[key]['node']!=r['node'] and (owners[key]['node'] not in r['depends_on'] and r['node'] not in owners[key]['depends_on']):
            raise ProviderError('RM-12 overlapping JVM module jobs require native ordering')
        owners[key]=r
    for r in jvm_rows:
        p=r['provider']
        for dependency in p['project']['edges'][p['selected']]:
            other=owners.get((p['manager'],dependency))
            if other and other['node']!=r['node'] and other['node'] not in r['depends_on']:
                raise ProviderError('RM-12 JVM reactor dependency requires native DAG edge')
    # Cargo workspace member producers must follow native DAG dependency edges.
    rust_rows=[r for r in rows if r['provider']['provider']=='rust-cargo']
    for row in rust_rows:
        # A workspace operation compiles its own path dependencies. Any package
        # operation that reuses a produced member requires an explicit DAG edge.
        if row['provider']['command']=='package':
            name=row['provider']['package_name']
            for other in rust_rows:
                if other is row or other['provider']['command']!='build':continue
                if other['node'] not in row['depends_on']:
                    raise ProviderError('RM-10 Cargo package requires native build dependency')
    # .NET solution restore/build/test/pack/publish ordering is expressed by
    # native DAG needs, never by a hidden second Actions scheduler.
    dotnet_rows=[r for r in rows if r['provider']['provider']=='dotnet']
    for row in dotnet_rows:
        op=row['provider']['command'];selected=row['provider']['selected']
        if op in ('build','test','pack','publish'):
            predecessor=('restore' if op=='build' else 'build')
            owners=[r for r in dotnet_rows if r['provider']['selected']==selected and
                    r['provider']['command']==predecessor]
            if len(owners)!=1 or owners[0]['node'] not in row['depends_on']:
                raise ProviderError('RM-09 '+op+' requires a direct native '+predecessor+' dependency for '+selected)
        if op=='restore':
            owners=[r for r in dotnet_rows if r['node']!=row['node'] and
                    r['provider']['command']=='restore' and
                    set(r['provider']['graph']['solutions'].get(r['provider']['selected'],[r['provider']['selected']])) &
                    set(row['provider']['graph']['solutions'].get(selected,[selected]))]
            for other in owners:
                if other['node'] not in row['depends_on'] and row['node'] not in other['depends_on']:
                    raise ProviderError('RM-09 overlapping NuGet restore owners require native DAG ordering')
    # Workspace dependency edges must be owned by native DAG nodes, not by a
    # second hosted scheduler. When both modules have producers in this workflow,
    # a direct native dependency is mandatory (an implicit Go workspace build
    # order must never bypass native target ownership).
    owned_modules={}
    for row in rows:
        if row['provider']['provider']=='go':
            directory=row['provider']['workspace']['selected']['directory']
            if directory in owned_modules and owned_modules[directory]!=row['node']:
                # Multiple target-local commands per module are valid; they
                # must remain explicitly ordered if they produce shared bytes.
                continue
            owned_modules[directory]=row['node']
    for row in rows:
        if row['provider']['provider']!='go':continue
        workspace=row['provider']['workspace']
        directory=workspace['selected']['directory']
        for dependency in workspace['workspace_edges'].get(directory,[]):
            owner=owned_modules.get(dependency)
            if owner and owner!=row['node'] and owner not in row['depends_on']:
                raise ProviderError('RM-08 Go module dependency must be a native DAG edge: '+dependency)
    # One version fanout per native DAG: every node participates in its cell.
    # Mixed providers would require a per-node product strategy; refuse that
    # rather than misbind a Go producer to another Python version's outputs.
    matrix_specs={tuple(r['provider']['matrix_versions']) for r in rows}
    if len(matrix_specs)>1 and any(len(v)>1 for v in matrix_specs):
        raise ProviderError('RM-08 version matrix requires a uniform native DAG toolchain declaration')
    if len(matrix_specs)==1 and len(next(iter(matrix_specs)))>1 and len({r['provider']['provider'] for r in rows})>1:
        raise ProviderError('RM-08 version fanout refuses a mixed-language DAG')
    providers = sorted({r['provider']['provider'] for r in rows})
    head, tree = _git(root, 'rev-parse', 'HEAD'), _git(root, 'rev-parse', 'HEAD^{tree}')
    # A generated workflow is itself a new Git commit: binding the frozen review
    # digest to the *pre-export* HEAD would make the first CI run fail. Instead
    # bind reviewed config, provider lockfiles and selected native job graph.
    from .actions_distributions import CONTRACT_PATH, distribution_contract, DistributionError
    distribution = None
    if (root / CONTRACT_PATH).exists():
        try:
            distribution = distribution_contract(root,target=target,workflow=workflow)
        except DistributionError as exc: raise ProviderError(str(exc)) from None
        for dist in distribution['entries']:
            e=dist['expected']
            candidates=[row for row in rows if e['path'] in row['outputs'] and
                (row['provider']['provider']=='go' if e['kind']=='go' else row['provider']['provider']=='python-shell')]
            if len(candidates)!=1:
                raise ProviderError('Each distribution must belong to exactly one declared native-DAG output: '+e['id'])
            owner=candidates[0]
            if e['kind']=='go':
                cmd=owner['command']
                go_cmd=cmd[3:] if len(cmd)>3 and cmd[1]=='-C' else cmd[1:]
                prefix=(cmd[2]+'/' if len(cmd)>3 and cmd[1]=='-C' else '')
                if len(go_cmd)<3 or go_cmd[0]!='build' or '-o' not in go_cmd or prefix+go_cmd[go_cmd.index('-o')+1]!=e['path']:
                    raise ProviderError('Go distribution must be produced by an exact go build -o job')
                if e['module']!=owner['provider']['workspace']['selected']['module']:
                    raise ProviderError('Go distribution module differs from job-selected tracked module')
                env=owner['env']
                if (env.get('GOOS','linux'),env.get('GOARCH','amd64'),env.get('CGO_ENABLED','0')) != (e['goos'],e['goarch'],'1' if e['cgo_enabled'] else '0'):
                    raise ProviderError('Go distribution does not match declared build environment')
            else:
                cmd=owner['command']
                if cmd[:3] not in (['python','-m','build'],['python3','-m','build']):
                    raise ProviderError('Python distribution requires python -m build --no-isolation')
                if '--no-isolation' not in cmd:
                    raise ProviderError('Python build isolation could install unpinned dependencies')
                if '--outdir' not in cmd or cmd[cmd.index('--outdir')+1:cmd.index('--outdir')+2] != ['dist']:
                    raise ProviderError('Python build outputs must use exact --outdir dist')
                if ('--wheel' if e['kind']=='wheel' else '--sdist') not in cmd:
                    raise ProviderError('Python build command does not select declared output format')
                if owner['provider'].get('project'):
                    if not owner['provider']['project']['dependency_install_supported']:
                        raise ProviderError('RM-08 distribution needs reviewed hash-pinned package policy')
                elif not _tracked(root, 'requirements.txt'):
                    raise ProviderError('RM-08 legacy distribution requires tracked hash-pinned requirements.txt')
    for row in rows:
        if row['sdist_smoke']:
            if distribution is None:
                raise ProviderError('RM-08 sdist smoke requires a reviewed distribution contract')
            owned=[d['expected'] for d in distribution['entries'] if d['expected']['path'] in row['outputs']]
            project=row['provider'].get('project') or {}
            from .actions_distributions import _python_source
            scripts=_python_source(root)['entrypoints']
            if len(owned)!=1 or owned[0]['kind']!='sdist' or len(scripts)!=1:
                raise ProviderError('RM-08 sdist smoke requires one owned source archive and console entrypoint')
        if not row['wheel_smoke']:
            continue
        if distribution is None:
            raise ProviderError('RM-08 wheel smoke requires a reviewed distribution contract')
        owned=[d['expected'] for d in distribution['entries'] if d['expected']['path'] in row['outputs']]
        if len(owned)!=1 or owned[0]['kind']!='wheel' or len(owned[0]['entrypoints'])!=1:
            raise ProviderError('RM-08 wheel smoke requires one owned wheel and one reviewed console entrypoint')
    tracked_sources = ['.devtool.toml'] + sorted({p for row in rows for p in row['provider']['lockfiles']})
    if distribution:
        tracked_sources += [CONTRACT_PATH,'pyproject.toml'] if any(e['kind']!='go' for e in (r['expected'] for r in distribution['entries'])) else [CONTRACT_PATH]
    source_digests = {p: hashlib.sha256((root / p).read_bytes()).hexdigest()
                      for p in tracked_sources if _tracked(root, p)}
    if '.devtool.toml' not in source_digests:
        raise ProviderError('Provider review requires tracked .devtool.toml')
    bound = {'source_digests':source_digests, 'target':target,
             'workflow':workflow, 'runtime_sha':devtool_sha, 'nodes':rows,
             'host':{'os':'ubuntu-24.04','arch':'x86_64'}, 'providers':providers,
             'distribution_contract_sha256':distribution['contract_sha256'] if distribution else ''}
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
            'distributions': ([] if not distribution else [{'id':r['id'],'kind':r['kind'],'path':r['path']} for r in distribution['entries']]),
            'changed_target_selection':'run-full-dag-unless-authoritatively-proven',
            'test_evidence':'not_verified','remote_dispatched':False,
            'artifact_authenticated':False,'release_qualified':False}


def _workflow_yaml(plan: dict[str, Any]) -> str:
    from .actions_adoption import _CHECKOUT_SHA, _UPLOAD_SHA, _SETUP_GO_SHA
    target, workflow, pin = plan['target'], plan['workflow'], plan['runtime_sha']
    go_versions={(r['provider']['version'],r['provider']['version_source']) for r in plan['nodes'] if r['provider']['provider']=='go'}
    py_versions={r['provider']['version'] for r in plan['nodes'] if r['provider']['provider']=='python-shell'}
    if len(go_versions)>1 or len(py_versions)>1:
        raise ProviderError('RM-08 single-host job cannot safely combine different pinned toolchains; use multi-job')
    if any(len(r['provider']['matrix_versions'])>1 for r in plan['nodes']):
        raise ProviderError('RM-08 requested multiple toolchain versions; choose --mode multi-job')
    if any(r['auto_coverage'] for r in plan['nodes']):
        raise ProviderError('RM-08 automatic native pytest coverage requires --mode multi-job')
    go_version_file=next(iter(go_versions))[1] if go_versions else 'go.mod'
    python_version=next(iter(py_versions)) if py_versions else '3.12'
    namespace = f'devtool-{target}-{workflow}-providers'
    # All dynamic strings are slug/SHA constants checked by provider_plan.
    dist = bool(plan.get('distributions'))
    py_dist = any(row['kind'] in ('wheel','sdist') for row in plan.get('distributions',[]))
    py_requirements = ('''      - name: Install hash-verified Python build/test dependencies
        shell: bash
        run: |
          set -euo pipefail
          python3 -m pip install --require-hashes -r requirements.txt
''' if py_dist else '')
    clean_outputs = (f'''      - name: Refuse pre-existing candidate outputs
        shell: bash
        run: |
          set -euo pipefail
{chr(10).join('          test ! -e '+repr(row['path'])+' && test ! -L '+repr(row['path']) for row in plan['distributions'])}
''' if dist else '')
    post_verify = (f'''      - name: Verify declared distributions against tracked source contract
        shell: bash
        run: |
          set -euo pipefail
          "$RUNNER_TEMP/devtool-venv/bin/python" -m devtool --repo "$GITHUB_WORKSPACE" actions provider-distributions --target {target} --workflow {workflow} --json > "$RUNNER_TEMP/devtool-distributions.json"
''' if dist else '')
    candidate_upload = (f'''      - name: Upload verified candidate distributions (not release-qualified)
        if: success()
        uses: actions/upload-artifact@{_UPLOAD_SHA}
        with:
          name: {namespace}-candidate-bytes
          path: |
{chr(10).join('            '+row['path'] for row in plan['distributions'])}
          if-no-files-found: error
          retention-days: 14
''' if dist else '')
    go_setup = (f'''      - name: Pinned Go toolchain
        uses: actions/setup-go@{_SETUP_GO_SHA}
        with:
          go-version-file: {go_version_file}
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
      GOTOOLCHAIN: local
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
          python-version: '{python_version}'
{go_setup}{py_requirements}      - name: Acquire frozen Devtool runtime
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
{clean_outputs}      - name: Run native DAG (one owner, no duplicated GitHub scheduler)
        shell: bash
        run: |
          set -euo pipefail
          "$RUNNER_TEMP/devtool-venv/bin/python" -m devtool --repo "$GITHUB_WORKSPACE" --machine-file "$RUNNER_TEMP/devtool-invoke.json" invoke --target {target} {workflow}
{post_verify}{candidate_upload}      - name: Record source-bound evidence, never release authority
        if: always()
        shell: bash
        run: |
          set -euo pipefail
          mkdir -p "$RUNNER_TEMP/devtool-provider-evidence"
          printf '%s\\n' "$GITHUB_SHA" > "$RUNNER_TEMP/devtool-provider-evidence/consumer-source.txt"
          printf '%s\\n' "$DEVTOOL_SOURCE_SHA" > "$RUNNER_TEMP/devtool-provider-evidence/devtool-source.txt"
          printf '%s\\n' "$GITHUB_RUN_ID/$GITHUB_RUN_ATTEMPT" > "$RUNNER_TEMP/devtool-provider-evidence/github-run-attempt.txt"
          for f in devtool-provider-plan.json devtool-plan.json devtool-preflight.json devtool-invoke.json devtool-distributions.json; do
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
                    write: bool = False, verify: bool = False, mode: str = 'single-job',
                    host_profiles: str = '', cache_locks: bool = False) -> dict[str, Any]:
    if write and verify:
        raise ProviderError('Export --yes and --verify are mutually exclusive')
    if mode not in ('single-job', 'multi-job'):
        raise ProviderError('Unknown provider exporter mode')
    if mode == 'single-job' and (host_profiles or cache_locks):
        raise ProviderError('RM-07 host profiles/cache require --mode multi-job')
    if mode == 'multi-job':
        from .actions_graph_export import compile_graph, workflow_yaml
        graph = compile_graph(root,target=target,workflow=workflow,devtool_sha=devtool_sha,
                              host_profiles=host_profiles,cache_locks=cache_locks)
        plan = {key:value for key,value in graph.items() if key != 'provider_plan'}
        frozen_sha = graph['graph_sha256']
        content = workflow_yaml(graph).encode()
        dest = f'.github/workflows/devtool-{target}-{workflow}-providers-multi.yml'
    else:
        plan = provider_plan(root,target=target,workflow=workflow,devtool_sha=devtool_sha)
        if 'dotnet' in plan['providers'] or 'rust-cargo' in plan['providers'] or 'node' in plan['providers'] or 'jvm' in plan['providers']:
            raise ProviderError('RM-09–RM-12 reviewed .NET/Rust/Node/JVM require native multi-job export with reviewed toolchain preflight')
        frozen_sha = plan['plan_sha256']
        content = _workflow_yaml(plan).encode()
        dest = f'.github/workflows/devtool-{target}-{workflow}-providers.yml'
    root = root.resolve()
    try:
        path = _inside_repo(root, dest)
    except AdoptionError as exc:
        raise ProviderError(str(exc)) from None
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
    if mode == 'single-job' and (host_profiles or cache_locks):
        raise ProviderError('RM-07 host profiles/cache require --mode multi-job')
    if mode == 'multi-job':
        from .actions_graph_export import compile_graph
        if compile_graph(root,target=target,workflow=workflow,devtool_sha=devtool_sha,
                         host_profiles=host_profiles,cache_locks=cache_locks)['graph_sha256'] != frozen_sha:
            raise ProviderError('RM-06 graph changed after review')
    elif provider_plan(root,target=target,workflow=workflow,devtool_sha=devtool_sha)['plan_sha256'] != frozen_sha:
        raise ProviderError('Provider plan changed after review')
    _atomic(path, content)
    result['status'] = 'LOCAL_WORKFLOW_WRITTEN_NOT_REMOTE'
    result['writes_performed'] = True
    return result


def provider_doctor(root: Path, *, target: str = '', all_profiles: bool = False) -> dict[str, Any]:
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
                requested=[{'node':row['node'], 'provider':row['provider']['provider'],
                            'toolchain':row['provider']['toolchain'],
                            'requested_version':row['provider']['version'],
                            'version_source':row['provider']['version_source'],
                            'lockfiles':row['provider']['lockfiles'],
                            'expected_hosted_runner':'ubuntu-24.04'} for row in entry['nodes']]
                results.append({'target':name,'workflow':wf,'state':'locally_eligible',
                                'providers':entry['providers'],'node_count':len(entry['nodes']),
                                'requested_toolchains':requested,
                                'next_action':'Review multi-job export; do not infer hosted runner availability'})
            except (ProviderError, ValueError) as exc:
                results.append({'target':name,'workflow':wf,'state':'blocked',
                                'reason':str(exc)[:280],
                                'next_action':'Review configured native workflow and missing prerequisites; no automatic installation'})
    from .toolchain_profiles import capability_report
    host_report=capability_report(root)
    return {'schema_version':SCHEMA_VERSION, 'kind':IDENTITY+'.doctor',
            'toolchains':host_report, 'all_profiles_requested':bool(all_profiles),
            'read_only':True,'registered_providers':[reviewed_providers()[key].describe()
                             for key in sorted(PROVIDERS)],
            'workflows':results,'source_attested':False,'host_executed':False,
            'release_qualified':False}
