from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rich.panel import Panel
from rich.prompt import Prompt
from rich.syntax import Syntax
from rich.table import Table

from .console_utils import create_console, is_narrow_console

console = create_console()
stderr_console = create_console(stderr=True)

__all__ = [
    "ParsedBlock",
    "RemoteCapabilities",
    "RemoteDevtoolCapabilities",
    "TermuxRunError",
    "add_termux_run_parser",
    "block_contains_commit",
    "build_ssh_options",
    "confirm_clipboard_block",
    "handle_termux_run",
    "parse_clipboard_block",
    "rewrite_block_for_remote",
    "scp_options_from_ssh",
]

TERMUX_SHARED_DOWNLOADS = Path("/sdcard/Download")
TERMUX_SHARED_DOWNLOADS_ALT = Path("/storage/emulated/0/Download")
TERMUX_APP_DOWNLOADS = Path.home() / "Downloads"

ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar.gz",
    ".tgz",
    ".tar.xz",
    ".txz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xg",
)

COPY_FLAG_PATTERN = re.compile(r"(?<!\S)--copy(?:-(?:json|raw|summary))?(?!\S)")
STDOUT_PAYLOAD_PATTERN = re.compile(
    r"(?<!\S)--stdout-payload(?:=|\s+)(?:auto|summary|json|raw|runner-json)(?!\S)"
)
JSON_FLAG_PATTERN = re.compile(r"(?<!\S)(-j|--json)(?!\S)")
DEVTOOL_LINE_PATTERN = re.compile(r"(^|\n)(?P<indent>[ \t]*)(?P<line>devtool[^\n]*)")
ASSIGNMENT_PATTERN = re.compile(
    r"""(?mx)
    ^
    (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    =
    (?P<quote>["']?)
    (?P<value>.*?)
    (?P=quote)
    $
    """
)
TOKEN_PATTERN = re.compile(r"""(?P<token>[/~$A-Za-z0-9_.\-{}]+(?:/[~$A-Za-z0-9_.\-{}]+)*)""")

DEVTOOL_COMMANDS = {
    "restore", "rs", "build", "b", "test", "t", "validate", "v",
    "wine-validate", "wv", "run", "rn", "package", "pkg", "status", "st",
    "doctor", "dr", "ai-prompt", "ap", "commit", "ci", "apply-overlay", "ao",
    "apply-snapshot", "as", "apply-overlay-validate", "aov",
    "apply-snapshot-validate", "asv", "batch", "init-config",
    "logs", "tag", "release-notes", "completion", "config", "termux-run",
}
APPLY_COMMANDS = {
    "apply-overlay", "ao", "apply-snapshot", "as",
    "apply-overlay-validate", "aov", "apply-snapshot-validate", "asv",
}


class TermuxRunError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParsedBlock:
    original_text: str
    remote_artifact_expr: str | None
    remote_artifact_path: str | None
    archive_basename: str | None
    requires_archive_upload: bool


@dataclass(frozen=True)
class RemoteDevtoolCapabilities:
    supports_stdout_payload: bool
    supports_runner_json: bool


# Backward-compatible public alias used by older tests and integrations.
RemoteCapabilities = RemoteDevtoolCapabilities


def add_termux_run_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "termux-run",
        help="Run a clipboard devtool block against a remote machine from Termux.",
    )
    parser.add_argument("--ssh-user", default=os.environ.get("SSH_USER", ""))
    parser.add_argument("--ssh-host", default=os.environ.get("SSH", ""))
    parser.add_argument("--ssh-port", default=os.environ.get("SSH_PORT", ""))
    parser.add_argument("--ssh-key", default=os.environ.get("SSH_KEY", ""))
    parser.add_argument("--remote-shell", default="zsh")
    parser.add_argument("--remote-devtool", default="/home/mike/.local/bin/devtool")
    parser.add_argument("--download-dir", default=str(TERMUX_SHARED_DOWNLOADS))
    parser.add_argument("--keep-local-archive", action="store_true")
    parser.add_argument("--yes", action="store_true")
    return parser


