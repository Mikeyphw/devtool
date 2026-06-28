from __future__ import annotations

import os
from dataclasses import replace

from .models import CommandSpec


def wrap_specs_for_chroot(specs: list[CommandSpec], chroot_name: str) -> list[CommandSpec]:
    name = chroot_name.strip()
    if not name:
        return specs
    chrootctl = os.environ.get("DEVTOOL_CHROOTCTL", "chrootctl")
    wrapped: list[CommandSpec] = []
    for spec in specs:
        metadata = dict(spec.metadata)
        metadata["execution_mode"] = "chroot"
        metadata["chroot"] = name
        wrapped.append(replace(spec, argv=[chrootctl, "exec", name, "--", *spec.argv], metadata=metadata))
    return wrapped


def apply_matrix(specs: list[CommandSpec], matrix_items: list[str], *, mode: str = "rid") -> list[CommandSpec]:
    if not matrix_items:
        return specs
    expanded: list[CommandSpec] = []
    for item in matrix_items:
        item = item.strip()
        if not item:
            continue
        for spec in specs:
            argv = list(spec.argv)
            metadata = dict(spec.metadata)
            metadata["matrix"] = item
            metadata["matrix_mode"] = mode
            if mode == "rid" and argv and argv[0] == "dotnet" and spec.name in {"restore", "build", "test", "wine-test", "run"}:
                if "-r" not in argv and "--runtime" not in argv:
                    argv.extend(["-r", item])
            env = dict(spec.env)
            env["DEVTOOL_MATRIX_ITEM"] = item
            expanded.append(replace(spec, name=f"{spec.name}[{item}]", argv=argv, env=env, metadata=metadata))
    return expanded
