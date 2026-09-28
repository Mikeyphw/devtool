from __future__ import annotations

"""Language-neutral test discovery, selection, profiles, and command translation.

The catalog is intentionally lightweight and source based.  It gives the CLI and
applications a stable selection model without requiring every framework to be
installed merely to browse tests.  Runners still use their native selectors for
execution, and report parsers remain authoritative for final counts.
"""

from dataclasses import asdict, dataclass, field
import ast
import fnmatch
import json
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any, Iterable

from .models import CommandSpec, ProjectConfig


@dataclass(frozen=True, slots=True)
class TestDescriptor:
    __test__ = False
    id: str
    name: str
    suite: str
    project: str
    framework: str
    test_type: str = "unit"
    tags: tuple[str, ...] = ()
    file: str = ""
    line: int | None = None
    environment: str = "host"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["type"] = payload.pop("test_type")
        payload["tags"] = list(self.tags)
        return payload


@dataclass(slots=True)
class TestSelection:
    __test__ = False
    profile: str = ""
    selectors: list[str] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    exclude_tags: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    native_filter: str = ""
    task: str = ""
    tasks: list[str] = field(default_factory=list)
    failed: bool = False
    changed: bool = False
    changed_from: str = "HEAD"
    instrumented: bool = False
    # Pytest-native selection. ``pytest_tests`` is intentionally distinct from
    # the language-neutral ``ids`` field: its presence means Devtool must treat
    # the listed node IDs/files as the complete pytest execution scope unless
    # ``pytest_include_defaults`` is explicitly enabled.
    pytest_tests: list[str] = field(default_factory=list)
    pytest_markers: list[str] = field(default_factory=list)
    pytest_k: str = ""
    pytest_args: list[str] = field(default_factory=list)
    pytest_include_defaults: bool = False
    pytest_source: str = ""

    def active(self) -> bool:
        return any((
            self.profile,
            self.selectors,
            self.ids,
            self.tags,
            self.exclude_tags,
            self.paths,
            self.classes,
            self.methods,
            self.native_filter,
            self.task,
            self.tasks,
            self.failed,
            self.changed,
            self.instrumented,
            self.pytest_tests,
            self.pytest_markers,
            self.pytest_k,
            self.pytest_args,
        ))

    def exact_pytest(self) -> bool:
        """Whether pytest has an explicit complete selector list."""
        return bool(self.pytest_tests)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)




def _strict_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled", "none", "null", ""}:
        return False
    return default

