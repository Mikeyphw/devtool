# REMED-R03A — Provider & Lifecycle Authority

## Scope and position

- Gate-window position: **5/8** implementation overlay.
- Full-plan position: **75/93**.
- Next overlay: **REMED-R03B — Retention, Cleanup & Repository-Pack Convergence, 76/93**.
- Next major phase after remediation implementation window: **CORE-G3R at 79/93**.

R03A is the first provider/lifecycle half of REMED-R03. It does not claim the full REMED-R03 provider, retention, cleanup, Android advanced execution, TEST distributed execution, or real-provider G3R closure. It closes the destructive-cleanup fail-closed authority gap and makes provider phase lifecycle ownership visible in normal SSH/federation provider metadata so later R03B/G3R work can enforce retention and real-provider qualification without a second provider cleanup authority.

## Delivered promises

### LIFE-A11 — cleanup authority read failures fail closed

Before this overlay, `build_cleanup_ownership_view()` treated unreadable or corrupt canonical protection sources as absence of protection. That meant a destructive cleanup plan could proceed after losing visibility into RUN/RER, artifact-registry, transaction, or validation protection roots.

R03A adds a typed `CleanupAuthorityUnavailableError` and makes `apply_cleanup_plan()` refresh the ownership view with `strict_authorities=True` before mutation. Missing authority stores remain valid for a fresh repository. Existing but unreadable/corrupt canonical authority stores now fail closed before any deletion. v2 additionally verifies active transaction pointers before the ordinary recovery helper can prune malformed markers, and wraps corrupt active validation state as cleanup-authority unavailability rather than allowing an untyped validation exception to escape.

Authorities covered by the strict mutation boundary:

- RUN/RER catalog under `.devtool/private/run-v3/catalog.json`;
- artifact registry under `.devtool/artifacts/registry.json`;
- active artifact transaction state;
- active/resumable validation session state.

### DIST-A11 partial — provider lifecycle ownership is emitted in production phase metadata

Before this overlay, provider cleanup descriptors existed and provider status projections could rebuild them, but normal provider phase metadata did not carry a compact lifecycle authority signal at phase launch. R03A adds `provider_cleanup_authority_metadata()` and wires it into `ssh_phase_dist_metadata()` for both delegated and non-delegated SSH provider work.

Normal SSH provider phase metadata now carries:

- `dist_provider_lifecycle_authority=RUN/LIFE`;
- `dist_provider_cleanup_descriptor_count`;
- `dist_provider_cleanup_digest`.

The digest is derived from canonical `provider_cleanup_descriptors()` for provider staging, provider session state, and event cursor resources. Full descriptors remain reconstructable from provider status and lifecycle helpers; ordinary phase metadata carries only count/digest to avoid creating a second cleanup store or leaking extra remote paths.

This is intentionally **partial DIST-A11**: R03A establishes the launch-time lifecycle authority signal, while R03B still owns universal retention/GC enforcement and final reference release semantics.

## Explicit non-claims / exports

R03A does not claim closure of:

- `EXEC-A09` source-proof retention/GC barrier;
- `ENV-A05/A06/A08` destination snapshot and Gradle broker takeover;
- `ANDROID-A13/A14/A16` advanced Android execution/device/provider matrix;
- `LIFE-A04/A06/A07/A08/A09/A10` remote signing, tar.zst packs, historical commit packs, pack retention, artifact prune lowering, and backup-name cleanup convergence;
- `DIST-A01..A07/A12/A13` real provider parity, transport, cancellation, peer negotiation, session authentication, and reconnect qualification;
- `TEST-A11` real distributed immutable test selection.

Those remain exported to R03B and the later G3R gates.

## Validation

Targeted validation command embedded in the artifact:

```bash
./devtoolw remed-r03a
```

Construction validation performed locally:

```text
python -m py_compile devtool/cleanup_lifecycle.py devtool/distributed_provider_control.py devtool/oci_resilience.py tests/test_remed_r03a_provider_lifecycle_authority.py
python -m pytest -q --tb=short tests/test_remed_r03a_provider_lifecycle_authority.py tests/test_life_x03_generated_cleanup_ownership.py tests/test_life_g2_ownership_retention_gate.py tests/test_dist_x03_provider_control_hierarchy.py tests/test_remed_r02_whole_scope_audit.py
```

Result: **51 passed, 0 failed**.

## Overlay summary

Production ingress changed:

- Destructive cleanup mutation now re-reads protection authorities in strict mode and refuses mutation if a required existing authority is unreadable/corrupt, including malformed active transaction markers and corrupt active validation markers.
- Normal SSH provider phase metadata now carries lifecycle authority count/digest derived from the same provider cleanup descriptor model used by status/reconnect projections.
- OCI/SSH phase metadata allowlists the public lifecycle-authority key so `RUN/LIFE` authority survives redaction without exposing secret values.

Duplicate/legacy authorities removed or lowered:

- Cleanup can no longer silently downgrade corrupt protection stores to “no references observed.”
- Provider phase metadata no longer relies only on later status projections to express lifecycle cleanup ownership.

Remaining adoption gaps:

- Provider cleanup descriptors still need R03B retention/GC enforcement through release/import/reference release.
- Real remote/provider/device/network qualification belongs to G3R gates.


## v2 audit correction

A pre-apply audit found that v1 proved corrupt RUN/RER and artifact-registry protection stores but did not explicitly fail closed on malformed active transaction pointers before the normal transaction recovery helper could prune them. v2 closes that transaction-authority bypass and adds adversarial coverage for corrupt transaction and validation markers. R03A still only fully claims `LIFE-A11`; `DIST-A11` remains partial/exported to R03B for retention and reference-release enforcement.
