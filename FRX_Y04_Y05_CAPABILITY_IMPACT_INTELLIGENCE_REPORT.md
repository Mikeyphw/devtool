# FRX-Y04 + FRX-Y05 Capability and Impact Intelligence Report

## Scope

This non-gate overlay combines two adjacent federation-intelligence milestones because FRX-Y05 is a reasoning layer over the capability/dependency graph introduced by FRX-Y04. FRX-Y06 remains separate because interface conformance introspects a different child public-contract surface.

## FRX-Y04 — Repository capability/dependency graph

- Adds versioned architecture capability declarations under each explicitly enrolled federation repository.
- `exports.capabilities` declares provider contracts such as `securefs/v1`.
- `requires.capabilities` accepts exact string requirements and inline requirements with `optional`, explicit `compatible` identifiers, and `policy = "exact" | "explicit-list"`.
- Compatibility is explicit; Devtool does not infer or select package-manager versions.
- Mandatory missing or ambiguous providers fail closed during capability preflight/planning.
- Optional missing/ambiguous requirements remain visible evidence without blocking the graph.
- Federated plan JSON includes the capability graph and reason-bearing provider→consumer edges.
- Provider→consumer dependencies feed sequential execution, the FRX-Y01 scheduler, and EXO node dependency truth rather than being display-only.
- `devtool federation capabilities` / `graph` exposes the machine-readable architecture graph.

## FRX-Y05 — Affected / why / change-impact engine

- Adds `devtool federation affected`, `devtool federation why`, and `devtool federation run <workflow> --affected`.
- Direct changes may be selected explicitly with repeatable `--repository`, discovered from dirty child worktrees, or detected with `--since <rev>` where the revision resolves in the selected child repository.
- Impact propagates only over declared capability edges; source-text mentions never create dependency edges.
- Reports direct and transitive affected repositories, conservatively impacted exported capabilities, workflows, executable/metadata groups, and complete causal reason paths.
- The report already has an `interfaces` projection hook; it remains empty until FRX-Y06 introduces concrete interface contracts.
- `--affected` projects the selected workflow to affected repository steps plus the full closure of explicitly declared workflow prerequisites; it never drops a required setup/provider step merely because that repository itself did not change. Capability dependencies among participating steps are rebuilt by the shared graph authority.
- A clean no-change run is a successful no-op with structured evidence.

## Native roadmap execution

Two first-class test identities remain separately traceable:

- `roadmap-frx-y04` → `tests/test_frx_y04_capability_dependency_graph.py`
- `roadmap-frx-y05` → `tests/test_frx_y05_affected_impact_engine.py`

Two native workflows (`roadmap-frx-y04`, `roadmap-frx-y05`) compose those tests with `source-syntax`, federation baseline, and EXO/workflow baseline. `roadmap-current` orders Y04 after Gate 1 and Y05 after Y04.

No reusable non-test fixture operation was needed beyond first-class tests, so this milestone does not add artificial TLJ jobs merely to satisfy a count.

## Audit loop

- Focused combined Y04+Y05 suite after safety hardening: 22 passed.
- Final declared Y04 JUnit run: 9 passed.
- Final declared Y05 JUnit run: 13 passed.
- Federation regression sweep (FRX-01..12, X01..X04, Y01, Y04, Y05): 202 passed.
- Federation/config/planning/execution/completion/native-roadmap compatibility sweep: 137 passed + 32 subtests.
- Sealed observability/final-seal/completion subset: 30 passed + 32 subtests.
- Gate-1 regression aggregate replay after these changes: 103 passed.
- `git diff --check`: pass.
- `python -m compileall -q devtool tests`: pass.

Completion generation and the checked-in zsh mirror are updated together; the sealed FRX-11/12 observability/final-seal and WRAP completion contracts remain green.

## Environment evidence

Native plan JSON succeeds for both roadmap workflows with 4 nodes / 4 waves. Preflight fails only at the expected repository target contract: `native-termux:termux` is incompatible with this desktop-linux analysis host. The target was not weakened.

## Delivery behavior

This is not a validation gate. Apply with `--no-validate`. There is no finalizer and no Devtool editable reinstall; the next reinstall remains Gate 2 unless another explicit gate is reached earlier.

## Packaged artifact replay

The final non-gate ZIP is checked against the exact post-Gate-1 baseline with the pre-Y04/Y05 Devtool. The touched-files contract verifies all 15 payload paths. A fresh Git clone then applies the artifact through the existing isolated-worktree transaction path with `--no-validate --keep-artifact`; the apply finishes with zero diagnostics and a clean primary checkout after integration. All 15 packaged payload hashes match the replayed repository, and the replayed Y04 + Y05 + native-roadmap contracts pass 26/26.
