# Android Variant Profile v9 Final Audit

V9 rebases the v8 first-class Android variant profile implementation onto the 2026-09-18 21:46 Devtool snapshot.

The prior v8 overlay failed preflight because `devtool/cli.py` had changed after the overlay's source baseline. V9 preserves the current CLI's `add-dir` artifact verification behavior while applying only the variant-profile CLI hunks (`variants` parser, config loading, dispatch, and rendering).

Regression validation on the rebased tree: 111 passed (`tests/test_android_variant_profiles.py` + `tests/test_android_runner.py`).

V9 otherwise retains v8's end-to-end profile authority: semantic module-qualified tasks, variant-aware signing, instrumentation, release qualification/gates, deployability, artifact selection, resumable validation, flavors/custom build types, and legacy compatibility fallbacks where profile metadata is absent.
