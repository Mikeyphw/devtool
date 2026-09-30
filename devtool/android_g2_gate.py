from __future__ import annotations

"""ANDROID-G2 authoritative instrumentation/device lifecycle milestone proof."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from .android.instrumentation import (
    _execution_classification,
    gradle_receipts_from_current_run,
    instrumentation_intent,
    resolve_instrumentation_plan,
    InstrumentationRequest,
)
from .core_runtime import private_run_root
from .models import ProjectConfig
from .project_runners import AndroidGradleRunner


class AndroidG2Error(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AndroidG2Error(message)


def _is_termux() -> bool:
    prefix = str(os.environ.get("PREFIX") or "")
    return prefix.startswith("/data/data/com.termux/") or "/com.termux/" in sys.executable


def run_gate() -> dict[str, Any]:
    allow_test = os.environ.get("DEVTOOL_ANDROID_G2_ALLOW_TEST_HOST") == "1"
    _require(_is_termux() or allow_test, "ANDROID-G2 authoritative process proof requires native Termux")
    with tempfile.TemporaryDirectory(prefix="devtool-android-g2-") as td:
        root = Path(td)
        (root / "settings.gradle").write_text("rootProject.name='g2'\n", encoding="utf-8")
        (root / "build.gradle").write_text("android { compileSdk 36 }\n", encoding="utf-8")
        config = ProjectConfig(
            repo_root=root, project_root=str(root), kind="android", runner="android",
            selected_target="phone", variant="debug",
            runner_options={
                "android": {
                    "instrumentation": {"variant": "demoDebug", "module": "mobile", "policy": "required"},
                    "variants": {"demoDebug": {"build_type": "debug", "module": "mobile"}},
                },
                "gradle": {"command": "gradle"},
            },
        )
        specs = AndroidGradleRunner().build_specs(config, "instrumentation")
        device = next(spec for spec in specs if "devtool.android_instrumentation" in spec.argv)
        target_index = device.argv.index("--target")
        _require(device.argv[target_index + 1] == "phone", "project-runner did not preserve selected target into instrumentation subprocess")
        _require(any(":mobile:assembleDemoDebug" in spec.argv for spec in specs[:-1]), "instrumentation Gradle producer lost module/variant lane")

        run_id = "android-g2-process"
        run_dir = private_run_root(root) / run_id
        run_dir.mkdir(parents=True)
        receipt = {"receipt_id": "g2-gradle", "plan_digest": "g2-plan", "success": True, "process_executed": True}
        event = {
            "type": "TaskFinished", "event_type": "node.finished", "run_id": run_id,
            "sequence": 1, "task": "instrumentation-build", "success": True, "status": "succeeded",
            "metadata": {"gradle_execution_receipt": receipt},
        }
        (run_dir / "execution-events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
        recovered = gradle_receipts_from_current_run(root, run_id=run_id)
        _require(recovered == (receipt,), "current RUN Gradle receipt did not survive the process boundary")

        intent = instrumentation_intent(InstrumentationRequest(root, policy="required", classes=["demo.UiTest"]))
        plan = resolve_instrumentation_plan(
            intent, environment_id="env", artifact_pair_id="pair", device_lease_id="lease",
            destination_id="android-device:device-1", runner="demo.test/Runner",
            target_package="demo.app", test_package="demo.test",
            command=["am", "instrument", "-w", "-r", "-e", "class", "demo.UiTest", "demo.test/Runner"],
        )
        _require(plan.destination_id == "android-device:device-1" and plan.command[-1] == "demo.test/Runner", "resolved plan lost destination/command authority")
        crash = "INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS_CODE: -2\nINSTRUMENTATION_RESULT: shortMsg=Process crashed.\n"
        counts, failed_ids, kind, _reason = _execution_classification(crash, 1)
        _require(kind == "setup-crash" and counts["executed"] == 0 and counts["failed"] == 0 and not failed_ids, "process crash was misclassified as a test failure")

        child = subprocess.run(
            [sys.executable, "-c", "from devtool.android.instrumentation import _execution_classification; c,i,k,r=_execution_classification('INSTRUMENTATION_STATUS: numtests=1\\nINSTRUMENTATION_STATUS_CODE: -2\\nINSTRUMENTATION_RESULT: shortMsg=Process crashed.\\n',1); print(k,c['executed'],c['failed'])"],
            cwd=str(Path(__file__).resolve().parents[1]), text=True, capture_output=True, check=False,
        )
        _require(child.returncode == 0 and child.stdout.strip() == "setup-crash 0 0", "fresh-process instrumentation classification proof failed")
        return {
            "schema_version": 1,
            "status": "passed",
            "native_termux": _is_termux(),
            "project_runner_target": device.argv[target_index + 1],
            "gradle_receipt_id": recovered[-1]["receipt_id"],
            "intent_id": intent.intent_id,
            "plan_id": plan.plan_id,
            "fresh_process": child.stdout.strip(),
        }


def main() -> int:
    try:
        print(json.dumps(run_gate(), sort_keys=True))
        return 0
    except Exception as exc:
        print(f"ANDROID-G2 failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
