#!/usr/bin/env python3
"""GA-02 focused native validation; full 468-case regression run independently.

Native apply uses its own committed-state locks. Keep this validation bounded to
production paths touched by GA-02 and their direct credential/federation peers;
full Delivery/Actions/GA-00/GA-01 regression is a separate exact-source test gate.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if not (ROOT/'.devtool.toml').is_file():
    raise SystemExit('GA02_NATIVE_CANDIDATE_ROOT_UNVERIFIED')
sys.path.insert(0,str(ROOT))
import pytest
TESTS=(
    'tests/test_ga02_installation_relationships.py',
    'tests/test_ga02_federation_source_guards.py',
    'tests/test_ga02_live_registry.py',
    'tests/test_ga02_validator_inventory.py',
    'tests/test_ga01_scoped_auth.py',
    'tests/test_github_app_m4.py',
    'tests/test_federation_app_auth.py',
    'tests/test_frx02_federation_config.py',
    'tests/test_frx03_federation_discovery.py',
    'tests/test_delivery_session.py',
)
if __name__=='__main__':
    if any(not (ROOT/p).is_file() for p in TESTS):
        raise SystemExit('GA02_NATIVE_REQUIRED_TEST_MISSING')
    raise SystemExit(pytest.main(['-q',*TESTS]))
