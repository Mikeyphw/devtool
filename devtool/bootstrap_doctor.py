"""Dependency-light, isolated import diagnosis for a broken Devtool installation.

Invoke with ``python -m devtool.bootstrap_doctor`` even when ``devtool.cli``
raises at import time.  The parent uses only Python's standard library.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Sequence


def run(module: str = "devtool.cli", *, timeout: int = 25) -> dict:
    if not module or not all(part.isidentifier() for part in module.split(".")):
        return {"ok": False, "classification": "invalid_module", "detail": "Invalid dotted Python module name", "mutation_performed": False}
    snippet = ("import importlib,sys,traceback\n"
               "try:\n"
               " importlib.import_module(sys.argv[1])\n"
               "except BaseException as e:\n"
               " traceback.print_exc()\n"
               " sys.exit(3 if isinstance(e, ModuleNotFoundError) else 4)\n")
    env = {**os.environ, "DEVTOOL_HUD": "0", "DEVTOOL_JOB_UI": "plain", "PYTHONNOUSERSITE": "1"}
    try:
        p = subprocess.run([sys.executable, "-B", "-c", snippet, module],
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "classification": "import_timeout", "detail": str(exc), "mutation_performed": False}
    kind = {0: "import_ok", 3: "dependency_missing", 4: "import_failure"}.get(p.returncode, "interpreter_failure")
    return {"schema_version": 1, "ok": p.returncode == 0, "classification": kind,
            "module": module, "python": sys.executable, "exit_code": p.returncode,
            "traceback": p.stderr, "stdout": p.stdout,
            "mutation_performed": False, "overlay_defect_confirmed": False,
            "next_step": ("" if p.returncode == 0 else
                          "Inspect the complete import traceback and environment; do not apply an overlay automatically.")}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated, read-only Devtool import doctor (no Devtool CLI import)")
    parser.add_argument("--module", default="devtool.cli")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    result = run(args.module)
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Devtool bootstrap: {result['classification']}")
        print(f"Python: {result.get('python', sys.executable)}")
        if result.get("traceback"):
            print(result["traceback"], file=sys.stderr, end="")
        if result.get("next_step"):
            print(result["next_step"])
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
