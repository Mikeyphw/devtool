# RM-08 — reviewed Go and Python provider extensions

The RM-08 overlay extends the existing immutable `go` and `python-shell` providers; it does **not** register arbitrary tools, shell snippets or new hosted runtime authority. The safe fallback remains Devtool's native target-local DAG. See `docs/RM08_PROMISE_AUDIT.md` for a per-promise verdict and specific behavioral tests.

## Go

- A nested module job can declare `command=["go", "-C", "services/api", "test", "./..."]` with a tracked `services/api/go.mod` and exact native job inputs. `go -C` is executed without a shell wrapper. The selected module's `go` directive pins the hosted setup action's `go-version-file` to `services/api/go.mod`.
- `go.work` may contain literal tracked `use` entries; workspace/module versions must agree. External `replace`, traversal, nontracked or symlinked manifests, and conflicting dynamic toolchain directives refuse. Hosted Go sets `GOTOOLCHAIN=local` to prevent undocumented auto-installation.
- `go test -race` needs explicit native job environment `CGO_ENABLED=1` and Linux/amd64. The macOS profile refuses this profile. Other native cross-build environment restrictions remain in `provider_plan()`.
- The real binary receipt regression builds an explicitly declared `GOOS=linux GOARCH=amd64 CGO_ENABLED=0` candidate, even when invoked on Termux (`android/arm64`) or another host. It compares the resulting ELF/buildinfo to the declared target and also refuses deliberately mismatched architecture and CGO declarations. This is a cross-compiled test candidate, not evidence of Android-native runnable executables.
- A nested Go distribution may use `services/api/dist/program` when it matches the job's selected module and exact `go -C services/api build -o dist/program ...` command. Real binary architecture/buildinfo/module and the unchanged committed source are verified before a local bytes-verified receipt is emitted. The executable is not run during structural verification.

## Python

- A native job may set `metadata={ci_python_version="3.11"}` (also reviewed: 3.12 and 3.13). The generated single-job or multi-job workflow pins `actions/setup-python` to its SHA and selects the version for that node. This is not a multi-version Cartesian product.
- For stricter package planning, opt into `metadata={ci_python_policy="reviewed"}`. The project then needs a tracked `pyproject.toml`, a reviewed build backend with exact pinned dependencies, and exactly one source-tracked dependency lock. Hosted installation is currently permitted only through fully hash-pinned pip `requirements.txt`; uv, Poetry and PDM are recognized but not executed without equivalent reviewed installation policy.
- For an opted-in package build owned by an exact distribution contract, set `ci_wheel_smoke=true`. The native-node runner verifies the built wheel, installs it in a temporary venv with `pip --no-index --no-deps`, invokes the **single** declared console script with `--help`, and fails before staging handoff artifacts if any step fails. It does not fetch dependencies, accept unrelated scripts or run arbitrary user-supplied shell code. Consumers whose scripts do not support `--help` should leave smoke disabled until a reviewed contract is available.
- Read-only `merge_coverage_xml(root, paths)` merges bounded coverage.py XML reports with unique file identities and no entities/DOCTYPE, but is not yet automatically invoked by native CI jobs.

## Strict limits

Neither successful local compilation, a YAML file, a clean wheel RECORD, nor a SHA-256 handoff proves independent GitHub producer authority. All local plans, smoke receipts and test evidence remain unqualified for device, signing, external consumer or release gates. Windows hosted export, arbitrary dependency manager execution, source-ownership affected target selection and version cross-products remain open.


## RM-08R2 consolidated replacement additions

**Installation:** Apply this source-bound full replacement to a clean post-RM-07 Git tree; do not install RM-08 then R1 then R2. If RM-08/R1 was already committed, this original source-bound replacement refuses rather than reverting commits or mutating the checkout.

- Go local `go.mod` / `go.work` requirements and tracked local `replace` build a bounded manifest graph (`workspace_edges`, `workspace_build_order`). Remote replace, untracked paths, cycles and implicit DAG producers refuse. No new DAG scheduler is installed.
- Native job metadata `ci_go_versions="1.23,1.24"` (base module must appear) or `ci_python_versions="3.11,3.12,3.13"` expands into version-specific GitHub jobs under `--mode multi-job`. At most three versions, two reviewed hosts and 48 total jobs; mixed-language/inconsistent fanout refuses. Each version cell has disjoint producer IDs, transfer names and dependencies.
- Native Go distribution jobs can opt into `ci_go_reproducible=true` only with `go build -trimpath -buildvcs=false -o dist/file ...` and disabled CGO. The runner repeats the build twice in disposable output locations with external Go network disabled and compares SHA-256 to the candidate. Report is still local-only evidence; nonreproducible builds refuse.
- Strict Python review accepts uv.lock, poetry.lock or pdm.lock only when package pins and archive hashes are reviewable and a separately tracked `requirements-ci.txt` supplies exactly matching fully hash-pinned pip install entries. No manager installer/download is inferred. A normal `requirements.txt` remains an independent pip policy.
- Native `python -m coverage run -m pytest ...` and `python -m coverage xml -o dist/coverage.xml` commands are bounded; declare `dist/coverage.xml` in the reporter node outputs and a native dependency edge to the test job. XML is verified/merged and bound into the direct-node readout before handoff staging. Coverage is opt-in native ownership, not a monkeypatched pytest runner.
- Opt-in `ci_sdist_smoke=true` checks one source distribution owned by the producing node, validates its metadata/archive, provisions a disposable venv backend from `dist/wheelhouse` using the repo's reviewed pip `--require-hashes` lock (offline, no isolation downloads), installs the sdist and runs its single console entrypoint. Symlinked/missing wheelhouse, wrong wheel hashes and unsafe archive paths refuse. Synthetic real PEP517 execution is covered by acceptance tests.

See `docs/RM08_COMPLETION_PROMISES.md` for scope/limits, not just file inventory.
