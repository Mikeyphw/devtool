# RMP-H1 Phase 3 — Resumed command plan and authoritative shard cases

This overlay builds on Phase 2's native 119-test acceptance receipt.

**N02**: A persisted session with a malformed explicit `plan_specs` must fail to
load rather than be normalized to an empty successful plan. Even a list-shaped
plan is verified on load; secrets are refused before status/resume logic.
Missing historical plans are still supported.

**C04**: An internal aggregate cannot authenticate its own original selection:
its expected IDs can be modified alongside attachment IDs without breaking
count checks. `run_attachment_manifest(..., authorized_case_ids=...)` now
accepts an **independent** selected inventory. TEST-G3 supplies its selection
before interpreting the aggregate. Without that external binding, a generic
manifest remains a projection and cannot be independently certified.

The Phase 3 JSON receipt validates the preceding Phase 2 receipt against its
committed source/tree, then runs the selected local regression lanes. All five
H1 formal closures remain false. N01 source provenance is retained but not
independently signed; C05 Android HUD, C06 downstream environment identity,
N02 historical journals and C04 remote TEST-A11 still require separate proof.