def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def resolve_test_selection(config: ProjectConfig) -> TestSelection:
    raw_value = config.runner_options.get("test_selection", {})
    raw = dict(raw_value) if isinstance(raw_value, dict) else {}

    python_options = config.runner_options.get("python", {})
    python_options = dict(python_options) if isinstance(python_options, dict) else {}
    pytest_defaults = python_options.get("pytest", {})
    pytest_defaults = dict(pytest_defaults) if isinstance(pytest_defaults, dict) else {}

    selection = TestSelection(
        profile=str(raw.get("profile") or "").strip(),
        selectors=_list(raw.get("selectors") or raw.get("select")),
        ids=_list(raw.get("ids") or raw.get("id")),
        tags=_list(raw.get("tags") or raw.get("tag")),
        exclude_tags=_list(raw.get("exclude_tags") or raw.get("exclude_tag")),
        paths=_list(raw.get("paths") or raw.get("path")),
        classes=_list(raw.get("classes") or raw.get("class")),
        methods=_list(raw.get("methods") or raw.get("method")),
        native_filter=str(raw.get("filter") or "").strip(),
        task="",
        tasks=_list(raw.get("tasks") or raw.get("task")),
        failed=_strict_bool(raw.get("failed", False), False),
        changed=_strict_bool(raw.get("changed", False), False),
        changed_from=str(raw.get("changed_from") or "HEAD").strip() or "HEAD",
        instrumented=_strict_bool(raw.get("instrumented", False), False),
        pytest_tests=_list(pytest_defaults.get("tests")),
        pytest_markers=_list(pytest_defaults.get("markers") or pytest_defaults.get("marker")),
        pytest_k=str(pytest_defaults.get("k") or "").strip(),
        pytest_args=_list(pytest_defaults.get("args")),
        pytest_include_defaults=_strict_bool(pytest_defaults.get("include_defaults", False), False),
        pytest_source="project-config" if _list(pytest_defaults.get("tests")) else "",
    )

    profiles = config.runner_options.get("test_profiles", {})
    profiles = dict(profiles) if isinstance(profiles, dict) else {}
    if selection.profile:
        profile = profiles.get(selection.profile)
        if not isinstance(profile, dict):
            available = ", ".join(sorted(profiles)) or "<none>"
            raise SystemExit(
                f"Unknown test profile '{selection.profile}'. Available profiles: {available}"
            )
        selection.selectors = _list(profile.get("include") or profile.get("select")) + selection.selectors
        selection.ids = _list(profile.get("ids")) + selection.ids
        selection.tags = _list(profile.get("tags")) + selection.tags
        selection.exclude_tags = _list(profile.get("exclude_tags")) + selection.exclude_tags
        selection.paths = _list(profile.get("paths")) + selection.paths
        selection.classes = _list(profile.get("classes")) + selection.classes
        selection.methods = _list(profile.get("methods")) + selection.methods
        selection.native_filter = selection.native_filter or str(profile.get("filter") or "").strip()
        profile_tasks = _list(profile.get("tasks") or profile.get("task"))
        selection.tasks = profile_tasks + selection.tasks
        selection.failed = selection.failed or _strict_bool(profile.get("failed", False), False)
        selection.changed = selection.changed or str(profile.get("selection") or "").strip().lower() == "changed" or _strict_bool(profile.get("changed", False), False)
        selection.changed_from = str(profile.get("base") or selection.changed_from).strip() or "HEAD"
        selection.instrumented = selection.instrumented or _strict_bool(profile.get("instrumented", False), False)
        if "pytest_tests" in profile:
            selection.pytest_tests = _list(profile.get("pytest_tests"))
            selection.pytest_source = f"profile:{selection.profile}" if selection.pytest_tests else ""
        if "pytest_markers" in profile:
            selection.pytest_markers = _list(profile.get("pytest_markers"))
        if "pytest_k" in profile:
            selection.pytest_k = str(profile.get("pytest_k") or "").strip()
        if "pytest_args" in profile:
            selection.pytest_args = _list(profile.get("pytest_args"))
        if "pytest_include_defaults" in profile:
            selection.pytest_include_defaults = _strict_bool(profile.get("pytest_include_defaults"), False)

    # Invocation/artifact selection is deliberately applied after profiles and
    # project defaults. This gives explicit requests the same precedence model
    # as repeated Gradle --task values.
    if "pytest_tests" in raw:
        selection.pytest_tests = _list(raw.get("pytest_tests"))
        selection.pytest_source = str(raw.get("pytest_source") or "explicit").strip() or "explicit"
    if "pytest_markers" in raw:
        selection.pytest_markers = _list(raw.get("pytest_markers"))
    if "pytest_k" in raw:
        selection.pytest_k = str(raw.get("pytest_k") or "").strip()
    if "pytest_args" in raw:
        selection.pytest_args = _list(raw.get("pytest_args"))
    if "pytest_include_defaults" in raw:
        selection.pytest_include_defaults = _strict_bool(raw.get("pytest_include_defaults"), False)

    if selection.failed:
        failed_ids = load_failed_test_ids(config.repo_root)
        if not failed_ids:
            raise SystemExit("No failed tests are recorded for this repository.")
        selection.ids.extend(failed_ids)
    if selection.changed:
        changed = changed_paths(config.repo_root, selection.changed_from, project_root=Path(config.project_root or config.repo_root))
        if not changed:
            raise SystemExit(f"No changed files were found relative to {selection.changed_from}.")
        selection.paths.extend(changed)

    # Stable de-duplication keeps generated commands deterministic.
    for name in (
        "selectors", "ids", "tags", "exclude_tags", "paths", "classes",
        "methods", "tasks", "pytest_tests", "pytest_markers", "pytest_args",
    ):
        values = getattr(selection, name)
        setattr(selection, name, list(dict.fromkeys(values)))
    selection.task = selection.tasks[0] if selection.tasks else ""
    return selection


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _python_test_type(tags: Iterable[str], path: Path) -> str:
    tokens = {str(tag).lower() for tag in tags}
    for part in path.parts:
        tokens.update(token for token in re.split(r"[^a-z0-9]+", part.lower()) if token)
    for name in ("instrumentation", "integration", "smoke", "ui", "unit"):
        if name in tokens:
            return name
    return "unit"


