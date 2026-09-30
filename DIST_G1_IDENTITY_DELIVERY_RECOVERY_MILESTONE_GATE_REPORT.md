# DIST-G1 — Identity, delivery and recovery milestone gate

**Campaign position:** 42/70  
**DIST gate window:** 3/3  
**Previous qualified:** DIST-X01 (`e5c5337b4d2e41318cc0f6c359460f8c`, `13fa36e`) and DIST-X02 (`a4f4de69f2074152beb5906f6c9be566`, `2888434`)  
**Next after successful gate qualification:** LIFE-X03 — 43/70

## Gate audit result

The gate audits every promise carried by DIST-X01 and DIST-X02. Sixty-five X01/X02 promises are sealed by this milestone, including the X01 partials `DIST-P036`, `DIST-P038` and `DIST-P070` after the sequential/parallel parity and terminal-authority proofs. Eleven provider/hierarchy/control/lifecycle promises remain explicitly partial for later DIST work and are not overclaimed here.

The audit found two real correctness gaps and folded both into the natural DIST gate boundary before sealing:

1. **Fresh-process replay integrity.** X02 persisted the acknowledged sequence but the receiver-side sequence/content digest cache was process-local. A fresh receiver could therefore lose the evidence needed to verify an at-least-once replay at the acknowledgement boundary. DIST-G1 extends `DeliveryCursor` with the acknowledged event ID and content digest, restores `DelegatedEventReceiver` from that durable cursor, verifies identical boundary replay, rejects different-body replay, rejects unverifiable older stale replay, and initializes parallel federation receivers from the same cursor used by the forwarder.
2. **Primary failure preservation.** Git-lineage finalization could raise after an already-observed child failure and replace the actionable primary result with a secondary evidence-finalization error. DIST-G1 now preserves failed/cancelled/blocked child status and exit code while recording lineage finalization as secondary evidence. Even if publishing that secondary lineage artifact also fails, the primary child failure remains authoritative and the publish failure is carried in parent metadata. A child that otherwise succeeded still fails closed if required lineage finalization fails.

## Authoritative proof boundary

The Devtool-native `roadmap-dist-g1` workflow contains five ordered stages: source syntax; full X01/X02 window audit; retained federation scheduling/lineage/cancellation/observability; retained chroot/PRoot/OCI/SSH transport substrate; and a real fresh-process cursor/replay proof. The real-process stage launches two independent Python processes over one durable cursor/journal boundary and proves identical acknowledged replay, same-sequence/different-body rejection, and exact resume from origin sequence N+1.

The gate also proves one live subscriber update per accepted semantic event across reconnect catch-up, bounded/non-blocking publication with journal-backed overflow recovery, secret-safe semantic frames plus raw-stream references, cursor/snapshot impossible-ordering failure, secondary delivery truth, sequential/parallel federation semantic/terminal parity, and preservation of a primary child failure through secondary finalization failure.

## Validation

Construction-host Devtool workflow with an explicit host execution override: **194 passed, 0 failed, 0 skipped; 5/5 stages; 0 warnings; 0 errors.**

Additional retained sweep across Git parent/child lineage, hierarchical federation, EXO event/store authority and SSH history/lifecycle: **86 passed, 0 failed.** `python -m compileall -q` and `git diff --check` are clean.

The production artifact must run `./devtoolw dist-g1` under its required `native-termux` validation environment. Construction success is not campaign closure; only the user's native-Termux artifact apply closes 42/70.

## Baseline isolation

The four OCI-05 embedded Android-builder digest mismatches discovered during X02 reproduce on the pre-X02 base and remain owned by OCI/ANDROID. DIST-G1 neither hides nor repairs that unrelated baseline issue.

## Promise disposition

Machine-readable closure is in `DIST_G1_PROMISE_LEDGER.json`. Remaining later-DIST obligations are in `DIST_G1_EXPORTED_OBLIGATIONS_TO_LATER_DIST.json`. No SSH/tmux/hierarchy/control promise is silently sealed by this gate.
