from __future__ import annotations

"""Private ANDROID-X04 worker for one typed instrumentation child.

The public authority is ``devtool android instrumentation``.  This module is an
internal process boundary only: it accepts no repository-authored typed
capability flags and runs solely from a parent-PID-bound private handoff created
by the canonical advanced planner.
"""

import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

from .advanced_execution import consume_advanced_worker_handoff

_MARKER = "DEVTOOL_ANDROID_ADVANCED_RESULT "


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value]


def main() -> int:
    payload = consume_advanced_worker_handoff()
    if not payload:
        print("Android advanced worker requires a valid immediate-parent handoff", file=sys.stderr)
        return 2

    child_run_id = str(payload.get("child_run_id") or "").strip()
    child_id = str(payload.get("child_id") or "").strip()
    parent_plan_id = str(payload.get("parent_plan_id") or "").strip()
    repo_root = Path(str(payload.get("repo_root") or "")).expanduser().resolve(strict=False)
    if not child_run_id or not child_id or not parent_plan_id or not repo_root.is_dir():
        print("Android advanced worker handoff is incomplete", file=sys.stderr)
        return 2

    # Require the shared managed-run capability as an independent parent-bound
    # proof that this worker may adopt the preallocated child RUN identity.
    from ..invocation_origin import consume_managed_run_handoff
    managed_run_id = consume_managed_run_handoff()
    if managed_run_id != child_run_id:
        print("Android advanced worker RUN handoff mismatch", file=sys.stderr)
        return 2
    os.environ["DEVTOOL_RUN_ID"] = child_run_id

    try:
        from ..config import load_project_config
        from ..core_runtime import private_run_root
        from .instrumentation import AndroidInstrumentationOrchestrator, request_from_config

        config_path = str(payload.get("config_path") or "").strip() or None
        config = load_project_config(
            str(repo_root), config_path,
            target_name=str(payload.get("target") or ""),
            variant_name=str(payload.get("variant") or ""),
            allow_ambiguous_target=True,
            allow_parent_config=True,
        )
        # Preserve effective parent runtime Android/Gradle overrides.  This is a
        # private handoff from the already-resolved parent configuration, not a
        # second repository-config interpretation.
        overrides = _mapping(payload.get("runner_options"))
        for key in ("android", "gradle"):
            value = overrides.get(key)
            if isinstance(value, Mapping):
                config.runner_options[key] = dict(value)
        config.device_serial = str(payload.get("serial") or "")

        request_data = _mapping(payload.get("request"))
        report = private_run_root(repo_root) / "evidence" / child_run_id / "android-instrumentation" / "result.json"
        request = request_from_config(
            config,
            classes=_list(request_data.get("classes")),
            methods=_list(request_data.get("methods")),
            annotations=_list(request_data.get("annotations")),
            not_annotations=_list(request_data.get("not_annotations")),
            size=str(request_data.get("size") or ""),
            runner_args={str(k): str(v) for k, v in _mapping(request_data.get("runner_args")).items()},
            skip_build=True,
            report=report,
        )
        request.serial = str(payload.get("serial") or "")
        request.device_policy = "serial"
        request.typed_runner_args = {
            str(k): str(v) for k, v in _mapping(payload.get("typed_runner_args")).items()
        }
        request.advanced_plan_id = parent_plan_id
        request.advanced_child_id = child_id

        orchestrator = AndroidInstrumentationOrchestrator.from_config(
            config,
            env=os.environ,
            gradle_receipts=tuple(
                dict(item) for item in (payload.get("gradle_receipts") or ()) if isinstance(item, Mapping)
            ),
        )
        result = orchestrator.run(request)
        envelope = {
            "schema_version": 1,
            "kind": "devtool.android-advanced-worker-result",
            "child_run_id": child_run_id,
            "child_id": child_id,
            "parent_plan_id": parent_plan_id,
            "result": result.as_dict(),
        }
        print(_MARKER + json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0 if result.ok else (int(result.returncode or 0) or 1)
    except BaseException as exc:
        envelope = {
            "schema_version": 1,
            "kind": "devtool.android-advanced-worker-result",
            "child_run_id": child_run_id,
            "child_id": child_id,
            "parent_plan_id": parent_plan_id,
            "error": f"{type(exc).__name__}: {exc}",
        }
        print(_MARKER + json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        print(f"Android advanced worker failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
