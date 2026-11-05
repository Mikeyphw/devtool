"""Copy Git-tracked fixture assets into a separate, committed test-consumer repo.

The fixture is *not* an independently operated GitHub consumer and its
results must never be represented as independent-consumer qualification.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess


def prepare(dest: Path, *, template: Path | None = None) -> str:
    template = template or Path(__file__).resolve().parent / "template"
    if dest.exists() or dest.is_symlink():
        raise ValueError("RM21 fixture destination must not exist")
    if not template.is_dir():
        raise ValueError("RM21 fixture template is missing")
    shutil.copytree(template, dest, symlinks=False)
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_DATE": "2001-01-01T00:00:00+0000",
                "GIT_COMMITTER_DATE": "2001-01-01T00:00:00+0000"})
    for args in (("init", "-q", "--initial-branch=main"),
                 ("add", "-A"),
                 ("-c", "user.name=RM21 Fixture", "-c", "user.email=rm21@example.invalid",
                  "commit", "-qm", "immutable hosted fixture")):
        subprocess.run(["git", "-C", str(dest), *args], check=True, timeout=30, env=env)
    sha = subprocess.check_output(["git", "-C", str(dest), "rev-parse", "HEAD"],
                                  text=True, timeout=10).strip()
    if len(sha) != 40:
        raise ValueError("RM21 fixture commit identity missing")
    return sha


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dest", type=Path, required=True)
    args = p.parse_args()
    print(prepare(args.dest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
