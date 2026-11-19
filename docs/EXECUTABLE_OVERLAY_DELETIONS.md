# Native executable-overlay deletion binding

The canonical exact-tree manifest contract requires each `touched_paths` entry to be represented in both `source_sha256` and `expected_post_sha256`. A deleted path must have a real verified source digest and a **null** postimage digest. Executable-overlay v2 now uses the same rule, including for its byte-exact inventory checks.

This fixes a disagreement between the existing `artifact_base_contract` validator and executable-overlay classification. It does not alter the canonical v1/v2 `__main__.py` bootstrap bytes, apply, rollback, transaction, delivery-session or GitHub authorization mechanisms. Repository names continue to identify the selected worktree leaf; remote and branch enforcement remain mandatory.

The positive and negative cases in `tests/test_executable_overlay_deletion.py` are current source-bound coverage. Deletion entries have no archive payload, cannot omit the source hash, cannot invent a postimage, and cannot be silently excluded from the touched-path set.
