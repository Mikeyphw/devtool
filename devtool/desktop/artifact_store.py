from __future__ import annotations

from pathlib import Path
from typing import Any


class DesktopArtifactStore:
    def __init__(self, repository: Path | str) -> None:
        self.repository = Path(repository).expanduser()

    def inspect(self, artifact: Path | str) -> dict[str, Any]:
        from ..artifact_inspection import inspect_artifact

        return inspect_artifact(Path(artifact).expanduser(), repo_root=self.repository)
