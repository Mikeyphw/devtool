"""RM20: conservative, source-bound, whole-repository in-place onboarding.

No extraction, shell discovery or project/build execution is permitted. Native
Devtool owns the resulting configuration and affected-target DAG.
"""
from __future__ import annotations

import hashlib
import json
import stat
import tomllib
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from typing import Any

from .affected import _target_match_reasons, target_dependencies
from .config import load_project_config
from . import guarded_git as gg
from .gradle_identity import gradle_identity, GradleIdentityError
from .workflow_runtime import plan_workspace_targets, validate_workspace_workflows
from .wrapper_launcher import expected_launcher_texts

SCHEMA = 'devtool.whole-repository-onboarding.v1'
_MARKERS = {
    'pyproject.toml': ('python', 'server'),
    'go.mod': ('go', 'server'),
    'Cargo.toml': ('rust', 'native'),
    'package.json': ('node', 'web'),
    'pom.xml': ('maven', 'jvm'),
    'CMakeLists.txt': ('cmake', 'native'),
    'meson.build': ('meson', 'native'),
    'build.gradle': ('gradle', 'jvm'),
    'build.gradle.kts': ('gradle', 'jvm'),
    '.shellcheckrc': ('command', 'terminal'),
}
_UNKNOWN_BUILD_MARKERS = {'build.sbt','pubspec.yaml','composer.json','mix.exs',
                         'Package.swift','Makefile.am','WORKSPACE','WORKSPACE.bazel','MODULE.bazel'}

_SHA = re.compile(r'[0-9a-f]{64}\Z')
_TARGET = re.compile(r'[a-z][a-z0-9_-]{0,47}\Z')


class OnboardingBlocked(ValueError):
    pass


