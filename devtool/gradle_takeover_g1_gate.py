from __future__ import annotations

"""GRADLE-TAKEOVER-G1 authoritative real-process qualification.

The gate deliberately performs process-boundary proof with the host's actual
Gradle executable when the qualified Termux host provides one.  Construction
hosts may use an explicit fake only behind a test-only opt-in; artifact
validation does not set that opt-in.
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Iterator, Mapping

from .config import load_project_config
from .gradle_ingress import converge_gradle_spec
from .gradle_takeover import (
    GradleIntent,
    GradleOrigin,
    GradleTakeoverError,
    broker_gradle_invocation,
    execute_intercepted_gradle,
    resolve_real_launcher,
    shell_bin_dir,
    strip_takeover_shims,
    takeover_status,
    write_shell_assets,
)
from .models import CommandSpec
from .runners import run_specs


class GradleTakeoverG1Error(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GradleTakeoverG1Error(message)


def _is_termux() -> bool:
    prefix = os.environ.get("PREFIX", "")
    return prefix.startswith("/data/data/com.termux/") or "/com.termux/" in sys.executable


def _gradle_executable() -> str:
    override = os.environ.get("DEVTOOL_GRADLE_TAKEOVER_G1_GRADLE", "").strip()
    allow_test = os.environ.get("DEVTOOL_GRADLE_TAKEOVER_G1_ALLOW_TEST_GRADLE", "").strip() == "1"
    if not _is_termux() and not allow_test:
        raise GradleTakeoverG1Error("authoritative GRADLE-TAKEOVER-G1 real-process proof requires native Termux")
    if override:
        if not allow_test:
            raise GradleTakeoverG1Error(
                "DEVTOOL_GRADLE_TAKEOVER_G1_GRADLE is forbidden for authoritative GRADLE-TAKEOVER-G1 validation"
            )
        candidate = Path(override).expanduser().resolve(strict=False)
        _require(candidate.is_file() and os.access(candidate, os.X_OK), f"test Gradle override is not executable: {candidate}")
        return str(candidate)

    path_value, _ = strip_takeover_shims(os.environ.get("PATH", ""), extra_dirs=(shell_bin_dir(),))
    resolved = shutil.which("gradle", path=path_value)
    _require(bool(resolved), "GRADLE-TAKEOVER-G1 requires a real Gradle executable on PATH")
    candidate = Path(str(resolved)).resolve(strict=False)
    _require(candidate.parent != shell_bin_dir().resolve(strict=False), "real Gradle resolution recursed into takeover shim")
    return str(candidate)


@contextmanager
def _patched_environment(values: Mapping[str, str]) -> Iterator[None]:
    old = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(old)
        os.environ.update({str(k): str(v) for k, v in values.items()})
        yield
    finally:
        os.environ.clear()
        os.environ.update(old)


def _write_fixture(root: Path, real_gradle: str, gradle_home: Path) -> None:
    (root / "settings.gradle").write_text("rootProject.name = 'devtool-gradle-takeover-g1'\n", encoding="utf-8")
    (root / "build.gradle").write_text(
        """
        tasks.register('gateShell') {
            doLast { file('gate-shell.marker').text = 'shell-ok' }
        }
        tasks.register('gateWrapper') {
            doLast { file('gate-wrapper.marker').text = 'wrapper-ok' }
        }
        tasks.register('gateProbe') {
            doLast { file('gate-probe.marker').text = 'probe-ok' }
        }
        """,
        encoding="utf-8",
    )
    # The gate-owned wrapper is an executable project wrapper boundary around the
    # qualified real Gradle binary.  It lets the gate prove requested-wrapper
    # identity/process execution without downloading a wrapper distribution.
    wrapper = root / "gradlew"
    wrapper.write_text(
        "#!/bin/sh\nset -eu\nexec " + shlex.quote(real_gradle) + " \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    props = root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    props.parent.mkdir(parents=True, exist_ok=True)
    props.write_text("distributionUrl=https\\://services.gradle.org/distributions/gradle-9.7-bin.zip\n", encoding="utf-8")
    (props.parent / "gradle-wrapper.jar").write_bytes(b"gradle-takeover-g1-wrapper-identity")

    gradle_q = real_gradle.replace("\\", "\\\\").replace('"', '\\"')
    home_q = str(gradle_home).replace("\\", "\\\\").replace('"', '\\"')
    (root / ".devtool.toml").write_text(
        f'''[project]\nrunner = "gradle"\nroot = "."\n\n[execution]\nenvironment = "native-termux"\n\n[gradle]\ncommand = "{gradle_q}"\nuser_home = "{home_q}"\nconfiguration_cache = "off"\nbuild_cache = false\ndaemon = false\nvfs = false\nparallel = false\nmax_workers = 1\nheap_mb = 512\nresource_profile = "balanced"\n\n[gradle.takeover]\nenabled = true\nmode = "strict"\nscope = "both"\nlauncher = "auto"\n''',
        encoding="utf-8",
    )


def _devtool_launcher(root: Path) -> Path:
    launcher = root / "devtool-gate-launcher"
    source_root = Path(__file__).resolve().parent.parent
    launcher.write_text(
        "#!/bin/sh\nset -eu\n"
        + f"export PYTHONPATH={shlex.quote(str(source_root))}${{PYTHONPATH:+:$PYTHONPATH}}\n"
        + "exec " + shlex.quote(sys.executable) + " -m devtool \"$@\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return launcher


def _run_process(argv: list[str], *, cwd: Path, env: Mapping[str, str], label: str) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        argv,
        cwd=str(cwd),
        env=dict(env),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=180,
    )
    _require(process.returncode == 0, f"{label} failed with exit {process.returncode}:\n{process.stdout[-5000:]}")
    return process


def _assert_real_shell_interception(root: Path, config: Any, real_gradle: str, env: dict[str, str]) -> dict[str, Any]:
    resolution = resolve_real_launcher(config, requested="system", env=env)
    _require(Path(resolution.executable).resolve() == Path(real_gradle).resolve(), "shim-first PATH did not resolve to actual Gradle")
    _require(str(shell_bin_dir().resolve(strict=False)) in resolution.stripped_shim_dirs, "shim directory was not recorded as stripped")

    marker = root / "gate-shell.marker"
    marker.unlink(missing_ok=True)
    result = _run_process(["gradle", "gateShell", "--offline", "--console=plain"], cwd=root, env=env, label="bare gradle shell interception")
    _require(marker.read_text(encoding="utf-8") == "shell-ok", "bare gradle interception did not execute the real Gradle task")
    status = takeover_status(config, env=env)
    _require(status["coverage"]["bare_gradle_shell"] is True, "shell coverage did not claim bare Gradle interception")
    _require(status["coverage"]["direct_project_wrapper_shell"] is False, "shell coverage falsely claimed direct ./gradlew interception")
    _require(status["coverage"]["arbitrary_nested_processes"] is False, "shell coverage falsely claimed arbitrary nested interception")
    return {
        "real_gradle": resolution.executable,
        "stripped_shim_dirs": list(resolution.stripped_shim_dirs),
        "marker": marker.name,
        "output_tail": result.stdout[-600:],
    }


def _assert_wrapper_process(root: Path, config: Any, env: dict[str, str]) -> dict[str, Any]:
    marker = root / "gate-wrapper.marker"
    marker.unlink(missing_ok=True)
    raw = CommandSpec(name="gate-wrapper", argv=["./gradlew", "gateWrapper", "--offline", "--console=plain"], cwd=str(root), env={"GRADLE_USER_HOME": env["GRADLE_USER_HOME"]})
    rendered = converge_gradle_spec(config, raw, default_origin=GradleOrigin.WORKFLOW)
    _require(len(rendered) == 1, f"wrapper gate unexpectedly split into {len(rendered)} command specs")
    results, _ = run_specs(config, rendered, verbose=False, continue_on_error=False)
    _require(len(results) == 1 and results[0].returncode == 0, "canonical wrapper process failed")
    _require(marker.read_text(encoding="utf-8") == "wrapper-ok", "canonical wrapper process did not execute the real Gradle task")
    metadata = results[0].metadata
    takeover = dict(metadata.get("gradle_takeover") or {})
    receipt = dict(takeover.get("receipt") or {})
    _require(receipt.get("requested_launcher") == "wrapper", f"wrapper requested-launcher truth drifted: {receipt}")
    _require(bool(receipt.get("launcher_identity")), "wrapper launcher identity missing")
    _require(bool(metadata.get("gradle_takeover_semantic_identity")), "wrapper semantic takeover identity missing")
    return {
        "receipt_id": receipt.get("receipt_id"),
        "semantic_identity": metadata.get("gradle_takeover_semantic_identity"),
        "launcher_identity": receipt.get("launcher_identity"),
        "marker": marker.name,
    }


def _assert_probe_control_processes(root: Path, config: Any, env: dict[str, str]) -> dict[str, Any]:
    request = __import__("devtool.gradle_planning", fromlist=["GradleInvocationRequest"]).GradleInvocationRequest(
        caller="gradle-takeover-g1",
        action="gate",
        project_root=str(root),
        requested_roots=("gateProbe",),
        raw_args=("--offline", "--console=plain"),
    )
    work = broker_gradle_invocation(config, request, origin=GradleOrigin.DEVTOOL, intent=GradleIntent.WORK, requested_launcher="system", env=env)
    probe = broker_gradle_invocation(config, request, origin=GradleOrigin.DEVTOOL, intent=GradleIntent.PROBE, requested_launcher="system", env=env)
    control = broker_gradle_invocation(config, request, origin=GradleOrigin.DEVTOOL, intent=GradleIntent.CONTROL, requested_launcher="system", env=env)
    _require(work.plan is not None and work.receipt.canonicalized, "WORK did not enter canonical Gradle planning")
    _require(probe.plan is None and probe.receipt.bypass_reason == "typed-probe-bypass", "PROBE collapsed into WORK")
    _require(control.plan is None and control.receipt.bypass_reason == "typed-control-bypass", "CONTROL collapsed into WORK")

    # CONTROL executes the qualified real launcher directly; `--version` is a
    # control operation and must not be swallowed by a zero-root WORK plan.
    rc = execute_intercepted_gradle(config, ["--version"], origin="devtool", intent="control", requested_launcher="system", env=env)
    _require(rc == 0, f"CONTROL real Gradle process failed with exit {rc}")
    return {
        "work_receipt": work.receipt.receipt_id,
        "probe_receipt": probe.receipt.receipt_id,
        "control_receipt": control.receipt.receipt_id,
    }


def run_gate() -> dict[str, Any]:
    real_gradle = _gradle_executable()
    with tempfile.TemporaryDirectory(prefix="devtool-gradle-takeover-g1-") as tmp:
        root = Path(tmp).resolve()
        state_home = root / "state"
        gradle_home = root / ".gradle-home"
        gradle_home.mkdir(parents=True, exist_ok=True)
        _write_fixture(root, real_gradle, gradle_home)
        launcher = _devtool_launcher(root)

        base_env = dict(os.environ)
        base_env["XDG_STATE_HOME"] = str(state_home)
        base_env["GRADLE_USER_HOME"] = str(gradle_home)
        base_env["DEVTOOL_GRADLE_TAKEOVER_DEVTOOL"] = str(launcher)
        base_env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + (os.pathsep + base_env["PYTHONPATH"] if base_env.get("PYTHONPATH") else "")

        with _patched_environment(base_env):
            assets = write_shell_assets()
            clean_path, _ = strip_takeover_shims(base_env.get("PATH", ""), extra_dirs=(shell_bin_dir(),))
            # Keep the real Gradle's directory first after the takeover shim so
            # the real-launcher resolver has one deterministic destination.
            real_dir = str(Path(real_gradle).resolve().parent)
            path_parts = [real_dir] + [p for p in clean_path.split(os.pathsep) if p and str(Path(p).resolve(strict=False)) != str(Path(real_dir).resolve(strict=False))]
            gate_env = dict(base_env)
            gate_env["PATH"] = os.pathsep.join([str(shell_bin_dir()), *path_parts])
            os.environ["PATH"] = gate_env["PATH"]

            config = load_project_config(str(root), None, allow_ambiguous_target=True)
            shell = _assert_real_shell_interception(root, config, real_gradle, gate_env)
            wrapper = _assert_wrapper_process(root, config, gate_env)
            intents = _assert_probe_control_processes(root, config, gate_env)
            _require(Path(assets["shim"]).is_file(), "takeover shell shim disappeared during gate")

            return {
                "status": "passed",
                "host": "native-termux" if _is_termux() else "construction-host",
                "real_gradle": real_gradle,
                "shell": shell,
                "wrapper": wrapper,
                "intents": intents,
                "coverage": takeover_status(config, env=gate_env)["coverage"],
            }


def main() -> int:
    try:
        payload = run_gate()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
