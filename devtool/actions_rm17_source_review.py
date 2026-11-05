"""RM17: bounded, read-only source reviews for native polyglot providers.

This is a generic RM-11/RM-12 helper, not a consumer adapter. The caller
retains execution ownership and must bind the reviewed source to its native DAG.
No npm, Gradle, Python script or downloaded code is executed by this module.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any


class SourceReviewError(ValueError):
    pass


_NAME = re.compile(r'[A-Za-z][A-Za-z0-9:_-]{0,79}\Z')
_SOURCE_ARG = re.compile(r'(?:\.\./){0,3}(?:[A-Za-z0-9_.-]+/){0,8}[A-Za-z0-9_.-]+\.(?:py|mjs|js)\Z')
_PY_FLAG = re.compile(r'--[a-z][a-z0-9-]{0,45}(?:=[A-Za-z0-9_.-]{1,80})?\Z')
_PLUGIN = re.compile(r'[A-Za-z][A-Za-z0-9_.-]{0,99}\Z')
_CLASS = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,79}\Z')
_MODULE = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,79}\Z')
_JVM = {'java', 'java-library', 'application', 'org.jetbrains.kotlin.jvm'}
_DECORATORS = {'org.jetbrains.compose', 'org.jetbrains.kotlin.plugin.compose',
               'org.jetbrains.kotlin.plugin.serialization', 'com.android.lint'}
_ANDROID = re.compile(r'com\.android\.(?!lint\b)|org\.jetbrains\.kotlin\.android')


def _git_tracked(root: Path) -> set[str]:
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'],
                            capture_output=True, timeout=15, check=True)
    names = {p.decode('utf-8') for p in result.stdout.split(b'\0') if p}
    if len(names) > 50000:
        raise SourceReviewError('RM17 repository source inventory exceeds cap')
    return names


def _tracked_data(root: Path, relative: str, tracked: set[str], *, maximum: int = 1 << 20) -> bytes:
    if relative not in tracked or not relative or relative.startswith('/') or '\\' in relative:
        raise SourceReviewError('RM17 source is not tracked or has unsafe name: ' + relative[:100])
    path = root
    for part in relative.split('/'):
        if part in ('', '.', '..'):
            raise SourceReviewError('RM17 source component is unsafe')
        path = path / part
        if path.is_symlink():
            raise SourceReviewError('RM17 source symlink refused: ' + relative)
    if not path.is_file() or path.stat().st_size > maximum:
        raise SourceReviewError('RM17 missing or unbounded reviewed source: ' + relative)
    data = path.read_bytes()
    if b'\0' in data:
        raise SourceReviewError('RM17 binary source refused: ' + relative)
    return data


def _resolve_source(repo: Path, project: Path, package_path: str, token: str,
                    tracked: set[str]) -> str:
    if not _SOURCE_ARG.fullmatch(token):
        raise SourceReviewError('RM17 unreviewed script source path: ' + token[:100])
    cursor = project
    for part in (package_path + '/' + token if package_path else token).split('/'):
        if part == '.':
            continue
        if part == '..':
            cursor = cursor.parent
        else:
            cursor = cursor / part
        if cursor.is_symlink():
            raise SourceReviewError('RM17 symlink in source path')
    try:
        relative = cursor.relative_to(repo).as_posix()
    except ValueError:
        raise SourceReviewError('RM17 composed source escapes consumer repository') from None
    _tracked_data(repo, relative, tracked)
    return relative


def review_node_pipeline(repo: Path, project: Path, packages: dict[str, dict],
                         *, manager: str, max_steps: int = 48) -> dict[str, Any]:
    """Review a closed npm/pnpm/yarn/bun script graph without invoking a shell.

    Each step must be a literal manager-run, Node/Python file, or a bounded
    Python unittest-discovery command. Python/Node files are Git-tracked.
    Chaining only supports the literal ` && ` operator. Arbitrary shell
    operators, env interpolation, flags, redirections and cycles are refused.
    """
    repo, project = repo.resolve(), project.resolve()
    if not project.is_relative_to(repo) or manager not in {'npm', 'pnpm', 'yarn', 'bun'}:
        raise SourceReviewError('RM17 nested project/manager outside reviewed bounds')
    tracked = _git_tracked(repo)
    seen: set[tuple[str, str]] = set()
    active: set[tuple[str, str]] = set()
    sources: set[str] = set()
    steps: list[dict[str, str]] = []

    def bind(relative: str, pkgpath: str) -> None:
        sources.add(_resolve_source(repo, project, pkgpath, relative, tracked))

    def visit(pkgpath: str, script_name: str) -> None:
        key = (pkgpath, script_name)
        if key in active:
            raise SourceReviewError('RM17 recursive npm script cycle')
        if key in seen:
            return
        if len(seen) >= 48 or len(active) >= 20:
            raise SourceReviewError('RM17 composed script graph exceeds bound')
        scripts = packages.get(pkgpath, {}).get('scripts', {})
        if not _NAME.fullmatch(script_name) or script_name not in scripts:
            raise SourceReviewError('RM17 undeclared or unsafe script reference')
        raw = scripts[script_name]
        if not isinstance(raw, str) or not 0 < len(raw) <= 1024 or '\n' in raw or '\r' in raw:
            raise SourceReviewError('RM17 unbounded or multiline script')
        # Reject every shell operator except the literal, unquoted conjunction.
        if any(c in raw for c in (';', '|', '<', '>', '$', '`', '\\', '\t')):
            raise SourceReviewError('RM17 command injection/operator refused')
        chunks = raw.split(' && ')
        if not chunks or len(chunks) > 12 or any(not x or '&' in x for x in chunks):
            raise SourceReviewError('RM17 unreviewed pipeline conjunction')
        active.add(key)
        try:
            for chunk in chunks:
                if len(steps) >= max_steps:
                    raise SourceReviewError('RM17 pipeline exceeds step budget')
                try:
                    argv = shlex.split(chunk, posix=True)
                except ValueError as exc:
                    raise SourceReviewError('RM17 malformed quoted script step') from exc
                if not argv:
                    raise SourceReviewError('RM17 empty pipeline step')
                tool = argv[0]
                if tool == manager and len(argv) == 3 and argv[1] == 'run':
                    if argv[2].startswith(('pre', 'post')):
                        raise SourceReviewError('RM17 npm lifecycle side effect refused')
                    visit(pkgpath, argv[2])
                elif tool == 'node' and len(argv) == 2 and argv[1].endswith(('.js', '.mjs')):
                    bind(argv[1], pkgpath)
                elif tool == 'python3' and len(argv) >= 2:
                    if argv[1:4] == ['-m', 'unittest', 'discover']:
                        if argv[4:] != ['-s', 'tests', '-p', 'test_*.py'] or "'test_*.py'" not in chunk:
                            raise SourceReviewError('RM17 only literal quoted unittest discover is admitted')
                        start = project / pkgpath / 'tests'
                        if not start.is_dir() or start.is_symlink():
                            raise SourceReviewError('RM17 unittest source directory missing')
                        found = [p for p in start.rglob('test_*.py') if p.is_file()]
                        if not 1 <= len(found) <= 200:
                            raise SourceReviewError('RM17 unittest source count exceeds bound')
                        for p in found:
                            try: rel = p.relative_to(repo).as_posix()
                            except ValueError: raise SourceReviewError('RM17 unittest path escaped source') from None
                            _tracked_data(repo, rel, tracked)
                            sources.add(rel)
                    elif (argv[1].endswith('.py') and
                          all(_PY_FLAG.fullmatch(arg) for arg in argv[2:])):
                        bind(argv[1], pkgpath)
                    else:
                        raise SourceReviewError('RM17 unreviewed Python command')
                else:
                    raise SourceReviewError('RM17 unreviewed composed command: ' + tool[:50])
                steps.append({'package': pkgpath, 'script': script_name, 'command': chunk})
            seen.add(key)
        finally:
            active.remove(key)

    for pkgpath, entry in packages.items():
        scripts = entry['scripts']
        if any(not _NAME.fullmatch(k) or k.startswith(('pre', 'post')) for k in scripts):
            raise SourceReviewError('RM17 dangerous or invalid npm helper script')
        for name in ('typecheck', 'lint', 'test', 'build', 'pack'):
            if name in scripts:
                visit(pkgpath, name)
    if not steps:
        raise SourceReviewError('RM17 no reviewed compositional scripts found')
    hashes = {p: hashlib.sha256(_tracked_data(repo, p, tracked)).hexdigest() for p in sorted(sources)}
    return {'policy': 'reviewed-composite', 'source_sha256': hashes,
            'repo_script_sources': sorted(sources), 'steps': steps,
            'execution_performed': False, 'release_qualified': False}


def _braced(text: str, start: int) -> str:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{': depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0: return text[start + 1:i]
    raise SourceReviewError('RM17 unbalanced Gradle convention declaration')


def review_jvm_conventions(root: Path, module_build: str,
                           tracked: set[str] | None = None) -> dict[str, Any]:
    """Resolve Git-tracked included-build convention plugins by literal IDs.

    All entries and implementation classes must be statically declared. This
    does not execute arbitrary Kotlin/Gradle and does not admit Android tasks.
    """
    root = root.resolve()
    tracked = _git_tracked(root) if tracked is None else tracked
    candidates = [p for p in ('settings.gradle.kts', 'settings.gradle') if p in tracked]
    if len(candidates) != 1:
        raise SourceReviewError('RM17 requires unambiguous checked-in Gradle settings')
    settings = candidates[0]
    settings_text = _tracked_data(root, settings, tracked).decode('utf-8')
    settings_text = re.sub(r'(?m)^\s*//[^\n]*$', '', settings_text)
    paths = re.findall(r'\bincludeBuild\(\s*["\']([A-Za-z0-9_-]{1,80})["\']\s*\)', settings_text)
    if not paths or len(paths) != len(re.findall(r'\bincludeBuild\s*\(', settings_text)) or len(paths) > 4:
        raise SourceReviewError('RM17 dynamic/missing included Gradle build')
    sources: dict[str, str] = {}
    def get(name: str) -> str:
        data = _tracked_data(root, name, tracked)
        sources[name] = hashlib.sha256(data).hexdigest()
        try: return data.decode('utf-8')
        except UnicodeError: raise SourceReviewError('RM17 non-UTF-8 Gradle source') from None
    get(settings)
    registry: dict[str, str] = {}
    for included in paths:
        child_settings = [p for p in (included + '/settings.gradle.kts', included + '/settings.gradle') if p in tracked]
        if len(child_settings) != 1:
            raise SourceReviewError('RM17 included build has ambiguous/missing tracked settings')
        get(child_settings[0])
        for item in sorted(tracked):
            if not item.startswith(included + '/') or not item.endswith('/build.gradle.kts'):
                continue
            parent = Path(item).parent
            if len(parent.parts) != 2:
                continue
            body = get(item)
            if 'gradlePlugin' not in body:
                continue
            regex = re.compile(r'\bregister\(\s*["\']([A-Za-z][A-Za-z0-9_-]{0,79})["\']\s*\)\s*\{')
            found = list(regex.finditer(body))
            if not found or len(found) != len(re.findall(r'\bregister\s*\(', body)):
                raise SourceReviewError('RM17 dynamic Gradle convention registration')
            for match in found:
                block = _braced(body, match.end() - 1)
                ids = re.findall(r'\bid\s*=\s*["\']([^"\']+)["\']', block)
                impls = re.findall(r'\bimplementationClass\s*=\s*["\']([^"\']+)["\']', block)
                if len(ids) != 1 or len(impls) != 1 or not _PLUGIN.fullmatch(ids[0]) or not _CLASS.fullmatch(impls[0]):
                    raise SourceReviewError('RM17 ambiguous Gradle convention plugin identity')
                if ids[0] in registry:
                    raise SourceReviewError('RM17 duplicate Gradle convention plugin')
                registry[ids[0]] = (parent / 'src/main/kotlin' / (impls[0] + '.kt')).as_posix()
    if not registry or len(registry) > 64:
        raise SourceReviewError('RM17 missing/unbounded checked-in convention registry')
    module_text = re.sub(r'(?m)^\s*//[^\n]*$', '', get(module_build))
    if re.search(r'\balias\s*\(', module_text):
        raise SourceReviewError('RM17 convention plugin review requires literal plugin ID')
    ids = re.findall(r'\bid\s*\(\s*["\']([^"\']+)["\']\s*\)', module_text)
    if not ids or len(ids) != len(re.findall(r'\bid\s*\(', module_text)):
        raise SourceReviewError('RM17 dynamic/absent Gradle plugin ID')
    seen: set[str] = set()
    active: set[str] = set()
    terminal: set[str] = set()
    android_lint = False

    def resolve(plugin: str) -> None:
        nonlocal android_lint
        if plugin in seen:
            return
        if plugin in active or len(active) >= 12:
            raise SourceReviewError('RM17 Gradle convention plugin cycle/depth')
        if plugin in _JVM or plugin in _DECORATORS:
            if plugin == 'com.android.lint': android_lint = True
            terminal.add(plugin)
            seen.add(plugin)
            return
        if _ANDROID.search(plugin) or plugin.startswith('com.android.'):
            raise SourceReviewError('RM17 Android plugin in JVM-only convention closure')
        target = registry.get(plugin)
        if not target:
            raise SourceReviewError('RM17 unreviewed Gradle convention ID: ' + plugin[:90])
        active.add(plugin)
        try:
            body = get(target)
            if re.search(r'\b(?:exec|Exec|ProcessBuilder|Runtime\.getRuntime|afterEvaluate|apply\s+from)\b', body):
                raise SourceReviewError('RM17 dynamic code in convention implementation')
            calls = re.findall(r'\bpluginManager\.apply\(\s*["\']([^"\']+)["\']\s*\)', body)
            if not calls or len(calls) != len(re.findall(r'\bpluginManager\.apply\s*\(', body)):
                raise SourceReviewError('RM17 dynamic/unresolved convention application')
            for item in calls:
                resolve(item)
        finally:
            active.remove(plugin)
        seen.add(plugin)

    for plugin in ids:
        resolve(plugin)
    if not any(p in _JVM for p in terminal):
        raise SourceReviewError('RM17 convention closure lacks reviewed JVM plugin')
    return {'identity': 'jvm', 'source_sha256': sources,
            'convention_ids': sorted(seen), 'android_lint_adjacent': android_lint,
            'execution_performed': False, 'release_qualified': False}
