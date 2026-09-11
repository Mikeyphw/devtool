"""Terminal glyph selection for Devtool live progress chrome.

The active TUI panel uses a diamond while work is running.  When the terminal is
likely to support Nerd Font glyphs, replace that decorative diamond with a
project-language icon while keeping success/failure status icons unchanged.
"""

from __future__ import annotations

from pathlib import Path
import os
import re
from collections.abc import Mapping

FALLBACK_ACTIVITY_ICON = "◆"

# Codepoints are Nerd Font / Devicons / MDI glyphs.  Keep these as literal
# characters so terminal width handling observes the same value users see.
NERD_FONT_ICONS: dict[str, str] = {
    "android": "\ue70e",      # nf-dev-android
    "kotlin": "\U000f1219",  # nf-md-language_kotlin
    "python": "\ue73c",       # nf-dev-python
    "java": "\ue738",         # nf-dev-java
    "javascript": "\ue781",   # nf-dev-javascript
    "typescript": "\ue89d",   # nf-seti-typescript
    "node": "\ue718",         # nf-dev-nodejs
    "rust": "\ue7a8",         # nf-dev-rust
    "go": "\ue627",           # nf-seti-go
    "shell": "\ue795",        # nf-dev-terminal
    "docker": "\ue7b0",       # nf-dev-docker
    "git": "\ue702",          # nf-dev-git
    "dotnet": "\U000f0310",   # nf-md-dot_net
    "gradle": "\ue70e",       # no stable Gradle devicon; prefer Android/Java fallback
    "generic": FALLBACK_ACTIVITY_ICON,
}

_LANGUAGE_ALIASES: dict[str, str] = {
    "py": "python",
    "python3": "python",
    "android_hud": "android",
    "android-hud": "android",
    "gradle": "gradle",
    "kts": "kotlin",
    "kt": "kotlin",
    "js": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "jsx": "javascript",
    "npm": "node",
    "nodejs": "node",
    "sh": "shell",
    "bash": "shell",
    "zsh": "shell",
    "fish": "shell",
    "cs": "dotnet",
    "csharp": "dotnet",
    ".net": "dotnet",
}

_ENV_TRUE = {"1", "true", "yes", "on", "force"}
_ENV_FALSE = {"0", "false", "no", "off", "never"}


def _env_flag(value: object) -> str:
    return str(value or "").strip().lower()


def nerd_font_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether Nerd Font glyphs should be used for decorative chrome.

    The user can force the behavior with ``DEVTOOL_NERD_FONT=1`` or disable it
    with ``DEVTOOL_NO_NERD_FONT=1`` / ``DEVTOOL_NERD_FONT=0``.  In auto mode we
    avoid CI, dumb terminals, explicit ASCII modes, and non-UTF locales; this is
    intentionally conservative because a missing Nerd Font renders tofu boxes.
    """

    explicit_env = env is not None
    env = env or os.environ
    ui_icon_mode = _env_flag(env.get("DEVTOOL_UI_ICONS"))
    if not ui_icon_mode and not explicit_env:
        try:
            from .ui_core.preferences import resolve_user_ui_preferences
            ui_icon_mode = resolve_user_ui_preferences(surface="tui").icons
        except Exception:
            ui_icon_mode = "auto"
    if ui_icon_mode == "nerd":
        return True
    if ui_icon_mode in {"ascii", "unicode", "native"}:
        return False
    if _env_flag(env.get("DEVTOOL_NO_NERD_FONT")) in _ENV_TRUE:
        return False
    configured = _env_flag(env.get("DEVTOOL_NERD_FONT"))
    if configured in _ENV_TRUE:
        return True
    if configured in _ENV_FALSE:
        return False
    if _env_flag(env.get("DEVTOOL_ASCII")) in _ENV_TRUE:
        return False
    if _env_flag(env.get("CI")) in _ENV_TRUE:
        return False
    term = _env_flag(env.get("TERM"))
    if not term or term == "dumb":
        return False
    encoding_bits = " ".join(str(env.get(key, "") or "") for key in ("LC_ALL", "LC_CTYPE", "LANG", "PYTHONIOENCODING")).lower()
    if "ascii" in encoding_bits or "ansi_x3.4" in encoding_bits:
        return False
    if encoding_bits and "utf" not in encoding_bits:
        return False
    return True


def _normalize_language(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.+-]+", "-", str(value or "").strip().lower()).strip("-._")
    if not text:
        return ""
    return _LANGUAGE_ALIASES.get(text, text)


def _tokens(*values: str) -> list[str]:
    result: list[str] = []
    for value in values:
        for token in re.split(r"[^A-Za-z0-9_.+-]+", str(value or "").lower()):
            normalized = _normalize_language(token)
            if normalized:
                result.append(normalized)
    return result


def infer_project_language(*, kind: str = "", command: str = "", target: str = "", cwd: str | Path | None = None) -> str:
    """Infer the best project-language key for a progress panel."""

    candidates = _tokens(kind, command, target)
    for preferred in ("android", "kotlin", "python", "rust", "go", "node", "typescript", "javascript", "java", "dotnet", "shell"):
        if preferred in candidates:
            return preferred

    if cwd:
        root = Path(cwd).expanduser()
        try:
            if (root / "AndroidManifest.xml").is_file() or (root / "app" / "src" / "main" / "AndroidManifest.xml").is_file():
                return "android"
            gradle_texts: list[str] = []
            gradle_found = False
            for marker in ("settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts", "app/build.gradle", "app/build.gradle.kts"):
                path = root / marker
                if path.is_file():
                    gradle_found = True
                    try:
                        gradle_texts.append(path.read_text(encoding="utf-8", errors="ignore")[:32768].lower())
                    except OSError:
                        pass
            if gradle_found:
                text = "\n".join(gradle_texts)
                if "com.android." in text or "android {" in text:
                    return "android"
                if "kotlin" in text:
                    return "kotlin"
                return "java"
            if (root / "pyproject.toml").is_file() or (root / "setup.py").is_file() or (root / "tox.ini").is_file():
                return "python"
            if (root / "Cargo.toml").is_file():
                return "rust"
            if (root / "go.mod").is_file():
                return "go"
            if (root / "package.json").is_file():
                return "node"
            if (root / "Dockerfile").is_file() or (root / "docker-compose.yml").is_file() or (root / "compose.yml").is_file():
                return "docker"
        except (OSError, RuntimeError, ValueError):
            pass
    return "generic"


def activity_icon(
    *,
    kind: str = "",
    command: str = "",
    target: str = "",
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    fallback: str = FALLBACK_ACTIVITY_ICON,
) -> str:
    """Return the running-state icon for a progress panel."""

    if not nerd_font_enabled(env):
        return fallback
    language = infer_project_language(kind=kind, command=command, target=target, cwd=cwd)
    icon = NERD_FONT_ICONS.get(language, fallback)
    return icon if icon.strip() else fallback
