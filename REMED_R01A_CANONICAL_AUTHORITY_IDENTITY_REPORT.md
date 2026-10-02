# REMED-R01A — Canonical Authority & Identity

## Campaign position

- Roadmap: **REMED-R01A**
- Gate-window position: **1 of 8 remediation implementation overlays**
- Full-plan position: **71 of 93**
- Next overlay: **REMED-R01B — Trust, Resolution & Mutation Closure (72/93)**
- Remaining implementation overlays before requalification gates: **7**
- Next major gate phase: **CORE-G3R through PRES-G3R (79–86/93)**

This is an implementation/remediation overlay, not a G3R seal. Final qualification remains deferred to the fixed-point G3R audit after all remediation overlays.


## Artifact baseline reconciliation (v5)

The user-supplied rebase archive `devtool-20261002-192158-267047.tar.gz` is a **clean worktree** at Git HEAD `80e7e5e09e274cea460dc601696e56332a5dc721` (archive SHA-256 `98d7b758da304f0e8e0d9b7668fe85293aa4d90a96f5799777af78ea1c4b1ae3`). Unlike the earlier donor working tree, it does not contain the monolithic R01 changes.

v5 is therefore rebuilt from the complete R01A production delta rather than the reduced v2/v3 payload. Every `modify` entry is freshly source-bound to bytes from this clean base, and every new path remains an `add`. `devtool/invocation_origin.py` is explicitly included, so the trusted federation/wrapper provenance dependency is closed inside the artifact. No source check is waived or replaced with a compatibility fallback.

## Canonical scope reconstructed before implementation

The authoritative handoff/re-audit material was reread before implementation. The current merged maxima relevant to this overlay are:

- **CORE-P084** (current CORE maximum; M001 retains **336** source obligations)
- **ENV-P131** (current ENV maximum after later amendments; M004 retains **252** source obligations)
- **LIFE-P127** (current LIFE maximum after later extensions; M006 retains **168** source obligations)

The source-preservation universe remains authoritative. This overlay does **not** silently replace or renumber those obligations. It remediates nine findings from the 16-finding R01 family while leaving all untouched obligations retained for their owning G3R source-disposition audit.

### R01 split used by the revised 93-position roadmap

R01A owns:

1. CORE-A01 — CLI registry/parser authority direction
2. CORE-A02 — unforgeable invocation provenance
3. CORE-A03 — canonical repository/global config ingestion
4. CORE-A04 — devtoolw command authority convergence
5. CORE-A05 — universal unique run-instance/RUN-V3 registration
6. CORE-A06 — semantic operation identity separate from run identity
7. CORE-A08 — compatibility aliases omitted from default completion
8. ENV-A02 — recursive Python dependency/environment identity
9. LIFE-A03 — LIFE signing identity authority adopted by Android signing

R01B retains the seven remaining R01 findings: EXEC-A03, ENV-A03, ENV-A04, ANDROID-A12, LIFE-A01, LIFE-A02 and LIFE-A05.

## Production authority changes

### CORE-A01 — canonical CLI schema now compiles argparse

`devtool/core_cli_schema.json` is now the runtime source for both semantic command/option identities and argparse lowering. `devtool/core_parser.py` compiles the parser directly from that checked-in schema. `build_parser()` no longer hand-builds the production parser.

The schema contains 281 semantic command identities and 2,068 option identities. A fail-closed identity projection binds the registry rows to the parser-lowering portion of the same document. The live generated parser has zero missing/extra command or option spellings against the registry.

`adopt_parser_registry()` remains only as a compatibility/parity verifier; it cannot add identities to the canonical registry.

**Status:** `IMPLEMENTED_AND_PRODUCTION_ADOPTED`

### CORE-A02 — private trusted invocation provenance

The user-facing parser no longer accepts `--internal-invocation-source`, `--internal-invocation-command` or `--internal-wrapper-schema`. Wrapper and in-process federation re-entry now pass provenance with `trusted_invocation_context()` rather than forgeable argv.

Historical wrapper/federation regression tests that asserted the old hidden argv mechanism were updated to assert the new trust boundary instead. Their prior expectation was explicitly superseded by CORE-A02 rather than silently ignored.

**Status:** `IMPLEMENTED_AND_PRODUCTION_ADOPTED`

### CORE-A03 — one repository/global config ingestion boundary

Repository and global TOML now cross a canonical parsed document boundary carrying source scope and byte digest. Repository unknown namespaces fail closed except explicit extension namespaces. Global unknown namespaces fail closed while known repository-scoped tables remain backward-compatible inputs that are ignored by global-default projection and diagnosed by config doctor.

Feature, federation, SSH, tmux, UI preference, target-seal and repository final-seal consumers were routed through canonical config loaders where they consume `.devtool.toml`. Direct TOML parsing that remains in the tree is for other file formats (for example version catalogs, validation profiles, examples, migration text or generated configuration), not an independent `.devtool.toml` authority.

**Status:** `IMPLEMENTED_AND_PRODUCTION_ADOPTED`

### CORE-A04 — wrapper command discovery uses CORE registry

