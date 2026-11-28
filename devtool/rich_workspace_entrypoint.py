"""Rich TUI adapter. Consume existing native projections; never own operations."""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


def run_rich_workspace(args: Any, *, repo_root: Any, console: Any = None) -> int:
    from .ui_core.shortcuts import normalize_tui_page
    from .operator_workspace import run_operator_workspace
    from .operator_rich import browse_operator
    root = Path(repo_root)
    page, notice = normalize_tui_page(getattr(args, 'page', 'dashboard'))
    artifact = str(getattr(args, 'artifact', '') or '').strip()
    if artifact and page == 'dashboard':
        page = 'overlays'
    if notice:
        print(notice, file=sys.stderr)
    try:
        tabs = {'dashboard': None, 'github': 'actions', 'runs': 'runs',
                'workers': 'ssh', 'validation': 'validation'}
        if page in tabs:
            shown = run_operator_workspace(root, None, args=args, console=console,
                                           initial_tab=tabs[page])
        else:
            from .textual_control_center_runtime import CanonicalControlCenterProvider
            provider = CanonicalControlCenterProvider(root, startup_artifact=artifact)
            def project():
                native = provider.snapshot()
                value = native.get(page, [])
                if page == 'logs':
                    value = value.get('lines', []) if isinstance(value, dict) else []
                if not isinstance(value, (list, tuple, dict)):
                    raise ValueError('Unexpected native page shape')
                return {'repo_root': str(root), 'items': value,
                        'owns_truth': False, 'message': 'Read-only native projection'}
            # Generic items are also the browser's source of selection identity.
            shown = browse_operator(page.upper(), project(), args=args,
                                    refresh=project, console=console)
        if shown:
            return 0
        print('Devtool Rich workspace requires an interactive TTY; use --once or explicit --backend textual.', file=sys.stderr)
    except (RuntimeError, ValueError, OSError) as exc:
        print('Devtool Rich workspace refused ('+type(exc).__name__+')', file=sys.stderr)
    return 2
