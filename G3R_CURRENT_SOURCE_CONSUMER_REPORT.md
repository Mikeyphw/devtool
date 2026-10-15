# G3R V1 — current-source evidence consumer

## Scope and result vocabulary

The independent G3R consumer checks an entire current-source evidence ZIP or
extracted directory against a clean Git checkout at the **exact** recorded
commit and tree. It independently recomputes historical-vs-current Android/LIFE
roots, 3,893 R04B source bindings (count varies as source evolves), nine
presentation surfaces, all original manifest digests, Git HEAD blob hashes,
obligation structure, process log hashes, and the ZIP's SHA256SUMS inventory.

- `PASS_CURRENT_SOURCE_ONLY`: source reconciliation passes; native lanes have
  not been proven by this invocation.
- `PASS_CURRENT_SOURCE_AND_NATIVE_LANES`: all 13 named Android G1/G2,
  LIFE cleanup chain, and semantic process receipts report PASS, have zero exit
  codes and SHA-matching retained logs; the run declares Android/Termux.
- Any mismatched commit/tree, dirty checkout, changed original, missing lane,
  failed process, suspect ZIP member, unresolved R04B reference or incorrect
  historical/ledger receipt results in **failure** (exit 2).

**Neither PASS status proves final G3 source-seal requalification or a full
release.** Native execution provenance here is asserted by collected process
receipts, not cryptographically attested hardware. Device instrumentation,
signing, external providers and six carried real-substrate obligations remain
open: `ENV-A06`, `ANDROID-A16`, `DIST-A01`, `DIST-A12`, `DIST-A13`, `TEST-A11`.

## Install / qualification flow

Apply the source-bound overlay to the expected, clean branch/commit (see the
bundle README). **Do not run the previously collected 74890f83 candidate
against the newly changed Git HEAD.** Commit the overlay using Devtool's
normal transaction/commit workflow before generating new evidence.

On Termux, generate a fresh candidate using the previously supplied
`devtool_current_evidence_v2.zip` runner and its `run.sh`, then run:

```bash
python3 -m devtool.g3r_evidence_gate \
  --repo "$HOME/Code/devtool" \
  --candidate /path/to/new-candidate.zip
```

For source-only analysis, use `--source-only` (does not qualify native lanes).
CI runs contract tests on hosted Linux; it is not a replacement for Termux.
The manual GitHub workflow can consume an externally uploaded artifact from a
prior Actions run, but cannot manufacture native Termux evidence itself.

## Historical retention

Do **not** modify `ANDROID_G3_FINAL_ATTESTATION.json`,
`LIFE_G3_FINAL_ATTESTATION.json`, `CORE_G3_FINAL_ATTESTATION.json`,
`ENV_G3_FINAL_ATTESTATION.json`, or R04B historical binding manifests.
Old 11 failures and 352 stale historical bindings are not suppressed or
reclassified as passing. The G3R candidate is **new evidence**.

## R04B source lookup correction

`remed_r04b_fixed_point._git_value()` previously returned an empty string when
Git rejected an archive restored with a foreign UID. It now configures
`safe.directory` for the exact caller-supplied repository only for that Git
invocation, without changing global Git settings, and fails explicitly when
Git cannot return a nonempty value.