def _git(root: Path, *args: str, binary: bool = False) -> bytes | str:
    try:
        run = subprocess.run(['git', '-C', str(root), *args], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OnboardingBlocked('Git inspection unavailable') from exc
    if run.returncode:
        raise OnboardingBlocked('Git '+str(args[0])+' refused: '+run.stderr.decode(errors='replace')[:160])
    return run.stdout if binary else run.stdout.decode('utf-8', errors='replace').strip()


def _root(path: Path) -> Path:
    root = path.expanduser().resolve()
    if not root.is_dir() or _git(root, 'rev-parse', '--show-toplevel') != str(root):
        raise OnboardingBlocked('onboarding requires the exact Git worktree root')
    if subprocess.run(['git', '-C', str(root), 'symbolic-ref', '-q', '--short', 'HEAD'], capture_output=True, timeout=10).returncode:
        raise OnboardingBlocked('onboarding requires a named branch')
    return root


def _source(root: Path) -> dict[str, str]:
    return {'head': str(_git(root, 'rev-parse', 'HEAD')),
            'tree': str(_git(root, 'rev-parse', 'HEAD^{tree}')),
            'branch': str(_git(root, 'symbolic-ref', '-q', 'HEAD'))}


def _tracked(root: Path) -> tuple[list[str], list[str]]:
    data = _git(root, 'ls-files', '--stage', '-z', binary=True)
    paths: list[str] = []
    unsafe: list[str] = []
    for entry in data.split(b'\0'):
        if not entry:
            continue
        try:
            record, raw = entry.split(b'\t', 1)
            mode, _, stage = record.split(b' ', 2)
            path = raw.decode('utf-8')
        except (ValueError, UnicodeDecodeError) as exc:
            raise OnboardingBlocked('invalid Git path encoding or stage') from exc
        if len(paths) >= 50000:
            raise OnboardingBlocked('more than 50,000 tracked paths require manual scoped onboarding')
        if (not path or path.startswith('/') or '\\' in path or
                any(p in ('.', '..', '.git') for p in PurePosixPath(path).parts) or
                stage != b'0'):
            raise OnboardingBlocked('unsafe path or unresolved Git merge stage')
        paths.append(path)
        if mode not in (b'100644', b'100755'):
            unsafe.append(path)
    return sorted(set(paths)), sorted(set(unsafe))


def _name(path: str) -> str:
    slug = re.sub(r'[^a-z0-9_-]+', '-', path.lower().replace('/', '-')).strip('-_')
    if not slug or not slug[0].isalpha():
        slug = 't-' + slug
    slug = slug[:41].rstrip('-_')
    if not _TARGET.fullmatch(slug):
        raise OnboardingBlocked('unsupported target root naming; author config manually')
    return slug


def _discovery(root: Path, paths: list[str]) -> tuple[list[dict[str, str]], list[str]]:
    """Inventory tracked build roots without executing project configuration.

    Unresolved Gradle plugins or unknown build roots force review: a partially
    discovered polyglot repository must never be proposed as complete.
    """
    found: dict[str, set[tuple[str, str]]] = {}
    issues: list[str] = []
    tracked=set(paths)
    for path in paths:
        marker = PurePosixPath(path).name
        folder = str(PurePosixPath(path).parent)
        kind = _MARKERS.get(marker)
        if marker in ('build.gradle','build.gradle.kts'):
            try:
                identity, reason=gradle_identity(root,path,tracked)
            except GradleIdentityError as exc:
                identity,reason='review',str(exc)
            if identity=='android':kind=('android','android')
            elif identity=='jvm':kind=('gradle','jvm')
            else:
                issues.append('unreviewed Gradle plugin identity at '+path+': '+reason)
                kind=None
        elif marker.endswith(('.csproj','.fsproj','.vbproj','.sln','.slnx')):
            kind=('dotnet','desktop')
        elif marker in _UNKNOWN_BUILD_MARKERS:
            issues.append('unrecognized build-system root requires review: '+path)
        if kind:
            found.setdefault(folder,set()).add(kind)
    # A shell-only repository is a candidate, but executable commands and
    # package ownership require explicit review (FC-05) before application.
    if not found and any(p.endswith(('.sh','.bash','.zsh')) for p in paths):
        found.setdefault('.',set()).add(('command','terminal'))
        issues.append('shell-only repository requires reviewed commands and ownership')
    if not found:
        issues.append('no supported build-system root can be qualified for automatic onboarding')
    for folder, values in found.items():
        if len(values)>1:
            issues.append('conflicting language markers at '+folder)
    roots=sorted(found)
    if len(roots)>1 and '.' in roots:
        issues.append('root manifest overlaps subprojects; explicit ownership required')
    for i,left in enumerate(roots):
        for right in roots[i+1:]:
            if right.startswith(left.rstrip('/')+'/'):
                issues.append('nested project roots need manual ownership: '+left+' -> '+right)
    rows=[];names=set()
    for folder in roots:
        runner,platform=sorted(found[folder])[0]
        name=_name('repo' if folder=='.' else folder)
        if name in names:
            issues.append('target names collide: '+name)
        names.add(name)
        rows.append({'name':name,'root':folder,'runner':runner,'platform':platform})
    return rows,sorted(set(issues))


def _generate(rows: list[dict[str, str]]) -> bytes:
    if not rows:
        return b''
    # No guessed commands or build workflows. Native Devtool target identity
    # and conservative affected graph are available immediately; operators
    # explicitly add reviewed jobs/workflows later.
    first = rows[0]
    lines = ['# RM20 reviewed in-place onboarding; no commands are inferred.',
             '[project]', f'runner = "{first["runner"]}"', f'platform = "{first["platform"]}"', '',
             '[workspace]', f'default_target = "{first["name"]}"', '']
    for row in rows:
        lines += [f'[targets.{row["name"]}]', f'runner = "{row["runner"]}"',
                  f'platform = "{row["platform"]}"', f'root = "{row["root"]}"', '']
    return ('\n'.join(lines).rstrip()+'\n').encode()


REVIEW_SCHEMA = 'devtool.onboarding-review.v1'
_MAX_REVIEW_BYTES = 262144


def _reviewed_spec(path: Path) -> tuple[dict[str, Any], str]:
    """Read a separately reviewed, bounded specification; never execute it."""
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
            raise OnboardingBlocked('review specification must be a regular non-symlink file')
        if path.stat().st_size > _MAX_REVIEW_BYTES:
            raise OnboardingBlocked('review specification exceeds 256 KiB')
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, 'rb') as src:
            if not stat.S_ISREG(os.fstat(src.fileno()).st_mode):
                raise OnboardingBlocked('review specification changed file type')
            raw = src.read(_MAX_REVIEW_BYTES + 1)
        if len(raw) > _MAX_REVIEW_BYTES:
            raise OnboardingBlocked('review specification exceeds 256 KiB')
        spec = json.loads(raw.decode('utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OnboardingBlocked('review specification unavailable or invalid') from exc
    if not isinstance(spec, dict) or spec.get('schema') != REVIEW_SCHEMA:
        raise OnboardingBlocked('review specification schema mismatch')
    allowed = {'schema', 'source', 'config_toml', 'unowned_paths', 'shared_paths',
               'existing_actions', 'host_toolchains', 'authority', 'decisions', 'wrapper_launchers'}
    if set(spec) != allowed:
        raise OnboardingBlocked('review specification fields incomplete or unrecognized')
    return spec, hashlib.sha256(raw).hexdigest()


def _complete_review(root: Path, source: dict[str, str], paths: list[str],
                     rows: list[dict[str, str]], ownership: dict[str, list[str]],
                     unowned: list[str], shared: list[str], review_spec: str | Path,
                     ) -> dict[str, Any]:
    spec, spec_sha = _reviewed_spec(Path(review_spec).expanduser())
    if spec['source'] != source:
        raise OnboardingBlocked('review specification source HEAD/tree/branch drift')
    if spec['unowned_paths'] != unowned or spec['shared_paths'] != shared:
        raise OnboardingBlocked('reviewed source ownership does not match Git inventory')
    decisions = spec['decisions']
    if not isinstance(decisions, list) or not decisions or any(not isinstance(d, str) or not d.strip() for d in decisions):
        raise OnboardingBlocked('review requires explicit documented decisions')
    host = spec['host_toolchains']
    if not isinstance(host, dict) or set(host) != {r['name'] for r in rows}:
        raise OnboardingBlocked('every discovered target needs host/toolchain review')
    for name, setting in host.items():
        if not isinstance(setting, dict) or set(setting) != {'host', 'toolchain', 'qualification'} or any(
            not isinstance(v, str) or not v.strip() for v in setting.values()
        ):
            raise OnboardingBlocked('incomplete host/toolchain admission for '+name)
    authority = spec['authority']
    if not isinstance(authority, dict) or set(authority) != {'signing', 'device', 'release'} or any(
        not isinstance(v, str) or not v.strip() for v in authority.values()
    ):
        raise OnboardingBlocked('signing/device/release authority must be explicitly reviewed')
    ci = spec['existing_actions']
    if not isinstance(ci, dict) or set(ci) != {'policy', 'files'} or ci['policy'] != 'retain-existing' or not isinstance(ci['files'], dict):
        raise OnboardingBlocked('existing Actions workflows require explicit retain-existing policy')
    workflows = {p for p in paths if p.startswith('.github/workflows/') and p.endswith(('.yml','.yaml'))}
    if set(ci['files']) != workflows:
        raise OnboardingBlocked('existing Actions inventory incomplete: all workflows must be reviewed')
    for path, expected in ci['files'].items():
        if not isinstance(expected, str) or not _SHA.fullmatch(expected):
            raise OnboardingBlocked('reviewed Actions file requires SHA-256')
        if (root / path).is_symlink() or hashlib.sha256((root/path).read_bytes()).hexdigest() != expected:
            raise OnboardingBlocked('existing Actions workflow bytes changed: '+path)
    wrapper_policy = spec['wrapper_launchers']
    launchers = expected_launcher_texts()
    if not isinstance(wrapper_policy,dict) or set(wrapper_policy) != {'policy','sha256'} or not isinstance(wrapper_policy['sha256'],dict) or set(wrapper_policy['sha256']) != set(launchers):
        raise OnboardingBlocked('complete reviewed wrapper launcher policy and hashes required')
    for name, body in launchers.items():
        if wrapper_policy['sha256'][name] != hashlib.sha256(body.encode()).hexdigest():
            raise OnboardingBlocked('reviewed wrapper launcher hash differs from pinned template')
    selected = wrapper_policy['policy']
    if selected not in {'install-if-absent','retain-existing'}:
        raise OnboardingBlocked('unsupported wrapper launcher policy')
    for name, body in launchers.items():
        file = root / name
        if file.is_symlink():
            raise OnboardingBlocked('symlinked wrapper launcher refused: '+name)
        if selected == 'install-if-absent':
            if file.exists():
                raise OnboardingBlocked('wrapper launcher already exists; review retain-existing policy')
        else:
            if not file.is_file() or file.read_bytes() != body.encode():
                raise OnboardingBlocked('existing wrapper launcher does not match reviewed template: '+name)
            if name == 'devtoolw' and not os.access(file,os.X_OK):
                raise OnboardingBlocked('existing wrapper launcher is not executable')
    proposed = spec['config_toml']
    if not isinstance(proposed, str) or not proposed.strip() or len(proposed.encode()) > _MAX_REVIEW_BYTES:
        raise OnboardingBlocked('reviewed configuration text missing or too large')
    try:
        document = tomllib.loads(proposed)
    except tomllib.TOMLDecodeError as exc:
        raise OnboardingBlocked('reviewed configuration is invalid TOML') from exc
    targets = document.get('targets')
    if not isinstance(targets, dict) or set(targets) != {r['name'] for r in rows}:
        raise OnboardingBlocked('reviewed target identities omit or add discovered projects')
    for row in rows:
        raw = targets[row['name']]
        if not isinstance(raw, dict) or any(raw.get(k) != row[k] for k in ('root','runner','platform')):
            raise OnboardingBlocked('reviewed target identity differs from discovered project: '+row['name'])
        workflows_cfg = raw.get('workflows')
        if not isinstance(workflows_cfg, dict) or 'ci' not in workflows_cfg or not workflows_cfg['ci']:
            raise OnboardingBlocked('every target requires an explicit reviewed ci workflow')
    jobs = document.get('jobs', {})
    if not isinstance(jobs, dict) or not jobs:
        raise OnboardingBlocked('reviewed proposal requires explicit jobs')
    for name, job in jobs.items():
        if not isinstance(job, dict) or not isinstance(job.get('inputs'), list) or not job['inputs']:
            raise OnboardingBlocked('job requires reviewed inputs: '+name)
        if not isinstance(job.get('output_scopes'), list) or not job['output_scopes']:
            raise OnboardingBlocked('job requires typed resource/output scopes: '+name)
    wrapper = document.get('wrapper',{})
    if not isinstance(wrapper,dict) or not isinstance(wrapper.get('commands'),dict):
        raise OnboardingBlocked('reviewed wrapper commands required')
    wrapped = {item.get('target') for item in wrapper['commands'].values() if isinstance(item,dict)
               and item.get('workflow') == 'ci' and not item.get('devtool')}
    if wrapped != set(targets):
        raise OnboardingBlocked('reviewed wrapper commands must bind the native ci workflow for every target')
    # Parse the complete candidate through Devtool itself without creating or
    # updating any consumer repository files.
    with tempfile.TemporaryDirectory(prefix='devtool-fc05-review-') as temp:
        scratch = Path(temp) / 'reviewed-consumer'
        scratch.mkdir()
        for row in rows:
            (scratch / row['root']).mkdir(parents=True, exist_ok=True)
        subprocess.run(['git','-C',str(scratch),'init','-q'],
                       capture_output=True, check=True, timeout=30)
        candidate = scratch / '.devtool.toml'
        candidate.write_text(proposed, encoding='utf-8')
        try:
            parsed = load_project_config(str(scratch), str(candidate),
                allow_ambiguous_target=True, allow_parent_config=False)
            validate_workspace_workflows(parsed)
            dag = {}
            for target in sorted(parsed.targets):
                graph = plan_workspace_targets(parsed, [target], 'ci')
                dag[target] = {'node_count': len(graph.nodes),
                               'node_ids': sorted(node.key for node in graph.nodes)}
                if not graph.nodes:
                    raise OnboardingBlocked('reviewed target workflow has empty DAG: '+target)
        except (ValueError, SystemExit, OSError) as exc:
            raise OnboardingBlocked('reviewed native config/DAG validation failed: '+str(exc)[:250]) from exc
    approved_files = {'.devtool.toml': proposed.encode()}
    if selected == 'install-if-absent':
        approved_files.update({name:body.encode() for name,body in launchers.items()})
    return {'proposal_text': proposed, 'spec_sha256': spec_sha,
            'approved_files': approved_files, 'wrapper_launchers': wrapper_policy,
            'dag': dag, 'host_toolchains': host, 'authority': authority,
            'existing_actions': ci, 'decisions': decisions}


def review_template(report: dict[str, Any]) -> dict[str, Any]:
    """Non-executable starter for a human-authored complete review."""
    return {'schema': REVIEW_SCHEMA, 'source': report['source'],
        'config_toml': _generate(report['targets']).decode(),
        'unowned_paths': report['inventory']['unowned_paths'],
        'shared_paths': report['inventory']['shared_paths'],
        'existing_actions': {'policy': 'retain-existing', 'files': {}},
        'wrapper_launchers': {'policy':'install-if-absent', 'sha256': {k:hashlib.sha256(v.encode()).hexdigest() for k,v in expected_launcher_texts().items()}},
        'host_toolchains': {r['name']: {'host': '', 'toolchain': '', 'qualification': 'PENDING'} for r in report['targets']},
        'authority': {'signing':'PENDING','device':'PENDING','release':'PENDING'},
        'decisions': []}


def inventory(repo: Path, *, review_spec: str | Path | None = None) -> dict[str, Any]:
    root = _root(Path(repo))
    source = _source(root)
    paths, unsafe = _tracked(root)
    cfgfile = root / '.devtool.toml'
    cfg_exists = cfgfile.exists() or cfgfile.is_symlink()
    if cfgfile.is_symlink():
        raise OnboardingBlocked('existing Devtool config is a symlink')
    rows, issues = _discovery(root, paths)
    ownership: dict[str, list[str]] = {}
    if cfg_exists:
        # Loading the canonical repository model prevents treating a detected
        # language marker as a new source of truth over a real config.
        try:
            config = load_project_config(str(root), str(cfgfile),
                                         allow_ambiguous_target=True,
                                         allow_parent_config=False)
        except (ValueError, SystemExit) as exc:
            raise OnboardingBlocked('existing Devtool config is invalid: '+str(exc)[:200]) from exc
        deps = {k: list(v) for k, v in target_dependencies(config).items()}
        rows = [{'name': name, 'root': target.root, 'runner': target.runner,
                 'platform': target.platform} for name, target in sorted(config.targets.items())]
        for path in paths:
            ownership[path] = [name for name in sorted(config.targets)
                               if _target_match_reasons(config, name, path)]
        issues = []
    else:
        deps = {row['name']: [] for row in rows}
        for path in paths:
            ownership[path] = [row['name'] for row in rows if
                               row['root'] == '.' or path.startswith(row['root'].rstrip('/') + '/')]
    unowned = [p for p in paths if not ownership[p]]
    shared = [p for p in paths if len(ownership[p]) > 1]
    # Discovery alone is not authority to commit a stub configuration.
    complete = None
    if review_spec and not (cfg_exists or issues or unsafe):
        complete = _complete_review(root, source, paths, rows, ownership, unowned, shared, review_spec)
    proposal = complete['proposal_text'].encode() if complete else b''
    if not cfg_exists and not complete and not issues:
        issues.append('full job/DAG, resource, wrapper and CI review specification required')
    # A config's own addition changes the affected selection; treated as
    # workspace-wide shared/unowned and never pruned to one inferred target.
    digest = hashlib.sha256(proposal).hexdigest() if proposal else ''
    return {'schema': SCHEMA, 'source': source, 'configured': cfg_exists,
            'status': ('CONFIGURED' if cfg_exists else 'REVIEW_REQUIRED' if issues or unsafe or not proposal else 'READY'),
            'targets': rows, 'dependencies': deps,
            'inventory': {'tracked_count': len(paths), 'all_tracked_paths': paths,
                          'ownership': ownership, 'unowned_paths': unowned,
                          'shared_paths': shared, 'unsupported_git_modes': unsafe,
                          'unknown_change_policy': 'select-all-candidate-targets'},
            'review_issues': issues, 'proposal_sha256': digest,
            'review_sha256': hashlib.sha256(json.dumps({'source': source, 'proposal_sha256': digest,
                'paths': paths, 'ownership': ownership, 'review_spec_sha256': complete['spec_sha256'],
                'approved_files_sha256': {p:hashlib.sha256(b).hexdigest() for p,b in complete['approved_files'].items()}}, sort_keys=True, separators=(',', ':')).encode()).hexdigest() if proposal else '',
            'proposal_text': proposal.decode() if proposal else '',
            'operations': ['add '+p for p in sorted(complete['approved_files'])] if proposal else [],
            'reviewed_path_digests': {p:hashlib.sha256(b).hexdigest() for p,b in complete['approved_files'].items()} if proposal else {},
            'wrapper_launchers': complete['wrapper_launchers'] if complete else {},
            'review_spec_sha256': complete['spec_sha256'] if complete else '',
            'validated_dag': complete['dag'] if complete else {},
            'host_toolchains': complete['host_toolchains'] if complete else {},
            'authority': complete['authority'] if complete else {},
            'existing_actions': complete['existing_actions'] if complete else {},
            'manual_review_decisions': complete['decisions'] if complete else [],
            'review_template': None if cfg_exists else review_template({'source':source,'targets':rows,'inventory':{'unowned_paths':unowned,'shared_paths':shared}}),
            'safe_to_apply': bool(proposal), 'in_place': True,
            'extracts_repositories': False, 'executes_jobs': False,
            'release_qualified': False}


def apply(repo: Path, *, reviewed_sha256: str, yes: bool, review_spec: str | Path | None = None) -> dict[str, Any]:
    if not yes:
        raise OnboardingBlocked('apply requires explicit --yes')
    if not _SHA.fullmatch(str(reviewed_sha256 or '')):
        raise OnboardingBlocked('apply requires exact --reviewed-sha256 from plan')
    root = _root(Path(repo))
    # Enforce clean and exact source *before* preparing or touching files.
    if _git(root, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise OnboardingBlocked('onboarding requires a completely clean Git worktree')
    before = _source(root)
    report = inventory(root, review_spec=review_spec)
    if not report['safe_to_apply'] or report['review_sha256'] != reviewed_sha256:
        raise OnboardingBlocked('reviewed plan drift or unsupported repository; re-review required')
    if _source(root) != before:
        raise OnboardingBlocked('Git source changed during inventory')
    changes = {'.devtool.toml': report['proposal_text'].encode()}
    if report['wrapper_launchers']['policy'] == 'install-if-absent':
        changes.update({name:body.encode() for name,body in expected_launcher_texts().items()})
    for path in changes:
        f = root/path
        if f.exists() or f.is_symlink():
            raise OnboardingBlocked('refusing to replace existing reviewed destination: '+path)
        ignored = subprocess.run(['git', '-C', str(root), 'check-ignore', '-q', path], timeout=10)
        if ignored.returncode == 0:
            raise OnboardingBlocked('reviewed destination is ignored by Git: '+path)
        if ignored.returncode != 1:
            raise OnboardingBlocked('cannot establish Git ignore status for '+path)
    try:
        candidate, candidate_tree = gg.pinned_commit(root, before['head'], changes,
                    b'feat(devtool): onboard whole repository in place\n',
                    mode_for=lambda name:'100755' if name=='devtoolw' else '100644')
        gg.verify_candidate(root, before['head'], candidate, candidate_tree,
                {path: gg.digest(data) for path,data in changes.items()},
                {path: None for path in changes})
    except gg.GuardedGitError as exc:
        raise OnboardingBlocked(str(exc)) from exc
    if _source(root) != before or _git(root, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise OnboardingBlocked('Git source changed before publication')
    # All new paths are created exclusively; nothing unowned is overwritten.
    # An interruption before branch CAS intentionally leaves any owned files
    # as visible partial work requiring operator review, never unsafe deletion.
    created=[]
    for path, value in sorted(changes.items()):
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os,'O_NOFOLLOW',0)
        try:
            fd = os.open(root/path,flags,0o700 if path=='devtoolw' else 0o600)
        except FileExistsError as exc:
            raise OnboardingBlocked('concurrent config or wrapper creation refused; existing files preserved: '+path) from exc
        try:
            with os.fdopen(fd,'wb',closefd=False) as out:
                out.write(value)
                out.flush()
                os.fsync(out.fileno())
            if hasattr(os,'fchmod'):
                os.fchmod(fd,0o755 if path=='devtoolw' else 0o644)
        finally:
            os.close(fd)
        created.append(path)
    try:
        gg.cas_branch(root,before['branch'],candidate,before['head'])
    except gg.GuardedGitError as exc:
        raise OnboardingBlocked('source HEAD lease rejected; reviewed files preserved for manual review: '+str(exc)) from exc
    try:
        gg.reconcile_index_locked(root,candidate)
    except gg.GuardedGitError as exc:
        raise OnboardingBlocked('branch published, index reconciliation pending: '+str(exc)) from exc
    after = _source(root)
    if after['head'] != candidate or after['tree'] != candidate_tree or _git(root,'status','--porcelain=v1','--untracked-files=all'):
        raise OnboardingBlocked('branch published but worktree changed; manual reconciliation required')
    return {'schema':SCHEMA, 'status':'APPLIED','ok':True,'in_place':True,
            'source_before':before,'source_after':after,
            'proposal_sha256':reviewed_sha256,'commit_count':1,
            'created_paths':created,'cross_repository_atomic':False,'release_qualified':False}
