"""UX-RICH02: pure read-only operator Rich views and terminal guards.

No GitHub calls, filesystem mutation, clipboard, execute or status polling.
"""
from __future__ import annotations
import io
import types
import unittest
from unittest.mock import patch

from rich.console import Console
from devtool.operator_rich import (build_operator_view, operator_enabled, render_operator,
                                  browse_operator, operator_items, operator_drilldown)


class FakeStream:
    def __init__(self, tty): self.tty=tty
    def isatty(self): return self.tty


class OperatorViews(unittest.TestCase):
    def text(self, kind, snapshot, *, width=80, detail=False):
        file=io.StringIO()
        console=Console(file=file,force_terminal=False,color_system=None,width=width)
        console.print(build_operator_view(kind,snapshot,width=width,detail=detail))
        return file.getvalue()

    def test_read_only_terminal_gating(self):
        with patch('devtool.operator_rich.terminal_rich_enabled',return_value=True):
            self.assertTrue(operator_enabled(args=types.SimpleNamespace()))
            for kwargs in ({'json':True},{'_devtool_json':True},{'quiet':True},
                           {'machine':'json'},{'format':'json'},{'stdout_payload':'raw'}):
                self.assertFalse(operator_enabled(args=types.SimpleNamespace(**kwargs)))

    def test_no_tty_or_no_color_no_render(self):
        self.assertFalse(operator_enabled(stdout=FakeStream(False),stdin=FakeStream(True),env={'TERM':'xterm'}))
        self.assertFalse(operator_enabled(stdout=FakeStream(True),stdin=FakeStream(True),env={'TERM':'dumb'}))
        self.assertFalse(operator_enabled(stdout=FakeStream(True),stdin=FakeStream(True),env={'NO_COLOR':'1'}))

    def test_delivery_and_ci_receipt_presentation_no_fabricated_success(self):
        text=self.text('delivery',{'id':'a'*32,'state':'CHECKS_FAILED','ci':{'state':'CHECKS_FAILED',
             'checks_qualified':False},'runs':[{'name':'native Android','status':'completed','conclusion':'failure','id':123}]})
        self.assertIn('DELIVERY',text)
        self.assertIn('CHECKS_FAILED',text)
        self.assertIn('native Android',text)
        self.assertNotIn('release qualified',text.lower())

    def test_pipeline_dag_rows(self):
        text=self.text('pipeline',{'nodes':{'sync':{'state':'success'},'android':{'state':'failed'}}})
        self.assertIn('sync',text)
        self.assertIn('android',text)
        self.assertIn('failed',text)

    def test_actions_run_attempt_job(self):
        text=self.text('actions',{'remote':{'id':8,'run_attempt':2,'head_sha':'deadbeef','conclusion':'failure'},
                'jobs':[{'id':19,'name':'JVM test','status':'completed','conclusion':'failure'}]})
        self.assertIn('JVM test',text)
        self.assertIn('deadbeef',text)

    def test_validation_phases_and_events(self):
        text=self.text('validation',{'data':{'phases':[{'label':'restore','status':'completed'},
                                    {'label':'test','status':'failed'}], 'events':[{'type':'failure','event_id':'e1'}]}})
        self.assertIn('restore',text)
        self.assertIn('test',text)
        self.assertIn('failure',text)

    def test_local_runs_nested_catalog(self):
        text=self.text('runs',{'runs':[{'run':{'run_id':'abc','status':'failed','command_id':'test','provider':'local'}}]})
        self.assertIn('abc',text)
        self.assertIn('failed',text)

    def test_ssh_history_and_worker(self):
        text=self.text('ssh',{'profile':'termux','sessions':[{'session':'tmux1','status':'running'}],
                              'destination':'host:8022'})
        self.assertIn('termux',text)
        self.assertIn('host:8022',text)
        self.assertIn('running',text)

    def test_overlay_conflicts_and_no_mutation(self):
        text=self.text('overlay',{'decision':'refuse','conflicts':[{'path':'src/x.py','kind':'edit'}],
                'next_step':'Inspect conflict'})
        self.assertIn('src/x.py',text)
        self.assertIn('refuse',text)

    def test_workflow_retry_blocked(self):
        text=self.text('retry',{'disposition':'BLOCKED','reason':'source drift','original_run_id':'r01'})
        self.assertIn('source drift',text)
        self.assertIn('BLOCKED',text)

    def test_evidence_no_auto_qualification(self):
        text=self.text('qualification',{'qualification':{'qualified':False},'issues':[{'name':'signing unknown'}]})
        self.assertIn('signing unknown',text)
        self.assertIn('False',text)

    def test_transactions_and_campaign(self):
        self.assertIn('tx1',self.text('transaction',{'transaction_id':'tx1','blockers':['already committed']}))
        self.assertIn('gen1',self.text('campaign',{'steps':[{'id':'gen1','state':'pending'}]}))

    def test_credential_redaction_control_characters(self):
        text=self.text('delivery',{'state':'failed','reason':'Authorization: Bearer abc123\x1b[31m\nBAD'})
        self.assertNotIn('abc123',text)
        self.assertNotIn('\x1b',text)

    def test_long_rows_and_missing_values_narrow(self):
        text=self.text('actions',{'runs':[{'id':i,'name':'a'*300,'status':'queued'} for i in range(70)]},width=40)
        self.assertIn('additional records',text)
        self.assertLess(len(text),10000)

    def test_presentation_has_no_side_effects(self):
        value={'runs':[{'name':'example','status':'pending'}]}
        before=repr(value)
        for k in ('actions','runs','validation','ssh','delivery','pipeline'):
            self.text(k,value)
        self.assertEqual(before,repr(value))

    def test_renderer_failure_falls_back_to_plain_without_changing_data(self):
        data={'runs':[{'id':'one','status':'failed'}]}
        before=repr(data)
        with patch('devtool.operator_rich.operator_enabled',return_value=True), \
             patch('devtool.operator_rich.build_operator_view',side_effect=ValueError('renderer broken')):
            self.assertFalse(render_operator('runs',data))
        self.assertEqual(repr(data),before)

    def test_disabled_render_emits_nothing(self):
        with patch('devtool.operator_rich.operator_enabled',return_value=False):
            self.assertFalse(render_operator('runs',{'runs':[1]}))
            self.assertFalse(browse_operator('actions',{'items':[]}))

    def test_enabled_render_output(self):
        out=io.StringIO()
        console=Console(file=out,force_terminal=False,color_system=None,width=80)
        with patch('devtool.operator_rich.operator_enabled',return_value=True):
            self.assertTrue(render_operator('runs',{'runs':[{'id':'xyz'}]},console=console))
        self.assertIn('xyz',out.getvalue())

    def test_browser_does_not_require_tui_in_plain_mode(self):
        with patch('devtool.operator_rich.operator_enabled',return_value=False):
            self.assertFalse(browse_operator('actions',{'items':[]},refresh=lambda: (_ for _ in ()).throw(Exception('must not call'))))


    def test_pipeline_dependencies_are_visible_and_not_inferred(self):
        graph={'nodes':[{'id':'prepare','state':'success','needs':[]},
                        {'id':'test','state':'failed','needs':['prepare']}]}
        view=self.text('pipeline',graph)
        self.assertIn('needs: prepare',view)
        self.assertIn('failed',view)
        self.assertIn('needs: none',view)

    def test_legacy_pipeline_marks_missing_edges_unverified(self):
        view=self.text('pipeline',{'nodes':[{'id':'legacy','state':'pending'}]})
        self.assertIn('unavailable in older journal',view)
        self.assertNotIn('needs: none',view)

    def test_nested_actions_drilldown_preserves_real_identities(self):
        native={'repository':'Mikeyphw/devtool','items':[
          {'name':'Workflow X','id':42,'runs':[{'id':100,'jobs':[
             {'id':5,'name':'build','steps':[{'name':'Install','conclusion':'success'},
                                          {'name':'Test','conclusion':'failure'}]}]}]}]}
        first=operator_drilldown(native,0)
        self.assertIsNotNone(first)
        self.assertEqual(first[1],'runs')
        self.assertEqual(first[0]['items'][0]['id'],100)
        second=operator_drilldown(first[0],0)
        self.assertEqual(second[1],'jobs')
        third=operator_drilldown(second[0],0)
        self.assertEqual(third[1],'steps')
        self.assertEqual(third[0]['items'][1]['conclusion'],'failure')
        self.assertIsNone(operator_drilldown(third[0],100))

    def test_browser_selection_is_scoped_not_synthesized(self):
        payload={'items':[{'id':'a','name':'first','state':'success'},
                          {'id':'b','name':'second','state':'blocked'}]}
        self.assertEqual(len(operator_items(payload)),2)
        self.assertIsNone(operator_drilldown(payload,0))
        selected=self.text('actions',payload)
        self.assertIn('Selected native identity',selected)
        self.assertIn('first',selected)
        self.assertIn('blocked',selected)
        self.assertFalse('qualified_release' in payload)


if __name__=='__main__': unittest.main()
