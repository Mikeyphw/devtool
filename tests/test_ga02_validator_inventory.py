"""DT-20261124-30: native/hosted GA-02 must run both security suites."""
from __future__ import annotations
import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REQUIRED={
    'tests/test_ga02_installation_relationships.py',
    'tests/test_ga02_federation_source_guards.py',
    'tests/test_ga02_live_registry.py',
    'tests/test_ga02_validator_inventory.py',
}

def _names(text: str) -> tuple[str,...]:
    tree=ast.parse(text)
    nodes=[n.value for n in tree.body if isinstance(n,ast.Assign)
           and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and
           n.targets[0].id=='TESTS']
    assert len(nodes)==1, 'one auditable, literal native TESTS inventory is mandatory'
    value=ast.literal_eval(nodes[0])
    assert type(value) is tuple and all(type(s) is str for s in value)
    assert len(set(value))==len(value), 'duplicate tests silently double-count acceptance'
    return value

def test_native_validator_includes_existing_security_modules():
    source=(ROOT/'tests/ga02_native_validate.py').read_text(encoding='utf-8')
    names=_names(source)
    assert REQUIRED.issubset(set(names)), 'the GA02 source-bound validator must run its security modules'
    assert 'GA02_NATIVE_REQUIRED_TEST_MISSING' in source
    assert "pytest.main(['-q',*TESTS])" in source
    assert all((ROOT/p).is_file() for p in names), 'all declared tests must exist'

def test_inventory_drops_are_rejected():
    source=(ROOT/'tests/ga02_native_validate.py').read_text(encoding='utf-8')
    for name in sorted(REQUIRED):
        mutated=source.replace("    '"+name+"',\n",'',1)
        assert mutated!=source
        assert not REQUIRED.issubset(set(_names(mutated)))

def test_hosted_ga_source_acceptance_runs_native_validator():
    workflow=(ROOT/'.github/workflows/devtool-ga-source-acceptance.yml').read_text(encoding='utf-8')
    assert 'tests/w3_native_validate.py --profile full' in workflow
    unified = _names((ROOT/'tests/w3_native_validate.py').read_text(encoding='utf-8'))
    assert set(_names((ROOT/'tests/ga02_native_validate.py').read_text(encoding='utf-8'))).issubset(unified)
