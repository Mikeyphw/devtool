"""U01 canonical ownership mode, diagnostics and negative selection coverage."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from devtool.affected import analyze_affected_targets
from devtool.affected_ownership_diagnostics import diagnose
from devtool.config import load_wrapper_project_config


BASE = '''[project]
runner = "python"
[targets.phone]
runner = "python"
root = "."
inputs = ["phone/**"]
[targets.phone.metadata]
affected_ownership = "explicit"
[targets.tv]
runner = "python"
root = "."
affected_ownership = "inputs-only"
inputs = ["tv/**"]
'''


class OwnershipMigrationTests(unittest.TestCase):
    def test_legacy_is_diagnosed_not_silently_applied(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / '.devtool.toml').write_text(BASE, encoding='utf-8')
            report = diagnose(root)
            self.assertEqual(report['status'], 'NEEDS_MIGRATION')
            self.assertEqual(report['target_count'], 2)
            self.assertEqual(report['legacy_metadata_count'], 1)
            self.assertFalse(report['mutated'])
            self.assertEqual(report['effective_modes']['phone'], 'root-and-inputs')
            self.assertEqual(report['effective_modes']['tv'], 'inputs-only')
            codes = {(row['target'], row['code']) for row in report['findings']}
            self.assertIn(('phone', 'IGNORED_METADATA_AFFECTED_OWNERSHIP'), codes)
            self.assertIn(('phone', 'REPOSITORY_ROOT_WILDCARD'), codes)
            cfg = load_wrapper_project_config(str(root), str(root / '.devtool.toml'))
            old = analyze_affected_targets(cfg, changed_paths=['tv/ui.py'])
            self.assertIn('phone', old.direct_targets)

    def test_migrated_real_selection_and_uncertain_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            updated = BASE.replace('inputs = ["phone/**"]',
                'affected_ownership = "inputs-only"\ninputs = ["phone/**"]').replace(
                '[targets.phone.metadata]\naffected_ownership = "explicit"\n', '')
            (root / '.devtool.toml').write_text(updated, encoding='utf-8')
            report = diagnose(root)
            self.assertEqual(report['status'], 'PASS')
            cfg = load_wrapper_project_config(str(root), str(root / '.devtool.toml'))
            self.assertEqual(analyze_affected_targets(cfg, changed_paths=['phone/main.py']).direct_targets, ('phone',))
            self.assertEqual(analyze_affected_targets(cfg, changed_paths=['tv/ui.py']).direct_targets, ('tv',))
            unknown = analyze_affected_targets(cfg, changed_paths=['unowned/change.bin'])
            self.assertEqual(set(unknown.targets), {'phone', 'tv'})
            self.assertEqual(unknown.uncertain_paths, ('unowned/change.bin',))

    def test_diagnosis_is_read_only_and_outside_config_refused(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as other:
            root = Path(td)
            source = root / '.devtool.toml'
            source.write_text(BASE, encoding='utf-8')
            before = source.read_bytes()
            diagnose(root)
            self.assertEqual(source.read_bytes(), before)
            with self.assertRaises(ValueError):
                diagnose(root, config_path=Path(other) / '.devtool.toml')


if __name__ == '__main__':
    unittest.main()
