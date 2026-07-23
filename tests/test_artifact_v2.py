from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from devtool.artifacts import (
    create_overlay_bundle,
    list_archive_members,
    materialize_overlay_bundle_entries,
    read_artifact_manifest,
    validate_artifact,
    write_archive_entries,
)
from devtool.artifact_validation import run_artifact_validation_script
from devtool.cli import _apply_artifact_manifest_defaults, _normalize_command_aliases, build_parser, main
from devtool.config import load_project_config


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ArtifactV2Tests(unittest.TestCase):
    def test_manifest_supplies_target_validation_and_commit_with_cli_opt_outs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "overlay.zip"
            manifest = {
                "schema_version": 2,
                "type": "overlay",
                "target": "s3oc",
                "solution": "s3oc_modern/S3oc.Modern.sln",
                "apply": {"commit": {"enabled": True, "message": "Apply S3OC diagnostics"}},
                "validation": {"required": True, "restore": False, "build": True, "test": False},
            }
            write_archive_entries(archive, {".devtool-artifact.json": json.dumps(manifest).encode(), "file.txt": b"ok"})

            parser = build_parser()
            args = parser.parse_args(["apply-overlay", str(archive)])
            _normalize_command_aliases(args)
            args._devtool_cli_target_explicit = False
            args._devtool_cli_solution_explicit = False
            _apply_artifact_manifest_defaults(args)

            self.assertEqual(args.target, "s3oc")
            self.assertEqual(args.solution, "s3oc_modern/S3oc.Modern.sln")
            self.assertTrue(args.validate)
            self.assertTrue(args.skip_restore)
            self.assertFalse(args.skip_build)
            self.assertTrue(args.skip_test)
            self.assertEqual(args.commit, "Apply S3OC diagnostics")

            args = parser.parse_args(["--target", "override", "apply-overlay", str(archive), "--no-validate", "--no-commit"])
            _normalize_command_aliases(args)
            args._devtool_cli_target_explicit = True
            args._devtool_cli_solution_explicit = False
            _apply_artifact_manifest_defaults(args)
            self.assertEqual(args.target, "override")
            self.assertFalse(args.validate)
            self.assertEqual(args.commit, "")


    def test_overlay_can_create_first_solution_and_auto_register_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "empty-repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            solution_path = "src/NewApp.sln"
            solution = b"Microsoft Visual Studio Solution File, Format Version 12.00\n"
            manifest = {
                "schema_version": 2,
                "type": "overlay",
                "target": "new_app",
                "solution": solution_path,
                "inventory": {
                    "format": 1,
                    "entries": [
                        {"path": solution_path, "action": "add", "sha256": sha(solution), "size": len(solution)},
                    ],
                },
                "validation": {"required": False},
            }
            archive = root / "bootstrap.zip"
            write_archive_entries(archive, {".devtool-artifact.json": json.dumps(manifest).encode(), solution_path: solution})

            rc = main(["-r", str(repo), "--yes", "apply-overlay", str(archive), "--keep-artifact"])

            self.assertEqual(rc, 0)
            self.assertTrue((repo / solution_path).is_file())
            config_text = (repo / ".devtool.toml").read_text(encoding="utf-8")
            self.assertNotIn("default_target", config_text)
            self.assertIn("[targets.new_app]", config_text)
            self.assertIn(f'solution = "{solution_path}"', config_text)
            config = load_project_config(str(repo), None, target_name="new_app")
            self.assertEqual(config.kind, "dotnet")
            self.assertEqual(config.selected_target, "new_app")
            self.assertEqual(config.build_target, str((repo / solution_path).resolve()))

    def test_bundle_auto_registers_every_overlay_created_solution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "empty-repo"
            repo.mkdir()
            (repo / ".git").mkdir()

            def make_overlay(filename: str, solution_path: str) -> Path:
                data = b"Microsoft Visual Studio Solution File, Format Version 12.00\n"
                manifest = {
                    "schema_version": 2,
                    "type": "overlay",
                    "solution": solution_path,
                    "inventory": {
                        "format": 1,
                        "entries": [{"path": solution_path, "action": "add", "sha256": sha(data), "size": len(data)}],
                    },
                    "validation": {"required": False},
                }
                path = root / filename
                write_archive_entries(path, {".devtool-artifact.json": json.dumps(manifest).encode(), solution_path: data})
                return path

            first = make_overlay("core.zip", "src/Core.sln")
            second = make_overlay("ui.zip", "src/UI.sln")
            bundle = create_overlay_bundle(
                root / "bundle.zip",
                [first, second],
                name="Bootstrap bundle",
                targets=["core", "ui"],
            )

            rc = main(["-r", str(repo), "--yes", "apply-overlays", str(bundle), "--keep-artifact"])

            self.assertEqual(rc, 0)
            config_text = (repo / ".devtool.toml").read_text(encoding="utf-8")
            self.assertIn("[targets.core]", config_text)
            self.assertIn('solution = "src/Core.sln"', config_text)
            self.assertIn("[targets.ui]", config_text)
            self.assertIn('solution = "src/UI.sln"', config_text)
            self.assertEqual(load_project_config(str(repo), None, target_name="core").selected_target, "core")
            self.assertEqual(load_project_config(str(repo), None, target_name="ui").selected_target, "ui")

    def test_inventory_hashes_actions_and_deletes_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            old = b"old\n"
            new = b"new\n"
            deleted = b"remove me\n"
            (repo / "old.txt").write_bytes(old)
            (repo / "delete.txt").write_bytes(deleted)
            manifest = {
                "schema_version": 2,
                "type": "overlay",
                "repository": {"name": "repo"},
                "inventory": {
                    "format": 1,
                    "entries": [
                        {"path": "old.txt", "action": "modify", "source_sha256": sha(old), "sha256": sha(new), "size": len(new)},
                        {"path": "add.txt", "action": "add", "sha256": sha(b"added\n"), "size": 6},
                        {"path": "delete.txt", "action": "delete", "source_sha256": sha(deleted)},
                    ],
                },
                "apply": {"atomic": True, "conflict_policy": "fail", "path_policy": "repository-relative"},
            }
            archive = root / "overlay.zip"
            write_archive_entries(
                archive,
                {
                    ".devtool-artifact.json": json.dumps(manifest).encode(),
                    "old.txt": new,
                    "add.txt": b"added\n",
                },
            )

            validation = validate_artifact(repo, archive)
            self.assertTrue(validation.ok, validation.messages)
            rc = main(["-r", str(repo), "--yes", "apply-overlay", str(archive), "--keep-artifact"])
            self.assertEqual(rc, 0)
            self.assertEqual((repo / "old.txt").read_bytes(), new)
            self.assertEqual((repo / "add.txt").read_bytes(), b"added\n")
            self.assertFalse((repo / "delete.txt").exists())

    def test_inventory_rejects_bad_payload_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            manifest = {
                "schema_version": 2,
                "type": "overlay",
                "inventory": {"format": 1, "entries": [{"path": "file.txt", "action": "add", "sha256": "0" * 64, "size": 2}]},
            }
            archive = root / "bad.zip"
            write_archive_entries(archive, {".devtool-artifact.json": json.dumps(manifest).encode(), "file.txt": b"ok"})
            validation = validate_artifact(repo, archive)
            self.assertFalse(validation.ok)
            self.assertTrue(any("SHA-256 mismatch" in message for message in validation.messages))

    def test_validation_expected_accepts_common_outcome_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            manifest = {
                "schema_version": 2,
                "type": "overlay",
                "validation": {
                    "expected": {
                        "restore": "skipped",
                        "build": "success",
                        "tests": "failed",
                    }
                },
            }
            archive = root / "overlay.zip"
            write_archive_entries(archive, {".devtool-artifact.json": json.dumps(manifest).encode()})

            validation = validate_artifact(repo, archive)

        self.assertTrue(validation.ok, validation.messages)


    def test_ignore_checksum_preserves_other_inventory_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "existing.txt").write_bytes(b"unexpected base\n")
            manifest = {
                "schema_version": 2,
                "type": "overlay",
                "inventory": {
                    "format": 1,
                    "entries": [
                        {
                            "path": "existing.txt",
                            "action": "modify",
                            "source_sha256": "0" * 64,
                            "sha256": "1" * 64,
                            "size": 3,
                        }
                    ],
                },
            }
            archive = root / "checksum-mismatch.zip"
            write_archive_entries(
                archive,
                {".devtool-artifact.json": json.dumps(manifest).encode(), "existing.txt": b"new"},
            )

            strict = validate_artifact(repo, archive)
            self.assertFalse(strict.ok)
            ignored = validate_artifact(repo, archive, ignore_checksum=True)
            self.assertTrue(ignored.ok, ignored.messages)
            self.assertTrue(any("checksum verification skipped" in message for message in ignored.messages))

            manifest["inventory"]["entries"][0]["size"] = 99
            write_archive_entries(
                archive,
                {".devtool-artifact.json": json.dumps(manifest).encode(), "existing.txt": b"new"},
            )
            size_failure = validate_artifact(repo, archive, ignore_checksum=True)
            self.assertFalse(size_failure.ok)
            self.assertTrue(any("Payload size mismatch" in message for message in size_failure.messages))

    def test_ignore_checksum_propagates_through_overlay_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            payload = b"bundle payload\n"
            overlay = root / "nested.zip"
            manifest = {
                "schema_version": 2,
                "type": "overlay",
                "inventory": {
                    "format": 1,
                    "entries": [
                        {"path": "nested.txt", "action": "add", "sha256": "0" * 64, "size": len(payload)}
                    ],
                },
            }
            write_archive_entries(
                overlay,
                {".devtool-artifact.json": json.dumps(manifest).encode(), "nested.txt": payload},
            )
            bundle = create_overlay_bundle(root / "bundle.zip", [overlay])

            self.assertFalse(validate_artifact(repo, bundle).ok)
            ignored = validate_artifact(repo, bundle, ignore_checksum=True)
            self.assertTrue(ignored.ok, ignored.messages)

    def test_cli_ignore_checksum_applies_overlay_without_disabling_manifest_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            payload = b"ok\n"
            manifest = {
                "schema_version": 2,
                "type": "overlay",
                "inventory": {
                    "format": 1,
                    "entries": [
                        {"path": "file.txt", "action": "add", "sha256": "0" * 64, "size": len(payload)}
                    ],
                },
            }
            archive = root / "bad-checksum.zip"
            write_archive_entries(archive, {".devtool-artifact.json": json.dumps(manifest).encode(), "file.txt": payload})

            rc = main(["-r", str(repo), "--yes", "apply-overlay", str(archive), "--ignore-checksum", "--keep-artifact"])
            self.assertEqual(rc, 0)
            self.assertEqual((repo / "file.txt").read_bytes(), payload)

    def test_embedded_validator_runs_from_repo_root_and_is_never_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            temp_parent = root / "preferred-tmp"
            temp_parent.mkdir()
            script = b"""#!/usr/bin/env bash
set -euo pipefail
test "$PWD" = "$DEVTOOL_REPO_ROOT"
test "$TMPDIR" = "$DEVTOOL_VALIDATION_TMPDIR"
test -f "$DEVTOOL_REPO_ROOT/payload.txt"
test ! -e "$DEVTOOL_REPO_ROOT/.devtool-artifact/validate.sh"
printf 'validator-ok\\n'
printf 'ran\\n' > "$DEVTOOL_REPO_ROOT/validator-ran.txt"
"""
            payload = b"payload\n"
            manifest = {
                "schema_version": 2,
                "type": "overlay",
                "repository": {"name": "repo"},
                "inventory": {"format": 1, "entries": [{"path": "payload.txt", "action": "add", "sha256": sha(payload), "size": len(payload)}]},
                "validation": {
                    "required": True,
                    "restore": False,
                    "build": False,
                    "test": False,
                    "rollback_on_failure": True,
                    "script": {
                        "path": ".devtool-artifact/validate.sh",
                        "runner": "bash",
                        "sha256": sha(script),
                        "timeout_seconds": 30,
                    },
                },
            }
            archive = root / "scripted.zip"
            write_archive_entries(
                archive,
                {
                    ".devtool-artifact.json": json.dumps(manifest).encode(),
                    ".devtool-artifact/validate.sh": script,
                    "payload.txt": payload,
                },
            )
            with patch.dict(os.environ, {"TMPDIR": str(temp_parent)}, clear=False):
                rc = main(["-r", str(repo), "--yes", "apply-overlay", str(archive), "--keep-artifact"])
            self.assertEqual(rc, 0)
            self.assertTrue((repo / "validator-ran.txt").exists())
            self.assertFalse((repo / ".devtool-artifact").exists())
            self.assertEqual(list(temp_parent.iterdir()), [])


    def test_ignore_checksum_allows_embedded_validator_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            script = b"#!/usr/bin/env bash\nset -e\ntest \"$PWD\" = \"$DEVTOOL_REPO_ROOT\"\n"
            manifest = {
                "schema_version": 2,
                "type": "overlay",
                "validation": {
                    "script": {
                        "path": ".devtool-artifact/validate.sh",
                        "runner": "bash",
                        "sha256": "0" * 64,
                        "timeout_seconds": 30,
                    }
                },
            }
            archive = root / "script.zip"
            write_archive_entries(
                archive,
                {".devtool-artifact.json": json.dumps(manifest).encode(), ".devtool-artifact/validate.sh": script},
            )
            self.assertFalse(validate_artifact(repo, archive).ok)
            self.assertTrue(validate_artifact(repo, archive, ignore_checksum=True).ok)
            result = run_artifact_validation_script(repo, archive, ignore_checksum=True)
            self.assertIsNotNone(result)
            self.assertEqual(result.returncode, 0)

    def test_bundle_dependencies_are_topologically_sorted_and_cycles_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.zip"
            second = root / "second.zip"
            write_archive_entries(first, {"a.txt": b"a"})
            write_archive_entries(second, {"b.txt": b"b"})
            manifest = {
                "schema_version": 2,
                "type": "overlay_bundle",
                "overlays": [
                    {"id": "second", "artifact": "overlays/second.zip", "after": ["first"]},
                    {"id": "first", "artifact": "overlays/first.zip"},
                ],
            }
            bundle = root / "bundle.zip"
            write_archive_entries(
                bundle,
                {
                    ".devtool-artifact.json": json.dumps(manifest).encode(),
                    "overlays/first.zip": first.read_bytes(),
                    "overlays/second.zip": second.read_bytes(),
                },
            )
            with tempfile.TemporaryDirectory() as out:
                _manifest, entries = materialize_overlay_bundle_entries(bundle, out)
                self.assertEqual([entry.id for entry in entries], ["first", "second"])

            manifest["overlays"][0]["after"] = ["first"]
            manifest["overlays"][1]["after"] = ["second"]
            cyclic = root / "cyclic.zip"
            write_archive_entries(
                cyclic,
                {
                    ".devtool-artifact.json": json.dumps(manifest).encode(),
                    "overlays/first.zip": first.read_bytes(),
                    "overlays/second.zip": second.read_bytes(),
                },
            )
            with self.assertRaisesRegex(RuntimeError, "dependency cycle"):
                materialize_overlay_bundle_entries(cyclic, root / "cycle-out")

    def test_embedded_commit_message_creates_final_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "devtool@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Devtool Tests"], cwd=repo, check=True)
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "Base"], cwd=repo, check=True)

            payload = b"new\n"
            manifest = {
                "schema_version": 2,
                "type": "overlay",
                "repository": {"name": "repo"},
                "inventory": {"format": 1, "entries": [{"path": "new.txt", "action": "add", "sha256": sha(payload), "size": len(payload)}]},
                "apply": {"commit": {"enabled": True, "strategy": "single", "message": "Apply embedded commit"}},
            }
            archive = root / "commit.zip"
            write_archive_entries(archive, {".devtool-artifact.json": json.dumps(manifest).encode(), "new.txt": payload})
            rc = main(["-r", str(repo), "--yes", "apply-overlay", str(archive), "--keep-artifact"])
            self.assertEqual(rc, 0)
            subject = subprocess.run(
                ["git", "log", "-1", "--pretty=%s"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE
            ).stdout.strip()
            self.assertEqual(subject, "Apply embedded commit")

    def test_bundle_validator_failure_rolls_back_every_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()

            def overlay(path: Path, payload_path: str, data: bytes) -> Path:
                manifest = {
                    "schema_version": 2,
                    "type": "overlay",
                    "inventory": {"format": 1, "entries": [{"path": payload_path, "action": "add", "sha256": sha(data), "size": len(data)}]},
                }
                write_archive_entries(path, {".devtool-artifact.json": json.dumps(manifest).encode(), payload_path: data})
                return path

            first = overlay(root / "first.zip", "first.txt", b"first\n")
            second = overlay(root / "second.zip", "second.txt", b"second\n")
            validator = root / "validate-bundle.sh"
            validator.write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
            bundle = create_overlay_bundle(
                root / "bundle.zip",
                [first, second],
                validation_required=True,
                validation_script=validator,
                validation_runner="bash",
                commit_message="Must not commit",
            )
            manifest = read_artifact_manifest(bundle)
            manifest["validation"].update({"restore": False, "build": False, "test": False})
            members = {member.name: member.data for member in list_archive_members(bundle, include_data=True) if not member.is_dir}
            members["devtool-bundle.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
            write_archive_entries(bundle, {name: data or b"" for name, data in members.items()})

            rc = main(["-r", str(repo), "--yes", "apply-overlays", str(bundle), "--keep-artifact"])
            self.assertEqual(rc, 1)
            self.assertFalse((repo / "first.txt").exists())
            self.assertFalse((repo / "second.txt").exists())


if __name__ == "__main__":
    unittest.main()
