"""SFDT-02 affected-target semantics: explicit ownership and fail-closed fallbacks."""
from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest

from devtool.affected import AffectedSelectionError, analyze_affected_targets
from devtool.config import load_project_config, resolve_config_dict


def config_for(root: Path, text: str):
    (root / ".devtool.toml").write_text(textwrap.dedent(text), encoding="utf-8")
    return load_project_config(str(root), None, allow_ambiguous_target=True)


class ExplicitOwnershipTests(unittest.TestCase):
    THREE = """
        [project]
        runner = "python"
        [targets.phone]
        runner = "python"
        root = "."
        affected_ownership = "inputs-only"
        inputs = ["app/**", "core/**", "build-logic/**"]
        [targets.tv]
        runner = "python"
        root = "."
        affected_ownership = "inputs-only"
        inputs = ["app-tv/**", "core/**", "build-logic/**"]
        [targets.desktop]
        runner = "python"
        root = "."
        affected_ownership = "inputs-only"
        inputs = ["desktopApp/**", "core/**", "build-logic/**"]
    """

    def test_phone_owned_source_is_not_all_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config_for(Path(tmp), self.THREE)
            result = analyze_affected_targets(cfg, changed_paths=["app/src/main.kt"])
            self.assertEqual(result.targets, ("phone",))
            self.assertEqual(result.direct_targets, ("phone",))
            self.assertEqual(result.uncertain_paths, ())
            self.assertTrue(any(x.kind == "target-input" for x in result.reasons["phone"]))
            self.assertFalse(any(x.kind == "target-root" for x in result.reasons["phone"]))
            self.assertEqual(resolve_config_dict(cfg)["targets"]["phone"]["affected_ownership"], "inputs-only")

    def test_shared_sources_and_ambiguous_changes_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config_for(Path(tmp), self.THREE)
            shared = analyze_affected_targets(cfg, changed_paths=["core/model/Record.kt"])
            self.assertEqual(shared.targets, ("phone", "tv", "desktop"))
            unknown = analyze_affected_targets(cfg, changed_paths=["UNMAPPED.md"])
            self.assertEqual(unknown.targets, ("phone", "tv", "desktop"))
            self.assertEqual(unknown.uncertain_paths, ("UNMAPPED.md",))
            for name in unknown.targets:
                self.assertTrue(any(x.kind == "uncertain-workspace" for x in unknown.reasons[name]))

    def test_default_mode_retains_legacy_root_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config_for(Path(tmp), """
                [project]
                runner = "python"
                [targets.legacy]
                runner = "python"
                root = "."
                inputs = ["app/**"]
            """)
            result = analyze_affected_targets(cfg, changed_paths=["README.md"])
            self.assertEqual(result.targets, ("legacy",))
            self.assertEqual(result.uncertain_paths, ())
            self.assertTrue(any(x.kind == "target-root" for x in result.reasons["legacy"]))

    def test_target_local_job_inputs_contribute_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config_for(Path(tmp), """
                [project]
                runner = "python"
                [targets.phone]
                runner = "python"
                root = "."
                affected_ownership = "inputs-only"
                inputs = ["app/**"]
                [targets.phone.jobs.audit]
                runner = "command"
                command = ["python3", "-c", "print('safe')"]
                inputs = ["scripts/**"]
                metadata = { read_only = true }
                [targets.phone.workflows]
                build = ["job:audit"]
                [targets.tv]
                runner = "python"
                root = "."
                affected_ownership = "inputs-only"
                inputs = ["app-tv/**"]
            """)
            result = analyze_affected_targets(cfg, changed_paths=["scripts/source_contract.py"])
            self.assertEqual(result.targets, ("phone",))
            self.assertTrue(any(x.kind == "job-input" and x.source == "audit" for x in result.reasons["phone"]))

    def test_global_job_default_root_remains_repository_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config_for(Path(tmp), """
                [project]
                runner = "python"
                [jobs.contract]
                runner = "command"
                command = ["python3", "-c", "print('safe')"]
                inputs = ["scripts/**"]
                [targets.phone]
                runner = "python"
                root = "app"
                affected_ownership = "inputs-only"
                inputs = ["src/**"]
                [targets.phone.workflows]
                build = ["job:contract"]
            """)
            # A global job without an explicit root owns repo-root-relative
            # paths, not the consuming target root (legacy MP07 semantics).
            result = analyze_affected_targets(cfg, changed_paths=["scripts/check.py"])
            self.assertEqual(result.targets, ("phone",))
            self.assertEqual(result.uncertain_paths, ())
            self.assertTrue(any(x.kind == "job-input" for x in result.reasons["phone"]))

    def test_dependency_propagation_survives_candidate_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config_for(Path(tmp), """
                [project]
                runner = "python"
                [targets.shared]
                runner = "python"
                root = "."
                affected_ownership = "inputs-only"
                inputs = ["protocol/**"]
                [targets.phone]
                runner = "python"
                root = "."
                affected_ownership = "inputs-only"
                inputs = ["app/**"]
                [targets.phone.workflows]
                build = ["target:shared#build", "runner:python#build"]
            """)
            result = analyze_affected_targets(cfg, changed_paths=["protocol/schema.json"], candidate_targets=("phone",))
            self.assertEqual(result.targets, ("phone",))
            self.assertEqual(result.direct_targets, ())
            self.assertTrue(any(x.kind == "dependency" and x.source == "shared" for x in result.reasons["phone"]))

    def test_invalid_or_empty_modes_fail_config_loading(self):
        bad = [
            'affected_ownership="unknown"\ninputs=["app/**"]\n',
            'affected_ownership="inputs-only"\n',
            'affected_ownership=123\ninputs=["app/**"]\n',
        ]
        for value in bad:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                content = '[project]\nrunner="python"\n[targets.phone]\nrunner="python"\n' + value
                with self.assertRaises(SystemExit):
                    config_for(Path(tmp), content)

    def test_programmatic_invalid_mode_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config_for(Path(tmp), self.THREE)
            cfg.targets["phone"].affected_ownership = "unsafe"
            with self.assertRaises(AffectedSelectionError):
                analyze_affected_targets(cfg, changed_paths=["app/source.kt"])


if __name__ == "__main__":
    unittest.main()
