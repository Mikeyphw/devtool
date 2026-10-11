"""ACTIONS-00/01 boundaries: discovery only, no GitHub mutations."""
from __future__ import annotations
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from devtool.cli import build_parser, main
from devtool.github_actions import (
    GitHubActionsClient, GitHubActionsError, actions_baseline, parse_github_remote,
    resolve_repository, resolve_token,
)


class Response:
    status = 200
    def __init__(self, payload, headers=None):
        self.stream = io.BytesIO(json.dumps(payload).encode())
        self.headers = headers or {}
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return None
    def read(self, n): return self.stream.read(n)


class FakeOpener:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
    def open(self, req, timeout):
        self.requests.append(req)
        if not self.replies:
            raise AssertionError("unplanned network request")
        response = self.replies.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_baseline_is_read_only_and_phase_bounded():
    baseline = actions_baseline()
    assert baseline["phase"] == "ACTIONS-00+01+02+03+04"
    assert not baseline["remote_execution"]
    assert "run (explicit --yes only)" in baseline["mutation_operations"]
    assert baseline["token_persistence"] is False
    assert "run" in baseline["operations"]


@pytest.mark.parametrize("remote,expected", [
    ("git@github.com:Owner/repo.git", "Owner/repo"),
    ("https://github.com/Owner/repo.git", "Owner/repo"),
    ("ssh://git@github.com/Owner/repo", "Owner/repo"),
    ("git://github.com/Owner/repo", "Owner/repo"),
])
def test_supported_remote_identity(remote, expected):
    assert parse_github_remote(remote) == expected


@pytest.mark.parametrize("remote", [
    "https://example.com/owner/repo", "https://github.com.evil.tld/owner/repo",
    "https://user:secret@github.com/owner/repo.git", "https://user@github.com/owner/repo",
    "git@github.com:owner/repo/extra", "https://github.com/a/../b",
    "https://github.com:443/owner/repo", "https://github.com/owner/repo?x=1",
    "file:///tmp/private", "https://github.com/owner/.git",
])
def test_unsafe_remotes_fail_closed_without_echo(remote):
    with pytest.raises(GitHubActionsError) as exc:
        parse_github_remote(remote)
    assert "secret" not in str(exc.value)
    assert "evil.tld" not in str(exc.value)


def test_no_origin_requires_explicit_slug(tmp_path):
    with pytest.raises(GitHubActionsError, match="origin"):
        resolve_repository("", tmp_path)
    assert resolve_repository("owner/repo", tmp_path) == "owner/repo"
    with pytest.raises(GitHubActionsError):
        resolve_repository("owner/../other", tmp_path)


def test_token_env_precedence_and_gh_fallback(monkeypatch):
    assert resolve_token("none", environ={}) == (None, "anonymous")
    assert resolve_token("auto", environ={"GITHUB_TOKEN": "g", "GH_TOKEN": "h"}) == ("h", "environment")
    with pytest.raises(GitHubActionsError):
        resolve_token("env", environ={})
    monkeypatch.setattr("devtool.github_actions.shutil.which", lambda v: "/usr/bin/gh")
    class Result:
        returncode = 0
        stdout = "test-fake-token\n"
        stderr = ""
    monkeypatch.setattr("devtool.github_actions.subprocess.run", lambda *a, **k: Result())
    assert resolve_token("gh", environ={}) == ("test-fake-token", "github-cli")


def test_api_only_get_fixed_origin_and_secret_header_not_payload():
    opener = FakeOpener([Response({"full_name":"Owner/repo","id":7,"private":True,"default_branch":"main"})])
    client = GitHubActionsClient("supersecret", opener=opener)
    result = client.repository("Owner/repo")
    assert result["id"] == 7 and result["private"] is True
    assert "supersecret" not in json.dumps(result)
    assert opener.requests[0].get_method() == "GET"
    assert opener.requests[0].full_url == "https://api.github.com/repos/Owner/repo"
    assert opener.requests[0].get_header("Authorization") == "Bearer supersecret"
    assert opener.requests[0].get_header("X-github-api-version") == "2022-11-28"


