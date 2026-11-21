"""Current CI session-bound pagination, no historical/phase gate chains."""
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from devtool.delivery_ci_registrar import collect_runs, registration_window, DeliverySessionError
from devtool.github_actions import GitHubActionsClient

T = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)


def window():
    return {'began':T, 'deadline':T+timedelta(minutes=30),
            'now':T+timedelta(minutes=5)}


def record(index, *, at=None, event='pull_request'):
    return {'id':index,'name':'CI','workflow_id':42,'event':event,
            'created_at':(at or T).isoformat().replace('+00:00','Z')}


def cli(responses):
    client=GitHubActionsClient()
    calls=[]
    def get(path):
        calls.append(path)
        query=parse_qs(urlsplit(path).query)
        assert query['event']==['pull_request']
        assert query['per_page']==['100']
        assert 'created' in query
        return responses[int(query['page'][0])-1]
    client._get=get
    return client,calls


def test_busy_repo_530_global_runs_is_not_incomplete_when_scoped_is_exhaustive():
    c,paths=cli([{'total_count':2,'workflow_runs':[record(1),record(2)]}])
    found=collect_runs(c,'Mikeyphw/devtool',window=window(),event='pull_request')
    assert [item['id'] for item in found]==[1,2]
    assert len(paths)==1
    assert 'created=' in paths[0]


def test_two_exhaustive_scoped_pages():
    rows=[record(n) for n in range(1,102)]
    c,paths=cli([{'total_count':101,'workflow_runs':rows[:100]},
                 {'total_count':101,'workflow_runs':rows[100:]}])
    assert len(collect_runs(c,'Mikeyphw/devtool',window=window(),event='pull_request'))==101
    assert len(paths)==2


@pytest.mark.parametrize('responses,reason',[
    ([{'total_count':500,'workflow_runs':[]}],'pagination totals'),
    ([{'total_count':2,'workflow_runs':[record(1)]}],'ended before'),
    ([{'total_count':2,'workflow_runs':[record(1),record(1)]}],'duplicate'),
    ([{'total_count':1,'workflow_runs':[record(1,event='push')]}],'event filter'),
    ([{'total_count':1,'workflow_runs':[record(1,at=T-timedelta(hours=3))]}],'created filter'),
])
def test_bad_scoped_responses_refuse(responses,reason):
    c,_=cli(responses)
    with pytest.raises(DeliverySessionError,match=reason):
        collect_runs(c,'Mikeyphw/devtool',window=window(),event='pull_request')


def test_changed_total_count_across_pages_refuses():
    rows=[record(n) for n in range(1,102)]
    c,_=cli([{'total_count':101,'workflow_runs':rows[:100]},
             {'total_count':102,'workflow_runs':rows[100:]}])
    with pytest.raises(DeliverySessionError,match='inconsistent'):
        collect_runs(c,'Mikeyphw/devtool',window=window(),event='pull_request')


def test_original_unscoped_truncation_remains_fail_closed():
    class OldClient:
        def _paged(self,*args,**kwargs):
            return [record(n) for n in range(500)]
    with pytest.raises(DeliverySessionError,match='pagination'):
        collect_runs(OldClient(),'Mikeyphw/devtool')


def test_pr_anchor_scoped_queries_are_bounded_and_deduplicated():
    created=T+timedelta(hours=5)
    updated=created+timedelta(hours=3)
    win=registration_window({'created_at':T.isoformat()}, 1800,
        now=updated+timedelta(minutes=5),
        verified_pr_times=(created.isoformat(),updated.isoformat()))
    assert len(win['ranges'])==3
    query_windows=[]
    c=GitHubActionsClient()
    def get(path):
        q=parse_qs(urlsplit(path).query)
        time_range=q['created'][0]
        query_windows.append(time_range)
        if time_range.startswith('2026-11-16T14:58'):
            return {'total_count':1, 'workflow_runs':[record(11,at=created+timedelta(minutes=1))]}
        if time_range.startswith('2026-11-16T17:58'):
            return {'total_count':1, 'workflow_runs':[record(22,at=updated+timedelta(minutes=1))]}
        return {'total_count':1, 'workflow_runs':[record(33)]}
    c._get=get
    found=collect_runs(c,'Mikeyphw/devtool',window=win,event='pull_request')
    assert {x['id'] for x in found}=={11,22,33}
    assert len(query_windows)==3


def test_overlapping_window_retrieval_never_duplicates_run_identity():
    created=T+timedelta(minutes=1)
    win=registration_window({'created_at':T.isoformat()},1800,
        now=T+timedelta(minutes=10),verified_pr_times=(created.isoformat(),created.isoformat()))
    c=GitHubActionsClient()
    c._get=lambda _: {'total_count':1,'workflow_runs':[record(31,at=T+timedelta(minutes=3))]}
    found=collect_runs(c,'Mikeyphw/devtool',window=win,event='pull_request')
    assert [x['id'] for x in found]==[31]
