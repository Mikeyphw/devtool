# Termux validation and test-environment audit

This pass addresses the 46 failures reproduced by running the complete Devtool
suite from a live Termux session.

## Findings

The failures were not 46 independent regressions. They came from four shared
causes:

1. Ordinary tests inherited the developer's live Termux markers, global
   configuration, clipboard bridge, HUD run identity, and chroot context.
   Desktop-neutral tests therefore selected Android chroot execution, loaded
   user validation defaults, or rejected synthetic Gradle events.
2. Termux detection treated every path below `/data/data/com.termux/` as a live
   Termux home. Temporary test directories below `$PREFIX/tmp` were therefore
   misclassified even after the test replaced `HOME` and `PREFIX`.
3. Auto-chroot selection did not account for repositories outside the configured
   host-to-guest path map. Auto mode selected chroot and failed later with a path
   leak instead of keeping the command on native Termux.
4. Diagnostics parsing rebuilt repository `Path` objects for every issue. On
   phone storage, 25,000 diagnostics exceeded the UI performance budget.

## Corrections

- The pytest fixture now supplies an isolated desktop-neutral environment by
  default. Tests that exercise Termux, chroot, Android bridge, or global config
  opt in explicitly.
- Termux host detection now recognizes the canonical Termux prefix and home,
  rather than any arbitrary path under the application data directory.
- Auto-selected chroot execution falls back to native Termux when the repository
  is not mapped. Explicit chroot requests remain strict and fail preflight.
- Diagnostics parsing uses one lexical string-path formatter per output batch.
  Public path formatting behavior remains unchanged.
- Desktop Android runner tests now declare their desktop environment explicitly.

## Verification

- Normal Linux: `802 passed, 36 subtests passed`
- Simulated live Termux with ambient run ID and Termux paths:
  `802 passed, 36 subtests passed`
- The 25,000-item diagnostics benchmark fell from roughly 700 ms to roughly
  80–125 ms on the packaging host. The original phone log measured about
  3.1 seconds before this optimization.

## Live-proot follow-up

A real Termux device has `proot` installed, so three runner tests could reach
`subprocess.Popen` successfully and launch the actual binary before their mocked
`subprocess.run` fallback was used. The tests now force the fallback path
explicitly. This keeps production execution untouched while preventing unit
tests from starting host tools.

Verification after the follow-up:

- Normal Linux: `803 passed, 36 subtests passed`
- Simulated live Termux: `803 passed, 36 subtests passed`
