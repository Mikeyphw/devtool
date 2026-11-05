"""Offline positive and fail-closed tests for the portable CI evidence engine."""
import importlib.util
from pathlib import Path
import json
import tempfile
import unittest

MOD = Path(__file__).resolve().parents[1] / 'devtool' / 'actions_ci_qualification.py'
spec = importlib.util.spec_from_file_location('qualified', MOD)
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)
SHA = 'a' * 40
DEV = 'b' * 40
PROJECTS = {'pure': ('Tests/RoosterControl.PureTests/RoosterControl.PureTests.csproj', 21),
            'animal': ('Tests/EggsInWild.AnimalNotes.Tests/EggsInWild.AnimalNotes.Tests.csproj', 8)}
HOSTS = ('ubuntu-24.04', 'windows-2022')


def trx(count=21, outcomes=None):
    outcomes = outcomes or ['Passed'] * count
    passed = sum(x == 'Passed' for x in outcomes)
    nodes = ''.join(f'<UnitTestResult testId="id-{i}" executionId="exe-{i}" outcome="{v}"/>'
                    for i,v in enumerate(outcomes))
    return (f'<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">'
            f'<Results>{nodes}</Results><ResultSummary outcome="Completed"><Counters '
            f'total="{count}" executed="{count}" passed="{passed}" failed="{count-passed}" '
            f'notExecuted="0"/></ResultSummary></TestRun>').encode()


def coverage(rate='0.5', hit=5, valid=10):
    return (f'<coverage line-rate="{rate}" lines-covered="{hit}" lines-valid="{valid}">'
            '<packages><package><classes><class name="A"/></classes></package></packages>'
            '</coverage>').encode()