def test_identity_mismatch_rejected():
    opener = FakeOpener([Response({"full_name":"attacker/repo"})])
    with pytest.raises(GitHubActionsError, match="identity"):
        GitHubActionsClient(opener=opener).repository("owner/repo")


def test_bounded_paginated_workflow_discovery_and_query():
    a = {"id":1,"name":"CI","path":".github/workflows/ci.yml","state":"active"}
    b = {"id":2,"name":"Tests","path":".github/workflows/test.yml","state":"active"}
    url = "https://api.github.com/repos/o/r/actions/workflows?page=2&per_page=100"
    opener = FakeOpener([Response({"workflows":[a]}, {"Link":f'<{url}>; rel="next"'}),Response({"workflows":[b]})])
    assert [x["id"] for x in GitHubActionsClient(opener=opener).workflows("o/r")] == [1, 2]
    assert all(req.get_method() == "GET" for req in opener.requests)


@pytest.mark.parametrize("url", [
    "https://evil.org/repos/o/r/actions/workflows?page=2",
    "http://api.github.com/repos/o/r/actions/workflows?page=2",
    "https://api.github.com/repos/other/repo/actions/workflows?page=2",
    "https://api.github.com/repos/o/r/actions/workflows?token=secret",
    "https://api.github.com/repos/o/r/actions/workflows#fragment",
])
def test_pagination_egress_rejected(url):
    opener = FakeOpener([Response({"workflows":[]}, {"Link":f'<{url}>; rel="next"'})])
    with pytest.raises(GitHubActionsError, match="scope"):
        GitHubActionsClient("secret", opener=opener).workflows("o/r")
    assert len(opener.requests) == 1


def test_pagination_cycle_and_cap_fail_closed():
    url = "https://api.github.com/repos/o/r/actions/workflows?per_page=100"
    opener = FakeOpener([Response({"workflows":[]}, {"Link":f'<{url}>; rel="next"'})])
    with pytest.raises(GitHubActionsError, match="loop"):
        GitHubActionsClient(opener=opener).workflows("o/r")


def test_workflow_identifier_validated_before_request():
    c = GitHubActionsClient(opener=FakeOpener([]))
    for value in ("../../secret", "a?token=secret", "ci.yml/dispatches", "0"):
        with pytest.raises(GitHubActionsError):
            c.workflow("o/r", value)
    response = Response({"id":123,"name":"CI"})
    opener = FakeOpener([response])
    assert GitHubActionsClient(opener=opener).workflow("o/r", "ci.yml")["id"] == 123


@pytest.mark.parametrize("status,expected",[(401,"authentication"),(403,"denied"),(404,"not found"),(429,"HTTP 429")])
def test_api_errors_do_not_disclose_body_or_token(status,expected):
    url="https://api.github.com/repos/o/r"
    exc=HTTPError(url,status,"secret-token",{"X-RateLimit-Remaining":"100"},io.BytesIO(b'{"message":"secret-token"}'))
    client=GitHubActionsClient("secret-token",opener=FakeOpener([exc]))
    with pytest.raises(GitHubActionsError) as raised:
        client.repository("o/r")
    assert expected.lower() in str(raised.value).lower()
    assert "secret-token" not in str(raised.value)


def test_redirect_handler_never_forwards_authorization():
    from devtool.github_actions import _NoRedirect
    req=type("Req",(),{})()
    assert _NoRedirect().redirect_request(req,None,302,"Found",{},"https://evil.org") is None


def test_malformed_api_response_rejected():
    opener=FakeOpener([Response({"workflows":{}})])
    with pytest.raises(GitHubActionsError,match="shape"):
        GitHubActionsClient(opener=opener).workflows("o/r")


def test_canonical_cli_commands_and_no_dispatch(capsys):
    parser=build_parser()
    args=parser.parse_args(["actions","workflow","ci.yaml","--github-repo","o/r","--auth","none"])
    assert args.command=="actions" and args.actions_command=="workflow"
    assert args.workflow_id=="ci.yaml" and args.github_repo=="o/r"
    with pytest.raises(SystemExit):
        parser.parse_args(["actions","run","ci.yaml"])  # --ref is required
    assert main(["actions","baseline"]) == 0
    assert '"remote_execution": false' in capsys.readouterr().out.lower()
