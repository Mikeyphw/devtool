# RM-05 — Artifact and test evidence convergence

RM-05 is read-only. It adds `ArtifactReceiptV2` and `TestShardReceiptV2` manifest readers, format-aware integrity adapters, optional pinned Ed25519 attestation verification, SBOM hash references, and deterministic per-node test shard aggregation. It does **not** grant GitHub identity, remote result authority, Android device acceptance, or release qualification from untrusted local receipts.

## CLI

```sh
devtool -r . artifacts verify --receipt /absolute/path/to/artifact.json --json
devtool -r . evidence merge-tests --receipt /absolute/path/to/test-shards.json --json
```

For both, `--commit <40-hex>`, `--run-id <native-id>`, and `--attempt <int>` can bind explicit caller expectations. Old registry-selector `artifacts verify SELECTOR` remains unchanged. The new receipt mode refuses simultaneous `SELECTOR` and `--receipt`.

## ArtifactReceiptV2

```json
{
  "schema_version": 2,
  "kind": "devtool.artifact-receipt",
  "producer": {"node":"api.ci.build", "source_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "run_id":"native-run-1", "attempt":1},
  "artifact": {"kind":"web-bundle", "path":"dist/site.zip", "sha256":"<64 lowercase hex>", "package_id":"site@1.0"},
  "consumers": [{"node":"api.ci.deploy", "sha256":"<same artifact hash>"}],
  "attestation": null,
  "sbom": {"path":"dist/sbom.json", "sha256":"<64 lowercase hex>"}
}
```

Expected SHA-1 and attempt refer to **receipt identities**, not independently trusted remote producers. Consumer declarations must bind the producer's exact artifact hash; they do not authorize or execute a consumer. Attestation is `UNSIGNED` when absent, `SIGNED_UNVERIFIED` when no pinned key is supplied, or `VERIFIED` only after actual Ed25519 verification using a caller-pinned raw public key via the Python API. A signed payload must bind the exact producer and digest; no trust is inferred from a receipt's `key_id`. A verified pinned-key signature still does not qualify a release or verify GitHub ownership.

File kinds: `apk`, `aab`, `jar`, `zip`, `web-bundle`, `nupkg`, `wheel`, `sdist`, `tar`, `archive`, `go-binary`, `native-binary`. Archive types reject malicious paths, duplicate members, symlinks, link-like specials, inflated entries and malformed/CRC-invalid ZIPs; they are never extracted. Wheel `RECORD` hashes are verified with the existing distribution verifier. The portable format checks deliberately **block** claims of compiled APK/AAB package identity absent provider-owned aapt validation; binary header checks do not claim full Go build-info provenance. Full Android provider identity verification is addressed by RM-15; specialized Go host metadata by RM-08. Unknown kinds fail closed.

## TestShardReceiptV2

```json
{
  "schema_version": 2,
  "kind": "devtool.test-shard-manifest",
  "producer": {"node":"api.ci.test", "source_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "run_id":"native-run-1", "attempt":1},
  "shards": [{"id":"linux-a", "node":"api.ci.test-a", "format":"junit", "path":"out/junit.xml", "sha256":"<64 lowercase hex>"}],
  "exo": {"path":"out/execution-events.jsonl", "sha256":"<64 lowercase hex>"}
}
```

Supported formats are `junit`, `pytest-junit`, `js-junit`, `trx`, `go-json` and `cargo-json`. Each shard's SHA-256 is checked against the actual bytes. Malformed/truncated data, an incomplete running test, duplicate terminal identities (even if outcomes agree), contradictory shard identities, changed source/attempt, and missing/contradictory EXO node terminal events fail closed. Output contains deterministic shard and test ordering with each test's node and totals.

`exo` is optional so preexisting test reports can be merged; without it `exo_correlation` is explicitly `UNKNOWN`. With a hash-bound journal the check is *local journal consistency*, not independent provenance. Report `PASS` reflects observed cases, not signing/release qualification.

## Boundaries and integration with RM-06

- RM-05 defines verified handoff *content and identities*; RM-06 must bind those identities to real GitHub `needs` and downloaded artifact bytes before a consumer starts.
- Do not accept `signature.state == VERIFIED` from untrusted serialized JSON. Recompute verification with caller-pinned public key bytes.
- No network, GitHub API access, signing, extraction, mutation, retry or dispatch occurs in these readers.
- This overlay validates the native `devtool` target in-process and runs adversarial behavior tests inside the transaction; the separate standalone CLI invocation is kept outside the self-update hook to avoid recursive Devtool deadlocks on Termux.
