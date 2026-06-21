from __future__ import annotations

import argparse
import json
import os
import re
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

from .console_utils import create_console, syntax_line_numbers

console = create_console()
stderr_console = create_console(stderr=True)

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

TERMUX_SHARED_DOWNLOADS = Path("/sdcard/Download")
TERMUX_SHARED_DOWNLOADS_ALT = Path("/storage/emulated/0/Download")
TERMUX_APP_DOWNLOADS = Path.home() / "Downloads"

COPY_FLAG_PATTERN = re.compile(r"(?<!\S)--copy(?:-(?:json|raw|summary))?(?!\S)")
JSON_FLAG_PATTERN = re.compile(r"(?<!\S)(?:-j|--json)(?!\S)")
STDOUT_PAYLOAD_PATTERN = re.compile(r"(?<!\S)--stdout-payload(?:=|\s+)(?:auto|summary|json|raw|runner-json)(?!\S)")
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
TOKEN_PATTERN = re.compile(r"""(?P<token>[/~$A-Za-z0-9_.\-{}]+(?:[/~$A-Za-z0-9_.\-{}]+|/[~$A-Za-z0-9_.\-{}]+)*)""")

DEVTOOL_COMMANDS = {
    "restore", "rs", "build", "b", "test", "t", "validate", "v",
    "wine-validate", "wv", "run", "rn", "package", "pkg", "status", "st",
    "doctor", "dr", "ai-prompt", "ap", "commit", "ci", "apply-overlay", "ao",
    "apply-snapshot", "as", "apply-overlay-validate", "aov",
    "apply-snapshot-validate", "asv", "batch", "init-config", "ic",
    "logs", "tag", "release-notes", "completion", "config", "termux-run",
}


class TermuxRunError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParsedBlock:
    original_text: str
    remote_artifact_expr: str
    remote_artifact_path: str
    archive_basename: str


@dataclass(frozen=True)
class RemoteCapabilities:
    supports_stdout_payload: bool
    supports_runner_json: bool


def add_termux_run_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    cmd = subparsers.add_parser("termux-run", help="Run a clipboard block against a remote host from Termux")
    cmd.add_argument("--ssh-user", default="", help="SSH username. Defaults to $SSH_USER")
    cmd.add_argument("--ssh-host", default="", help="SSH host. Defaults to $SSH")
    cmd.add_argument("--ssh-port", default="", help="SSH port. Defaults to $SSH_PORT")
    cmd.add_argument("--ssh-key", default="", help="SSH private key path. Defaults to $SSH_KEY")
    cmd.add_argument("--remote-devtool", default="/home/mike/.local/bin/devtool", help="Remote devtool path")
    cmd.add_argument("--remote-shell", default="zsh", help="Remote shell executable")
    cmd.add_argument("--download-dir", default="/sdcard/Download", help="Local archive search directory")
    cmd.add_argument("--keep-local-archive", action="store_true", help="Do not delete the uploaded local archive")
    cmd.add_argument("--yes", action="store_true", help="Skip clipboard and commit confirmations")
    cmd.add_argument("--connect-timeout", type=int, default=8, help="SSH connect timeout in seconds")


