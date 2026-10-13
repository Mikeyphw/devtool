# G4 actual-device AAB import: unsigned jarsigner status hotfix

## Observed live behavior

G4 Android AAB workflow `38445139596`, attempt 1, on source `5f2c99e667696467ee93b29e516718d444b1170c` succeeded; it uploaded GitHub artifact `11837083625`. The Termux operator ran `actions android aab-import` with bundletool 1.18.3 (`DEVTOOL_BUNDLETOOL_JAR` set), but Devtool returned `Expected unsigned AAB but JAR verification passed`. The downloaded GitHub artifact ZIP is cached at `.devtool/artifacts/github-downloads/github-Mikeyphw_devtool-run38445139596-artifact11837083625-5dd2efdfbcd493f1.zip`.

## Root cause / bounded fix

`jarsigner -verify -strict` **can return 0 on an entirely unsigned archive**, printing `jar is unsigned.`. This is reproducible with a real OpenJDK jarsigner and an unsigned ZIP. The prior code treated `returncode == 0` as proof of *signed* status, rejecting a valid candidate even though the ZIP structure had no JAR signature entries.

The corrected validator:

1. Keeps the existing bounded ZIP, CRC, path, and `META-INF/*.SF/*.RSA/*.DSA/*.EC/SIG-*` signature-entry rejection **before** invoking jarsigner.
2. Invokes `jarsigner -verify -strict` and requires **both** process success and exactly one explicit `jar is unsigned.` output line, without contradictory `jar verified` status. An error return or ambiguous report fails closed. The process exit status alone is not an unsignedness signal.
3. Continues to require a configured bundletool JAR, bundletool `validate`, exact application ID/versionCode, source-bound GitHub run/artifact identity, receipt digests, unit-test results, and release lint.
4. Leaves LIFE signing, registry trust, signed AAB verification, and the `qualified_release: false`/`source_attested: false` policy unchanged.

The hotfix adds regression cases for **actual unsigned OpenJDK jarsigner output**, unsigned-marker ambiguity, nonzero tool status, contradictory signed markers, ZIP JAR-signature entries, and existing bundletool checks.

## Retest with cached GitHub run (no rebuild)

From a clean repository after the hotfix has successfully committed:

```sh
cd "$HOME/Code/devtool"
OUT=$(cat "$HOME/.local/state/devtool/real-work/latest")
export DEVTOOL_BUNDLETOOL_JAR="$HOME/.local/share/devtool/bundletool/1.18.3/bundletool-all-1.18.3.jar"

devtool -r "$PWD" actions android aab-import \
  --run-id 38445139596 \
  --artifact-id 11837083625 \
  --expected-sha 5f2c99e667696467ee93b29e516718d444b1170c \
  --json > "$OUT/g4-retest-import-after-jarsigner-fix.json" \
  2> "$OUT/g4-retest-import-after-jarsigner-fix.err"
```

This defaults to no explicit `--download` request and **revalidates the prior cached download** against GitHub artifact identity before any unsigned AAB is materialized. If cache was removed, add `--download` only after confirming there is no registered copy. Check output `data.registry_id`, `data.archive_verified`, `data.bundletool_verified`, `data.package.signing_state`, `data.qualified_release`. Do not mark G4 fully passed until import and (if desired) LIFE signing evidence passes on Termux. The original failed and successful runs remain in testing history.

## Evidence scope

The overlay's Linux/Termux tests and native apply simulation validate the source fix, not this user's device. Retesting the cached AAB through actual Termux jarsigner and bundletool is the live acceptance step. No GitHub re-dispatch, credential upload, or AAB signing is performed by this overlay.
