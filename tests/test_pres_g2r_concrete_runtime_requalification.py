from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

from devtool.textual_control_center import ACTION_SPECS, PAGE_ACTIONS, PANEL_AUTHORITIES
from devtool.textual_execution_host import select_execution_textual_host
from devtool.tui_textual import TextualAvailability, resolve_tui_backend_selection


ROOT = Path(__file__).resolve().parents[1]


class _TTY:
    def isatty(self) -> bool:
        return True


def _digest_tree(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _json(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_g2r_reconciles_successful_r03_native_evidence_and_r01_reopened_promises() -> None:
    r03 = _json("PRES_R03_PROMISE_LEDGER.json")
    assert r03["status"] == "qualified_native"
    assert r03["native_qualification"] == {
        "run": "9b5fff37ecd14b9c81d294a00540183b",
        "tests": "175 passed, 0 failed, 0 skipped",
        "diagnostics": "0 warnings, 0 errors",
        "git_commit": "0fd7d0c",
        "artifact": "pres_r03_textual_parity_qualification_v6.zip",
    }

    r01 = _json("PRES_R01_PROMISE_LEDGER.json")
    assert r01["status"] == "qualified_native_runtime_closed_by_pres_r03"
    promises = {row["id"]: row for row in r01["promises"]}
    for promise_id in ("PRES-R01-P003", "PRES-R01-P008", "PRES-R01-P009"):
        assert promises[promise_id]["status"] == "delivered_qualified_native"
        assert "9b5fff37ecd14b9c81d294a00540183b" in promises[promise_id]["qualification"]


def test_g2r_gate_ledger_covers_complete_reopened_window_and_effective_next() -> None:
    ledger = _json("PRES_G2R_PROMISE_LEDGER.json")
    assert ledger["position"] == "64/76"
    assert ledger["gate_window_position"] == "4/4"
    assert ledger["status"] == "candidate_pending_native_qualification"
    required = {
        "PRES-X04", "PRES-X05", "PRES-X06", "PRES-X07", "PRES-G1", "PRES-X08",
        "PRES-G2 remediation", "PRES-G2", "PRES-R01", "PRES-H01", "PRES-H02", "PRES-R02", "PRES-R03",
    }
    assert set(ledger["audit_window"]) == required
    assert ledger["next_on_success"].startswith("DIST-G3 66/76")
    assert "DIST-R01 65/76 already qualified/applied" in ledger["next_on_success"]
    assert ledger["final_presentation_seal"] == "PRES-G3 69/76"


def test_g2r_historical_spec_claims_are_backed_by_concrete_runtime_surfaces() -> None:
    runtime = (ROOT / "devtool" / "textual_control_center_runtime.py").read_text(encoding="utf-8")
    execution = (ROOT / "devtool" / "textual_execution_host.py").read_text(encoding="utf-8")
    presentation = (ROOT / "devtool" / "execution_presentation.py").read_text(encoding="utf-8")
    h02 = (ROOT / "devtool" / "textual_apply_host.py").read_text(encoding="utf-8")

    # X04/X05/X08/G2 are no longer accepted on model/spec evidence alone.
    assert "class DevtoolTextualControlCenter(App):" in runtime
    assert "ContentSwitcher" in runtime and "DataTable" in runtime and "RichLog" in runtime
    assert "ConfirmationModal" in runtime
    assert "ENABLE_COMMAND_PALETTE = False" in runtime

    # X06/X07/G1 normal execution replacement is concrete and main-thread hosted.
    assert "class ExecutionTextualHostSource" in execution
    assert "build_textual_execution_app" in execution
    assert 'child_env["DEVTOOL_EXECUTION_PRESENTATION"] = "plain"' in execution
    assert 'child_env["DEVTOOL_AUTO_HUD_CHILD"] = "1"' in execution
    assert "rich.live" not in presentation.lower()

    # H02 is a separate concrete main-thread apply host, not a model promise.
    assert "build_apply_textual_app" in h02
    assert 'child_env["DEVTOOL_AUTO_HUD_CHILD"] = "1"' in h02


def test_g2r_tui_default_and_normal_execution_selection_are_truthful() -> None:
    available = TextualAvailability(True, "textual", "available")
    selected = resolve_tui_backend_selection(requested_backend="auto", availability=available)
    assert selected.effective_backend == "textual"
    assert selected.default_backend == "textual"

    args = type("Args", (), {
        "command": "invoke", "quiet": False, "verbose": False, "trace": False,
        "stdout_payload": "auto", "json": False,
    })()
    decision = select_execution_textual_host(args, stdin=_TTY(), stdout=_TTY(), textual_probe=lambda: True, env={})
    assert decision.enabled is True

    machine = type("Args", (), {
        "command": "invoke", "quiet": False, "verbose": False, "trace": False,
        "stdout_payload": "auto", "json": True,
    })()
    assert select_execution_textual_host(machine, stdin=_TTY(), stdout=_TTY(), textual_probe=lambda: True, env={}).enabled is False


def test_g2r_visible_mutations_remain_canonical_and_fake_surfaces_stay_absent() -> None:
    assert set(PANEL_AUTHORITIES) >= {"runs", "workers", "artifacts", "overlays", "targets"}
    assert PANEL_AUTHORITIES["workers"] == "DIST/SSH"
    assert PANEL_AUTHORITIES["artifacts"] == "LIFE"
    assert PANEL_AUTHORITIES["overlays"] == "LIFE"
    assert {"overlay.apply", "worker.recover"}.issubset(ACTION_SPECS)
    assert {"artifact.apply", "target.launch", "worker.cancel", "worker.resume"}.isdisjoint(ACTION_SPECS)
    assert PAGE_ACTIONS["artifacts"] == ()
    assert PAGE_ACTIONS["targets"] == ()
    assert PAGE_ACTIONS["workers"] == ("worker.recover",)


def test_g2r_native_workflow_requires_all_four_concrete_textual_proofs() -> None:
    config = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    wrapper = config["wrapper"]["commands"]["pres-g2r"]
    assert wrapper["workflow"] == "roadmap-pres-g2r"
    workflow = config["targets"]["devtool"]["workflows"]["roadmap-pres-g2r"]
    refs = [row["ref"] for row in workflow]
    assert refs == [
        "job:source-syntax",
        "test:roadmap-pres-g2r-audit-contracts",
        "test:roadmap-pres-g2r-historical-window",
        "test:roadmap-pres-g2r-remediation-window",
        "test:roadmap-pres-g2r-real-textual-fixed-point",
    ]
    tests = {row["id"]: row for row in config["test"] if isinstance(row, dict) and row.get("id")}
    real = tests["roadmap-pres-g2r-real-textual-fixed-point"]
    assert real["runner"] == "command"
    assert real["env"] == {
        "DEVTOOL_PRES_R01_REQUIRE_TEXTUAL": "1",
        "DEVTOOL_PRES_R02_REQUIRE_TEXTUAL": "1",
        "DEVTOOL_PRES_R03_REQUIRE_TEXTUAL": "1",
        "DEVTOOL_PRES_H02_REQUIRE_TEXTUAL": "1",
    }
    command = " ".join(real["command"])
    for test_name in (
        "real_textual_execution_app_updates_and_confirms_cancel",
        "real_apply_host_app_runs_on_textual_main_thread_when_required",
        "real_textual_control_center_pages_selection_confirmation_and_live_refresh",
        "real_textual_r03_navigation_help_palette_failure_live_resize_and_shutdown",
    ):
        assert test_name in command


def test_g2r_real_textual_tests_fail_closed_when_native_requirement_is_set() -> None:
    checks = {
        "tests/test_pres_r01_execution_presentation.py": "DEVTOOL_PRES_R01_REQUIRE_TEXTUAL",
        "tests/test_pres_r02_textual_control_center_app.py": "DEVTOOL_PRES_R02_REQUIRE_TEXTUAL",
        "tests/test_pres_r03_textual_parity_qualification.py": "DEVTOOL_PRES_R03_REQUIRE_TEXTUAL",
        "tests/test_pres_h02_textual_apply_host.py": "DEVTOOL_PRES_H02_REQUIRE_TEXTUAL",
    }
    for rel, flag in checks.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert flag in text
        assert "pytest.fail" in text
        assert "run_test" in text


def test_g2r_fresh_process_once_projection_is_read_only_and_bounded(tmp_path: Path) -> None:
    (tmp_path / "tracked.txt").write_text("stable\n", encoding="utf-8")
    before = _digest_tree(tmp_path)
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(ROOT) + ((os.pathsep + env["PYTHONPATH"]) if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [sys.executable, "-m", "devtool.tui_textual_runtime", "--once", "--page", "workers", "--width", "44"],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = ast.literal_eval(proc.stdout.strip())
    assert payload["page"] == "workers"
    assert payload["owns_truth"] is False
    assert payload["capabilities"]["mode"] == "ultra-narrow"
    assert _digest_tree(tmp_path) == before


def test_g2r_legacy_rich_live_is_explicit_hud_scope_not_normal_execution() -> None:
    hud = (ROOT / "devtool" / "hud.py").read_text(encoding="utf-8")
    tasker = (ROOT / "devtool" / "tasker.py").read_text(encoding="utf-8")
    execution = (ROOT / "devtool" / "textual_execution_host.py").read_text(encoding="utf-8")
    assert "from rich.live import Live" in hud
    assert 'os.environ.get("DEVTOOL_AUTO_HUD_CHILD") == "1"' in tasker
    assert 'child_env["DEVTOOL_AUTO_HUD_CHILD"] = "1"' in execution
    report = (ROOT / "PRES_G2R_CONCRETE_RUNTIME_REQUALIFICATION_GATE_REPORT.md").read_text(encoding="utf-8")
    assert "PRES-G3 69/76" in report
    assert "not reclassified as normal execution presentation" in report
