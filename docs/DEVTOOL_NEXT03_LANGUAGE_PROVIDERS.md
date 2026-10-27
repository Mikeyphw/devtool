# NEXT-03 — Source-bound hosted language provider foundation

**Implementation date:** 2026-10-21  
**Status:** source candidate, offline tests; **not** authenticated hosted CI, native Android/Termux acceptance, independent consumer acceptance or release qualification.

NEXT-03 builds on A03/A09, the MP03 native DAG, NEXT-02 host eligibility and source-bound EXO evidence. It **does not** add another scheduler, runner, dispatcher or artifact registry. There are two independently implemented built-in, version-1 adapters: `go` and `python-shell`. No untrusted/third-party entry-point or dynamic provider code is loaded.

## Commands

```sh
# Read-only capability inventory: reviews target-local workflows
devtool --repo REPO actions provider-doctor --json

# Source/lockfile/graph/host eligibility review; no writes or CI dispatch
devtool --repo REPO actions provider-plan --target TARGET --workflow WF --devtool-sha EXACT_40_CHAR_SHA --json

# Review-only by default; no existing workflow overwritten
devtool --repo REPO actions provider-export --target TARGET --workflow WF --devtool-sha EXACT_40_CHAR_SHA --json

# Explicitly write a NEW source-reviewed GitHub workflow, no commit or dispatch
devtool --repo REPO actions provider-export --target TARGET --workflow WF --devtool-sha EXACT_40_CHAR_SHA --yes --json

# Verify exact generated workflow bytes (does not trust the remote or modify state)
devtool --repo REPO actions provider-export --target TARGET --workflow WF --devtool-sha EXACT_40_CHAR_SHA --verify --json
```

The generated file is `.github/workflows/devtool-TARGET-WF-providers.yml`. Operator must review, commit and push it and ensure the pinned Devtool runtime commit is available before manually dispatching. Existing A03 adoption is unchanged. Export deliberately does **not** write a wrapper alias or dispatch remote jobs. It refuses dirty checkouts, symlink destinations, changed source between checks or an existing target workflow file.

## Contract and limits

1. The native target-local DAG (`graph_from_workflow`) remains the dependency authority. Export creates **one hosted job** with one `ubuntu-24.04` x86_64 matrix cell, provisioning reviewed Python and optional Go and invoking Devtool's named DAG once. This preserves dependency ordering without pretending that individual nodes have an independent remote execution bridge.
2. Every DAG node is independently checked against NEXT-02 host OS, arch, privilege/device/signing, toolchain and command policy before **any** GitHub workflow is generated. Only explicit argv command/python/go target-local jobs, rooted at the repository, are accepted. Unknown runners, nested/federated DAGs, shell expansion or shell-command jobs, arbitrary programs, `python -c`, unreviewed Go commands, inline code, custom env and unsupported host profiles are refused.
3. Go v1 admits `go test`, `go vet`, `go build` with tracked `go.mod`, declared Go toolchain directive and optional `go.sum`; cross-compilation, go.work, CGO, ABI and executable verification are reserved for NEXT-04. Python-shell v1 admits bounded `python -m pytest/unittest/compileall/ruff` with a pinned setup-python Action and 3.12 interpreter channel; dependencies/lock enforcement, coverage and package building are NEXT-04.
4. The frozen plan checksum covers native node/dependency identity, original argv and inputs digests, provider identity, tracked config and lockfile content, host profile and exact pinned runtime. Since committing the generated workflow necessarily changes Git HEAD, the plan checksum is **content-bound rather than pre-export-HEAD-bound**; the generated GitHub workflow separately asserts that checkout HEAD matches GitHub's event SHA. The installed pinned Devtool runtime recalculates the plan checksum **before** invoking the DAG.
5. `workflow_dispatch` only, minimal read permissions, SHA-pinned checkout/setup-python/setup-go/upload-artifact actions, no secrets or write token, `cache=false`/no dependency cache, conservative full-DAG selection (no unproven changed-target skipping), evidence-only upload with run/attempt, source and invocation receipts. No signing, artifact authenticity, test-report attestation, device or release `PASS` is inferred.
6. `provider-doctor` is read-only and reports eligibility, not a universal claim that unknown projects are compatible. Existing G5 fixture compatibility remains independently defined. The provider API is an internal versioned explicit registry; future adapters must pass the same negative/conformance suite.

## Still open

- **NEXT-03 remaining acceptance:** actual external Go/Python source repositories, authenticated GitHub workflow run/attempt and artifact collection, plus independently verified platform/host provenance. GitHub Actions runner matrix is currently one reviewed cell, not an arbitrary cross-OS or cross-repository matrix. Native DAG scheduling is preserved but not translated into per-node GitHub `needs` edges. DT-035/041/042/053/055 are **partial**, not complete across all languages.
- **NEXT-04:** Go/Python full project/dependency/lock tooling, generated binaries/wheels and substantive report/ABI verification.
- **NEXT-07/08:** .NET, Rust, Node, JVM and other providers, complete matrix/changed-target caching and report producer authentication.
- **Independent gates:** R09 hosted, native device, consumer, formal security and release gates remain separately open.

## Test entry points

`PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 pytest -q tests/test_next03_language_providers.py tests/test_next02_evidence_kernel.py tests/test_actions_a03_adoption.py tests/test_actions_a09_ci_host_contract.py tests/test_actions_g05_polyglot_ci.py tests/test_core_x04_generated_surfaces.py`

Tests cover two provider adapters, native DAG dependency projection, source lock and generated-workflow-commit checksum stability, exact artifact bytes on verify, no-readonly-mutations, dirty/symlink refusal, root/device/signer admission, arbitrary code/runner/unknown module denial and A03/A09 backwards compatibility. No remote CI run is claimed.
