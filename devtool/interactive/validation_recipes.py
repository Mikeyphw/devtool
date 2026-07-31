from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import tomllib
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class ValidationRecipe:
    name: str
    label: str
    description: str
    argv: tuple[str, ...]
    prefix_argv: tuple[str, ...] = ()
    builtin: bool = False
    scope: str = "global"
    metadata: dict[str, Any] = field(default_factory=dict)


BUILTIN_RECIPES: tuple[ValidationRecipe, ...] = (
    ValidationRecipe("smart", "Smart validation", "Run the normal restore, build, and test validation pipeline.", ("validate",), (), True),
    ValidationRecipe("collect-all", "Collect all failures", "Continue independent validation checks and report every failure.", ("validate-all",), (), True),
    ValidationRecipe("android-battery-safe", "Android battery-safe", "Use one worker, two CPUs, no Gradle daemon, and device safety guards.", ("validate", "--sequential", "--resource-profile", "android-low-power", "--stop-below-battery", "30", "--require-charging", "--stop-above-temperature", "44"), (), True),
    ValidationRecipe("next-checkpoint", "Next checkpoint", "Run the next pending phase in the active durable session.", ("validate", "continue", "--scope", "next"), (), True),
    ValidationRecipe("through-package", "Through package", "Continue the active session through the package checkpoint.", ("validate", "continue", "--scope", "through", "--through", "package"), (), True),
    ValidationRecipe("complete-session", "Complete remaining session", "Run every remaining validation phase.", ("validate", "continue", "--scope", "remaining"), (), True),
    ValidationRecipe("changed-tests", "Changed tests only", "Validate tests related to files changed from HEAD.", ("validate", "--skip-restore", "--skip-build", "--changed"), (), True),
    ValidationRecipe("failed-tests", "Retry failed tests", "Validate only tests recorded as failed by the previous structured run.", ("validate", "--skip-restore", "--skip-build", "--failed"), (), True),
    ValidationRecipe("safe-retry", "Safe retry failed phase", "Retry the failed durable phase using the Android low-power resource profile.", ("validate", "retry", "--resource-profile", "android-low-power"), (), True),
    ValidationRecipe("watch", "Watch current target", "Rerun ordinary validation after repository changes.", ("validate", "--watch"), (), True),
    ValidationRecipe("target-matrix", "Validate target matrix", "Validate a comma-separated set of named targets.", ("validate", "--matrix", "{matrix}", "--matrix-mode", "target"), (), True),
    ValidationRecipe("overlay-integrity", "Overlay integrity check", "Validate an overlay archive without applying it.", ("validate", "overlay", "{artifact}"), (), True),
    ValidationRecipe("deferred-overlay", "Deferred overlay validation", "Apply an overlay and create a durable validation session without running a phase.", ("apply-overlay", "{artifact}", "--defer-validate"), (), True),
)


def global_recipe_path() -> Path:
    return Path.home() / ".config" / "devtool" / "validation-recipes.toml"


def repo_recipe_path(repo_root: Path) -> Path:
    return repo_root / ".devtool" / "validation-recipes.toml"


def load_recipes(repo_root: Path) -> list[ValidationRecipe]:
    merged: dict[str, ValidationRecipe] = {item.name: item for item in BUILTIN_RECIPES}
    for scope, path in (("global", global_recipe_path()), ("repository", repo_recipe_path(repo_root))):
        for recipe in _read_file(path, scope=scope):
            merged[recipe.name] = recipe
    return list(merged.values())


def save_recipe(
    repo_root: Path,
    *,
    name: str,
    label: str,
    description: str,
    argv: Iterable[str],
    prefix_argv: Iterable[str] = (),
    repository: bool,
) -> Path:
    path = repo_recipe_path(repo_root) if repository else global_recipe_path()
    current = {item.name: item for item in _read_file(path, scope="repository" if repository else "global")}
    slug = _slug(name or label)
    current[slug] = ValidationRecipe(
        slug, label.strip() or slug, description.strip(), tuple(str(item) for item in argv),
        tuple(str(item) for item in prefix_argv), False, "repository" if repository else "global"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_file(current.values()), encoding="utf-8")
    return path


def delete_recipe(repo_root: Path, name: str) -> bool:
    deleted = False
    for path, scope in ((repo_recipe_path(repo_root), "repository"), (global_recipe_path(), "global")):
        recipes = {item.name: item for item in _read_file(path, scope=scope)}
        if name not in recipes:
            continue
        del recipes[name]
        path.write_text(_render_file(recipes.values()), encoding="utf-8")
        deleted = True
    return deleted


def expand_recipe(recipe: ValidationRecipe, *, artifact: str = "", matrix: str = "") -> list[str]:
    output: list[str] = []
    for item in recipe.argv:
        if item == "{artifact}":
            if not artifact:
                raise ValueError(f"Recipe {recipe.label!r} requires an artifact path.")
            output.append(artifact)
        elif item == "{matrix}":
            if not matrix:
                raise ValueError(f"Recipe {recipe.label!r} requires matrix values.")
            output.append(matrix)
        else:
            output.append(item)
    return output


def _read_file(path: Path, *, scope: str) -> list[ValidationRecipe]:
    if not path.is_file():
        return []
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    raw = payload.get("recipes", {})
    if not isinstance(raw, dict):
        return []
    output: list[ValidationRecipe] = []
    for name, value in raw.items():
        if not isinstance(value, dict):
            continue
        argv = value.get("argv", [])
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            continue
        prefix_argv = value.get("prefix_argv", [])
        if not isinstance(prefix_argv, list) or not all(isinstance(item, str) for item in prefix_argv):
            prefix_argv = []
        output.append(ValidationRecipe(
            name=str(name),
            label=str(value.get("label") or name),
            description=str(value.get("description") or ""),
            argv=tuple(argv),
            prefix_argv=tuple(prefix_argv),
            builtin=False,
            scope=scope,
        ))
    return output


def _render_file(recipes: Iterable[ValidationRecipe]) -> str:
    lines = ["# Devtool validation recipes", "# Generated by `devtool menu validation`.", ""]
    for recipe in sorted(recipes, key=lambda item: item.name):
        lines.append(f"[recipes.{_toml_key(recipe.name)}]")
        lines.append(f"label = {_toml_string(recipe.label)}")
        lines.append(f"description = {_toml_string(recipe.description)}")
        if recipe.prefix_argv:
            lines.append("prefix_argv = [" + ", ".join(_toml_string(item) for item in recipe.prefix_argv) + "]")
        lines.append("argv = [" + ", ".join(_toml_string(item) for item in recipe.argv) + "]")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return text or "custom"


def _toml_key(value: str) -> str:
    return _toml_string(value)


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'
