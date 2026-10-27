"""FC08-HOST-R1 active policy tests: exact source, negative truth and no promotion."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from unittest import TestCase, mock

# Do not import through the unqualified ``tools`` package: on native Termux,
# Hermes Agent installs a distinct ``tools`` package that shadows this repo's
# implicit namespace directory during pytest collection. Use an absolute,
# repository-owned sibling-module search path, also when ``tools`` is cached.
_LOCAL_TOOLS = Path(__file__).resolve().parents[1] / 'tools'
if str(_LOCAL_TOOLS) not in sys.path:
    sys.path.insert(0, str(_LOCAL_TOOLS))
import fc08_host_workloads as h
import fc08_host_summary as s
import fc08_host_capture as c


def identity():
    return {'repository':'Mikeyphw/devtool','sha':'a'*40,'tree':'b'*40,'run_id':7,'attempt':1,'ref':'refs/heads/feature/rmp01-security-remediation'}


def record(lane):
    checks={name:{'pass':True} for name in h.EXPECTED_CHECKS[lane]}
    return {'schema':'devtool.fc08.host-real-workload.v1','lane':lane,'identity':identity(),
        'checks':checks,'checks_sha256':h.sha_bytes(json.dumps(checks,sort_keys=True,separators=(',',':')).encode()),
        'status':'WORKLOAD_COMPLETED_NEEDS_INDEPENDENT_REVIEW','release_qualified':False,'device_qualified':False}


class Contract(TestCase):
    def test_modules_are_repo_local_even_if_foreign_tools_is_imported(self):
        # Herm(es) and other installed packages can own top-level ``tools``.
        # This suite must never resolve source tests to that unrelated package.
        for module, filename in ((h, 'fc08_host_workloads.py'),
                                 (s, 'fc08_host_summary.py'),
                                 (c, 'fc08_host_capture.py')):
            self.assertEqual(Path(module.__file__).resolve(), (_LOCAL_TOOLS / filename).resolve())

    def test_all_four_required_lanes_have_explicit_checks(self):
        self.assertEqual(len(h.LANES),4)
        for lane in h.LANES:
            self.assertGreaterEqual(len(h.EXPECTED_CHECKS[lane]),3)
            h.validate_receipt(record(lane),lane,identity())

    def test_missing_or_foreign_check_fails_closed(self):
        r=record('linux-polyglot');r['checks'].pop('meson')
        with self.assertRaisesRegex(ValueError,'MISSING'):h.validate_receipt(r,'linux-polyglot',identity())
        r=record('linux-polyglot');r['checks']['fake']={'pass':True}
        with self.assertRaisesRegex(ValueError,'MISSING'):h.validate_receipt(r,'linux-polyglot',identity())

    def test_fake_release_or_source_is_refused(self):
        r=record('windows-dotnet-msvc');r['release_qualified']=True
        with self.assertRaisesRegex(ValueError,'FALSE'):h.validate_receipt(r,'windows-dotnet-msvc',identity())
        r=record('macos-native');r['identity']['tree']='c'*40
        with self.assertRaisesRegex(ValueError,'SOURCE'):h.validate_receipt(r,'macos-native',identity())

    def test_digest_tamper_refused(self):
        r=record('macos-native');r['checks']['native-cmake']['pass']=False
        with self.assertRaisesRegex(ValueError,'DIGEST'):h.validate_receipt(r,'macos-native',identity())

    def test_host_gates_must_not_assume_release(self):
        r=record('github-actions-dag');r['device_qualified']=True
        with self.assertRaisesRegex(ValueError,'FALSE'):h.validate_receipt(r,'github-actions-dag',identity())

    def test_parent_dag_failures_rejected_even_with_good_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            env={'FC08_NEEDS_JSON':json.dumps({'dag':{'result':'failure'},'linux':{'result':'success'},'windows':{'result':'success'},'macos':{'result':'success'}})}
            with mock.patch.object(s,'identity',return_value=identity()):
                with self.assertRaisesRegex(ValueError,'GITHUB_DAG'):s.summarize(Path(d),env)

    def test_all_four_raw_receipts_required_and_source_bound(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for lane in h.LANES:
                folder=root/('fc08-real-'+lane+'-attempt1');folder.mkdir()
                (folder/(lane+'.json')).write_text(json.dumps(record(lane)))
            env={'FC08_NEEDS_JSON':json.dumps({x:{'result':'success'} for x in ('dag','linux','windows','macos')})}
            with mock.patch.object(s,'identity',return_value=identity()):
                receipt=s.summarize(root,env)
                self.assertFalse(receipt['release_qualified'])
                self.assertFalse(receipt['fc08_overall_qualified'])
                (root/'fc08-real-macos-native-attempt1'/'macos-native.json').unlink()
                with self.assertRaisesRegex(ValueError,'MISSING'):s.summarize(root,env)

    def test_source_guard_refuses_drift_dirty_and_wrong_identity(self):
        env={'GITHUB_SHA':'a'*40,'GITHUB_REPOSITORY':'Mikeyphw/devtool','GITHUB_REF':'refs/heads/feature/rmp01-security-remediation','GITHUB_RUN_ID':'7','GITHUB_RUN_ATTEMPT':'1'}
        def git(_, *args):
            if args[-1]=='HEAD':return 'a'*40
            if args[-1]=='HEAD^{tree}':return 'b'*40
            return ''
        with mock.patch.object(h,'git',side_effect=git):
            self.assertEqual(h.identity(env)['sha'],'a'*40)
            with self.assertRaisesRegex(RuntimeError,'SOURCE_SHA'):h.identity({**env,'GITHUB_SHA':'c'*40})
            with self.assertRaisesRegex(RuntimeError,'REPOSITORY'):h.identity({**env,'GITHUB_REPOSITORY':'evil/repo'})
            with self.assertRaisesRegex(RuntimeError,'RUN_IDENTITY'):h.identity({**env,'GITHUB_RUN_ATTEMPT':'0'})
        with mock.patch.object(h,'git',side_effect=lambda _,*a:'?? source.py' if a[0]=='status' else ('a'*40 if a[-1]=='HEAD' else 'b'*40)):
            with self.assertRaisesRegex(RuntimeError,'DIRTY'):h.identity(env)

    def test_workflow_real_execution_and_fail_closed_summary(self):
        y=(Path(__file__).parents[1]/'.github/workflows/devtool-fc08-host-workloads.yml').read_text()
        self.assertIn('workflow_dispatch:',y)
        self.assertIn('contents: read',y)
        self.assertNotIn('contents: write',y)
        self.assertIn('needs: [dag, linux, windows, macos]',y)
        for lane in h.LANES:self.assertIn('--lane '+lane,y)
        self.assertIn('if-no-files-found: error',y)
        self.assertIn('python tools/fc08_host_summary.py --input fc08-raw',y)
        self.assertIn('if: always()',y)

    def test_scoped_evidence_dirs_do_not_disable_tracked_guards(self):
        env={'GITHUB_SHA':'a'*40,'GITHUB_REPOSITORY':'Mikeyphw/devtool','GITHUB_REF':'refs/heads/feature/rmp01-security-remediation','GITHUB_RUN_ID':'7','GITHUB_RUN_ATTEMPT':'1'}
        def git(_, *a):
            if a[-1]=='HEAD':return 'a'*40
            if a[-1]=='HEAD^{tree}':return 'b'*40
            return '?? fc08-raw/file.json\n?? out/report.json'
        with mock.patch.object(h,'git',side_effect=git):
            self.assertEqual(h.identity(env,allow_evidence_dirs=True)['tree'],'b'*40)
            with self.assertRaisesRegex(RuntimeError,'DIRTY'):h.identity(env)
        def tracked(_, *a):
            if a[-1]=='HEAD':return 'a'*40
            if a[-1]=='HEAD^{tree}':return 'b'*40
            return ' M devtool/cli.py\n?? fc08-raw/a.json'
        with mock.patch.object(h,'git',side_effect=tracked):
            with self.assertRaisesRegex(RuntimeError,'DIRTY'):h.identity(env,allow_evidence_dirs=True)


class CaptureContract(TestCase):
    def test_exact_five_job_and_artifact_cells(self):
        jobs=[{'name':n,'conclusion':'success'} for n in (
            'FC08 real github-actions-dag','FC08 real linux-polyglot',
            'FC08 real windows-dotnet-msvc','FC08 real macos-native',
            'FC08 fail-closed four-lane evidence inventory')]
        arts=[{'name':'fc08-real-'+l+'-attempt1','expired':False,'digest':'sha256:'+'a'*64} for l in h.LANES]
        arts.append({'name':'fc08-real-host-summary-attempt1','expired':False,'digest':'sha256:'+'a'*64})
        run={'head_sha':'a'*40,'run_attempt':1,'status':'completed','conclusion':'success','head_branch':c.BRANCH,'path':c.WF}
        self.assertEqual(len(c.validate_host(run,jobs,arts,'a'*40)),5)
        with self.assertRaisesRegex(ValueError,'ARTIFACT'):c.validate_host(run,jobs,arts[:-1],'a'*40)
        with self.assertRaisesRegex(ValueError,'SOURCE'):c.validate_host({**run,'head_sha':'b'*40},jobs,arts,'a'*40)
        with self.assertRaisesRegex(ValueError,'JOBS'):c.validate_host(run,jobs[:-1],arts,'a'*40)

    def test_registration_is_explicitly_source_pinned(self):
        txt=(Path(__file__).parents[1]/'tools/fc08_host_capture.py').read_text()
        self.assertIn('EXPECTED_MAIN=',txt)
        self.assertIn('if not a.register_default:',txt)
        self.assertIn('REFUSED_MAIN_FEATURE_WORKFLOW_BYTES_DIFFER',txt)
        self.assertNotIn('git push --force',txt)
