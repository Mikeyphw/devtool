# ACTIONS G0 + G1 — Consolidated provider reliability overlay

## Frozen scope and source identity

Source snapshot: `e72335640aab0db58ae96c3e319a6bcec1e2ac19`, from 2026-10-13 native Termux source TAR. See `ACTIONS_G00_G01_EVIDENCE_INDEX.json` for three workflow SHA-256 hashes, original roadmap digest and immutable observed run/artifact IDs. The 88-obligation historical baseline is copied verbatim to `ACTIONS_BASELINE_OBLIGATIONS.json`. These files **do not** upgrade historical results to `PASS`.

## Implemented source changes (G1)

1. Read-only HTTP 204/outcome-unknown dispatch candidate inspection through the existing `actions resume --request-id HEX` operation. It checks repository, workflow ID, ref, observed commit, `workflow_dispatch` event and bounded time. It uses at most the 100 most recent runs and **never** automatically binds candidates or repeats POST; even a unique candidate is unproven. The existing correlated receipt path remains unchanged. Explicit `actions view --run-id ID` is the safe manual observation path.
2. Repository capability signals are strictly hints; GitHub Actions write permission is not provable from repository push permissions. The API's dispatch decision remains authoritative, requiring `--yes`. No new provider, token store or privileged GitHub access is introduced.
3. Job trees use the exact GitHub run attempt's jobs endpoint (`/attempts/N/jobs`) where available. Cross-attempt jobs are rejected instead of mixed. If the API report reaches 200 jobs the output identifies possible truncation. Run source comparison now checks workflow and branch on top of SHA when the remote supplies those fields.
4. Generic artifact downloads remain non-extracting, SHA-256 checked and unqualified. Validate remote attempt identity when API supplies it; registered artifact verification rejects different later run attempts. ZIP member paths reject noncanonical aliases such as `a//b` and `a/./b`, preserving existing casefold collision and CRC checks.
5. New provider/recovery, artifact trust, and evidence-contract tests are shipped. All network interactions in the embedded validator are mocked. Existing ACTIONS-10 unsigned APK/signature code is unchanged.

## G0 / G1 exit status — source qualification versus live acceptance

**Source qualification:** On-device apply must pass the included Actions regression suite with required tests, source guards, and a single clean commit. Test results and warnings are authoritative, not declared in advance.

**G0 live items still blocked:** Required Textual runtime and source-integrity checks on Termux; high-impact original 5k+ suite is not retested here, historical V105 G3 ledgers not rewritten. Native Gradle debug baseline remains historically verified, not rerun.

**G1 live items still blocked:** Read-only GitHub API access for a private repo with current credentials; permission/SSO/rate-limit negative matrix; reproducible 204 or ambiguous transport with exact request journal; moved ref; expired/artifact digest tamper behavior; external provider+registry consumer interoperability. They cannot be truthfully demonstrated in an offline simulated apply. No release qualification is claimed.

The paid GitHub Free private branch protection/ruleset/signing-secret requirement is explicitly excluded, as requested. No branch protection bypass or signing-policy relaxation occurs.

### Read-only device qualification commands

```
devtool -r "$HOME/Code/devtool" actions diagnose --json
devtool -r "$HOME/Code/devtool" actions workflows --json
# Only for an actual uncorrelated dispatch request ID:
devtool -r "$HOME/Code/devtool" actions resume --request-id YOUR_HEX_REQUEST_ID --json
```

Do not generate a fresh dispatch just to produce an HTTP 204 case; test if one occurs during normal development. Never blindly retry a request with unknown outcome.

### Next milestone

G2 — Canonical mixed-provider DAG and recovery (parallel ready set with concurrency bounds, cancellation and restart lineage). Real artifact producer→consumer acceptance remains required before G2 promotion.
