from __future__ import annotations

"""Target-scoped final qualification seals for Devtool workspace targets.

The repository-wide FS03 seal proves the whole tree is internally consistent.
This module adds one deterministic, side-effect-free seal per configured
``.devtool.toml`` target so final overlay validation can be split by target
without collapsing all target promises into one artifact.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import ast
import tomllib

TARGET_SEAL_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class TargetSealCheck:
    id: str
    status: str
    summary: str
    details: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TargetSealExpectation:
    target: str
    runner: str
    platform: str
    root: str
    report: str
    required_paths: tuple[str, ...]
    test_files: tuple[str, ...]
    validation_keys: tuple[str, ...] = ()
    apply_command_target: str = ""


TARGET_SEAL_EXPECTATIONS: dict[str, TargetSealExpectation] = {
    "devtool": TargetSealExpectation(
        target="devtool",
        runner="python",
        platform="server",
        root=".",
        report="REM15_DEVTOOL_TARGET_FINAL_QUALIFICATION_REPORT.md",
        required_paths=(
            "devtool/cli.py",
            "devtool/final_seal.py",
            "devtool/target_seal.py",
            "tools/release-qualify.py",
            "tools/make-release-candidate.sh",
            "tests/test_rem15_devtool_target_final_seal.py",
            "docs/FINAL_RELEASE_SEAL.md",
        ),
        test_files=("tests/test_rem15_devtool_target_final_seal.py",),
        validation_keys=("python_safe", "memory_guard_mb"),
        apply_command_target="devtool",
    ),
}


def _check(id: str, ok: bool, summary: str, details: list[str] | tuple[str, ...] = ()) -> TargetSealCheck:
    return TargetSealCheck(id=id, status="pass" if ok else "fail", summary=summary, details=tuple(details))


def _read_config(repo_root: Path) -> tuple[dict[str, Any], list[str]]:
    path = repo_root / ".devtool.toml"
    if not path.is_file():
        return {}, ["missing:.devtool.toml"]
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        return {}, [f"invalid:.devtool.toml:{exc}"]
    return (data if isinstance(data, dict) else {}), []


def _markdown_errors(repo_root: Path, paths: tuple[str, ...], *, min_bytes: int = 120) -> list[str]:
    errors: list[str] = []
    for rel in paths:
        path = repo_root / rel
        if not path.is_file():
            errors.append(f"missing:{rel}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if len(text.encode("utf-8")) < min_bytes:
            errors.append(f"too-small:{rel}")
        if "#" not in text[:2048]:
            errors.append(f"no-heading:{rel}")
        if any(marker in text.lower() for marker in ("todo: fill", "placeholder report", "tbd release evidence")):
            errors.append(f"placeholder:{rel}")
    return errors


def _python_test_errors(repo_root: Path, paths: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    for rel in paths:
        path = repo_root / rel
        if not path.is_file():
            errors.append(f"missing:{rel}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError as exc:
            errors.append(f"syntax:{rel}:{exc.lineno}:{exc.msg}")
            continue
        has_test = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_") for node in ast.walk(tree))
        has_assertion = "assert " in text or "self.assert" in text
        if not has_test or not has_assertion:
            errors.append(f"no-test-contract:{rel}")
    return errors


def _nested_mapping(value: object, *keys: str) -> dict[str, Any]:
    current: object = value
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def target_seal_report(repo_root: Path, target_name: str) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    target_name = str(target_name or "").strip()
    checks: list[TargetSealCheck] = []
    config, config_errors = _read_config(repo_root)
    expectation = TARGET_SEAL_EXPECTATIONS.get(target_name)

    checks.append(_check("target.request", bool(target_name), "a target name was supplied", ["empty target"] if not target_name else []))
    checks.append(_check("config.parse", not config_errors, "repository target configuration parses", config_errors))

    targets = config.get("targets") if isinstance(config, dict) else None
    target_cfg = targets.get(target_name) if isinstance(targets, dict) else None
    checks.append(_check(
        "target.configured",
        isinstance(target_cfg, dict),
        "target exists in .devtool.toml",
        [f"target={target_name!r}"] if not isinstance(target_cfg, dict) else [],
    ))
    checks.append(_check(
        "target.expectation",
        expectation is not None,
        "target has an explicit final-seal overlay expectation",
        [f"missing expectation for {target_name!r}"] if expectation is None else [],
    ))

    if expectation is not None and isinstance(target_cfg, dict):
        identity_errors: list[str] = []
        for key, expected in (("runner", expectation.runner), ("platform", expectation.platform), ("root", expectation.root)):
            actual = str(target_cfg.get(key) or ("." if key == "root" else ""))
            if actual != expected:
                identity_errors.append(f"{key}: expected {expected!r}, found {actual!r}")
        checks.append(_check("target.identity", not identity_errors, "target runner/platform/root identity matches the sealed expectation", identity_errors))

        target_root = (repo_root / expectation.root).resolve()
        inside_repo = target_root == repo_root or repo_root in target_root.parents
        checks.append(_check(
            "target.root",
            target_root.exists() and inside_repo,
            "target root exists inside the repository",
            [] if target_root.exists() and inside_repo else [str(target_root)],
        ))

        missing_required = [rel for rel in expectation.required_paths if not (repo_root / rel).is_file()]
        checks.append(_check("target.required-paths", not missing_required, "target-owned final qualification paths exist", missing_required))

        validation = _nested_mapping(target_cfg, "validation")
        missing_validation = [key for key in expectation.validation_keys if key not in validation]
        checks.append(_check(
            "target.validation-contract",
            not expectation.validation_keys or not missing_validation,
            "target validation policy declares the sealed keys",
            [f"missing:{key}" for key in missing_validation],
        ))

        report_errors = _markdown_errors(repo_root, (expectation.report,), min_bytes=160)
        checks.append(_check("target.report", not report_errors, "target final qualification report is present and content-validated", report_errors))

        test_errors = _python_test_errors(repo_root, expectation.test_files)
        checks.append(_check("target.tests", not test_errors, "target final qualification tests are present and syntactically valid", test_errors))

        command_details = [
            f"devtool --copy --target {expectation.apply_command_target or expectation.target} apply overlay <artifact> ...",
            "final validation remains enabled on this target-specific final overlay",
        ]
        checks.append(_check("target.apply-command", bool(expectation.apply_command_target or expectation.target), "target has a concrete target-scoped apply command", command_details))

    failures = [item for item in checks if item.status == "fail"]
    return {
        "schema_version": TARGET_SEAL_SCHEMA_VERSION,
        "report_kind": "devtool-target-final-seal",
        "status": "pass" if not failures else "fail",
        "repo_root": str(repo_root),
        "target": target_name,
        "summary": {
            "checks": len(checks),
            "passed": len(checks) - len(failures),
            "failed": len(failures),
            "expected_targets": sorted(TARGET_SEAL_EXPECTATIONS),
        },
        "checks": [item.as_dict() for item in checks],
    }


def render_target_seal_text(payload: dict[str, Any]) -> str:
    target = str(payload.get("target") or "-")
    lines = [
        f"Devtool target final seal [{target}]: {str(payload.get('status', 'unknown')).upper()}",
        f"schema: {payload.get('schema_version')}",
    ]
    for item in payload.get("checks", []):
        marker = "PASS" if item.get("status") == "pass" else "FAIL"
        lines.append(f"[{marker}] {item.get('id')}: {item.get('summary')}")
        for detail in item.get("details") or []:
            lines.append(f"  - {detail}")
    summary = payload.get("summary", {})
    lines.append(f"checks: {summary.get('passed', 0)} passed, {summary.get('failed', 0)} failed")
    return "\n".join(lines)
