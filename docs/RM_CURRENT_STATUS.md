# RM00–RM20: authoritative current-status index (FC-07)

This is a **current cross-reference**, not a retroactive rewrite of historical promise audits or an FC-09 release certificate. The accompanying `RM_CURRENT_STATUS.json` identifies the original audit reference, subsequent repairs and evidence boundary for every milestone. `RM_ORIGINAL_PROMISE_LEDGER_20261024.json` preserves the exact 168 promise texts as recorded for full closure planning: 160 active and 8 user-cancelled RM19 obligations.

**Authority:** Original promise text → current code and tests → source-bound install and external run receipt → final gate. Local/mock/source tests are never device, host, consumer, signing or release authority. Not-yet-run conditions remain **OPEN**, not failed or passed. The status index must not depend on dates in historical prose to establish qualification.

| Milestone | Source/local engineering state | Superseding repairs | Still required |
|---|---|---|---|
| RM00 | LOCAL_SOURCE_TEST_COVERAGE_REQUIRES_FINAL_REQUALIFICATION | Historical local implementation | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM01 | SOURCE_REPAIRED_REQUIRES_FINAL_REQUALIFICATION | FC-01 | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM02 | LOCAL_SOURCE_TEST_COVERAGE_REQUIRES_FINAL_REQUALIFICATION | Historical local implementation | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM03 | LOCAL_SOURCE_TEST_COVERAGE_REQUIRES_FINAL_REQUALIFICATION | Historical local implementation | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM04 | LOCAL_SOURCE_TEST_COVERAGE_REQUIRES_FINAL_REQUALIFICATION | Historical local implementation | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM05 | LOCAL_SOURCE_TEST_COVERAGE_REQUIRES_FINAL_REQUALIFICATION | Historical local implementation | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM06 | LOCAL_SOURCE_TEST_COVERAGE_REQUIRES_FINAL_REQUALIFICATION | Historical local implementation | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM07 | LOCAL_SOURCE_TEST_COVERAGE_REQUIRES_FINAL_REQUALIFICATION | Historical local implementation | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM08 | LOCAL_SOURCE_TEST_COVERAGE_REQUIRES_FINAL_REQUALIFICATION | Historical local implementation | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM09 | SOURCE_REPAIRED_REQUIRES_FINAL_REQUALIFICATION | FC-06 | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM10 | LOCAL_SOURCE_TEST_COVERAGE_REQUIRES_FINAL_REQUALIFICATION | Historical local implementation | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM11 | SOURCE_REPAIRED_REQUIRES_FINAL_REQUALIFICATION | FC-03 | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM12 | SOURCE_REPAIRED_REQUIRES_FINAL_REQUALIFICATION | FC-03/FC-04 | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM13 | SOURCE_REPAIRED_REQUIRES_FINAL_REQUALIFICATION | FC-04 | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM14 | LOCAL_SOURCE_TEST_COVERAGE_REQUIRES_FINAL_REQUALIFICATION | Historical local implementation | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM15 | LOCAL_SOURCE_TEST_COVERAGE_REQUIRES_FINAL_REQUALIFICATION | Historical local implementation | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM16 | LOCAL_SOURCE_TEST_COVERAGE_REQUIRES_FINAL_REQUALIFICATION | Historical local implementation | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM17 | SOURCE_REPAIRED_REQUIRES_FINAL_REQUALIFICATION | FC-02 | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM18 | SOURCE_REPAIRED_REQUIRES_FINAL_REQUALIFICATION | FC-02 | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |
| RM19 | CANCELLED_BY_USER | Historical local implementation | NOT_APPLICABLE_CANCELLED |
| RM20 | SOURCE_REPAIRED_REQUIRES_FINAL_REQUALIFICATION | FC-02/FC-03/FC-05 | PENDING_OR_UNVERIFIED_REAL_HOST_CONSUMER_DEVICE_AS_APPLICABLE |

## Regression authority

- `tests/test_rm00_shared_contracts.py` asserts the **eight** reviewed providers, retaining immutability and unreviewed-provider refusal.
- `tools/fc07_regression_inventory.py inventory` enumerates every tracked test module exactly once in 12 balanced deterministic shards; no selective suite is misrepresented as a whole-repository pass.
- `tools/fc07_regression_inventory.py collect` calls native `pytest --collect-only`, records the **process exit**, logs the collection output and counts, and marks incomplete collection honestly.
- `tools/fc07_regression_inventory.py run --shard N` executes one bounded shard with `--junitxml`, an OS process timeout and a machine JSON report containing PASS/FAIL/INCOMPLETE/TIMEOUT and skip counts. Never interpret printed `N passed` as process success without exit 0.
- Existing FC-01 through FC-06 adversarial tests remain executable. Do not blanket-deselect npm CI/pack, which are separate live Termux qualifications; any platform-dependent failures must be classified and investigated.
- Canonical generated CLI and completion drift is checked by `tests/test_core_x04_generated_surfaces.py` and the stock generator; generated surfaces are not modified by this index.

## Limitations and handoff to FC-08/FC-09

Sharded all-suite execution cannot replace real Windows/macOS/Android or authenticated GitHub/consumer results. FC-08 collects actual provenance and host/device gates; FC-09 applies the 168-promise seal. Future additions to the provider registry require source review and an updated exact expected set; do not substitute `assert set(PROVIDERS)==set(PROVIDERS)`.
