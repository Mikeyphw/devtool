from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java"
PACKAGE = JAVA / "io" / "github" / "mikeceead" / "devtoolhud"


@pytest.fixture(scope="module")
def importer_classpath(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("javac") is None or shutil.which("java") is None:
        pytest.skip("JDK is not installed")
    root = tmp_path_factory.mktemp("command-importer-java")
    sources = root / "io" / "github" / "mikeceead" / "devtoolhud"
    sources.mkdir(parents=True)
    for name in ("DevtoolRunConfig.java", "DevtoolCommandBuilder.java", "DevtoolCommandImporter.java"):
        shutil.copy2(PACKAGE / name, sources / name)
    (sources / "ImporterHarness.java").write_text(
        textwrap.dedent(
            """
            package io.github.mikeceead.devtoolhud;

            import java.nio.file.Files;
            import java.nio.file.Path;

            public final class ImporterHarness {
                public static void main(String[] args) throws Exception {
                    String command = Files.readString(Path.of(args[0]));
                    DevtoolCommandImporter.ImportResult result = DevtoolCommandImporter.parse(command);
                    DevtoolRunConfig c = result.config;
                    print("operation", c.operation);
                    print("repo", c.repo);
                    print("target", c.target);
                    print("artifact", c.artifact);
                    print("commitMessage", c.commitMessage);
                    print("environment", c.environmentAssignments);
                    print("extraGlobal", c.extraGlobalArguments);
                    print("extraOperation", c.extraOperationArguments);
                    print("chroot", Boolean.toString(c.useChroot));
                    print("chrootName", c.chrootName);
                    print("copy", Boolean.toString(c.copy));
                    print("copyViaTermux", Boolean.toString(c.copyViaTermux));
                    print("autoHud", Boolean.toString(c.autoHud));
                    print("validate", Boolean.toString(c.validate));
                    print("commit", Boolean.toString(c.commit));
                    print("yes", Boolean.toString(c.assumeYes));
                    print("workdir", result.workdir);
                    print("notice", result.notice);
                    print("rebuilt", DevtoolCommandBuilder.build(c));
                }

                private static void print(String key, String value) {
                    System.out.println(key + "=" + value.replace("\\n", "\\\\n"));
                }
            }
            """
        ),
        encoding="utf-8",
    )
    output = root / "classes"
    output.mkdir()
    subprocess.run(
        ["javac", "-d", str(output), *map(str, sources.glob("*.java"))],
        check=True,
        text=True,
        capture_output=True,
    )
    return output


def run_import(classpath: Path, tmp_path: Path, command: str) -> dict[str, str]:
    command_file = tmp_path / "command.sh"
    command_file.write_text(command, encoding="utf-8")
    completed = subprocess.run(
        ["java", "-cp", str(classpath), "io.github.mikeceead.devtoolhud.ImporterHarness", str(command_file)],
        check=True,
        text=True,
        capture_output=True,
    )
    return dict(line.split("=", 1) for line in completed.stdout.splitlines())


def test_imports_multiline_devtool_command_into_visual_fields(importer_classpath: Path, tmp_path: Path) -> None:
    command = r'''cd "$HOME/Code/sims3tools" && \
DEVTOOL_HUD_SINGLETON=kill devtool \
  --copy \
  --auto-hud \
  --hud-mode android-bridge \
  --yes \
  -r "$HOME/Code/sims3tools" \
  --target s3atelier_avalonia_S3Atelier_Modern \
  apply-overlay "$HOME/Downloads/s3atelier_object_modl_mlod_workbench_overlay.zip" \
  --validate \
  --commit "Add Object MODL MLOD workbench"'''
    values = run_import(importer_classpath, tmp_path, command)
    assert values["operation"] == "apply-overlay"
    assert values["repo"] == "$HOME/Code/sims3tools"
    assert values["workdir"] == "$HOME/Code/sims3tools"
    assert values["target"] == "s3atelier_avalonia_S3Atelier_Modern"
    assert values["artifact"].endswith("s3atelier_object_modl_mlod_workbench_overlay.zip")
    assert values["commitMessage"] == "Add Object MODL MLOD workbench"
    assert values["environment"] == "DEVTOOL_HUD_SINGLETON=kill"
    assert values["copy"] == values["autoHud"] == values["validate"] == values["commit"] == values["yes"] == "true"
    assert "--hud-mode android-bridge" in values["extraGlobal"]
    assert "--hud-mode android-bridge" in values["rebuilt"]


def test_import_preserves_desktop_hud_flags_and_unknown_arguments(importer_classpath: Path, tmp_path: Path) -> None:
    command = """FOO='two words' devtool-androidos --chroot androidos --copy --copy-via termux \\
--auto-hud --hud-mode desktop-window -r /home/mike/Code/sims3tools --target s3mo_s3mo_s3mo \\
apply-overlays /home/termux/Downloads/bundle.zip --validate --commit 'Apply bundle' --future-flag value"""
    values = run_import(importer_classpath, tmp_path, command)
    assert values["chroot"] == "true"
    assert values["chrootName"] == "androidos"
    assert values["copyViaTermux"] == "true"
    assert values["environment"] == "FOO='two words'"
    assert "--hud-mode desktop-window" in values["extraGlobal"]
    assert values["extraOperation"] == "--future-flag value"
    assert "converted to android-bridge" not in values["notice"]
    assert "--hud-mode desktop-window" in values["rebuilt"]
    assert "--hud-mode android-bridge" not in values["rebuilt"]
    assert "--future-flag value" in values["rebuilt"]


def test_import_accepts_unknown_subcommand_as_custom(importer_classpath: Path, tmp_path: Path) -> None:
    values = run_import(
        importer_classpath,
        tmp_path,
        "devtool -r ~/Code/project experimental-command --alpha 'two words'",
    )
    assert values["operation"] == "custom"
    assert "experimental-command" in values["rebuilt"]
    assert "'two words'" in values["rebuilt"]


def test_import_rejects_plain_prose_mentions_of_devtool(importer_classpath: Path, tmp_path: Path) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        run_import(importer_classpath, tmp_path, "This note mentions devtool but is not a command.")


def test_import_accepts_terminal_prompt_prefixes(importer_classpath: Path, tmp_path: Path) -> None:
    values = run_import(importer_classpath, tmp_path, "❯ devtool status")
    assert values["operation"] == "status"
    assert values["notice"] == "Command imported into the visual builder"


@pytest.mark.requires_tool("javac")
@pytest.mark.requires_tool("java")
def test_termux_workdir_normalizes_shell_home_forms(tmp_path: Path) -> None:
    if shutil.which("javac") is None or shutil.which("java") is None:
        pytest.skip("JDK is not installed")
    root = tmp_path / "termux-paths"
    sources = root / "io" / "github" / "mikeceead" / "devtoolhud"
    sources.mkdir(parents=True)
    shutil.copy2(PACKAGE / "TermuxPaths.java", sources / "TermuxPaths.java")
    annotation = root / "android" / "annotation" / "SuppressLint.java"
    annotation.parent.mkdir(parents=True)
    annotation.write_text(
        "package android.annotation; public @interface SuppressLint { String[] value(); }\n",
        encoding="utf-8",
    )
    (sources / "PathHarness.java").write_text(
        textwrap.dedent(
            """
            package io.github.mikeceead.devtoolhud;

            public final class PathHarness {
                public static void main(String[] args) {
                    for (String arg : args) {
                        System.out.println(TermuxPaths.normalizeWorkdir(arg));
                    }
                }
            }
            """
        ),
        encoding="utf-8",
    )
    output = root / "classes"
    output.mkdir()
    subprocess.run(
        ["javac", "-d", str(output), *map(str, root.rglob("*.java"))],
        check=True,
        text=True,
        capture_output=True,
    )
    values = [
        "$HOME/Code/sims3tools",
        "/$HOME/Code/sims3tools",
        "${HOME}/Code/sims3tools",
        "~/Code/sims3tools",
        "Code/sims3tools",
        "/data/data/com.termux/files/home/Code/sims3tools",
    ]
    completed = subprocess.run(
        ["java", "-cp", str(output), "io.github.mikeceead.devtoolhud.PathHarness", *values],
        check=True,
        text=True,
        capture_output=True,
    )
    expected = "/data/data/com.termux/files/home/Code/sims3tools"
    assert completed.stdout.splitlines() == [expected] * len(values)
