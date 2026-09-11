from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import discover_dotnet_candidates, discover_gradle_roots, detect_project_kind, detect_project_kind_details, find_config

IGNORED_DIRS = {".git", ".devtool", "bin", "obj", "node_modules", "venv", ".venv", "dist", "build", "__pycache__"}


def _collect(repo_root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    matches: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        base = Path(dirpath)
        for filename in filenames:
            if filename.lower().endswith(suffixes):
                matches.append(base / filename)
    return sorted(matches)


def _rel(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except Exception:
        return str(path)


def _is_devtool_dirty_path(path_text: str) -> bool:
    """Return True when a git status path is inside the repo-local .devtool dir."""
    path_text = path_text.strip().strip('"')
    return path_text == ".devtool" or path_text.startswith(".devtool/")


def _status_line_ignored(line: str) -> bool:
    if not line.strip():
        return True
    # Porcelain v1 is usually ``XY path``. Rename/copy lines are
    # ``XY old -> new``; ignore only when every touched path is under .devtool.
    paths_text = line[3:] if len(line) > 3 else line.strip()
    paths = [part.strip() for part in paths_text.split(" -> ")]
    return bool(paths) and all(_is_devtool_dirty_path(path) for path in paths)


def _git_dirty(repo_root: Path) -> bool | None:
    if not (repo_root / ".git").exists():
        return None
    proc = subprocess.run(["git", "status", "--porcelain"], cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    if proc.returncode != 0:
        return None
    return any(not _status_line_ignored(line) for line in proc.stdout.splitlines())


def detect_repo(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.expanduser().resolve()
    slns = _collect(repo_root, (".sln",))
    csprojs = _collect(repo_root, (".csproj",))
    pyprojects = [repo_root / "pyproject.toml"] if (repo_root / "pyproject.toml").exists() else []
    node_manifest = repo_root / "package.json"
    rust_manifest = repo_root / "Cargo.toml"
    go_manifest = repo_root / ("go.work" if (repo_root / "go.work").exists() else "go.mod")
    maven_manifest = repo_root / "pom.xml"
    cmake_manifest = repo_root / "CMakeLists.txt"
    meson_manifest = repo_root / "meson.build"
    make_manifest = next((repo_root / name for name in ("Makefile", "makefile", "GNUmakefile") if (repo_root / name).exists()), repo_root / "Makefile")
    gradle_roots = discover_gradle_roots(repo_root, android_only=False)
    android_roots = discover_gradle_roots(repo_root, android_only=True)
    package_scripts = [p for p in [repo_root / "scripts" / "package-release.sh", repo_root / "build.sh", repo_root / "package.sh"] if p.exists()]
    docs = [
        p for p in [
            repo_root / "AI_AGENT_DEVELOPER_PROMPT.md",
            repo_root / "README.md",
            repo_root / "docs" / "final-parity-report.md",
            repo_root / "docs" / "migration-map.md",
        ]
        if p.exists()
    ]
    test_projects = [p for p in csprojs if "test" in p.name.lower() or "tests" in {part.lower() for part in p.parts}]
    runnable_projects = [p for p in csprojs if p not in test_projects]
    config_path = find_config(repo_root, None)
    kind_details = detect_project_kind_details(repo_root)

    return {
        "repo_root": str(repo_root),
        "repo_name": repo_root.name,
        "runner": kind_details["kind"],
        "runner_candidates": list(kind_details["candidates"]),
        "root_runner_candidates": list(kind_details["root_candidates"]),
        "runner_reason": kind_details["reason"],
        # Compatibility aliases retained for 1.0/early-1.1 consumers.
        "kind": kind_details["kind"],
        "kind_candidates": list(kind_details["candidates"]),
        "root_kind_candidates": list(kind_details["root_candidates"]),
        "kind_reason": kind_details["reason"],
        "git": (repo_root / ".git").exists(),
        "git_dirty": _git_dirty(repo_root),
        "config": _rel(repo_root, config_path) if config_path else "",
        "dotnet": {
            "solutions": [_rel(repo_root, p) for p in slns],
            "solution_count": len(slns),
            "projects": [_rel(repo_root, p) for p in csprojs],
            "project_count": len(csprojs),
            "test_projects": [_rel(repo_root, p) for p in test_projects],
            "runnable_projects": [_rel(repo_root, p) for p in runnable_projects],
        },
        "android": {
            "roots": [_rel(repo_root, p) for p in android_roots],
            "root_count": len(android_roots),
        },
        "gradle": {
            "roots": [_rel(repo_root, p) for p in gradle_roots],
            "root_count": len(gradle_roots),
            "wrapper": any((p / "gradlew").is_file() for p in gradle_roots),
        },
        "python": {
            "pyproject": [_rel(repo_root, p) for p in pyprojects],
            "pytest": (repo_root / "tests").exists() or (repo_root / "pytest.ini").exists(),
        },
        "node": {"manifest": _rel(repo_root, node_manifest) if node_manifest.exists() else ""},
        "rust": {"manifest": _rel(repo_root, rust_manifest) if rust_manifest.exists() else ""},
        "go": {"manifest": _rel(repo_root, go_manifest) if go_manifest.exists() else ""},
        "maven": {"manifest": _rel(repo_root, maven_manifest) if maven_manifest.exists() else ""},
        "cmake": {"manifest": _rel(repo_root, cmake_manifest) if cmake_manifest.exists() else ""},
        "meson": {"manifest": _rel(repo_root, meson_manifest) if meson_manifest.exists() else ""},
        "make": {"manifest": _rel(repo_root, make_manifest) if make_manifest.exists() else ""},
        "packaging": [_rel(repo_root, p) for p in package_scripts],
        "docs": [_rel(repo_root, p) for p in docs],
        "tools": {
            "dotnet": shutil.which("dotnet") or "",
            "python": shutil.which("python3") or shutil.which("python") or "",
            "java": shutil.which("java") or "",
            "gradle": shutil.which("gradle") or "",
            "adb": shutil.which("adb") or "",
            "git": shutil.which("git") or "",
            "wine": shutil.which("wine") or "",
            "node": shutil.which("node") or "",
            "npm": shutil.which("npm") or "",
            "pnpm": shutil.which("pnpm") or "",
            "yarn": shutil.which("yarn") or "",
            "bun": shutil.which("bun") or "",
            "cargo": shutil.which("cargo") or "",
            "go": shutil.which("go") or "",
            "mvn": shutil.which("mvn") or "",
            "cmake": shutil.which("cmake") or "",
            "meson": shutil.which("meson") or "",
            "make": shutil.which("make") or "",
        },
    }


def render_detect_text(info: dict[str, Any]) -> str:
    dotnet = info.get("dotnet", {})
    python = info.get("python", {})
    android = info.get("android", {})
    gradle = info.get("gradle", {})
    lines = [
        f"repo: {info.get('repo_root', '-')}",
        f"runner: {info.get('runner', info.get('kind', '-'))}",
        f"runner_reason: {info.get('runner_reason', info.get('kind_reason', '-'))}",
        f"runner_candidates: {', '.join(info.get('runner_candidates', info.get('kind_candidates', []))) or '-'}",
        f"root_runner_candidates: {', '.join(info.get('root_runner_candidates', info.get('root_kind_candidates', []))) or '-'}",
        f"git: {'yes' if info.get('git') else 'no'} dirty={info.get('git_dirty')}",
        f"config: {info.get('config') or '-'}",
        f"solutions: {dotnet.get('solution_count', 0)}",
        f"projects: {dotnet.get('project_count', 0)}",
        f"test_projects: {len(dotnet.get('test_projects', []))}",
        f"android_roots: {android.get('root_count', 0)}",
        f"gradle_roots: {gradle.get('root_count', 0)}",
        f"python_pyproject: {'yes' if python.get('pyproject') else 'no'}",
        f"node_manifest: {info.get('node', {}).get('manifest') or '-'}",
        f"rust_manifest: {info.get('rust', {}).get('manifest') or '-'}",
        f"go_manifest: {info.get('go', {}).get('manifest') or '-'}",
        f"maven_manifest: {info.get('maven', {}).get('manifest') or '-'}",
        f"cmake_manifest: {info.get('cmake', {}).get('manifest') or '-'}",
        f"meson_manifest: {info.get('meson', {}).get('manifest') or '-'}",
        f"make_manifest: {info.get('make', {}).get('manifest') or '-'}",
        f"package_scripts: {len(info.get('packaging', []))}",
        f"docs: {', '.join(info.get('docs', [])) or '-'}",
    ]
    for key in ("solutions", "runnable_projects", "test_projects"):
        values = dotnet.get(key, [])
        if values:
            lines.append(f"{key}:")
            lines.extend(f"  - {value}" for value in values[:30])
            if len(values) > 30:
                lines.append(f"  ... {len(values) - 30} more")
    return "\n".join(lines)
