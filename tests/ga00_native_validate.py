#!/usr/bin/env python3
"""GA-00 native artifact validation: explicitly prefer the staged worktree.

The trusted native runtime is intentionally a *different* Devtool checkout.
Validation must import the candidate's updated production modules, not the
baseline runtime selected by M2. No apply, CI or remote mutation occurs here.
"""
from __future__ import annotations

from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if not (SOURCE_ROOT / '.devtool.toml').exists():
    raise SystemExit('GA00_VALIDATION_SOURCE_ROOT_UNVERIFIED')
sys.path.insert(0, str(SOURCE_ROOT))

import pytest

TESTS = (
    'tests/test_ga00_security_baseline.py',
    'tests/test_ga00_p09_owner_projection.py',
    'tests/test_actions_00_01_github_provider.py',
    'tests/test_w2_signing_profile.py',
    'tests/test_ga00_actions_secret_integration.py',
    'tests/test_ga00_followup_security.py',
    'tests/test_github_app_m4.py',
    'tests/test_federation_app_auth.py',
    'tests/test_delivery_m4_integration.py',
    'tests/test_actions_09_release_setup.py',
    'tests/test_actions_08_release_security.py',
    'tests/test_actions_10_unsigned_candidate.py',
    'tests/test_android_unsigned_first_hotfix.py',
    'tests/test_rm15_hf02_release_unification.py',
    'tests/test_life_x04_lifecycle_convergence.py',
    'tests/test_life_g3_cleanup_receipt_contract.py',
    'tests/test_native_delivery_contract_v3.py',
    'tests/test_native_delivery_m5.py',
    'tests/test_actions_05_android_builds.py',
    'tests/test_actions_g04_android_aab_candidate.py',
    'tests/test_actions_g05_polyglot_ci.py',
)
if __name__ == '__main__':
    raise SystemExit(pytest.main(['-q', *TESTS]))
