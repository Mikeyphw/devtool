"""R06B review-only overlay lifecycle decision, not an alternate apply/recovery path."""
from __future__ import annotations

from pathlib import Path
import subprocess
import os
import hashlib
import stat
from ..artifacts import artifact_inventory_entries
from typing import Any

from .checker import check_overlay
from .equivalence import inspect_patch_equivalence


def _legacy_inventory_presence(repo_root: Path, raw: dict[str, Any]) -> dict[str, Any]:
    """Conservative digest-only presence for overlays without a Git base tree.

    This does *not* imply a commit, trusted patch equivalence, or acceptance.
    Every touched file must match the complete validated archive inventory.
    """
    states: list[str] = []
    for entry in artifact_inventory_entries(raw):
        rel = str(entry.get('path') or '')
        kind = str(entry.get('action') or '').lower()
        expected = str(entry.get('sha256') or '').lower()
        preimage = str(entry.get('source_sha256') or '').lower()
        target = repo_root / rel
        # Never read through symlinked parents or symlink files. A symlink's
        # bytes are not equivalent to a regular source path's content.
        cur = repo_root
        unsafe = False
        for component in Path(rel).parts:
            cur = cur / component
            if cur.is_symlink():
                unsafe = True
                break
        if unsafe:
            states.append('divergent')
            continue
        try:
            st = target.lstat()
        except FileNotFoundError:
            st = None
        if st is not None and not stat.S_ISREG(st.st_mode):
            states.append('divergent')
            continue
        digest = (hashlib.sha256(target.read_bytes()).hexdigest() if st else None)
        if kind == 'delete':
            state = 'present' if digest is None else ('absent' if preimage and digest == preimage else 'divergent')
        elif kind in ('add','modify') and expected and digest == expected:
            state = 'present'
        elif (kind == 'add' and digest is None) or (kind == 'modify' and preimage and digest == preimage):
            state = 'absent'
        else:
            state = 'divergent'
        states.append(state)
    if states and all(x == 'present' for x in states):
        status = 'fully-present'
    elif states and all(x == 'absent' for x in states):
        status = 'absent'
    else:
        status = 'ambiguous'
    return {'status': status, 'authority': 'bounded-legacy-inventory-hashes',
            'not_git_patch_equivalence': True,
            'path_counts': {name: states.count(name) for name in ('present','absent','divergent')}}


def overlay_plan(repo_root: Path, artifact: str | Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    check = check_overlay(repo_root, artifact)  # Always enforce checksums.
    equivalence: dict[str, Any] = {'status': 'unavailable', 'messages': []}
    error = ''
    if check.structural_ok:
        try:
            equivalence = inspect_patch_equivalence(repo_root, artifact).to_dict()
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            error = f'Patch equivalence unavailable: {type(exc).__name__}'
    if check.structural_ok and equivalence.get('status') == 'legacy':
        equivalence = _legacy_inventory_presence(repo_root, check.manifest.get('raw') or {})
    proc = subprocess.run(['git', '-C', str(repo_root), 'status', '--porcelain', '--untracked-files=normal'],
                          text=True, capture_output=True, timeout=15)
    repo_clean = proc.returncode == 0 and not bool(proc.stdout.strip())
    state = str(equivalence.get('status') or 'unavailable')
    if state == 'fully-present' and check.structural_ok:
        disposition = 'already_present'
    elif state in ('partially-present', 'ambiguous'):
        disposition = 'manual_review'
    elif not check.structural_ok or not check.source_state_ok or check.conflicts:
        disposition = 'refuse'
    elif not repo_clean or error or state != 'absent':
        disposition = 'manual_review'
    else:
        disposition = 'candidate_only'
    guidance = {
        'already_present': 'Do not reapply. Inspect Git history and durable receipts for acceptance.',
        'manual_review': 'Preserve current source and transaction state; inspect conflicting or partial paths. Do not force or auto-rebase.',
        'refuse': 'Artifact integrity or source preflight failed; do not apply, force, or ignore checksums.',
        'candidate_only': 'Review the overlay preflight and apply explicitly with the existing transactional devtool apply overlay command.',
    }
    return {
        'schema_version': 1, 'producer': 'devtool-overlay-r06b-plan',
        'read_only': True, 'mutation_performed': False,
        'qualification': 'LOCAL_REVIEW_ONLY', 'decision': disposition,
        'safe_to_auto_apply': False, 'safe_to_auto_recover': False,
        'git_worktree_clean': repo_clean, 'git_inspection_ok': proc.returncode == 0,
        'integrity_ok': check.structural_ok, 'source_compatible': check.source_state_ok,
        'conflicts': [item.to_dict() for item in check.conflicts],
        'check_messages': list(check.messages),
        'patch_equivalence': equivalence, 'diagnostic': error,
        'next_step': guidance[disposition],
    }
