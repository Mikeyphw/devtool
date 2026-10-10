from __future__ import annotations
import copy, json
from pathlib import Path
from devtool.final_seal import _final_remediation_coverage_errors

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / 'FINAL_REMEDIATION_COVERAGE.json').read_text())
TARGETS = {'android_hud', 'devtool'}


def check(tmp_path, value):
    (tmp_path / 'FINAL_REMEDIATION_COVERAGE.json').write_text(json.dumps(value))
    return _final_remediation_coverage_errors(tmp_path, TARGETS)


def test_canonical_coverage_passes(tmp_path):
    assert check(tmp_path, copy.deepcopy(BASE)) == []


def test_fake_unique_id_does_not_count_as_authoritative_coverage(tmp_path):
    altered = copy.deepcopy(BASE)
    altered['finding_assignments'][0]['id'] = 'FORGED-UNIQUE'
    assert 'finding_assignments_authority_digest' in check(tmp_path, altered)


def test_reassigned_owner_is_rejected_even_when_total_unchanged(tmp_path):
    altered = copy.deepcopy(BASE)
    altered['finding_assignments'][0]['overlay'] = 2
    assert 'finding_assignments_authority_digest' in check(tmp_path, altered)
    assert 'overlay_counts_assignment_mismatch' in check(tmp_path, altered)


def test_overlay_counts_must_bind_to_each_owner_not_only_sum(tmp_path):
    altered = copy.deepcopy(BASE)
    altered['overlay_counts']['1'] += 1
    altered['overlay_counts']['2'] -= 1
    assert 'overlay_counts_assignment_mismatch' in check(tmp_path, altered)


def test_overlay_title_cannot_be_replaced(tmp_path):
    altered = copy.deepcopy(BASE)
    altered['finding_assignments'][0]['overlay_title'] = 'forged proof'
    assert 'finding_assignments_authority_digest' in check(tmp_path, altered)


def test_legacy_schema_cannot_bypass_pinned_assignments(tmp_path):
    altered = copy.deepcopy(BASE)
    altered['schema_version'] = 1
    altered.pop('finding_assignments')
    assert 'canonical_schema_version' in check(tmp_path, altered)
