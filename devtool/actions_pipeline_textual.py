"""G3: interactive, read-only Textual view of the canonical ACTIONS-06 journal.

The journal and ACTIONS-07 projector own all state. UI refresh never dispatches,
resumes, cancels or supplies a new execution authority. Safe for Termux phones.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from .actions_presentation import project_pipeline

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def _safe_text(value: Any, limit: int = 180) -> str:
    # Untrusted remote/journal diagnostic output is never rendered as Rich markup.
    return _CONTROL.sub(" ", _ESCAPE.sub("", str(value or ""))).strip()[:limit]


def pipeline_screen_lines(snapshot: Mapping[str, Any]) -> dict[str, str]:
    """Pure projection shared by Textual tests and narrow terminal fallback."""
    view = project_pipeline(snapshot)
    header = f"{_safe_text(view['name'], 56) or 'Pipeline'} · {view['state'].upper()}"
    identity = (f"Pipeline: {view['pipeline_id']}\n"
                f"Repository: {_safe_text(view['repository'], 90)}\n"
                f"Source: {view['source_sha']}")
    symbols = {'success':'✓', 'waiting':'◷', 'running':'▶', 'failure':'✕',
               'blocked':'!', 'uncertain':'?', 'cancelled':'—', 'pending':'·'}
    rows=[]
    for node in view['nodes']:
        state = node['state']
        rows.append(f"{symbols[state]} {state.upper():9s} {_safe_text(node['id'], 50)}  [{_safe_text(node['kind'], 32)}]")
        if node['detail']:
            rows.append(f"    {_safe_text(node['detail'])}")
    note = ('Stop requested. Existing remote jobs are NOT cancelled.' if view['cancel_requested']
            else 'Blocked/uncertain: manual reconciliation required; no automatic replay.' if view['state']=='blocked'
            else 'Waiting for remote evidence; no build success is inferred.' if view['state']=='waiting'
            else 'Refresh reads the journal only. Run/resume require separate reviewed CLI commands.')
    return {'header':header,'identity':identity,'nodes':'\n'.join(rows) or '(no nodes)',
            'note':note+'\nFormal release: NOT QUALIFIED', 'state':view['state']}


def create_pipeline_app(identity: str, *, load: Callable[[], Mapping[str, Any]] | None = None,
                        initial: Mapping[str, Any] | None = None):
    """Build an isolated Textual App; import Textual only in the interactive path."""
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import VerticalScroll
    from textual.widgets import Header, Footer, Static
    from rich.text import Text
    from .actions_pipeline import pipeline_status

    get_state = load if load is not None else lambda: pipeline_status(identity)

    class PipelineInspector(App):
        TITLE = 'Devtool · Actions CI'
        SUB_TITLE = 'Pipeline inspector · read-only'
        CSS = '''
        Screen { layout: vertical; }
        #headline { text-style: bold; height: auto; padding: 1 1 0 1; }
        #identity { height: auto; color: $text-muted; padding: 0 1; }
        #nodes-scroll { height: 1fr; padding: 1 1; }
        #nodes { height: auto; }
        #note { height: auto; padding: 1 1; color: $warning; }
        '''
        BINDINGS = [Binding('r', 'refresh', 'Refresh journal'), Binding('q', 'quit', 'Close')]

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Static('', id='headline')
            yield Static('', id='identity')
            with VerticalScroll(id='nodes-scroll'):
                yield Static('', id='nodes')
            yield Static('', id='note')
            yield Footer()

        def on_mount(self) -> None:
            self.apply_snapshot(initial if initial is not None else get_state())

        def apply_snapshot(self, snapshot: Mapping[str, Any]) -> None:
            if str(snapshot.get('pipeline_id')) != identity:
                raise ValueError('Pipeline inspector identity changed during refresh')
            rendered=pipeline_screen_lines(snapshot)
            for field, selector in (('header','#headline'),('identity','#identity'),
                                    ('nodes','#nodes'),('note','#note')):
                self.query_one(selector, Static).update(Text(rendered[field]))

        def action_refresh(self) -> None:
            try:
                self.apply_snapshot(get_state())
            except Exception as exc:
                # Keep last trusted snapshot visible; no automatic recovery mutation.
                self.query_one('#note', Static).update(
                    'Refresh failed (journal unchanged): '+_safe_text(str(exc),120))

    return PipelineInspector()


def run_pipeline_app(payload: Mapping[str, Any]) -> None:
    identity = str(payload.get('pipeline_id') or '')
    create_pipeline_app(identity, initial=payload).run()
