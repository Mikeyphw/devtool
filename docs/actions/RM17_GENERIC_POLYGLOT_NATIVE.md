# RM17 — Generic native polyglot provider source review

RM17 extends upstream RM-11/RM-12 and the existing NEXT-03/RM-06 DAG. No
consumer-specific adapters, hidden GitHub schedulers, signing, or device gates.

## Node: opt-in reviewed-composite scripts

An exact manager `packageManager` pin and lockfile are mandatory as before.
For a nested JS/TS package, leave native job `root` at the repository root and
set metadata:

- `ci_toolchain = "node"`
- `ci_node_project_dir = "web"`
- `ci_node_version = "22"`
- `ci_node_script_policy = "reviewed-composite"`
- `ci_python_version = "3.13"` if the composed script uses Python

The static source reviewer recursively expands literal `npm run <helper>` (or
selected package-manager equivalent), accepting *only* literal ` && ` chains,
tracked `node <file>.mjs/.js`, tracked `python3 <file>.py [--literal-flags]`,
and explicit `python3 -m unittest discover -s tests -p 'test_*.py'`.
Every referenced source file is Git-tracked and included in the provider plan
SHA. Scripts with escapes, redirections, environment substitution, process
substitution, unknown commands, network installers, lifecycle hooks, or cycles
are refused. Npm dependency installation still uses `--ignore-scripts` and
frozen locks. At runtime, project directory, manager, reviewed scripts, Git
state, source digest and owned outputs are checked again. Default mode remains
`strict` without behavior changes for existing consumers.

**Limit:** The existing RM-11 test provider still requires an owned JUnit or
Jest JSON report. Plain `unittest` console success does **not** become an
attested test receipt; consumers need an explicit tested report adapter before
native `test` cutover. This is independent of the new source review policy.

## JVM: checked-in convention plugins

Opt in to the existing `ci_jvm_mixed_module` selector. When an included Gradle
module uses an unknown literal plugin ID, RM17 attempts a *bounded read-only*
lookup of `pluginManagement { includeBuild("build-logic") }` and checked-in
`gradlePlugin` registrations under immediate included-build child directories.
All registered plugin IDs, implementation-class paths and literal
`pluginManager.apply(...)` dependencies are checked. The selected module's
closure must resolve to a JVM plugin. Android application/library/test plugins,
unresolved IDs, dynamic included builds, dynamic convention application and
cycles are refused. The exact convention registry, implementation files,
module source and dependency/verification files are source-hashed for the DAG.
A `com.android.lint` plugin is marked as Android-adjacent; this is not a license
to run Android tasks locally. JVM/Gradle execution remains GitHub-hosted only.

**Limit:** This does not claim all Compose Desktop packaging tasks are
supported. A hosted *real consumer* acceptance and the existing JUnit/JAR
receipt semantics are required before approving a CI or release cutover.

## CI and qualification

- `devtool-rm17-polyglot-source-gate.yml` is PR-triggered and manually
  dispatchable. It runs the new hostile fixtures plus RM-11/RM-12/RM-15,
  NEXT-03 and RM-06 native DAG regressions, producing JUnit and SHA-256 evidence.
- The repository's full 24-shard Python regression is a separate mandatory
  check. Source-gate PASS is *not* independently a release certificate.
- Real GitHub-hosted Node/npm and Gradle consumer acceptance remains a
  subsequent gate. No local Gradle, .NET, signer or device acceptance is run.
- Release signing, artifact authenticity, protected environments, real-device
  acceptance and authoritative consumer CI remain unchanged.
