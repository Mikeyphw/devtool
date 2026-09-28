# ENV-X02 Promise Audit Loop — 2026-09-28

## Scope and authority

This audit reconciles ENV-X02 against the M004 ENV merged program, the ENV-X02 roadmap boundary, the existing mise/Poetry/direnv source audit, and the requested asdf extension. It is an implementation audit, not a green-test shortcut: claims are separated into delivered behavior, preserved upstream authority, and foundations that remain owned by later ENV gates/overlays.

Campaign position: ENV gate-window 2/3; ENV 2/7; full plan 12/63. The canonical 63-item order is unchanged.

## Pass 1 — v1 promise/accounting audit

The v1 overlay was not complete enough to ship as the final ENV-X02 candidate.

Reproduced gaps:

1. mise trust/exact-version evidence was partly inferred rather than fully qualified.
2. Poetry receipts omitted semantic inputs including manager version, groups, extras and lock policy.
3. direnv authorization depended too heavily on Devtool marker variables instead of passively reading existing direnv authorization state.
4. provider PATH verification covered mise incompletely and did not cover the provider family consistently.
5. active direnv overlays were treated as more portable than could be proved.
6. `.tool-versions` was incorrectly usable as mise auto-ownership despite being a shared asdf surface.
7. Python source-audit inspection leaked extensionless-script file handles.
8. ENV-P029 was over-accounted as an X02 delivery even though protected Devtool namespaces are an ENV-X01 authority consumed and regression-proved here.
9. ENV-P050 wording was stronger than the report evidence; destination allocation needed explicit execution-plan proof, not only a path helper.

Disposition: v1 superseded; all implementation-owned gaps folded into the replacement candidate. Ledger accounting corrected.

## Pass 2 — asdf/provider and direnv hardening

The requested asdf extension exposed additional same-boundary issues:

- `.tool-versions` ownership must be deterministic when mise and asdf are both available.
- asdf plugin remotes may contain credentials/query tokens and must be sanitized before receipt persistence.
- `ASDF_*_VERSION` overrides are execution provenance and must affect identity.
- direnv must fail closed when an authorized overlay tries to replace an asdf-owned tool/version.
- direnv auto mode must remain optional when direnv is unavailable; an installed-but-blocked direnv is ACTION_REQUIRED, not silently ignored.
- asdf configurable tool-version filenames must participate in discovery/identity.
- `ASDF_CONFIG_FILE` must be absolute when explicitly set; invalid configuration fails before execution.
- requested asdf versions cannot be relabeled as resolved versions when `asdf current` cannot prove them.

Disposition: fixed and regression-tested in the replacement candidate.

## Pass 3 — provider identity and environment-snapshot trust

The audit then found additional evidence defects:

- provider verification compared only PATH text and allowed a provider to disappear after qualification;
- same-path provider replacement was not detected;
- direnv status-derived global configuration identity was computed and discarded;
- absent `DIRENV_CONFIG` could accidentally produce a fake `.` configuration identity;
- explicit asdf environment snapshots could inherit the controller's ambient HOME for `.asdfrc` / data-dir defaults.

Disposition: provider identity now includes executable content identity and fails closed on disappearance/replacement; direnv global config identity is preserved correctly; explicit asdf environment mappings no longer inherit ambient HOME.

## Pass 4 — promise-by-promise reconciliation

### Delivered by ENV-X02

- ENV-P025..ENV-P028 — canonical composition object, typed role separation, semantic ordering, policy/provenance consumption.
- ENV-P030..ENV-P042 — direnv/mise/Poetry ownership, secret-safe identity, fail-closed conflicts, provider identity preservation, Python request/resolution truth.
- ENV-P044..ENV-P048 — safe manager auto-selection, interpreter provenance/probing, explicit-environment truth, validator ambient-PYTHONPATH isolation.
- ENV-P050 — destination Python allocation plan is emitted before restore/execution using destination-owned repo-relative environment roots; controller-only environment paths are stripped before SSH transport. Actual destination execution snapshots remain P043/X04 work.
- ENV-P057..ENV-P064 — Go request/resolution, launcher-vs-toolchain truth, GOTOOLCHAIN/workspace evidence, selector safety, structured events and destination-owned build/cache planning.

### Preserved upstream authority, regression-proved in X02

- ENV-P029 — protected Devtool runtime namespaces remain owned by ENV-X01. X02 consumes the same safety boundary and tests that its composition overlays cannot create a competing authority.

### Foundations intentionally not claimed as final closure

- ENV-P043 — PythonExecutionSnapshot type exists; destination-returned actual execution snapshot remains ENV-X04/adoption work.
- ENV-P049 — validator isolation/project-runtime foundations exist; ENV-G1 must prove fresh-process runtime ownership, and later ENV work closes full dependency bootstrap policy.
- ENV-P051..ENV-P056 — recursive source/lock/cache/ownership/explain lifecycle is ENV-X04 completion scope.
- ENV-P065..ENV-P071 — full Go provider matrix, CGO/cross-target, cache ownership, module security policy, returned snapshots and advanced qualified actions remain ENV-X04.
- ENV-P068/P069/P072 have partial foundations only and are not marked final.

