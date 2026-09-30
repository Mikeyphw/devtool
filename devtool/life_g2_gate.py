from __future__ import annotations

"""LIFE-G2 ownership/retention milestone gate contract.

The gate is deliberately small: canonical cleanup semantics live in
``cleanup_lifecycle``.  This module names the authoritative milestone promises
and produces a stable machine projection consumed by the gate tests/report.
"""

from dataclasses import asdict, dataclass
from typing import Iterable


_GATE_PROMISES = (
    "LIFE-P063", "LIFE-P064", "LIFE-P065", "LIFE-P066", "LIFE-P067",
    "LIFE-P068", "LIFE-P069", "LIFE-P070", "LIFE-P071", "LIFE-P072",
    "LIFE-P073", "LIFE-P074", "LIFE-P075", "LIFE-P076", "LIFE-P077",
    "LIFE-P078", "LIFE-P079", "LIFE-P080", "LIFE-P081", "LIFE-P082",
    "LIFE-P083", "LIFE-P084", "LIFE-P085", "LIFE-P086", "LIFE-P087",
    "LIFE-P088", "LIFE-P089", "LIFE-P090", "LIFE-P091", "LIFE-P092",
    "LIFE-P093", "LIFE-P094", "LIFE-P095", "LIFE-P098", "LIFE-P099",
    "LIFE-P101", "LIFE-P105",
)


@dataclass(frozen=True, slots=True)
class LifeG2GateContract:
    roadmap_id: str = "LIFE-G2"
    position: str = "44/70"
    gate_window: str = "2/2"
    promises: tuple[str, ...] = _GATE_PROMISES
    invariants: tuple[str, ...] = (
        "tracked-user-source-survives",
        "heuristic-only-never-deletion-authority",
        "global-scope-explicit-and-confirmed",
        "live-protection-roots-revalidated-at-apply",
        "stale-path-content-policy-destination-fails-closed",
        "partial-cleanup-remains-partial",
        "remote-cleanup-destination-local-only",
        "cleanup-secondary-failure-preserves-primary",
        "receipt-tombstone-provenance-survives-payload",
    )

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def gate_contract() -> LifeG2GateContract:
    return LifeG2GateContract()


def missing_promises(proved: Iterable[str]) -> tuple[str, ...]:
    have = {str(item) for item in proved}
    return tuple(item for item in _GATE_PROMISES if item not in have)


__all__ = ["LifeG2GateContract", "gate_contract", "missing_promises"]
