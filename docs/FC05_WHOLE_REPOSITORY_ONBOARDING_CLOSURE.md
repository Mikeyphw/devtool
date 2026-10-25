# FC-05 — RM20 complete reviewed in-place onboarding

**Baseline:** clean post-FC-04 tree `de457660b38c071d940618679d3d574af528204f`.

## Promise audit

| Obligation | Implementation evidence | Status |
| --- | --- | --- |
| RM20-T1 inventory and target ownership | RM20/FC-03 static discovery, tracked path/ownership inventory, mixed/unknown/overlap fail-closed | Source complete; real consumers pending |
| RM20-T2 reviewed full jobs, dependency DAG, resources | `onboard plan --review-spec` requires explicit job argv/inputs/typed output scopes, full target CI workflows, and validates native workspace planner in a scratch Git repo | Source complete; hosted evidence pending |
| RM20-T3 wrappers and existing Actions preservation | Canonical wrapper config parsed; pre-reviewed launcher templates installed exclusively or byte-verified and retained; all tracked Actions YAML must have exact reviewed hashes; none rewritten or duplicated | Source complete; real CI integration external |
| RM20-T4 in-place safe apply | FC-02 isolated Git commit tree, approved byte digests, ref compare-and-swap, index lease, O_EXCL file creation; never reset other commits or delete foreign files | Source complete; consumer acceptance pending |
| RM20-A1 Studyflow variants | Code can inventory only supported unambiguous projects; real TV/web/desktop and Android evidence not supplied | OPEN external gate |
| RM20-A2 BKX Go+Android dual root | Bounded mixed-project fixtures and reviewed CI retention pass; real BKX full acceptance not supplied | OPEN external gate |
| RM20-A3 no repo extraction | Only one local config and two wrapper launcher files; projects and prior CI stay in place | Locally supported |
| RM20-A4 ambiguous/unsupported sources refuse | Unknown marker, nested roots, symlinks, Git drift, absent reviewed job/scopes, unknown CI all refuse | Locally supported |

## Review and apply contract

`devtool -r PATH onboard inventory --json` is read-only and includes `review_template`, source head/tree/branch, every tracked path, ownership, review issues and existing target roots. A discovery-only report is **never** authorization to commit a target-only stub.

Create a **separately reviewed JSON file**, using the emitted `review_template` as a starter. Supply `source` unchanged, `config_toml` with every discovered target (`root`, `runner`, `platform`), reviewed jobs with explicit runner/argv, tracked inputs and typed `output_scopes`, a nonempty `ci` DAG workflow per target and native wrapper commands binding `workflow = "ci"` and an explicit target for every discovered target. Provide `unowned_paths` and `shared_paths` exactly as inventoried. Provide one host/toolchain/qualification record per target, explicitly declare signing/device/release authority (e.g., `PENDING` rather than invented acceptance), and list human review decisions. Declare `existing_actions.policy = retain-existing` and every tracked `.github/workflows/*.{yml,yaml}` path with its full SHA-256. Choose launcher policy `install-if-absent` or `retain-existing`, with both checked template hashes from the starter.

The reviewed JSON is not executed, may reside outside the repository, is size-bounded to 256 KiB and refuses a symlink. Devtool parses the proposal using its real configuration/workflow/wrapper authorities on a **separate disposable scratch root**. It never executes the proposed commands, Gradle plugins, npm scripts, or project hooks during review. The candidate only becomes `READY` if the entire contract validates.

```sh
# Side-effect-free inspection and complete plan
 devtool -r "$PWD" onboard inventory --json
 devtool -r "$PWD" onboard plan --review-spec ~/onboard-review.json --json

# Copy review_sha256 from the plan only after human review of config_toml,
# changed paths, ownership, CI bytes, host/toolchains and external gates.
 devtool -r "$PWD" onboard apply --review-spec ~/onboard-review.json --reviewed-sha256 FULL_64_HEX --yes --json
```

The approved commit can add only `.devtool.toml`, `devtoolw`, and `devtoolw.cmd`, or only `.devtool.toml` if the byte-identical, executable wrapper pair already exists and was explicitly reviewed. No preexisting Actions YAML, consumer source, build outputs or project directories are changed. A concurrent destination file is preserved; a partial exclusive-creation failure reports error and leaves created files for manual reconciliation instead of deleting or overwriting unknown bytes. Only an accepted expected-HEAD CAS updates the branch; index reconciliation holds `index.lock`. Rerunning after commit produces `CONFIGURED` with no second commit.

**CI note:** Existing GitHub workflows are preserved and SHA-checked. `ci` is a native Devtool execution DAG; this phase does not automatically generate, push, dispatch, or qualify new GitHub workflows. A new hosted Actions export requires a separate reviewed admission and pinned-run qualification. Do not misrepresent the presence of a local DAG as live GitHub Actions authority.

## Qualification and portability

Selected FC-05, RM20, FC-03, FC-02, FC-04 and canonical-schema tests run on Linux. Native apply and its three negative transaction checks are separate from real user-device acceptance. Root-owned shared paths are enumerated as unowned or shared and may not be silently attributed to one project. RM19 extraction remains cancelled.
