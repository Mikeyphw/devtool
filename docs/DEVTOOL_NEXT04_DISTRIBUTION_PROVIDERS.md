# NEXT-04 — Go/Python source-bound distribution candidates

**Date:** 2026-10-21 · **Acceptance:** locally implemented and falsification-tested; no real hosted, consumer, signing, device, or release authority.

NEXT-04 extends the NEXT-03 reviewed Go and Python adapters with a **tracked, versioned output inventory**, rather than inventing a second builder or scheduler. A target-local Devtool DAG still produces every output. The new independent, read-only verifier checks only the exact artifacts declared by that DAG. **Build success alone does not establish artifact verification, authenticity, runnable platform support, test success, or a release seal.**

## Example tracked contract

Create `ci/devtool-distributions.json` in the consumer repository and commit it. Replace the sample identities, commands and paths with the repository's actual definitions:

```json
{
  "schema_version": 1,
  "target": "app",
  "workflow": "ci",
  "distributions": [
    {
      "id": "linux-cli",
      "kind": "go",
      "path": "dist/hello",
      "module": "example.org/hello",
      "goos": "linux",
      "goarch": "amd64",
      "cgo_enabled": false
    }
  ]
}
```

The corresponding DAG must own the exact output path with an explicit `go build -o dist/hello ...` job, with `job.env` declaring only reviewed `GOOS`, `GOARCH`, `CGO_ENABLED` if needed. Go builds may target Linux/Windows/macOS amd64/arm64 when supported; CGO is admitted only for native hosted Linux/amd64, and **no** native Termux/Android qualification is inferred. Tracked `go.mod`, optional `go.sum` and root-owned `go.work`/`go.work.sum` are hashed into the plan. The Go verifier checks ELF/PE/Mach-O headers, declared architecture, and Go build-info module identity without executing the candidate binary. These checks can be forged and are **not** source-to-binary reproducibility or a signature.

For Python, use `kind: "wheel"` or `kind: "sdist"` with exact `path`, `name`, `version`, and wheel `entrypoints: []` or a list of declared console script names. `pyproject.toml` must declare static `[project]` name/version. A Python packaging node runs `python3 -m build --wheel|--sdist --no-isolation --outdir dist` and owns its exact declared output(s). The reviewed hosted job installs only dependencies in **tracked** `requirements.txt` using pip `--require-hashes`; the file must include a hash-pinned `build` package and all transitive build/backend requirements needed by that consumer. Alternative lockfiles are recorded but do **not** silently replace the verified requirements policy. Cache is disabled. No unreviewed build isolation download is admitted.

Wheels are scanned for unsafe/duplicate entries, metadata name and version, wheel tag, declared scripts and exact SHA-256/size coverage by their `RECORD`. Sdists are inspected without extraction, refusing links/special members, unsafe paths and metadata mismatch. The verifier limits archive members and expanded member sizes, enforces conservative candidate paths under `dist/`, and compares relevant tracked manifests/lockfiles against Git HEAD before verification. Candidate outputs are refused if already present before the DAG starts; successful output checks are needed before the generated Actions workflow uploads **only the manifest-declared paths**. No generic `dist/**` glob is used.

## CLI

```sh
# Read-only tracked contract and source policy (does not require build outputs):
devtool --repo REPO actions provider-distributions --target TARGET --workflow WF --plan --json

# Read-only on-disk verification after the native DAG created candidate bytes:
devtool --repo REPO actions provider-distributions --target TARGET --workflow WF --json

# Existing NEXT-03 frozen plan/export automatically incorporate a present contract:
devtool --repo REPO actions provider-plan --target TARGET --workflow WF --devtool-sha EXACT_40_CHAR_SHA --json
devtool --repo REPO actions provider-export --target TARGET --workflow WF --devtool-sha EXACT_40_CHAR_SHA --yes --json
```

The generated workflow requires a Devtool commit that **contains NEXT-04**; the overlay user's unpublished local commit is not necessarily retrievable by `git clone` from GitHub. Commit/push/distribution publication remain operator-owned. The emitted local JSON reports `remote_source_authenticated=false`, `hosted_run_attested=false`, `signatures_authenticated=false`, `native_device_qualified=false`, and `formal_release_qualified=false` regardless of candidate hash verification. No new trusted evidence producer was introduced.

## Safety / limitations

- The existing A03/A09 and NEXT-03 workflow behavior remains unchanged when the consumer has no distribution contract. The same native DAG and GitHub Actions runner own builds and test commands. No job executes during `--plan` or `provider-distributions --plan`.
- Disallowed: undeclared artifacts, multiple owners of an output, unexpected source/manifest drift, unsafe paths and symlinks, wrong GOOS/GOARCH, unsupported CGO tuple, malformed or incomplete wheels/sdists, mutable dependency policy, dynamic Python package version, and missing outputs.
- This does **not** prove installed-wheel import/smoke behavior, independently authenticated GitHub run/attempt, a complete pytest/coverage/Ruff or Go test/vet/race acceptance report, cross compiler availability, reproducible binary bytes, SBOM, signing, consumer integration or a qualified release. Those are unresolved NEXT-04 external acceptance or later source extensions. Keep DT-043/044 **partial** until real-source qualification.
- A source-only synthetic Go project is compiled and inspected in the local tests; Python wheel/sdist fixtures are synthetic and tested for adversarial metadata and archive corruption, **not** claimed to be actual Python package builds. Build hashes in test fixtures are not valid deployable dependency locks.

## Validation

Focused suites: `tests/test_next04_distributions.py`, `tests/test_next03_language_providers.py`, `tests/test_next02_evidence_kernel.py`, `tests/test_actions_a03_adoption.py`, `tests/test_actions_a09_ci_host_contract.py`, `tests/test_actions_g05_polyglot_ci.py`, canonical CLI/surface gates. Disposable source-bound application is required before calling the overlay installable. Real consumers (xbsctl/BKX; dotcli/mike-cli-suite), authenticated hosted runs and actual distribution/install-smoke checks are **pending**.
