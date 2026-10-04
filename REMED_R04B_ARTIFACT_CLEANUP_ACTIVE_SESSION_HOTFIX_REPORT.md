# REMED-R04B Artifact Cleanup Active Session Hotfix

## Summary

The R04B overlay applied, validated, committed, and passed `verify-apply`, but the final source-artifact cleanup step returned failure because canonical LIFE cleanup treated the external overlay zip in `/sdcard/Download` as ordinary repository cleanup while an older active validation session was present.

This hotfix keeps validation/transaction sessions as blockers for repository-owned cleanup candidates, while allowing the already-consumed external apply source artifact descriptor to be removed when it has no direct durable reference.

## Invariant

- Active validation sessions still block repository cleanup.
- Direct references still protect any cleanup candidate, including external artifacts.
- A transient `artifact-apply-source` descriptor outside the repository root is not converted into durable repository evidence merely because repository cleanup is currently blocked.

## Validation

- `tests/test_remed_r04b_artifact_cleanup_active_session.py`
- `tests/test_remed_r04a_validation_schema6_cleanup.py`
