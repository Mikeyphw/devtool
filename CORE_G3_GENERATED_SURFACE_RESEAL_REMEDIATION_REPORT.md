# Pre-CORE-G3 generated-surface fixed-point remediation — V3

The CORE-G3 audit found generated CLI/config/wrapper surfaces stale after later program commands were added.

V3 is rebuilt against the exact artifact-chain reconstruction of the qualified post-EXEC-X04 state (`d4d8d30`). It installs the complete final `core-g3` wrapper/workflow/test surface before regenerating checked-in CORE surfaces, so the final gate cannot immediately invalidate its prerequisite.

V3 also corrects the V2 packaging-base defect: every preimage guard is derived from the successful DIST-X03 → EXEC-X04 artifact chain, not from a local rehearsal tree.

Validation: retained CORE-X04 generated-surface contract. This remediation consumes no roadmap position; CORE-G3 remains a separate gate.
