# RM-06 — Native DAG to reviewed GitHub Actions multi-job export

**Status (2026-10-22):** Locally implemented and tested, pending user Termux application and independent GitHub-hosted acceptance. **RM-06 remains partially open** for bounded optional matrix expansion, cache policies and ownership-proof affected-target selection (joint RM-06/RM-07 interface). Do not count it as fully closed until those promises are implemented and validated.

## User interface

```sh
# Read-only plan; default mode remains single-job.
devtool -r REPO actions provider-export --target TARGET --workflow NAME --devtool-sha PIN40 --mode multi-job --plan --json
# Explicit source-reviewed write of a NEW workflow; never commits/pushes/dispatches.
devtool -r REPO actions provider-export --target TARGET --workflow NAME --devtool-sha PIN40 --mode multi-job --yes --json
# Byte-identical generated output check.
devtool -r REPO actions provider-export --target TARGET --workflow NAME --devtool-sha PIN40 --mode multi-job --verify --json
# Existing single-job runner remains the default and can be selected explicitly.
devtool -r REPO actions provider-export --target TARGET --workflow NAME --devtool-sha PIN40 --mode single-job --plan --json
```

The 40-digit pinned Devtool source SHA **must contain this RM-06 module and the matching provider contract**, and must be available from the configured GitHub Devtool repository. After this overlay is committed locally, publish it according to your normal reviewed workflow before generating any hosted multi-job CI. The tool does not publish the runtime or generated workflow by itself.

## Contract

`ExportGraphV2` (`schema_version = 2`) is a content-stable projection of the validated native target-owned workflow. Each node preserves its native key, target-local job, typed provider and explicit predecessors. A→C with independent B emits `needs` **only** on C. GitHub executes the approved node's `build_direct_job_specs` using Devtool's existing `run_specs` path; the compiler does not launch a second scheduler. Generated YAML pins checkout, setup-python, optional setup-go, upload-artifact and download-artifact by full immutable action commit SHAs; write permissions, credential persistence, unreviewed execution mode, arbitrary shell job and unsafe host requirements remain refused by the existing provider adapter.

The runner verifies the GitHub event's SHA equals checkout HEAD, and verifies the recompiled graph digest before executing. Outputs are optional, exact relative POSIX regular files (not directories, globs, hidden files, symlinks or tracked source). A dependency artifact includes SHA-256 for every declared file and records graph digest, generated workflow SHA-256, producer node/job, consumer commit, GitHub run ID and attempt. All downloaded manifests and bytes are checked **before any file is copied**; overwrites, path traversal, symlink paths and conflicting producer mappings fail closed. Handoff files are uploaded under a deterministic per-job artifact name only if the producing job succeeds. No artifact receipt is an independently authenticated attestation merely because it has a digest or came from a workflow run. Public release readiness, device provenance, signing and consumer qualification remain unknown.

The initial multi-job host is the existing reviewed `ubuntu-24.04` x86-64 environment. Cache is disabled, fanout is a single OS cell, and changed-target selection is full DAG until ownership evidence is reviewable. There is an explicit `single-job` fallback; **the exporter does not silently drop unsupported DAG nodes or change hosts**. Compile refuses graphs over 24 nodes, more than 64 file outputs/node, multiple producers of one output, implicit generated input dependencies, unreviewed globs and output handoff over 512 MiB. Python dependency and package build profiles remain covered only by their current provider restrictions; comprehensive toolchain resolution is RM-07/RM-08.

The bridge does not claim remote run acceptance, runner OS qualification, cryptographically signed source provenance, authenticated producer attestation, artifact authenticity, or release qualification. On other hosts or unsupported toolchains, use the reviewed single-job fallback if it is eligible or refuse and wait for RM-07.

## Locally verified behavior

- Native DAG branching/independence and `needs` edge projection; deterministic graph and workflow bytes.
- Read-only plan vs explicit write vs byte-verify vs no overwrite; old single-job path unchanged.
- Source and graph drift rejection; wrong GitHub run/attempt/source/producer rejection; digest and symlink tampering refused before workspace mutation.
- Declared outputs only; duplicate producer collision, unproven generated dependency and unsafe output path rejection.
- Native direct-job resolver validated through both mocked adapter and a real local Python `unittest` process; zero-test runs correctly fail. No real hosted Actions run.
- No source-level remote authentication or signed-artifact claims.

Behavior tests: `tests/test_rm06_graph_export.py`; cross-regression: `test_next03_language_providers`, `test_next04_distributions`, `test_next02_evidence_kernel`, `test_rm05_artifact_convergence`, `test_rm04_workflow_retry`, `test_rm03_exo_lineage`, `test_core_x04_generated_surfaces`. Installed Termux/GitHub qualification remains separate.
