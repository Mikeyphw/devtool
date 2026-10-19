"""Final H1/H2 source hardening: executable consumer and negative contracts."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import pytest
from devtool.environment_composition import _secret_safe_structure, _digest
from devtool.presentation_surfaces import (PresentationEvent, PresentationProjection, PresentationSurface, compose_dashboard, redact_mapping)
from devtool.rmp_final_hardening import independent_obligations, historical_probe_accounting

@pytest.mark.parametrize('bad', ['failed','blocked','cancelled','running','unknown','skipped','unexpected'])
def test_dashboard_green_summary_cannot_hide_adverse_event(bad):
    normal = PresentationEvent('ok','RUN','passed','ok')
    bad_event = PresentationEvent('bad','RUN',bad,'not green')
    p = PresentationProjection(PresentationSurface.TUI,'run','nominally passed','passed', (normal,bad_event),())
    result=compose_dashboard(p,limit=1)
    assert result.truncated
    assert result.status not in {'passed','succeeded','reused'}
    if bad=='failed': assert result.status=='failed'

def test_successful_dashboard_stays_successful():
    p=PresentationProjection(PresentationSurface.TUI,'run','done','passed', (PresentationEvent('ok','RUN','passed','ok'),),())
    assert compose_dashboard(p).status=='passed'

@pytest.mark.parametrize('label', ['API_TOKEN','DATABASE_PASSWORD','AUTH_COOKIE','SECRET_KEY'])
def test_nested_env_list_secret_rotation_is_identity_invariant(label):
    first={'exports':[f'{label}=PRIVATE_ALPHA','THEME=light']}
    second={'exports':[f'{label}=PRIVATE_BETA','THEME=light']}
    assert _digest(_secret_safe_structure(first))==_digest(_secret_safe_structure(second))
    assert 'PRIVATE_ALPHA' not in json.dumps(_secret_safe_structure(first))
    assert _digest(_secret_safe_structure(first))!=_digest(_secret_safe_structure({'exports':[f'{label}=PRIVATE_ALPHA','THEME=dark']}))

def test_plaintext_nested_presentation_details_redacted():
    secret='unsafe-value-7238'
    details={'provider':{'config':{'api_token':secret,'list':[{'password':secret},'mode=active']}},'status':'failed'}
    safe=redact_mapping(details)
    assert secret not in json.dumps(safe)
    assert safe['provider']['config']['list'][0]['password']=='<redacted>'
    event=PresentationEvent('one','RUN','failed','error',detail=details)
    assert secret not in json.dumps(event.as_dict())

def test_nested_secret_string_redaction():
    assert '<redacted>' in json.dumps(redact_mapping({'headers':['Bearer abcdefg123','mode=fast']}))

@pytest.mark.parametrize('state', ['PASS','SATISFIED','qualified','pass'])
def test_u01_does_not_autoclose_from_broad_label(state):
    out=independent_obligations({'ENV-A06':state,'ANDROID-A16':state,'DIST-A01':state,'DIST-A12':state,'DIST-A13':state,'TEST-A11':state})
    assert out['release_qualified'] is False
    assert out['formal_u01_closed'] is False

def test_missing_critical_substrate_evidence_remains_open():
    result=independent_obligations({})
    assert len(result['open_obligations'])==6
    assert not result['formal_u01_closed']

def test_historical_20_probes_are_unchanged_and_not_requalified():
    root=Path(__file__).resolve().parents[1]
    result=historical_probe_accounting(root)
    assert result['original_probes']==20
    assert result['historical_mismatches']==6
    assert not result['historical_probes_requalified']
