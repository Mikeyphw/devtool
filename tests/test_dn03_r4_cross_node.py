"""DN03-R4 source, generated-YAML and cross-node evidence regression."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from devtool import actions_dn03_dotnet as dn03
from devtool import actions_dn03_r3 as r3
from devtool import actions_dn03_r4 as r4
from devtool import actions_dn03_r4_verify as verifier

DEVTOOL_SHA = "a" * 40
GRAPH_SHA = "b" * 64
PROVIDER_SHA = "c" * 64


def jobs(outputs: bool = True):
    return [
        {"id": "node-01", "node": "restore", "provider": "dotnet",
         "profile_id": "ubuntu-24.04-x64", "toolchain_version": "10.0.100",
         "dotnet_legacy_runtime": "6.0.36", "outputs": []},
        {"id": "node-02", "node": "test", "provider": "dotnet",
         "profile_id": "ubuntu-24.04-x64", "toolchain_version": "10.0.100",
         "dotnet_legacy_runtime": "6.0.36", "outputs": ["dist/test-results.trx"] if outputs else []},
    ]


def fixture(monkeypatch):
    items = jobs()
    graph = {
        "jobs": items, "graph_sha256": GRAPH_SHA,
        "provider_plan_sha256": PROVIDER_SHA,
        "runtime_sha": DEVTOOL_SHA,
    }
    source = ('name: original\non:\n  workflow_dispatch:\n'
              'permissions:\n  contents: read\njobs:\n'
              + ''.join('  ' + j['id'] + ':\n    runs-on: ubuntu-24.04\n    steps:\n'
                 '      - name: DN03-R3 reverify native .NET node, host and output bytes\n'
                 '        run: echo verified\n'
                 for j in items))
    previous = {
        "kind": "devtool.dn03-r3-verified-native-export",
        "graph_sha256": GRAPH_SHA, "provider_plan_sha256": PROVIDER_SHA,
        "native_nodes": [j["node"] for j in items],
    }
    monkeypatch.setattr(r3, "compile_r3", lambda *a, **kw: (source, previous))
    monkeypatch.setattr(dn03, "compile_graph", lambda *a, **kw: deepcopy(graph))
    return graph, source


def git_root(tmp_path):
    root = tmp_path / "consumer"
    root.mkdir(parents=True)
    for args in (("init", "-q"), ("config", "user.name", "Test"),
                 ("config", "user.email", "test@example.invalid")):
        subprocess.run(("git", "-C", str(root), *args), check=True)
    (root / "README.md").write_text("consumer\n")
    subprocess.run(("git", "-C", str(root), "add", "."), check=True)
    subprocess.run(("git", "-C", str(root), "commit", "-qm", "base"), check=True)
    return root


def test_r4_appends_one_observer_without_mutating_native_dag(monkeypatch, tmp_path):
    graph, source = fixture(monkeypatch)
    result, receipt = r4.compile_r4(git_root(tmp_path), target="app", workflow="ci",
                                    devtool_sha=DEVTOOL_SHA)
    assert result.startswith(source)
    assert result.count("  dn03_r4_acceptance:\n") == 1
    assert result.count("DN03-R3 reverify native .NET node") == 2
    assert receipt["graph_sha256"] == GRAPH_SHA
    assert receipt["workflow_sha256"] == sha256(result.encode()).hexdigest()
    assert receipt["real_generated_workflow_executed"] is False
    assert receipt["remote_authenticated"] is False
    parsed = yaml.safe_load(result)
    assert set(parsed["jobs"]) == {"node-01", "node-02", r4.ACCEPTANCE_JOB}
    acceptance = parsed["jobs"][r4.ACCEPTANCE_JOB]
    assert acceptance["needs"] == ["node-01", "node-02"]
    assert acceptance["if"] == "${{ always() }}"
    steps = acceptance["steps"]
    names = [s["name"] for s in steps]
    assert names.index("Fetch node-01 typed native evidence") < names.index("DN03-R4 cross-check all same-run native node receipts")
    assert "Fetch node-01 native output bytes" not in names
    assert "Fetch node-02 native output bytes" in names
    assert len([s for s in steps if s.get("uses", "").startswith("actions/download-artifact@")]) == 5
    assert len([s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")]) == 1
    for s in steps:
        if "run" in s:
            check = subprocess.run(("bash", "-n"), input=s["run"], text=True,
                                   capture_output=True)
            assert check.returncode == 0, (s["name"], check.stderr)


def test_r4_refuses_mixed_and_duplicate_native_jobs(monkeypatch, tmp_path):
    graph, _ = fixture(monkeypatch)
    root = git_root(tmp_path)
    graph["jobs"][1]["provider"] = "go"
    monkeypatch.setattr(dn03, "compile_graph", lambda *a, **kw: deepcopy(graph))
    with pytest.raises(r4.R4Error, match="non-Linux/non-.NET"):
        r4.compile_r4(root, target="app", workflow="ci", devtool_sha=DEVTOOL_SHA)
    graph["jobs"][1]["provider"] = "dotnet"
    graph["jobs"][1]["id"] = "node-01"
    with pytest.raises(r4.R4Error, match="duplicate"):
        r4.compile_r4(root, target="app", workflow="ci", devtool_sha=DEVTOOL_SHA)


def test_r4_refuses_unbound_source_and_missing_r3_anchors(monkeypatch, tmp_path):
    graph, source = fixture(monkeypatch)
    with pytest.raises(r4.R4Error, match="pin"):
        r4._append_acceptance(source, graph, target="app", workflow="ci", devtool_sha="main")
    with pytest.raises(r4.R4Error, match="post-execution"):
        r4._append_acceptance(source.replace("DN03-R3 reverify native .NET node", "gone", 1),
                              graph, target="app", workflow="ci", devtool_sha=DEVTOOL_SHA)
    with pytest.raises(r4.R4Error, match="collide"):
        clone = deepcopy(graph)
        clone["jobs"][0]["id"] = r4.ACCEPTANCE_JOB
        r4._append_acceptance(source, clone, target="app", workflow="ci", devtool_sha=DEVTOOL_SHA)


def test_r4_plan_write_verify_are_create_only_and_deterministic(monkeypatch, tmp_path):
    fixture(monkeypatch)
    root = git_root(tmp_path)
    before = dn03._git(root, "rev-parse", "HEAD")
    plan = r4.export(root, target="app", workflow="ci", devtool_sha=DEVTOOL_SHA, mode="plan")
    assert not (root / plan["workflow_path"]).exists()
    with pytest.raises(r4.R4Error, match="explicit --yes"):
        r4.export(root, target="app", workflow="ci", devtool_sha=DEVTOOL_SHA, mode="write")
    created = r4.export(root, target="app", workflow="ci", devtool_sha=DEVTOOL_SHA,
                        mode="write", confirmed=True)
    assert created["wrote_files"] and dn03._git(root, "rev-parse", "HEAD") == before
    assert r4.export(root, target="app", workflow="ci", devtool_sha=DEVTOOL_SHA,
                     mode="verify")["verified_files"]
    subprocess.run(("git", "-C", str(root), "add", "."), check=True)
    subprocess.run(("git", "-C", str(root), "commit", "-qm", "generated"), check=True)
    assert r4.export(root, target="app", workflow="ci", devtool_sha=DEVTOOL_SHA,
                     mode="verify")["verified_files"]
    with pytest.raises(r4.R4Error, match="existing"):
        r4.export(root, target="app", workflow="ci", devtool_sha=DEVTOOL_SHA,
                  mode="write", confirmed=True)
    (root / created["workflow_path"]).write_text("tampered\n")
    with pytest.raises(r4.R4Error, match="differs"):
        r4.export(root, target="app", workflow="ci", devtool_sha=DEVTOOL_SHA, mode="verify")


def receipt_fixture(monkeypatch, tmp_path):
    from devtool import actions_graph_export, actions_node_evidence
    root = git_root(tmp_path)
    run, attempt = 12345678, 2
    runner = tmp_path / "runner"
    runner.mkdir()
    evidence = runner / "dn03-r4"
    for path in ("evidence", "host", "bytes"):
        (evidence / path).mkdir(parents=True)
    graph = {
        "jobs": [jobs(outputs=False)[0]], "graph_sha256": GRAPH_SHA,
        "provider_plan_sha256": PROVIDER_SHA, "runtime_sha": DEVTOOL_SHA,
        "consumer_commit": "",
    }
    monkeypatch.setattr(actions_graph_export, "compile_graph", lambda *a, **kw: deepcopy(graph))
    monkeypatch.setattr(actions_node_evidence, "verify_node_evidence", lambda *a, **kw: {
        "identity_matched": True, "structure_valid": True,
        "remote_authenticated": False, "release_qualified": False,
    })
    # The real verifier recompiles and checks the checked-in generated YAML.
    generated = "name: generated\n"
    generated_receipt = {"workflow_sha256": sha256(generated.encode()).hexdigest()}
    yaml_path = root / ".github/workflows/dn03r4.yml"
    sidecar = root / "docs/ci/dn03r4.json"
    yaml_path.parent.mkdir(parents=True)
    sidecar.parent.mkdir(parents=True)
    yaml_path.write_text(generated)
    sidecar.write_text(json.dumps(generated_receipt, sort_keys=True, indent=2) + "\n")
    subprocess.run(("git", "-C", str(root), "add", "."), check=True)
    subprocess.run(("git", "-C", str(root), "commit", "-qm", "reviewed generated workflow"), check=True)
    source = subprocess.check_output(("git", "-C", str(root), "rev-parse", "HEAD"), text=True).strip()
    tree = subprocess.check_output(("git", "-C", str(root), "rev-parse", "HEAD^{tree}"), text=True).strip()
    graph["consumer_commit"] = source
    from devtool import actions_dn03_r4 as r4
    monkeypatch.setattr(r4, "compile_r4", lambda *a, **kw: (generated, generated_receipt))
    monkeypatch.setattr(r4, "_paths", lambda *a, **kw: (yaml_path, sidecar))
    job = graph["jobs"][0]
    eid = job["id"]
    edir = evidence / "evidence" / eid
    hdir = evidence / "host" / eid
    edir.mkdir(parents=True)
    hdir.mkdir(parents=True)
    native = {"source": source, "graph": GRAPH_SHA, "typed_evidence": {"source": {"repository": "Mikeyphw/consumer"}}}
    host = {
        "kind": "devtool.dn03-host-review", "source_sha": source,
        "devtool_sha": DEVTOOL_SHA, "graph_sha256": GRAPH_SHA,
        "node": eid, "sdk": "10.0.100", "legacy_runtime": "6.0.36",
        "run_id": run, "run_attempt": attempt,
        "runtime_inventory_checked": True, "hosted_authenticated": False,
        "release_qualified": False, "runtime_receipt_sha256": "d" * 64,
    }
    def dump(p, data):
        p.write_text(json.dumps(data, sort_keys=True) + "\n")
    dump(edir / "node-evidence.json", native)
    dump(hdir / "host.json", host)
    local = {
        "kind": "devtool.dn03-r3-local-node-verified",
        "source_sha": source, "source_tree": tree,
        "repository": "Mikeyphw/consumer", "devtool_sha": DEVTOOL_SHA,
        "graph_sha256": GRAPH_SHA, "job_id": eid, "native_node": job["node"],
        "run_id": run, "run_attempt": attempt,
        "node_evidence_sha256": sha256((edir / "node-evidence.json").read_bytes()).hexdigest(),
        "host_receipt_sha256": sha256((hdir / "host.json").read_bytes()).hexdigest(),
        "native_output_bytes_rechecked": True, "native_job_qualified": True,
        "test_reports_independently_verified": False,
        "remote_authenticated": False, "release_qualified": False,
    }
    dump(edir / "dn03-r3-node-verification.json", local)
    env = {
        "GITHUB_SHA": source, "GITHUB_REPOSITORY": "Mikeyphw/consumer",
        "GITHUB_RUN_ID": str(run), "GITHUB_RUN_ATTEMPT": str(attempt),
        "GITHUB_ACTIONS": "true", "GITHUB_WORKSPACE": str(root),
        "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64",
        "RUNNER_TEMP": str(runner),
        "DN03_R4_NEEDS_JSON": json.dumps({eid: {"result": "success"}}),
    }
    return root, evidence, env, eid


def verify(root, evidence, env, *, write=False):
    return verifier.verify_workflow(
        root, target="app", workflow="ci", devtool_sha=DEVTOOL_SHA,
        graph_sha=GRAPH_SHA, evidence=evidence, environment=env, write=write)


def test_r4_cross_node_review_accepts_same_run_local_evidence(monkeypatch, tmp_path):
    root, evidence, env, eid = receipt_fixture(monkeypatch, tmp_path)
    result = verify(root, evidence, env, write=True)
    assert result["native_dag_passed"] and result["node_count"] == 1
    assert result["remote_authenticated"] is False
    assert result["test_reports_independently_verified"] is False
    assert result["declared_output_nodes_verified"] == 0
    assert (evidence / "acceptance.json").is_file()
    with pytest.raises(FileExistsError):
        verify(root, evidence, env, write=True)


def test_r4_refuses_failed_missing_or_skipped_needs(monkeypatch, tmp_path):
    root, evidence, env, eid = receipt_fixture(monkeypatch, tmp_path)
    for data in ({}, {eid: {"result": "failure"}}, {eid: {"result": "skipped"}},
                 {eid: {"result": "success"}, "unknown": {"result": "success"}}):
        broken = {**env, "DN03_R4_NEEDS_JSON": json.dumps(data)}
        with pytest.raises(verifier.R4Refused, match="native DAG"):
            verify(root, evidence, broken)


def test_r4_refuses_wrong_attempt_and_graph(monkeypatch, tmp_path):
    root, evidence, env, eid = receipt_fixture(monkeypatch, tmp_path)
    with pytest.raises(verifier.R4Refused, match="mismatch"):
        verify(root, evidence, {**env, "GITHUB_RUN_ATTEMPT": "3"})
    with pytest.raises(verifier.R4Refused, match="graph"):
        verifier.verify_workflow(root, target="app", workflow="ci",
                                 devtool_sha=DEVTOOL_SHA, graph_sha="0" * 64,
                                 evidence=evidence, environment=env, write=False)


def test_r4_refuses_native_and_host_mutation(monkeypatch, tmp_path):
    root, evidence, env, eid = receipt_fixture(monkeypatch, tmp_path)
    native_file = evidence / "evidence" / eid / "node-evidence.json"
    native_file.write_text('{"bad":true}\n')
    with pytest.raises(verifier.R4Refused, match="repository|mismatch"):
        verify(root, evidence, env)
    root2, evidence2, env2, eid2 = receipt_fixture(monkeypatch, tmp_path / "next")
    host_file = evidence2 / "host" / eid2 / "host.json"
    data = json.loads(host_file.read_text())
    data["sdk"] = "9.0.100"
    host_file.write_text(json.dumps(data))
    with pytest.raises(verifier.R4Refused, match="mismatch"):
        verify(root2, evidence2, env2)


def test_r4_refuses_symlinks_and_source_drift(monkeypatch, tmp_path):
    root, evidence, env, eid = receipt_fixture(monkeypatch, tmp_path)
    n = evidence / "evidence" / eid / "node-evidence.json"
    dst = tmp_path / "foreign.json"
    dst.write_bytes(n.read_bytes())
    n.unlink()
    n.symlink_to(dst)
    with pytest.raises(verifier.R4Refused, match="unsafe"):
        verify(root, evidence, env)
    root2, evidence2, env2, eid2 = receipt_fixture(monkeypatch, tmp_path / "later")
    with pytest.raises(verifier.R4Refused, match="GHA source"):
        verify(root2, evidence2, {**env2, "GITHUB_SHA": "f" * 40})


def test_r4_source_compiles_and_action_pins_are_immutable():
    assert len(r4.CHECKOUT) == len(r4.DOWNLOAD) == len(r4.SETUP_PYTHON) == len(r4.UPLOAD) == 40
    assert r4.ACCEPTANCE_JOB == "dn03_r4_acceptance"


def test_r4_refuses_mutated_checked_in_generated_workflow(monkeypatch, tmp_path):
    root, evidence, env, eid = receipt_fixture(monkeypatch, tmp_path)
    workflow = root / ".github/workflows/dn03r4.yml"
    workflow.write_text("name: injected\n")
    with pytest.raises(verifier.R4Refused, match="deployed"):
        verify(root, evidence, env)


def test_r4_refuses_foreign_repository_in_typed_evidence(monkeypatch, tmp_path):
    root, evidence, env, eid = receipt_fixture(monkeypatch, tmp_path)
    path = evidence / "evidence" / eid / "node-evidence.json"
    data = json.loads(path.read_text())
    data["typed_evidence"]["source"]["repository"] = "other/consumer"
    path.write_text(json.dumps(data))
    with pytest.raises(verifier.R4Refused, match="repository"):
        verify(root, evidence, env)


def test_r4_refuses_missing_host_receipt(monkeypatch, tmp_path):
    root, evidence, env, eid = receipt_fixture(monkeypatch, tmp_path)
    (evidence / "host" / eid / "host.json").unlink()
    with pytest.raises(verifier.R4Refused, match="missing"):
        verify(root, evidence, env)


def test_r4_refuses_unreviewed_provider_profile(monkeypatch, tmp_path):
    root, evidence, env, eid = receipt_fixture(monkeypatch, tmp_path)
    from devtool import actions_graph_export
    old = actions_graph_export.compile_graph
    def unreviewed(*a, **kw):
        data = old(*a, **kw)
        data["jobs"][0]["profile_id"] = "windows-2022-x64"
        return data
    monkeypatch.setattr(actions_graph_export, "compile_graph", unreviewed)
    with pytest.raises(verifier.R4Refused, match="provider"):
        verify(root, evidence, env)


def test_r4_refuses_missing_post_execution_r3_receipt(monkeypatch, tmp_path):
    root, evidence, env, eid = receipt_fixture(monkeypatch, tmp_path)
    (evidence / "evidence" / eid / "dn03-r3-node-verification.json").unlink()
    with pytest.raises(verifier.R4Refused, match="missing"):
        verify(root, evidence, env)


def test_r4_output_verifier_uses_downloaded_bytes_and_refuses_tampering(monkeypatch, tmp_path):
    root, evidence, env, eid = receipt_fixture(monkeypatch, tmp_path)
    from devtool import actions_graph_export, actions_node_evidence
    graph_builder = actions_graph_export.compile_graph
    def output_graph(*a, **kw):
        graph = graph_builder(*a, **kw)
        graph["jobs"][0]["outputs"] = ["dist/example.dll"]
        return graph
    monkeypatch.setattr(actions_graph_export, "compile_graph", output_graph)
    stage = evidence / "bytes" / eid
    stage.mkdir(parents=True)
    file = stage / "payload" / "dist" / "example.dll"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"tampered")
    def refuse_bad_bytes(*args, **kwargs):
        assert kwargs["staging"] == evidence / "bytes"
        raise ValueError("native handoff SHA256 mismatch")
    monkeypatch.setattr(actions_node_evidence, "verify_node_evidence", refuse_bad_bytes)
    with pytest.raises(verifier.R4Refused, match="native typed evidence or output bytes failed"):
        verify(root, evidence, env)


def test_r4_refuses_unavailable_legacy_runtime_digest(monkeypatch, tmp_path):
    root, evidence, env, eid = receipt_fixture(monkeypatch, tmp_path)
    host = evidence / "host" / eid / "host.json"
    data = json.loads(host.read_text())
    data.pop("runtime_receipt_sha256")
    host.write_text(json.dumps(data))
    with pytest.raises(verifier.R4Refused, match="legacy runtime"):
        verify(root, evidence, env)


def test_r4_refuses_untracked_workflow_receipt_even_if_bytes_match(monkeypatch, tmp_path):
    root, evidence, env, eid = receipt_fixture(monkeypatch, tmp_path)
    subprocess.run(("git", "-C", str(root), "rm", "--cached", "docs/ci/dn03r4.json"),
                   check=True, capture_output=True)
    with pytest.raises(verifier.R4Refused, match="not tracked"):
        verify(root, evidence, env)
