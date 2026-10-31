"""R36-R1: Rich display is a safe, separate presentation surface."""
from __future__ import annotations

from pathlib import Path

import pytest
from rich.text import Text

from devtool.job_ui import styled_job_line, CompactLogBuffer, readable_job_line


def test_trusted_rich_style_tags_render_as_text_not_raw_markup():
    styled = styled_job_line('[bold green]✓ completed[/bold green]')
    assert isinstance(styled, Text)
    assert styled.plain == '✓ completed'
    assert styled.spans and 'green' in str(styled.spans[0].style)
    assert styled_job_line('[bold][cyan]working[/cyan][/bold]').plain == 'working'
    assert styled_job_line('[yellow]warn[/]').plain == 'warn'


def test_bracket_path_never_triggers_markup_parser_or_disappears():
    cases = [
        '6.67s tests/test_example.py::test_access[/etc/passwd] PASSED',
        '/storage/emulated/0/Download/[release].zip',
        'ERROR: closing tag [/etc/passwd] does not match any open tag',
        '[green]incomplete',
        '[red]oops[/cyan]',
        '[link=https://bad.example]not a link[/link]',
        'foo [green]done[/green] then [/etc/passwd]',
    ]
    for value in cases:
        styled = styled_job_line(value)
        assert isinstance(styled, Text)
        # Unknown or malformed text must remain on screen, not cause MarkupError.
        assert '[/etc/passwd]' in styled.plain if '[/etc/passwd]' in value else bool(styled.plain)


def test_ansi_and_no_color_are_honored_without_control_injection():
    assert styled_job_line('\x1b[1;32mPASS\x1b[0m').plain == 'PASS'
    assert styled_job_line('\x1b]0;EVIL\x07done').plain == 'done'
    assert styled_job_line('[bold green]✓ done[/bold green]', no_color=True).plain == '✓ done'
    assert not styled_job_line('[bold green]✓ done[/bold green]', no_color=True).spans


def test_raw_log_keeps_unmodified_original_markup_authority():
    source = '[bold green]source status[/bold green]'
    raw = CompactLogBuffer(50)
    raw.append(source)
    assert raw.raw[-1] == source
    # Rendering happens only on demand; no logfile rewrite and no status claim.
    assert styled_job_line(raw.raw[-1]).plain == 'source status'
    assert readable_job_line('test[/etc/passwd]') == 'test[/etc/passwd]'


def test_every_dynamic_textual_surface_routes_through_safe_rich_text():
    root=Path(__file__).resolve().parents[1]/'devtool'
    apply=(root/'textual_apply_host.py').read_text()
    normal=(root/'execution_presentation.py').read_text()
    compact=(root/'job_ui.py').read_text()
    assert 'Static).update(styled_job_line(text))' in apply
    assert 'Static).update(styled_job_line(fit_middle(phase_line, width)))' in apply
    assert 'RichLog).write(styled_job_line(payload))' in normal
    assert 'Static).update(styled_job_line(view["detail"]))' in compact


def test_real_textual_static_accepts_literal_markup_error_and_rendered_rich_tags():
    textual = pytest.importorskip('textual')
    from textual.app import App, ComposeResult
    from textual.widgets import Static
    import asyncio

    class TextApp(App):
        def compose(self) -> ComposeResult:
            yield Static('',id='test')

    async def exercise():
        app=TextApp()
        async with app.run_test(size=(60,12)) as pilot:
            widget=app.query_one('#test',Static)
            widget.update(styled_job_line('time test_name[/etc/passwd] PASSED'))
            await pilot.pause()
            # Rendering the Rich Text object must not invoke Textual's markup parser.
            assert widget.render() is not None
            widget.update(styled_job_line('[bold green]done[/bold green]'))
            await pilot.pause()
            assert widget.render() is not None
    asyncio.run(exercise())
