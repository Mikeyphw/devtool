# ENV-X02 — Language Environment Convergence + mise / asdf / pyenv / Poetry / direnv

## Campaign position

- Gate window: **ENV overlay 2 of 3** (`ENV-X01`, `ENV-X02`, `ENV-G1`).
- Current major scope: **ENV overlay 2 of 7**.
- Full implementation plan: **overlay 12 of 63**.
- Merge-window decision: **do not merge** with `ENV-G1` or `EXEC-X01`; the gate and next program remain distinct authority boundaries.
- Base authority: user's successful ENV-X01 commit `8305b8c` (construction base is content-equivalent replay state).
- This report supersedes ENV-X02 v1 and v2 after the final bounded promise audit loop.

## Audit-loop verdict

ENV-X02 v1 was not fully promise-complete, and the v2 audit candidate still contained two execution-truth gaps: provider re-verification could drop the authoritative parent/config PATH, and `ASDF_<TOOL>_VERSION` could affect receipt identity without establishing runtime ownership when no version file existed. The final replacement fixes both without expanding X02 scope. Full details are in `ENV_X02_PROMISE_AUDIT_LOOP.md`.

## Canonical composition and provider truth

- `EnvironmentCompositionPlan` is the single composition object for host/base, authorized environment overlay, selected toolchain provider, language environment, and final invocation.
- Semantic order is host/base → authorized direnv → selected tool/runtime provider (mise/asdf/pyenv) → language environment → invocation.
- Only one toolchain-provider role may own a command.
- Repository overlays continue through the ENV-X01 protected-variable boundary; ENV-X02 does not create a second privilege/config authority.
- Provider identity now includes path, executable metadata and content SHA-256 and is reverified against the same authoritative parent/config/spec execution environment used for qualification. Disappearance, path drift and same-path replacement fail closed without confusing an empty per-command env overlay for the whole process environment.
- Semantic environment domains are merged rather than overwritten so composition/Python/Go/provider identities coexist in later RER/EXO evidence.

## direnv

- `.envrc` presence is inventory only and does not grant authorization.
- Auto mode is optional when direnv is absent.
- Installed-but-blocked direnv is action-required/fail-closed instead of silently ignored.
- Existing authorization is read passively through direnv status; Devtool does not run a trust/allow mutation.
- Authorized export values are reduced immediately to opaque hashes; raw secret-bearing values never enter receipts/logs.
- Active overlays are conservatively nonportable.
- direnv cannot silently replace provider-owned `MISE_ENV`, `ASDF_*_VERSION`, `PYENV_VERSION`, `VIRTUAL_ENV`, or provider executable selection.
- status-derived global configuration identity is preserved; absent `DIRENV_CONFIG` does not fabricate a `.` configuration identity.

## mise

- mise remains a first-class declared tool/runtime provider.
- ordinary execution uses paranoid/no-auto-install behavior and does not trust/install as a hidden side effect.
- qualification records provider identity/version, active config/lock digests, trust state, exact current versions/backends and resolved executable evidence where available.

## asdf extension

asdf is now a first-class M004 ENV provider without changing the 63-overlay campaign:

- explicit `manager = "asdf"` and deterministic auto-selection are supported;
- dedicated mise markers win mise auto-ownership; the shared asdf version-file surface is not silently reinterpreted as mise;
- `ASDF_TOOL_VERSIONS_FILENAME` participates in marker discovery and receipt identity and is constrained to a filename, not a traversal path;
- `ASDF_CONFIG_FILE` must be absolute when explicitly set;
- `ASDF_<TOOL>_VERSION` is both provenance and selection truth: it can establish/override provider-owned tool selection even without a version file, and must be proved by `asdf current`;
- AsdfQualification records provider identity/version, version-file digests, requested tools, exact `asdf current` results, plugin origin, config/legacy policy, `ASDF_*_VERSION` overrides, and data-dir identity;
- requested versions are never mislabeled as resolved when `asdf current` cannot prove them;
- plugin remote credentials/query/fragment are stripped before persistence;
- ordinary execution wraps with qualified `asdf exec` and never invokes hidden `install`, plugin mutation or `reshim`;
- asdf provider disappearance/PATH drift/in-place replacement fails closed;
- direnv cannot silently override asdf-owned tool versions/paths;
- Python/Poetry/Go consume asdf as a toolchain provider without transferring language-environment or scheduling ownership to asdf.

The canonical asdf plan extension is recorded in `ENV_X02_ASDF_ADDITION_AUDIT_20260928.md`. Destination-returned asdf snapshots, legacy-version-file resolution and remote ASDF_DATA_DIR completion remain ENV-X04; ENV-G1 and ENV-G3 own the corresponding gates.

## pyenv extension

pyenv is now a first-class **Python runtime provider**, not a Python environment/dependency manager:

- Python-only auto-selection recognizes `.python-version`; explicit pyenv is rejected for Go/non-Python targets;
- `PYENV_VERSION`, `PYENV_DIR`, `.python-version`, legacy `.pyenv-version`, active versions and observed version source are provenance-bearing;
- `PyenvQualification` records provider executable/version, version-file/source digests, requested vs active versions, resolved Python/manager executables, `PYENV_ROOT` identity and search-root identity;
- `pyenv which` proves the actual runtime/manager executable and `pyenv exec` is the execution wrapper; ordinary Devtool execution never performs hidden install/uninstall/rehash/local/global/shell mutation;
- pyenv + Poetry/PDM/Pipenv/Hatch/venv/uv keeps runtime ownership separate from language-environment/dependency ownership;
- provider disappearance, PATH drift and same-path replacement fail closed;
- authorized direnv cannot silently override `PYENV_VERSION`;
- pyenv `system` is represented as pass-through: pyenv remains in composition evidence while the actual runtime stays system-owned.

