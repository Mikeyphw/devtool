from __future__ import annotations

"""Python environment detection, package verification, and cleanup helpers."""

from dataclasses import dataclass
import argparse
import csv
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tarfile
from typing import Any, Sequence
import zipfile


@dataclass(frozen=True, slots=True)
class PythonProjectInfo:
    root: Path
    manager: str
    framework: str
    build_backend: str
    package_name: str
    lock_file: str


def _pyproject(root: Path) -> dict[str, Any]:
    path = root / "pyproject.toml"
    if not path.is_file():
        return {}
    try:
        import tomllib
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def inspect_python_project(root: Path, configured_manager: str = "auto") -> PythonProjectInfo:
    root = root.resolve()
    data = _pyproject(root)
    manager = configured_manager.strip().lower() or "auto"
    lock = ""
    if manager == "auto":
        for name, value in (
            ("uv", "uv.lock"),
            ("poetry", "poetry.lock"),
            ("pdm", "pdm.lock"),
            ("pipenv", "Pipfile.lock"),
        ):
            if (root / value).is_file():
                manager, lock = name, value
                break
        else:
            tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
            backend_hint = str((data.get("build-system") or {}).get("build-backend") or "") if isinstance(data.get("build-system"), dict) else ""
            if (isinstance(tool, dict) and isinstance(tool.get("poetry"), dict)) or "poetry" in backend_hint:
                manager = "poetry"
            elif (isinstance(tool, dict) and isinstance(tool.get("pdm"), dict)) or "pdm" in backend_hint:
                manager = "pdm"
            elif (isinstance(tool, dict) and isinstance(tool.get("hatch"), dict)) or "hatch" in backend_hint:
                manager = "hatch"
            elif (root / "Pipfile").is_file():
                manager = "pipenv"
            else:
                manager = "uv" if shutil.which("uv") else "venv"
    else:
        lock_map = {"uv": "uv.lock", "poetry": "poetry.lock", "pdm": "pdm.lock", "pipenv": "Pipfile.lock"}
        candidate = lock_map.get(manager, "")
        lock = candidate if candidate and (root / candidate).is_file() else ""

    build = data.get("build-system") if isinstance(data.get("build-system"), dict) else {}
    backend = str(build.get("build-backend") or "setuptools.build_meta")
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    poetry = (data.get("tool") or {}).get("poetry") if isinstance(data.get("tool"), dict) else {}
    poetry = poetry if isinstance(poetry, dict) else {}
    name = str(project.get("name") or poetry.get("name") or root.name)
    framework = "pytest" if any((root / value).exists() for value in ("pytest.ini", "conftest.py", "tests")) else "unittest"
    return PythonProjectInfo(root, manager, framework, backend, name, lock)


