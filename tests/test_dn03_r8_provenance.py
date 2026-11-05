"""Offline deterministic R8 tests: no network, GitHub token or .NET required."""
import copy
from hashlib import sha256
from io import BytesIO
import json
import unittest
from unittest.mock import patch
import subprocess

from devtool.actions_dn03_r8_provenance import _github_json
from zipfile import ZipFile, ZIP_DEFLATED

from devtool.actions_dn03_r8_provenance import R8Refused, verify

REPO = 'Mikeyphw/Mike_SVMods'
RUN = 42030323139
SHA = '5dc04f83acce009be8058e1ac5bab3d3aaaf6da2'
GRAPH = 'a' * 64
TREE = 'b' * 40
DEV = '0ebd22c17ac3058704012a1e03ee3265328b0659'


def blob(contents):
    s = BytesIO()
    with ZipFile(s, 'w', compression=ZIP_DEFLATED) as out:
        for name, data in contents.items():
            out.writestr(name, data if isinstance(data, bytes) else data.encode())
    return s.getvalue()


def jd(d):
    return (json.dumps(d, sort_keys=True, indent=2) + '\n').encode()


def fixture():
    records = {}
    nodes = []
    for i in range(1, 7):
        node = f'node-{i:02}'
        ev = f'evidence-{node}'.encode()
        r3 = f'R3-{node}'.encode()
        host = f'host-{node}'.encode()
        records[f'devtool-dn03_r5-native_r6-multi-{node}-evidence'] = blob({
            'node-evidence.json': ev, 'dn03-r3-node-verification.json': r3})
        records[f'dn03-host-{node}-{RUN}-1'] = blob({'host.json':host})
        trx = None
        if i in (3, 6):
            path, count = ('dist/pure/results.trx',21) if i == 3 else ('dist/animal/results.trx',8)
            trx = {'path':path,'passed':count,'sha256':'d'*64,
                   'remote_authenticated':False,'release_qualified':False,
                   'tests':{'format':'trx','total':count,'failed':0,'skipped':0,
                            'required':True,'report_sha256':'d'*64}}
            records[f'devtool-dn03_r5-native_r6-multi-{node}-bytes'] = blob({path:b'<TestRun />'})
        nodes.append({'job_id':node,'native_evidence_sha256':sha256(ev).hexdigest(),
                      'r3_verification_sha256':sha256(r3).hexdigest(),
                      'host_sha256':sha256(host).hexdigest(),'verified_trx':trx})
    receipt = {'kind':'devtool.dn03-r4-cross-node-review','consumer_repository':REPO,
               'consumer_source_sha':SHA,'consumer_tree':TREE,'devtool_sha':DEV,
               'graph_sha256':GRAPH,'run_id':RUN,'run_attempt':1,'node_count':6,
               'native_dag_passed':True,'test_reports_independently_verified':True,
               'all_declared_outputs_verified':True,'verified_test_node_count':2,
               'verified_test_case_count':29,'remote_authenticated':False,
               'release_qualified':False,'nodes':nodes}
    def seal_receipt():
        b=jd(receipt)
        records[f'dn03-r4-acceptance-{RUN}-1']=blob({'acceptance.json':b,
          'source-run.txt':f'{SHA}/{RUN}/1\n',
          'SHA256SUMS': f'{sha256(b).hexdigest()}  ./acceptance.json\n'})
    seal_receipt()
    names = sorted(records)
    api_artifacts = []
    repo_id=1156723196
    for i,name in enumerate(names, start=1000):
        api_artifacts.append({'name':name,'id':i,'expired':False,
                              'digest':'sha256:'+sha256(records[name]).hexdigest(),
                              'workflow_run':{'id':RUN,'repository_id':repo_id,
                                              'head_repository_id':repo_id,'head_sha':SHA,
                                              'head_branch':'main'}})
    jobs = ([{'id':i,'name':f'node-{i:02}','run_id':RUN,'status':'completed',
              'conclusion':'success'} for i in range(1,7)] +
            [{'id':7,'name':'DN03-R7 / source-admitted graph (non-release)',
              'run_id':RUN,'status':'completed','conclusion':'success'},
             {'id':8,'name':'DN03-R7 / verify 29 TRX tests (non-release)',
              'run_id':RUN,'status':'completed','conclusion':'success'}])
    api = {
        f'/repos/{REPO}/actions/runs/{RUN}':{'id':RUN,'head_sha':SHA,'head_branch':'main',
             'event':'push','path':'.github/workflows/devtool-dn03-r7-native-ci.yml',
             'status':'completed','conclusion':'success','run_attempt':1,
             'repository':{'id':repo_id,'full_name':REPO},
             'head_repository':{'id':repo_id,'full_name':REPO}},
        f'/repos/{REPO}/actions/runs/{RUN}/jobs?per_page=100':{
            'total_count':8,'jobs':jobs},
        f'/repos/{REPO}/actions/runs/{RUN}/artifacts?per_page=100':{
            'total_count':15,'artifacts':api_artifacts}
    }
    by_id={a['id']:records[a['name']] for a in api_artifacts}
    return api,by_id,receipt,seal_receipt



