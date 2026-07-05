from __future__ import annotations

import os
import shlex
from dataclasses import replace

from .models import CommandSpec


def build_chroot_exec_argv(command: str, chroot_name: str, *, use_sudo: bool | None = None) -> list[str]:
    """Build the Termux chrootctl command used to execute a shell command.

    The runner intentionally uses the Android chroot workflow:

        sudo chrootctl --name <name> exec --zsh -lc '<command>'

    Environment overrides keep this testable and let non-root setups customize
    the launcher without changing project config:

    - DEVTOOL_CHROOTCTL: chrootctl executable, default ``chrootctl``
    - DEVTOOL_CHROOT_SUDO: sudo executable. Set to an empty string to skip sudo.
    """

    name = chroot_name.strip()
    if not name:
        raise ValueError("chroot_name must not be empty")

    chrootctl = os.environ.get("DEVTOOL_CHROOTCTL", "chrootctl").strip() or "chrootctl"
    sudo = os.environ.get("DEVTOOL_CHROOT_SUDO", "sudo")
    if use_sudo is False:
        sudo = ""
    elif use_sudo is True and not sudo.strip():
        sudo = "sudo"

    argv: list[str] = []
    if sudo.strip():
        argv.append(sudo.strip())
    argv.extend([chrootctl, "--name", name, "exec", "--zsh", "-lc", command])
    return argv


def wrap_specs_for_chroot(specs: list[CommandSpec], chroot_name: str) -> list[CommandSpec]:
    name = chroot_name.strip()
    if not name:
        return specs
    wrapped: list[CommandSpec] = []
    for spec in specs:
        metadata = dict(spec.metadata)
        metadata["execution_mode"] = "chroot"
        metadata["chroot"] = name
        metadata["chroot_runner"] = "termux-chrootctl-zsh"
        command = shlex.join(spec.argv)
        wrapped.append(replace(spec, argv=build_chroot_exec_argv(command, name), metadata=metadata))
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
