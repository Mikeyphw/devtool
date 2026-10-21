# NEXT-02 — typed evidence, host eligibility and report normalization

**Status:** source candidate, **not** independently accepted native/hosted/device/release qualification. Uses the existing Actions R04 lineage, A09 hosted exporter, EXO child RUN catalog, R06C federation plan contract, and artifact registry; no competing scheduler, dispatcher, mutable registry or release authority.

## APIs and CLI

* `devtool evidence validate FILE --commit FULL_SHA --run-id ID --attempt N --json`: validates version 1 `devtool.typed-evidence` structural identity and rejects unknown fields, wrong run attempts and incoherent missing/failing tests. **Reported `PASS` gates become effective `UNKNOWN`** without independently verified producer evidence. This is a schema verification, not signing or remote authentication.
* `devtool evidence normalize FILE --format junit|pytest-junit|js-junit|trx|go-json|cargo-json --json`: parses **actual** test cases and errors, hashes report bytes and returns `PASS/FAIL/BLOCKED` in the *local report* domain. Zero mandatory tests, invalid XML/JSON, missing report and Go package-level failures fail closed. Current adapters intentionally do not aggregate multi-shard test reports or attest test producers.
* `devtool artifacts verify REGISTRY_ID --repository OWNER/REPO --commit FULL_SHA --run-id ID --attempt N --json`: reads existing `.devtool/artifacts/registry.json` under the R04 bounded symlink-safe reader, checks the exact registry producer identity, hashes the local file, and **does not append verification events**. `artifact_integrity=PASS` never implies signature, authenticity, native device, executable ABI or release. Producer-specific APK/AAB/ZIP/Go verifiers remain authoritative.
* `host_eligibility` and `graph_host_admission`: bounded, explainable runner/OS/architecture/toolchain/device/root/signer admission attached per DAG node. Existing A09 adoption enforces host admission before generating an Ubuntu job, retaining its existing more specific refusals and argv safety.
* `project_lineage(actions_snapshot, federation_contract=None)`: read-only identity-bound R04 Actions/EXO node and R06C federation plan projection, preserving separate source locks and never asserting cross-repo trusted joins from offline JSON.

## No-upward-promotion rules

The typed domains `source`, `ci_presence`, `host`, `build`, `test`, `artifact_integrity`, `artifact_authenticity`, `instrumentation`, `native_device`, `consumer`, `signature`, and `release` are **separate**. Each starts `UNKNOWN` absent independently validated authority. `NOT_RUN`, `UNKNOWN`, `WAITING`, `BLOCKED`, `PASS`, and `FAIL` are valid states, not interchangeable aliases. Successful unit tests, GitHub workflow presence, provider JSON assertions, hashes, source commits, or a locally readable EXO catalog do not authorize release.

### Deferred acceptance (not silently considered complete)

NEXT-03 provider backends must emit versioned evidence tied to *actual* authenticated GitHub run/attempt and host proof. NEXT-04/07/08 must supply real distribution signatures/ABI and dedicated normalized test report producers; existing A09 fixed Ubuntu jobs remain narrow. Formal R09 native/hosted/consumer/device/security gates are separate and still open. Normalized result payloads are not automatically ingested as privileged EXO results until an independent producer verifier is available.

## Source acceptance

`tests/test_next02_evidence_kernel.py` exercises wrong source/run/attempt, forged PASS, malformed reports, zero-test and failing-test cases, path traversal/symlink, digest tampering, repository/attempt conflicts, per-domain status, and read-only ledger invariance. A03/A09/R04 and canonical CLI-generated-surface regression suites are separately checked. No remote GitHub dispatch, native Termux run, independent consumer or device was performed by this patch.

The `graph_host_admission` read-only projection resolves target-local job metadata from its actual target owner, not the global job table. A09 remains the enforcement owner for generated workflow dispatch.
