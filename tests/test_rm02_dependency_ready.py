"""RM-02 behavior proofs: real subprocess overlap, safety, failures, cancellation.

Not file-existence tests: asserts the actual live admission/completion order and
subprocess outcomes, including nodes that must *not* execute.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import tempfile
import unittest

from devtool.config import load_project_config
from devtool.workflow_runtime import build_workspace_specs
from devtool.workflow_scheduler import build_execution_plan_from_specs, run_workflow_specs


def _fixture(root: Path, *, jobs: dict[str, dict], edges: dict[str, list[str]],
             max_nodes: int = 2, resource: str = ""):
    # JSON arrays and strings are legal TOML values for these data types.
    rows = ['[project]', 'runner = "command"', '[workspace]', 'default_target = "app"',
            '[workspace.scheduler]', 'parallel = true', f'max_parallel_nodes = {max_nodes}',
            'max_gradle_nodes = 1', 'max_dotnet_nodes = 1']
    for name, entry in jobs.items():
        rows += [f'[jobs.{name}]', 'runner = "command"', 'root = "."',
                 f'command = {json.dumps(entry["command"])}',
                 'inputs = ["src/**"]',
                 f'outputs = {json.dumps(entry.get("outputs", [f"out/{name}/**"]))}']
        if entry.get('resource') or entry.get('reservation'):
            rows += [f'[jobs.{name}.metadata]']
            if entry.get('resource'):
                rows += [f'resource_class = {json.dumps(entry["resource"])}']
            if entry.get('reservation'):
                rows += [f'global_reservations = {json.dumps([entry["reservation"]])}']
    rows += ['[targets.app]', 'runner = "command"', '[targets.app.workflows]', 'build = [']
    for name, deps in edges.items():
        rows += ['  { id = ' + json.dumps(name) + ', ref = ' + json.dumps('job:' + name)
                 + ', depends_on = ' + json.dumps(deps) + ' },']
    rows += [']']
    (root / 'src').mkdir(exist_ok=True)
    (root / '.devtool.toml').write_text('\n'.join(rows) + '\n', encoding='utf8')
    cfg = load_project_config(str(root), str(root / '.devtool.toml'), target_name='app')
    specs = build_workspace_specs(cfg, ['app'], 'build')
    return cfg, specs


def _cmd(name: str, duration: float, *, exit_code: int = 0, wait_for: str = ""):
    code = (
        'import pathlib,time,sys; '
        'root=pathlib.Path("out")/' + repr(name) + '; '
        'root.mkdir(parents=True,exist_ok=True); '
        '(root/"start").write_text(str(time.monotonic())); '
        + (
            'deadline=time.monotonic()+20; '
            'marker=pathlib.Path("out")/' + repr(wait_for) + '\n'
            'while not marker.exists():\n'
            '    if time.monotonic()>deadline: sys.exit(93)\n'
            '    time.sleep(0.01)\n'
            if wait_for else ''
        )
        + 'time.sleep(' + str(duration) + '); ' 
        '(root/"finish").write_text(str(time.monotonic())); '
        'sys.exit(' + str(exit_code) + ')'
    )
    return ['python3', '-c', code]


def _run(cfg, specs, *, continue_on_error=False, dry_run=False, progress=None):
    return run_workflow_specs(cfg, specs, verbose=False, dry_run=dry_run,
                              continue_on_error=continue_on_error, log_path=None,
                              parallel_requested=True, max_parallel_nodes=2,
                              tasker_progress=progress)


class DependencyReadyTests(unittest.TestCase):
    def test_successor_starts_before_unrelated_long_sibling_finishes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # B cannot finish until C actually completes. A static wave barrier
            # would deadlock this handshake; a ready-queue admits C after A.
            jobs = {
                'a': {'command': _cmd('a', .01)},
                'b': {'command': _cmd('b', .01, wait_for='c/finish')},
                'c': {'command': _cmd('c', .01)},
            }
            cfg, specs = _fixture(root, jobs=jobs, edges={'a': [], 'b': [], 'c': ['a']})
            before = build_execution_plan_from_specs(cfg, specs, parallel_requested=True)
            self.assertEqual(tuple(before.waves[0].nodes), ('app.build.a', 'app.build.b'))
            results, _, plan = _run(cfg, specs)
            self.assertTrue(all(not r.returncode for r in results))
            self.assertEqual([r.metadata['workflow_node_key'] for r in results],
                             ['app.build.a','app.build.b','app.build.c'])
            self.assertIsNotNone(plan)
            self.assertLess(float((root/'out/c/start').read_text()), float((root/'out/b/finish').read_text()))
            self.assertEqual(list(plan.actual_start_order), ['app.build.a','app.build.b','app.build.c'])
            self.assertLess(float((root/'out/c/finish').read_text()), float((root/'out/b/finish').read_text()))
            self.assertCountEqual(plan.actual_finish_order, ('app.build.a', 'app.build.b', 'app.build.c'))
            self.assertEqual(plan.as_dict()['runtime_scheduling']['mode'], 'dependency-ready')
            self.assertTrue(all('workflow_scheduler_admission_order' in r.metadata for r in results))

    def test_shared_output_reservation_prevents_overtaking_active_writer(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs = {n: {'command': _cmd(n, duration)} for n, duration in [('a', .06), ('b', .35), ('c', .01)]}
            jobs['b']['outputs'] = ['out/b/**', 'shared/**']
            jobs['c']['outputs'] = ['out/c/**', 'shared/**']
            cfg, specs = _fixture(root, jobs=jobs, edges={'a': [], 'b': [], 'c': ['a']})
            results, _, plan = _run(cfg, specs)
            self.assertTrue(all(r.returncode == 0 for r in results))
            self.assertGreaterEqual(float((root/'out/c/start').read_text()), float((root/'out/b/finish').read_text()))
            self.assertIn('app.build.c', dict(plan.resource_wait_reasons))

    def test_reserved_global_resource_serializes_disjoint_writers(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            jobs={n: {'command':_cmd(n, .1), 'reservation':'one-device'} for n in ('a','b')}
            cfg,specs=_fixture(root,jobs=jobs,edges={'a':[],'b':[]})
            results,_,plan=_run(cfg,specs)
            self.assertEqual([r.returncode for r in results],[0,0])
            self.assertGreaterEqual(float((root/'out/b/start').read_text()),float((root/'out/a/finish').read_text()))
            self.assertIn('reservation conflict',dict(plan.resource_wait_reasons)['app.build.b'])

    def test_fail_fast_drains_running_and_blocks_unscheduled(self):
        """Handshake makes failure-before-B-completion a fact, not a sleep guess.

        On slower Termux devices a nominal 0.10s process can finish later than
        a 0.30s process because startup/host load is not deterministic.
        B is kept in-flight until the scheduler has *observed* A failing and
        processed the blocked C node. Only then may B finish and expose D.
        """
        from unittest.mock import patch
        from devtool import runners
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs = {
                'a': {'command': _cmd('a', 0, exit_code=9, wait_for='b/start')},
                'b': {'command': _cmd('b', 0, wait_for='release-b')},
                'c': {'command': _cmd('c', 0)},
                'd': {'command': _cmd('d', 0)},
            }
            cfg, specs = _fixture(root, jobs=jobs,
                                  edges={'a': [], 'b': [], 'c': ['a'], 'd': ['b']})
            original = runners.run_specs
            observed = []

            def signal_after_failure(config, command_specs, **kwargs):
                key = command_specs[0].metadata.get('workflow_node_key')
                if key == 'app.build.c':
                    # The synthetic blocked C is visited ONLY after A's
                    # failure has entered the scheduler's terminal state.
                    statuses = kwargs.get('workflow_initial_statuses', {})
                    observed.append(dict(statuses))
                    (root / 'out/release-b').write_text('after-failure')
                return original(config, command_specs, **kwargs)

            with patch.object(runners, 'run_specs', side_effect=signal_after_failure):
                results, _, plan = _run(cfg, specs)
            outcomes = dict(plan.node_outcomes)
            self.assertEqual(outcomes, {
                'app.build.a': 'failed',
                'app.build.b': 'passed',
                'app.build.c': 'blocked',
                'app.build.d': 'skipped',
            })
            self.assertTrue(observed)
            self.assertEqual(observed[0]['app.build.a'], 'failed')
            self.assertFalse((root / 'out/c/start').exists())
            self.assertFalse((root / 'out/d/start').exists())
            self.assertEqual(len(results), 4)

    def test_fail_fast_does_not_revoke_a_node_admitted_before_failure(self):
        """A later failure may not retroactively cancel already-admitted D."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs = {
                'a': {'command': _cmd('a', 0, exit_code=9, wait_for='d/start')},
                'b': {'command': _cmd('b', 0)},
                'c': {'command': _cmd('c', 0)},
                'd': {'command': _cmd('d', 0)},
            }
            cfg, specs = _fixture(root, jobs=jobs,
                                  edges={'a': [], 'b': [], 'c': ['a'], 'd': ['b']})
            results, _, plan = _run(cfg, specs)
            outcomes = dict(plan.node_outcomes)
            self.assertEqual(outcomes['app.build.a'], 'failed')
            self.assertEqual(outcomes['app.build.b'], 'passed')
            self.assertEqual(outcomes['app.build.c'], 'blocked')
            self.assertEqual(outcomes['app.build.d'], 'passed')
            self.assertTrue((root / 'out/d/start').exists())
            self.assertIn('app.build.d', plan.actual_start_order)
            self.assertEqual(len(results), 4)

    def test_unharvested_failure_is_processed_before_new_admissions(self):
        """When a failure is done but omitted from a prior wait snapshot,
        harvest it before admitting an unrelated successor. Tests the actual
        future observation order without relying on process sleep durations.
        """
        from concurrent.futures import wait as real_wait, ALL_COMPLETED
        from unittest.mock import patch
        from devtool import workflow_scheduler as scheduling
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs = {
                'a': {'command': _cmd('a', 0, exit_code=7)},
                'b': {'command': _cmd('b', 0)},
                'c': {'command': _cmd('c', 0)},
                'd': {'command': _cmd('d', 0)},
            }
            cfg, specs = _fixture(root, jobs=jobs,
                                  edges={'a': [], 'b': [], 'c': ['a'], 'd': ['b']})
            injected = []

            def omit_first_terminal(futures, *, timeout=None, return_when=None):
                active = tuple(futures)
                if len(active) == 2 and not injected:
                    done, _ = real_wait(active, timeout=20, return_when=ALL_COMPLETED)
                    self.assertEqual(len(done), 2, 'both A and B must be completed')
                    injected.append(True)
                    # Emulate wait seeing B just before A completes: B is
                    # harvested now, while A is terminal by the next cycle.
                    return {active[1]}, {active[0]}
                return real_wait(active, timeout=timeout, return_when=return_when)

            with patch.object(scheduling, 'wait', side_effect=omit_first_terminal):
                results, _, plan = _run(cfg, specs)
            self.assertTrue(injected)
            self.assertEqual(dict(plan.node_outcomes), {
                'app.build.a': 'failed',
                'app.build.b': 'passed',
                'app.build.c': 'blocked',
                'app.build.d': 'skipped',
            })
            self.assertNotIn('app.build.d', plan.actual_start_order)
            self.assertFalse((root / 'out/d/start').exists())
            self.assertEqual(len(results), 4)

    def test_continue_on_error_runs_independent_but_blocks_descendant(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            jobs={'a':{'command':_cmd('a',.02,exit_code=4)},
                  'b':{'command':_cmd('b',.04)},
                  'c':{'command':_cmd('c',.01)}}
            cfg,specs=_fixture(root,jobs=jobs,edges={'a':[],'b':[],'c':['a']})
            results,_,plan=_run(cfg,specs,continue_on_error=True)
            self.assertEqual(dict(plan.node_outcomes), {'app.build.a':'failed','app.build.b':'passed','app.build.c':'blocked'})
            self.assertFalse((root/'out/c/start').exists())
            self.assertEqual(len(results),3)

    def test_worker_exception_is_terminal_and_does_not_deadlock(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            jobs={'a':{'command':_cmd('a',.01)}, 'b':{'command':_cmd('b',.01)}}
            cfg,specs=_fixture(root,jobs=jobs,edges={'a':[],'b':['a']})
            from devtool import runners
            original=runners.run_specs
            def break_a(config, command_specs, **kwargs):
                if command_specs[0].metadata.get('workflow_node_key')=='app.build.a':
                    raise RuntimeError('deliberate worker error')
                return original(config,command_specs,**kwargs)
            with patch.object(runners,'run_specs',side_effect=break_a):
                results,_,plan=_run(cfg,specs)
            self.assertEqual(dict(plan.node_outcomes),{'app.build.a':'failed','app.build.b':'blocked'})
            self.assertFalse((root/'out/b/start').exists())
            self.assertIn('worker',results[0].failure_kind)

    def test_gradle_resource_cap_blocks_overlap_without_path_collision(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            jobs={n:{'command':_cmd(n,.15), 'resource':'gradle'} for n in ('a','b')}
            cfg,specs=_fixture(root,jobs=jobs,edges={'a':[],'b':[]})
            results,_,plan=_run(cfg,specs)
            self.assertTrue(all(r.returncode==0 for r in results))
            self.assertGreaterEqual(float((root/'out/b/start').read_text()),
                                    float((root/'out/a/finish').read_text()))
            self.assertEqual(dict(plan.resource_wait_reasons)['app.build.b'],
                             'resource class capacity/exclusivity')

    def test_cancel_during_running_node_prevents_later_successor(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            jobs={'a':{'command':_cmd('a',.6)},'b':{'command':_cmd('b',.01)}}
            cfg,specs=_fixture(root,jobs=jobs,edges={'a':[],'b':['a']})
            class CancelWhenStarted:
                current_phase = None
                def cancel_requested(self): return (root/'out/a/start').exists()
                def plan_phases(self,*args,**kwargs): pass
                def phase_started(self,*args,**kwargs): pass
                def phase_finished(self,*args,**kwargs): pass
                def progress(self,*args,**kwargs): pass
            results,_,plan=_run(cfg,specs,progress=CancelWhenStarted())
            self.assertIn(dict(plan.node_outcomes)['app.build.b'], ('cancelled','blocked'))
            self.assertFalse((root/'out/b/start').exists())
            self.assertNotIn('app.build.b',plan.actual_start_order)

    def test_cancellation_prevents_unstarted_commands(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            jobs={'a':{'command':_cmd('a',.01)},'b':{'command':_cmd('b',.01)}}
            cfg,specs=_fixture(root,jobs=jobs,edges={'a':[],'b':['a']})
            class CancelSink:
                current_phase = None
                def cancel_requested(self): return True
                def plan_phases(self,*args,**kwargs): pass
                def phase_started(self,*args,**kwargs): pass
                def phase_finished(self,*args,**kwargs): pass
                def progress(self,*args,**kwargs): pass
            results,_,plan=_run(cfg,specs,progress=CancelSink())
            self.assertEqual(dict(plan.node_outcomes),{
                'app.build.a':'cancelled','app.build.b':'cancelled'})
            self.assertFalse((root/'out').exists())
            self.assertEqual(plan.actual_start_order,())
            self.assertEqual(len(results),2)

    def test_dry_run_remains_sequential_and_non_mutating(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            jobs={'a':{'command':_cmd('a',.01)},'b':{'command':_cmd('b',.01)}}
            cfg,specs=_fixture(root,jobs=jobs,edges={'a':[],'b':[]})
            _,_,plan=_run(cfg,specs,dry_run=True)
            self.assertFalse((root/'out').exists())
            self.assertEqual(plan.actual_start_order,())
            self.assertEqual(plan.as_dict()['runtime_scheduling']['mode'],'not-executed')

if __name__=='__main__':
    unittest.main()
