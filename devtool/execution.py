from __future__ import annotations

import os
import re
import shlex
from dataclasses import replace

from .models import CommandSpec
from .execution_environments import chroot_environment_assignments


def build_chroot_exec_argv(command: str, chroot_name: str, *, use_sudo: bool | None = None) -> list[str]:
    """Build the Termux chrootctl command used to execute a shell command.

    The runner intentionally uses the Android chroot workflow:

        sudo chrootctl --name <name> exec -- zsh -lc '<command>'

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

    # Make every command executed inside chroot see Linuxbrew first.
    #
    # This is injected into the zsh -lc payload, not the host devtool process,
    # so commands such as npm/python/node resolved by chrootctl use Linuxbrew
    # before the chroot's default PATH.
    #
    # Override with:
    #   DEVTOOL_CHROOT_PATH_PREFIX=/custom/bin:/other/bin
    #
    # Disable with:
    #   DEVTOOL_CHROOT_PATH_PREFIX=
    path_prefix = os.environ.get("DEVTOOL_CHROOT_PATH_PREFIX", "/home/linuxbrew/.linuxbrew/bin")
    path_prefix = path_prefix.strip()
    if path_prefix:
        # Quote only the static prefix. Keep "$PATH" expandable inside chroot.
        command = "export PATH=" + shlex.quote(path_prefix) + ':"$PATH"; ' + command

    argv: list[str] = []
    if sudo.strip():
        argv.append(sudo.strip())
    argv.extend([chrootctl, "--name", name, "exec", "--", "zsh", "-lc", command])
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
        # Keep the unwrapped command available to the dotnet progress parser.
        # The executed argv becomes ``chrootctl ... zsh -lc ...`` and is not
        # directly recognizable as a dotnet restore/build/test command.
        metadata.setdefault("progress_argv", list(spec.argv))
        if spec.cwd:
            metadata.setdefault("progress_cwd", spec.cwd)
        command = shlex.join(spec.argv)
        assignments = list(chroot_environment_assignments(name))
        for key, value in sorted(spec.env.items()):
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(key)):
                assignments.append(f"{key}={value}")
        guarded = "exec env " + " ".join(shlex.quote(item) for item in assignments) + " " + command
        wrapped.append(replace(spec, argv=build_chroot_exec_argv(guarded, name), metadata=metadata))
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
            # Workflow DAG identity must be isolated per matrix lane. Without
            # this, two matrix copies of the same workflow node share status
            # and validation phase keys, so one lane can incorrectly satisfy,
            # block, or overwrite another lane.
            if metadata.get("workflow_graph"):
                lane = f"matrix:{mode}:{item}"
                node_key = str(metadata.get("workflow_node_key") or "").strip()
                if node_key:
                    metadata["workflow_matrix_base_node_key"] = node_key
                    metadata["workflow_node_key"] = f"{node_key}[{lane}]"
                deps = [str(value) for value in (metadata.get("workflow_depends_on") or [])]
                if deps:
                    metadata["workflow_matrix_base_depends_on"] = list(deps)
                    metadata["workflow_depends_on"] = [f"{value}[{lane}]" for value in deps]
                phase_key = str(metadata.get("validation_phase_key") or "").strip()
                if phase_key:
                    metadata["workflow_matrix_base_validation_phase_key"] = phase_key
                    metadata["validation_phase_key"] = f"{phase_key}[{lane}]"
                phase_deps = [str(value) for value in (metadata.get("validation_depends_on") or [])]
                if phase_deps:
                    metadata["workflow_matrix_base_validation_depends_on"] = list(phase_deps)
                    metadata["validation_depends_on"] = [f"{value}[{lane}]" for value in phase_deps]
            if mode == "rid" and argv and argv[0] == "dotnet" and spec.name in {"restore", "build", "test", "wine-test", "run"}:
                if "-r" not in argv and "--runtime" not in argv:
                    argv.extend(["-r", item])
            env = dict(spec.env)
            env["DEVTOOL_MATRIX_ITEM"] = item
            expanded.append(replace(spec, name=f"{spec.name}[{item}]", argv=argv, env=env, metadata=metadata))
    return expanded
