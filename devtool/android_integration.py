from __future__ import annotations

"""Shared Android/Gradle integration built from the standalone ``build-apk`` tool.

The heavy toolchain implementation remains executable as a self-contained
script so it can be copied from Termux into a mounted chroot.  This module owns
configuration translation, lightweight inspection/doctor data, and staging the
script in a stable XDG data path.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Iterable

from .build_optimization import android_validation_cli_resolution_notes, resolve_android_validation_phase_policy, resolve_android_validation_policy, resolve_build_policy
from . import android_build
from .execution_environments import CHROOT, NATIVE_TERMUX, detect_current_environment
from .models import ProjectConfig
from .termux_toolchains import detect_runtime_identity, termux_native_provider, termux_cmake_probe, termux_ninja_probe, termux_ccache_probe, termux_android_tool_providers, classify_native_requirement
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


def _list_option(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item for item in value.split() if item]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item)]
    return [str(value)] if str(value) else []


def android_project_root(config: ProjectConfig) -> Path:
    raw = config.project_root or config.build_target or str(config.repo_root)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = config.repo_root / path
    return path.resolve()


def _android_runtime_dir() -> Path:
    """Runtime assets stored in the canonical Devtool XDG data root."""
    explicit = str(os.environ.get("DEVTOOL_ANDROID_RUNTIME_DIR", "") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            raise ValueError("DEVTOOL_ANDROID_RUNTIME_DIR must be an absolute path")
        return path.resolve(strict=False)
    from .xdg import override_root
    override = override_root()
    if override is not None:
        return override
    return (data_dir() / "runtime").resolve(strict=False)

def _state_builder_path() -> Path:
    explicit = os.environ.get("DEVTOOL_ANDROID_BUILDER", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return _android_runtime_dir() / "android" / "build-apk"


def stage_android_builder(*, destination: Path | None = None) -> Path:
    """Install the Android builder runtime bundle in a chroot-visible path.

    The executable builder is intentionally staged outside Termux's ``$PREFIX``
    so an Android chroot can run it. Toolchain inspection lives in the canonical
    sibling ``termux_toolchains.py`` module; stage both files atomically enough
    that every refreshed builder has the companion required for direct-script
    execution without Python package context.
    """

    source = android_build.standalone_source_path()
    companion_source = android_build.standalone_companion_path()

    target = (destination or _state_builder_path()).expanduser().resolve()
    companion_target = target.with_name("termux_toolchains.py")

    target.parent.mkdir(parents=True, exist_ok=True)

    if not companion_source.is_file():
        raise RuntimeError(
            f"Bundled Android toolchain companion is missing: {companion_source}"
        )

    def digest(path: Path) -> str:
        value = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(chunk)
        return value.hexdigest()

    def stage_file(
        source_path: Path,
        target_path: Path,
        *,
        executable: bool,
    ) -> None:
        source_digest = digest(source_path)

        if not target_path.is_file() or digest(target_path) != source_digest:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target_path.name}.",
                suffix=".tmp",
                dir=target_path.parent,
            )

            os.close(descriptor)
            temporary = Path(temporary_name)

            try:
                shutil.copy2(source_path, temporary)

                if executable:
                    temporary.chmod(temporary.stat().st_mode | 0o111)

                temporary.replace(target_path)
            finally:
                temporary.unlink(missing_ok=True)

            if digest(target_path) != source_digest:
                raise RuntimeError(
                    f"Failed to refresh staged Android runtime file: {target_path}"
                )

        elif executable and not os.access(target_path, os.X_OK):
            target_path.chmod(target_path.stat().st_mode | 0o111)

    # Companion first, builder last. A freshly replaced build-apk therefore
    # never observes a missing toolchain helper during a normal refresh.
    stage_file(
        companion_source,
        companion_target,
        executable=False,
    )

    stage_file(
        source,
        target,
        executable=True,
    )

    return target


def refresh_android_builders_for_specs(specs: Iterable[Any]) -> None:
    """Refresh staged Android builders immediately before command wrapping.

    Validation plans can be resumed or reconstructed after the builder was first
    staged.  Re-staging at the execution boundary prevents a newer Devtool
    command line from reaching an older ``build-apk`` parser in the chroot.
    """

    for spec in specs:
        metadata = getattr(spec, "metadata", {})
        if not isinstance(metadata, dict):
            continue
        raw_builder = str(metadata.get("android_builder") or "").strip()
        if not raw_builder:
            continue
        staged = stage_android_builder(destination=Path(raw_builder))
        argv = [str(value) for value in getattr(spec, "argv", ())]
        required_options = {
            option
            for option in ("--gradle-user-home", "--toolchain-downloads", "--bootstrap-network", "--sdk-provider")
            if option in argv
        }
        if not required_options:
            continue
        staged_text = staged.read_text(encoding="utf-8", errors="replace")
        missing = sorted(option for option in required_options if option not in staged_text)
        if missing:
            raise RuntimeError(
                "Staged Android builder is incompatible with the planned command: "
                + ", ".join(missing)
                + f" ({staged})"
            )


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


_ANDROID_INSPECTION_CACHE_KEY = "_devtool_android_inspection_cache"


@contextmanager
def android_project_inspection_cache(config: ProjectConfig):
    """Cache expensive Android inspection only while one command plan is built."""

    had_previous = _ANDROID_INSPECTION_CACHE_KEY in config.runner_options
    previous = config.runner_options.get(_ANDROID_INSPECTION_CACHE_KEY)
    config.runner_options[_ANDROID_INSPECTION_CACHE_KEY] = {}
    try:
        yield
    finally:
        if had_previous:
            config.runner_options[_ANDROID_INSPECTION_CACHE_KEY] = previous
        else:
            config.runner_options.pop(_ANDROID_INSPECTION_CACHE_KEY, None)


def inspect_android_project(config: ProjectConfig) -> android_build.ProjectInfo:
    root = android_build.resolve_project_root(android_project_root(config))
    cache = config.runner_options.get(_ANDROID_INSPECTION_CACHE_KEY)
    key = str(root)
    if isinstance(cache, dict):
        cached = cache.get(key)
        if isinstance(cached, android_build.ProjectInfo):
            return cached
    project = android_build.inspect_project(root)
    if isinstance(cache, dict):
        cache[key] = project
    return project


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


_DOC_SUFFIXES = {".md", ".rst", ".adoc", ".txt", ".html", ".css", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
_SOURCE_SUFFIXES = {".java", ".kt", ".kts", ".groovy", ".scala", ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".rs"}


def android_validation_change_scope(config: ProjectConfig) -> str:
    """Classify artifact changes for the resumable validation phase plan.

    The result is conservative. Unknown files force ``full`` validation, while
    documentation-only inventories avoid starting Gradle at all.
    """

    changed_paths = _validation_changed_paths(config)
    if not changed_paths:
        return "full"
    categories: set[str] = set()
    for raw in changed_paths:
        path = raw.replace("\\", "/").strip("/")
        lowered = path.lower()
        name = lowered.rsplit("/", 1)[-1]
        suffix = Path(name).suffix.lower()
        if _is_gradle_wide_change(path):
            return "full"
        if "/src/androidtest/" in f"/{lowered}/" or "/src/test/" in f"/{lowered}/" or any(part in {"test", "tests"} for part in lowered.split("/")):
            categories.add("tests")
        elif name == "androidmanifest.xml" or "/res/" in f"/{lowered}/" or "/assets/" in f"/{lowered}/":
            categories.add("resources")
        elif suffix in _SOURCE_SUFFIXES:
            categories.add("source")
        elif suffix in _DOC_SUFFIXES or lowered.startswith(("docs/", ".github/")) or name in {"license", "notice", "readme", "changelog"}:
            categories.add("docs")
        else:
            categories.add("unknown")
    if "unknown" in categories:
        return "full"
    if "source" in categories:
        return "source"
    if "resources" in categories:
        return "resources"
    if "tests" in categories:
        return "tests"
    return "docs"


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
        if project_prefix == ".":
            project_prefix = ""
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


def _append_non_default_option(argv: list[str], flag: str, value: Any, *, default: str = "auto") -> None:
    """Append builder flags only when they intentionally override defaults.

    Android builder helpers are staged into long-lived chroot-visible runtime
    paths.  A stale staged ``build-apk`` parser may not know newer provider
    selection flags, and passing ``--provider auto`` is semantically identical
    to omitting that flag.  Keep explicit non-default selections strict while
    omitting default ``auto`` provider knobs for compatibility with staged
    helpers that are older than the host Devtool command generator.
    """

    if value is None or value == "":
        return
    normalized = str(value).strip()
    if not normalized or normalized == default:
        return
    argv.extend([flag, normalized])


def _positive_integer(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _explicit_zero(value: Any) -> bool:
    if value is None or value == "":
        return False
    try:
        return int(str(value).strip()) == 0
    except (TypeError, ValueError):
        return False


def _cli_override_record(options: dict[str, Any], key: str) -> dict[str, Any] | None:
    records = options.get("_cli_resource_overrides")
    if not isinstance(records, dict):
        return None
    record = records.get(key)
    return record if isinstance(record, dict) else None


def _resolve_auto_android_platform(
    root: Path,
    project: android_build.ProjectInfo | None = None,
) -> str | None:
    """Resolve ``--android-platform auto`` before invoking staged builders.

    Long-lived chroot runtime helpers can lag behind the host Devtool package.
    Passing a concrete platform derived from the Gradle project's declared
    ``compileSdk`` avoids letting a stale helper guess and try to install a
    newer/unavailable SDK platform.
    """

    try:
        project = project or android_build.inspect_project(root)
    except Exception:
        return None
    if not project.android or project.compile_sdk is None:
        return None
    if project.compile_sdk <= 0:
        return None
    return str(project.compile_sdk)


def _android_platform_option(
    root: Path,
    android: dict[str, Any],
    project: android_build.ProjectInfo | None = None,
) -> str:
    configured = str(android.get("platform", "auto") or "auto").strip()
    if not configured:
        configured = "auto"
    if configured != "auto":
        return configured
    return _resolve_auto_android_platform(root, project) or "auto"


def _bool_setting(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _bootstrap_policy(config: ProjectConfig) -> tuple[str, str]:
    bootstrap = _dict_option(config, "bootstrap")
    downloads = str(bootstrap.get("downloads", "") or "").strip().lower()
    network = str(bootstrap.get("network", "") or "").strip().lower()
    if downloads not in {"never", "missing", "auto", "force"}:
        downloads = "never" if _android_execution_mode(config) == "chroot" else "auto"
    if network not in {"deny", "allow"}:
        network = "deny" if downloads == "never" else "allow"
    return downloads, network


def _is_android_packaging_workload(tasks: Iterable[str]) -> bool:
    """Return whether tasks can invoke D8/R8 or final APK/AAB packaging."""

    packaging_markers = (
        "assemble",
        "bundle",
        "package",
        "minify",
        "shrink",
        "dex",
        "apk",
        "aab",
        "install",
    )
    return any(
        any(marker in str(task).rsplit(":", 1)[-1].lower() for marker in packaging_markers)
        for task in tasks
    )


def android_builder_arguments_for_tasks(
    config: ProjectConfig,
    tasks: Iterable[str],
    *,
    scan_artifacts: bool,
    validation: bool = False,
    validation_phase: str = "",
) -> list[str]:
    root = android_project_root(config)
    android = _dict_option(config, "android")
    gradle = _dict_option(config, "gradle")
    java_options = android.get("java", {}) if isinstance(android.get("java", {}), dict) else {}
    nested_gradle = android.get("gradle", {}) if isinstance(android.get("gradle", {}), dict) else {}
    native_options = android.get("native", {}) if isinstance(android.get("native", {}), dict) else {}
    storage_options = android.get("storage", {}) if isinstance(android.get("storage", {}), dict) else {}
    build_tools_value = android.get("build_tools", "auto")
    build_tools_options = build_tools_value if isinstance(build_tools_value, dict) else {}
    artifacts = _dict_option(config, "artifacts")
    project_info = None
    if isinstance(config.runner_options.get(_ANDROID_INSPECTION_CACHE_KEY), dict):
        try:
            project_info = inspect_android_project(config)
        except Exception:
            project_info = None

    argv = [str(root), "--no-chroot"]
    seen_tasks: set[str] = set()
    for raw_task in tasks:
        task = str(raw_task or "").strip()
        if not task or task in seen_tasks:
            continue
        seen_tasks.add(task)
        argv.extend(["--task", task])

    _append_option(argv, "--java-home", android.get("java_home") or java_options.get("home") or gradle.get("java_home"))
    _append_non_default_option(argv, "--java-provider", java_options.get("provider") or android.get("java_provider", "auto"))
    _append_option(argv, "--sdk-root", android.get("sdk_root"))
    _append_non_default_option(argv, "--sdk-provider", android.get("sdk_provider", "auto"))
    _append_option(argv, "--aapt2", android.get("aapt2"))
    requested_aapt2_provider = str(
        build_tools_options.get("aapt2_provider")
        or android.get("aapt2_provider", "auto")
        or "auto"
    ).strip().lower()
    effective_aapt2_provider = requested_aapt2_provider
    if requested_aapt2_provider == "termux" and _android_execution_mode(config) == "chroot":
        # Termux's AAPT2 is an Android/Bionic executable and cannot be used by
        # the glibc Linux chroot. Let the staged Linux builder use its normal
        # ARM64 provider chain instead; ensure_arm64_build_tools_compatible()
        # installs the Commit451 aarch64-linux-gnu AAPT2 into the selected SDK.
        effective_aapt2_provider = "auto"
    _append_option(argv, "--aapt2-provider", effective_aapt2_provider)
    _append_option(argv, "--aapt2-release", android.get("aapt2_release"))
    _append_option(argv, "--android-platform", _android_platform_option(root, android, project_info))
    _append_option(argv, "--build-tools", build_tools_options.get("version") or (build_tools_value if not isinstance(build_tools_value, dict) else "auto"))
    _append_option(argv, "--arm-build-tools-provider", build_tools_options.get("provider") or android.get("build_tools_provider", "auto"))
    _append_option(argv, "--ndk-version", android.get("ndk_version", "auto"))
    _append_non_default_option(argv, "--ndk-host-provider", android.get("ndk_host_provider", "auto"))
    _append_option(argv, "--arm64-ndk-url", android.get("arm64_ndk_url"))
    _append_option(argv, "--qemu-command", android.get("qemu_command"))
    _append_non_default_option(argv, "--qemu-ld-prefix", android.get("qemu_ld_prefix", "auto"))
    _append_option(argv, "--qemu-sysroot-url", android.get("qemu_sysroot_url"))
    _append_option(argv, "--qemu-sysroot-sha256", android.get("qemu_sysroot_sha256"))
    _append_non_default_option(argv, "--qemu-install", android.get("qemu_install", "auto"))
    _append_option(argv, "--cmake-version", android.get("cmake_version", "auto"))
    _append_non_default_option(argv, "--native-provider", native_options.get("provider") or android.get("native_provider", "auto"))
    _append_non_default_option(argv, "--native-cache", native_options.get("cache") or android.get("native_cache", "auto"))
    _append_option(argv, "--gradle-command", gradle.get("command") or nested_gradle.get("command") or (android.get("gradle") if not isinstance(android.get("gradle"), dict) else None))
    _append_non_default_option(argv, "--gradle-provider", gradle.get("provider") or nested_gradle.get("provider") or "auto")
    _append_option(argv, "--gradle-version", gradle.get("version"))
    _append_option(argv, "--gradle-user-home", gradle.get("user_home") or android.get("gradle_user_home"))
    _append_non_default_option(
        argv,
        "--space-cleanup",
        storage_options.get("auto_cleanup", "safe"),
        default="safe",
    )
    _append_option(argv, "--space-min-free-mb", storage_options.get("min_free_mb", android_build.DEFAULT_ANDROID_SPACE_MIN_FREE_MB))
    _append_option(argv, "--space-target-free-mb", storage_options.get("target_free_mb", android_build.DEFAULT_ANDROID_SPACE_TARGET_FREE_MB))
    _append_option(argv, "--managed-gradle-retention", storage_options.get("managed_gradle_retention", android_build.DEFAULT_ANDROID_MANAGED_GRADLE_RETENTION))
    if bool(storage_options.get("manage_gradle_user_cache", False)):
        argv.append("--manage-gradle-user-cache")
    if bool(storage_options.get("keep_provider_downloads", False)):
        argv.append("--keep-provider-downloads")
    if storage_options.get("cleanup_after_build", True) is False:
        argv.append("--no-post-build-space-cleanup")
    downloads, network = _bootstrap_policy(config)
    _append_option(argv, "--toolchain-downloads", downloads)
    _append_option(argv, "--bootstrap-network", network)
    validation_policy = (
        resolve_android_validation_phase_policy(config, validation_phase)
        if validation and validation_phase
        else resolve_android_validation_policy(config)
        if validation
        else None
    )
    max_workers = (
        validation_policy.max_workers
        if validation_policy is not None
        else gradle.get("max_workers") or android.get("max_workers") or resolve_build_policy(config, config.kind).max_workers
    )
    _append_option(argv, "--max-workers", max_workers)
    _append_option(argv, "--system-deps", android.get("system_deps", "auto"))
    _append_option(argv, "--daemon-idle-timeout-ms", gradle.get("daemon_idle_timeout_ms", 900_000))
    if validation_policy is not None:
        # The Android builder owns the Gradle memory watchdog. Always pass the
        # resolved value, including 0, so the outer validation runner can see
        # that ownership explicitly and never run a second competing guard.
        # This also keeps the builder's process summary truthful: guard=disabled
        # now means no validation watchdog is active anywhere in the stack.
        _append_option(argv, "--memory-guard-mb", validation_policy.memory_guard_mb)

    if validation_policy is not None and validation_policy.enabled:
        validation_options = _dict_option(config, "validation")
        packaging_workload = _is_android_packaging_workload(seen_tasks)
        heap_mb = validation_policy.gradle_heap_mb
        cli_resource_overrides = validation_options.get("_cli_resource_overrides")
        cli_heap_explicit = (
            isinstance(cli_resource_overrides, dict)
            and isinstance(cli_resource_overrides.get("gradle_heap_mb"), dict)
        )
        if packaging_workload and not cli_heap_explicit:
            configured_packaging_heap = _positive_integer(
                os.environ.get("DEVTOOL_GRADLE_PACKAGING_HEAP_MB")
                or validation_options.get("gradle_packaging_heap_mb")
                or gradle.get("validation_packaging_heap_mb")
            )
            heap_mb = configured_packaging_heap or max(int(heap_mb), 1024)
        _append_option(argv, "--gradle-jvm-heap-mb", heap_mb)

        metaspace_record = _cli_override_record(validation_options, "gradle_metaspace_mb")
        raw_env_metaspace = os.environ.get("DEVTOOL_GRADLE_VALIDATION_METASPACE_MB")
        raw_validation_metaspace = validation_options.get("gradle_metaspace_mb")
        raw_gradle_metaspace = gradle.get("validation_metaspace_mb")
        explicit_metaspace_disabled = (
            (metaspace_record is not None and _explicit_zero(metaspace_record.get("value")))
            or (metaspace_record is None and raw_env_metaspace not in {None, ""} and _explicit_zero(raw_env_metaspace))
            or (metaspace_record is None and raw_env_metaspace in {None, ""} and raw_validation_metaspace not in {None, ""} and _explicit_zero(raw_validation_metaspace))
        )

        if explicit_metaspace_disabled:
            argv.append("--no-gradle-metaspace-cap")
            flag = str((metaspace_record or {}).get("flag") or "configured gradle_metaspace_mb=0")
            _append_option(
                argv,
                "--resource-resolution",
                f"gradle_metaspace_mb: {flag} requested=0 -> effective=uncapped-by-devtool (applied)",
            )
        else:
            configured_metaspace = _positive_integer(
                (metaspace_record or {}).get("value")
                if metaspace_record is not None
                else raw_env_metaspace or raw_validation_metaspace or raw_gradle_metaspace
            )
            lint_workload = any("lint" in task.lower() for task in seen_tasks)
            test_workload = any("test" in task.lower() for task in seen_tasks)
            workload_metaspace: int | None = None
            if packaging_workload:
                configured_packaging_metaspace = _positive_integer(
                    os.environ.get("DEVTOOL_GRADLE_PACKAGING_METASPACE_MB")
                    or validation_options.get("gradle_packaging_metaspace_mb")
                    or gradle.get("validation_packaging_metaspace_mb")
                )
                workload_metaspace = max(configured_packaging_metaspace or 0, 768)
            if lint_workload:
                lint_metaspace = _positive_integer(
                    os.environ.get("DEVTOOL_GRADLE_LINT_METASPACE_MB")
                    or validation_options.get("gradle_lint_metaspace_mb")
                    or gradle.get("validation_lint_metaspace_mb")
                )
                workload_metaspace = max(workload_metaspace or 0, lint_metaspace or 0, 640)
            if packaging_workload and lint_workload and test_workload:
                workload_metaspace = max(workload_metaspace or 0, 896)

            metaspace_mb = max(
                configured_metaspace or 0,
                workload_metaspace or 0,
            ) or None
            if metaspace_mb is not None:
                _append_option(argv, "--gradle-metaspace-mb", metaspace_mb)
                if metaspace_record is not None:
                    flag = str(metaspace_record.get("flag") or "--gradle-metaspace-mb")
                    _append_option(
                        argv,
                        "--resource-resolution",
                        f"gradle_metaspace_mb: {flag} requested={metaspace_record.get('value')} -> effective={metaspace_mb} (applied)",
                    )
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
    if validation_policy is not None and not validation_policy.enabled:
        validation_options = _dict_option(config, "validation")
        metaspace_record = _cli_override_record(validation_options, "gradle_metaspace_mb")
        if metaspace_record is not None:
            requested_metaspace = int(metaspace_record.get("value") or 0)
            if requested_metaspace == 0:
                argv.append("--no-gradle-metaspace-cap")
                _append_option(argv, "--resource-resolution", "gradle_metaspace_mb: explicit CLI requested=0 -> effective=uncapped-by-devtool (applied)")
            else:
                _append_option(argv, "--gradle-metaspace-mb", requested_metaspace)
                _append_option(argv, "--resource-resolution", f"gradle_metaspace_mb: explicit CLI requested={requested_metaspace} -> effective={requested_metaspace} (applied)")
        heap_record = _cli_override_record(validation_options, "gradle_heap_mb")
        if heap_record is not None:
            _append_option(argv, "--gradle-jvm-heap-mb", int(heap_record.get("value") or 0))

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
    if validation_policy is not None:
        resolution_phase = validation_phase or "validation"
        termux_options = _dict_option(config, "termux")
        termux_profile = str(termux_options.get("resource_profile") or "balanced")
        termux_applied = bool(termux_options.get("_resource_profile_applied", False))
        _append_option(
            argv,
            "--resource-resolution",
            f"android_safe: effective={str(validation_policy.enabled).lower()} environment={validation_policy.environment}",
        )
        _append_option(
            argv,
            "--resource-resolution",
            f"termux_resource_profile: requested={termux_profile} applied={str(termux_applied).lower()}",
        )
        for note in android_validation_cli_resolution_notes(config, resolution_phase):
            _append_option(argv, "--resource-resolution", note)
    fail_fast_default = bool(validation_policy is not None and validation_policy.enabled)
    fail_fast_value: Any = None
    env_fail_fast = os.environ.get("DEVTOOL_GRADLE_FAIL_FAST", "").strip()
    if env_fail_fast:
        fail_fast_value = env_fail_fast
    elif validation:
        validation_options = _dict_option(config, "validation")
        if "fail_fast" in validation_options:
            fail_fast_value = validation_options.get("fail_fast")
    if fail_fast_value is None and "fail_fast" in gradle:
        fail_fast_value = gradle.get("fail_fast")
    fail_fast = _bool_setting(fail_fast_value, fail_fast_default)
    if fail_fast:
        argv.append("--fail-fast")

    if not scan_artifacts:
        argv.append("--no-artifact-scan")
    else:
        output_dir = artifacts.get("output_directory") or artifacts.get("output_dir")
        if not output_dir and not validation:
            output_dir = ".devtool/build-artifacts"
        output_name = artifacts.get("output_name")
        _append_option(argv, "--output-dir", output_dir)
        _append_option(argv, "--output-name", output_name)
        if bool(artifacts.get("scan", True)) is False:
            argv.append("--no-artifact-scan")

    # The builder treats arguments after ``--`` as direct Gradle arguments.
    # The staged init script emits the exact task/test lifecycle consumed by
    # terminal and native HUD progress, avoiding percentage guesses.
    gradle_args: list[str] = []
    structured_events = bool(gradle.get("structured_events", True))
    if structured_events:
        gradle_args.extend([
            "--init-script",
            str(stage_gradle_init_script()),
        ])

    configured_gradle_args = _list_option(gradle.get("args"))
    disable_configuration_cache = bool(validation or structured_events)
    if disable_configuration_cache:
        # Android validation may provision or repair the managed SDK immediately
        # before Gradle starts. A configuration-cache entry that fingerprints
        # that mutable SDK can therefore be invalidated for reasons unrelated to
        # repository inputs. Validation already has its own durable phase cache,
        # so force Gradle configuration-cache off here. Keep this independent of
        # structured events and make the explicit Devtool policy win over a
        # user-provided --configuration-cache switch.
        configured_gradle_args = [
            item for item in configured_gradle_args
            if item not in {"--configuration-cache", "--no-configuration-cache"}
        ]

    gradle_args.extend(configured_gradle_args)
    if disable_configuration_cache:
        gradle_args.append("--no-configuration-cache")
    if gradle_args:
        argv.append("--")
        argv.extend(gradle_args)
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
    gradle = _dict_option(config, "gradle")
    java_options = android.get("java", {}) if isinstance(android.get("java", {}), dict) else {}
    java_provider = str(java_options.get("provider") or android.get("java_provider") or "auto")
    gradle_provider = str(gradle.get("provider") or "auto")
    native_options = android.get("native", {}) if isinstance(android.get("native", {}), dict) else {}
    storage_options = android.get("storage", {}) if isinstance(android.get("storage", {}), dict) else {}
    build_tools_value = android.get("build_tools", {})
    build_tools_options = build_tools_value if isinstance(build_tools_value, dict) else {}
    native_provider_name = str(native_options.get("provider") or android.get("native_provider") or "auto")
    native_cache = str(native_options.get("cache") or android.get("native_cache") or "auto")
    build_tools_provider = str(build_tools_options.get("provider") or android.get("build_tools_provider") or "auto")
    aapt2_provider = str(build_tools_options.get("aapt2_provider") or android.get("aapt2_provider") or "auto")
    sdk_provider = str(android.get("sdk_provider") or "auto")
    downloads, network = _bootstrap_policy(config)
    tasks = configured_tasks(config, "build")
    test_tasks = configured_tasks(config, "test")
    lint_tasks = configured_tasks(config, "lint")
    return [
        ("project_root", str(project.root)),
        ("project_name", project.name),
        ("project_type", "Android application" if project.android_application else "Android library/Gradle"),
        ("execution_mode", _android_execution_mode(config)),
        ("gradle_wrapper", wrapper_version or "not declared"),
        ("java_provider", java_provider),
        ("gradle_provider", gradle_provider),
        ("native_provider", native_provider_name),
        ("native_cache", native_cache),
        ("android_sdk_provider", sdk_provider),
        ("android_space_cleanup", str(storage_options.get("auto_cleanup", "safe"))),
        (
            "android_space_thresholds",
            f"{storage_options.get('min_free_mb', android_build.DEFAULT_ANDROID_SPACE_MIN_FREE_MB)} MiB trigger / "
            f"{storage_options.get('target_free_mb', android_build.DEFAULT_ANDROID_SPACE_TARGET_FREE_MB)} MiB target",
        ),
        ("android_build_tools_provider", build_tools_provider),
        ("aapt2_provider", aapt2_provider),
        ("minimum_java", str(requirement.minimum) + (" exact toolchain" if requirement.exact else "")),
        ("compile_sdk", str(project.compile_sdk) if project.compile_sdk else "auto/unknown"),
        ("android_gradle_plugin", str(project.agp_major) if project.agp_major else "unknown"),
        ("sdk_install", "manual" if bool(android.get("skip_sdk_install", False)) else "auto"),
        ("bootstrap_downloads", downloads),
        ("bootstrap_network", network),
        ("toolchain_profile", str(config.runner_options.get("toolchain_profile") or config.chroot_name or "")),
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
    gradle = _dict_option(config, "gradle")
    java_options = android.get("java", {}) if isinstance(android.get("java", {}), dict) else {}
    java_provider = str(java_options.get("provider") or android.get("java_provider") or "auto")
    gradle_provider = str(gradle.get("provider") or "auto")
    native_options = android.get("native", {}) if isinstance(android.get("native", {}), dict) else {}
    storage_options = android.get("storage", {}) if isinstance(android.get("storage", {}), dict) else {}
    build_tools_value = android.get("build_tools", {})
    build_tools_options = build_tools_value if isinstance(build_tools_value, dict) else {}
    native_provider_name = str(native_options.get("provider") or android.get("native_provider") or "auto")
    native_cache = str(native_options.get("cache") or android.get("native_cache") or "auto")
    build_tools_provider = str(build_tools_options.get("provider") or android.get("build_tools_provider") or "auto")
    aapt2_provider = str(build_tools_options.get("aapt2_provider") or android.get("aapt2_provider") or "auto")
    sdk_provider = str(android.get("sdk_provider") or "auto")
    downloads, network = _bootstrap_policy(config)
    java_home: Path | None = None
    try:
        java_home = android_build.find_java_home(None, requirement.minimum, exact=requirement.exact, provider=java_provider)
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

    native_requirement = classify_native_requirement(project.root, android=project.android)
    runtime = detect_runtime_identity()
    termux_rows: list[tuple[str, str]] = []
    if runtime.is_termux:
        native_probe = termux_native_provider(runtime=runtime)
        termux_rows.append(("termux_native", f"READY {native_probe.version} @ {native_probe.path}" if native_probe.compatible and native_probe.path else f"UNUSABLE {native_probe.rejection_reason or native_probe.reason}"))
        for label, probe in (("termux_cmake", termux_cmake_probe(runtime=runtime)), ("termux_ninja", termux_ninja_probe(runtime=runtime)), ("termux_ccache", termux_ccache_probe(runtime=runtime))):
            termux_rows.append((label, f"READY {probe.version} @ {probe.path}" if probe.compatible and probe.path else "MISSING/UNUSABLE"))
        tools = termux_android_tool_providers(runtime=runtime)
        termux_rows.append(("termux_android_tools", ", ".join(f"{item.capability}:{'ready' if item.compatible else 'unavailable'}" for item in tools)))

    return [
        ("android_project", "ready" if project.android else "not Android"),
        ("execution_mode", _android_execution_mode(config)),
        ("native_requirement", native_requirement.capability),
        ("native_provider", native_provider_name),
        ("native_cache", native_cache),
        ("android_sdk_provider", sdk_provider),
        ("android_space_cleanup", str(storage_options.get("auto_cleanup", "safe"))),
        (
            "android_space_thresholds",
            f"{storage_options.get('min_free_mb', android_build.DEFAULT_ANDROID_SPACE_MIN_FREE_MB)} MiB trigger / "
            f"{storage_options.get('target_free_mb', android_build.DEFAULT_ANDROID_SPACE_TARGET_FREE_MB)} MiB target",
        ),
        ("android_build_tools_provider", build_tools_provider),
        ("aapt2_provider", aapt2_provider),
        ("java_provider", java_provider),
        ("gradle_provider", gradle_provider),
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
        ("bootstrap_downloads", downloads),
        ("bootstrap_network", network),
        ("toolchain_profile", str(config.runner_options.get("toolchain_profile") or config.chroot_name or "")),
        ("auto_sdk", "yes" if bool(android.get("auto_sdk", False)) else "no"),
        ("android_sdk", str(sdk) if sdk else "missing"),
        ("android_sdk_source", sdk_source or "missing"),
        ("sdkmanager", str(sdkmanager) if sdkmanager else "missing/repairable"),
        ("platform", platform_status),
        ("build_tools", build_tools[0] if build_tools else "missing/repairable"),
        ("aapt2", aapt2_status),
        ("builder", str(stage_android_builder())),
        *termux_rows,
    ]


def _termux_chroot_project_kinds(config: ProjectConfig) -> set[str]:
    options = _dict_option(config, "termux_chroot")
    raw = options.get("project_kinds", ["android", "gradle"])
    if isinstance(raw, str):
        values = [item.strip().lower() for item in raw.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        values = [str(item).strip().lower() for item in raw]
    else:
        values = []
    return {value for value in values if value}


def should_prefer_android_chroot(config: ProjectConfig) -> bool:
    """Return whether Termux auto mode should use the configured chroot.

    The historical name is kept for API compatibility. The policy now supports
    non-Android project kinds through ``[termux_chroot] project_kinds`` in the
    repo or sparse global config. Native Termux remains available explicitly via
    ``--host``, ``--execution-environment native-termux``, or Android
    ``prefer_native_termux = true``.
    """

    # Chroot auto-selection is a Termux-host policy. A desktop Linux machine
    # may have a command named chrootctl, but that must never opt a project into
    # the Android/Termux chroot backend.
    if detect_current_environment() != NATIVE_TERMUX:
        return False
    allowed = _termux_chroot_project_kinds(config)
    if "all" not in allowed and config.kind not in allowed:
        return False
    android = _dict_option(config, "android")
    if config.kind in {"android", "gradle"} and bool(android.get("prefer_native_termux", False)):
        return False
    return bool(shutil.which(os.environ.get("DEVTOOL_CHROOTCTL", "chrootctl")))
