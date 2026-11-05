# RM15-HF01 — Full regression acceptance repair

Source: existing PR #11, `feature/rm15-polyglot-native`, exact `d9fa947c96d45f3a46d86b69419bbd320c77894e`.

Run #42092691517: 7,640 tests; 29 failures, 0 errors. These were from three legacy test modules, not RM15 provider tests.

- Shard 2 (27): `roadmap_gate_audit` rejected literal `actions/setup-dotnet@26b0ec14cb23fa6904739307f278c14f94c95bf1` though it resolves to GitHub's immutable `actions/setup-dotnet` `v5` tag. Added only that exact, independently resolved SHA; arbitrary refs still denied.
- Shard 6 (1): R03B destructive inventory omitted three DN03 create-only exporter rollback calls and the runtime-only archive/stage cleanup. Record exact source expressions and individual rationale with no blanket unclassification waiver. The staging-directory pathname-swap risk is explicitly **not** declared eliminated.
- Shard 15 (1): RM06 test matched `steps[].env` due to substring splitting. Assert at the exact `jobs.<id>.env` indentation and add a hostile test proving job-level `runner.temp` still fails while a step-level reference passes.

The fixes do not alter original CI release, signing, Android execution, or generic provider code. Added a targeted automatic PR source gate that runs tests for all three failures plus RM15 unit tests; full 24-shard regression remains authoritative before merge. **This is not CI cutover or release qualification.**

## Acceptance sequence

1. Apply transactional overlay to existing RM15 branch, auto-commit, and push. Existing PR #11 updates automatically.
2. Inspect the new automatic `Devtool RM15 strict regression qualification` workflow (must succeed).
3. Run `gh workflow run devtool-full-repository-pytest.yml -R Mikeyphw/devtool --ref feature/rm15-polyglot-native`, verify all 24 shards and summary.
4. Confirm the PR mergeable against current `main`; requalify after any required upstream reconciliation rather than force push or rewriting source.

No Gradle or .NET tasks are executed locally by the overlay.
