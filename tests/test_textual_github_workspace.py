"""GitHub API projection and canonical log preview negative/identity contracts."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import zipfile
import pytest

from devtool.textual_github_workspace import (
    ActionsSelection, GithubProjectionError, display_status,
    load_github_actions, read_github_log_preview, search_actions,
)


class API:
    def __init__(self):
        self.calls=[]
        self.workflows_value=[dict(id=9,name="Hosted Python",path=".github/workflows/test.yml",state="active")]
        self.runs_value=[dict(id=37,workflow_id=9,name="Hosted Python",status="completed",
                              conclusion="failure",head_sha="a"*40,head_branch="feature/ui",
                              run_attempt=2,repository={"full_name":"Mikeyphw/devtool"})]
        self.detail_value=dict(self.runs_value[0])
        self.jobs_value=[dict(id=88,name="Build and test",run_attempt=2,status="completed",
                              conclusion="failure",steps=[dict(name="Compile",status="completed",conclusion="success")])]
        self._token=None
    def workflows(self,slug):self.calls.append(("workflows",slug));return self.workflows_value
    def runs(self,slug,*,limit):self.calls.append(("runs",slug,limit));return self.runs_value
    def get_run(self,slug,run_id):self.calls.append(("get_run",slug,run_id));return self.detail_value
    def run_attempt_jobs(self,slug,run_id,attempt):
        self.calls.append(("run_attempt_jobs",slug,run_id,attempt));return self.jobs_value


def test_read_only_bounded_lists_and_status(tmp_path:Path):
    api=API()
    state=load_github_actions(tmp_path,selection=ActionsSelection("Mikeyphw/devtool"),client=api)
    assert state["read_only"] is True and state["release_qualified"] is False
    assert state["runs"][0]["url"]=="https://github.com/Mikeyphw/devtool/actions/runs/37"
    assert state["selected_run"] is None and state["jobs"] == []
    assert ("runs","Mikeyphw/devtool",30) in api.calls
    assert display_status(state["runs"][0])=="failure"
    assert display_status({"status":"in_progress","conclusion":"success"})=="in_progress"


def test_exact_attempt_selected_jobs_and_steps(tmp_path:Path):
    api=API()
    state=load_github_actions(tmp_path,selection=ActionsSelection("Mikeyphw/devtool",run_id=37),client=api)
    assert state["jobs_scope"]=="exact-run-attempt"
    assert ("run_attempt_jobs","Mikeyphw/devtool",37,2) in api.calls
    assert state["jobs"][0]["steps"][0]["conclusion"]=="success"


def test_attempt_or_repository_mismatch_refuses(tmp_path:Path):
    api=API();api.jobs_value[0]["run_attempt"]=1
    with pytest.raises(GithubProjectionError,match="different run attempt"):
        load_github_actions(tmp_path,selection=ActionsSelection("Mikeyphw/devtool",run_id=37),client=api)
    api=API();api.detail_value["repository"]={"full_name":"different/repo"}
    with pytest.raises(GithubProjectionError,match="different repository"):
        load_github_actions(tmp_path,selection=ActionsSelection("Mikeyphw/devtool",run_id=37),client=api)
    api=API();api.detail_value["id"]=555
    with pytest.raises(GithubProjectionError,match="identity mismatch"):
        load_github_actions(tmp_path,selection=ActionsSelection("Mikeyphw/devtool",run_id=37),client=api)


def test_missing_attempt_blocks_unsafe_jobs(tmp_path:Path):
    api=API();api.detail_value["run_attempt"]=None
    state=load_github_actions(tmp_path,selection=ActionsSelection("Mikeyphw/devtool",run_id=37),client=api)
    assert state["jobs_scope"]=="unavailable-no-attempt" and state["jobs"]==[]
    assert not any(row[0]=="run_attempt_jobs" for row in api.calls)


def test_step_bounds_input_validation_and_search():
    api=API();api.jobs_value[0]["steps"]=[{"name":f"step-{i}"} for i in range(400)]
    state=load_github_actions(Path("/tmp"),selection=ActionsSelection("Mikeyphw/devtool",run_id=37),client=api)
    assert len(state["jobs"][0]["steps"])==300 and state["jobs"][0]["steps_truncated"] is True
    for selection in ({"run_id":0},{"workflow_id":True},{"limit":101}):
        with pytest.raises(GithubProjectionError):ActionsSelection("Mikeyphw/devtool",**selection)
    rows=[{"name":"Build\x1b[31m","branch":"feature/ui"},{"name":"Lint","branch":"main"}]
    assert len(search_actions(rows,"build ui"))==1


def test_log_preview_uses_canonical_archive_without_extraction_or_signed_url(tmp_path:Path):
    api=API()
    zpath=tmp_path/"canonical.zip"
    with zipfile.ZipFile(zpath,"w") as z:
        z.writestr("build/1_test.txt","one\nsecond secret line\nthree\n")
        z.writestr("nested/2_run.txt","runner output\n")
    with patch("devtool.github_actions.resolve_repository",return_value="Mikeyphw/devtool"),\
         patch("devtool.actions_runs.remote_logs",return_value={"archive":str(zpath)} ) as logs:
        preview=read_github_log_preview(tmp_path,run_id=37,attempt=2,head_sha="a"*40,client=api)
    assert preview["run_attempt"]==2 and preview["qualified_artifact"] is False
    assert "runner output" in preview["lines"]
    assert all("https://" not in item for item in preview["lines"])
    assert logs.call_count==1
    assert len([row for row in api.calls if row[0]=="get_run"])==2
    assert not (tmp_path/"nested").exists()


def test_log_preview_refuses_run_race_before_canonical_download(tmp_path:Path):
    api=API();api.detail_value["run_attempt"]=3
    with patch("devtool.github_actions.resolve_repository",return_value="Mikeyphw/devtool"),\
         patch("devtool.actions_runs.remote_logs") as logs:
        with pytest.raises(GithubProjectionError,match="attempt changed"):
            read_github_log_preview(tmp_path,run_id=37,attempt=2,head_sha="a"*40,client=api)
        assert not logs.called


def test_log_preview_refuses_run_race_after_canonical_download(tmp_path:Path):
    api=API()
    zpath=tmp_path/"canonical.zip"
    with zipfile.ZipFile(zpath,"w") as z:z.writestr("job.txt","hello")
    i=[0]
    def race(*_args):
        i[0]+=1
        r=dict(api.detail_value)
        if i[0]==2:r["run_attempt"]=3
        return r
    api.get_run=race
    with patch("devtool.github_actions.resolve_repository",return_value="Mikeyphw/devtool"),\
         patch("devtool.actions_runs.remote_logs",return_value={"archive":str(zpath)}):
        with pytest.raises(GithubProjectionError,match="attempt changed"):
            read_github_log_preview(tmp_path,run_id=37,attempt=2,head_sha="a"*40,client=api)


def test_run_control_is_identity_bound_and_uses_canonical_journal(tmp_path:Path):
    from devtool.textual_github_workspace import control_github_run
    api=API()
    with patch("devtool.github_actions.resolve_repository",return_value="Mikeyphw/devtool"),\
         patch("devtool.actions_runs.control_run",return_value={"control_id":"1"*32,"state":"request-accepted"}) as send:
        answer=control_github_run(tmp_path,run_id=37,attempt=2,head_sha="a"*40,action="rerun",client=api)
        assert answer["state"] == "request-accepted"
        send.assert_called_once_with(api,"Mikeyphw/devtool",37,"rerun")
        send.reset_mock()
        api.detail_value=dict(api.detail_value,run_attempt=3)
        with pytest.raises(GithubProjectionError,match="Run changed"):
            control_github_run(tmp_path,run_id=37,attempt=2,head_sha="a"*40,action="rerun",client=api)
        send.assert_not_called()


def test_dispatch_delegates_source_binding_and_duplicate_prevention(tmp_path:Path):
    from devtool.textual_github_workspace import dispatch_github_workflow
    api=API()
    with patch("devtool.github_actions.resolve_repository",return_value="Mikeyphw/devtool"),\
         patch("devtool.actions_dispatch.local_head_for_dispatch",return_value="a"*40),\
         patch("devtool.actions_dispatch.dispatch_workflow",return_value={"request_id":"1"*32,"dispatched":True}) as send:
        response=dispatch_github_workflow(tmp_path,workflow_id=9,ref="feature/ui",
                                          inputs={"debug":True},client=api)
        assert response["dispatched"] is True
        send.assert_called_once_with(api,"Mikeyphw/devtool","9","feature/ui",{"debug":"true"},
                                     execute=True,expected_source_sha="a"*40,resume_if_existing=True)
        send.reset_mock()
        with pytest.raises(GithubProjectionError,match="JSON object of scalars"):
            dispatch_github_workflow(tmp_path,workflow_id=9,ref="feature/ui",inputs={"secret":None},client=api)
        send.assert_not_called()
