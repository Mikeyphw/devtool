"""Actual GET transport and native CI journal regressions; no live mutations."""
import io
import json
from collections import Counter, deque
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import pytest

from devtool import delivery_github as observer
from devtool.delivery_provider import request_json
from devtool.delivery_session import DeliverySessionError, status
from devtool.github_actions import (
    GitHubActionsClient, GitHubActionsError, GitHubDispatchOutcomeUnknown,
    GitHubGatewayReadUnavailable,
)
from test_delivery_ci_registrar import ctx, row


class Response:
    def __init__(self, payload, status=200):
        self.status = status
        self.headers = {}
        self.stream = io.BytesIO(json.dumps(payload).encode())
    def __enter__(self): return self
    def __exit__(self, *args): self.stream.close()
    def read(self, n): return self.stream.read(n)


def error(status):
    return HTTPError('https://api.github.com/fixture', status, 'fixture-secret', {},
                     io.BytesIO(b'fixture-secret'))


class Queue:
    def __init__(self, replies):
        self.replies = deque(replies)
        self.calls = []
    def open(self, req, timeout):
        self.calls.append((req.get_method(), req.full_url, req.data, timeout))
        value = self.replies.popleft()
        if isinstance(value, BaseException): raise value
        return value


@pytest.fixture(autouse=True)
def delays(monkeypatch):
    result = []
    monkeypatch.setattr('devtool.github_actions.time.sleep', result.append)
    return result


@pytest.mark.parametrize('code', [502, 503, 504])
def test_get_retries_exact_request_and_stops_on_success(code, delays):
    q = Queue([error(code), Response({'id': 7})])
    c = GitHubActionsClient('token', opener=q)
    assert c.get_run('owner/repo', 7) == {'id': 7}
    assert q.calls[0] == q.calls[1]
    assert q.calls[0][0] == 'GET' and delays == [.25]


@pytest.mark.parametrize('code', [502, 503, 504])
def test_exhaustion_is_bounded_and_sanitized(code, delays):
    q = Queue([error(code), error(code), error(code), Response({'id':7})])
    with pytest.raises(GitHubGatewayReadUnavailable) as e:
        GitHubActionsClient('token', opener=q).get_run('owner/repo', 7)
    assert e.value.status == code and e.value.attempts == 3
    assert 'fixture-secret' not in str(e.value)
    assert len(q.calls) == 3 and len(set(q.calls)) == 1 and delays == [.25, .5]


@pytest.mark.parametrize('code', [401, 403, 404, 422, 429, 500])
def test_non_gateway_errors_do_not_gain_retry(code, delays):
    q = Queue([error(code), Response({'id':7})])
    with pytest.raises(GitHubActionsError):
        GitHubActionsClient(opener=q).get_run('owner/repo', 7)
    assert len(q.calls) == 1 and not delays


def test_status_response_and_mixed_gateway_retry(delays):
    q = Queue([Response({}, 502), error(504), Response({'id':7})])
    assert GitHubActionsClient(opener=q).get_run('owner/repo', 7)['id'] == 7
    assert len(q.calls) == 3 and delays == [.25, .5]


@pytest.mark.parametrize('code', [502, 503, 504])
def test_writes_never_replayed_even_on_gateway(code, delays):
    q = Queue([error(code), Response({},201)])
    c = GitHubActionsClient('token', opener=q)
    with pytest.raises(GitHubDispatchOutcomeUnknown):
        c.dispatch('owner/repo', 'ci.yml', 'main', {})
    assert len(q.calls) == 1 and q.calls[0][0] == 'POST' and not delays
    q = Queue([error(code), Response({},201)])
    with pytest.raises(DeliverySessionError,match='outcome uncertain'):
        request_json(GitHubActionsClient('token',opener=q), 'POST', '/repos/owner/repo/pulls', {})
    assert len(q.calls) == 1 and q.calls[0][0] == 'POST' and not delays


def test_shared_rules_transport_does_not_restart_retry_budget(delays):
    q = Queue([URLError('temporary'), error(502), error(503), Response([])])
    with pytest.raises(DeliverySessionError, match='provider_unavailable'):
        request_json(GitHubActionsClient(opener=q), 'GET', '/repos/owner/repo/rules/branches/main')
    assert len(q.calls) == 3 and len(set(q.calls)) == 1 and delays == [.1, .5]


