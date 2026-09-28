from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import sys
from dataclasses import replace

from .models import CommandSpec
from .execution_environments import chroot_environment_assignments
from .host_context import detect_host_context
from .privilege import HOST_CHROOT_ENTER, PrivilegeRequest, load_global_privilege_policy, resolve_privilege_plan


def build_chroot_exec_argv(command: str, chroot_name: str, *, use_sudo: bool | None = None, env: dict[str, str] | None = None) -> list[str]:
    """Build the trusted Termux chroot entry command.

    ENV-X01 removes the old implicit ``sudo chrootctl`` default.  Chroot entry
    is now a scoped ``host.chroot.enter`` privilege request.  Legacy callers
    that explicitly set ``DEVTOOL_CHROOT_SUDO`` or ``use_sudo`` retain a
    compatibility path, but repository configuration cannot grant privilege.
    """

    name = chroot_name.strip()
    if not name:
        raise ValueError("chroot_name must not be empty")

    env_map = dict(os.environ if env is None else env)
    chrootctl = str(env_map.get("DEVTOOL_CHROOTCTL", "chrootctl")).strip() or "chrootctl"

    # Make every command executed inside chroot see Linuxbrew first.
    path_prefix = str(env_map.get("DEVTOOL_CHROOT_PATH_PREFIX", "/home/linuxbrew/.linuxbrew/bin")).strip()
    if path_prefix:
        command = "export PATH=" + shlex.quote(path_prefix) + ':"$PATH"; ' + command

    base_argv = [chrootctl, "--name", name, "exec", "--", "zsh", "-lc", command]

    # Explicit compatibility overrides are trusted user/process intent.  There
    # is deliberately no default value here; absence enters the broker path.
    if use_sudo is False:
        return base_argv
    explicit = env_map.get("DEVTOOL_CHROOT_SUDO") if "DEVTOOL_CHROOT_SUDO" in env_map else None
    if use_sudo is True:
        explicit = str(explicit or "sudo")
    if explicit is not None:
        prefix = shlex.split(str(explicit).strip()) if str(explicit).strip() else []
        if prefix and Path(prefix[0]).name == "sudo" and "-n" not in prefix:
            prefix.append("-n")
        return [*prefix, *base_argv]

    interactive = str(env_map.get("DEVTOOL_PRIVILEGE_INTERACTIVE", "")).strip().lower() in {"1", "true", "yes", "on"}
    if "DEVTOOL_PRIVILEGE_INTERACTIVE" not in env_map:
        interactive = bool(getattr(sys.stdin, "isatty", lambda: False)() and getattr(sys.stdout, "isatty", lambda: False)())
    request = PrivilegeRequest(
        HOST_CHROOT_ENTER,
        scope="host",
        reason=f"enter chroot {name}",
        requested_by="chroot-execution",
        interactive=interactive,
        mutation=False,
        reversible=True,
    )
    plan = resolve_privilege_plan(
        request,
        policy=load_global_privilege_policy(),
        host=detect_host_context(env_map),
        env=env_map,
        probe=True,
        allow_action_required=interactive,
    )
    return plan.wrap_argv(base_argv)


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
