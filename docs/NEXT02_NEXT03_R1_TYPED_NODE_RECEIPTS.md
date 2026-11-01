# NEXT-02/03-R1 — typed native-DAG node receipts (source integration)

This slice **reuses** the existing NEXT-02 `evidence_kernel.validate_evidence`
and NEXT-03 provider registry, plus RM-06 `compile_graph`, `run_node`,
`stage_outputs`, and `_load_manifest`. It adds no dispatcher, scheduler,
provider registry, signing policy, or device authorization.

## Contract

Every successfully executed **native multi-job DAG node** produces a
`devtool.actions.node-typed-evidence` v1 projection, including:

- Source commit **and actually executed Git tree** (distinct from frozen
  pre-export review tree); frozen graph and compiled workflow SHA-256.
- Target, workflow, exact job and node, reviewed language provider, host cell,
  run ID and **positive attempt**. Mismatches are rejected, never remapped.
- Artifact paths and SHA-256 digests only when previously verified against
  RM-06's exact source/run/attempt handoff receipt and local bytes.
- All NEXT-02 gate domains stated explicitly. Local source/native-command
  success and byte integrity may be **reported** as local PASS; effective
  gates remain **UNKNOWN** without independent producer authentication.
- Test gate stays **NOT_RUN** without an independently parsed test report,
  even for commands named `test` that exited zero. Signature, device, release,
  and hosted authentication remain UNKNOWN/false.

In the real exported GitHub workflow, the native node writes a create-only
`$RUNNER_TEMP/rm06-evidence/<job-id>/node-evidence.json` with private file
permissions. The existing `if: always()` evidence upload includes this file.
If the native step returned success but the receipt is missing, the evidence
step fails. A failed native job has no successful typed node receipt, and the
existing `source-run.txt` breadcrumb **cannot** be interpreted as PASS.

`build_node_evidence()` and `verify_node_evidence()` are read-only in-memory
operations; `write_node_evidence()` writes only the explicitly owned evidence
folder. Synthetic workspaces inside the Devtool regression CI never qualify
for hosted persistence. Each node belongs to its frozen RM-06 graph exactly.
The receipt's self-digest is for local corruption detection, **not a
cryptographic producer signature**; caller-supplied records can never upgrade
an effective trust gate or qualify a release.

## Acceptance and refusal

Targeted validation covers the provider-neutral schema for all nine reviewed
providers; exact commit/source-tree/run/attempt/node/provider/graph binding;
missing test evidence and forged PASS states; native artifact tampering;
replayed handoff attempts; symlink and overwrite rejection; distinct Git review
versus execution trees; and the generated YAML evidence-presence guard.

Full repository Python regression and hosted real-consumer execution remain
**separate**. No local Gradle, ADB, signing, root operations or Actions POST
is performed during application. The tests do not constitute device, CI run
attribution, remote authentication, or release qualification.
