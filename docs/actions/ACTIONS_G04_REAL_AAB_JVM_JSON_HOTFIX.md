# G4 live AAB Android unit-test JSON runtime hotfix

## Real evidence / reason for the change

GitHub Actions run `38438620783` (attempt 1) on source `a3e414d1fbfdb7773d7ed8c7d57b024f01e38d90` was `failure`, with zero uploaded AAB artifacts. The `bundle-candidate` job failed in `Build unsigned AAB and run same-source unit tests and release lint`: Gradle reported `:app:testDebugUnitTest` with **26 unit tests, four failures**, all in `ExecutionGraphStateTest` (canonical accounting, typed child operations, blocked state and malformed JSON). The run had successfully compiled the Java tests. Log evidence was supplied directly by the device operator.

### Root cause and patch

`ExecutionGraphState` uses Android's `org.json` parser. Android Gradle Plugin's mockable Android classes in local JVM tests can return null/default values when `unitTests.returnDefaultValues` is enabled. The build declared only JUnit, not a functioning `org.json` JVM implementation. That accounts for the observed nulls, empty node lists and missing blocked status. This is the source-level diagnosis; **only a new hosted run can verify the recovery**.

- Add pinned `testImplementation 'org.json:json:20240303'` to `android/devtool-live-hud/app/build.gradle`; this is **test-scoped**, never part of app runtime or bundled candidate.
- Add a direct real-JSON-parser assertion before the four existing `ExecutionGraphStateTest` cases; keep existing tests unchanged.
- Keep `devtoolBundleUnsignedReleaseCandidate` depending on release bundle, debug unit tests, and `lintRelease`. Do not bypass tests, signing safety or artifact provenance.
- Add offline, source-bound Python regression coverage.

### Live verification procedure (operator on Termux)

1. Apply the guarded overlay on the clean installed Devtool checkout; it creates one Git commit automatically.
2. Push that commit to GitHub `main` after reviewing the change; confirm local and remote SHA match.
3. Run `devtool -r "$HOME/Code/devtool" actions android aab-plan --ref main --json` and confirm the new `source_sha`, `qualified_release: false`.
4. Dispatch **one** new run with `devtool ... actions android aab-run --ref main --yes --json`. Do not reuse or rerun run `38438620783` as a qualification of the updated source. If dispatch returns uncorrelated, reconcile before retrying.
5. `gh run watch RUN_ID --repo Mikeyphw/devtool --exit-status`. Check all `:app:testDebugUnitTest` tests and `:app:lintRelease` passed, AAB exists, CI manifest/evidence stage succeeds and the GitHub artifact is uploaded.
6. Run the G4 `aab-check` and `aab-import` commands with the new run ID and **new exact source SHA**. Record JSON, registry ID, and artifact SHA-256 in existing `OUT` session. Verify bundletool separately before local signing; no signing secrets should enter GitHub.
7. Only mark `G4_AAB` passed when the hosted run plus artifact/evidence validation are successful.

### Qualifications

Offline Python regressions and Devtool overlay integration alone do **not** prove GitHub can resolve `org.json` or that Android tests succeed. The local build environment may not contain the Android SDK/Gradle caches. No artifact or successful bundle is claimed for run `38438620783`.
