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
    failed: bool = False
    changed: bool = False
    changed_from: str = "HEAD"
    instrumented: bool = False

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
            self.failed,
            self.changed,
            self.instrumented,
        ))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def resolve_test_selection(config: ProjectConfig) -> TestSelection:
    raw = config.runner_options.get("test_selection", {})
    raw = dict(raw) if isinstance(raw, dict) else {}
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
        task=str(raw.get("task") or "").strip(),
        failed=bool(raw.get("failed", False)),
        changed=bool(raw.get("changed", False)),
        changed_from=str(raw.get("changed_from") or "HEAD").strip() or "HEAD",
        instrumented=bool(raw.get("instrumented", False)),
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
        selection.task = selection.task or str(profile.get("task") or "").strip()
        selection.failed = selection.failed or bool(profile.get("failed", False))
        selection.changed = selection.changed or str(profile.get("selection") or "").strip().lower() == "changed"
        selection.changed_from = str(profile.get("base") or selection.changed_from).strip() or "HEAD"
        selection.instrumented = selection.instrumented or bool(profile.get("instrumented", False))

    if selection.failed:
        failed_ids = load_failed_test_ids(config.repo_root)
        if not failed_ids:
            raise SystemExit("No failed tests are recorded for this repository.")
        selection.ids.extend(failed_ids)
    if selection.changed:
        changed = changed_paths(config.repo_root, selection.changed_from)
        if not changed:
            raise SystemExit(f"No changed files were found relative to {selection.changed_from}.")
        selection.paths.extend(changed)

    # Stable de-duplication keeps generated commands deterministic.
    for name in ("selectors", "ids", "tags", "exclude_tags", "paths", "classes", "methods"):
        values = getattr(selection, name)
        setattr(selection, name, list(dict.fromkeys(values)))
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


_JAVA_TEST = re.compile(r"@Test(?:\([^)]*\))?\s*(?:public\s+|protected\s+|private\s+|internal\s+|suspend\s+)*fun\s+(\w+)|@Test(?:\([^)]*\))?\s*(?:public\s+|protected\s+|private\s+)?(?:void|[\w<>?]+)\s+(\w+)\s*\(", re.S)
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
        package = (_JAVA_PACKAGE.search(text).group(1) if _JAVA_PACKAGE.search(text) else "")
        class_name = (_JAVA_CLASS.search(text).group(1) if _JAVA_CLASS.search(text) else path.stem)
        suite = ".".join(item for item in (package, class_name) if item)
        tags = tuple(dict.fromkeys(next(item for item in match.groups() if item) for match in _JAVA_TAG.finditer(text)))
        instrumentation = "/androidTest/" in path.as_posix()
        framework = "android-instrumentation" if instrumentation else "gradle-junit"
        rel = _relative(root, path)
        for match in _JAVA_TEST.finditer(text):
            name = next(item for item in match.groups() if item)
            line = text.count("\n", 0, match.start()) + 1
            tests.append(TestDescriptor(
                id=f"{suite}.{name}" if suite else name,
                name=name,
                suite=suite,
                project=_gradle_module(path, root),
                framework=framework,
                test_type="instrumentation" if instrumentation else _python_test_type(tags, path),
                tags=tags,
                file=rel,
                line=line,
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
        wanted = set(selection.ids)
        items = [item for item in items if item.id in wanted or any(token in item.id for token in wanted)]
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
        positive.extend(f"FullyQualifiedName~{value}" for value in selection.ids)
        positive.extend(f"FullyQualifiedName~{value}" for value in selection.classes)
        positive.extend(f"FullyQualifiedName~{value}" for value in selection.methods)
        for value in selection.selectors:
            if value.lower() in {"unit", "integration", "smoke", "ui", "instrumentation"}:
                positive.append(f"Category={value}")
            else:
                positive.append(f"FullyQualifiedName~{value}")
        positive.extend(f"Category={value}" for value in selection.tags)
        negative.extend(f"Category!={value}" for value in selection.exclude_tags)
        positive.extend(f"FullyQualifiedName~{Path(value).stem}" for value in selection.paths if not value.endswith((".csproj", ".sln")))
        native = selection.native_filter
        if not native:
            positive_group = "|".join(positive)
            parts = ([f"({positive_group})"] if positive_group else []) + negative
            native = "&".join(parts)
        if native:
            argv.extend(["--filter", native])
    elif config.kind in {"android", "gradle"} and not selection.instrumented:
        patterns: list[str] = []
        patterns.extend(selection.ids)
        patterns.extend(selection.classes)
        patterns.extend(selection.methods)
        patterns.extend(value for value in selection.selectors if value.lower() not in {"unit", "integration", "smoke", "ui"})
        if selected_catalog and not patterns and (selection.paths or selection.tags or selection.exclude_tags):
            patterns.extend(item.id for item in selected_catalog)
        for value in dict.fromkeys(patterns):
            _append_gradle_arg(argv, "--tests", value)
        if selection.native_filter:
            _append_gradle_arg(argv, *selection.native_filter.split())
    elif config.kind == "python":
        # Pytest node IDs and paths can be passed directly.  Keep them before
        # expression/marker flags so generated commands remain readable.
        direct = [*selection.ids, *selection.paths]
        if selected_catalog and not direct and (selection.classes or selection.methods):
            direct.extend(item.id for item in selected_catalog)
        argv.extend(dict.fromkeys(direct))
        expressions = [*selection.selectors, *selection.classes, *selection.methods]
        if selection.native_filter:
            expressions.append(selection.native_filter)
        if expressions:
            argv.extend(["-k", " and ".join(f"({value})" for value in expressions)])
        marker_parts = [*selection.tags, *(f"not {value}" for value in selection.exclude_tags)]
        if marker_parts:
            argv.extend(["-m", " and ".join(marker_parts)])

    return CommandSpec(
        spec.name,
        argv,
        env=spec.env,
        cwd=spec.cwd,
        destructive=spec.destructive,
        metadata=metadata,
    )


def changed_paths(repo_root: Path, base: str) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--name-only", base, "--"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


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
