"""UX-RICH01 native Rich delivery / Actions presentation acceptance."""
import io
import os
import unittest
from rich.console import Console
from devtool.delivery_rich import (NativeRichMonitor, MonitorDetached,
    ci_snapshot_view, actions_run_view, terminal_rich_enabled)

class FakeStream:
    def __init__(self, tty):self.tty=tty
    def isatty(self):return self.tty

class RichMonitorTests(unittest.TestCase):
    def test_direct_tty_uses_rich(self):
        self.assertTrue(terminal_rich_enabled(env={'TERM':'xterm-256color'},stdout=FakeStream(True),stdin=FakeStream(True)))

    def test_unsafe_force_color_pipe_stays_plain(self):
        self.assertFalse(terminal_rich_enabled(env={'TERM':'xterm','FORCE_COLOR':'1',
            'DEVTOOL_EXECUTION_PRESENTATION':'plain','DEVTOOL_JOB_UI':'plain'},
            stdout=FakeStream(False),stdin=FakeStream(True)))

    def test_no_animated_terminal_for_redirected_pipe(self):
        self.assertFalse(terminal_rich_enabled(env={'TERM':'xterm'},stdout=FakeStream(False),stdin=FakeStream(True)))

    def test_no_animated_terminal_for_ci_no_color_or_dumb(self):
        for patch in ({'CI':'true'},{'NO_COLOR':'1'},{'CLICOLOR':'0'},{'TERM':'dumb'}):
            with self.subTest(patch=patch):
                env={'TERM':'xterm','FORCE_COLOR':'1',**patch}
                self.assertFalse(terminal_rich_enabled(env=env,stdout=FakeStream(True),stdin=FakeStream(True)))

    def render(self, obj, width):
        out=io.StringIO();console=Console(file=out,width=width,color_system=None,force_terminal=False)
        console.print(obj)
        return out.getvalue()

    def test_ci_running_and_failed_with_real_identifiers(self):
        snap={'state':'CHECKS_FAILED','poll':4,'required':['A','B'],'missing':[],
              'runs':[{'id':43885559323,'name':'R0 / R1 Android API 102','status':'completed','conclusion':'failure'},
                      {'id':43885559259,'name':'Repository contract','status':'completed','conclusion':'success'}],
              'diagnosis':{'failures':[{'jobs':[{'name':'JVM tests and Android APK','excerpt':['AssertionError: a real failure']}]}]}}
        for width in (40,52,80,120):
            with self.subTest(width=width):
                view=self.render(ci_snapshot_view(snap,width=width,detail=True,verbose=True),width)
                self.assertIn('FAILED',view)
                self.assertIn('JVM tests',view)
                self.assertIn('Repository contract',view)
                self.assertIn('CI NOT QUALIFIED' if False else 'PR CHECKS',view)
                self.assertTrue(max(map(len,view.splitlines()))<=width+1)

    def test_pending_and_missing_dont_indicate_pass(self):
        p=self.render(ci_snapshot_view({'state':'WAITING_REGISTRATION','required':['A'],
            'runs':[],'missing':['A'],'poll':1},width=50),50)
        self.assertIn('WAITING',p)
        self.assertIn('MISSING',p)
        self.assertNotIn('PASS ',p)

    def test_states_never_map_ambiguous_to_success(self):
        for state in ('BLOCKED_AMBIGUOUS','BLOCKED_TRIGGER','REGISTRATION_TIMED_OUT',
                      'BRANCH_RULES_UNVERIFIED','REGISTRATION_PROVIDER_UNAVAILABLE'):
            text=self.render(ci_snapshot_view({'state':state,'runs':[],'missing':['A']},width=80),80)
            self.assertNotIn('✓',text)

    def test_run_job_steps_and_failure_render(self):
        snap={'remote':{'id':23,'status':'completed','conclusion':'failure','run_attempt':2,'head_sha':'abcd'},
              'source_binding':'matching',
              'jobs':[{'name':'android','status':'completed','conclusion':'failure',
                       'steps':[{'name':'Gradle tests','status':'completed','conclusion':'failure'}]}]}
        text=self.render(actions_run_view(snap,width=80,detail=True,verbose=True),80)
        self.assertIn('Attempt 2',text)
        self.assertIn('Gradle tests',text)
        self.assertIn('failure'.upper(),text)
        self.assertIn('Source abcd',text)

    def test_navigation_never_changes_actual_ci_facts(self):
        m=NativeRichMonitor(enabled=False)
        snap={'state':'CHECKS_FAILED','runs':[{'name':'A'},{'name':'B'}]}
        m.observe(snap)
        for key in ('j','enter','v','l','r','k'):
            m.key(key)
        self.assertEqual(m.last,snap)
        self.assertEqual(m.selected,0)
        self.assertTrue(m.detail)
        self.assertTrue(m.verbose)
        self.assertTrue(m.log_requested)
        self.assertTrue(m.refresh_requested)

    def test_log_tab_is_toggleable_without_api_or_mutation(self):
        m=NativeRichMonitor(enabled=False)
        m.key('l');self.assertTrue(m.log_requested)
        m.key('l');self.assertFalse(m.log_requested)

    def test_detach_does_not_manufacture_ci_success(self):
        m=NativeRichMonitor(enabled=False)
        m.key('q')
        with self.assertRaises(MonitorDetached):m.observe({'state':'CHECKS_RUNNING'})

    def test_native_polling_manual_refresh_consumes_existing_slot(self):
        m=NativeRichMonitor(enabled=True,console=Console(file=io.StringIO(),width=80))
        m.key('r')
        m.wait_interval(0.5)
        self.assertFalse(m.refresh_requested)

    def test_native_polling_keyboard_detach_interrupts_without_green(self):
        m=NativeRichMonitor(enabled=True,console=Console(file=io.StringIO(),width=80))
        m.key('q')
        with self.assertRaises(MonitorDetached):m.wait_interval(0.1)

    def test_40_and_120_column_resizes_adapt_console(self):
        from unittest.mock import patch
        m=NativeRichMonitor(enabled=True,console=Console(file=io.StringIO(),width=80))
        class DummyLive:
            def update(self,*a,**kw):pass
        m._live=DummyLive()
        # Simulate a terminal independent of pytest's stdin capture.
        with patch('devtool.delivery_rich.sys.stdin') as terminal_input:
            terminal_input.fileno.return_value=0
            for columns,rows in ((40,24),(120,40)):
                with (self.subTest(columns=columns),
                      patch('devtool.delivery_rich.os.get_terminal_size',return_value=os.terminal_size((columns,rows))),
                      patch('devtool.delivery_rich.shutil.get_terminal_size',return_value=os.terminal_size((106,24)))):
                    m.refresh()
                    self.assertEqual(m.console.width,columns)

    def test_resize_fallback_when_stdin_has_no_terminal(self):
        from unittest.mock import patch
        m=NativeRichMonitor(enabled=True,console=Console(file=io.StringIO(),width=80))
        class DummyLive:
            def update(self,*a,**kw):pass
        m._live=DummyLive()
        # Ensure the captured-stdin path uses the fallback and clamps widths.
        with patch('devtool.delivery_rich.sys.stdin') as captured_input:
            captured_input.fileno.side_effect=OSError('no terminal')
            for columns,expected in ((20,40),(52,52),(106,106),(220,180)):
                with (self.subTest(columns=columns),
                      patch('devtool.delivery_rich.os.get_terminal_size',return_value=os.terminal_size((80,24))),
                      patch('devtool.delivery_rich.shutil.get_terminal_size',return_value=os.terminal_size((columns,24))) as fallback):
                    m.refresh()
                    self.assertEqual(m.console.width,expected)
                    self.assertTrue(fallback.called)

    def test_newly_added_required_ci_policy_is_checked_after_native_commit(self):
        # Native source tests run against the real staged launcher; no target
        # mutations are performed by this fixture.
        from unittest.mock import patch
        import tempfile
        import hashlib
        import zipfile
        from pathlib import Path
        from devtool import launcher_contract_v3 as lc
        with tempfile.TemporaryDirectory() as dirname:
            root=Path(dirname)
            policy=root/'config'/'delivery-required-ci.json'
            policy.parent.mkdir()
            policy.write_bytes(b'{"schema_version":1}')
            m={'source':{'repository':'Mikeyphw/devtool','branch':'feature/ux-rich',
                         'source_sha':'a'*40},
               'delivery':{'base':'main','commit_message':'test'},
               'plan':{'schema_version':1,'changes':[
                   {'path':'config/delivery-required-ci.json','action':'add'}]},
               'inventory':[{'action':'add','path':'config/delivery-required-ci.json',
                   'payload_sha256':hashlib.sha256(policy.read_bytes()).hexdigest()}]}
            archive=root/'candidate.zip'
            with zipfile.ZipFile(archive,'w') as candidate:
                candidate.writestr('fixture.txt',b'verified adoption fixture archive')
            with (patch.object(lc,'check_source'),
                  patch.object(lc,'runtime_probe'),
                  patch.object(lc,'repo_identity',return_value={'branch':'feature/ux-rich',
                            'source_sha':'b'*40}),
                  patch.object(lc,'verify_postcommit_status'),
                  patch.object(lc,'scaffold',return_value=archive),
                  patch.object(lc,'native'),
                  patch.object(lc,'native_adopt_committed',return_value={'id':'c'*32}) as adoption,
                  patch.object(lc,'required_ci_preflight',side_effect=[None,{'policy_sha256':'e'*64}]) as preflight):
                result=lc.apply_package(root,root,m,{},False,watch_ci=True)
            self.assertEqual(result,0)
            self.assertEqual(preflight.call_count,2)
            adoption.assert_called_once_with(root,root,archive,'b'*40)

    def test_noninteractive_view_does_not_mutate_logs_or_clipboard(self):
        original=os.environ.copy()
        m=NativeRichMonitor(enabled=False)
        with m:m.observe({'state':'CHECKS_RUNNING','runs':[],'missing':[]})
        self.assertEqual(dict(os.environ),original)


    def test_m6_ci_is_shared_vertical_portrait_not_fullscreen(self):
        from devtool.m6_live import build_live_view
        from rich.console import Console
        for width in (40,52,60,80,120):
            out=io.StringIO()
            console=Console(file=out,width=width,color_system=None,force_terminal=False)
            snapshot={'state':'CHECKS_FAILED','runs':[{'id':123,'name':'Required tests',
                      'conclusion':'failure','status':'completed'}]}
            console.print(build_live_view('ci',snapshot,width=width,icons='ascii'))
            text=out.getvalue()
            for label in ('IDENTITY','STAGES','EVENTS','OUTPUT','EVIDENCE','Required tests'):
                self.assertIn(label,text)
            self.assertTrue(all(len(line)<=width for line in text.splitlines()))

    def test_m6_native_run_failure_does_not_qualify_ci(self):
        m=NativeRichMonitor(enabled=False,kind='run')
        m.observe({'remote':{'id':101,'run_attempt':3,'conclusion':'failure'},
                   'jobs':[{'name':'test','conclusion':'failure','status':'completed'}]})
        out=io.StringIO()
        console=Console(file=out,width=52,color_system=None)
        console.print(m.render())
        self.assertIn('failure'.upper(),out.getvalue())
        self.assertNotIn('Native required checks passed',out.getvalue())

if __name__=='__main__':unittest.main()