def handle_termux_run(args: argparse.Namespace) -> int:
    try:
        ensure_commands(("ssh", "scp", "termux-clipboard-get"))
        console.print("[cyan]Termux runner:[/cyan] clipboard block -> remote run")
        target = build_target(args)
        check_ssh_connection(target, args)

        clipboard_text = get_termux_clipboard()
        if not args.yes:
            confirm_clipboard_block(clipboard_text)

        parsed = parse_clipboard_block(clipboard_text)
        if parsed.requires_archive_upload:
            local_archive = find_local_archive(parsed.archive_basename or "", Path(args.download_dir))
            show_transfer_plan(target, args, local_archive, parsed.remote_artifact_path or "")
            ensure_remote_parent_dir(target, args, parsed.remote_artifact_path or "")
            upload_archive(target, args, local_archive, parsed.remote_artifact_path or "")
            if not args.keep_local_archive:
                delete_local_archive(local_archive)
        else:
            console.print("[cyan]No archive path detected; skipping upload step.[/cyan]")

        capabilities = detect_remote_capabilities(target, args)
        rewritten_block = rewrite_block_for_remote(clipboard_text, capabilities, args.remote_devtool, parsed)
        show_rewritten_block(rewritten_block, capabilities)

        stdout_text, returncode = run_remote_block(target, args, rewritten_block)

        consumed = False
        if stdout_text.strip():
            if capabilities.supports_runner_json:
                try:
                    payload = json.loads(stdout_text)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict) and payload.get("schema_version") == 1:
                    consume_runner_json(payload)
                    consumed = True
            if not consumed:
                show_remote_stdout(stdout_text)

        if returncode != 0:
            stderr_console.print(
                f"[yellow]Remote command exited with status {returncode}.[/yellow] "
                "Keeping execution non-fatal."
            )
        return 0
    except KeyboardInterrupt:
        stderr_console.print("\n[bold yellow]Aborted.[/bold yellow]")
        return 130
    except TermuxRunError as exc:
        stderr_console.print(f"[bold red]Error:[/bold red] {exc}")
        return 1


def ensure_commands(commands: Iterable[str]) -> None:
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        raise TermuxRunError(f"Missing required command(s): {', '.join(missing)}")


def build_target(args: argparse.Namespace) -> str:
    ssh_user = (args.ssh_user or "").strip()
    ssh_host = (args.ssh_host or "").strip()
    if not ssh_user or not ssh_host:
        raise TermuxRunError("Missing SSH target. Set SSH_USER and SSH, or pass --ssh-user and --ssh-host.")
    return f"{ssh_user}@{ssh_host}"


def ssh_base_command(target: str, args: argparse.Namespace) -> list[str]:
    command = ["ssh", *build_ssh_options(port=getattr(args, "ssh_port", ""), key_path=getattr(args, "ssh_key", ""))]
    command.append(target)
    return command


def scp_base_command(args: argparse.Namespace) -> list[str]:
    return scp_options_from_ssh(build_ssh_options(port=getattr(args, "ssh_port", ""), key_path=getattr(args, "ssh_key", "")))


