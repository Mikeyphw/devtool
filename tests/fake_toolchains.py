from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


_FAKE_GO = r'''from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path


def module_for(path: Path):
    path = path.resolve()
    for parent in (path, *path.parents):
        modfile = parent / "go.mod"
        if modfile.is_file():
            for line in modfile.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "module":
                    return parent, parts[1]
    return None


def package_for(path: Path):
    pair = module_for(path)
    if not pair:
        return None
    moddir, mod = pair
    try:
        rel = path.resolve().relative_to(moddir)
    except ValueError:
        return None
    return mod if str(rel) == "." else f"{mod}/{rel.as_posix()}"


def is_main(path: Path):
    source = path / "main.go"
    return source.is_file() and any(line.strip() == "package main" for line in source.read_text(encoding="utf-8").splitlines())


def all_modules(root: Path):
    return sorted(p.parent for p in root.rglob("go.mod"))


def resolve_target(root: Path, target: str):
    if target in {".", "./"}:
        return [root]
    if target in {"./...", "..."}:
        return sorted({p.parent for p in root.rglob("main.go")})
    if target.startswith("./") or target.startswith("/"):
        raw = target[2:] if target.startswith("./") else target
        recursive = raw.endswith("/...")
        if recursive:
            raw = raw[:-4]
        base = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        if not base.exists():
            return []
        return sorted({p.parent for p in base.rglob("main.go")}) if recursive else [base]
    for moddir in all_modules(root):
        pair = module_for(moddir)
        if not pair:
            continue
        _, mod = pair
        if target == mod:
            return [moddir]
        prefix = mod + "/"
        if target.startswith(prefix):
            return [moddir / target[len(prefix):]]
    return []


def emit(path: Path, fmt: str):
    if not path.is_dir() or not is_main(path):
        return
    pair = module_for(path)
    pkg = package_for(path)
    if not pair or not pkg:
        return
    moddir, mod = pair
    if ".Module" in fmt or fmt.strip() == "{{.Path}}":
        print(f"{moddir}\t{mod}")
    elif "\\t" in fmt or ".Dir" in fmt:
        print(f"{pkg}\t{path.resolve()}")
    else:
        print(pkg)


def main(argv):
    root = Path.cwd()
    if not argv:
        return 2
    cmd, *args = argv
    if cmd == "env":
        key = args[0] if args else ""
        print(os.environ.get(key, {"GOOS": "linux", "GOARCH": "amd64"}.get(key, "")))
        return 0
    if cmd == "list":
        if args and args[0] == "-m":
            # GoRunner uses this to enumerate workspace modules. Emit the module
            # directories in the format its shell driver consumes.
            work = root / "go.work"
            if work.is_file():
                for line in work.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("use "):
                        print(str((root / line[4:].strip()).resolve()) + "/...")
            return 0
        fmt = ""
        if len(args) >= 2 and args[0] == "-f":
            fmt, args = args[1], args[2:]
        target = args[0] if args else "./..."
        for path in resolve_target(root, target):
            emit(path, fmt)
        return 0
    if cmd == "build":
        out = None
        package = ""
        i = 0
        while i < len(args):
            if args[i] == "-o" and i + 1 < len(args):
                out = Path(args[i + 1]); i += 2; continue
            if args[i] == "-p" and i + 1 < len(args):
                i += 2; continue
            if args[i].startswith("-"):
                i += 1; continue
            package = args[i]; i += 1
        if out is None:
            return 0
        out.parent.mkdir(parents=True, exist_ok=True)
        module = "example.com/fake"
        if package in {"", ".", "./"}:
            pair = module_for(root)
            if pair:
                module = pair[1]
        else:
            for moddir in all_modules(root):
                pair = module_for(moddir)
                if pair and (package == pair[1] or package.startswith(pair[1] + "/")):
                    module = pair[1]; break
        meta = {
            "module": module,
            "goos": os.environ.get("GOOS", "linux"),
            "goarch": os.environ.get("GOARCH", "amd64"),
        }
        git_dir = root / ".git"
        if git_dir.exists():
            try:
                revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
                modified = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True, stderr=subprocess.DEVNULL).strip())
                meta.update({"vcs": "git", "vcs_revision": revision, "vcs_modified": str(modified).lower()})
            except Exception:
                pass
        marker = "# DEVTOOL_FAKE_GO=" + json.dumps(meta, separators=(",", ":")) + "\n"
        if meta["goos"] == "windows":
            # Devtool's structural PE probe only needs the DOS MZ signature;
            # build metadata comes from this shim's `go version -m` response.
            out.write_bytes(("MZ\n" + marker).encode("utf-8"))
        else:
            out.write_text("#!/bin/sh\n" + marker + "exit 0\n", encoding="utf-8")
            out.chmod(0o755)
        return 0
    if cmd == "version":
        if args and args[0] == "-m" and len(args) >= 2:
            path = Path(args[1])
            meta = {"module": "example.com/fake", "goos": "linux", "goarch": "amd64"}
            if path.is_file():
                for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.startswith("# DEVTOOL_FAKE_GO="):
                        meta.update(json.loads(line.split("=", 1)[1]))
                        break
            print(f"{path}: go1.23.0")
            print(f"\tmod\t{meta['module']}\t(devel)")
            print(f"\tbuild\tGOOS={meta['goos']}")
            print(f"\tbuild\tGOARCH={meta['goarch']}")
            if meta.get("vcs"):
                print(f"\tbuild\tvcs={meta['vcs']}")
            if meta.get("vcs_revision"):
                print(f"\tbuild\tvcs.revision={meta['vcs_revision']}")
            if meta.get("vcs_modified"):
                print(f"\tbuild\tvcs.modified={meta['vcs_modified']}")
            return 0
        print("go version go1.23.0 fake/fake")
        return 0
    if cmd == "mod":
        if args[:1] == ["init"] and len(args) >= 2:
            (root / "go.mod").write_text(f"module {args[1]}\n\ngo 1.23\n", encoding="utf-8")
        return 0
    if cmd == "work":
        if args[:1] == ["init"]:
            (root / "go.work").write_text("".join(f"use {item}\n" for item in args[1:]), encoding="utf-8")
        return 0
    if cmd == "test":
        # Model the failing package used by Devtool's Go JSON integration test.
        pair = module_for(root)
        package = pair[1] if pair else "example.com/fake"
        test_files = list(root.glob("*_test.go"))
        if "-json" in args and test_files:
            events = [
                {"Action": "start", "Package": package},
                {"Action": "run", "Package": package, "Test": "TestContract"},
                {"Action": "output", "Package": package, "Test": "TestContract", "Output": "=== RUN   TestContract\n"},
                {"Action": "output", "Package": package, "Test": "TestContract", "Output": "    contracts_test.go:3: schema drift: expected 1, got 2\n"},
                {"Action": "output", "Package": package, "Test": "TestContract", "Output": "--- FAIL: TestContract (0.00s)\n"},
                {"Action": "fail", "Package": package, "Test": "TestContract", "Elapsed": 0.0},
                {"Action": "output", "Package": package, "Output": "FAIL\n"},
                {"Action": "fail", "Package": package, "Elapsed": 0.0},
            ]
            for event in events:
                print(json.dumps(event, separators=(",", ":")))
            return 1
        return 0
    if cmd in {"vet", "run", "clean"}:
        return 0
    print(f"fake go: unsupported command: {cmd}", file=sys.stderr)
    return 2

raise SystemExit(main(sys.argv[1:]))
'''


def install_fake_go(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install a fast, isolated Go shim for GoRunner contract tests.

    Most GoRunner tests validate Devtool's discovery/driver logic rather than the
    Go compiler. Spawning the real toolchain in every case is especially costly
    on Termux, where pytest intentionally gives each test an isolated HOME. This
    shim models only the commands emitted by GoRunner. A small, explicit set of
    ``slow_external`` tests still exercises the real Go toolchain.
    """

    bin_dir = root / "fake-go-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    driver = bin_dir / "fake_go.py"
    driver.write_text(_FAKE_GO, encoding="utf-8")
    go = bin_dir / "go"
    # `python -S` avoids importing site packages for every fake Go subprocess.
    # That cuts startup from seconds to milliseconds on instrumentation-heavy
    # Python environments and on Termux, while the shell wrapper avoids relying
    # on multi-argument shebang behavior.
    go.write_text(
        "#!/bin/sh\n"
        f"exec {__import__('shlex').quote(sys.executable)} -S {__import__('shlex').quote(str(driver))} \"$@\"\n",
        encoding="utf-8",
    )
    go.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    return go
