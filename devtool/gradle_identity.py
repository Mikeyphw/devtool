"""FC-03: conservative static Gradle plugin identity, shared by JVM and onboarding.

Never executes Gradle, Kotlin, shell or version-catalog expressions. A missing,
ambiguous or dynamic declaration is a review requirement, not a JVM default.
"""
from __future__ import annotations

from pathlib import Path
import re
import tomllib

class GradleIdentityError(ValueError):
    pass

_ANDROID_ID = re.compile(r'^com\.android\.(?:application|library|test|dynamic-feature|asset-pack|feature)$')
_JVM_IDS = {'java','java-library','application','org.jetbrains.kotlin.jvm','org.jetbrains.kotlin.kapt', 'kotlin'}
_ALIAS = re.compile(r'\balias\s*\(\s*libs\.plugins\.([A-Za-z][\w.]*)\s*\)')
_ID = re.compile(r'\bid\s*(?:\(\s*[\'\"]([\w.-]+)[\'\"]\s*\)|\s+[\'\"]([\w.-]+)[\'\"])')
_KOTLIN = re.compile(r'\bkotlin\s*\(\s*[\'\"](jvm|android)[\'\"]\s*\)')
_MAX = 1024 * 1024


def _read(root: Path, name: str, tracked: set[str]) -> str:
    if name not in tracked:
        raise GradleIdentityError('Gradle identity source is untracked: '+name)
    p=root
    for bit in name.split('/'):
        if bit in ('','.','..'):
            raise GradleIdentityError('unsafe Gradle identity source')
        p=p/bit
        if p.is_symlink():
            raise GradleIdentityError('Gradle identity source symlink refused')
    if not p.is_file() or p.stat().st_size>_MAX:
        raise GradleIdentityError('missing or oversized Gradle identity source')
    try: return p.read_text(encoding='utf-8')
    except (UnicodeError,OSError) as exc: raise GradleIdentityError('unreadable Gradle identity source') from exc


def gradle_identity(root: Path, build: str, tracked: set[str]) -> tuple[str, str]:
    """Return ('android'|'jvm'|'review', reason) for tracked source.

    This does not establish whether a job is safe to execute; RM12 separately
    checks modules, locks, toolchain/version and denied dynamic execution.
    """
    text=_read(root,build,tracked)
    # Remove comments, not quoted arbitrary scripts; nonliteral plugin syntax
    # is conservatively REVIEW rather than interpreted as executable code.
    text=re.sub(r'(?m)^\s*//[^\n]*$', '', text)
    if re.search(r'com\.android\.|\bkotlin\s*\(\s*[\'\"]android[\'\"]',text):
        return 'android','literal Android plugin or marker'
    if re.search(r'\bapply\s+(?:plugin|from)\s*:|\bapply\s*\(',text):
        return 'review','dynamic or legacy Gradle apply requires manual review'
    references=_ALIAS.findall(text)
    ids=[x or y for x,y in _ID.findall(text)]
    ids += ['org.jetbrains.kotlin.'+x for x in _KOTLIN.findall(text)]
    # Any plugin alias not parsed as a supported *literal* catalog reference
    # must block JVM acceptance, even when another Java plugin is present.
    if len(re.findall(r'\balias\s*\(',text))!=len(references):
        return 'review','dynamic Gradle plugin alias'
    if re.search(r'\bid\s*\(',text) and len(re.findall(r'\bid\s*\(',text))>len(re.findall(r'\bid\s*\(\s*[\'\"]',text)):
        return 'review','dynamic Gradle plugin ID'
    if references:
        catalog='gradle/libs.versions.toml'
        try:
            parsed=tomllib.loads(_read(root,catalog,tracked))
            plugins=parsed.get('plugins')
            if not isinstance(plugins,dict):raise ValueError('catalog lacks plugins')
            aliases={re.sub(r'[-_.]','.',str(k)):v for k,v in plugins.items()}
            for ref in references:
                row=aliases.get(ref)
                if isinstance(row,dict) and isinstance(row.get('id'),str):
                    ids.append(row['id'])
                else:
                    return 'review','unresolved version catalog plugin alias: '+ref
        except (ValueError,GradleIdentityError,tomllib.TOMLDecodeError) as exc:
            return 'review','unresolved or unsafe Gradle version catalog: '+str(exc)[:100]
    if any(_ANDROID_ID.fullmatch(i) for i in ids):return 'android','resolved Android plugin identity'
    if any(i not in _JVM_IDS for i in ids):return 'review','unknown plugin identity requires review'
    if ids or re.search(r'\b(?:java|java-library)\s*(?:\n|\}|$)',text):
        return 'jvm','reviewed JVM plugin identity'
    return 'review','no statically reviewed Gradle plugin identity'