def handle_termux_run(args: argparse.Namespace) -> int:
    try:
        ssh_user = args.ssh_user.strip() or os.environ.get("SSH_USER", "").strip()
        ssh_host = args.ssh_host.strip() or os.environ.get("SSH", "").strip()
        ssh_port = args.ssh_port.strip() or os.environ.get("SSH_PORT", "").strip()
        ssh_key = args.ssh_key.strip() or os.environ.get("SSH_KEY", "").strip()
        if not ssh_user:
            raise TermuxRunError("Missing SSH username. Set --ssh-user or SSH_USER.")
        if not ssh_host:
            raise TermuxRunError("Missing SSH host. Set --ssh-host or SSH.")
        ensure_commands(("ssh", "scp", "termux-clipboard-get", "termux-clipboard-set"))

        target = f"{ssh_user}@{ssh_host}"
        ssh_options = build_ssh_options(port=ssh_port, key_path=ssh_key, connect_timeout=args.connect_timeout)

        check_ssh_connection(target, ssh_options)

        clipboard_text = get_termux_clipboard()
        confirm_clipboard_block(clipboard_text, assume_yes=args.yes)

        parsed = parse_clipboard_block(clipboard_text)
        download_dir = Path(args.download_dir).expanduser()
        local_archive = find_local_archive(parsed.archive_basename, download_dir)

        show_transfer_plan(
            target,
            local_archive,
            parsed.remote_artifact_path,
            args.remote_devtool,
            args.remote_shell,
            ssh_port=ssh_port or "-",
            ssh_key=ssh_key or "-",
        )
        ensure_remote_parent_dir(target, parsed.remote_artifact_path, ssh_options)
        upload_archive(target, local_archive, parsed.remote_artifact_path, ssh_options)

        if not args.keep_local_archive:
            delete_local_archive(local_archive)

        capabilities = detect_remote_capabilities(target, args.remote_devtool, args.remote_shell, ssh_options)
        rewritten_block = rewrite_block_for_remote(
            clipboard_text,
            remote_devtool=args.remote_devtool,
            capabilities=capabilities,
        )
        show_rewritten_block(rewritten_block, capabilities)

        if block_contains_commit(rewritten_block):
            confirm_commit_block(assume_yes=args.yes)

        stdout_text = run_remote_block(target, rewritten_block, shell=args.remote_shell, ssh_options=ssh_options)

        if capabilities.supports_runner_json:
            payload = parse_runner_json(stdout_text)
            copy_preferred_clipboard(payload)
            maybe_notify(payload)
            print_runner_result(payload)
            console.print("[bold green]Done.[/bold green] runner-json consumed successfully.")
        else:
            set_termux_clipboard(stdout_text)
            console.print("[bold green]Done.[/bold green] Remote stdout copied to Termux clipboard.")

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


def build_ssh_options(*, port: str, key_path: str, connect_timeout: int) -> list[str]:
    options = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}"]
    if port:
        options.extend(["-p", port])
    if key_path:
        options.extend(["-i", str(Path(key_path).expanduser())])
    return options