def package_records(dist: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not dist.is_dir():
        return records
    for path in sorted(dist.iterdir()):
        if not path.is_file() or not (path.suffix == ".whl" or path.name.endswith((".tar.gz", ".zip"))):
            continue
        record: dict[str, Any] = {"path": str(path), "type": "wheel" if path.suffix == ".whl" else "sdist", "valid": True, "checks": []}
        try:
            if path.suffix in {".whl", ".zip"}:
                with zipfile.ZipFile(path) as archive:
                    corrupt = archive.testzip()
                    if corrupt:
                        raise RuntimeError(f"corrupt member: {corrupt}")
                    names = archive.namelist()
                    if path.suffix == ".whl":
                        required = (".dist-info/METADATA", ".dist-info/WHEEL", ".dist-info/RECORD")
                        missing = [member for member in required if not any(name.endswith(member) for name in names)]
                        if missing:
                            raise RuntimeError("wheel metadata is missing: " + ", ".join(missing))
            else:
                with tarfile.open(path, "r:*") as archive:
                    names = archive.getnames()
                    if not any(name.endswith(("/PKG-INFO", "/pyproject.toml", "/setup.py")) for name in names):
                        raise RuntimeError("sdist package metadata is missing")
            record["checks"].append({"name": "archive", "status": "passed"})
        except Exception as exc:
            record["valid"] = False
            record["checks"].append({"name": "archive", "status": "failed", "detail": str(exc)})
        record["size"] = path.stat().st_size
        records.append(record)
    return records


def verify_packages(root: Path, *, import_smoke: bool = False) -> tuple[dict[str, Any], int]:
    records = package_records(root / "dist")
    if not records:
        return {"count": 0, "valid": 0, "invalid": 0, "items": [], "error": "No wheel or source distribution found"}, 1
    twine = shutil.which("twine")
    twine_result: dict[str, Any] = {"available": bool(twine), "returncode": None, "output": ""}
    if twine:
        result = subprocess.run([twine, "check", *[item["path"] for item in records]], text=True, capture_output=True, check=False)
        twine_result.update({"returncode": result.returncode, "output": (result.stdout + result.stderr).strip()})
        if result.returncode != 0:
            for item in records:
                item["valid"] = False
                item["checks"].append({"name": "twine", "status": "failed", "detail": twine_result["output"]})
        else:
            for item in records:
                item["checks"].append({"name": "twine", "status": "passed"})
    install_smoke: dict[str, Any] = {"attempted": False, "returncode": None, "output": "", "import": "skipped"}
    wheels = [item for item in records if item.get("type") == "wheel" and item.get("valid")]
    if wheels:
        install_smoke["attempted"] = True
        with tempfile.TemporaryDirectory(prefix="devtool-python-package-") as temporary:
            target = Path(temporary) / "site"
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(wheels[0]["path"])],
                text=True, capture_output=True, check=False,
            )
            install_smoke["returncode"] = result.returncode
            install_smoke["output"] = (result.stdout + result.stderr).strip()
            if result.returncode != 0:
                wheels[0]["valid"] = False
                wheels[0]["checks"].append({"name": "clean_install", "status": "failed", "detail": install_smoke["output"]})
            else:
                wheels[0]["checks"].append({"name": "clean_install", "status": "passed"})
                if import_smoke:
                    candidates = sorted(path.name for path in target.iterdir() if path.is_dir() and not path.name.endswith((".dist-info", ".data")) and path.name.isidentifier())
                    if candidates:
                        env = os.environ.copy()
                        env["PYTHONPATH"] = str(target)
                        check = subprocess.run([sys.executable, "-c", f"import {candidates[0]}"], text=True, capture_output=True, env=env, check=False)
                        install_smoke["import"] = "passed" if check.returncode == 0 else "failed"
                        if check.returncode != 0:
                            wheels[0]["valid"] = False
                            wheels[0]["checks"].append({"name": "import_smoke", "status": "failed", "detail": (check.stdout + check.stderr).strip()})
                        else:
                            wheels[0]["checks"].append({"name": "import_smoke", "status": "passed"})
                    else:
                        install_smoke["import"] = "skipped-no-import-package"

    payload = {
        "count": len(records),
        "valid": sum(1 for item in records if item["valid"]),
        "invalid": sum(1 for item in records if not item["valid"]),
        "items": records,
        "twine": twine_result,
        "clean_install": install_smoke,
    }
    return payload, 0 if payload["invalid"] == 0 else 1


def clean_python(root: Path) -> list[str]:
    removed: list[str] = []
    names = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".coverage", "build", "dist", ".tox", ".nox"}
    for path in sorted(root.rglob("*"), reverse=True):
        if path.name not in names:
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(str(path))
        except OSError:
            continue
    for path in root.glob("*.egg-info"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed.append(str(path))
    return removed


def main(raw_args: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Devtool Python support helper")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("package-check")
    verify.add_argument("root", type=Path)
    verify.add_argument("--report", type=Path, default=None)
    verify.add_argument("--import-smoke", action="store_true")
    clean = sub.add_parser("clean")
    clean.add_argument("root", type=Path)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("root", type=Path)
    args = parser.parse_args(raw_args)

    root = args.root.expanduser().resolve()
    if args.command == "package-check":
        payload, rc = verify_packages(root, import_smoke=bool(args.import_smoke))
        report = args.report or root / ".devtool" / "reports" / "python-packages.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"DEVTOOL_PYTHON_PACKAGE_RESULT {json.dumps(payload, separators=(',', ':'))}")
        return rc
    if args.command == "clean":
        removed = clean_python(root)
        print(json.dumps({"removed": removed}, indent=2))
        return 0
    info = inspect_python_project(root)
    print(json.dumps({
        "root": str(info.root),
        "manager": info.manager,
        "framework": info.framework,
        "build_backend": info.build_backend,
        "package_name": info.package_name,
        "lock_file": info.lock_file,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
