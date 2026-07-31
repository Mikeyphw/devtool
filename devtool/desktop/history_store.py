from __future__ import annotations

from pathlib import Path
from typing import Any


class DesktopHistoryStore:
    def __init__(self, repository: Path | str) -> None:
        self.repository = Path(repository).expanduser()

    def load(self, *, limit: int = 50) -> list[dict[str, Any]]:
        from ..artifacts import load_history

        entries = load_history(self.repository)
        return [dict(entry) for entry in entries[-max(0, limit):]]