def receipt(profile, test_id, count):
    return {
        'schema_version':1, 'kind':'devtool.ci-portable-test-receipt',
        'source_sha':SHA,'devtool_sha':DEV,'run_id':456,'run_attempt':1,
        'host_profile':profile,'test_id':test_id,'project':PROJECTS[test_id][0],
        'sdk_version':'10.0.100',
        'tests':{'total':count,'passed':count,'failed':0,'skipped':0,
                 'report_sha256':'c'*64,'report_format':'trx'},
        'coverage':{'collected':False,'status':'NOT_COLLECTED'},
        'remote_authenticated':False,'sigstore_attested':False,
        'device_qualified':False,'release_qualified':False
    }


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.xml=self.base/'results.trx'
        self.xml.write_bytes(trx())
        self.folder=self.base/'reports'
        self.folder.mkdir()
        for p in HOSTS:
            for t,(project,count) in PROJECTS.items():
                (self.folder/f'{p}-{t}.json').write_text(json.dumps(receipt(p,t,count)))

    def aggregate(self, **extra):
        return q.aggregate(root=self.folder,source=SHA,devtool_sha=DEV,run_id=456,attempt=1,
                           profiles=HOSTS,tests=PROJECTS,**extra)

    def write(self, profile='ubuntu-24.04', t='pure', change=None):
        f=self.folder/f'{profile}-{t}.json'
        d=json.loads(f.read_text())
        if change: change(d)
        f.write_text(json.dumps(d))

    def denied(self, fn):
        with self.assertRaises(q.QualificationRefused):
            fn()

    def test_valid_trx(self):
        self.assertEqual(q.trx_report(trx(),21)['passed'],21)

    def test_nonpassing_trx(self):
        bad=['Passed']*20+['Failed']
        self.denied(lambda:q.trx_report(trx(outcomes=bad),21))

    def test_wrong_counter(self):
        self.denied(lambda:q.trx_report(trx().replace(b'passed="21"',b'passed="20"'),21))

    def test_duplicate_execution(self):
        self.denied(lambda:q.trx_report(trx().replace(b'exe-20',b'exe-19'),21))

    def test_missing_execution(self):
        self.denied(lambda:q.trx_report(trx().replace(b'executionId="exe-0" ',b''),21))

    def test_wrong_expected_count(self):
        self.denied(lambda:q.trx_report(trx(),20))

    def test_non_xml(self):
        self.denied(lambda:q.trx_report(b'<notxml />',21))

    def test_external_entity(self):
        self.denied(lambda:q.trx_report(b'<!DOCTYPE X [ <!ENTITY x SYSTEM "file:///etc/passwd">]>'+trx(),21))

    def test_valid_coverage(self):
        self.assertEqual(q.cobertura_report(coverage())['line_rate'],0.5)

    def test_coverage_threshold(self):
        self.denied(lambda:q.cobertura_report(coverage(),0.6))

    def test_coverage_inconsistent(self):
        self.denied(lambda:q.cobertura_report(coverage('0.9')))

    def test_coverage_no_classes(self):
        self.denied(lambda:q.cobertura_report(b'<coverage line-rate="0.5" lines-covered="1" lines-valid="2"/>'))

    def test_coverage_doctype(self):
        self.denied(lambda:q.cobertura_report(b'<!DOCTYPE foo>'+coverage()))

    def test_review(self):
        r=q.review(source=SHA,devtool_sha=DEV,run_id=456,attempt=1,
                   profile='windows-2022',project=PROJECTS['pure'][0],test_id='pure',
                   trx=self.xml,expected_tests=21,sdk='10.0.100')
        self.assertFalse(r['coverage']['collected'])
        self.assertFalse(r['release_qualified'])

    def test_missing_required_coverage(self):
        self.denied(lambda:q.review(source=SHA,devtool_sha=DEV,run_id=456,attempt=1,
                                    profile='windows-2022',project=PROJECTS['pure'][0],test_id='pure',
                                    trx=self.xml,expected_tests=21,sdk='10.0.100',
                                    require_coverage=True))

    def test_wrong_sha(self):
        self.denied(lambda:q.review(source='bad',devtool_sha=DEV,run_id=456,attempt=1,
                                    profile='windows-2022',project=PROJECTS['pure'][0],test_id='pure',
                                    trx=self.xml,expected_tests=21,sdk='10.0.100'))

    def test_trx_symlink(self):
        self.xml.unlink()
        self.xml.symlink_to(self.base/'target.trx')
        self.denied(lambda:q.checked_file(self.xml))

    def test_positive_matrix(self):
        a=self.aggregate()
        self.assertEqual(a['test_executions_verified'],58)
        self.assertEqual(a['matrix_entries_verified'],4)
        self.assertFalse(a['coverage_all_collected'])

    def test_missing_matrix(self):
        (self.folder/'ubuntu-24.04-pure.json').unlink()
        self.denied(self.aggregate)

    def test_extra_matrix(self):
        (self.folder/'extra.txt').write_text('bad')
        self.denied(self.aggregate)

    def test_cross_source(self):
        self.write(change=lambda d:d.__setitem__('source_sha','f'*40))
        self.denied(self.aggregate)

    def test_cross_run(self):
        self.write(change=lambda d:d.__setitem__('run_id',999))
        self.denied(self.aggregate)

    def test_cross_attempt(self):
        self.write(change=lambda d:d.__setitem__('run_attempt',999))
        self.denied(self.aggregate)

    def test_wrong_host(self):
        self.write(change=lambda d:d.__setitem__('host_profile','another-host'))
        self.denied(self.aggregate)

    def test_wrong_project(self):
        self.write(change=lambda d:d.__setitem__('project','another.csproj'))
        self.denied(self.aggregate)

    def test_wrong_test_count(self):
        self.write(change=lambda d:d['tests'].__setitem__('passed',20))
        self.denied(self.aggregate)

    def test_trx_hash_unavailable(self):
        self.write(change=lambda d:d['tests'].__setitem__('report_sha256','bad'))
        self.denied(self.aggregate)

    def test_forged_release(self):
        self.write(change=lambda d:d.__setitem__('release_qualified',True))
        self.denied(self.aggregate)

    def test_forged_remote(self):
        self.write(change=lambda d:d.__setitem__('remote_authenticated',True))
        self.denied(self.aggregate)

    def test_forged_sigstore(self):
        self.write(change=lambda d:d.__setitem__('sigstore_attested',True))
        self.denied(self.aggregate)

    def test_coverage_omission_without_status(self):
        self.write(change=lambda d:d['coverage'].__setitem__('status','PASS'))
        self.denied(self.aggregate)

    def test_required_coverage_gate(self):
        self.denied(lambda:self.aggregate(require_coverage=True))

    def test_output_exclusive(self):
        f=self.base/'existing.json'
        f.write_text('keep')
        self.denied(lambda:q.write_new(f,{'key':'new'}))
        self.assertEqual(f.read_text(),'keep')


if __name__ == '__main__':
    unittest.main()