class R8ApiRouteTests(unittest.TestCase):
    def test_exact_live_routes_are_admitted(self):
        base=f'/repos/{REPO}/actions/runs/{RUN}'
        allowed=(base, base+'/jobs?per_page=100',
                 base+'/artifacts?per_page=100')
        with patch('devtool.actions_dn03_r8_provenance.subprocess.run',
                   return_value=subprocess.CompletedProcess([],0,b'{}',b'')) as call:
            for route in allowed:
                self.assertEqual(_github_json(route), {})
            self.assertEqual(call.call_count,3)
            self.assertEqual([x.args[0][-1] for x in call.call_args_list],list(allowed))

    def test_unreviewed_routes_and_query_parameters_are_rejected(self):
        base=f'/repos/{REPO}/actions/runs/{RUN}'
        invalid=(
            base+'?per_page=100',
            base+'/jobs', base+'/artifacts',
            base+'/jobs?per_page=101', base+'/jobs?per_page=100&foo=bar',
            base+'/artifacts?per_page=10',
            base+'/jobs?per_page=100#fragment',
            base+'/jobs/3?per_page=100',
            base.replace('/actions/runs/','/actions/workflows/'),
            base+'/%2e%2e/jobs?per_page=100',
            base+'/jobs?per_page=100\nX-Injected: true',
            'https://api.github.com'+base,
        )
        with patch('devtool.actions_dn03_r8_provenance.subprocess.run') as call:
            for route in invalid:
                with self.subTest(route=route):
                    with self.assertRaisesRegex(R8Refused, 'unsafe GitHub API route'):
                        _github_json(route)
            call.assert_not_called()

    def test_live_route_api_failure_remains_fail_closed(self):
        base=f'/repos/{REPO}/actions/runs/{RUN}'
        with patch('devtool.actions_dn03_r8_provenance.subprocess.run',
                   return_value=subprocess.CompletedProcess([],1,b'',b'not authorized')):
            with self.assertRaisesRegex(R8Refused, 'authenticated GitHub API request failed'):
                _github_json(base+'/jobs?per_page=100')


class R8Tests(unittest.TestCase):
    def setUp(self):
        self.api,self.zips,self.receipt,self.seal = fixture()
        self.base=f'/repos/{REPO}/actions/runs/{RUN}'
        self.arts=self.api[self.base+'/artifacts?per_page=100']['artifacts']
        self.jobs=self.api[self.base+'/jobs?per_page=100']['jobs']

    def run_verify(self):
        return verify(REPO,RUN,SHA,api=lambda route:self.api[route],
                      archive=lambda repo,num:self.zips[num])

    def expect_bad(self):
        with self.assertRaises(R8Refused):
            self.run_verify()

    def test_positive(self):
        x=self.run_verify()
        self.assertEqual(x['r7_artifacts_sha256_verified_via_github_api'],15)
        self.assertEqual(x['r7_verified_test_cases'],29)
        self.assertFalse(x['release_qualified'])
        self.assertFalse(x['original_r7_artifacts_individually_sigstore_attested'])

    def test_wrong_run_identity(self):
        self.api[self.base]['head_sha']='0'*40;self.expect_bad()

    def test_wrong_repo(self):
        self.api[self.base]['repository']['id']=999;self.expect_bad()

    def test_failed_r7(self):
        self.api[self.base]['conclusion']='failure';self.expect_bad()

    def test_wrong_workflow(self):
        self.api[self.base]['path']='.github/workflows/ci.yml';self.expect_bad()

    def test_incomplete_jobs(self):
        self.jobs.pop();self.expect_bad()

    def test_failed_job(self):
        self.jobs[3]['conclusion']='failure';self.expect_bad()

    def test_skipped_job(self):
        self.jobs[3]['conclusion']='skipped';self.expect_bad()

    def test_truncated_artifact_listing(self):
        self.arts.pop();self.expect_bad()

    def test_cross_run_artifact(self):
        self.arts[0]['workflow_run']['id']=RUN-1;self.expect_bad()

    def test_tampered_zip(self):
        self.zips[self.arts[0]['id']]+=b'bad';self.expect_bad()

    def test_bad_archive_digest(self):
        self.arts[0]['digest']='sha256:'+'0'*64;self.expect_bad()

    def test_missing_r4_report(self):
        name=f'dn03-r4-acceptance-{RUN}-1'
        a=next(a for a in self.arts if a['name']==name)
        self.zips[a['id']]=blob({'source-run.txt':f'{SHA}/{RUN}/1',
                                  'SHA256SUMS':'none'})
        a['digest']='sha256:'+sha256(self.zips[a['id']]).hexdigest()
        self.expect_bad()

    def test_wrong_r4_count_with_consistent_zip_metadata(self):
        self.receipt['verified_test_case_count']=28
        self._reseal()
        self.expect_bad()

    def test_wrong_r4_source_with_consistent_zip_metadata(self):
        self.receipt['consumer_source_sha']='c'*40
        self._reseal()
        self.expect_bad()

    def test_forged_release(self):
        self.receipt['release_qualified']=True
        self._reseal()
        self.expect_bad()

    def test_r3_digest_tamper_with_updated_github_sha(self):
        name=f'devtool-dn03_r5-native_r6-multi-node-03-evidence'
        a=next(a for a in self.arts if a['name']==name)
        self.zips[a['id']]=blob({'node-evidence.json':b'evidence-node-03',
                                 'dn03-r3-node-verification.json':b'changed'})
        a['digest']='sha256:'+sha256(self.zips[a['id']]).hexdigest()
        self.expect_bad()

    def test_forged_remote_auth(self):
        self.receipt['remote_authenticated']=True
        self._reseal()
        self.expect_bad()

    def test_r4_wrong_trx_count(self):
        self.receipt['nodes'][2]['verified_trx']['passed']=22
        self._reseal()
        self.expect_bad()

    def _reseal(self):
        self.seal()
        data=blob({'acceptance.json':jd(self.receipt),
          'source-run.txt':f'{SHA}/{RUN}/1\n',
          'SHA256SUMS':f'{sha256(jd(self.receipt)).hexdigest()}  ./acceptance.json\n'})
        a=next(a for a in self.arts if a['name']==f'dn03-r4-acceptance-{RUN}-1')
        self.zips[a['id']]=data
        a['digest']='sha256:'+sha256(data).hexdigest()

if __name__=='__main__':
    unittest.main()
