from __future__ import annotations

import io
import json
import os
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from devtool.cli import build_parser, main
from devtool.compatibility import COMPATIBILITY_ENTRIES, compatibility_report
from devtool.config import minimal_global_config_template
from devtool.config_migration import analyze_config_migration_text, migrate_config_text
from devtool.detect import detect_repo, render_detect_text

ROOT = Path(__file__).resolve().parents[1]


class Fs01CompatibilityAuditTests(unittest.TestCase):
    def test_registry_covers_every_intentional_legacy_boundary(self) -> None:
        ids = {entry.id for entry in COMPATIBILITY_ENTRIES}
        required = {
            "config.project.kind", "config.target.kind", "config.target.type",
            "config.project.default_target", "config.ui.confirm_destructive",
            "config.ui.confirm_cancel", "cli.project_type", "cli.logs_print", "hud.gtk_window",
            "python.DotnetTarget", "machine.flat_v1", "ipc.cancel",
            "tasker.flat_fields", "android.bridge.v1",
            "artifact.manifest.repository_aliases", "artifact.manifest.commit_aliases",
            "artifact.manifest.files", "artifact.bundle.implicit",
            "artifact.bundle.string_entries", "artifact.history.v1_v2",
            "validation.session.v1_v5", "state.transaction.active_singleton",
            "workflow.ordered_lists",
        }
        self.assertEqual(required - ids, set())
        self.assertEqual(len(ids), len(COMPATIBILITY_ENTRIES))

    def test_runner_is_canonical_cli_flag_and_project_type_remains_alias(self) -> None:
        parser = build_parser()
        canonical = parser.parse_args(["--runner", "python", "status"])
        legacy = parser.parse_args(["--project-type", "python", "status"])
        self.assertEqual(canonical.project_type, "python")
        self.assertEqual(legacy.project_type, "python")

    def test_migration_canonicalizes_runner_workspace_and_ui_shims(self) -> None:
        source = '''[project]\nkind = "python"\ndefault_target = "api"\n\n[workspace]\n\n[targets.api]\ntype = "python"\nroot = "api"\n\n[ui]\nconfirm_destructive = false\nconfirm_cancel = true\n'''
        migrated, findings = migrate_config_text(source)
        ids = {item.compatibility_id for item in findings}
        self.assertIn("config.project.default_target", ids)
        self.assertIn("config.ui.confirm_destructive", ids)
        self.assertIn("config.ui.confirm_cancel", ids)
        parsed = tomllib.loads(migrated)
        self.assertEqual(parsed["project"]["runner"], "python")
        self.assertNotIn("kind", parsed["project"])
        self.assertEqual(parsed["workspace"]["default_target"], "api")
        self.assertNotIn("default_target", parsed["project"])
        self.assertEqual(parsed["targets"]["api"]["runner"], "python")
        self.assertEqual(parsed["ui"]["confirmations"]["cancel"], "always")
        self.assertEqual(parsed["ui"]["confirmations"]["rollback"], "never")
        self.assertEqual(parsed["ui"]["confirmations"]["delete_artifact"], "never")
        self.assertEqual(parsed["ui"]["confirmations"]["snapshot_overwrite"], "never")
        self.assertEqual(analyze_config_migration_text(migrated), [])

    def test_conflicting_default_target_is_reported_unsafe(self) -> None:
        source = '''[project]\ndefault_target = "api"\n\n[workspace]\ndefault_target = "web"\n'''
        findings = analyze_config_migration_text(source)
        self.assertEqual(len(findings), 1)
        self.assertFalse(findings[0].safe)
        self.assertEqual(findings[0].compatibility_id, "config.project.default_target")

    def test_config_migrate_global_rewrites_legacy_ui_preferences_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('[ui]\nconfirm_cancel = true\nconfirm_destructive = false\n', encoding="utf-8")
            stdout = io.StringIO()
            with patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(path)}, clear=False), redirect_stdout(stdout):
                rc = main(["config", "migrate", "--global", "--write", "--json"])
            self.assertEqual(rc, 0)
            self.assertTrue(path.with_name("config.toml.bak").exists())
            parsed = tomllib.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("confirm_cancel", parsed["ui"])
            self.assertEqual(parsed["ui"]["confirmations"]["cancel"], "always")
            self.assertFalse(json.loads(stdout.getvalue())["data"]["needs_migration"])

    def test_compatibility_json_marks_active_config_migrations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text('[project]\nname="demo"\nversion="0.1.0"\n', encoding="utf-8")
            (root / ".devtool.toml").write_text('[project]\nkind="python"\n', encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["--repo", str(root), "compatibility", "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(stdout.getvalue())["data"]
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["summary"]["active_config_migrations"], 1)
            self.assertEqual(payload["config_migration"]["findings"][0]["compatibility_id"], "config.project.kind")

    def test_compatibility_strict_fails_only_when_migration_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".devtool.toml"
            path.write_text('[project]\nrunner="python"\nplatform="server"\n', encoding="utf-8")
            self.assertEqual(compatibility_report(path)["summary"]["active_config_migrations"], 0)
            path.write_text('[project]\nkind="python"\n', encoding="utf-8")
            self.assertEqual(compatibility_report(path)["summary"]["active_config_migrations"], 1)

    def test_detect_publishes_runner_fields_and_keeps_kind_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text('[project]\nname="demo"\nversion="0.1.0"\n', encoding="utf-8")
            payload = detect_repo(root)
            self.assertEqual(payload["runner"], "python")
            self.assertEqual(payload["kind"], "python")
            self.assertEqual(payload["runner_candidates"], payload["kind_candidates"])
            text = render_detect_text(payload)
            self.assertIn("runner: python", text)
            self.assertNotIn("\nkind: python", text)


    def test_generated_internal_commands_use_runner_while_importers_keep_project_type_alias(self) -> None:
        desktop = (ROOT / "devtool" / "desktop" / "command_model.py").read_text(encoding="utf-8")
        wizard = (ROOT / "devtool" / "interactive" / "validation_wizard.py").read_text(encoding="utf-8")
        ssh = (ROOT / "devtool" / "ssh_tmux_runner.py").read_text(encoding="utf-8")
        chroot = (ROOT / "devtool" / "termux_chroot_runner.py").read_text(encoding="utf-8")
        android_builder = (ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud" / "DevtoolCommandBuilder.java").read_text(encoding="utf-8")
        android_importer = (ROOT / "android" / "devtool-live-hud" / "app" / "src" / "main" / "java" / "io" / "github" / "mikeceead" / "devtoolhud" / "DevtoolCommandImporter.java").read_text(encoding="utf-8")
        self.assertIn('["--runner", project_type]', desktop)
        self.assertIn('_pair(argv, "--runner", self.project_type)', wizard)
        self.assertIn('"--runner", "--project-type"', ssh)
        self.assertIn('"--runner", "--project-type"', chroot)
        self.assertIn('parts.add("--runner")', android_builder)
        self.assertNotIn('parts.add("--project-type")', android_builder)
        self.assertIn('case "--runner":', android_importer)
        self.assertIn('case "--project-type":', android_importer)

    def test_config_migrate_can_report_unsafe_raw_toml_without_loading_project_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".devtool.toml"
            original = '[project]\ndefault_target = "api"\n\n[workspace]\ndefault_target = "web"\n'
            path.write_text(original, encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["--repo", str(root), "config", "migrate", "--check", "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(stdout.getvalue())["data"]
            self.assertTrue(payload["needs_migration"])
            self.assertFalse(payload["safe_to_write"])
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                write_rc = main(["--repo", str(root), "config", "migrate", "--write"])
            self.assertNotEqual(write_rc, 0)
            self.assertIn("manual reconciliation", stderr.getvalue())
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_compatibility_strict_cli_is_a_release_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".devtool.toml"
            path.write_text('[project]\nrunner="python"\nplatform="server"\n', encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--repo", str(root), "compatibility", "--strict"]), 0)
            path.write_text('[project]\nkind="python"\n', encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--repo", str(root), "compatibility", "--strict"]), 1)

    def test_persisted_format_compatibility_entries_match_runtime_readers(self) -> None:
        artifacts = (ROOT / "devtool" / "artifacts.py").read_text(encoding="utf-8")
        sessions = (ROOT / "devtool" / "validation_sessions.py").read_text(encoding="utf-8")
        transactions = (ROOT / "devtool" / "transactions.py").read_text(encoding="utf-8")
        self.assertIn('manifest.get("repo") or manifest.get("repo_name") or manifest.get("expected_repo")', artifacts)
        self.assertIn('manifest.get("commit_message")', artifacts)
        self.assertIn('Backward-compatible string entries are accepted', artifacts)
        self.assertIn('Implicit legacy wrapper bundle detected by devtool.', artifacts)
        self.assertIn('Import legacy/index-only entries for backward compatibility.', artifacts)
        self.assertIn('SESSION_SCHEMA_VERSION = 7', sessions)
        self.assertIn('version not in {1, 2, 3, 4, 5, 6}', sessions)
        self.assertIn('Legacy single-transaction marker retained for read/recovery compatibility.', transactions)

    def test_generated_global_template_does_not_emit_legacy_confirmation_booleans(self) -> None:
        text = minimal_global_config_template()
        self.assertNotIn("confirm_destructive", text)
        self.assertNotIn("confirm_cancel", text)
        self.assertIn("[ui.confirmations]", text)

    def test_release_repository_config_is_already_canonical(self) -> None:
        text = (ROOT / ".devtool.toml").read_text(encoding="utf-8")
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["project"]["runner"], "python")
        self.assertEqual(parsed["project"]["platform"], "server")
        self.assertNotIn("kind", parsed["project"])
        self.assertNotIn("default_target", parsed["project"])
        self.assertEqual(parsed["workspace"]["default_target"], "devtool")
        for target in parsed["targets"].values():
            self.assertIn("runner", target)
            self.assertIn("platform", target)
            self.assertNotIn("kind", target)
            self.assertNotIn("type", target)
        self.assertEqual(analyze_config_migration_text(text), [])


class Fs02DocumentationExamplesTests(unittest.TestCase):
    def test_canonical_examples_are_valid_toml_and_use_runner_platform(self) -> None:
        names = [
            "python-service.devtool.toml",
            "polyglot-workspace.devtool.toml",
            "ui-profile.devtool.toml",
            "release-validation.devtool.toml",
        ]
        for name in names:
            with self.subTest(name=name):
                text = (ROOT / "examples" / name).read_text(encoding="utf-8")
                parsed = tomllib.loads(text)
                project = parsed.get("project", {})
                if project:
                    self.assertIn("runner", project)
                    self.assertIn("platform", project)
                    self.assertNotIn("kind", project)

    def test_migration_guide_and_compatibility_doc_cover_all_config_migration_ids(self) -> None:
        combined = (
            (ROOT / "docs" / "MIGRATION_1.1.md").read_text(encoding="utf-8")
            + (ROOT / "docs" / "BACKWARD_COMPATIBILITY.md").read_text(encoding="utf-8")
        )
        for entry in COMPATIBILITY_ENTRIES:
            if entry.category == "config":
                with self.subTest(entry=entry.id):
                    self.assertIn(entry.legacy, combined)
                    self.assertIn(entry.canonical, combined)

    def test_release_docs_link_canonical_examples_and_compatibility_contract(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("docs/EXAMPLES.md", readme)
        self.assertIn("docs/BACKWARD_COMPATIBILITY.md", readme)
        self.assertIn("FS01 + FS02 backward compatibility", changelog)
        for name in (
            "python-service.devtool.toml",
            "polyglot-workspace.devtool.toml",
            "ui-profile.devtool.toml",
            "release-validation.devtool.toml",
        ):
            self.assertIn(name, (ROOT / "docs" / "EXAMPLES.md").read_text(encoding="utf-8"))

    def test_python_and_runner_docs_teach_canonical_identity(self) -> None:
        python_doc = (ROOT / "docs" / "PYTHON_PROJECTS.md").read_text(encoding="utf-8")
        runner_doc = (ROOT / "docs" / "PROJECT_RUNNERS.md").read_text(encoding="utf-8")
        self.assertIn('runner = "python"', python_doc)
        self.assertIn('platform = "server"', python_doc)
        self.assertNotIn('kind = "python"', python_doc)
        self.assertIn("ProjectConfig.runner", runner_doc)
        self.assertIn("ProjectConfig.kind", runner_doc)  # documented compatibility field


if __name__ == "__main__":
    unittest.main()
