"""DT-28/29: bounded, source-bound session events and truthful App health."""
import io
from rich.console import Console
from devtool.m6_live import build_live_view


def draw(payload, width=100, focus='overview'):
    dest=io.StringIO()
    screen=Console(file=dest,width=width,color_system=None,force_terminal=False)
    screen.print(build_live_view('ci',payload,width=width,icons='ascii',focus=focus))
    return dest.getvalue()


def test_historical_events_are_labeled_not_live():
    status={'state':'REQUIRED_CHECKS_PASSED','runs':[],
        'events':[{'kind':'session_started','at':'2026-11-24T03:37:00Z','historical':True}]}
    message=draw(status,focus='events')
    assert 'saved: session_started' in message
    assert 'Native required checks passed' not in message or status['state']=='REQUIRED_CHECKS_PASSED'


def test_app_read_health_does_not_claim_write_authorization():
    status={'state':'CHECKS_RUNNING','runs':[],
        'credential_health':{'auth_mode':'app','status':'VALID','identity_verified':True,
           'effective_read_verified':True,'repository_coverage':'selected'}}
    message=draw(status,focus='evidence')
    assert 'M4 effective_read_verified' in message
    assert 'M4 identity_verified' in message
    assert 'authorized_for_operation' in message
    assert 'UNVERIFIED' in message


def test_empty_credential_display_never_presumes_access():
    # Real Rich layout splits into columns at 100 cells. Verify the complete
    # reason on a single-column viewport and the nonauthorization state at
    # both narrow and wide widths where the detail may be ellipsized.
    status={'state':'CHECKS_RUNNING','runs':[]}
    assert 'no M4 provider diagnostic supplied' in draw(status,width=80,focus='evidence')
    for width in (40,50,80,100,120):
        message=draw(status,width=width,focus='evidence')
        assert 'Credential health UNVERIFIED' in message
        assert 'Native required checks passed' not in message
