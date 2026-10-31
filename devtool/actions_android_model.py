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
        # Kotlin/Groovy permit a terminal comma in include(":app", ":lib",).
        # Ignore exactly one terminal separator; interior/leading empties and
        # dynamic expressions still fail closed below.
        if len(parts) > 1 and parts[-1] == '':
            parts.pop()
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

def _block(source: str, name: str) -> str:
    '''Return exactly one constant DSL block; refuse malformed nested braces.'''
    starts=list(re.finditer(r'\b'+re.escape(name)+r'\s*\{', source))
    if len(starts)>1: raise GitHubActionsError('Ambiguous Gradle '+name+' declarations')
    if not starts: return ''
    i=starts[0].end(); start=i; depth=1
    while i<len(source):
        if source[i]=='{': depth+=1
        if source[i]=='}':
            depth-=1
            if not depth:return source[start:i]
        i+=1
    raise GitHubActionsError('Unclosed Gradle '+name+' block')


def _names(block: str) -> dict[str,str]:
    '''Locate literal named DSL registrations with their local body.'''
    out={}
    pattern=r'(?m)(?:\b(?:create|register|maybeCreate|getByName)\s*\(\s*["\']([a-z][A-Za-z0-9]*)["\']\s*\)|^\s*([a-z][A-Za-z0-9]*)\s*)\s*\{'
    for match in re.finditer(pattern,block):
        name=match.group(1) or match.group(2)
        if name in out:raise GitHubActionsError('Duplicate Gradle dimension/build-type declaration')
        start=match.end();i=start;depth=1
        while i<len(block) and depth:
            if block[i]=='{':depth+=1
            if block[i]=='}':depth-=1
            i+=1
        if depth:raise GitHubActionsError('Unclosed Gradle named DSL block')
        out[name]=block[start:i-1]
    return out


def _variants(gradle: str) -> dict:
    from itertools import product
    flavors=_names(_block(gradle,'productFlavors'))
    build_types={'debug','release'}
    build_types.update(_names(_block(gradle,'buildTypes')))
    if len(build_types)>16 or len(flavors)>32:
        raise GitHubActionsError('Android flavors or build types exceed reviewed matrix limits')
    dimensions=[]
    match=re.search(r'\bflavorDimensions\s*(?:\+=?\s*listOf\s*\(([^)]*)\)|\(?\s*((?:["\'][A-Za-z0-9_]+["\']\s*,?\s*)+)\)?)',gradle)
    if match:
        dimensions=re.findall(r'["\']([A-Za-z][A-Za-z0-9_]*)["\']',match.group(1) or match.group(2) or '')
        if len(set(dimensions))!=len(dimensions):raise GitHubActionsError('Duplicate Android flavor dimensions')
    names_by_dimension={d:[] for d in dimensions}
    for name,body in flavors.items():
        d=re.search(r'\bdimension\s*(?:=\s*|\s+)["\']([A-Za-z][A-Za-z0-9_]*)["\']',body)
        if dimensions:
            if d is None or d.group(1) not in names_by_dimension:
                raise GitHubActionsError('Android flavor has missing or unknown dimension')
            names_by_dimension[d.group(1)].append(name)
        elif d:
            raise GitHubActionsError('Android flavor dimension not declared')
    if len(flavors)>1 and not dimensions:
        # Single implicit flavor dimension is safe; multiple explicit dimensions are not inferred.
        dimensions=['_default'];names_by_dimension={'_default':sorted(flavors)}
    elif len(flavors)==1 and not dimensions:
        dimensions=['_default'];names_by_dimension={'_default':list(flavors)}
    if dimensions and any(not names_by_dimension[d] for d in dimensions):
        raise GitHubActionsError('Android dimension with no declared flavors')
    rows=list(product(*(sorted(names_by_dimension[d]) for d in dimensions))) if dimensions else [()]
    variants={}
    for row in rows:
        flavor=''.join(part if n==0 else part.title() for n,part in enumerate(row))
        for build_type in sorted(build_types):
            key=(flavor+build_type.title()) if flavor else build_type
            if not _VARIANT.fullmatch(key) or key in variants:
                raise GitHubActionsError('Android variant identity collision or invalid name')
            variants[key]={'build_type':build_type,'flavor':flavor,
                           'flavors':list(row),'dimensions':list(dimensions)}
            if len(variants)>96:raise GitHubActionsError('Android variant matrix exceeds 96 reviewed cells')
    return variants


def discover_modules(project: Path) -> dict:
    settings = next((p for p in (project/'settings.gradle.kts', project/'settings.gradle') if p.is_file()), None)
    if settings is None:raise GitHubActionsError('Missing Gradle settings')
    source=_strip_comments(_read(settings,project))
    names=_include_names(source)
    aliases={}
    catalog=project/'gradle/libs.versions.toml'
    if catalog.is_file():
        raw=tomllib.loads(_read(catalog,project)).get('plugins',{})
        for k,v in raw.items():aliases[k.replace('-','.')]=v if isinstance(v,str) else v.get('id','') if isinstance(v,dict) else ''
    relocations={}
    for match in re.finditer(r'''project\(\s*["'](:[^"']+)["']\s*\)\.projectDir\s*=\s*file\(\s*["']([^"']+)["']\s*\)''',source):
        key,value=match.group(1),match.group(2)
        if key not in names or not _PATH.fullmatch(value):
            raise GitHubActionsError('Unsafe or unregistered Gradle projectDir relocation')
        relocations[key]=value
    if 'projectDir' in source and not relocations:
        raise GitHubActionsError('Dynamic Gradle projectDir requires reviewed override')
    result={}
    for name in sorted(names):
        module=name.lstrip(':');rel=relocations.get(name,module.replace(':','/'))
        location=project/rel
        if location.is_symlink() or not location.resolve().is_relative_to(project.resolve()):
            raise GitHubActionsError('Android module directory escapes project')
        scripts=[p for p in (location/'build.gradle.kts',location/'build.gradle') if p.is_file()]
        if not scripts:continue
        gradle=_strip_comments(_read(scripts[0],project))
        plugins=set(re.findall(r'''(?:id\s*\(\s*|id\s+)["']([^"']+)["']''',gradle))
        for alias in re.findall(r'alias\s*\(libs\.plugins\.([\w.]+)\)',gradle):
            plugins.add(aliases.get(alias,''))
        for plugin in tuple(plugins):
            if not plugin or plugin in ('com.android.application','com.android.library'):continue
            for candidate in (project/'build-logic/src/main/kotlin'/f'{plugin}.gradle.kts',
                              project/'build-logic/src/main/groovy'/f'{plugin}.gradle'):
                if candidate.is_file() and 'com.android.application' in _read(candidate,project):
                    plugins.add('com.android.application')
        if 'com.android.application' not in plugins:continue
        pkg_match=re.search(r'''\bapplicationId\s*(?:=|\s)\s*["']([A-Za-z_][\w.]*)["']''',gradle)
        result[module]={'module':module,'gradle_file':scripts[0].relative_to(project).as_posix(),
                        'manifest_present':(location/'src/main/AndroidManifest.xml').is_file(),
                        'application_id':pkg_match.group(1) if pkg_match else None,
                        'application_id_static':pkg_match is not None,'project_path':rel,
                        'variants':_variants(gradle),
                        'settings_sha256':hashlib.sha256(settings.read_bytes()).hexdigest(),
                        'gradle_sha256':hashlib.sha256(scripts[0].read_bytes()).hexdigest()}
    return result