def check_ssh_connection(target: str, ssh_options: list[str]) -> None:
    console.print(f"[cyan]Checking SSH connectivity:[/cyan] {target}")
    result = subprocess.run(
        ["ssh", *ssh_options, target, "printf", "online"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        reason = (result.stderr or "").strip() or "unknown error"
        raise TermuxRunError(f"SSH connection check failed for {target}: {reason}")
    if (result.stdout or "").strip() != "online":
        raise TermuxRunError(f"SSH connection check returned unexpected output for {target}")
    console.print("[bold green]SSH connection OK.[/bold green]")


def get_termux_clipboard() -> str:
    result = subprocess.run(
        ["termux-clipboard-get"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        reason = (result.stderr or "").strip() or "unknown error"
        raise TermuxRunError(f"termux-clipboard-get failed: {reason}")
    if not result.stdout.strip():
        raise TermuxRunError("Clipboard is empty.")
    return result.stdout


def confirm_clipboard_block(block: str, *, assume_yes: bool) -> None:
    console.print(
        Panel(
            Syntax(block.rstrip(), "bash", word_wrap=True, line_numbers=syntax_line_numbers(console)),
            title="Clipboard block",
            border_style="blue",
        )
    )
    if assume_yes:
        return
    answer = Prompt.ask("Use this clipboard block?", choices=["y", "n"], default="y", console=console)
    if answer.lower() != "y":
        raise TermuxRunError("Clipboard block not confirmed.")


def parse_clipboard_block(block: str) -> ParsedBlock:
    assignments = parse_assignments(block)
    artifact_expr = find_artifact_expression(assignments, block)
    remote_artifact_path = normalize_remote_artifact_path(artifact_expr, assignments)
    archive_basename = Path(remote_artifact_path).name
    if not archive_basename:
        raise TermuxRunError("Could not determine artifact basename from clipboard block.")
    if not has_archive_suffix(archive_basename):
        raise TermuxRunError(f"Artifact path is not a supported archive: {remote_artifact_path}")
    return ParsedBlock(
        original_text=block,
        remote_artifact_expr=artifact_expr,
        remote_artifact_path=remote_artifact_path,
        archive_basename=archive_basename,
    )


def normalize_remote_artifact_path(value: str, assignments: dict[str, str]) -> str:
    expanded = expand_vars(value, assignments)
    basename = Path(expanded).name
    remote_home = assignments.get("HOME", "/home/mike")
    termux_prefixes = (
        f"{Path.home()}/Downloads/",
        "/sdcard/Download/",
        "/storage/emulated/0/Download/",
    )
    if any(expanded.startswith(prefix) for prefix in termux_prefixes):
        return f"{remote_home}/Downloads/{basename}"
    return expanded


def parse_assignments(block: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for match in ASSIGNMENT_PATTERN.finditer(block):
        assignments[match.group("name")] = match.group("value")
    return assignments


def find_artifact_expression(assignments: dict[str, str], block: str) -> str:
    for key in ("ARTIFACT_CURRENT", "ARTIFACT", "ARCHIVE_CURRENT", "ARCHIVE"):
        value = assignments.get(key)
        if value and looks_like_archive_path(value):
            return value
    archive_tokens = find_archive_tokens(block)
    if archive_tokens:
        return archive_tokens[0]
    raise TermuxRunError("Could not find an archive path in the clipboard block.")


def find_archive_tokens(text: str) -> list[str]:
    matches: list[str] = []
    for match in TOKEN_PATTERN.finditer(text):
        token = match.group("token").strip("'\"")
        if looks_like_archive_path(token):
            matches.append(token)
    return matches


def looks_like_archive_path(value: str) -> bool:
    lower = value.lower()
    return any(lower.endswith(suffix) for suffix in ARCHIVE_SUFFIXES)


def has_archive_suffix(filename: str) -> bool:
    return looks_like_archive_path(filename)


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


def find_local_archive(basename: str, download_dir: Path) -> Path:
    candidates = [
        download_dir / basename,
        TERMUX_SHARED_DOWNLOADS / basename,
        TERMUX_SHARED_DOWNLOADS_ALT / basename,
        TERMUX_APP_DOWNLOADS / basename,
    ]
    seen: set[str] = set()
    for candidate in candidates:
        candidate_key = str(candidate)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        if candidate.is_file():
            console.print(f"[bold green]Found local archive:[/bold green] {candidate}")
            return candidate
    raise TermuxRunError(
        "Archive not found on phone. Tried: " + ", ".join(str(path) for path in candidates)
    )


def show_transfer_plan(
    target: str,
    local_archive: Path,
    remote_artifact_path: str,
    remote_devtool: str,
    remote_shell: str,
    *,
    ssh_port: str,
    ssh_key: str,
) -> None:
    table = Table(title="Transfer plan")
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", overflow="fold")
    table.add_row("SSH target", target)
    table.add_row("SSH port", ssh_port)
    table.add_row("SSH key", ssh_key)
    table.add_row("Phone archive", str(local_archive))
    table.add_row("Remote archive", remote_artifact_path)
    table.add_row("Remote devtool", remote_devtool)
    table.add_row("Remote shell", remote_shell)
    console.print(table)


def ensure_remote_parent_dir(target: str, remote_artifact_path: str, ssh_options: list[str]) -> None:
    remote_parent = str(Path(remote_artifact_path).parent)
    console.print(f"[cyan]Ensuring remote directory exists:[/cyan] {remote_parent}")
    result = subprocess.run(
        ["ssh", *ssh_options, target, "mkdir", "-p", remote_parent],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        reason = (result.stderr or "").strip() or "unknown error"
        raise TermuxRunError(f"Failed to create remote directory {remote_parent}: {reason}")


def upload_archive(target: str, local_archive: Path, remote_artifact_path: str, ssh_options: list[str]) -> None:
    console.print(f"[cyan]Uploading archive:[/cyan] {local_archive.name}")
    result = subprocess.run(
        ["scp", *scp_options_from_ssh(ssh_options), str(local_archive), f"{target}:{remote_artifact_path}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        reason = (result.stderr or "").strip() or "unknown error"
        raise TermuxRunError(f"Upload failed: {reason}")
    console.print("[bold green]Upload complete.[/bold green]")


def scp_options_from_ssh(ssh_options: list[str]) -> list[str]:
    translated: list[str] = []
    i = 0
    while i < len(ssh_options):
        item = ssh_options[i]
        if item == "-p":
            translated.extend(["-P", ssh_options[i + 1]])
            i += 2
            continue
        translated.append(item)
        i += 1
    return translated


def delete_local_archive(local_archive: Path) -> None:
    try:
        local_archive.unlink()
    except OSError as exc:
        raise TermuxRunError(f"Uploaded successfully, but failed to delete local archive: {exc}") from exc
    console.print(f"[bold green]Deleted phone copy:[/bold green] {local_archive}")


def detect_remote_capabilities(target: str, remote_devtool: str, remote_shell: str, ssh_options: list[str]) -> RemoteCapabilities:
    probes = [
        ["ssh", *ssh_options, target, remote_shell, "-lc", f"\"{remote_devtool}\" --help"],
        ["ssh", *ssh_options, target, remote_devtool, "--help"],
    ]
    combined_output = ""
    for probe in probes:
        result = subprocess.run(probe, text=True, capture_output=True, check=False)
        output = ((result.stdout or "") + "
" + (result.stderr or "")).strip()
        if output:
            combined_output = output
        if "--stdout-payload" in output or "runner-json" in output or "usage:" in output.lower():
            capabilities = RemoteCapabilities(
                supports_stdout_payload="--stdout-payload" in output,
                supports_runner_json="runner-json" in output,
            )
            table = Table(title="Remote devtool capabilities")
            table.add_column("Capability", style="cyan")
            table.add_column("Supported")
            table.add_row("--stdout-payload", "yes" if capabilities.supports_stdout_payload else "no")
            table.add_row("runner-json", "yes" if capabilities.supports_runner_json else "no")
            console.print(table)
            return capabilities
    reason = combined_output or "unknown error"
    raise TermuxRunError(f"Failed to inspect remote devtool help: {reason}")


def rewrite_block_for_remote(block: str, *, remote_devtool: str, capabilities: RemoteCapabilities) -> str:
    def replace_devtool_line(match: re.Match[str]) -> str:
        prefix = match.group(1)
        indent = match.group("indent")
        line = match.group("line")

        line = COPY_FLAG_PATTERN.sub("", line)
        line = JSON_FLAG_PATTERN.sub("", line)
        line = STDOUT_PAYLOAD_PATTERN.sub("", line)
        line = collapse_spaces(line)

        parts = line.split()
        if not parts:
            return f"{prefix}{indent}{line}"

        if parts[0] == "devtool":
            parts[0] = remote_devtool

        insert_at = len(parts)
        for index, part in enumerate(parts[1:], start=1):
            if part in DEVTOOL_COMMANDS and not part.startswith("-"):
                insert_at = index
                break

        if capabilities.supports_runner_json:
            parts[insert_at:insert_at] = ["--stdout-payload", "runner-json"]
        elif capabilities.supports_stdout_payload:
            parts[insert_at:insert_at] = ["--stdout-payload", "json"]
        else:
            parts[insert_at:insert_at] = ["--json"]

        return f"{prefix}{indent}{' '.join(parts)}"

    rewritten = DEVTOOL_LINE_PATTERN.sub(replace_devtool_line, block)
    return rewritten.rstrip() + "\n"


def collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def show_rewritten_block(block: str, capabilities: RemoteCapabilities) -> None:
    if capabilities.supports_runner_json:
        mode = "--stdout-payload runner-json"
    elif capabilities.supports_stdout_payload:
        mode = "--stdout-payload json"
    else:
        mode = "--json"
    console.print(
        Panel(
            Syntax(block.rstrip(), "bash", word_wrap=True, line_numbers=syntax_line_numbers(console)),
            title=f"Rewritten remote block ({mode})",
            border_style="magenta",
        )
    )


def block_contains_commit(block: str) -> bool:
    patterns = (
        r"(^|\n)\s*git\b[^\n]*\bcommit\b",
        r"(^|\n)\s*git\b[^\n]*\badd\b[^\n]*",
        rf"(^|\n)\s*{re.escape('devtool')}\b[^\n]*\b(commit|ci)\b",
        rf"(^|\n)\s*{re.escape('/home/mike/.local/bin/devtool')}\b[^\n]*\b(commit|ci)\b",
    )
    return any(re.search(pattern, block, flags=re.MULTILINE) for pattern in patterns)


def confirm_commit_block(*, assume_yes: bool) -> None:
    if assume_yes:
        return
    answer = Prompt.ask("This block contains commit commands. Continue?", choices=["y", "n"], default="y")
    if answer.lower() != "y":
        raise TermuxRunError("Commit-containing block not confirmed.")


def run_remote_block(target: str, block: str, *, shell: str, ssh_options: list[str]) -> str:
    console.print(f"[cyan]Running remote block via {shell} on[/cyan] {target}")
    process = subprocess.Popen(
        ["ssh", *ssh_options, target, shell, "-s"],
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
    if process.returncode != 0:
        raise TermuxRunError(
            f"Remote block failed with exit code {process.returncode}. "
            "See remote stderr above for the actual command failure."
        )
    if stdout_text:
        console.print(f"[bold green]Captured remote stdout:[/bold green] {len(stdout_text)} bytes")
    else:
        console.print("[yellow]Remote stdout was empty.[/yellow]")
    return stdout_text


def parse_runner_json(stdout_text: str) -> dict[str, object]:
    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise TermuxRunError(f"Failed to parse runner-json from remote stdout: {exc}") from exc
    if payload.get("schema_version") != 1:
        raise TermuxRunError(f"Unsupported runner-json schema_version: {payload.get('schema_version')}")
    return payload


def copy_preferred_clipboard(payload: dict[str, object]) -> None:
    clipboard = payload.get("clipboard", {})
    preferred = clipboard.get("preferred", "json")
    if preferred == "summary":
        text = str(clipboard.get("summary", ""))
    elif preferred == "raw":
        text = str(clipboard.get("raw", ""))
    else:
        text = json.dumps(clipboard.get("json", {}), indent=2)
    set_termux_clipboard(text)


def maybe_notify(payload: dict[str, object]) -> None:
    if shutil.which("termux-notification") is None:
        return
    notification = payload.get("notification", {})
    if not notification.get("should_notify", False):
        return
    cmd = [
        "termux-notification",
        "--title",
        str(notification.get("title", "devtool")),
        "--content",
        str(notification.get("body", "")),
        "--id",
        "devtool-termux-run",
    ]
    if notification.get("level") == "error":
        cmd += ["--priority", "high"]
    subprocess.run(cmd, text=True, capture_output=True, check=False)


def print_runner_result(payload: dict[str, object]) -> None:
    totals = payload.get("totals", {})
    table = Table(title="Remote result")
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", overflow="fold")
    table.add_row("ok", str(payload.get("ok")))
    table.add_row("command", str(payload.get("command")))
    table.add_row("repo", str(payload.get("repo")))
    table.add_row("duration_s", str(payload.get("duration_s")))
    table.add_row("warnings", str(totals.get("warnings")))
    table.add_row("errors", str(totals.get("errors")))
    table.add_row("failed", str(totals.get("failed")))
    table.add_row("skipped", str(totals.get("skipped")))
    console.print(table)


def set_termux_clipboard(text: str) -> None:
    result = subprocess.run(
        ["termux-clipboard-set"],
        input=text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        reason = (result.stderr or "").strip() or "unknown error"
        raise TermuxRunError(f"termux-clipboard-set failed: {reason}")


def quote_shell(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
