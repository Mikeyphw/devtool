"""V2 UX promise audit: stage fidelity, real progress semantics, Termux follow."""
from __future__ import annotations

import asyncio
from queue import Queue
from threading import Event

import pytest

from devtool.actions_live_progress import project_observation
from devtool.job_ui import build_compact_execution_app, job_icons
from devtool.textual_execution_host import interactive_watch_argv


def test_remote_stages_represent_actual_jobs_and_steps_only():
    remote = {'remote': {'id': 123, 'repository': 'x/y', 'status': 'in_progress'},
              'jobs': [
                  {'name': 'Android CI', 'status': 'in_progress', 'steps': [
                      {'name': 'Initialize', 'status': 'completed', 'conclusion': 'success'},
                      {'name': 'Gradle release', 'status': 'in_progress'}]},
                  {'name': 'Lint', 'status': 'completed', 'conclusion': 'failure', 'steps': [
                      {'name': 'Lint', 'status': 'completed', 'conclusion': 'failure'}]},
              ]}
    state, lines = project_observation(remote)
    assert state['percent'] is None
    assert state['completion_status'] == 'running'  # UI cannot qualify remote output
    assert state['stages'] == [
        {'name': 'Android CI', 'status': 'in_progress'},
        {'name': '↳ Gradle release', 'status': 'in_progress'},
        {'name': 'Lint', 'status': 'failure'},
        {'name': '↳ Lint', 'status': 'failure'},
    ]
    assert any('Gradle release' in s for s in lines)


def test_interactive_watch_default_extends_only_missing_limits():
    raw = ['-r', '/example', 'actions', 'watch', '--run-id', '42']
    assert interactive_watch_argv(raw, enabled=False) == raw
    assert interactive_watch_argv(raw, enabled=True) == raw + [
        '--max-checks', '120', '--interval', '10']
    assert interactive_watch_argv(raw + ['--max-checks=2', '--interval', '1'], enabled=True) == raw + [
        '--max-checks=2', '--interval', '1']
    assert interactive_watch_argv(raw + ['--interval', '4'], enabled=True) == raw + [
        '--interval', '4', '--max-checks', '120']


class Source:
    max_log_lines = 48
    def __init__(self):
        self._queue = Queue()
        self._ready = Event()
        self.calls = 0
    def _request_cancel(self):
        self.calls += 1


@pytest.mark.parametrize('size', [(32, 10), (40, 12), (80, 25)])
def test_actual_textual_stage_panel_and_indeterminate_progress(size):
    pytest.importorskip('textual')
    from textual.widgets import ProgressBar, Static
    source = Source()
    app = build_compact_execution_app(source, remote=True)

    async def run():
        async with app.run_test(size=size) as pilot:
            source._queue.put(('snapshot', {
                'command': 'GitHub Actions', 'phase_name': 'Run #123',
                'completion_status': 'running', 'percent': None,
                'stages': [
                    {'name': 'Android CI', 'status': 'in_progress'},
                    {'name': '↳ Gradle release', 'status': 'in_progress'},
                    {'name': 'Lint', 'status': 'failure'},
                ],
            }))
            await pilot.pause(0.2)
            bar = app.query_one('#job-progress', ProgressBar)
            assert bar.total is None
            assert bar.display  # Spinner/bar visible, no fake percentage
            panel = app.query_one('#job-stages', Static)
            if size[1] >= 16:
                assert 'Gradle release' in str(panel.render())
            source._queue.put(('snapshot', {
                'command': 'GitHub Actions', 'phase_name': 'Run #123',
                'completion_status': 'failed', 'percent': None,
                'stages': [{'name': 'Android CI', 'status': 'failure'}],
            }))
            await pilot.pause(0.2)
            assert app._outcome == 'failed'
            assert app.query_one('#job-progress', ProgressBar).display is False
            await pilot.press('q')
    asyncio.run(run())


def test_icons_plain_and_nerd_font_are_optional():
    assert job_icons({})['done'] == '✓'
    assert job_icons({'DEVTOOL_NERD_FONT': '1'})['done'] != '✓'
