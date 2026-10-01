# PRES-G2R — reopened concrete-runtime presentation requalification gate

**Position:** gate-window overlay 4 of 4; full-plan **64/76**.

## Gate purpose

PRES-G2R is a proof gate, not another presentation implementation overlay. It reopens the historical PRES-X04..X08/G1/G2 claims against the concrete runtime delivered by PRES-R01/R02/R03 and the H01/H02 incident corrections. Historical reports remain immutable evidence of what was claimed then; this gate determines which claims are now actually true in the current runtime.

## Audit-loop result before native qualification

The fixed-point audit found no remaining gate-window runtime implementation gap after PRES-R03 v6 qualified on native Termux (`9b5fff37ecd14b9c81d294a00540183b`, 175/175, commit `0fd7d0c`). The gate therefore adds no execution/control-center feature code. It does reconcile stale pending ledgers and strengthens authoritative validation so model/spec evidence cannot satisfy concrete-app promises.

| Reopened claim | Historical limitation | Current concrete evidence required by G2R |
| --- | --- | --- |
| X04/X05 Textual substrate + parity | specs/models did not prove widgets | R02/R03 concrete `DevtoolTextualControlCenter`, real pages/widgets, selection, actions, help/palette, resize and live refresh under `App.run_test()`/Pilot |
| X06/X07/G1 Textual replacement | `devtool tui` selector did not prove normal execution renderer ownership | R01 + H01 + R03 legal parent/main-thread normal-execution Textual host, plain/machine bypasses, no tracker-owned Rich Live, managed child retains execution truth |
| X08/G2 live control center | model/once/key/action assertions could false-positive concrete behavior | R02/R03 real control-center app, real confirmation modals, canonical dispatch, lifecycle-safe refresh/events, and broad Pilot navigation/action matrix |
| Artifact apply main-thread safety | H01 intentionally reopened live hosting | H02 v3 real main-thread apply host Pilot proof with canonical child lifecycle/rollback authority |

## Authority disposition

PRES remains renderer/control-only. RUN/EXO own execution/run state; LIFE owns artifact/overlay mutation; DIST/SSH owns workers; TEST/EXEC/WRAP and validation keep their existing authorities. Generic `artifact.apply`, `target.launch`, and worker cancel/resume stay unadvertised because no safe concrete control contract exists. Overlay apply and stale worker recovery remain confirmed delegated controls.

Legacy terminal/studio HUD renderers that still use Rich `Live` are not reclassified as normal execution presentation. Their explicit migration/deprecation/qualification remains the later **PRES-G3 69/76** cross-surface seal.

## Authoritative gate workflow

`./devtoolw pres-g2r` must run, in order:

1. source syntax;
2. G2R audit/ledger/fresh-process contracts;
3. the historical X04..X08/G1/G2 window;
4. the full R01/R02/R03/H01/H02 remediation fixed point (non-real-app portions);
5. all four concrete native Textual `App.run_test()`/Pilot proofs with Textual required.

The artifact validator exports every `DEVTOOL_PRES_*_REQUIRE_TEXTUAL=1` flag and invokes the canonical wrapper workflow. A missing Textual package or skipped concrete-app proof is a gate failure.

## Next on success

DIST-R01 **65/76** is already qualified/applied. A successful PRES-G2R therefore advances directly to **DIST-G3 66/76**. PRES-G3 remains later at **69/76** after DIST/TEST/EXEC final seals.
