from __future__ import annotations
from .artifacts import artifact_validation_tests
from .config import load_project_config
from .models import CommandSpec, OperationResult, Issue
from .runners import run_specs
from .test_declarations import command_specs

def run_artifact_tests(repo_root,manifest,*,verbose=False,dry_run=False,log_path=None,tasker_progress=None):
    declarations=artifact_validation_tests(manifest)
    if not declarations: return [],""
    config=load_project_config(str(repo_root),None,allow_ambiguous_target=True)
    specs=[]
    for d in declarations:
        if "ref" in d: specs.extend(command_specs(config,[d["ref"]])); continue
        cwd=d.get("cwd") or str(config.project_root or config.repo_root)
        specs.append(CommandSpec(name=f"artifact-test:{d['id']}",argv=list(d["command"]),cwd=cwd,env=dict(config.env),metadata={"test_spec_id":d["id"],"test_runner":d["runner"],"runner":d["runner"],"canonical_test":True,"artifact_validation_test":True,"required":d["required"]}))
    return run_specs(config,specs,verbose=verbose,dry_run=dry_run,continue_on_error=True,log_path=log_path,tasker_progress=tasker_progress)