def discover_python_tests(root: Path) -> list[TestDescriptor]:
    tests: list[TestDescriptor] = []
    candidates = sorted({*root.glob("test_*.py"), *root.glob("**/test_*.py"), *root.glob("**/*_test.py")})
    ignored = {".git", ".venv", "venv", "build", "dist", ".tox", ".nox", "__pycache__"}
    for path in candidates:
        if any(part in ignored for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        rel = _relative(root, path)
        module = rel[:-3].replace("/", ".") if rel.endswith(".py") else rel.replace("/", ".")
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                tags = tuple(_pytest_marks(node))
                tests.append(TestDescriptor(
                    id=f"{rel}::{node.name}",
                    name=node.name,
                    suite=module,
                    project=root.name,
                    framework="pytest",
                    test_type=_python_test_type(tags, path),
                    tags=tags,
                    file=rel,
                    line=node.lineno,
                ))
            elif isinstance(node, ast.ClassDef) and (node.name.startswith("Test") or _inherits_unittest(node)):
                class_tags = _pytest_marks(node)
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test"):
                        tags = tuple(dict.fromkeys([*class_tags, *_pytest_marks(child)]))
                        tests.append(TestDescriptor(
                            id=f"{rel}::{node.name}::{child.name}",
                            name=child.name,
                            suite=f"{module}.{node.name}",
                            project=root.name,
                            framework="pytest" if node.name.startswith("Test") else "unittest",
                            test_type=_python_test_type(tags, path),
                            tags=tags,
                            file=rel,
                            line=child.lineno,
                        ))
    return tests


def _pytest_marks(node: ast.AST) -> list[str]:
    values: list[str] = []
    for decorator in getattr(node, "decorator_list", []):
        current = decorator.func if isinstance(decorator, ast.Call) else decorator
        parts: list[str] = []
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        dotted = ".".join(reversed(parts))
        if ".mark." in dotted:
            values.append(dotted.rsplit(".", 1)[-1])
    return values


def _inherits_unittest(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Attribute) and base.attr == "TestCase":
            return True
        if isinstance(base, ast.Name) and base.id == "TestCase":
            return True
    return False


_DOTNET_ATTRIBUTE = re.compile(r"\[(?P<attrs>[^\]]*(?:Fact|Theory|Test|TestMethod)[^\]]*)\]", re.I)
_DOTNET_METHOD = re.compile(r"\b(?:public|internal|private|protected)?\s*(?:async\s+)?(?:Task|ValueTask|void|[A-Za-z_][\w<>,.?\[\]]*)\s+(?P<name>[A-Za-z_]\w*)\s*\(")
_DOTNET_CLASS = re.compile(r"\bclass\s+(?P<name>[A-Za-z_]\w*)")
_DOTNET_NAMESPACE = re.compile(r"\bnamespace\s+(?P<name>[A-Za-z_][\w.]*)")
_DOTNET_TAG = re.compile(r"(?:Trait\s*\(\s*\"(?:Category|Type)\"\s*,\s*\"([^\"]+)\"|Category\s*\(\s*\"([^\"]+)\")", re.I)


def discover_dotnet_tests(root: Path) -> list[TestDescriptor]:
    tests: list[TestDescriptor] = []
    for path in sorted(root.glob("**/*.cs")):
        if any(part in {"bin", "obj", ".git", "generated"} for part in path.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        namespace = ""
        class_name = ""
        pending_attrs: list[str] = []
        for index, line in enumerate(lines, start=1):
            ns = _DOTNET_NAMESPACE.search(line)
            if ns:
                namespace = ns.group("name")
            cls = _DOTNET_CLASS.search(line)
            if cls:
                class_name = cls.group("name")
            attr = _DOTNET_ATTRIBUTE.search(line)
            if attr:
                pending_attrs.append(attr.group("attrs"))
                continue
            if not pending_attrs:
                continue
            method = _DOTNET_METHOD.search(line)
            if not method:
                if line.strip() and not line.lstrip().startswith("["):
                    pending_attrs.clear()
                continue
            name = method.group("name")
            suite = ".".join(item for item in (namespace, class_name) if item)
            fqn = ".".join(item for item in (suite, name) if item)
            tags: list[str] = []
            attrs_text = " ".join(pending_attrs)
            for match in _DOTNET_TAG.finditer(attrs_text):
                tags.append(next(item for item in match.groups() if item))
            rel = _relative(root, path)
            project = _nearest_project(path, root)
            tests.append(TestDescriptor(
                id=fqn or f"{rel}:{index}:{name}",
                name=name,
                suite=suite,
                project=project,
                framework="dotnet",
                test_type=_python_test_type(tags, path),
                tags=tuple(dict.fromkeys(tags)),
                file=rel,
                line=index,
            ))
            pending_attrs.clear()
    return tests


def _nearest_project(path: Path, root: Path) -> str:
    for parent in [path.parent, *path.parents]:
        projects = sorted(parent.glob("*.csproj"))
        if projects:
            return projects[0].stem
        if parent == root:
            break
    return root.name


_JAVA_TEST_METHOD = re.compile(
    r"(?P<annotations>(?:@[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:\s*\([^)]*\))?\s*)+)"
    r"(?:(?:public|protected|private|internal|suspend|static|final|open|override|abstract)\s+)*"
    r"(?:fun\s+(?P<kotlin>[A-Za-z_]\w*)\s*\(|"
    r"(?:void|[A-Za-z_]\w*(?:[.<>,?\[\]\s]+[A-Za-z0-9_?\[\]]+)?)\s+(?P<java>[A-Za-z_]\w*)\s*\()",
    re.S,
)
_JAVA_CLASS = re.compile(r"\b(?:class|object)\s+(\w+)")
_JAVA_PACKAGE = re.compile(r"\bpackage\s+([\w.]+)")
_JAVA_TAG = re.compile(r"@(?:Tag|Category)\s*\(\s*(?:\"([^\"]+)\"|([A-Za-z_]\w*)\.class)")


def discover_gradle_tests(root: Path, *, android: bool) -> list[TestDescriptor]:
    tests: list[TestDescriptor] = []
    patterns = ("**/src/test/**/*.kt", "**/src/test/**/*.java", "**/src/androidTest/**/*.kt", "**/src/androidTest/**/*.java")
    candidates: set[Path] = set()
    for pattern in patterns:
        candidates.update(root.glob(pattern))
    for path in sorted(candidates):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        package_match = _JAVA_PACKAGE.search(text)
        package = package_match.group(1) if package_match else ""
        class_match = _JAVA_CLASS.search(text)
        class_name = class_match.group(1) if class_match else path.stem
        suite = ".".join(item for item in (package, class_name) if item)
        class_prefix = text[: class_match.start()] if class_match else ""
        class_tags = tuple(dict.fromkeys(
            next(item for item in match.groups() if item) for match in _JAVA_TAG.finditer(class_prefix)
        ))
        instrumentation = "/androidTest/" in path.as_posix()
        framework = "android-instrumentation" if instrumentation else "gradle-junit"
        rel = _relative(root, path)
        for match in _JAVA_TEST_METHOD.finditer(text):
            annotation_region = match.group("annotations") or ""
            if not re.search(r"@Test\b", annotation_region):
                continue
            name = match.group("kotlin") or match.group("java") or ""
            if not name:
                continue
            method_tags = tuple(dict.fromkeys(
                next(item for item in tag.groups() if item) for tag in _JAVA_TAG.finditer(annotation_region)
            ))
            tags = tuple(dict.fromkeys((*class_tags, *method_tags)))
            line = text.count("\n", 0, match.start()) + 1
            tests.append(TestDescriptor(
                id=f"{suite}.{name}" if suite else name,
                name=name, suite=suite, project=_gradle_module(path, root),
                framework=framework,
                test_type="instrumentation" if instrumentation else _python_test_type(tags, path),
                tags=tags, file=rel, line=line,
                environment="android-device" if instrumentation else "host",
            ))
    return tests


def _gradle_module(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return root.name
    parts = relative.parts
    try:
        index = parts.index("src")
    except ValueError:
        return root.name
    return ":".join(parts[:index]) or root.name


@dataclass(frozen=True, slots=True)
class TestDiscoveryResult:
    # Public API model, not a pytest test class. The explicit marker prevents
    # pytest from attempting to collect this imported dataclass during the
    # repository-wide final-seal inventory.
    __test__ = False

    tests: tuple[TestDescriptor, ...]
    source: str
    error: str = ""


def _parse_pytest_collect_output(root: Path, text: str) -> list[TestDescriptor]:
    """Parse ``pytest --collect-only -q`` node IDs without depending on plugins."""
    source_catalog = {item.id: item for item in discover_python_tests(root)}
    tests: list[TestDescriptor] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "::" not in line or line.startswith(("=", "<", "ERROR", "WARNING")):
            continue
        # Quiet collection output is one nodeid per line. Strip optional status
        # prefixes emitted by a few plugins but never reinterpret whitespace
        # inside parametrized IDs.
        nodeid = line
        if nodeid.startswith(("PASSED ", "FAILED ", "SKIPPED ")):
            nodeid = nodeid.split(" ", 1)[1].strip()
        if "::" not in nodeid or nodeid in seen:
            continue
        file_part = nodeid.split("::", 1)[0].replace("\\", "/")
        if not file_part.endswith(".py"):
            continue
        seen.add(nodeid)
        source = source_catalog.get(nodeid)
        parts = nodeid.split("::")
        name = parts[-1]
        # Parametrization suffix belongs to the executable node ID but not the
        # display name used for search/grouping.
        display_name = name.split("[", 1)[0]
        suite_parts = parts[1:-1]
        suite = ".".join([file_part[:-3].replace("/", "."), *suite_parts])
        tests.append(TestDescriptor(
            id=nodeid,
            name=source.name if source else display_name,
            suite=source.suite if source else suite,
            project=root.name,
            framework="pytest",
            test_type=source.test_type if source else "unit",
            tags=source.tags if source else (),
            file=source.file if source else file_part,
            line=source.line if source else None,
        ))
    return tests


def discover_pytest_runtime(config: ProjectConfig) -> TestDiscoveryResult:
    """Collect pytest's real executable node IDs using the configured Python runner.

    Collection is intentionally an opt-in UI/list operation; normal command
    planning remains side-effect free and continues to use the source catalog
    only for safe filter translation.
    """
    try:
        from .project_runners import get_project_runner
        runner = get_project_runner(config)
        builder = getattr(runner, "build_test_collection_spec", None)
        if not callable(builder):
            return TestDiscoveryResult(tuple(discover_tests(config)), "source", "runner does not support native test collection")
        spec = builder(config)
        completed = subprocess.run(
            list(spec.argv),
            cwd=spec.cwd or str(config.repo_root),
            env={**__import__("os").environ, **config.env, **spec.env},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=90,
        )
        root = Path(config.project_root or config.repo_root).expanduser().resolve()
        tests = _parse_pytest_collect_output(root, completed.stdout or "")
        if completed.returncode in {0, 5} and tests:
            return TestDiscoveryResult(tuple(sorted(tests, key=lambda item: item.id)), "pytest-collect")
        source = tuple(discover_python_tests(root))
        detail = (completed.stdout or "").strip()
        if len(detail) > 2000:
            detail = detail[-2000:]
        return TestDiscoveryResult(source, "source-fallback", detail or f"pytest collection exited {completed.returncode}")
    except (OSError, subprocess.TimeoutExpired, SystemExit) as exc:
        root = Path(config.project_root or config.repo_root).expanduser().resolve()
        return TestDiscoveryResult(tuple(discover_python_tests(root)), "source-fallback", str(exc))


def discover_tests_for_ui(config: ProjectConfig) -> TestDiscoveryResult:
    if config.kind == "python":
        try:
            from .project_runners import get_project_runner
            runner = get_project_runner(config)
            info_fn = getattr(runner, "_info", None)
            framework = str(info_fn(config).framework if callable(info_fn) else "").strip().lower()
        except (OSError, SystemExit, ValueError):
            framework = ""
        if framework == "pytest":
            return discover_pytest_runtime(config)
    return TestDiscoveryResult(tuple(discover_tests(config)), "source")


def discover_tests(config: ProjectConfig) -> list[TestDescriptor]:
    root = Path(config.project_root or config.repo_root).resolve()
    if config.kind == "python":
        tests = discover_python_tests(root)
    elif config.kind == "dotnet":
        tests = discover_dotnet_tests(root)
    elif config.kind in {"android", "gradle"}:
        tests = discover_gradle_tests(root, android=config.kind == "android")
    else:
        tests = []
    return sorted(tests, key=lambda item: (item.project, item.suite, item.name, item.id))


def select_catalog(catalog: Iterable[TestDescriptor], selection: TestSelection) -> list[TestDescriptor]:
    items = list(catalog)
    if selection.instrumented:
        items = [item for item in items if item.test_type == "instrumentation"]
    if selection.paths:
        normalized_paths = [value.replace("\\", "/").lstrip("./") for value in selection.paths]
        def path_matches(item: TestDescriptor) -> bool:
            for normalized in normalized_paths:
                stem = Path(normalized).stem.lower()
                if fnmatch.fnmatch(item.file, normalized) or item.file.startswith(normalized.rstrip("/") + "/"):
                    return True
                if stem and (stem in item.file.lower() or stem in item.suite.lower() or stem in item.name.lower()):
                    return True
            return False
        items = [item for item in items if path_matches(item)]
    if selection.ids:
        # Explicit IDs are exact identities.  Substring matching made a request
        # for ``FooTest.case`` also select ``FooTest.caseSlow`` and could
        # silently broaden a supposedly exact run.
        wanted = set(selection.ids)
        items = [item for item in items if item.id in wanted]
    if selection.classes:
        items = [item for item in items if any(token in item.suite for token in selection.classes)]
    if selection.methods:
        items = [item for item in items if any(token in item.name or token in item.id for token in selection.methods)]
    if selection.selectors:
        lowered = [value.lower() for value in selection.selectors]
        items = [
            item for item in items
            if any(
                token in item.id.lower()
                or token in item.suite.lower()
                or token == item.test_type.lower()
                or token in (tag.lower() for tag in item.tags)
                for token in lowered
            )
        ]
    if selection.tags:
        wanted = {value.lower() for value in selection.tags}
        items = [item for item in items if wanted.intersection(tag.lower() for tag in item.tags)]
    if selection.exclude_tags:
        excluded = {value.lower() for value in selection.exclude_tags}
        items = [item for item in items if not excluded.intersection(tag.lower() for tag in item.tags)]
    return items


def _append_gradle_arg(argv: list[str], *values: str) -> None:
    try:
        separator = argv.index("--")
    except ValueError:
        argv.append("--")
        separator = len(argv) - 1
    argv.extend(values)


def _native_test_values(selection: TestSelection) -> list[str]:
    values: list[str] = []
    values.extend(selection.ids)
    values.extend(selection.classes)
    values.extend(selection.methods)
    values.extend(selection.selectors)
    values.extend(selection.paths)
    values.extend(selection.tags)
    return list(dict.fromkeys(value for value in values if value))




def _regex_union_exact(values: Iterable[str]) -> str:
    escaped = [re.escape(str(value)) for value in dict.fromkeys(values) if str(value)]
    if not escaped:
        return ""
    return "^(?:" + "|".join(escaped) + ")$"


def _shlex_filter(value: str, *, context: str) -> list[str]:
    try:
        return shlex.split(value)
    except ValueError as exc:
        raise SystemExit(f"Invalid shell-style test filter for {context}: {exc}") from exc


def _safe_dotnet_filter_value(value: str, *, context: str) -> str:
    text = str(value).strip()
    if not text:
        raise SystemExit(f"Empty .NET test selector for {context}")
    if any(ch in text for ch in ('|', '&', '!', '(', ')')):
        raise SystemExit(f"Unsafe .NET test selector for {context}: {text!r}; use native filter for complex expressions")
    return text


def _maven_exact_test_value(value: str) -> str:
    text = str(value).strip()
    if not text:
        return ""
    allowed = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$.]*(?:#[A-Za-z_$][A-Za-z0-9_$]*(?:\[[^,*!?]+\])?)?$")
    if not allowed.match(text):
        raise SystemExit(
            f"Maven test selector {text!r} cannot be translated as an exact Surefire test. "
            "Use class names, Class#method syntax, tags/groups, tasks, or a native filter."
        )
    return text


def _maven_group_value(value: str, *, context: str) -> str:
    text = str(value).strip()
    if not text or any(ch in text for ch in ",*!?"):
        raise SystemExit(f"Unsafe Maven {context} selector: {text!r}; use a native filter for complex expressions")
    return text


def _unsupported_generic_selection(selection: TestSelection) -> bool:
    return any((
        selection.selectors, selection.ids, selection.tags, selection.exclude_tags,
        selection.paths, selection.classes, selection.methods, selection.native_filter,
    ))

def apply_test_selection(config: ProjectConfig, spec: CommandSpec) -> CommandSpec:
    selection = resolve_test_selection(config)
    contains_tests = bool(spec.metadata.get("contains_tests", False))
    if not selection.active() or (spec.name not in {"test", "instrumentation"} and not contains_tests):
        return spec

    argv = list(spec.argv)
    metadata = dict(spec.metadata)
    catalog = discover_tests(config)
    selected_catalog = select_catalog(catalog, selection)
    metadata["test_selection"] = selection.as_dict()
    if config.selected_target:
        metadata["target"] = config.selected_target
    if config.variant:
        metadata["variant"] = config.variant
    if catalog:
        metadata["test_catalog_discovered"] = len(catalog)
        metadata["test_catalog_selected"] = len(selected_catalog) if selection.active() else len(catalog)
        metadata["selected_test_ids"] = [item.id for item in selected_catalog]

    if config.kind == "dotnet":
        positive: list[str] = []
        negative: list[str] = []
        positive.extend(f"FullyQualifiedName={_safe_dotnet_filter_value(value, context='id')}" for value in selection.ids)
        positive.extend(f"FullyQualifiedName~{_safe_dotnet_filter_value(value, context='class')}" for value in selection.classes)
        positive.extend(f"FullyQualifiedName~{_safe_dotnet_filter_value(value, context='method')}" for value in selection.methods)
        for value in selection.selectors:
            if value.lower() in {"unit", "integration", "smoke", "ui", "instrumentation"}:
                positive.append(f"Category={_safe_dotnet_filter_value(value, context='category')}")
            else:
                positive.append(f"FullyQualifiedName~{_safe_dotnet_filter_value(value, context='selector')}")
        positive.extend(f"Category={_safe_dotnet_filter_value(value, context='tag')}" for value in selection.tags)
        negative.extend(f"Category!={_safe_dotnet_filter_value(value, context='exclude_tag')}" for value in selection.exclude_tags)
        positive.extend(f"FullyQualifiedName~{_safe_dotnet_filter_value(Path(value).stem, context='path')}" for value in selection.paths if not value.endswith((".csproj", ".sln")))
        native = selection.native_filter
        if not native:
            positive_group = "|".join(positive)
            parts = ([f"({positive_group})"] if positive_group else []) + negative
            native = "&".join(parts)
        if native:
            argv.extend(["--filter", native])
    elif config.kind in {"android", "gradle"} and not selection.instrumented:
        if catalog and selection.active() and not selected_catalog:
            raise SystemExit("Gradle test selection matched zero discovered tests; refusing to run the full suite.")
        patterns: list[str] = list(selection.ids)
        catalog_driven = bool(
            selection.classes or selection.methods or selection.paths or selection.tags
            or selection.exclude_tags or selection.selectors
        )
        if selected_catalog and catalog_driven:
            patterns.extend(item.id for item in selected_catalog)
        elif not catalog:
            patterns.extend(selection.classes)
            patterns.extend(selection.methods)
            patterns.extend(value for value in selection.selectors if value.lower() not in {"unit", "integration", "smoke", "ui"})
        for value in dict.fromkeys(patterns):
            if bool(metadata.get("gradle_direct")):
                argv.extend(["--tests", value])
            else:
                _append_gradle_arg(argv, "--tests", value)
        if selection.native_filter:
            if bool(metadata.get("gradle_direct")):
                argv.extend(shlex.split(selection.native_filter))
            else:
                _append_gradle_arg(argv, *shlex.split(selection.native_filter))
    elif config.kind == "python":
        framework = str(metadata.get("test_framework") or "pytest").strip().lower()
        # Selection syntax is framework-specific. Never append pytest's -k/-m
        # switches to tox, nox, unittest, or an arbitrary custom command.
        if framework == "pytest":
            direct = [*selection.pytest_tests, *selection.ids]
            if selection.paths:
                if catalog and not selected_catalog:
                    raise SystemExit("Python test selection matched zero discovered tests; refusing to run the full suite.")
                if selected_catalog:
                    direct.extend(item.id for item in selected_catalog)
                elif not catalog:
                    direct.extend(selection.paths)
            if selected_catalog and (selection.classes or selection.methods):
                direct.extend(item.id for item in selected_catalog)
            argv.extend(dict.fromkeys(direct))

            generic_expressions = [*selection.selectors, *selection.classes, *selection.methods]
            if selection.native_filter:
                generic_expressions.append(selection.native_filter)
            generic_k = " or ".join(f"({value})" for value in generic_expressions if value)
            if generic_k and selection.pytest_k:
                argv.extend(["-k", f"({generic_k}) and ({selection.pytest_k})"])
            elif generic_k:
                argv.extend(["-k", generic_k])
            elif selection.pytest_k:
                argv.extend(["-k", selection.pytest_k])

            positive_markers = [value for value in selection.tags if value]
            negative_markers = [value for value in selection.exclude_tags if value]
            marker_parts: list[str] = []
            if positive_markers:
                marker_parts.append("(" + " or ".join(positive_markers) + ")")
            marker_parts.extend(f"not {value}" for value in negative_markers)
            marker_parts.extend(f"({value})" for value in selection.pytest_markers if value)
            if marker_parts:
                argv.extend(["-m", " and ".join(marker_parts)])
            argv.extend(selection.pytest_args)
            metadata["pytest_selection_mode"] = "explicit" if selection.exact_pytest() else "filtered"
            metadata["pytest_selected_tests"] = list(selection.pytest_tests)
            metadata["pytest_selected_count"] = len(selection.pytest_tests)
            metadata["pytest_selection_source"] = selection.pytest_source
            metadata["pytest_include_defaults"] = bool(selection.pytest_include_defaults)
        else:
            raise SystemExit(
                f"Test selection is not implemented safely for Python framework '{framework}'; "
                "refusing to broaden the request to the full suite."
            )
    elif config.kind == "node":
        values = _native_test_values(selection)
        if selection.native_filter:
            argv.extend(["--", *_shlex_filter(selection.native_filter, context="node")])
        elif values:
            raise SystemExit(
                "Node test selection cannot safely translate generic selectors to package-manager script arguments. "
                "Use test_selection.tasks for package.json scripts or test_selection.filter for native runner args."
            )
    elif config.kind == "rust":
        values = _native_test_values(selection)
        if selection.native_filter:
            values = _shlex_filter(selection.native_filter, context="rust")
        if values:
            try:
                separator = argv.index("--")
            except ValueError:
                separator = len(argv)
            argv[separator:separator] = values
    elif config.kind == "go":
        if selection.tags or selection.exclude_tags or selection.classes:
            raise SystemExit(
                "Go test selection cannot safely translate tags/classes to native semantics; "
                "use explicit test names, paths/changed selection, or test_selection.filter."
            )
        if selection.paths:
            from .go_environment import packages_for_changed_paths
            root = Path(config.project_root or config.repo_root).expanduser().resolve()
            packages = list(packages_for_changed_paths(root, selection.paths))
            if not packages:
                raise SystemExit("Go path/changed test selection resolved to zero packages; refusing a false-green run.")
            original_packages = [str(item) for item in metadata.get("go_packages", []) if str(item)]
            if original_packages and argv[-len(original_packages):] == original_packages:
                argv = argv[:-len(original_packages)]
            argv.extend(packages)
            metadata["go_selected_packages"] = packages
            metadata["go_selection_mode"] = "package-path"
        explicit_names = [*selection.ids, *selection.methods, *selection.selectors]
        if explicit_names and selection.paths:
            raise SystemExit(
                "Go path/changed package selection cannot be combined with generic test-name selection without a proven catalog mapping."
            )
        pattern = selection.native_filter or _regex_union_exact(explicit_names)
        if pattern:
            try:
                index = argv.index("test")
            except ValueError:
                index = 1
            if "-run" not in argv:
                argv[index + 1:index + 1] = ["-run", pattern]
            metadata["go_selection_mode"] = "test-name"
    elif config.kind == "maven":
        if selection.native_filter:
            argv.append(f"-Dtest={selection.native_filter}")
        else:
            test_values = [*selection.ids, *selection.classes, *selection.methods, *selection.selectors]
            path_values = [Path(value).stem for value in selection.paths if not str(value).endswith(("pom.xml", ".xml"))]
            exact_tests = [_maven_exact_test_value(value) for value in [*test_values, *path_values]]
            exact_tests = [value for value in dict.fromkeys(exact_tests) if value]
            if exact_tests:
                argv.append("-Dtest=" + ",".join(exact_tests))
            if selection.tags:
                groups = [_maven_group_value(value, context="tag") for value in selection.tags]
                argv.append("-Dgroups=" + ",".join(dict.fromkeys(groups)))
            if selection.exclude_tags:
                groups = [_maven_group_value(value, context="exclude_tag") for value in selection.exclude_tags]
                argv.append("-DexcludedGroups=" + ",".join(dict.fromkeys(groups)))
    elif config.kind == "cmake":
        values = _native_test_values(selection)
        pattern = selection.native_filter or _regex_union_exact(values)
        if pattern:
            argv.extend(["-R", pattern])
        if selection.exclude_tags:
            argv.extend(["-E", _regex_union_exact(selection.exclude_tags)])
    elif config.kind == "meson":
        values = _native_test_values(selection)
        if selection.native_filter:
            values.extend(_shlex_filter(selection.native_filter, context="meson"))
        argv.extend(dict.fromkeys(values))

    return CommandSpec(
        spec.name,
        argv,
        env=spec.env,
        cwd=spec.cwd,
        destructive=spec.destructive,
        metadata=metadata,
    )


def changed_paths(repo_root: Path, base: str, *, project_root: Path | None = None) -> list[str]:
    root = repo_root.expanduser().resolve()
    project = (project_root or root).expanduser().resolve()
    try:
        project_prefix = project.relative_to(root).as_posix()
        if project_prefix == ".":
            project_prefix = ""
    except ValueError:
        project_prefix = ""
    commands = (
        ["git", "-C", str(root), "diff", "--name-only", base, "--"],
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
    )
    values: list[str] = []
    for argv in commands:
        try:
            result = subprocess.run(argv, text=True, capture_output=True, check=False)
        except OSError as exc:
            raise SystemExit(f"Git is required for changed-test selection but could not be executed: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "git command failed").strip()
            raise SystemExit(f"Changed-test selection could not query Git: {detail}")
        for line in result.stdout.splitlines():
            rel = line.strip().replace("\\", "/").lstrip("./")
            if not rel:
                continue
            if project_prefix:
                prefix = project_prefix.rstrip("/") + "/"
                if rel == project_prefix:
                    values.append(".")
                elif rel.startswith(prefix):
                    values.append(rel[len(prefix):])
                else:
                    continue
            else:
                values.append(rel)
    return list(dict.fromkeys(values))


def _history_path(repo_root: Path) -> Path:
    return repo_root / ".devtool" / "test-history.json"


def load_failed_test_ids(repo_root: Path) -> list[str]:
    path = _history_path(repo_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    values = payload.get("failed") if isinstance(payload, dict) else []
    return _list(values)


def save_failed_test_ids(repo_root: Path, values: Iterable[str]) -> Path:
    path = _history_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    unique = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    path.write_text(json.dumps({"failed": unique}, indent=2) + "\n", encoding="utf-8")
    return path
