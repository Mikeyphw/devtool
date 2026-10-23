"""Reviewable single-file migration of project Gradle JVM overrides.

Only an already-configured Devtool protected heap ceiling is authoritative. The
migration never invents or downgrades that policy. No Gradle execution occurs.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile
from .github_actions import GitHubActionsError

_REPAIR_LINE = re.compile(r'(?m)^org\.gradle\.jvmargs\s*=\s*(.+?)\s*$')
_HEAP = re.compile(r'(?:^|\s)-Xmx(\d+)([mMgG])(?=\s|$)')


def gradle_options_repair(root: Path, *, project_dir: str = '.', target: str = '',
                          execute: bool = False, expected_sha256: str = '') -> dict:
    from .config import load_project_config
    from .actions_android import _project_root
    project, relative = _project_root(root, project_dir)
    path = project/'gradle.properties'
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 64*1024:
        raise GitHubActionsError('Gradle options repair requires a regular bounded project gradle.properties')
    config = load_project_config(str(root), None, target_name=target)
    options = dict(config.runner_options or {})
    nested = options.get('gradle')
    if isinstance(nested, dict): options.update(nested)
    policy = options.get('gradle_heap_mb') or options.get('heap_default_mb')
    if type(policy) is not int or policy < 256 or policy > 16384:
        raise GitHubActionsError('No explicit protected Devtool Gradle heap; configure it before migration')
    original = path.read_bytes()
    if b'\x00' in original or len(original)>64*1024:
        raise GitHubActionsError('Invalid JVM property source bytes')
    try: text = original.decode('utf-8')
    except UnicodeDecodeError:
        raise GitHubActionsError('Gradle JVM properties are not UTF-8') from None
    matches=list(_REPAIR_LINE.finditer(text))
    if len(matches)!=1:
        raise GitHubActionsError('Exactly one old JVM setting is required for safe repair')
    value=matches[0].group(1)
    heap = _HEAP.search(value)
    if not heap or re.search(r'\$|`|\\\n|\s-Dorg\.gradle\.jvmargs|\s-XX:',value):
        raise GitHubActionsError('Ambiguous or executable JVM flags require manual review')
    old_mb = int(heap.group(1)) * (1024 if heap.group(2).lower()=='g' else 1)
    if old_mb > 32768:
        raise GitHubActionsError('Unbounded legacy JVM heap')
    # Retain only the safe, known non-heap flags. Unknown flags require review.
    remainder = (value[:heap.start()]+' '+value[heap.end():]).split()
    if any(not re.fullmatch(r'-(?:Dfile\.encoding=UTF-8|Duser\.language=[A-Za-z]{2}|Duser\.country=[A-Za-z]{2})',v) for v in remainder):
        raise GitHubActionsError('Unreviewed legacy JVM arguments need manual migration')
    # If an explicit old requirement exceeds the protected ceiling, removing it
    # would silently lower resource policy. Refuse and require config change first.
    if old_mb > policy:
        raise GitHubActionsError('Protected Devtool JVM heap is lower than legacy request; cannot downgrade')
    # Preserve non-heap flags, but let Devtool own heap policy, not gradle.properties.
    proposed=''.join(remainder)
    if proposed:
        proposed=' '.join(remainder)
        replacement='org.gradle.jvmargs='+proposed
    else:
        replacement='# org.gradle.jvmargs is managed by the protected Devtool Gradle policy'
    updated=text[:matches[0].start()]+replacement+text[matches[0].end():]
    original_sha=hashlib.sha256(original).hexdigest()
    result={'operation':'gradle-options-repair','project_dir':relative,'target':target,
            'file':str(path),'source_sha256':original_sha,'protected_heap_mb':policy,
            'legacy_heap_mb':old_mb,'before':text,'after':updated,
            'changes_required':updated!=text,'applied':False}
    if not execute:return result
    if not expected_sha256 or expected_sha256!=original_sha or hashlib.sha256(path.read_bytes()).hexdigest()!=original_sha:
        raise GitHubActionsError('Gradle JVM migration source changed since preview')
    fd, temporary=tempfile.mkstemp(prefix='.gradle-properties-',dir=project)
    try:
        os.fchmod(fd, path.stat().st_mode & 0o777)
        with os.fdopen(fd,'wb') as f:
            f.write(updated.encode());f.flush();os.fsync(f.fileno())
        os.replace(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    result['applied']=True
    return result
