# G8 post-baseline G3 Textual source amendment — 2026-10-14

This is an explicit source-contract amendment, **not** a re-baselining of the
immutable G8 qualification matrix or an assertion of formal release readiness.

## Why an amendment is necessary

G8's sealed `ACTIONS_G08_QUALIFICATION_MATRIX.json` pins the original
`devtool/actions_pipeline_textual.py`. Subsequent genuine Android/Termux
interactive PTY checks exposed limited visibility of the `archive` pipeline
node at 48×20. The G3 narrow Textual overlay introduced compact, one-line node
rows, `d` details toggle, retained read-only refresh, and preserved original
identity checks. A fixture-only correction subsequently supplied a synthetic
refresh loader and verified genuine missing-journal fail-closed behavior.

This amendment accepts **only the exact file bytes** of that completed repair
when accompanied by its exact focused regression test and original remediation
record. Other source drift, even in the same file, remains a G8 mismatch.

## Device evidence reported by the operator

- Original frozen G2 pipeline ID: `7eb51755200248a5b03b5314d4e8605e`.
- Remote GitHub run `38471591526`, archived artifact `11852550049`.
- Termux Textual PTY: **48×20 PASS** and **92×28 PASS** with all four nodes
  seen in each case, keyboard `r`/`q`, resize, and unchanged durable journal.
  Device-side JSON: `g3-device-20261014T001837Z-f54451a.json`.
- Android HUD authenticated bridge: run `termux-590262260`, exit 0;
  RunStore canonical JSON output parity PASS on package
  `io.github.mikeceead.devtoolhud.debug`, with unchanged G2 journal.
  Device-side JSON: `g3-hud-verify-20261014T003950Z-f60e8401.json`.
- G3 evidence validated and recorded in the existing real-device tracker:
  `g3-closure-batch-20261014T005734Z-537d44af.json`.

The source-only G8 checker **does not independently read or verify** these
operator-side JSON receipts. They remain in the Termux real-work session and
are referenced for human audit; no historical G8 gate is automatically
promoted. Only the source transition itself is authenticated by exact hashes.

## Non-negotiable boundaries

1. Keep `ACTIONS_G08_QUALIFICATION_MATRIX.json` and
   `ACTIONS_BASELINE_OBLIGATIONS.json` byte-for-byte unchanged.
2. Keep the existing G4 unsigned-JAR verified amendment intact.
3. Reject changes to the amended G3 source, the focused regression source,
   the original G3 fix document, or this amendment evidence/tests.
4. Preserve all remaining historical dependencies, source-attestation=false,
   qualified_release=false, campaign_closed=false, and pending live gates.
5. `qualification check --require-complete` must continue to **fail closed**
   (nonzero exit), even after source pin consistency is restored.
6. No GitHub dispatch/cancel/resume, G2 journal update, or Android build is
   authorized by this source-contract amendment.
