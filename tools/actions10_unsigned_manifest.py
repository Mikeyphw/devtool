#!/usr/bin/env python3
"""Emit source/run-bound unsigned APK metadata on GitHub (no secrets)."""
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devtool.actions_unsigned import emit_manifest
from devtool.github_actions import GitHubActionsError

if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != 'emit':
        sys.exit('usage: actions10_unsigned_manifest.py emit APK MANIFEST')
    try:
        emit_manifest(Path(sys.argv[2]), Path(sys.argv[3]), dict(os.environ))
    except GitHubActionsError as exc:
        sys.exit(f'unsigned candidate validation failed: {exc}')

# ACTIONS-10: retained V3 integration included in repair commit.
