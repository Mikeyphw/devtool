# FS03 final release seal implementation report

FS03 closes the MP01–MP10, UI01–UI12, and FS01–FS02 roadmap with a reproducible repository-wide release audit.

## Delivered

- Added `devtool final-seal` / `devtool seal` with text and versioned JSON output.
- Added `--strict` release gating.
- Added the `final-seal` validation workflow profile with required artifacts/external verification.
- Audits canonical `.devtool.toml`, workspace/default-target integrity, compatibility registry uniqueness and migration state, public schema versions, roadmap reports/tests, docs/examples, completion synchronization, tracked backup hygiene, and release discoverability.
- Added dedicated FS03 contracts and final release documentation.
- Completion, CLI help, manpage, README, architecture overview, and changelog are updated for the final seal.

## Release contract

The command is side-effect free. It complements rather than replaces runtime validation: `final-seal` checks repository promises and static release invariants, while artifact/validation gates execute pytest, Android Gradle, lint/package, and transactional apply/rollback evidence.

## Full-suite findings closed by FS03

The repository-wide test inventory exposed five compatibility regressions that the focused roadmap gates did not cover:

1. Toolchain doctor workspace analysis treated unspecified attributes on loose `Mock` callers as truthy workspace scope flags. FS03 normalizes `group`, `all_targets`, and `affected` to argparse-default semantics for programmatic callers.
2. Shared UI responsive policy had drifted from the historical terminal breakpoints. TUI is again compact through 60 columns, narrow from 61–96, and wide from 97 columns onward.
3. GTK/Android source-level parity tests still depended on historical literal fallback strings after semantic UI centralization. Compatibility markers are retained where external source-contract tooling still recognizes those literals.
4. Older Android/installer contracts still asserted legacy `kind`/`type`, `--project-type`, and `mock-v1` notification metadata even though FS01/UI10 intentionally moved producers to canonical `runner`/`platform`, `--runner`, and `semantic-v2`. Those release contracts now assert the canonical producer while legacy parsing remains covered separately.
5. The public `TestDiscoveryResult` dataclass looked like a pytest test class during repository-wide discovery. FS03 marks the API model `__test__ = False`, preserving its name/API while making the final inventory warning-free.

## Rebase findings closed

Rebasing FS03 onto the 2026-09-11 14:30 snapshot exposed additional FS01/FS02 drift in the live baseline. The rebased seal restores the canonical repository `.devtool.toml`, the complete 24-entry compatibility registry (including artifact, validation-session, transaction-state, and `logs --print` compatibility), the associated documentation/contracts, and the full compatibility CLI/completion categories. These are preserved roadmap promises rather than new compatibility surface.

## Validation evidence

- Complete Python inventory: **2,276 tests + 69 subtests passed**, 0 failed, 0 skipped, 0 collection warnings.
- FS03 command: all repository seal checks pass.
- Compatibility strict gate: no active repository migration.
- Config migration check: canonical repository configuration.
- Generated/static zsh completion: byte-identical.
- Changed Python modules compile successfully.
- `git diff --check`: clean.

The release artifact additionally declares both `devtool` and `android_hud` workspaces so the authoritative Termux apply runs the full Python test target and the Android HUD test target in one transactional validation.
