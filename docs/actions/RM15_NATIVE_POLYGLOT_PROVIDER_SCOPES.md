# RM15 — Reusable native Node and mixed JVM scopes

This feature lives upstream in Devtool's real RM-11/RM-12 providers and the
NEXT-03/RM-06 native DAG. It is **not** a StudyFlow-specific task adapter.

## Node/npm: tracked nested project roots

- Set `ci_toolchain = "node"`, `ci_node_version = "22"`, and `ci_node_project_dir = "web"` in the owning target's native job metadata.
- Keep the job's `root = "."` (or omit it). Devtool's native runner switches to
  the reviewed nested project only *after* checking the frozen provider plan,
  exact Git source and each source-directory component for symlinks.
- `web/package.json` must be tracked, declare `packageManager = "npm@10.9.4"`
  (or another supported exact pinned manager), and have its own tracked
  lockfile. Lifecycle hooks and arbitrary scripts remain refused by RM-11.
- Declare outputs as **repository-relative** paths, for example
  `web/dist/test-results.xml`, `web/dist/manifest.json`, or `web/dist/package.tgz`.
  Native JUnit/build/package verification takes place at the same reviewed
  nested project root. This avoids falsely treating `dist/` from another
  sibling project as a successful artifact.
- Native workflows are exported through Devtool's existing `provider-export`
  / native DAG path; no second GitHub scheduler is introduced.

## JVM/Gradle: module-scoped review in mixed repositories

- Set `ci_toolchain = "jvm"`, `ci_jvm_mixed_module = ":desktop"`,
  `ci_jdk_version = "21"`, and a compatible pinned `ci_jvm_tool_version`.
- Job command must remain a reviewed literal, e.g.
  `gradle --offline --no-daemon --console=plain :desktop:test`.
- Only the selected module and its statically discoverable Gradle `project()`
  dependency closure are inspected. All modules in the closure must have
  recognized JVM plugins and tracked scripts; Android/unknown-plugin modules
  inside the closure are **refused**. Unrelated Android siblings are not
  mistaken for JVM modules.
- Dynamic `include()` declarations, mismatched module selectors, symlinks,
  missing dependency verification metadata and unreviewed dynamic Gradle
  commands fail closed. This does not automatically admit every Android/JVM
  composite build: convention plugins and cross-target build logic may need
  a later explicit reviewed provider extension.

## Artifact integrity

Use `python3 -m devtool.actions_artifact_provenance --archive saved.zip --sha256
sha256:<exact GitHub artifact metadata digest> --out evidence.json` to compare
actual **downloaded ZIP bytes** against a known digest, inspect path/symlink/
size constraints, and bind SHA-256 of each archived file. It does not execute
or extract any file. GitHub/CI authenticity and signer/device qualification are
separate from this byte-integrity check.

## Qualification and rollout

1. Run local source unit tests and the existing RM-11/RM-12/NEXT-03 regressions.
2. Qualify an opt-in generated hosted native workflow on an independent Node
   consumer and a mixed Android/JVM Gradle consumer.
3. Only then adapt StudyFlow's `web/` scripts (which currently invoke Python
   and chained npm commands) and convention-plugin Desktop project. Neither
   can be silently declared first-class under the present reviewed grammar.
4. Keep StudyFlow's authoritative CI, release signing, protected environments,
   and real-device acceptance unchanged until independent parity and rollback
   gates have passed.

This is not a signing, remote-authentication, required-check cutover, or release
qualification. Existing root-level Node/JVM jobs retain their default behavior.