The pyenv plan extension is recorded in `ENV_X02_PYENV_ADDITION_AUDIT_20260928.md`; destination-returned pyenv snapshots and destination `PYENV_ROOT` ownership remain ENV-X04.

## Cross-provider runtime truth

The audit also separated **composition-provider identity** from **language-runtime identity**. mise/asdf may wrap a command because they manage some project tool without owning Python/Go. Python/Go runtime ownership is now assigned only when the provider actually declares and resolves that runtime (or declares destination ownership to be proved there). asdf plugin IDs are preserved separately from executable IDs, including `golang` → `go`, and `system` pass-through never becomes false provider ownership.

## Python environment truth

- `PythonEnvironmentRequest`, `PythonManagerReceipt`, `ResolvedPythonEnvironment`, and `PythonExecutionSnapshot` foundation are canonicalized.
- runtime provider, Python manager/environment ownership, restore policy, groups/extras and destination are separate evidence.
- explicit Python environments cannot be silently ignored by manager-owned Poetry/PDM/Pipenv/Hatch.
- manager-owned environments probe the actual interpreter/environment rather than inheriting bootstrap truth.
- Poetry receipts include manager version, lock/config identity, groups/extras, lock policy, runtime provider and actual environment/interpreter.
- Poetry locked mode verifies lock consistency before mutation.
- requirements-only projects do not auto-select uv merely because uv is installed.
- validator execution strips ambient Python ownership variables before declared roots/dependencies are introduced.
- destination Python restore plans use destination-owned repo-relative environment roots before execution; SSH translation strips controller-owned environment paths.
- Python source auditing closes extensionless script handles.
- pyenv is a first-class runtime provider for Python; destination-returned snapshots/root ownership and advanced explain/lifecycle remain ENV-X04 rather than being overclaimed here.

## Go environment truth

- `GoEnvironmentRequest`, `ResolvedGoEnvironment`, and `GoExecutionSnapshot` foundation distinguish launcher identity from effective toolchain/workspace truth.
- GOTOOLCHAIN and go/toolchain directives participate in qualification/provenance.
- `GOWORK=off`, explicit workspace and discovered workspace are distinct states.
- selector classes/tags without native Go translation fail closed.
- changed/path selection uses package/dependency semantics and never raw source paths as `-run` regexes.
- modern `build-output`, `build-fail` and `FailedBuild` events are retained.
- destination build/cache paths are destination-owned; SSH strips controller-owned Go cache paths.
- GOAUTH evidence is digested rather than persisted raw.

## Promise reconciliation

Delivered by ENV-X02:

- `ENV-P025`–`ENV-P028`
- `ENV-P030`–`ENV-P042`
- `ENV-P044`–`ENV-P048`
- `ENV-P050`
- `ENV-P057`–`ENV-P064`
- asdf extension `ENV-P105`–`ENV-P112` and `ENV-P114`
- pyenv extension `ENV-P117`–`ENV-P124`
- cross-provider truth `ENV-P128`–`ENV-P130`

Preserved/regression-proved but not re-owned:

- `ENV-P029` remains ENV-X01 authority.

Foundations not falsely marked final:

- `ENV-P043`, `ENV-P049`, `ENV-P051`–`ENV-P056`, `ENV-P065`–`ENV-P072`, `ENV-P113`, `ENV-P115`, `ENV-P116`, `ENV-P125`–`ENV-P127`, and `ENV-P131` remain later gate/X04/G3 obligations as described in the ledger.

## Bugs discovered and absorbed

The audit loop fixed X02-owned defects rather than creating hotfix overlays:

1. shared `.tool-versions` could be silently captured by mise auto-selection;
2. Python source audit leaked extensionless-script file descriptors;
3. direnv absent/blocked states could be conflated with authorization;
4. asdf plugin remote credentials/query/fragment could leak into receipts;
5. requested asdf versions could masquerade as observed current versions;
6. provider revalidation missed disappearance/same-path binary replacement;
7. direnv global-config identity could be discarded/fabricated;
8. explicit asdf qualification could inherit ambient controller HOME;
9. Go planning could record system Go while execution was actually provider-wrapped;
10. toolchain doctor could report a correctly selected asdf provider as inactive;
11. asdf plugin names were conflated with executable names (`golang` vs `go`);
12. asdf `current <tool>` parsing assumed one output shape and could record a source path as the version;
13. selected composition provider could be falsely recorded as Python/Go runtime provider for an unrelated managed tool;
14. asdf/pyenv `system` pass-through could be falsely recorded as provider-owned runtime;
15. pyenv lacked first-class `PYENV_DIR`/legacy `.pyenv-version` provenance and requested-vs-active mismatch checks;
16. relative `ASDF_DIR` / `ASDF_DATA_DIR` could enter receipts despite asdf requiring absolute locations;
17. provider post-qualification verification could use only `CommandSpec.env`, drop the parent/config PATH, and falsely report the already-qualified provider as missing;
18. `ASDF_<TOOL>_VERSION` could change identity without making that runtime provider-owned when no version file existed.

No new overlay/program was created for these defects.

## Validation

First-class Devtool identity: `roadmap-env-x02`.

Bounded final qualification:

- **270 passed**
- **0 failed**
- **0 skipped**
- **0 warnings**
- **0 errors**

Artifact validation references `test:roadmap-env-x02` directly, so the parent Devtool apply lifecycle owns selection, structured test evidence, rollback and commit. The known noninteractive terminal-clear `TERM` defect remains PRES-owned and does not change the test-stage result.

## Next

- Next canonical overlay after successful apply: **ENV-G1**, full-plan **13 of 63**.
- Remaining before next major scope: **1 overlay**.
- Next major scope: **EXEC**, beginning with `EXEC-X01`.
