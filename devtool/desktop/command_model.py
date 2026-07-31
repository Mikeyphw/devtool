from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping


def split_extra_arguments(value: str | Iterable[str] | None) -> list[str]:
    """Return shell-like tokens for an advanced argument field.

    Control Center text fields accept exactly the kind of small snippets users
    already paste into terminals.  Parsing is intentionally done once in the
    shared desktop model so GTK4 pages do not grow divergent command
    semantics.
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        return shlex.split(text)
    return [str(part) for part in value if str(part)]


def parse_environment_assignments(value: str | Mapping[str, str] | None) -> dict[str, str]:
    """Parse NAME=value assignments from a command-review field.

    Newlines and shell quoting are supported.  Invalid tokens are ignored by
    default because the UI review page should remain forgiving; execution still
    shows the exact generated command before launch.
    """
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(key): str(item) for key, item in value.items() if str(key)}
    assignments: dict[str, str] = {}
    for token in split_extra_arguments(value.replace("\n", " ")):
        if "=" not in token:
            continue
        key, item = token.split("=", 1)
        key = key.strip()
        if not key or key.startswith("-"):
            continue
        assignments[key] = item
    return assignments


@dataclass(slots=True)
class DevtoolCommandDraft:
    """Typed command model shared by desktop front-ends.

    The model emits argv first.  Shell text is a review/copy artifact only.
    """

    repository: Path | str
    operation: str
    devtool: str = "devtool"
    target: str = ""
    project_type: str = ""
    execution_environment: str = ""
    artifact: str = ""
    test_profile: str = ""
    selectors: list[str] = field(default_factory=list)
    test_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    exclude_tags: list[str] = field(default_factory=list)
    test_path: str = ""
    test_class: str = ""
    test_method: str = ""
    test_filter: str = ""
    test_tasks: list[str] = field(default_factory=list)
    list_tests: bool = False
    failed: bool = False
    changed: bool = False
    changed_from: str = "HEAD"
    instrumented: bool = False
    validate: bool = False
    commit: bool = False
    commit_message: str = ""
    keep_artifact: bool = False
    no_manifest_check: bool = False
    copy_mode: str = "summary"
    auto_hud: bool = True
    hud_mode: str = "control-center"
    yes: bool = False
    environment: dict[str, str] = field(default_factory=dict)
    extra_global_args: list[str] = field(default_factory=list)
    extra_operation_args: list[str] = field(default_factory=list)

    def normalized_operation(self) -> str:
        return self.operation.strip().lower().replace("_", "-")

    def operation_argv(self) -> list[str]:
        op = self.normalized_operation()
        if op in {"apply overlay", "apply-overlay", "ao"}:
            argv = ["apply-overlay", self.artifact]
            if self.validate:
                argv.append("--validate")
            if self.commit and self.commit_message.strip():
                argv.extend(["--commit", self.commit_message.strip()])
            if self.keep_artifact:
                argv.append("--keep-artifact")
            if self.no_manifest_check:
                argv.append("--no-manifest-check")
            return argv
        if op in {"apply bundle", "apply-overlays", "aos"}:
            argv = ["apply-overlays", self.artifact]
            if self.validate:
                argv.append("--validate")
            if self.commit and self.commit_message.strip():
                argv.extend(["--commit", self.commit_message.strip()])
            if self.keep_artifact:
                argv.append("--keep-artifact")
            if self.no_manifest_check:
                argv.append("--no-manifest-check")
            return argv
        mapping = {
            "validate": "validate",
            "restore": "restore",
            "build": "build",
            "test": "test",
            "lint": "lint",
            "run project": "run",
            "run": "run",
            "package": "package",
            "clean": "clean",
            "inspect": "inspect",
            "status": "status",
            "doctor": "doctor",
        }
        if op == "commit":
            if not self.commit_message.strip():
                return []
            return ["commit", self.commit_message.strip()]
        command = mapping.get(op)
        if not command:
            return []
        argv = [command]
        is_test = command == "test"
        is_validation = is_test or command == "validate" or op.startswith("apply")
        if is_validation and self.test_profile.strip():
            argv.extend(["--profile" if is_test else "--test-profile", self.test_profile.strip()])
        if is_validation:
            if is_test and self.list_tests:
                argv.append("--list-tests")
            for value in self.selectors:
                if value:
                    argv.extend(["--select", value])
            for value in self.test_ids:
                if value:
                    argv.extend(["--id", value])
            for value in self.tags:
                if value:
                    argv.extend(["--tag", value])
            for value in self.exclude_tags:
                if value:
                    argv.extend(["--exclude-tag", value])
            if self.test_path.strip():
                argv.extend(["--path", self.test_path.strip()])
            if self.test_class.strip():
                argv.extend(["--class", self.test_class.strip()])
            if self.test_method.strip():
                argv.extend(["--method", self.test_method.strip()])
            if self.test_filter.strip():
                argv.extend(["--filter", self.test_filter.strip()])
            for value in self.test_tasks:
                if value:
                    argv.extend(["--task", value])
            if self.failed:
                argv.append("--failed")
            if self.changed:
                argv.append("--changed")
                if self.changed_from.strip():
                    argv.extend(["--changed-from", self.changed_from.strip()])
            if self.instrumented:
                argv.append("--instrumented")
        return argv

    def argv(self) -> list[str]:
        repo = str(Path(self.repository).expanduser())
        if not repo:
            return []
        argv = [self.devtool]
        project_type = self.project_type.strip().lower()
        if project_type and project_type != "auto":
            argv.extend(["--project-type", project_type])
        environment = self.execution_environment.strip().lower()
        if environment and environment != "auto":
            argv.extend(["--execution-environment", environment])
        copy_mode = self.copy_mode.strip().lower()
        if copy_mode == "json":
            argv.append("--copy-json")
        elif copy_mode == "none":
            argv.append("--no-clipboard")
        else:
            argv.append("--copy")
        hud_mode = self.hud_mode.strip() or "control-center"
        if self.auto_hud and hud_mode.lower() != "none":
            argv.extend(["--auto-hud", "--hud-mode", hud_mode])
        else:
            argv.append("--no-hud")
        if self.yes:
            argv.append("--yes")
        argv.extend(["-r", repo])
        if self.target.strip():
            argv.extend(["--target", self.target.strip()])
        argv.extend(self.extra_global_args)
        operation = self.operation_argv()
        if not operation:
            return []
        argv.extend(operation)
        argv.extend(self.extra_operation_args)
        return argv

    def shell_command(self) -> str:
        argv = self.argv()
        if not argv:
            return ""
        prefix = []
        for key, value in sorted(self.environment.items()):
            prefix.append(f"export {shlex.quote(key)}={shlex.quote(value)}")
        prefix.append("export DEVTOOL_CONTROL_CENTER_ACTIVE=1")
        prefix.append(f"cd {shlex.quote(str(Path(self.repository).expanduser()))}")
        return "; ".join(prefix) + " && " + " ".join(shlex.quote(part) for part in argv)
