"""S5 current-behavior diagnostics: attribution, bounded logs and release isolation."""
from __future__ import annotations
import io
import sys
import types
from urllib.error import HTTPError

import pytest

from devtool import delivery_ci_diagnostics as diag

SHA='a'*40
SLUG='Mikeyphw/devtool'
RUNURL=f'https://github.com/{SLUG}/actions/runs/123'


def snapshot(state='CHECKS_FAILED', conclusion='failure', jobs=True):
    entry={'id':123,'attempt':2,'workflow_id':77,'name':'Hosted source tests',
           'event':'pull_request','head_sha':SHA,'status':'completed',
           'conclusion':conclusion,'url':RUNURL}
    return {'state':state,'source_sha':SHA,'required':['Hosted source tests'],
       'checks_qualified':state=='REQUIRED_CHECKS_PASSED',
       'runs':[entry], 'missing':[], 'ambiguous':[],
       'exact_run_evidence':{'issues':[]},
       'failures':([{'run_id':123,'attempt':2,'name':entry['name'],
           'detailed_jobs':([{'job_id':456,'job':'run unit tests','conclusion':'failure',
             'steps':[{'name':'pytest unit tests','conclusion':'failure'}]}] if jobs else [])}]
         if state=='CHECKS_FAILED' else []), 'release_qualified':False}


def test_failed_test_classification_and_exact_urls():
    obj=diag.diagnose_snapshot(snapshot(),slug=SLUG,fetch_logs=False)
    assert obj['aggregate']=='fail'
    assert obj['classification']=='required-check-failed'
    assert obj['matrix'][0]['attempt']==2 and obj['matrix'][0]['id']==123
    job=obj['failures'][0]['jobs'][0]
    assert job['classification']=='source-or-test-failure'
    assert job['url']==f'https://github.com/{SLUG}/actions/runs/123/attempts/2/job/456'
    assert not obj['release_qualified'] and not obj['remote_mutations']


def test_all_green_is_observational_only_without_release():
    obj=diag.diagnose_snapshot(snapshot('REQUIRED_CHECKS_PASSED','success'),slug=SLUG)
    assert obj['aggregate']=='pass' and obj['failures']==[]
    assert not obj['release_qualified']


@pytest.mark.parametrize('state,expected',[
    ('REGISTRATION_TIMED_OUT','registration-timeout'),
    ('REGISTRATION_PROVIDER_UNAVAILABLE','provider-unavailable'),
    ('BLOCKED_TRIGGER','workflow-configuration'),
    ('BRANCH_RULES_UNVERIFIED','branch-rule-unavailable'),
    ('BLOCKED_AMBIGUOUS','ambiguous-attribution'),
    ('DISPATCH_UNCORRELATED','uncorrelated-dispatch'),
    ('BLOCKED_CONDITIONAL','unproven-conditional-trigger'),
    ('CHECKS_EVIDENCE_PENDING','evidence-pending'),
    ('WAITING_REGISTRATION','registration-pending'),
])
def test_fail_closed_registration_and_provider_states(state,expected):
    row=snapshot(state,'success')
    row['runs']=[]
    row['missing']=['Hosted source tests']
    row['ambiguous']=['Hosted source tests'] if state=='BLOCKED_AMBIGUOUS' else []
    result=diag.diagnose_snapshot(row,slug=SLUG)
    assert result['classification']==expected
    assert result['aggregate']=='pending-or-blocked' and not result['release_qualified']


def test_unknown_and_spoofed_run_urls_refused():
    row=snapshot()
    row['runs'][0]['url']='https://attack.example/?sig=abc'
    with pytest.raises(ValueError,match='untrusted'):
        diag.diagnose_snapshot(row,slug=SLUG)


def test_invalid_run_attempt_refused():
    row=snapshot()
    row['runs'][0]['attempt']='two'
    with pytest.raises(ValueError,match='identity'):
        diag.diagnose_snapshot(row,slug=SLUG)


def test_log_reader_budget_three_only():
    row=snapshot()
    row['failures'][0]['detailed_jobs']=[{'job_id':i,'job':'test','conclusion':'failure',
                                          'steps':[]} for i in range(1,10)]
    calls=[]
    obj=diag.diagnose_snapshot(row,slug=SLUG,client=object(),
      log_reader=lambda client,slug,job:calls.append(job) or b'FAILED example')
    assert calls==[1,2,3] and len(obj['failures'][0]['jobs'])==8
    assert not obj['raw_log_content_persisted']


