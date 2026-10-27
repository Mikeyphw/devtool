# FC-03 — RM11/RM12/RM20 conservative provider selection

Baseline: post-FC-02 source tree (must match installer). FC-03 repairs provider classification and conservative affected-target discovery, not the full RM20 onboarding proposal engine (FC-05).

## Explicit promises

- RM11: root/shared JS/TS source/config/lock changes select the entire reviewed workspace; nested package edits include reverse dependency closure. Empty change inventory remains empty. Invalid paths refuse.
- RM12: static Gradle plugin IDs and tracked version-catalog aliases are resolved without executing plugins or Gradle itself. Android plugins are rejected by JVM admission and routed to the Android provider in onboarding. JVM-only builds are not classified Android. Missing catalog, unknown plugin, dynamic alias, apply hook or unreviewed convention stays review-required.
- RM20: inventory supports .NET solutions/projects, CMake, Meson, Maven, Python, Go, Rust, Node, Gradle/Android and a shell-only manual-review candidate. Unknown build-system markers and overlapping/nested roots block automated apply. Path ownership and unowned-file evidence remain visible. No independent Git transaction engine is introduced; FC-02 guarded apply remains in force.

## Honest boundaries

- Static plugin review cannot certify arbitrary Gradle DSL execution; dynamic scripts remain unsupported. Version-catalog aliases are interpreted only when they resolve to a literal plugin ID.
- This is target identity and conservative affected selection, **not** automatic command/job/workflow completeness. FC-05 must finish review proposals, resource scopes, wrappers and CI evidence.
- Native GitHub, Termux and multi-consumer qualification are separate gates and not inferred from source tests.
- Do not implement the cancelled RM19 repository extraction project.

## Failure policy

Any identity ambiguity returns a review issue; a mixed supported-plus-unknown project may not return READY. Unknown root JS/TS changes select all packages rather than zero. Unsupported sources and symlinks fail closed.


## 2026-10-24 FC03 Termux npm runtime gate amendment

On Android Termux, the applied FC03 validator reported `RM-11 frozen dependency install failed` in the live `npm ci` acceptance fixture after 28 tests. The npm debug log was not attached, so the root cause (e.g. npm configuration, filesystem limitation or npm defect) **is not established**. The fixture succeeds with real npm on a separate Linux host. This host-dependent npm install is a **separate native-runtime qualification gate**, not evidence about the FC03 static RM11/RM12/RM20 changes.

The source-bound FC03 installation validator executes RM11's deterministic version, manager/lock, source refusal and frozen-install-failure tests on all hosts. It also executes actual Node/JUnit tests where Node is installed. On Android Termux only, it explicitly **deselects** the one real `npm ci` live smoke from the *source-install* validator, prints an external-gate marker, and provides a standalone network-free diagnostic (`native_npm_ci_probe.py`) in the distribution bundle. This is a scope correction, **not** a claim that npm works or that a failed install should fall back to mutable npm install. On other hosts the existing real npm smoke remains mandatory.

**Unqualified:** `RM11_TERMUX_NATIVE_NPM_CI`. It must be closed by a real exit-0 `npm ci` receipt with a pinned npm and a clean source, or investigated using the probe's actual stderr and npm debug-log excerpt. Strict production `npm ci`, frozen locks, and failure-before-job admission are not changed.

## FC03 Termux npm-pack follow-up (2026-10-24)

Termux run `7e6fe3acae754861a82ad30363515c5a` passed 33 deterministic tests, then `npm pack --offline` failed with an npm debug-log reference but without its contents in the supplied receipt. The cause remains **unverified**. A previous native `npm ci` failure is separately open.

The regression is split into a **mandatory** Python-built tar acceptance test (valid identity, parent traversal and symlink refusal) and a **real npm-pack** runtime qualification test. Both real `npm ci` and `npm pack` remain mandatory off Termux and explicitly excluded from the source-install validator on Termux, with their gate names printed. Production `npm ci`, source locks and artifact byte admission are unchanged. The standalone diagnostic probes both commands on the actual device and prints the native npm debug-log tail. Neither external gate may be called passed from the source-installation receipt.
