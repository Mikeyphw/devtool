from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable, Sequence

from rich.console import Console
from rich.prompt import Confirm, Prompt

from ..console_utils import create_console, is_narrow_console, is_termux_environment


@dataclass(frozen=True, slots=True)
class Choice:
    key: str
    label: str
    description: str = ""
    preview: str = ""
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class Selection:
    keys: tuple[str, ...]
    action: str = "enter"

    @property
    def key(self) -> str:
        return self.keys[0] if self.keys else ""


class TerminalUI:
    """Small fzf-first UI with a deterministic numbered fallback.

    The fallback is intentionally complete rather than decorative. Devtool must
    remain usable on recovery shells, CI terminals, and minimal Termux installs
    where fzf or bat is temporarily unavailable.
    """

    REVIEW_EXPECT = ("ctrl-d", "ctrl-y", "ctrl-e", "ctrl-s", "ctrl-l")

    def __init__(self, *, console: Console | None = None, force_fallback: bool = False) -> None:
        self.console = console or create_console()
        self.force_fallback = force_fallback
        self.fzf = shutil.which("fzf")
        self.bat = shutil.which("bat") or shutil.which("batcat")
        self.last_action = "enter"
        self.termux = is_termux_environment()

    @property
    def interactive(self) -> bool:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())

    @property
    def use_fzf(self) -> bool:
        return bool(self.fzf and self.interactive and not self.force_fallback)

    @property
    def compact(self) -> bool:
        return bool(is_narrow_console(self.console) or self.console.width <= 72)

    @staticmethod
    def _ascii_text(value: str) -> str:
        replacements = {
            "▶": ">",
            "◆": "*",
            "◇": "-",
            "≫": ">>",
            "↻": "R",
            "⟳": "R",
            "♻": "R",
            "Ⅱ": "||",
            "■": "x",
            "●": "*",
            "✓": "+",
            "☷": "#",
            "⊞": "+",
            "▣": "#",
            "⬢": "*",
            "⚙": "*",
            "★": "*",
            "⚕": "+",
            "→": "->",
            "·": "-",
        }
        text = value
        for source, target in replacements.items():
            text = text.replace(source, target)
        return text

    def _display_text(self, value: str) -> str:
        if self.termux and self.compact:
            return self._ascii_text(value)
        return value

    def _preview_window(self) -> str:
        if self.termux and self.compact:
            return "down,45%,border-top,wrap"
        if self.compact:
            return "down,40%,border-top,wrap"
        return "right,58%,border-left,wrap"

    def choose(
        self,
        choices: Sequence[Choice],
        *,
        prompt: str,
        header: str = "",
        multi: bool = False,
        expect: Sequence[str] = (),
        initial_query: str = "",
    ) -> Selection | None:
        enabled = [item for item in choices if not item.disabled]
        if not enabled:
            return None
        self.last_action = "enter"
        if self.use_fzf:
            selection = self._choose_fzf(enabled, prompt=prompt, header=header, multi=multi, expect=expect, initial_query=initial_query)
        else:
            selection = self._choose_fallback(enabled, prompt=prompt, header=header, multi=multi)
        if selection is not None:
            self.last_action = selection.action
            if selection.action == "alt-left":
                return None
        return selection

    def _choose_fzf(
        self,
        choices: Sequence[Choice],
        *,
        prompt: str,
        header: str,
        multi: bool,
        expect: Sequence[str],
        initial_query: str,
    ) -> Selection | None:
        with tempfile.TemporaryDirectory(prefix="devtool-fzf-") as raw_dir:
            preview_dir = Path(raw_dir)
            rows: list[str] = []
            for index, item in enumerate(choices):
                token = f"{index:04d}"
                detail = self._display_text(item.description.replace("\n", " ").strip())
                rows.append(f"{token}\t{self._display_text(item.label)}\t{detail}")
                preview = self._display_text(item.preview.strip() or self._default_preview(item))
                (preview_dir / f"{token}.md").write_text(preview + "\n", encoding="utf-8")

            viewer = self._preview_command(preview_dir)
            pointer = ">" if self.termux and self.compact else "▶"
            marker = "*" if self.termux and self.compact else "◆"
            argv = [
                str(self.fzf),
                "--ansi",
                "--layout=reverse",
                "--border=rounded",
                "--info=inline",
                "--height=100%" if self.termux and self.compact else "--height=95%",
                "--delimiter=\t",
                "--with-nth=2,3",
                f"--prompt={prompt}> ",
                f"--preview-window={self._preview_window()}",
                "--bind=ctrl-p:toggle-preview",
                f"--pointer={pointer}",
                f"--marker={marker}",
                "--color=header:bold",
                "--preview",
                viewer,
            ]
            if header:
                argv.append(f"--header={self._display_text(header)}")
            if multi:
                argv.append("--multi")
            accepted_actions = tuple(dict.fromkeys([*expect, "alt-left"]))
            if "ctrl-r" not in accepted_actions:
                argv.append("--bind=ctrl-r:refresh-preview")
            argv.append("--expect=" + ",".join(accepted_actions))
            if initial_query:
                argv.append(f"--query={initial_query}")

            completed = subprocess.run(
                argv,
                input="\n".join(rows),
                text=True,
                capture_output=True,
                check=False,
            )
            if completed.returncode != 0:
                return None
            lines = [line for line in completed.stdout.splitlines() if line.strip()]
            action = "enter"
            if not lines:
                return None
            first = lines[0].strip()
            if first in accepted_actions:
                action = first
                lines = lines[1:]
            selected: list[str] = []
            for line in lines:
                token = line.split("\t", 1)[0]
                try:
                    selected.append(choices[int(token)].key)
                except (ValueError, IndexError):
                    continue
            if selected:
                return Selection(tuple(selected), action=action)
            if action != "enter":
                return Selection((), action=action)
            return None

    def _preview_command(self, preview_dir: Path) -> str:
        path = str(preview_dir)
        if self.bat:
            return f"{shlex_quote(str(self.bat))} --color=always --style=plain --language=markdown {shlex_quote(path)}/{{1}}.md"
        return f"cat {shlex_quote(path)}/{{1}}.md"

    @staticmethod
    def _default_preview(item: Choice) -> str:
        title = f"# {item.label}"
        return title + (f"\n\n{item.description}" if item.description else "")

    def _choose_fallback(
        self,
        choices: Sequence[Choice],
        *,
        prompt: str,
        header: str,
        multi: bool,
    ) -> Selection | None:
        if header:
            self.console.print(f"[bold]{self._display_text(header)}[/bold]")
        for index, item in enumerate(choices, start=1):
            label = self._display_text(item.label)
            description = self._display_text(item.description)
            if self.compact and description:
                self.console.print(f"  [cyan]{index:>2}[/cyan]  {label}")
                self.console.print(f"      [dim]{description}[/dim]")
            else:
                suffix = f" [dim]{description}[/dim]" if description else ""
                self.console.print(f"  [cyan]{index:>2}[/cyan]  {label}{suffix}")
        self.console.print("  [cyan] 0[/cyan]  Back")
        raw = Prompt.ask(prompt, default="0").strip()
        if not raw or raw == "0":
            return None
        selected: list[str] = []
        tokens = [token.strip() for token in raw.replace(" ", ",").split(",") if token.strip()]
        if not multi and len(tokens) > 1:
            tokens = tokens[:1]
        for token in tokens:
            try:
                selected.append(choices[int(token) - 1].key)
            except (ValueError, IndexError):
                self.console.print(f"[yellow]Ignoring invalid selection: {token}[/yellow]")
        return Selection(tuple(dict.fromkeys(selected))) if selected else None

    def ask(self, label: str, *, default: str = "", password: bool = False) -> str:
        return Prompt.ask(label, default=default or None, password=password).strip()

    def confirm(self, label: str, *, default: bool = False) -> bool:
        return bool(Confirm.ask(label, default=default, console=self.console))

    def page(self, text: str, *, language: str = "markdown") -> None:
        if self.bat and self.interactive:
            subprocess.run(
                [str(self.bat), "--color=always", "--style=plain", f"--language={language}"],
                input=text,
                text=True,
                check=False,
            )
        else:
            self.console.print(text)

    def edit_text(self, text: str, *, suffix: str = ".txt") -> str:
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or shutil.which("micro") or shutil.which("nano") or shutil.which("vi")
        if not editor:
            self.console.print("[yellow]No editor found. Set $VISUAL or $EDITOR.[/yellow]")
            return text
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=suffix, delete=False) as handle:
            handle.write(text)
            handle.flush()
            path = Path(handle.name)
        try:
            subprocess.run([*split_editor(editor), str(path)], check=False)
            return path.read_text(encoding="utf-8", errors="replace")
        finally:
            path.unlink(missing_ok=True)


def split_editor(editor: str) -> list[str]:
    import shlex

    return shlex.split(editor)


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)
