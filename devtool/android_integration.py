from __future__ import annotations

"""Shared Android/Gradle integration built from the standalone ``build-apk`` tool.

The heavy toolchain implementation remains executable as a self-contained
script so it can be copied from Termux into a mounted chroot.  This module owns
configuration translation, lightweight inspection/doctor data, and staging the
script in a stable XDG data path.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Iterable

from .build_optimization import resolve_android_validation_policy, resolve_build_policy
from . import android_build
from .execution_environments import CHROOT, NATIVE_TERMUX, detect_current_environment
from .models import ProjectConfig
from .xdg import data_dir


@dataclass(frozen=True, slots=True)
class AndroidCommandPlan:
    project_root: Path
    builder: Path
    argv_prefix: tuple[str, ...]
    default_tasks: tuple[str, ...]


def _dict_option(config: ProjectConfig, name: str) -> dict[str, Any]:
    value = config.runner_options.get(name, {})
    return dict(value) if isinstance(value, dict) else {}


def android_project_root(config: ProjectConfig) -> Path:
    raw = config.project_root or config.build_target or str(config.repo_root)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = config.repo_root / path
    return path.resolve()


def _android_runtime_dir() -> Path:
    """Runtime assets stored in a host path that can be mapped into chroot."""
    explicit = os.environ.get("DEVTOOL_ANDROID_RUNTIME_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.environ.get("DEVTOOL_XDG_DIR", "").strip():
        return data_dir().resolve()
    return (
        Path.home()
        / ".local"
        / "share"
        / "devtool"
        / "runtime"
    ).resolve()


def _state_builder_path() -> Path:
    explicit = os.environ.get("DEVTOOL_ANDROID_BUILDER", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return _android_runtime_dir() / "android" / "build-apk"


def stage_android_builder(*, destination: Path | None = None) -> Path:
    """Install the self-contained builder in a chroot-visible home path.

    A normal Python console-script under Termux's ``$PREFIX`` is not visible in
    most chroots.  The staged copy lives under the devtool XDG data directory,
    which should be inside the Termux home mapping for Android chroot use.
    """

    source = Path(android_build.__file__).resolve()
    target = (destination or _state_builder_path()).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    def digest(path: Path) -> str:
        value = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(chunk)
        return value.hexdigest()

    if not target.is_file() or digest(target) != digest(source):
        temporary = target.with_suffix(".tmp")
        shutil.copy2(source, temporary)
        temporary.chmod(0o755)
        temporary.replace(target)
    elif not os.access(target, os.X_OK):
        target.chmod(target.stat().st_mode | 0o111)
    return target


def _state_gradle_init_path() -> Path:
    explicit = os.environ.get("DEVTOOL_GRADLE_INIT_SCRIPT", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return _android_runtime_dir() / "gradle" / "devtool-events.init.gradle"


def stage_gradle_init_script(*, destination: Path | None = None) -> Path:
    source = Path(__file__).resolve().parent / "resources" / "gradle" / "devtool-events.init.gradle"
    target = (destination or _state_gradle_init_path()).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        raise RuntimeError(f"Bundled Gradle event init script is missing: {source}")
    if not target.is_file() or target.read_bytes() != source.read_bytes():
        temporary = target.with_suffix(".tmp")
        shutil.copy2(source, temporary)
        temporary.replace(target)
    return target


def inspect_android_project(config: ProjectConfig) -> android_build.ProjectInfo:
    root = android_project_root(config)
    return android_build.inspect_project(android_build.resolve_project_root(root))


def _task_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.replace(",", " ").split() if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def configured_tasks(config: ProjectConfig, action: str) -> list[str]:
    if action == "test":
        try:
            from .test_selection import resolve_test_selection
            selected = resolve_test_selection(config)
            selected_tasks = list(selected.tasks)
        except Exception:
            selected_tasks = []
        if selected_tasks:
            return selected_tasks
    tasks = _dict_option(config, "tasks")
    aliases = {
        "restore": ("restore", "configure"),
        "build": ("build",),
        "clean": ("clean",),
        "test": ("test",),
        "lint": ("lint",),
    }
    for name in aliases.get(action, (action,)):
        selected = _task_values(tasks.get(name))
        if selected:
            return selected

    project = inspect_android_project(config)
    if action == "restore":
        return ["help"]
    if action == "clean":
        return ["clean"]
    android = _dict_option(config, "android")
    variant = str(config.variant or android.get("variant", "debug") or "debug").strip()
    variant_title = variant[:1].upper() + variant[1:]
    if action == "build":
        if project.android_application:
            return ["assemble" + variant_title]
        return list(android_build.default_tasks(project))
    if action == "test":
        if project.android:
            return ["test" + variant_title + "UnitTest"]
        return ["test"]
    if action == "lint":
        if project.android:
            return ["lint" + variant_title]
        # JVM Gradle projects do not have a universal lint task. The target can
        # opt into checkstyle/detekt/etc. through [tasks].lint.
        return []
    return []




def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            values.extend(_string_list(item))
        return values
    text = str(value).strip()
    return [text] if text else []


def _validation_changed_paths(config: ProjectConfig) -> list[str]:
    validation = _dict_option(config, "validation")
    return list(dict.fromkeys(
        item.replace("\\", "/").lstrip("./")
        for item in _string_list(validation.get("changed_paths"))
        if item.strip()
    ))


def _is_android_module_build_file(build_file: Path) -> bool:
    try:
        text = build_file.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    markers = (
        "com.android.application",
        "com.android.library",
        "com.android.dynamic-feature",
        "com.android.test",
        "plugins.android.application",
        "plugins.android.library",
        "androidapplication",
        "androidlibrary",
    )
    return any(marker in text for marker in markers) or bool(
        re.search(r"(?m)^\s*android\s*\{", text)
    )


def _gradle_module_directories(root: Path) -> dict[Path, str]:
    modules: dict[Path, str] = {}
    ignored = {".git", ".gradle", "build", "out", "node_modules"}
    for name in ("build.gradle.kts", "build.gradle"):
        for build_file in root.rglob(name):
            relative = build_file.relative_to(root)
            if any(part in ignored for part in relative.parts):
                continue
            module_dir = build_file.parent.resolve()
            if module_dir == root.resolve() or not _is_android_module_build_file(build_file):
                continue
            module_name = ":" + ":".join(module_dir.relative_to(root).parts)
            modules[module_dir] = module_name
    return modules


def _is_gradle_wide_change(path: str) -> bool:
    lowered = path.lower().strip("/")
    name = lowered.rsplit("/", 1)[-1]
    parts = set(lowered.split("/"))
    if name in {
        "settings.gradle",
        "settings.gradle.kts",
        "gradle.properties",
        "libs.versions.toml",
        "gradlew",
        "gradlew.bat",
        "gradle-wrapper.properties",
        "gradle-wrapper.jar",
        ".devtool.toml",
    }:
        return True
    if "/" not in lowered and (name.endswith(".gradle") or name.endswith(".gradle.kts")):
        return True
    if lowered.startswith("gradle/wrapper/"):
        return True
    return bool(parts & {"buildsrc", "build-logic"})


def android_validation_lint_tasks(
    config: ProjectConfig,
) -> tuple[list[str], str, list[str]]:
    """Return lint tasks, scope and affected modules for validation.

    Direct ``devtool lint`` remains a full-project operation.  Artifact-backed
    validation may safely lint only modules whose files changed, while changes
    to shared Gradle configuration retain the conservative root lint fallback.
    """

    default_tasks = configured_tasks(config, "lint")
    if not default_tasks:
        return [], "disabled", []

    validation = _dict_option(config, "validation")
    if validation.get("affected_module_lint") is False:
        return default_tasks, "full-configured", []

    changed_paths = _validation_changed_paths(config)
    if not changed_paths:
        return default_tasks, "full-no-change-data", []

    root = android_project_root(config)
    repo_root = config.repo_root.expanduser().resolve()
    try:
        project_prefix = root.relative_to(repo_root).as_posix().strip("/")
    except ValueError:
        project_prefix = ""

    android = _dict_option(config, "android")
    variant = str(config.variant or android.get("variant", "debug") or "debug").strip()
    variant_title = variant[:1].upper() + variant[1:]
    expected_root_task = "lint" + variant_title
    normalized_defaults = [task.lstrip(":") for task in default_tasks]
    if normalized_defaults != [expected_root_task]:
        return default_tasks, "full-custom-task", []

    relative_changes: list[str] = []
    outside_gradle_change = False
    for raw in changed_paths:
        path = raw.strip("/")
        if not path:
            continue
        if project_prefix:
            if path == project_prefix:
                relative_changes.append("")
            elif path.startswith(project_prefix + "/"):
                relative_changes.append(path[len(project_prefix) + 1:])
            elif _is_gradle_wide_change(path):
                outside_gradle_change = True
        else:
            relative_changes.append(path)

    if outside_gradle_change or any(_is_gradle_wide_change(path) for path in relative_changes):
        return default_tasks, "full-gradle-wide-change", []

    modules = _gradle_module_directories(root)
    affected: set[str] = set()
    for relative in relative_changes:
        if not relative:
            continue
        absolute = (root / relative).resolve()
        candidates = [
            (module_dir, module_name)
            for module_dir, module_name in modules.items()
            if absolute == module_dir or module_dir in absolute.parents
        ]
        if candidates:
            _, module_name = max(candidates, key=lambda item: len(item[0].parts))
            affected.add(module_name)
            continue
        # A root Android module can keep sources directly below the build root.
        if relative.startswith(("src/", "AndroidManifest.xml")):
            affected.add("")

    if not affected:
        return [], "skipped-non-code-changes", []

    module_names = sorted(affected)
    tasks = [
        f"{module}:lint{variant_title}" if module else expected_root_task
        for module in module_names
    ]
    return tasks, "affected-modules", module_names


def _append_option(argv: list[str], flag: str, value: Any) -> None:
    if value is None or value == "":
        return
    argv.extend([flag, str(value)])


def android_builder_arguments_for_tasks(
    config: ProjectConfig,
    tasks: Iterable[str],
    *,
    scan_artifacts: bool,
    validation: bool = False,
) -> list[str]:
    root = android_project_root(config)
    android = _dict_option(config, "android")
    gradle = _dict_option(config, "gradle")
    artifacts = _dict_option(config, "artifacts")

    argv = [str(root), "--no-chroot"]
    seen_tasks: set[str] = set()
    for raw_task in tasks:
        task = str(raw_task or "").strip()
        if not task or task in seen_tasks:
            continue
        seen_tasks.add(task)
        argv.extend(["--task", task])

    _append_option(argv, "--java-home", android.get("java_home") or gradle.get("java_home"))
    _append_option(argv, "--sdk-root", android.get("sdk_root"))
    _append_option(argv, "--aapt2", android.get("aapt2"))
    _append_option(argv, "--aapt2-provider", android.get("aapt2_provider", "auto"))
    _append_option(argv, "--aapt2-release", android.get("aapt2_release"))
    _append_option(argv, "--android-platform", android.get("platform", "auto"))
    _append_option(argv, "--build-tools", android.get("build_tools", "auto"))
    _append_option(argv, "--ndk-version", android.get("ndk_version", "auto"))
    _append_option(argv, "--cmake-version", android.get("cmake_version", "auto"))
    _append_option(argv, "--gradle-command", gradle.get("command"))
    _append_option(argv, "--gradle-version", gradle.get("version"))
    validation_policy = resolve_android_validation_policy(config) if validation else None
    max_workers = (
        validation_policy.max_workers
        if validation_policy is not None
        else gradle.get("max_workers") or android.get("max_workers") or resolve_build_policy(config, config.kind).max_workers
    )
    _append_option(argv, "--max-workers", max_workers)
    _append_option(argv, "--system-deps", android.get("system_deps", "auto"))
    _append_option(argv, "--daemon-idle-timeout-ms", gradle.get("daemon_idle_timeout_ms", 900_000))
    if validation_policy is not None and validation_policy.enabled:
        _append_option(argv, "--gradle-jvm-heap-mb", validation_policy.gradle_heap_mb)
        validation_options = _dict_option(config, "validation")
        configured_metaspace = (
            validation_options.get("gradle_metaspace_mb")
            or gradle.get("validation_metaspace_mb")
        )
        lint_workload = any("lint" in task.lower() for task in seen_tasks)
        # Android Lint loads detector classes and lint models for every selected
        # module.  A general 384 MiB validation setting must not accidentally
        # erase lint's safer floor, which caused Gradle to expire its daemon in
        # the XDM multi-module validation run.
        if lint_workload:
            lint_metaspace = (
                validation_options.get("gradle_lint_metaspace_mb")
                or gradle.get("validation_lint_metaspace_mb")
            )
            metaspace_mb = lint_metaspace or max(int(configured_metaspace or 0), 512)
        else:
            metaspace_mb = configured_metaspace or 384
        _append_option(argv, "--gradle-metaspace-mb", metaspace_mb)
        _append_option(argv, "--memory-guard-mb", validation_policy.memory_guard_mb)
        _append_option(argv, "--cpu-limit", validation_policy.cpu_limit)
        argv.append("--constrained")
        if validation_policy.low_priority:
            argv.append("--low-priority")

    if bool(android.get("skip_sdk_install", False)):
        argv.append("--skip-sdk-install")
    elif bool(android.get("auto_sdk", False)):
        argv.append("--auto-sdk")
    if bool(android.get("offline", gradle.get("offline", False))):
        argv.append("--offline")
    if bool(android.get("force_setup", False)):
        argv.append("--force-setup")
    no_daemon = bool(gradle.get("no_daemon", False))
    parallel = bool(gradle.get("parallel", config.kind == "android"))
    if validation_policy is not None and validation_policy.enabled:
        validation_options = _dict_option(config, "validation")
        no_daemon = validation_policy.no_daemon
        parallel = bool(validation_options.get("parallel", False))
    if no_daemon:
        argv.append("--no-daemon")
    if parallel:
        argv.append("--parallel")
    if bool(gradle.get("build_cache", True)):
        argv.append("--build-cache")
    if bool(gradle.get("fail_fast", False)):
        argv.append("--fail-fast")

    if not scan_artifacts:
        argv.append("--no-artifact-scan")
    else:
        output_dir = artifacts.get("output_directory") or artifacts.get("output_dir")
        output_name = artifacts.get("output_name")
        _append_option(argv, "--output-dir", output_dir)
        _append_option(argv, "--output-name", output_name)
        if bool(artifacts.get("scan", True)) is False:
            argv.append("--no-artifact-scan")

    # The builder treats arguments after ``--`` as direct Gradle arguments.
    # The staged init script emits the exact task/test lifecycle consumed by
    # terminal and native HUD progress, avoiding percentage guesses.
    if bool(gradle.get("structured_events", True)):
        argv.extend([
            "--",
            "--init-script",
            str(stage_gradle_init_script()),
            "--no-configuration-cache",
        ])
    return argv


def android_builder_arguments(config: ProjectConfig, action: str) -> list[str]:
    return android_builder_arguments_for_tasks(
        config,
        configured_tasks(config, action),
        scan_artifacts=action == "build",
    )


def _android_execution_mode(config: ProjectConfig) -> str:
    current = detect_current_environment()
    if current == CHROOT:
        return "chroot"
    if current == NATIVE_TERMUX:
        android = _dict_option(config, "android")
        if bool(android.get("prefer_native_termux", False)):
            return "native-termux"
        return "chroot" if should_prefer_android_chroot(config) else "native-termux"
    return "host"


def plan_android_command(config: ProjectConfig, action: str) -> AndroidCommandPlan:
    project = inspect_android_project(config)
    builder = stage_android_builder()
    # Use a PATH-resolved interpreter so chroot execution can select its own
    # Python while the staged script path is translated through the home mount.
    prefix = (os.environ.get("DEVTOOL_ANDROID_PYTHON", "python3") or "python3", str(builder))
    return AndroidCommandPlan(
        project_root=project.root,
        builder=builder,
        argv_prefix=prefix,
        default_tasks=tuple(configured_tasks(config, action)),
    )


def android_inspect_rows(config: ProjectConfig) -> list[tuple[str, str]]:
    project = inspect_android_project(config)
    wrapper_version = android_build.wrapper_distribution_version(project.root)
    requirement = android_build.infer_java_requirement(project)
    sdk_root, sdk_source = android_build.discover_sdk_root_with_source(None, project.root)
    android = _dict_option(config, "android")
    tasks = configured_tasks(config, "build")
    test_tasks = configured_tasks(config, "test")
    lint_tasks = configured_tasks(config, "lint")
    return [
        ("project_root", str(project.root)),
        ("project_name", project.name),
        ("project_type", "Android application" if project.android_application else "Android library/Gradle"),
        ("execution_mode", _android_execution_mode(config)),
        ("gradle_wrapper", wrapper_version or "not declared"),
        ("minimum_java", str(requirement.minimum) + (" exact toolchain" if requirement.exact else "")),
        ("compile_sdk", str(project.compile_sdk) if project.compile_sdk else "auto/unknown"),
        ("android_gradle_plugin", str(project.agp_major) if project.agp_major else "unknown"),
        ("sdk_install", "manual" if bool(android.get("skip_sdk_install", False)) else "auto"),
        ("auto_sdk", "yes" if bool(android.get("auto_sdk", False)) else "no"),
        ("sdk_source", sdk_source or "missing"),
        ("sdk_root", str(sdk_root) if sdk_root else "missing"),
        ("native_build", "yes" if project.native_build else "no"),
        ("ndk", project.ndk_version or "auto/none"),
        ("cmake", project.cmake_version or "auto/none"),
        ("default_build_tasks", ", ".join(tasks) if tasks else "-"),
        ("default_test_tasks", ", ".join(test_tasks) if test_tasks else "-"),
        ("default_lint_tasks", ", ".join(lint_tasks) if lint_tasks else "not configured"),
        ("gradle_events", str(stage_gradle_init_script())),
    ]


def android_doctor_rows(config: ProjectConfig) -> list[tuple[str, str]]:
    project = inspect_android_project(config)
    requirement = android_build.infer_java_requirement(project)
    android = _dict_option(config, "android")
    java_home: Path | None = None
    try:
        java_home = android_build.find_java_home(None, requirement.minimum, exact=requirement.exact)
    except android_build.BuildError:
        pass

    sdk, sdk_source = android_build.discover_sdk_root_with_source(None, project.root)
    wrapper = project.root / "gradlew"
    gradle_path = wrapper if wrapper.is_file() else (Path(shutil.which("gradle")) if shutil.which("gradle") else None)
    sdkmanager = android_build.find_sdkmanager(sdk) if sdk else None
    build_tools = android_build.installed_build_tools_versions(sdk) if sdk else []

    machine = android_build.normalized_machine()
    aapt2_status = "AGP/default"
    if machine in {"arm64", "armv7"} and project.android:
        configured = _dict_option(config, "android").get("aapt2")
        cached = Path.home() / ".cache" / android_build.APP_NAME / "aapt2" / machine / "aapt2"
        if configured and Path(str(configured)).expanduser().is_file():
            aapt2_status = str(Path(str(configured)).expanduser())
        elif cached.is_file():
            aapt2_status = str(cached)
        else:
            aapt2_status = "repairable: native ARM AAPT2 will be downloaded"

    platform_status = "unknown"
    if sdk and project.compile_sdk:
        platform = sdk / "platforms" / f"android-{project.compile_sdk}" / "android.jar"
        platform_status = "ready" if platform.is_file() else f"missing android-{project.compile_sdk}"

    return [
        ("android_project", "ready" if project.android else "not Android"),
        ("execution_mode", _android_execution_mode(config)),
        (
            "java",
            str(java_home)
            if java_home
            else (
                f"missing Java {requirement.exact}"
                if requirement.exact is not None
                else f"missing Java {requirement.minimum}+"
            ),
        ),
        ("gradle", str(gradle_path) if gradle_path else "missing wrapper/Gradle"),
        ("sdk_install", "manual" if bool(android.get("skip_sdk_install", False)) else "auto"),
        ("auto_sdk", "yes" if bool(android.get("auto_sdk", False)) else "no"),
        ("android_sdk", str(sdk) if sdk else "missing"),
        ("android_sdk_source", sdk_source or "missing"),
        ("sdkmanager", str(sdkmanager) if sdkmanager else "missing/repairable"),
        ("platform", platform_status),
        ("build_tools", build_tools[0] if build_tools else "missing/repairable"),
        ("aapt2", aapt2_status),
        ("builder", str(stage_android_builder())),
    ]


def should_prefer_android_chroot(config: ProjectConfig) -> bool:
    """Return whether Android auto mode should use the configured chroot.

    Native Termux remains available explicitly. Auto mode is conservative when
    a chroot backend exists because Android SDK host tools are commonly glibc or
    architecture specific. Projects can opt into native-first behavior with
    ``[android] prefer_native_termux = true``.
    """

    if config.kind not in {"android", "gradle"}:
        return False
    android = _dict_option(config, "android")
    if bool(android.get("prefer_native_termux", False)):
        return False
    return bool(shutil.which(os.environ.get("DEVTOOL_CHROOTCTL", "chrootctl")))
