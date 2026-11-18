"""C3: immutable legacy PYZ bytes and opt-in native-terminal bootstrap acceptance.

All tests are offline: no provider mutation, branch switch, or host Gradle.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import re
from unittest.mock import patch

import pytest

from devtool import launcher_contract_v3 as launcher


LEGACY_BOOTSTRAP_SHA256 = "b0f359efdf72d44d18e7bdb0a7493a05075401ff2da26684961dee36043f1029"
VERSIONED_BOOTSTRAP_SHA256 = "fd2c76a70b3105fbd457883e60b5fd650fb7242982bc1fa71bda162e7b3a9714"
RUNTIME = {"source_sha":"a"*40, "source_tree":"b"*40,
           "module_blob":"c"*40, "schema_blob":"d"*40,
           "repository":"Mikeyphw/devtool"}
TARGET = {"repository":"Mikeyphw/devtool", "branch":"feature/test-compat",
          "source_sha":"e"*40, "source_tree":"f"*40}


def _versioned():
    return {**RUNTIME,"runtime_compatibility":{"minimum":"3.2.0", "major":3}}


def _manifest(profile: str | None = "native-terminal-v1"):
    inv = [{"path":"sample.txt", "action":"add", "source_blob_sha":"",
            "payload_sha256":hashlib.sha256(b"payload\n").hexdigest()}]
    # New builder manifest declares the profile; published version-aware
    # manifests omit it and remain valid under their old byte-bound contract.
    m=launcher.make_manifest("compat-acceptance", TARGET,
        {**RUNTIME,"launcher_version":"3.2.0"}, inv,
        "source-bound commit", "Test PR", "", "main", "1"*40)
    if profile is None:
        m["devtool"].pop("bootstrap_profile")
    else:
        m["devtool"]["bootstrap_profile"] = profile
    return m


def _package(path: Path, m: dict, bootstrap: str | None = None):
    return path.write_bytes(launcher.zip_bytes({
        "__main__.py":(bootstrap if bootstrap is not None else
                         launcher.bootstrap_content(m["devtool"])).encode(),
        "manifest.json":(json.dumps(m, sort_keys=True)+"\n").encode(),
        "payload/sample.txt":b"payload\n",
    }))


def test_published_legacy_and_versioned_bootstrap_bytes_are_still_exact():
    legacy=launcher.bootstrap_content(RUNTIME)
    versioned=launcher.bootstrap_content(_versioned())
    assert hashlib.sha256(legacy.encode()).hexdigest()==LEGACY_BOOTSTRAP_SHA256
    assert hashlib.sha256(versioned.encode()).hexdigest()==VERSIONED_BOOTSTRAP_SHA256
    assert "PINS = " in legacy
    assert "from devtool.launcher_contract_v3 import main" in versioned
    # The old contracts remain intact; they are not silently repinned or rewritten.
    assert "DEVTOOL_EXECUTION_PRESENTATION='plain'" in versioned


def test_new_manifest_has_explicit_profile_but_retains_immutable_runtime_pins():
    m=_manifest()
    launcher.verify_manifest(m)
    d=m["devtool"]
    assert d["bootstrap_profile"] == "native-terminal-v1"
    assert d["source_sha"]==RUNTIME["source_sha"]
    assert d["source_tree"]==RUNTIME["source_tree"]
    assert d["module_blob"]==RUNTIME["module_blob"]
    assert d["schema_blob"]=="1"*40
    assert d["runtime_compatibility"]=={"minimum":"3.2.0","major":3}


def test_new_bootstrap_is_only_a_verified_native_adapter():
    source=launcher.bootstrap_content(_manifest()["devtool"])
    assert "env.update(DEVTOOL_TEXTUAL_APPLY_HOST='0',DEVTOOL_TEXTUAL_EXECUTION_HOST='0')" in source
    assert "for key in ('DEVTOOL_TEXTUAL_APPLY_HOST','DEVTOOL_TEXTUAL_EXECUTION_HOST'):" in source
    assert "env.update(DEVTOOL_EXECUTION_PRESENTATION" not in source
    assert "os.environ['DEVTOOL_JOB_UI']" not in source
    assert "from devtool.launcher_contract_v3 import main" in source
    assert "subprocess.run(['git'" in source  # source attestation, not Git mutation
    assert "'push'" not in source and "'reset'" not in source and "'rebase'" not in source
    assert "GitHubActionsClient" not in source and "apply-overlay" not in source
    assert source != launcher.bootstrap_content(_versioned())


@pytest.mark.parametrize("profile", [None,"native-terminal-v1"])
def test_published_and_new_packages_verify_without_repacking(tmp_path,profile):
    m=_manifest(profile)
    p=tmp_path/("legacy-versioned.pyz" if profile is None else "new-native.pyz")
    _package(p,m)
    before=hashlib.sha256(p.read_bytes()).hexdigest()
    got,payload=launcher.read_embedded_package(p)
    assert got==m
    assert payload=={"payload/sample.txt":b"payload\n"}
    assert hashlib.sha256(p.read_bytes()).hexdigest()==before


def test_bootstrap_profile_tampering_or_downgrade_refused(tmp_path):
    m=_manifest();original=launcher.bootstrap_content(m["devtool"])
    tampered=json.loads(json.dumps(m));tampered["devtool"].pop("bootstrap_profile")
    with pytest.raises(launcher.Refused,match="ZIP_BOOTSTRAP_PIN_MISMATCH"):
        p=tmp_path/"downgraded.pyz";_package(p,tampered,original)
        launcher.read_embedded_package(p)
    bogus=json.loads(json.dumps(m));bogus["devtool"]["bootstrap_profile"]="unknown-v9"
    with pytest.raises(launcher.Refused,match="BOOTSTRAP_PROFILE_INVALID"):
        launcher.verify_manifest(bogus)
    bare=json.loads(json.dumps(m));bare["devtool"].pop("runtime_compatibility")
    with pytest.raises(launcher.Refused,match="BOOTSTRAP_PROFILE_INVALID"):
        launcher.verify_manifest(bare)
    wrong=json.loads(json.dumps(m));wrong["devtool"]["runtime_compatibility"]={"minimum":"4.0.0","major":4}
    with pytest.raises(launcher.Refused,match="MAJOR_INVALID"):
        launcher.verify_manifest(wrong)


def test_old_versioned_bootstrap_does_not_verify_as_new_profile(tmp_path):
    m=_manifest()
    p=tmp_path/"old-bootstrap-new-marker.pyz"
    _package(p,m,launcher.bootstrap_content(_versioned()))
    with pytest.raises(launcher.Refused,match="ZIP_BOOTSTRAP_PIN_MISMATCH"):
        launcher.read_embedded_package(p)


def test_native_child_respects_explicit_preferences_and_never_synthesizes_deprecated_variables():
    with patch.dict('os.environ', {"TERM":"dumb", "NO_COLOR":"1"}, clear=True):
        env=launcher.safe_env()
        assert "DEVTOOL_EXECUTION_PRESENTATION" not in env
        assert "DEVTOOL_JOB_UI" not in env
        assert env["DEVTOOL_TEXTUAL_APPLY_HOST"]=="0"
        assert env["DEVTOOL_TEXTUAL_EXECUTION_HOST"]=="0"
    with patch.dict('os.environ', {"DEVTOOL_EXECUTION_PRESENTATION":"user-value",
                                  "DEVTOOL_JOB_UI":"compact", "TERM":"dumb"},clear=True):
        env=launcher.safe_env()
        assert env["DEVTOOL_EXECUTION_PRESENTATION"]=="user-value"
        assert env["DEVTOOL_JOB_UI"]=="compact"
        assert env["DEVTOOL_TEXTUAL_APPLY_HOST"]=="0"
    # Native Devtool owns actual overlay and session authority. No clipboard copy.
    assert "--no-clipboard" in inspect.getsource(launcher.native)
    assert "delivery','session','start" in inspect.getsource(launcher.apply_package)
    assert "apply','overlay" in inspect.getsource(launcher.apply_package)


def test_existing_hosted_gate_runs_current_compatibility_tests_without_losing_previous_checks():
    workflow=(Path(__file__).resolve().parents[1]/".github/workflows/devtool-delivery-s7-p02.yml").read_text()
    for name in ("test_delivery_adoption.py","test_delivery_adoption_cli.py",
                 "test_delivery_cli_surface.py","test_launcher_contract_v3.py",
                 "test_launcher_runtime_compat.py","test_g0c3_launcher_compatibility.py"):
        assert name in workflow
    assert "core surfaces --check share/core-generated" in workflow
    assert "workflow_dispatch:" in workflow and "pull_request:" in workflow
    assert "gradle" not in workflow.lower()


def test_historical_provider_evidence_kept_until_equivalent_hosted_coverage():
    wf=Path(__file__).resolve().parents[1]/".github/workflows"
    # These two legacy-named workflows produce distinct provider-backed
    # evidence and cannot be discarded as obsolete just from their names.
    for p in ("devtool-delivery-s7-p06.yml","devtool-delivery-s7-p07.yml"):
        raw=(wf/p).read_text()
        assert "on:" in raw and "workflow_dispatch:" in raw
        assert "permissions:" in raw and "contents: read" in raw
        assert "upload-artifact" in raw


def test_new_pyzip_bootstrap_executes_with_verified_runtime_and_no_implicit_legacy_ui(tmp_path):
    """Real generated PYZ import against an isolated exact-Git runtime fixture."""
    import os
    import subprocess
    import sys

    repo=tmp_path/"runtime";repo.mkdir()
    def git(*parts):
        p=subprocess.run(["git","-C",str(repo),*parts],capture_output=True,text=True,timeout=15)
        assert p.returncode==0,(parts,p.stderr)
        return p.stdout.strip()
    git("init","-b","feature/compat-fixture")
    git("config","user.name","C3 Test")
    git("config","user.email","c3@example.invalid")
    git("remote","add","origin","git@github.com:Mikeyphw/devtool.git")
    mod=repo/"devtool";mod.mkdir()
    (mod/"__init__.py").write_text("")
    (mod/"core_cli_schema.json").write_text('{"schema_version":2,"kind":"devtool.canonical-cli-schema"}\n')
    (mod/"launcher_contract_v3.py").write_text(
        "SCHEMA='devtool.launcher-contract.v3'\n"
        "LAUNCHER_CONTRACT_VERSION='3.2.1'\n"
        "def main():\n"
        "    import os\n"
        "    print('NATIVE_ADAPTER_STARTED')\n"
        "    print('PRESENTATION='+str(os.getenv('DEVTOOL_EXECUTION_PRESENTATION')))\n"
        "    print('JOB_UI='+str(os.getenv('DEVTOOL_JOB_UI')))\n"
        "    print('TEXTUAL_HOST='+str(os.getenv('DEVTOOL_TEXTUAL_APPLY_HOST')))\n"
        "    return 0\n"
    )
    git("add",".");git("commit","-qm","native runtime fixture")
    m=_manifest()
    package=tmp_path/"new-native.pyz"
    _package(package,m)
    env={**os.environ,"TERM":"dumb","NO_COLOR":"1"}
    for key in ("DEVTOOL_EXECUTION_PRESENTATION","DEVTOOL_JOB_UI"):
        env.pop(key,None)
    child=subprocess.run([sys.executable,"-B",str(package),"check","--repo",str(repo),
                          "--devtool-repo",str(repo)],capture_output=True,text=True,
                         env=env,timeout=20)
    assert child.returncode==0,(child.stdout,child.stderr)
    assert "NATIVE_ADAPTER_STARTED" in child.stdout
    assert "PRESENTATION=None" in child.stdout
    assert "JOB_UI=None" in child.stdout
    assert "TEXTUAL_HOST=0" in child.stdout
    assert "LAUNCHER_V3_RUNTIME_VERSION_PASS" in child.stdout