class Provider:
    """HTTP fixture beneath the real GitHubActionsClient, not a fake CI verdict."""
    def __init__(self, item):
        self.item = item
        self.failures = {}
        self.calls = Counter()
        self.list_poll = 0
        self.empty_first = False
        self.head = item['head_sha']
        self.failure_start = {}
    def open(self, req, timeout):
        assert req.get_method() == 'GET' and req.data is None
        url = urlsplit(req.full_url)
        assert url.netloc == 'api.github.com'
        p = url.path.removeprefix('/repos/' + self.item['repository'])
        key = ('pages' if p == '/actions/runs' else 'workflow' if p == '/actions/workflows/77'
               else 'exact-run' if p == '/actions/runs/101' else 'jobs' if p.endswith('/attempts/1/jobs')
               else 'rules' if p == '/rules/branches/main' else 'base' if p == '/branches/main'
               else 'pr' if p == '/pulls/17' else 'merge' if p.endswith('/merge') else 'head')
        self.calls[key] += 1
        failures = self.failures.get(key)
        if failures and self.calls[key] >= self.failure_start.get(key,1):
            raise error(failures.popleft())
        if key == 'workflow': return Response({'id':77,'name':'Required Test Workflow','path':'.github/workflows/test.yml','state':'active'})
        if key == 'pages':
            self.list_poll += 1
            rows = [] if self.empty_first and self.list_poll == 1 else [row(self.item)]
            return Response({'total_count':len(rows),'workflow_runs':rows})
        if key == 'exact-run': return Response(row(self.item))
        if key == 'jobs': return Response({'jobs':[{'id':401,'run_id':101,'run_attempt':1,'name':'tests','status':'completed','conclusion':'success'}]})
        if key == 'rules': return Response([])
        if key == 'base': return Response({'protected':False})
        if key == 'pr':
            return Response({'number':17,'state':'open',
                'head':{'sha':self.item['head_sha'],'ref':self.item['head'],
                        'label':self.item['repository'].split('/')[0]+':'+self.item['head'],
                        'repo':{'full_name':self.item['repository']}},
                'base':{'ref':self.item['base'],'repo':{'full_name':self.item['repository']}}})
        if key == 'merge': return Response({'object':{'sha':'a'*40}})
        assert p.startswith('/branches/')
        return Response({'commit':{'sha':self.head}})


def observe(ctx, provider, checks=1):
    item, state, _ = ctx
    return observer.observe_ci(item['id'], client=GitHubActionsClient('token',opener=provider),
         state_root=state, required=('Required Test Workflow',),
         workflow_ids={'Required Test Workflow':77}, required_event='pull_request',
         policy_sha256='b'*64, strict_registration=True, checks=checks, interval=1,
         sleep=lambda _:None)


@pytest.mark.parametrize('endpoint', ['workflow','head','pr','merge','pages','exact-run','jobs','base','rules'])
def test_real_native_observer_recovers_gateway_on_every_read(ctx,endpoint):
    provider = Provider(ctx[0]); provider.failures[endpoint] = deque([502,504])
    result = observe(ctx,provider)
    assert result['state'] == 'REQUIRED_CHECKS_PASSED' and result['exact_run_evidence']['verified']
    assert provider.calls[endpoint] >= 3
    assert status(ctx[0]['id'],state_root=ctx[1])['source_binding'] == 'matching'


@pytest.mark.parametrize('endpoint,phase,start', [('head','source-provenance',2),('pages','workflow-run-pages',2),('merge','source-provenance',2)])
def test_exhausted_poll_retains_prior_history_and_recovers_same_session(ctx,endpoint,phase,start):
    item,state,_ = ctx; provider = Provider(item); provider.empty_first = True
    provider.failures[endpoint] = deque([502,503,504]); provider.failure_start[endpoint] = start
    first = observe(ctx,provider,checks=2)
    assert first['state'] == 'REGISTRATION_PROVIDER_UNAVAILABLE'
    assert first['registration']['phase'] == phase
    assert first['registration']['gateway_http_status'] == 504 and first['registration']['read_attempts'] == 3
    assert [x['state'] for x in first['history']] == ['WAITING_REGISTRATION','REGISTRATION_PROVIDER_UNAVAILABLE']
    before = json.loads((state/(item['id']+'.json')).read_text())
    assert before['events'][-1]['gateway_http_status'] == 504
    result = observe(ctx,provider)
    after = json.loads((state/(item['id']+'.json')).read_text())
    assert result['checks_qualified'] and after['id'] == before['id'] == item['id']
    assert after['intents'] == before['intents'] and after['events'][:-1] == before['events']
    assert after['events'][-1]['state'] == 'REQUIRED_CHECKS_PASSED'
    assert 'fixture-secret' not in json.dumps(after)


def test_exhausted_poll_continues_within_existing_budget(ctx):
    provider = Provider(ctx[0]); provider.failures['pages'] = deque([503]*3)
    result = observe(ctx,provider,checks=2)
    assert [x['state'] for x in result['history']] == ['REGISTRATION_PROVIDER_UNAVAILABLE','REQUIRED_CHECKS_PASSED']
    assert result['checks_qualified']


def test_final_source_recheck_cannot_keep_stale_green_on_gateway(ctx):
    provider = Provider(ctx[0]); provider.failures['head'] = deque([503]*3); provider.failure_start['head'] = 2
    result = observe(ctx,provider)
    assert not result['checks_qualified'] and result['registration']['phase'] == 'final-source-provenance'
    assert status(ctx[0]['id'],state_root=ctx[1])['ci']['state'] == 'REGISTRATION_PROVIDER_UNAVAILABLE'
    provider.head = 'f'*40
    with pytest.raises(DeliverySessionError,match='Remote branch moved'):
        observe(ctx,provider)
