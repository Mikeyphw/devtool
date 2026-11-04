# SFDT-08 — Native source-owned JUnit evidence

This is a Devtool-native RM-06 extension. Job metadata `ci_junit_report_dir` must specify a reviewed, repository-relative `build/test-results` directory, owned by the job. The runner refuses any report directory existing before execution, parses bounded actual `<testcase>` elements after the native command, verifies suite-count headers, binds the sorted per-file SHA-256 digest list to `typed_evidence.tests.report_sha256`, and reparses the same report bytes when verifying the local node evidence. A zero-exit process alone can never PASS tests.

`build_node_evidence` and `verify_node_evidence` remain backwards compatible: nodes without a declared report remain `test=NOT_RUN`. An opted-in node **must** produce a nonempty, passing report. No source or arbitrary user data is scanned. The `effective_gates.test=PASS` projection applies **only** to locally verified report bytes under the frozen DAG's source/run/attempt identity. `trusted_producer_verified`, `remote_authenticated`, `release_qualified`, `signature` and `native_device` remain false/UNKNOWN. Consumers must inspect the individual XML members; the canonical group digest alone is not independent GitHub attestation.

Gradle's ancillary `binary/` directory is ignored as evidence. If any report member is symlinked, malformed, contradictory, failing, or all-skipped, the native job refuses success. A report set that predates node execution is refused.

StudyFlow also needs the unsigned phone `:app:testReleaseUnitTest` task in hosted CI: this Devtool provider update permits **only** a source-reviewed, explicit `ci_android_unsigned_release_jvm_test=true` on `studyflow-ubuntu24`, with `testBuildType = "release"` verified in the tracked app Gradle source. It cannot assemble, bundle, sign, install, or publish Release packages.

No Gradle execution on local Termux; no automatic CI cutover, signing, device, or release admission. The acceptance gate is actual hosted Devtool runs and independently matched report members.