def check_ssh_connection(target: str, args: argparse.Namespace) -> None:
    console.print(f"[cyan]Checking SSH connectivity:[/cyan] {target}")
    result = subprocess.run(
        [*ssh_base_command(target, args), "printf", "online"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise TermuxRunError(f"SSH connection check failed for {target}: {((result.stderr or '').strip() or 'unknown error')}")
    if (result.stdout or "").strip() != "online":
        raise TermuxRunError(f"SSH connection check returned unexpected output for {target}")
    console.print("[bold green]SSH connection OK.[/bold green]")


def get_termux_clipboard() -> str:
    result = subprocess.run(["termux-clipboard-get"], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise TermuxRunError(f"termux-clipboard-get failed: {((result.stderr or '').strip() or 'unknown error')}")
    text = result.stdout
    if not text.strip():
        raise TermuxRunError("Clipboard is empty.")
    return text


def confirm_clipboard_block(block: str, assume_yes: bool = False) -> None:
    title = "Clipboard block"
    if is_narrow_console(console):
        title = "Clipboard"
    console.print(
        Panel(
            Syntax(block.rstrip(), "bash", word_wrap=True, line_numbers=False),
            title=title,
            border_style="blue",
        )
    )
    if assume_yes:
        return
    answer = Prompt.ask("Use this clipboard block?", choices=["y", "n"], default="y")
    if answer.lower() != "y":
        raise TermuxRunError("Clipboard block not confirmed.")


def parse_clipboard_block(block: str) -> ParsedBlock:
    assignments = parse_assignments(block)
    artifact_expr = find_artifact_expression(assignments, block)
    if artifact_expr is None:
        return ParsedBlock(
            original_text=block,
            remote_artifact_expr=None,
            remote_artifact_path=None,
            archive_basename=None,
            requires_archive_upload=False,
        )

    remote_artifact_path = normalize_remote_artifact_path(artifact_expr, assignments)
    archive_basename = Path(remote_artifact_path).name
    if not archive_basename or not has_archive_suffix(archive_basename):
        raise TermuxRunError(f"Artifact path is not a supported archive: {remote_artifact_path}")

    return ParsedBlock(
        original_text=block,
        remote_artifact_expr=artifact_expr,
        remote_artifact_path=remote_artifact_path,
        archive_basename=archive_basename,
        requires_archive_upload=True,
    )


def parse_assignments(block: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for match in ASSIGNMENT_PATTERN.finditer(block):
        assignments[match.group("name")] = match.group("value")
    return assignments


def find_artifact_expression(assignments: dict[str, str], block: str) -> str | None:
    for key in ("ARTIFACT_CURRENT", "ARTIFACT", "ARCHIVE_CURRENT", "ARCHIVE"):
        value = assignments.get(key)
        if value and looks_like_archive_path(value):
            return value

    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("devtool "):
            continue
        parts = shlex.split(stripped)
        command_name = find_devtool_subcommand(parts[1:])
        if command_name not in APPLY_COMMANDS:
            continue
        for token in reversed(parts):
            if looks_like_archive_path(token):
                return token

    return None


def normalize_remote_artifact_path(value: str, assignments: dict[str, str]) -> str:
    raw = value.strip()
    basename = Path(raw).name
    remote_home = assignments.get("HOME", "/home/mike")

    prefixes = (
        f"{Path.home()}/Downloads/",
        "/sdcard/Download/",
        "/storage/emulated/0/Download/",
    )
    if any(raw.startswith(prefix) for prefix in prefixes):
        return f"{remote_home}/Downloads/{basename}"
    if raw.startswith("$HOME/"):
        return f"{remote_home}/{raw[len('$HOME/'):]}"
    if raw.startswith("${HOME}/"):
        return f"{remote_home}/{raw[len('${HOME}/'):]}"
    return expand_vars(raw, assignments)


def expand_vars(value: str, assignments: dict[str, str]) -> str:
    home = assignments.get("HOME", "/home/mike")
    expanded = value.replace("$HOME", home).replace("${HOME}", home)
    for _ in range(10):
        changed = False
        for key, raw_value in assignments.items():
            for pattern in (f"${key}", f"${{{key}}}"):
                if pattern in expanded:
                    expanded = expanded.replace(pattern, raw_value)
                    changed = True
        if not changed:
            break
    return expanded


def looks_like_archive_path(value: str) -> bool:
    lower = value.lower()
    return any(lower.endswith(suffix) for suffix in ARCHIVE_SUFFIXES)


def build_ssh_options(*, port: str = "", key_path: str = "", connect_timeout: int = 8) -> list[str]:
    options = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}"]
    if port:
        options += ["-p", str(port)]
    if key_path:
        options += ["-i", str(Path(key_path).expanduser())]
    return options


def scp_options_from_ssh(ssh_options: list[str]) -> list[str]:
    options = ["scp"]
    index = 0
    while index < len(ssh_options):
        token = ssh_options[index]
        if token == "-p" and index + 1 < len(ssh_options):
            options += ["-P", ssh_options[index + 1]]
            index += 2
            continue
        if token == "-i" and index + 1 < len(ssh_options):
            options += ["-i", ssh_options[index + 1]]
            index += 2
            continue
        index += 1
    return options


def block_contains_commit(block: str) -> bool:
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parts = shlex.split(stripped)
        except ValueError:
            continue
        if not parts:
            continue
        command_name = Path(parts[0]).name
        if command_name in {"git", "devtool"} and "commit" in parts[1:]:
            return True
    return False


def has_archive_suffix(filename: str) -> bool:
    return looks_like_archive_path(filename)


def find_local_archive(basename: str, download_dir: Path) -> Path:
    candidates = [
        download_dir / basename,
        TERMUX_SHARED_DOWNLOADS / basename,
        TERMUX_SHARED_DOWNLOADS_ALT / basename,
        TERMUX_APP_DOWNLOADS / basename,
    ]
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            console.print(f"[bold green]Found local archive:[/bold green]\n{candidate}")
            return candidate
    raise TermuxRunError("Archive not found on phone. Tried: " + ", ".join(str(path) for path in candidates))


def show_transfer_plan(target: str, args: argparse.Namespace, local_archive: Path, remote_artifact_path: str) -> None:
    table = Table(title="Transfer plan")
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", overflow="fold")
    table.add_row("SSH target", target)
    table.add_row("SSH port", str(args.ssh_port or "-"))
    table.add_row("SSH key", str(args.ssh_key or "-"))
    table.add_row("Phone archive", str(local_archive))
    table.add_row("Remote archive", remote_artifact_path)
    table.add_row("Remote devtool", str(args.remote_devtool))
    table.add_row("Remote shell", str(args.remote_shell))
    console.print(table)


def ensure_remote_parent_dir(target: str, args: argparse.Namespace, remote_artifact_path: str) -> None:
    remote_parent = str(Path(remote_artifact_path).parent)
    console.print(f"[cyan]Ensuring remote directory exists:[/cyan] {remote_parent}")
    result = subprocess.run(
        [*ssh_base_command(target, args), "mkdir", "-p", remote_parent],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise TermuxRunError(f"Failed to create remote directory {remote_parent}: {((result.stderr or '').strip() or 'unknown error')}")


def upload_archive(target: str, args: argparse.Namespace, local_archive: Path, remote_artifact_path: str) -> None:
    console.print(f"[cyan]Uploading archive:[/cyan]\n{local_archive.name}")
    result = subprocess.run(
        [*scp_base_command(args), str(local_archive), f"{target}:{remote_artifact_path}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise TermuxRunError(f"Upload failed: {((result.stderr or '').strip() or 'unknown error')}")
    console.print("[bold green]Upload complete.[/bold green]")


def delete_local_archive(local_archive: Path) -> None:
    try:
        local_archive.unlink()
    except OSError as exc:
        raise TermuxRunError(f"Uploaded successfully, but failed to delete local archive: {exc}") from exc
    console.print(f"[bold green]Deleted phone copy:[/bold green]\n{local_archive}")


def detect_remote_capabilities(target: str, args: argparse.Namespace) -> RemoteDevtoolCapabilities:
    command = [*ssh_base_command(target, args), args.remote_shell, "-lc", f"{quote_shell(args.remote_devtool)} --help"]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    output = ((result.stdout or "") + "\n" + (result.stderr or ""))
    if not output.strip():
        raise TermuxRunError("Failed to inspect remote devtool help.")
    supports_stdout_payload = "--stdout-payload" in output
    supports_runner_json = "runner-json" in output
    table = Table(title="Remote devtool capabilities")
    table.add_column("Capability", style="cyan")
    table.add_column("Supported")
    table.add_row("--stdout-payload", "yes" if supports_stdout_payload else "no")
    table.add_row("runner-json", "yes" if supports_runner_json else "no")
    console.print(table)
    return RemoteDevtoolCapabilities(
        supports_stdout_payload=supports_stdout_payload,
        supports_runner_json=supports_runner_json,
    )


def rewrite_block_for_remote(
    block: str,
    capabilities: RemoteDevtoolCapabilities,
    remote_devtool: str,
    parsed: ParsedBlock | None = None,
) -> str:
    parsed = parsed or parse_clipboard_block(block)

    def replace_devtool_line(match: re.Match[str]) -> str:
        prefix = match.group(1)
        indent = match.group("indent")
        parts = shlex.split(match.group("line"))
        if not parts:
            return prefix + indent + match.group("line")
        parts[0] = remote_devtool

        command_index = None
        command_name = None
        for index, token in enumerate(parts[1:], start=1):
            if token in DEVTOOL_COMMANDS and not token.startswith("-"):
                command_index = index
                command_name = token
                break

        archive_index = None
        if parsed.requires_archive_upload and command_name in APPLY_COMMANDS and parsed.remote_artifact_expr:
            for index in range(len(parts) - 1, (command_index or 0), -1):
                token = parts[index]
                if token == parsed.remote_artifact_expr or looks_like_archive_path(token):
                    archive_index = index
                    break

        cleaned: list[str] = [remote_devtool]
        skip_next = False
        for idx, token in enumerate(parts[1:], start=1):
            if skip_next:
                skip_next = False
                continue
            if token == "--stdout-payload" and idx + 1 < len(parts):
                skip_next = True
                continue
            if token in {"--json", "-j"}:
                continue
            if COPY_FLAG_PATTERN.fullmatch(token):
                continue
            if token.startswith("--stdout-payload="):
                continue
            if archive_index is not None and idx == archive_index and parsed.remote_artifact_path:
                cleaned.append(parsed.remote_artifact_path)
                continue
            cleaned.append(token)

        parts = cleaned

        insert_at = len(parts)
        for index, token in enumerate(parts[1:], start=1):
            if token in DEVTOOL_COMMANDS and not token.startswith("-"):
                insert_at = index
                break

        if capabilities.supports_runner_json:
            parts[insert_at:insert_at] = ["--stdout-payload", "runner-json"]
        elif capabilities.supports_stdout_payload:
            parts[insert_at:insert_at] = ["--stdout-payload", "json"]
        else:
            parts[insert_at:insert_at] = ["--json"]

        return prefix + indent + shlex.join(parts)

    return DEVTOOL_LINE_PATTERN.sub(replace_devtool_line, block).rstrip() + "\n"
def show_rewritten_block(block: str, capabilities: RemoteDevtoolCapabilities) -> None:
    if capabilities.supports_runner_json:
        mode = "--stdout-payload runner-json"
    elif capabilities.supports_stdout_payload:
        mode = "--stdout-payload json"
    else:
        mode = "--json"
    console.print(
        Panel(
            Syntax(block.rstrip(), "bash", word_wrap=True, line_numbers=False),
            title=f"Remote block ({mode})",
            border_style="magenta",
        )
    )


def run_remote_block(target: str, args: argparse.Namespace, block: str) -> tuple[str, int]:
    console.print(f"[cyan]Remote run:[/cyan] {args.remote_shell} on {target}")
    process = subprocess.Popen(
        [*ssh_base_command(target, args), args.remote_shell, "-s"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout_text, stderr_text = process.communicate(block)
    except Exception:
        process.kill()
        raise
    if stderr_text:
        sys.stderr.write(stderr_text)
        sys.stderr.flush()
    return stdout_text or "", int(process.returncode or 0)


def consume_runner_json(payload: dict[str, object]) -> None:
    notification = payload.get("notification")
    if isinstance(notification, dict) and shutil.which("termux-notification"):
        if notification.get("should_notify", False):
            subprocess.run(
                [
                    "termux-notification",
                    "--title", str(notification.get("title", "devtool")),
                    "--content", str(notification.get("body", "")),
                    "--id", "devtool-runner",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

    show_runner_result(payload)


def show_runner_result(payload: dict[str, object]) -> None:
    totals = payload.get("totals")
    if isinstance(totals, dict):
        rows = [
            ("ok", str(payload.get("ok"))),
            ("command", str(payload.get("command"))),
            ("repo", str(payload.get("repo"))),
            ("warnings", str(totals.get("warnings"))),
            ("errors", str(totals.get("errors"))),
            ("failed", str(totals.get("failed"))),
            ("skipped", str(totals.get("skipped"))),
        ]
        if is_narrow_console(console):
            grid = Table.grid(expand=False)
            grid.add_column(style="bold", no_wrap=True)
            grid.add_column(overflow="fold")
            for key, value in rows:
                grid.add_row(key, value)
            console.print(Panel(grid, title="Remote result", expand=False))
        else:
            table = Table(title="Remote result")
            table.add_column("Field", style="cyan", no_wrap=True)
            table.add_column("Value", overflow="fold")
            for key, value in rows:
                table.add_row(key, value)
            console.print(table)


def show_remote_stdout(stdout_text: str) -> None:
    if not stdout_text.strip():
        return
    title = "Remote stdout" if not is_narrow_console(console) else "Remote output"
    console.print(
        Panel(
            Syntax(stdout_text.rstrip(), "text", word_wrap=True, line_numbers=False),
            title=title,
            border_style="green",
        )
    )


def set_termux_clipboard(text: str) -> None:
    result = subprocess.run(
        ["termux-clipboard-set"],
        input=text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise TermuxRunError(f"termux-clipboard-set failed: {((result.stderr or '').strip() or 'unknown error')}")


def find_devtool_subcommand(tokens: list[str]) -> str | None:
    skip_value = False
    options_with_value = {"-r", "--repo", "-c", "--config", "--theme", "--stdout-payload"}
    for idx, token in enumerate(tokens):
        if skip_value:
            skip_value = False
            continue
        if token in options_with_value:
            skip_value = True
            continue
        if token.startswith("--repo=") or token.startswith("--stdout-payload="):
            continue
        if token.startswith("-"):
            continue
        if token in DEVTOOL_COMMANDS:
            return token
    return None


def quote_shell(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
