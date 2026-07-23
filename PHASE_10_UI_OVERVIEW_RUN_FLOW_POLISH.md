# Phase 10: UI Overview and Run Flow Polish

This phase applies the next roadmap slice after the HUD parity/autosize and typography/navigation passes.

## Desktop Control Center

- Makes Overview action-first with a primary next-action card before metrics.
- Moves the latest run above snapshot metrics so live work is visible before dashboard counters.
- Converts Overview quick actions to wrapping Flow controls and snapshot metrics to an adaptive grid.
- Adds explicit Overview signals for opening Run and Logs without pretending a run card was selected.
- Adds a dynamic Run stepper: operations that do not need tests show Project → Operation → Review instead of a disabled Tests step.
- Adds progressive disclosure in the Run workflow: Project options, Advanced execution, Advanced output, exact command, and a Danger zone.

## Android Companion

- Reorders Overview around the next useful action, latest run, readiness, quick actions, and advanced setup.
- Keeps repository/status setup available without letting it dominate the first phone screen.
- Adds progressive Run sections for project options, advanced execution, advanced output, and danger-zone choices.
- Updates the Run action bar to show a 3-step flow when the selected operation does not need tests.

## Non-goals

- No HUD detail reduction.
- No notification layout changes beyond the already-applied parity/autosize phase.
- No command-builder behavior changes.
