from pathlib import Path
from devtool.models import ProjectConfig, TargetConfig, WorkflowConfig, WorkflowStepRef
from devtool.test_model import TestSpec as CanonicalSpec
from devtool.workflow_runtime import build_workflow_specs, workflow_runner_kinds
from devtool.workflow_config import parse_workflow_step


def _config(tmp_path):
    test=CanonicalSpec("unit","Unit","pytest",("python","-m","pytest","-q"),cwd=str(tmp_path))
    target=TargetConfig(name="app",runner="python")
    target.workflows["validate"]=WorkflowConfig("validate",(WorkflowStepRef("test:unit","test","unit"),))
    return ProjectConfig(repo_root=tmp_path,kind="python",selected_target="app",targets={"app":target},tests={"unit":test},project_root=str(tmp_path))


def test_workflow_test_node_uses_declared_testspec(tmp_path):
    cfg=_config(tmp_path); specs=build_workflow_specs(cfg,"validate")
    assert len(specs)==1 and specs[0].argv==["python","-m","pytest","-q"]
    assert specs[0].metadata["test_spec_id"]=="unit"
    assert specs[0].metadata["workflow_ref_kind"]=="test"
    assert workflow_runner_kinds(cfg,"validate")==("pytest",)


def test_workflow_parser_rejects_unknown_test(tmp_path):
    try:
        parse_workflow_step("test:missing",target_name="app",workflow_name="validate",jobs={},targets={},supported_runners={"python"},tests={})
    except SystemExit as exc:
        assert "unknown test 'missing'" in str(exc)
    else: raise AssertionError("expected failure")


def test_config_loader_resolves_declared_test_inside_target_workflow(tmp_path):
    from devtool.config import load_project_config
    from devtool.runners import build_validation_specs
    (tmp_path/".devtool.toml").write_text('''
[project]
runner = "python"
default_target = "app"

[[test]]
id = "unit"
name = "Unit"
runner = "pytest"
command = ["python", "-m", "pytest", "-q", "tests/unit"]

[targets.app]
runner = "python"
root = "."

[targets.app.workflows]
validate = ["test:unit"]
''')
    cfg=load_project_config(str(tmp_path),None)
    specs=build_validation_specs(cfg)
    assert len(specs)==1
    assert specs[0].argv==["python","-m","pytest","-q","tests/unit"]
    assert specs[0].metadata["test_spec_id"]=="unit"
    assert specs[0].metadata["workflow_ref_kind"]=="test"
