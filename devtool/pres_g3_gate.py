from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def run_gate(repo_root: Path) -> dict[str, object]:
    repo_root = repo_root.resolve()
    hud_source = (repo_root / "devtool" / "hud.py").read_text(encoding="utf-8")
    execution_source = (repo_root / "devtool" / "execution_presentation.py").read_text(encoding="utf-8")
    if "rich.live" in hud_source.lower() or "Live(" in hud_source:
        raise RuntimeError("PRES-G3 requires Rich Live to be absent from HUD presentation ownership")
    if "rich.live" in execution_source.lower():
        raise RuntimeError("PRES-G3 requires Rich Live to remain absent from normal execution presentation")
    if "build_textual_terminal_hud_app" not in hud_source:
        raise RuntimeError("PRES-G3 Textual terminal HUD factory is missing")

    env = dict(os.environ)
    env.pop("TERM", None)
    env.pop("DEVTOOL_NO_HUD", None)
    env.pop("DEVTOOL_AUTO_HUD_CHILD", None)
    env.pop("DEVTOOL_NO_AUTO_HUD", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(repo_root) + ((os.pathsep + env["PYTHONPATH"]) if env.get("PYTHONPATH") else "")
    with tempfile.TemporaryDirectory(prefix="devtool-pres-g3-") as tmp:
        proc = subprocess.run(
            [sys.executable, "-m", "devtool.hud", "--mode", "terminal", "--once", "--state-dir", tmp],
            cwd=repo_root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"static terminal HUD failed without TERM: {proc.stderr.strip()}")
    if "TERM environment variable not set" in (proc.stdout + proc.stderr):
        raise RuntimeError("terminal HUD still emits TERM-dependent clear diagnostics")
    if "Devtool" not in proc.stdout:
        raise RuntimeError("static terminal HUD did not render canonical projection")

    return {
        "schema_version": 1,
        "gate": "PRES-G3",
        "position": "70/77",
        "rich_live_runtime_refs": 0,
        "static_terminal_without_term": "pass",
        "normal_execution_textual_ownership": "retained",
        "cross_surface_truth": "canonical_projection_only",
        "next": "RMP-X01 71/77",
    }


def main(argv: list[str] | None = None) -> int:
    root = Path.cwd()
    if argv:
        if argv[:1] == ["--repo-root"] and len(argv) >= 2:
            root = Path(argv[1])
    print(json.dumps(run_gate(root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
