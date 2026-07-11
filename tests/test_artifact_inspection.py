from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from devtool.artifact_inspection import inspect_artifact
from devtool.cli import build_parser, main


class ArtifactInspectionTests(unittest.TestCase):
    def _overlay(self, root: Path) -> Path:
        payload = b"hello\n"
        manifest = {
            "schema_version": 2,
            "type": "overlay",
            "name": "Example overlay",
            "repository": {"name": "repo"},
            "target": "app",
            "inventory": {"format": 1, "entries": [{
                "path": "src/new.txt", "action": "add",
                "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload),
            }]},
        }
        artifact = root / "example.zip"
        with zipfile.ZipFile(artifact, "w") as zf:
            zf.writestr(".devtool-artifact.json", json.dumps(manifest))
            zf.writestr("src/new.txt", payload)
        return artifact

    def test_inspection_returns_inventory_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._overlay(root)
            payload = inspect_artifact(artifact)
            self.assertEqual(payload["name"], "Example overlay")
            self.assertEqual(payload["target"], "app")
            self.assertEqual(payload["counts"]["add"], 1)
            self.assertTrue(payload["validation"]["ok"])

    def test_parser_exposes_inspect_artifact(self) -> None:
        args = build_parser().parse_args(["inspect-artifact", "example.zip", "--diff"])
        self.assertEqual(args.command, "inspect-artifact")
        self.assertTrue(args.diff)

    def test_cli_prints_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = self._overlay(Path(tmp))
            self.assertEqual(main(["inspect-artifact", str(artifact)]), 0)
