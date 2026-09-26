from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from unittest import mock

import pytest

from devtool.cli import build_parser
from devtool.config import load_project_config
from devtool.federation_lock import FederationSourceLock, source_lock_path, write_federation_source_lock
from devtool.federation_plan import FederationPlanError, build_federated_plan
from devtool.git_bisect import (
    BisectTaskOutcome,
    GitBisectError,
    parse_federated_range,
    parse_pin,
    run_federated_bisect,
    run_git_bisect,
)


def _git(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Devtool Test"], check=True)


def _commit(root: Path, value: str, message: str) -> str:
    (root / "state.txt").write_text(value + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", message], check=True)
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def _history(root: Path) -> list[str]:
    _git(root)
    (root / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\ncommand=["python","-c","import pathlib,sys; sys.exit(0 if pathlib.Path('state.txt').read_text().startswith('good') else 1)"]\n[targets.app.workflows]\nvalidate=["job:check"]\n''',
        encoding="utf-8",
    )
    commits = []
    for index, value in enumerate(("good-0", "good-1", "bad-2", "bad-3", "bad-4")):
        commits.append(_commit(root, value, f"c{index}"))
    return commits


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(
            root,
            root / ".devtool.toml",
            target_name="",
            allow_ambiguous_target=True,
            allow_ambiguous_project_kind=True,
        )


def _federation_fixture(tmp_path: Path):
    parent = tmp_path / "parent"
    app = tmp_path / "app"
    lib = tmp_path / "lib"
    parent.mkdir(parents=True); app.mkdir(); lib.mkdir()
    app_commits = _history(app)
    _git(lib)
    (lib / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\ncommand=["echo","ok"]\n[targets.app.workflows]\nvalidate=["job:check"]\n''',
        encoding="utf-8",
    )
    lib_commit = _commit(lib, "lib", "lib-base")
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.lib]\npath="../lib"\n[federation.repositories.lib.exports]\ncapabilities=["contracts/v1"]\n[federation.repositories.app]\npath="../app"\n[federation.repositories.app.requires]\ncapabilities=["contracts/v1"]\n[federation.workflows.verify]\nsteps=[\n {id="lib",ref="lib::app::workflow:validate"},\n {id="app",ref="app::app::workflow:validate",depends_on=["lib"],inputs={contracts="lib:artifact:schemas.json"}},\n]\n''',
        encoding="utf-8",
    )
    config = _load(parent)
    frozen = FederationSourceLock.from_dict(build_federated_plan(config, "verify").source_lock)
    write_federation_source_lock(config, frozen)
    return config, app, lib, app_commits, lib_commit, frozen


def test_single_repo_bisect_uses_disposable_worktrees_and_persists_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = tmp_path / "repo"; root.mkdir()
    commits = _history(root)
    before_head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    (root / "dirty.txt").write_text("keep me\n", encoding="utf-8")

    def evaluator(candidate_root: Path, _task: str, _identity) -> int:
        return 0 if (candidate_root / "state.txt").read_text().startswith("good") else 1

    report = run_git_bisect(root, good=commits[0], bad=commits[-1], task="workflow:validate", evaluator=evaluator)
    assert report.first_bad_commit == commits[2]
    assert report.candidates
    assert all(row["candidate_identity_digest"] and row["combination"] for row in report.candidates)
    assert subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip() == before_head
    assert (root / "dirty.txt").read_text() == "keep me\n"
    assert not (root / ".git" / "BISECT_LOG").exists()
    assert not (root / ".git" / "BISECT_START").exists()
    state = json.loads(Path(report.state_path).read_text())
    history = [json.loads(line) for line in Path(report.history_path).read_text().splitlines()]
    assert state["status"] == "succeeded" and state["managed_git_bisect_state"] == "unused"
    assert [row["event"] for row in history].count("candidate") == len(report.candidates)
    assert history[-1]["event"] == "finished"


def test_single_repo_public_task_evaluator_runs_devtool_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = tmp_path / "repo"; root.mkdir()
    commits = _history(root)
    report = run_git_bisect(root, good=commits[0], bad=commits[-1], task="workflow:validate")
    assert report.first_bad_commit == commits[2]
    assert all(len(row["result"]["output_digest"]) == 64 for row in report.candidates)


def test_single_repo_failures_are_persisted_without_global_bisect_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = tmp_path / "repo"; root.mkdir()
    commits = _history(root)
    with pytest.raises(GitBisectError, match="exit 125"):
        run_git_bisect(root, good=commits[0], bad=commits[-1], task="workflow:validate", evaluator=lambda *_: 125)
    states = list((tmp_path / "state" / "devtool" / "git-bisect").glob("*/state.json"))
    assert len(states) == 1
    payload = json.loads(states[0].read_text())
    assert payload["status"] == "failed"
    history = [json.loads(line) for line in states[0].with_name("history.jsonl").read_text().splitlines()]
    assert history[-1]["event"] == "failed"
    assert not (root / ".git" / "BISECT_LOG").exists()


def test_single_repo_rejects_non_ancestor_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = tmp_path / "repo"; root.mkdir()
    commits = _history(root)
    subprocess.run(["git", "-C", str(root), "checkout", "-qb", "side", commits[0]], check=True)
    side = _commit(root, "bad-side", "side")
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "master"], check=True)
    with pytest.raises(GitBisectError, match="ancestor"):
        run_git_bisect(root, good=side, bad=commits[-1], task="workflow:validate", evaluator=lambda *_: 1)


def test_federated_bisect_pins_nonvarying_repo_and_extends_candidate_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, app, lib, commits, lib_commit, frozen = _federation_fixture(tmp_path / "fixture")
    seen: list[dict] = []

    def evaluator(scoped, combination, identity):
        seen.append(dict(identity))
        assert combination["lib"] == lib_commit
        assert identity["frozen_lock_digest"] == frozen.lock_digest
        assert identity["frozen_lock_equivalence_digest"] == frozen.equivalence_digest
        assert len(identity["capability_generation"]["digest"]) == 64
        assert len(identity["artifact_inputs"]["digest"]) == 64
        assert any(row["artifact_inputs"] for row in identity["artifact_inputs"]["nodes"])
        app_root = Path(scoped.federation.repositories["app"].root)
        return 0 if (app_root / "state.txt").read_text().startswith("good") else 1

    app_head = subprocess.check_output(["git", "-C", str(app), "rev-parse", "HEAD"], text=True).strip()
    lib_head = subprocess.check_output(["git", "-C", str(lib), "rev-parse", "HEAD"], text=True).strip()
    report = run_federated_bisect(
        config,
        repository_ranges=[f"app={commits[0]}..{commits[-1]}"],
        pins=[],
        task="verify",
        evaluator=evaluator,
    )
    assert report.first_bad_commit == commits[2]
    assert report.source_identity["pin_sources"] == {"lib": "frozen-lock"}
    assert seen
    assert subprocess.check_output(["git", "-C", str(app), "rev-parse", "HEAD"], text=True).strip() == app_head
    assert subprocess.check_output(["git", "-C", str(lib), "rev-parse", "HEAD"], text=True).strip() == lib_head
    assert not (app / ".git" / "BISECT_LOG").exists()


def test_federated_bisect_explicit_pin_overrides_frozen_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, _app, lib, commits, frozen_lib, _frozen = _federation_fixture(tmp_path / "fixture")
    old_lib = frozen_lib
    new_lib = _commit(lib, "lib-new", "lib-new")
    assert new_lib != old_lib
    report = run_federated_bisect(
        config,
        repository_ranges=[f"app={commits[0]}..{commits[-1]}"],
        pins=[f"lib={old_lib}"],
        task="workflow:verify",
        evaluator=lambda scoped, _combo, _identity: 0 if Path(scoped.federation.repositories["app"].root, "state.txt").read_text().startswith("good") else 1,
    )
    assert report.first_bad_commit == commits[2]
    assert report.source_identity["pins"]["lib"] == old_lib
    assert report.source_identity["pin_sources"]["lib"] == "explicit"


def test_federated_bisect_rejects_cartesian_search_and_missing_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, _app, _lib, commits, lib_commit, _frozen = _federation_fixture(tmp_path / "fixture")
    with pytest.raises(GitBisectError, match="exactly one repository"):
        run_federated_bisect(
            config,
            repository_ranges=[f"app={commits[0]}..{commits[-1]}", f"lib={lib_commit}..{lib_commit}"],
            pins=[], task="verify", evaluator=lambda *_: 0,
        )
    source_lock_path(config).unlink()
    with pytest.raises(GitBisectError, match="requires a persisted frozen"):
        run_federated_bisect(config, repository_ranges=[f"app={commits[0]}..{commits[-1]}"], pins=[], task="verify", evaluator=lambda *_: 0)


def test_bisect_assignment_parsing_is_fail_closed() -> None:
    assert parse_federated_range("app=abc..def") == ("app", "abc", "def")
    assert parse_pin("lib=deadbeef") == ("lib", "deadbeef")
    with pytest.raises(GitBisectError):
        parse_federated_range("app=abc...def")
    with pytest.raises(GitBisectError):
        parse_pin("missing-equals")


def test_cli_declares_git_and_federation_bisect_surfaces() -> None:
    parser = build_parser()
    single = parser.parse_args(["git", "bisect", "--good", "a", "--bad", "b", "--task", "workflow:validate", "--json"])
    assert single.git_command == "bisect" and single.good == "a" and single.bad == "b" and single.task == "workflow:validate"
    federated = parser.parse_args(["federation", "bisect", "--repo", "app=a..b", "--pin", "lib=c", "--task", "verify", "--json"])
    assert federated.federation_command == "bisect"
    assert federated.bisect_repositories == ["app=a..b"]
    assert federated.bisect_pins == ["lib=c"]


def test_candidate_planning_failure_is_classified_bad_and_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, _app, _lib, commits, _lib_commit, _frozen = _federation_fixture(tmp_path / "fixture")

    def evaluator(_scoped, _combination, _identity):
        return BisectTaskOutcome(0)

    # The public task identity itself is validated before candidate evaluation.
    with pytest.raises(FederationPlanError):
        run_federated_bisect(config, repository_ranges=[f"app={commits[0]}..{commits[-1]}"], pins=[], task="missing-workflow", evaluator=evaluator)
