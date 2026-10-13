"""ACTIONS-00/01/02/03 command adapter; mutation gated by explicit --yes."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Any
from .github_actions import GitHubActionsError, actions_cli


def run_actions_command(args: Any) -> int:
    command = str(getattr(args, 'actions_command', '') or '')
    subcommand = str(getattr(args, 'android_command', '') or '')
    streaming = (os.environ.get('DEVTOOL_ACTIONS_STREAM') == '1'
                 and (command == 'watch' or (command == 'android' and subcommand == 'unsigned-run')))
    callback = None
    if streaming:
        from .actions_live_progress import encode_observation
        def callback(observation: dict[str, Any]) -> None:
            print(encode_observation(observation), flush=True)
    try:
        root = Path(str(getattr(args, "repo", "") or Path.cwd()))
        payload = (actions_cli(args, root, observation_callback=callback) if streaming
                   else actions_cli(args, root))
    except GitHubActionsError as exc:
        print(f"GitHub Actions: {exc}", file=sys.stderr)
        return 1
    if streaming:
        result = payload.get('monitor', payload)
        last = result.get('last', {}) if isinstance(result, dict) else {}
        remote = last.get('remote', {}) if isinstance(last, dict) else {}
        print(f"GitHub run {remote.get('id', '?')}: {remote.get('conclusion') or remote.get('status') or 'unknown'}")
        if remote.get('html_url'):
            print(f"Run: {remote['html_url']}")
        if last.get('source_binding') == 'not-confirmed':
            print('WARNING: run source SHA differs from dispatch receipt', file=sys.stderr)
    elif bool(getattr(args, "json", False)) or bool(getattr(args, "_devtool_json", False)):
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    elif (str(getattr(args, 'actions_command', '')) == 'release'
          and str(getattr(args, 'release_command', '')) in ('setup', 'readiness')
          and sys.stdout.isatty()):
        from .actions_presentation import release_setup_text
        print(release_setup_text(payload))
    elif (str(getattr(args, "actions_command", "")) == "pipeline"
          and str(getattr(args, "pipeline_command", "")) == "status"
          and sys.stdout.isatty() and sys.stdin.isatty() and "nodes" in payload):
        # G3: only an explicit read-only status query becomes a Textual screen.
        # Interactive output is never selected for JSON, pipes, CI or plain mode.
        use_textual = (not os.environ.get('CI')
                       and os.environ.get('DEVTOOL_EXECUTION_PRESENTATION', 'auto') != 'plain'
                       and os.environ.get('DEVTOOL_JOB_UI', 'auto') != 'plain')
        if use_textual:
            try:
                from .textual_execution_host import textual_available
                use_textual = textual_available()
            except (ImportError, ValueError):
                use_textual = False
        if use_textual:
            from .actions_pipeline_textual import run_pipeline_app
            run_pipeline_app(payload)
        else:
            from .actions_presentation import pipeline_text
            print(pipeline_text(payload))
    elif str(getattr(args, "actions_command", "")) == "pipeline" and sys.stdout.isatty() and "nodes" in payload:
        from .actions_presentation import pipeline_text
        print(pipeline_text(payload))
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    if (str(getattr(args, 'actions_command', '')) == 'release'
            and str(getattr(args, 'release_command', '')) == 'readiness'
            and not payload.get('ready', False)):
        return 1
    if (str(getattr(args, "actions_command", "")) == "pipeline"
            and payload.get("state") in ("blocked", "cancelled")
            and str(getattr(args, "pipeline_command", "")) != "cancel"):
        return 1
    if command == 'watch' or (command == 'android' and subcommand == 'unsigned-run' and streaming):
        result = payload.get('monitor', payload)
        if not result.get('complete'):
            return 2  # Deadline reached; remote result still unknown.
        last = result.get('last', {})
        remote = last.get('remote', {})
        if remote.get('conclusion') != 'success' or last.get('source_binding') == 'not-confirmed':
            return 1
    return 0
