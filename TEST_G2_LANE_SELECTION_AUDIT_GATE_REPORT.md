# TEST-G2 Lane / Selection Audit Gate Report

## Scope

TEST-G2 is a standalone milestone gate. For this gate only, the audit window was widened from "since the previous gate" to the full TEST campaign so far:

- TEST-X01 failure trustworthiness and process-state isolation
- TEST-G1 isolation/trust milestone proof
- TEST-X02 stable inventory, lanes, fixtures, and deterministic selection

The gate audits those overlays' promises, folds remediation into the gate candidate, then re-runs the audit until no known promise gaps remain.

## Audit-loop findings and folded fixes

1. **Multi-command gate counts were under-reported.** TEST-G1 executed several validation commands, but the final apply summary reported only the first pytest summary. G2 fixes summary extraction to aggregate every pytest terminal summary per overlay and adds a regression proving multi-command gate counts are accumulated.
2. **External lane identity was underspecified.** External-lane checks now must declare exact tools or host capabilities. G2 adds explicit Gradle, Go, and Java/Javac smoke checks with required-tool metadata, and rejects false external contracts.
3. **Test environment reproducibility was implicit.** The repository now declares a test environment block in `devtool-tests.toml` and a committed `devtool-test-requirements.lock` consumed by G2 validation commands.
4. **Heavy historical campaigns were eligible for fast feedback through selector fallback.** G2 adds central classification rules that push FRX/GIT-FRX/final-seal/final-reseal/final-qualification campaigns to the system lane unless they have explicit durable inventory identity.
5. **Unclassified tests were too optimistic for parallel execution.** Selector-derived tests now default to `serial-process-global` until explicitly qualified.
6. **Artifact fixtures only covered overlay ZIPs.** The reusable fixture builder now covers overlay, snapshot, and overlay-bundle artifacts.
7. **Declared roadmap jobs still duplicated pytest selectors.** G2 adds stable group-backed Devtool jobs so roadmap validation can consume `selection_groups` instead of mutable file lists.
8. **Strict marker proof was initially syntactic.** G2 includes a real subprocess collection test proving unknown markers fail with strict pytest policy.

## Gate assertions

The gate proves:

- fast lane excludes broad campaigns/seals;
- integration lane preserves Git/process/PTY boundaries;
- external lane has exact tool/environment requirements;
- system lane owns broad qualification and `full` is the exact union of executable lanes;
- stable check groups survive selector moves and declared tests consume groups;
- unknown pytest markers fail real collection;
- selections and shard plans are deterministic, explainable, and immutable;
- unknown dependencies and false external contracts fail closed;
- dependency closure is explicit and cannot silently widen fast feedback;
- unclassified discovery defaults to serial process-global safety;
- artifact fixtures cover overlay, snapshot, and bundle artifacts;
- fake repository fixtures avoid real Git for unit contracts;
- repository test environment is explicitly pinned;
- declared tests without parallel qualification default to serial;
- multi-validation pytest summaries are aggregated for gate reporting.

## Local targeted validation

No Android/Gradle project validation was run in this implementation environment. External-tool smoke tests are gate-owned and skip unavailable tools.

- TEST-G2 lane-selection gate: 17 passed
- TEST-G2 external-tool lane: 2 passed, 1 skipped in this environment because Gradle was unavailable
- TEST-X02 fast inventory/selection family: 39 passed
- TEST-X01/Git isolation family: 28 passed
- validation truth/reporting family: 24 passed
- TEST-G1 system proof family: 5 passed
- Total local targeted result: 115 passed, 1 skipped
- `python -m py_compile`: passed
- `git diff --check`: passed

## Result

The expanded audit loop is clean for known TEST-X01, TEST-G1, and TEST-X02 promises after the folded fixes above. TEST-G2 remains a standalone gate and does not merge TEST-X03 feature scope.