## asdf extension ownership

No new program and no new overlay are created. asdf extends M004 ENV:

- ENV-X02: provider selection, shared-marker ownership, AsdfQualification, no-hidden-mutation execution, `asdf exec`, exact current-version evidence, config/version-file provenance, override provenance, provider identity verification, plugin-remote redaction, Python/Go integration.
- ENV-G1: authoritative fresh-process asdf fixture coverage and promise audit.
- ENV-X04: destination-returned asdf snapshots, ASDF_DATA_DIR/remote ownership completion, legacy-version-file qualification and explain/diff/doctor completion.
- ENV-G3: final local/Termux/chroot/PRoot/SSH cross-substrate asdf disposition.

## Validation closure

The first-class Devtool qualification identity remains `roadmap-env-x02`. After the final audit fixes, the bounded direct and Devtool-native suite is 268/268 green. The same test identity is used by artifact `validation.tests`; the parent Devtool apply lifecycle owns validation, evidence, rollback and commit.

The known noninteractive terminal-clear/TERM defect is not an ENV-X02 failure and remains owned by PRES.

## Pass 5 — pyenv first-class provider extension

The user requested pyenv as a first-class provider rather than a `.python-version` filename heuristic. The audit added:

- Python-only pyenv auto/explicit ownership;
- `PYENV_VERSION`, `PYENV_DIR`, `.python-version`, legacy `.pyenv-version`, active-version and source provenance;
- actual `pyenv which` runtime/manager resolution;
- `pyenv exec` composition without hidden install/rehash/version mutation;
- pyenv + Poetry separation of runtime vs environment/dependency ownership;
- provider drift/replacement and direnv override conflicts;
- `system` pass-through semantics.

Disposition: `ENV-P117`–`ENV-P124` delivered in ENV-X02; destination/lifecycle/gate work assigned to `ENV-P125`–`ENV-P127`.

## Pass 6 — composition provider vs runtime provider truth

Provider integration exposed four additional evidence bugs:

1. Go planning could qualify system `go` before mise/asdf wrapping and then record the wrong runtime provider.
2. A selected mise/asdf layer managing only an unrelated tool could still be labeled as Python/Go runtime ownership.
3. asdf plugin identity was conflated with executable identity (`golang` plugin vs `go` executable).
4. asdf/pyenv `system` pass-through could be labeled as provider-owned runtime.

Disposition: composition-provider and per-language runtime-provider identities are now distinct; provider-declared runtimes must resolve locally or fail closed, plugin and executable IDs are preserved separately, and system pass-through keeps runtime ownership truthful. These are `ENV-P128`–`ENV-P131`.

## Pass 7 — final closure audit

Re-ran the complete bounded ENV-X02 qualification through the first-class Devtool test identity `roadmap-env-x02`: **268 passed, 0 failed, 0 skipped, 0 warnings, 0 errors**.

Final accounting:

- implementation-owned X02 promises are delivered and covered by the first-class test;
- `ENV-P029` is correctly recorded as consumed ENV-X01 authority, not re-delivered by X02;
- snapshot/destination/lifecycle promises remain explicitly pending under ENV-X04;
- ENV-G1 remains a separate authoritative fresh-process gate and gains asdf+pyenv/cross-provider fixtures;
- campaign order/count remains 63.


## Pass 8 — final execution-environment and asdf-override reconciliation

A final promise-to-code pass against the delivered `ENV-P039`, `ENV-P109`, `ENV-P110`, `ENV-P122`, and `ENV-P128` contracts found two remaining gaps in the v2 candidate:

1. provider re-verification in `apply_composition_to_spec()` used only `CommandSpec.env`. In normal Devtool execution that object is an overlay, not the full process environment, so a provider qualified from parent/config PATH could be falsely reported as `MISSING` before execution;
2. `ASDF_<TOOL>_VERSION` values were recorded in `version_overrides` and therefore changed receipt identity, but did not participate in `selected_tools` when no version file existed. Devtool could therefore execute under asdf while falsely recording Python/Go runtime ownership as system-owned.

Disposition: the render boundary now accepts the authoritative qualification/execution environment and overlays per-command values on top before provider identity re-verification. Toolchain wrappers pass the same parent + config + spec environment used during planning. asdf version overrides are merged into canonical selected-tool truth before `asdf which/current` qualification, with override precedence over version-file requests.

Exact regressions were added for both failure modes. The complete bounded direct qualification is now **270 passed, 0 failed**. The artifact continues to use the first-class Devtool `roadmap-env-x02` validation identity; the Linux construction sandbox cannot execute the repository's `native-termux` target, so authoritative live first-class execution remains the parent Devtool apply on Termux.

No additional X02-owned promise gaps were found in this bounded reconciliation. Later-owner obligations remain unchanged: ENV-G1 fresh-process provider fixtures, ENV-X04 destination-returned snapshots/lifecycle/explain surfaces, and ENV-G3 cross-substrate final disposition.
