from types import SimpleNamespace
import pytest
from devtool.cleanup_lifecycle import annotate_cleanup_specs, build_cleanup_ownership_view, compile_cleanup_plan, provider_cleanup_receipt_from_results
from devtool.models import CommandSpec, OperationResult

def evaluate(tmp_path, code, *, skip=False):
    spec=CommandSpec("clean",["npm","run","clean"],cwd=str(tmp_path),destructive=True,metadata={"runner":"node","cleanup_scope":"project"})
    actions=annotate_cleanup_specs([spec])
    view=build_cleanup_ownership_view(tmp_path,(),policy_generation="r19-provider")
    plan=compile_cleanup_plan(view,opaque_actions=actions)
    result=SimpleNamespace(returncode=code,skipped=skip,failure_kind="synthetic")
    return provider_cleanup_receipt_from_results(plan,actions,[result])

@pytest.mark.parametrize("code",[None,"unexpected",object(),True,False,"", "0.1"])
def test_nonterminal_or_malformed_returncode_never_passes(tmp_path,code):
    receipt=evaluate(tmp_path,code)
    assert receipt.status=="failed"
    assert receipt.results[0].status=="failed"

@pytest.mark.parametrize("code",[0,"0"])
def test_explicit_zero_still_passes(tmp_path,code):
    receipt=evaluate(tmp_path,code)
    assert receipt.status=="applied"

@pytest.mark.parametrize("code",[1,124,"3"])
def test_explicit_failure_still_fails(tmp_path,code):
    assert evaluate(tmp_path,code).status=="failed"

def test_skipped_stays_non_success(tmp_path):
    receipt=evaluate(tmp_path,0,skip=True)
    assert receipt.status=="partial"
