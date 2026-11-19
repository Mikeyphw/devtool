"""Current native executable-overlay deletion contract: positive and refusal cases."""
from pathlib import Path
import copy
import hashlib
import unittest

import devtool.executable_overlay as native
from devtool.executable_overlay import (
    PROFILE, PROFILE_V1, _require_v2_source_contract,
    canonical_bootstrap_source,
)

H = "a" * 64
K = "b" * 64
COMMIT = "c" * 40
TREE = "d" * 40


def manifest():
    return {
        "schema_version": 2, "type": "overlay",
        "base_contract_mode": "exact-tree", "base_commit": COMMIT, "base_tree": TREE,
        "repository": {
            "remote": "https://github.com/Example/example.git",
            "branch": "feature/example", "enforce_remote": True,
            "enforce_branch": True,
        },
        "inventory": {"format": 1, "entries": [
            {"path": "a.txt", "action": "add", "sha256": H},
            {"path": "b.txt", "action": "modify", "source_sha256": K, "sha256": H},
            {"path": "c.txt", "action": "delete", "source_sha256": K},
        ]},
        "touched_paths": ["a.txt", "b.txt", "c.txt"],
        "source_sha256": {"a.txt": None, "b.txt": K, "c.txt": K},
        "expected_post_sha256": {"a.txt": H, "b.txt": H, "c.txt": None},
    }


class ExecutableOverlayDeletionContract(unittest.TestCase):
    def test_current_worktree_is_imported_not_external_runtime(self):
        target = Path(__file__).resolve().parents[1] / "devtool/executable_overlay.py"
        self.assertEqual(Path(native.__file__).resolve(), target.resolve())

    def test_add_modify_delete_consistent_native_exact_tree(self):
        _require_v2_source_contract(manifest())

    def test_missing_deleted_postimage_key_refused(self):
        x = manifest()
        del x["expected_post_sha256"]["c.txt"]
        with self.assertRaisesRegex(ValueError, "source/post hash inventory"):
            _require_v2_source_contract(x)

    def test_deleted_path_must_have_null_postimage(self):
        x = manifest()
        x["expected_post_sha256"]["c.txt"] = H
        with self.assertRaisesRegex(ValueError, "source/post hash inventory"):
            _require_v2_source_contract(x)

    def test_invalid_delete_preimage_digest_refused(self):
        x = manifest()
        x["inventory"]["entries"][2]["source_sha256"] = "unsafe"
        with self.assertRaisesRegex(ValueError, "source blob hash"):
            _require_v2_source_contract(x)

    def test_missing_delete_preimage_in_binding_refused(self):
        x = manifest()
        del x["source_sha256"]["c.txt"]
        with self.assertRaisesRegex(ValueError, "source/post hash inventory"):
            _require_v2_source_contract(x)

    def test_extraneous_postimage_refused(self):
        x = manifest()
        x["expected_post_sha256"]["extra.txt"] = None
        with self.assertRaisesRegex(ValueError, "source/post hash inventory"):
            _require_v2_source_contract(x)

    def test_canonical_bootstrap_remains_versioned(self):
        self.assertEqual(PROFILE, "devtool.executable-overlay.v2")
        self.assertTrue(canonical_bootstrap_source(PROFILE_V1))
        self.assertNotEqual(canonical_bootstrap_source(PROFILE_V1), canonical_bootstrap_source(PROFILE))


if __name__ == "__main__":
    unittest.main()
