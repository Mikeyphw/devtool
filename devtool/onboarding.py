"""RM20: conservative, source-bound, whole-repository in-place onboarding.

No extraction, shell discovery or project/build execution is permitted. Native
Devtool owns the resulting configuration and affected-target DAG.
"""
from __future__ import annotations

import hashlib
import json
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


def inventory(repo: Path) -> dict[str, Any]:
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
    proposal = b'' if cfg_exists or issues or unsafe else _generate(rows)
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
                'paths': paths, 'ownership': ownership}, sort_keys=True, separators=(',', ':')).encode()).hexdigest() if proposal else '',
            'proposal_text': proposal.decode() if proposal else '',
            'operations': ['add .devtool.toml'] if proposal else [],
            'safe_to_apply': bool(proposal), 'in_place': True,
            'extracts_repositories': False, 'executes_jobs': False,
            'release_qualified': False}


def apply(repo: Path, *, reviewed_sha256: str, yes: bool) -> dict[str, Any]:
    if not yes:
        raise OnboardingBlocked('apply requires explicit --yes')
    if not _SHA.fullmatch(str(reviewed_sha256 or '')):
        raise OnboardingBlocked('apply requires exact --reviewed-sha256 from plan')
    root = _root(Path(repo))
    # Enforce clean and exact source *before* preparing or touching files.
    if _git(root, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise OnboardingBlocked('onboarding requires a completely clean Git worktree')
    before = _source(root)
    report = inventory(root)
    if not report['safe_to_apply'] or report['review_sha256'] != reviewed_sha256:
        raise OnboardingBlocked('reviewed plan drift or unsupported repository; re-review required')
    if _source(root) != before:
        raise OnboardingBlocked('Git source changed during inventory')
    target = root / '.devtool.toml'
    if target.exists() or target.is_symlink():
        raise OnboardingBlocked('refusing to replace existing config')
    # Refuse an ignored config: committing would silently omit the only file.
    ignored = subprocess.run(['git', '-C', str(root), 'check-ignore', '-q', '.devtool.toml'], timeout=10)
    if ignored.returncode == 0:
        raise OnboardingBlocked('.devtool.toml is ignored by Git')
    if ignored.returncode != 1:
        raise OnboardingBlocked('cannot establish Git ignore status')
    # Build the reviewed commit off-checkout, from the exact approved parent.
    # Neither Git staging nor an ordinary `git commit` may move HEAD.
    payload = report['proposal_text'].encode()
    try:
        candidate, candidate_tree = gg.pinned_commit(root, before['head'],
                    {'.devtool.toml': payload},
                    b'feat(devtool): onboard whole repository in place\n')
        gg.verify_candidate(root, before['head'], candidate, candidate_tree,
                            {'.devtool.toml': gg.digest(payload)}, {'.devtool.toml': None})
    except gg.GuardedGitError as exc:
        raise OnboardingBlocked(str(exc)) from exc
    if _source(root) != before or _git(root, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise OnboardingBlocked('Git source changed before publication')
    # Exclusive creation protects against a second process creating the config
    # after the last scan. No os.replace()/unlink of an unknown path.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(target, flags, 0o600)
    except FileExistsError as exc:
        raise OnboardingBlocked('concurrent config creation refused; file preserved') from exc
    created_inode = os.fstat(fd).st_ino
    try:
        with os.fdopen(fd,'wb') as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        try:
            gg.cas_branch(root, before['branch'], candidate, before['head'])
        except gg.GuardedGitError as exc:
            # A failed lease can have a concurrent editor touching this file.
            # Preserve it for manual review instead of an unsafe check-then-unlink.
            raise OnboardingBlocked('source HEAD lease rejected; local config preserved for manual review: '+str(exc)) from exc
        # Index-only operation: never let Git touch the new filesystem file.
        try:
            gg.reconcile_index_locked(root, candidate)
        except gg.GuardedGitError as exc:
            raise OnboardingBlocked('branch published, index reconciliation pending: '+str(exc)) from exc
        after = _source(root)
        if after['head'] != candidate or after['tree'] != candidate_tree or _git(root, 'status', '--porcelain=v1', '--untracked-files=all'):
            raise OnboardingBlocked('branch published but worktree changed; manual reconciliation required')
        return {'schema':SCHEMA, 'status':'APPLIED','ok':True,'in_place':True,
                'source_before':before,'source_after':after,
                'proposal_sha256':reviewed_sha256,'commit_count':1,
                'cross_repository_atomic':False,'release_qualified':False}
    except Exception:
        # After publication, never remove the candidate file or reset refs.
        raise
