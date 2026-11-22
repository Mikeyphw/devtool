#!/usr/bin/env python3
"""GA-01 native source check; candidate import paths, no live writes."""
from __future__ import annotations
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if not (ROOT/'.devtool.toml').is_file():
    raise SystemExit('GA01_NATIVE_CANDIDATE_ROOT_UNVERIFIED')
sys.path.insert(0,str(ROOT))

import pytest

TESTS=(
    'tests/test_ga01_scoped_auth.py',
    'tests/test_github_app_m4.py',
    'tests/test_ga00_security_baseline.py',
    'tests/test_m7_credentials.py',
    'tests/test_m7_cli_surface.py',
    'tests/test_federation_app_auth.py',
    'tests/test_actions_00_01_github_provider.py',
    'tests/test_actions_02_workflow_dispatch.py',
    'tests/test_actions_03_run_lifecycle.py',
    'tests/test_actions_04_artifact_retrieval.py',
    'tests/test_actions_06_pipeline_dag.py',
    'tests/test_actions_ux02_handoff.py',
    'tests/test_actions_r04b_lineage_authority.py',
    'tests/test_delivery_m4_integration.py',
    'tests/test_g0b_delivery_fork_and_provider.py',
    'tests/test_m8_automation.py',
)
if __name__=='__main__':
    paths=[p for p in TESTS if (ROOT/p).is_file()]
    if len(paths)!=len(TESTS):
        raise SystemExit('GA01_NATIVE_DECLARED_TEST_MISSING')
    raise SystemExit(pytest.main(['-q',*TESTS]))
