"""G3 regression: all nodes remain inspectable at narrow phone sizes without mutation."""
from __future__ import annotations

import asyncio

import pytest

from devtool.actions_pipeline_textual import pipeline_screen_lines

PIPELINE = '7' * 32
SHA = 'd' * 40


def snapshot(*, cancelled=False, state='success'):
    nodes = [
        {'id': 'source', 'kind': 'local-git', 'state': state, 'detail': 'source metadata ' * 10},
        {'id': 'android_build', 'kind': 'github-dispatch', 'state': state, 'detail': 'dispatch receipt ' * 10},
        {'id': 'remote_result', 'kind': 'github-result', 'state': state, 'detail': 'remote observation ' * 10},
        {'id': 'archive', 'kind': 'github-archive', 'state': state, 'detail': 'artifact receipt ' * 10},
    ]
    return {'phase': 'ACTIONS-06', 'pipeline_id': PIPELINE, 'name': 'G3-readonly',
            'repository': 'Mikeyphw/devtool', 'source_sha': SHA,
            'completed_successfully': state == 'success' and not cancelled,
            'qualified_release': False, 'cancel_requested': cancelled, 'nodes': nodes}


def test_compact_one_row_per_node_and_all_nodes_present():
    full = pipeline_screen_lines(snapshot())
    compact = pipeline_screen_lines(snapshot(), compact=True)
    assert len(compact['nodes'].splitlines()) == 4
    assert len(full['nodes'].splitlines()) == 8
    assert [line.split()[-1] for line in compact['nodes'].splitlines()] == [
        'source', 'android_build', 'remote_result', 'archive']
    assert all(len(line) < 42 for line in compact['nodes'].splitlines())
    assert 'NOT QUALIFIED' in compact['note']
    assert 'NOT QUALIFIED' in full['note']
    assert SHA[:12] in compact['identity'] and SHA in full['identity']
    assert 'd details' in compact['note']


def test_compact_preserves_stop_warning_and_untrusted_text_is_safe():
    view = pipeline_screen_lines(snapshot(cancelled=True), compact=True)
    assert 'remote jobs NOT cancelled' in view['note']
    assert 'NOT QUALIFIED' in view['note']
    bad = snapshot(state='blocked')
    bad['nodes'][0]['id'] = '\x1b[31m[bad]\x00'
    rendered = pipeline_screen_lines(bad, compact=True)
    assert '\x1b' not in rendered['nodes'] and '\x00' not in rendered['nodes']
    assert 'no replay' in rendered['note']


def test_actual_textual_48x20_and_details_toggle():
    pytest.importorskip('textual')
    from devtool.actions_pipeline_textual import create_pipeline_app
    from textual.widgets import Static
    from textual.containers import VerticalScroll

    # This fixture does not install a durable ACTIONS-06 journal.
    # Refresh must load the same deterministic synthetic snapshot instead.
    app = create_pipeline_app(PIPELINE, load=lambda: snapshot(), initial=snapshot())

    async def exercise():
        async with app.run_test(size=(48, 20)) as pilot:
            await pilot.pause()
            nodes = app.query_one('#nodes', Static)
            scroll = app.query_one('#nodes-scroll', VerticalScroll)
            assert 'archive' in str(nodes.render())
            assert len(str(nodes.render()).splitlines()) == 4
            # With four concise rows, each named node fits in the scroll viewport.
            assert scroll.size.height >= nodes.size.height
            await pilot.press('d')
            await pilot.pause()
            assert 'github-archive' in str(nodes.render())
            await pilot.press('d')
            await pilot.pause()
            assert 'archive' in str(nodes.render())
            assert len(str(nodes.render()).splitlines()) == 4
            await pilot.press('r')
            await pilot.pause()
            assert 'archive' in str(nodes.render())
            assert 'NOT QUALIFIED' in str(app.query_one('#note', Static).render())

    asyncio.run(exercise())


def test_failed_refresh_preserves_last_trusted_snapshot():
    """An unavailable journal must not discard or fabricate existing UI state."""
    pytest.importorskip('textual')
    from devtool.actions_pipeline_textual import create_pipeline_app
    from textual.widgets import Static

    def unavailable_journal():
        raise ValueError('Pipeline journal missing or invalid')

    app = create_pipeline_app(PIPELINE, load=unavailable_journal, initial=snapshot())

    async def exercise():
        async with app.run_test(size=(48, 20)) as pilot:
            await pilot.pause()
            nodes = app.query_one('#nodes', Static)
            assert 'archive' in str(nodes.render())
            await pilot.press('r')
            await pilot.pause()
            assert 'archive' in str(nodes.render())
            note = str(app.query_one('#note', Static).render())
            assert 'Refresh failed (journal unchanged)' in note
            assert 'Pipeline journal missing or invalid' in note
            assert app._snapshot is not None
            assert app._snapshot['pipeline_id'] == PIPELINE

    asyncio.run(exercise())
