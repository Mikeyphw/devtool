# NEXT-04/05-R1 — reviewed consumer adoption draft

`devtool adopt-batch draft /absolute/path/input.json` reads an operator's small input file and emits a read-only **proposal** containing an exact Git HEAD/tree/branch/remote and overlay SHA-256 for every selected checkout. Its output is not an approval, GitHub run receipt, or adoption action. The underlying NEXT-05 source-locked planner and transaction engine remain the only authorities for `plan`/`apply`/`resume`.

## Example input

```json
{
  "schema": "devtool.multi-repo-adoption-input.v1",
  "repositories": [
    {
      "id": "goapp",
      "path": "/absolute/checkout/of/goapp",
      "overlay": "/absolute/path/to/reviewed.overlay.zip",
      "remote": "origin",
      "wrapper": true,
      "distribution": {
        "target": "app",
        "workflow": "ci",
        "devtool_sha": "REPLACE_WITH_EXACT_40_CHARACTER_REVIEWED_DEVTOOL_COMMIT"
      }
    }
  ]
}
```

`distribution` is optional. When supplied, draft reuses the existing NEXT-03 native provider review and NEXT-04 `ci/devtool-distributions.json` contract inspection on the selected **source**. It does not run Go, Python, Gradle or shell builds; does not install wheels; does not upload evidence; and does not infer artifact authenticity or producer qualification. The `devtool_sha` value is an explicit operator-reviewed Devtool source pin, never auto-fetched.

## Operator workflow

1. Ensure each checkout is on its reviewed branch and clean; choose reviewed overlay ZIPs and absolute paths. Never paste credentials into remote URLs.
2. Run `devtool adopt-batch draft /absolute/input.json --json`. Review the returned `data.assessments`, `data.consumer_source_reviews`, exact Git identities, overlay digests, and `data.manifest`. `data.safe_to_apply` means only *local source planning readiness*.
3. Save **only** the `manifest` object into a new reviewed JSON file. With `jq`, for instance: `devtool adopt-batch draft /absolute/input.json --json | jq -e 'select(.data.safe_to_apply == true) | .data.manifest' > /absolute/reviewed.json` (the operator must check `safe_to_apply == true` first; do not pipe blocked outputs into a live manifest).
4. Run `devtool adopt-batch plan /absolute/reviewed.json` against the still-current clean checkouts. Only after explicit review and preserved source identity may the operator separately authorize existing `apply`/`resume`.

The standalone Python entry point also offers `python3 -m devtool.adoption_batch draft /absolute/input.json --manifest-only`, writing only the manifest to standard output if its source review passes. The top-level CLI emits the complete review JSON, not a silent auto-approval.

## Safety and acceptance limits

- No source changes, Git writes, temporary manifests, journals, network POST, packaging, device/root/signer operation, or auto-push occur during draft.
- Duplicate JSON keys, duplicate/nested checkout roots, symlinks, dirty checkouts, malformed source IDs, untracked or changed inputs, credential-bearing remote URLs and mismatched overlays fail closed.
- The subsequent native adoption plan is independently run against derived identities. Multi-repository commits cannot be globally atomic; interrupted batches require durable per-repository reconciliation.
- A passing distribution source review does **not** prove consumer acceptance, installed-wheel smoke, Go native-device compatibility, CI run/attempt identity, signing, or release eligibility. All remain separately unqualified until independent evidence exists.
- For the real NEXT-04 acceptance gate, use current pinned Go and Python consumer repositories and an independently authenticated hosted run with substantive test evidence, installed-wheel smoke, and verified distribution identity. The source-only demo fixture is not that gate.