`WRAPPER_NATIVE_COMMANDS` is derived from the canonical CORE registry instead of a separately maintained command token set. Wrapper execution lowers back into canonical Devtool argv while provenance travels through the private invocation context.

**Status:** `IMPLEMENTED_AND_PRODUCTION_ADOPTED`

### CORE-A05 / CORE-A06 — universal run instances and separate semantic identity

Every parsed production CLI invocation allocates a non-empty run-instance ID and registers a RUN-V3 row before dispatch. Ordinary inherited `DEVTOOL_RUN_ID` and public child-role flags are no longer root authority. Textual/apply and local parallel federation children inherit a preallocated identity only through a parent-bound, one-shot private handoff capability; in-process federation re-entry uses the trusted invocation context.

Repeated identical root operations produce distinct run IDs while preserving the same semantic operation ID. RUN rows are finalized with terminal status and exit code. Universal JSON exposes the real run ID through machine state.

**Status:** `IMPLEMENTED_AND_PRODUCTION_ADOPTED`

### CORE-A08 — default completion does not promote compatibility aliases

Generated shell completion is projected from canonical semantic paths. Compatibility aliases remain accepted by the parser but are not suggested by default. The checked-in generated surface bundle was regenerated and must remain at a byte-for-byte fixed point.

**Status:** `IMPLEMENTED_AND_PRODUCTION_ADOPTED`

### ENV-A02 — recursive Python dependency identity

Python environment identity now traverses nested requirements/constraints and local dependency source identity. Changing a nested constraint changes the environment fingerprint even when the top-level requirements file is unchanged.

Production consumers in `python_support` / `python_environment` use this identity.

**Status:** `IMPLEMENTED_AND_PRODUCTION_ADOPTED`

### LIFE-A03 — Android signing consumes LIFE identity registry

Production Android signing resolves configured `identity_id` through `SigningIdentityRegistry`, validates purpose/variant applicability and lowers the LIFE record into the existing signing execution profile. Registry identity overrides conflicting legacy keystore/alias fields; nonexistent identities fail rather than silently falling back.

The registry path honors the private `DEVTOOL_SIGNING_IDENTITY_REGISTRY` boundary used by managed execution/tests. Android release, CLI, artifact verification and task validation continue to consume `signing_profile()` / its validation wrapper.

**Status:** `IMPLEMENTED_AND_PRODUCTION_ADOPTED`

## Duplicate-authority / bypass audit

The overlay specifically searched for:

- live argparse-to-registry adoption authority;
- user argv capable of declaring trusted invocation source;
- wrapper/federation child argv carrying internal provenance tokens;
- independent wrapper-native command sets;
- direct `.devtool.toml` TOML readers outside the canonical config boundary;
- root CLI reuse of ambient run IDs, including forged/inherited Textual/federation child-role flags without a one-shot parent capability;
- Android signing paths bypassing LIFE identity resolution;
- Python environment fingerprints that stop at the first requirements file.

No unowned R01A bypass remains in the inspected production paths. R01B deliberately retains artifact trust/resolution/execution-record and other secret/offline/direnv work; this report makes no closure claim for those findings.

## Executable evidence

Local targeted validation on the implementation worktree:

- `tests/test_remed_r01a_canonical_authority_identity.py`: **10 passed**
- retained CORE identity/generated-surface/wrapper tests: **44 passed**
- wrapper/federation provenance and Gradle-ingress regressions: **37 passed**
- config/environment/signing and related retained tests: **173 passed**
- core R01A targeted boundary: **265 passed**
- managed-child/Textual runtime regressions: **28 passed, 2 optional Textual construction tests skipped on the desktop construction host**
- combined construction boundary: **293 passed, 2 skipped**

The artifact validator is first-class Devtool configuration. v5 was materialized over the exact clean rebase archive before packaging; only files present in the artifact inventory were overlaid, and that self-contained targeted boundary passed 293 tests with 2 construction-host-only Textual skips:

```text
./devtoolw remed-r01a
```

It is declared `native-termux`, requires no Gradle/Android SDK build, and runs the same targeted test groups after overlay application. The current packaging host is desktop Linux, so the native-Termux wrapper preflight is intentionally not treated as local qualification; the pytest boundary above is the local executable evidence and the artifact validator supplies authoritative Termux replay.

## Historical/source disposition for this overlay

The M001/M004/M006 source-preservation rows remain authoritative. R01A does not declare the hundreds of untouched source rows newly requalified. Their disposition here is `RETAINED_NOT_REQUALIFIED`; their owning G3R must re-resolve them against final HEAD and current evidence hashes.

The nine remediation findings above are mapped in `REMED_R01A_PROMISE_DISPOSITION.json` to production implementation and exact test nodes. Old regression assertions that depended on forgeable hidden argv are marked `SUPERSEDED_BY_CORE_A02` and replaced with private-context assertions.

## Remaining adoption gaps

R01A does not close R01 as a whole. The next required overlay is **REMED-R01B (72/93)**, which owns artifact trust, resolved-plan/mutation authority, execution-record persistence and the remaining R01 trust-boundary findings. No G3R may seal from this artifact alone.
