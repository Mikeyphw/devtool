"""Read-only G3 evidence consistency preflight.

Does not reseal, rewrite hashes, qualify a native host, or infer a release pass.
Emits bounded discrepancies that prevent false-positive final qualifications.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Mapping

def assess_evidence(root: Path, disposition_files: Mapping[str,str],
                    attestation_files: Mapping[str,str] | None = None) -> dict:
    root = Path(root).resolve()
    failures = []
    entries = 0
    for family, name in disposition_files.items():
        path = root / name
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
            rows = doc.get('rows')
            if not isinstance(rows,list): raise ValueError('rows missing')
        except (OSError, ValueError, TypeError) as exc:
            failures.append({'family':family,'kind':'disposition-unreadable','detail':str(exc)})
            continue
        for row in rows:
            identity = str(row.get('source_id') or row.get('promise_id') or row.get('obligation_id') or '?')
            refs = row.get('evidence') or []
            pins = row.get('evidence_sha256') or {}
            if not isinstance(refs,list) or not isinstance(pins,dict) or set(refs)!=set(pins) or not refs:
                failures.append({'family':family,'id':identity,'kind':'binding-incomplete'})
                continue
            for rel in refs:
                entries+=1
                # Historical provenance is untrusted until confined within the repository.
                target = (root / str(rel)).resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    failures.append({'family':family,'id':identity,'kind':'missing-or-escaped-evidence','path':str(rel)})
                    continue
                digest=hashlib.sha256(target.read_bytes()).hexdigest()
                if digest != pins[rel]:
                    failures.append({'family':family,'id':identity,'kind':'stale-pin','path':str(rel)})
    for family, name in (attestation_files or {}).items():
        path=root/name
        try:
            att=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(att,dict) or not att:
                raise ValueError('empty attestation')
        except (OSError,ValueError) as exc:
            failures.append({'family':family,'kind':'attestation-unreadable','detail':str(exc)})
    return {'status':'blocked' if failures else 'consistent',
            'entries_checked':entries, 'issues':failures, 'issue_count':len(failures)}

def main() -> int:
    import sys
    print("Historical G3 preflight retired; use python -m devtool.g3r_evidence_gate",file=sys.stderr)
    return 2

if __name__ == '__main__':
    raise SystemExit(main())
