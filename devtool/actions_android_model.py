"""Read-only, bounded Android Gradle settings/variant discovery.

Never evaluates Gradle scripts. Anything not statically representable requires a
reviewed project/model override; source discovery is not a release assertion.
"""
from __future__ import annotations
import hashlib
import re
from pathlib import Path
import tomllib
from .github_actions import GitHubActionsError

_NAME = re.compile(r':[A-Za-z][A-Za-z0-9_-]*(?::[A-Za-z][A-Za-z0-9_-]*){0,7}\Z')
_VARIANT = re.compile(r'[a-z][A-Za-z0-9]{0,60}\Z')
_PATH = re.compile(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z')

def _read(path: Path, root: Path) -> str:
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size > 512*1024:
        raise GitHubActionsError('Unsafe, oversized or external Gradle source')
    return path.read_text('utf-8')

def _strip_comments(text: str) -> str:
    # Only simple comment forms are reviewed; no scripting or interpolation permitted in include paths.
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return re.sub(r'(?m)^\s*//[^\n]*$', '', text)

def _include_names(text: str) -> set[str]:
    found: set[str] = set()
    spans: list[tuple[int,int]] = []
    for match in re.finditer(r'(?m)^\s*include\s*\(', text):
        depth, end = 1, match.end()
        while end < len(text) and depth and end - match.end() < 8192:
            if text[end] == '(': depth += 1
            elif text[end] == ')': depth -= 1
            end += 1
        if depth:
            raise GitHubActionsError('Unclosed or dynamic Gradle module include')
        payload = text[match.end():end-1]
        parts = re.split(r'\s*,\s*', payload.strip())
        if not parts or any(not re.fullmatch(r'''["'](:[A-Za-z0-9_:-]+)["']''', p.strip()) for p in parts):
            raise GitHubActionsError('Dynamic Gradle include requires explicit reviewed override')
        found.update(p.strip()[1:-1] for p in parts)
        spans.append((match.start(),end))
    # Groovy include ':app', ':core' optionally wrapping onto following lines.
    for match in re.finditer(r'(?m)^\s*include\s+(?!\()(.*(?:\n\s*["\']:[^\n]+)*)', text):
        payload = match.group(1).strip()
        parts = [p.strip() for p in payload.split(',')]
        if not parts or any(not re.fullmatch(r'''["'](:[A-Za-z0-9_:-]+)["']''', p) for p in parts):
            raise GitHubActionsError('Dynamic Gradle include requires explicit reviewed override')
        found.update(p[1:-1] for p in parts)
    if not found:
        raise GitHubActionsError('No statically reviewed Gradle module includes')
    if any(not _NAME.fullmatch(x) for x in found) or len(found)>128:
        raise GitHubActionsError('Invalid Gradle module name/count')
    return found

def discover_modules(project: Path) -> dict:
    settings = next((p for p in (project/'settings.gradle.kts', project/'settings.gradle') if p.is_file()), None)
    if settings is None: raise GitHubActionsError('Missing Gradle settings')
    source = _strip_comments(_read(settings,project))
    names = _include_names(source)
    aliases = {}
    catalog = project/'gradle/libs.versions.toml'
    if catalog.is_file():
        raw = tomllib.loads(_read(catalog,project)).get('plugins',{})
        for k,v in raw.items(): aliases[k.replace('-','.')]=v if isinstance(v,str) else v.get('id','') if isinstance(v,dict) else ''
    relocations = {}
    for m in re.finditer(r'''project\(\s*["'](:[^"']+)["']\s*\)\.projectDir\s*=\s*file\(\s*["']([^"']+)["']\s*\)''', source):
        key, value = m.group(1), m.group(2)
        if key not in names or not _PATH.fullmatch(value):
            raise GitHubActionsError('Unsafe or unregistered Gradle projectDir relocation')
        relocations[key] = value
    if 'projectDir' in source and not relocations:
        raise GitHubActionsError('Dynamic Gradle projectDir requires reviewed override')
    result={}
    for name in sorted(names):
        module=name.lstrip(':')
        rel=relocations.get(name, module.replace(':','/'))
        location=project/rel
        if location.is_symlink() or not location.resolve().is_relative_to(project.resolve()):
            raise GitHubActionsError('Android module directory escapes project')
        scripts=[p for p in (location/'build.gradle.kts',location/'build.gradle') if p.is_file()]
        if not scripts: continue
        gradle=_strip_comments(_read(scripts[0],project))
        plugins = set(re.findall(r'''(?:id\s*\(\s*|id\s+)["']([^"']+)["']''',gradle))
        for alias in re.findall(r'alias\s*\(\s*libs\.plugins\.([\w.]+)\s*\)', gradle):
            plugins.add(aliases.get(alias,''))
        for plugin in tuple(plugins):
            if not plugin or plugin in ('com.android.application','com.android.library'): continue
            path=project/'build-logic/src/main/kotlin'/f'{plugin}.gradle.kts'
            if path.is_file() and re.search(r'''com\.android\.application''',_read(path,project)):
                plugins.add('com.android.application')
        if 'com.android.application' not in plugins: continue
        pkg_match=re.search(r'''\bapplicationId\s*(?:=|\s)\s*["']([A-Za-z_][\w.]*)["']''',gradle)
        pkg = pkg_match.group(1) if pkg_match else None
        flavors = set(re.findall(r'''\b(?:create\s*\(\s*|register\s*\(\s*|\b)(["'])([a-z][A-Za-z0-9]*)\1''',gradle))
        # A build's flavor declarations must be identified from productFlavors block, not arbitrary strings.
        fl = re.search(r'\bproductFlavors\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', gradle, re.S)
        flavor_names=set()
        if fl:
            flavor_names.update(re.findall(r'''\b(?:create\s*\(\s*|register\s*\(\s*)["']([a-z][A-Za-z0-9]*)["']''',fl.group(1)))
            flavor_names.update(re.findall(r'''(?m)^\s*([a-z][A-Za-z0-9]*)\s*\{''',fl.group(1)))
        variants={'debug':{'build_type':'debug','flavor':''},'release':{'build_type':'release','flavor':''}}
        if flavor_names:
            variants={}
            for flavor in sorted(flavor_names):
                for build_type in ('debug','release'):
                    key=flavor+build_type.title()
                    if _VARIANT.fullmatch(key): variants[key]={'build_type':build_type,'flavor':flavor}
        result[module]={"module":module,"gradle_file":scripts[0].relative_to(project).as_posix(),
                        "manifest_present":(location/'src/main/AndroidManifest.xml').is_file(),
                        "application_id":pkg,"application_id_static":pkg is not None,
                        "project_path":rel,"variants":variants,"settings_sha256":hashlib.sha256(settings.read_bytes()).hexdigest(),
                        "gradle_sha256":hashlib.sha256(scripts[0].read_bytes()).hexdigest()}
    return result
