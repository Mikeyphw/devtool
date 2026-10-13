"""ACTIONS-10 V4: canonical apply-phase markers in Termux Textual host."""
import asyncio
import pytest

from devtool.textual_ux import ApplyStageTracker, artifact_stage


def test_apply_stages_follow_actual_lines_before_exit():
    t = ApplyStageTracker()
    assert t.outcomes['Review'] == 'active'
    t.begin()
    assert t.outcomes['Review'] == 'done'
    assert t.outcomes['Qualify'] == 'active'
    t.observe_line('artifact-validation: Base contract verified (touched-files) for 8 touched path(s).')
    assert t.outcomes['Qualify'] == 'done'
    assert t.outcomes['Apply'] == 'active'
    t.observe_line('Applied overlay from /storage/emulated/0/Download/overlay.zip')
    assert t.outcomes['Apply'] == 'done'
    assert t.outcomes['Validate'] == 'active'
    assert t.current == 'Validate'
    t.observe_line('$ sh .devtool-artifact/validate.sh')
    assert t.outcomes['Validate'] == 'active'
    t.observe_line('[detached HEAD a7cf4e3] feat(ui): fix overlay')
    assert t.outcomes['Validate'] == 'done'
    assert t.outcomes['Commit'] == 'done'
    assert t.outcomes['Finish'] == 'active'
    t.finish(0)
    assert all(value == 'done' for value in t.outcomes.values())


def test_validation_failure_stays_red_and_commit_pending():
    t = ApplyStageTracker()
    t.begin()
    t.observe_line('Applied overlay from /tmp/overlay.zip')
    t.observe_line('$ sh .devtool-artifact/validate.sh')
    t.observe_state({'event_type': 'phase_finished',
                     'phase': {'name':'Artifact validation'},
                     'result': {'returncode': 1}})
    t.finish(1)
    assert t.outcomes['Validate'] == 'failed'
    assert t.outcomes['Commit'] == 'pending'
    assert t.outcomes['Finish'] == 'failed'


def test_unseen_steps_are_never_imputed_from_successful_exit():
    t = ApplyStageTracker()
    t.begin()
    t.finish(0)
    assert t.outcomes['Qualify'] == 'pending'
    assert t.outcomes['Apply'] == 'pending'
    assert t.outcomes['Validate'] == 'pending'
    assert t.outcomes['Commit'] == 'pending'
    assert t.outcomes['Finish'] == 'done'


def test_canonical_phase_events_drive_stage_without_parsing_arbitrary_log():
    t = ApplyStageTracker()
    t.begin()
    t.observe_state({'event_type': 'phase_started', 'phase': {'name':'Artifact preflight: source'}})
    assert t.current == 'Qualify'
    t.observe_state({'event_type':'phase_finished',
                     'phase': {'name':'Artifact preflight: source'},
                     'result': {'returncode': 0}})
    assert t.outcomes['Qualify'] == 'done'
    t.observe_state({'event_type':'phase_started', 'phase': {'name':'Artifact validation'}})
    assert t.current == 'Validate'
    t.observe_state({'event_type':'phase_finished',
                     'phase': {'name':'Artifact validation'},
                     'result': {'returncode': 0, 'skipped': True}})
    assert t.outcomes['Validate'] == 'skipped'
    t.observe_state({'event_type':'progress','phase': {'name':'Artifact validation'}})
    assert t.outcomes['Validate'] == 'skipped'


def test_log_content_cannot_fake_commit_or_success():
    t = ApplyStageTracker()
    t.begin()
    for line in ['my repository git has a commit', 'A test says Applied overlay from path',
                 'test_commit_changes_should_fail.py::test_green']:
        t.observe_line(line)
    assert t.outcomes['Commit'] == 'pending'
    assert t.outcomes['Apply'] == 'pending'


def test_guard_against_regression_to_review_on_stale_updates():
    t = ApplyStageTracker()
    t.begin()
    t.observe_line('Applied overlay from /tmp/z.zip')
    assert t.current == 'Validate'
    t.observe_state({'event_type':'phase_started','phase': {'name':'Artifact preflight: old'}})
    assert t.current == 'Validate'

def test_actual_textual_apply_widgets_progress_before_process_exit():
    pytest.importorskip('textual')
    from devtool.textual_apply_host import build_apply_textual_app

    class Source:
        max_log_lines = 80
        review = {'artifact':'overlay.zip', 'target':'devtool', 'changes':'2 files',
                  'validation':'required', 'apply':'atomic', 'lifecycle':'commit'}
        returncode = None
        cancel_requested = False
        elapsed_seconds = 4
        started = False
        @property
        def running(self):
            return self.started and self.returncode is None

        def __init__(self):
            self.lines = []
            self.state = {}
        def start(self, *, force_yes):
            self.started = True
        def drain_lines(self):
            lines = self.lines[:]
            self.lines.clear()
            return lines
        def read_state(self):
            return self.state
        def request_cancel(self):
            self.cancel_requested = True
        def terminate(self):
            pass

    source = Source()
    app = build_apply_textual_app(source, confirm_required=False, compact=True)

    async def exercise():
        async with app.run_test(size=(40, 22)) as pilot:
            await pilot.pause(.12)
            assert app._stage_tracker.outcomes['Qualify'] == 'active'
            source.lines = ['artifact-validation: Base contract verified (touched-files)',
                            'Applied overlay from /tmp/overlay.zip',
                            '$ sh .devtool-artifact/validate.sh']
            await pilot.pause(.23)
            assert app._stage_tracker.outcomes['Apply'] == 'done'
            assert app._stage_tracker.current == 'Validate'
            assert 'Validate' in str(app.query_one('#apply-stage').render())
            source.lines = ['[detached HEAD 2182abc] feat(ui): fix']
            await pilot.pause(.18)
            assert app._stage_tracker.outcomes['Commit'] == 'done'
            assert app._stage_tracker.current == 'Finish'
            source.returncode = 0
            await pilot.pause(.18)
            assert app._stage_tracker.outcomes['Finish'] == 'done'

    asyncio.run(exercise())
