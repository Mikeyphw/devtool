"""SFDT08 adversarial, source-only JUnit/typed native evidence tests; no Gradle."""
from __future__ import annotations
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from devtool.actions_junit_evidence import (JUnitEvidenceError,
    reviewed_junit_directory, require_fresh_junit_directory, scan_junit_bundle)
from devtool.actions_node_evidence import build_node_evidence, verify_node_evidence, NodeEvidenceError, _digest


SRC = 'a'*40
TREE = 'b'*40
DIR = 'app-tv/build/test-results/testDebugUnitTest'


def row():
    node={'id':'node-01','node':'demo.ci.tv','job':'tv','provider':'android-gradle',
          'profile_id':'ubuntu-24.04-x64','outputs':[],'junit_report_dir':DIR}
    graph={'jobs':[node],'graph_sha256':'c'*64,'consumer_tree':TREE,
           'target':'demo','workflow':'ci'}
    return graph,node


class TestJUnitNativeEvidence(unittest.TestCase):
    def setUp(self):
        self.d=tempfile.TemporaryDirectory(prefix='sfdt08-junit-')
        self.root=Path(self.d.name)
        self.directory=self.root/DIR
    def tearDown(self):
        self.d.cleanup()
    def xml(self, *, failed=False, skipped=False, mismatch=False):
        self.directory.mkdir(parents=True,exist_ok=True)
        body=('<failure message="failure"/>' if failed else '<skipped/>' if skipped else '')
        n=9 if mismatch else 1
        (self.directory/'TEST-Sample.xml').write_text(f'<testsuite tests="{n}" failures="{int(failed)}" skipped="{int(skipped)}">'
                     f'<testcase name="one" classname="Sample">{body}</testcase></testsuite>')
    def test_missing_cannot_pass(self):
        with self.assertRaisesRegex(JUnitEvidenceError,'missing'):
            scan_junit_bundle(self.root,DIR)
    def test_freshness_rejects_existing_dir(self):
        require_fresh_junit_directory(self.root,DIR)
        self.xml()
        with self.assertRaisesRegex(JUnitEvidenceError,'pre-existing'):
            require_fresh_junit_directory(self.root,DIR)
    def test_traversal_absolute_and_unbounded(self):
        for value in ('../build/test-results/x','/app/build/test-results/x','foo/../build/test-results/x',
                      'a\\b/build/test-results/x','some/bad/report','x//build/test-results/y'):
            with self.subTest(path=value),self.assertRaises(JUnitEvidenceError):
                reviewed_junit_directory({'ci_junit_report_dir':value},'android-gradle')
    def test_unknown_provider_or_optional_refused(self):
        for metadata,provider in [({'ci_junit_report_dir':DIR},'go'),
                ({'ci_junit_report_dir':DIR,'ci_junit_required':False},'android-gradle')]:
            with self.assertRaises(JUnitEvidenceError):
                reviewed_junit_directory(metadata,provider)
    def test_symlinked_dir_refused(self):
        (self.root/'app-tv').symlink_to(self.root,target_is_directory=True)
        with self.assertRaisesRegex(JUnitEvidenceError,'symlink'):
            scan_junit_bundle(self.root,DIR)
    def test_invalid_xml_and_header_discrepancy_refused(self):
        self.xml(mismatch=True)
        with self.assertRaisesRegex(JUnitEvidenceError,'disagrees'):
            scan_junit_bundle(self.root,DIR)
        (self.directory/'TEST-Sample.xml').write_text('<testsuite tests="1"/>')
        with self.assertRaises(JUnitEvidenceError):
            scan_junit_bundle(self.root,DIR)
    def test_failures_or_all_skipped_refused(self):
        self.xml(failed=True)
        with self.assertRaisesRegex(JUnitEvidenceError,'failing'):
            scan_junit_bundle(self.root,DIR)
        self.xml(skipped=True)
        with self.assertRaisesRegex(JUnitEvidenceError,'skipped'):
            scan_junit_bundle(self.root,DIR)
    def test_external_report_or_symlink_member_refused(self):
        self.xml()
        (self.directory/'foo.txt').write_text('unexpected')
        with self.assertRaisesRegex(JUnitEvidenceError,'unreviewed'):
            scan_junit_bundle(self.root,DIR)
        (self.directory/'foo.txt').unlink()
        (self.directory/'TEST-Others.xml').symlink_to(self.directory/'TEST-Sample.xml')
        with self.assertRaisesRegex(JUnitEvidenceError,'symlink'):
            scan_junit_bundle(self.root,DIR)
    def test_positive_report_digest_is_stable_and_nonrelease(self):
        self.xml()
        proof=scan_junit_bundle(self.root,DIR)
        self.assertEqual(proof['tests']['total'],1)
        self.assertEqual(proof['tests']['failed'],0)
        self.assertFalse(proof['release_qualified'])
        self.assertEqual(scan_junit_bundle(self.root,DIR)['tests'],proof['tests'])
    def test_typed_evidence_verified_local_gate_only(self):
        self.xml()
        graph,node=row()
        with patch('devtool.actions_graph_export.workflow_yaml',return_value='reviewed yaml'):
            receipt=build_node_evidence(graph,node,source=SRC,source_tree=TREE,
                run_id=55,attempt=2,staging=self.root/'staging',junit_root=self.root)
            view=verify_node_evidence(receipt,graph,node,source=SRC,source_tree=TREE,
                run_id=55,attempt=2,junit_root=self.root)
        self.assertEqual(receipt['typed_evidence']['gates']['test']['state'],'PASS')
        self.assertEqual(receipt['typed_evidence']['tests']['total'],1)
        self.assertEqual(view['effective_gates']['test']['state'],'PASS')
        self.assertEqual(view['effective_gates']['release']['state'],'UNKNOWN')
        self.assertFalse(view['remote_authenticated'])
    def test_same_receipt_rejected_after_report_tamper(self):
        self.xml()
        graph,node=row()
        with patch('devtool.actions_graph_export.workflow_yaml',return_value='reviewed yaml'):
            receipt=build_node_evidence(graph,node,source=SRC,source_tree=TREE,
                run_id=55,attempt=2,staging=self.root/'staging',junit_root=self.root)
            (self.directory/'TEST-Sample.xml').write_text('<testsuite tests="1"><testcase name="different"/></testsuite>')
            with self.assertRaisesRegex(NodeEvidenceError,'JUnit'):
                verify_node_evidence(receipt,graph,node,source=SRC,source_tree=TREE,
                   run_id=55,attempt=2,junit_root=self.root)
    def test_fake_pass_on_no_optin_cannot_pass(self):
        graph,node=row();node.pop('junit_report_dir')
        with patch('devtool.actions_graph_export.workflow_yaml',return_value='reviewed yaml'):
            receipt=build_node_evidence(graph,node,source=SRC,source_tree=TREE,
                run_id=55,attempt=2,staging=self.root/'staging')
            receipt['typed_evidence']['gates']['test']['state']='PASS'
            receipt['receipt_sha256']=_digest({k:v for k,v in receipt.items() if k!='receipt_sha256'})
            with self.assertRaises(NodeEvidenceError):
                verify_node_evidence(receipt,graph,node,source=SRC,source_tree=TREE,
                    run_id=55,attempt=2)


if __name__=='__main__':
    unittest.main(verbosity=2)