def test_redaction_uses_canonical_redactor_and_rejects_secrets(monkeypatch):
    module=types.ModuleType('devtool.logstore')
    module.redact_sensitive_text=lambda text:text.replace('SECRET_NOT_SHOWN','[REDACTED]')
    monkeypatch.setitem(sys.modules,'devtool.logstore',module)
    content=b'\n'.join((
      b'FAILED tests/test_valid.py::test_one',
      b'error: authorization bearer abc123',
      b'Error: fetch https://signed.example/?sig=123',
      b'AssertionError: SECRET_NOT_SHOWN',
      b'error: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZA',
      b'fatal: git checkout failed',
    ))
    lines=diag.safe_excerpt(content)
    assert 'FAILED tests/test_valid.py::test_one' in lines
    assert 'fatal: git checkout failed' in lines
    assert not any(x in ' '.join(lines) for x in ['abc123','signed.example','SECRET_NOT_SHOWN','ghp_'])
    assert len(lines)<=6


def test_missing_canonical_redactor_returns_no_excerpts(monkeypatch):
    monkeypatch.setitem(sys.modules,'devtool.logstore',None)
    assert diag.safe_excerpt(b'FAILED tests/test_file.py')==[]


@pytest.mark.parametrize('url,allowed',[
    ('https://results-receiver.actions.githubusercontent.com/abc?sig=hidden',True),
    ('https://abc.blob.core.windows.net/p?sig=hidden',True),
    ('http://abc.blob.core.windows.net/p',False),
    ('https://evil.example/abc',False),
    ('https://user:password@abc.blob.core.windows.net/p',False),
    ('https://abc.blob.core.windows.net.evil.example/p',False),
])
def test_signed_log_redirect_origin_restrictions(url,allowed):
    assert diag._signed_host(url) is allowed


class DummyResponse:
    status=200
    def __init__(self,body=b'FAILED tests/test_file.py'):self.body=body
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,size):return self.body[:size]


class DummyOpen:
    def __init__(self,signed='https://abc.blob.core.windows.net/signed?sig=private',body=b'FAILED tests/test_file.py'):
        self.signed=signed; self.body=body; self.requests=[]
    def open(self,request,timeout):
        self.requests.append(request)
        if len(self.requests)==1:
            raise HTTPError(request.full_url,302,'Moved',{'Location':self.signed},io.BytesIO())
        return DummyResponse(self.body)


def test_bounded_log_download_drops_authorization_on_signed_redirect():
    op=DummyOpen(); c=types.SimpleNamespace(_token='secret',_opener=op,timeout=12)
    data=diag.fetch_bounded_job_log(c,SLUG,456)
    assert data==b'FAILED tests/test_file.py'
    assert op.requests[0].get_header('Authorization')=='Bearer secret'
    assert op.requests[1].get_header('Authorization') is None
    assert op.requests[1].full_url.startswith('https://abc.blob.core.windows.net/')


def test_log_rejects_cross_origin_redirect_and_oversized():
    op=DummyOpen(signed='https://evil.example/log')
    c=types.SimpleNamespace(_token='secret',_opener=op,timeout=12)
    assert diag.fetch_bounded_job_log(c,SLUG,456) is None
    assert len(op.requests)==1
    op=DummyOpen(body=b'X'*(256*1024+1))
    c._opener=op
    assert diag.fetch_bounded_job_log(c,SLUG,456) is None


def test_log_fetcher_outage_never_invents_failure_text():
    obj=diag.diagnose_snapshot(snapshot(),slug=SLUG,client=object(),
       log_reader=lambda *_:(_ for _ in ()).throw(OSError('API transport signed URL confidential')))
    assert obj['aggregate']=='fail'
    assert obj['failures'][0]['jobs'][0]['excerpt']==[]
    assert 'signed URL' not in str(obj)


def test_legacy_provider_job_names_redacted_before_persistence():
    legacy=[{'job':'Authorization: Bearer secret-not-visible',
             'steps':[{'name':'error: token=mysecret','conclusion':'failure',
                       'hint':'Inspect login token secret'}],
             'url':f'https://github.com/{SLUG}/actions/runs/123/attempts/2/job/456',
             'job_id':456}]
    clean=diag.sanitize_failed_metadata(legacy,detailed=True)
    assert clean[0]['job']=='[REDACTED PROVIDER LABEL]'
    assert clean[0]['steps'][0]['name']=='[REDACTED PROVIDER LABEL]'
    assert clean[0]['steps'][0]['hint']=='[REDACTED PROVIDER LABEL]'
    assert 'secret-not-visible' not in str(clean)
    assert 'mysecret' not in str(clean)
