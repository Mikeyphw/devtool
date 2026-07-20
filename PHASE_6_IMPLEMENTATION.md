# Android Logs and Repositories Phase 6

Android frontend: **1.13.0** (`versionCode 26`)

## Scope

Phase 6 completes the planned mobile-polish and hardening slice for the structured Logs and Repositories features. It does not broaden the Git security boundary or add background remote probing.

## Adaptive UI

- Central `AdaptiveLayout` policy based on width and system font scale.
- Bottom navigation on phones and a primary navigation rail from 720 dp.
- Repository and exact-log split panes from 840 dp when text is not extra large.
- Vertical action groups for compact widths and font scale 1.3 or greater.
- Extra-large text disables crowded two-pane layouts.
- Adaptive content padding and rail sizing for tablets and foldables.

## Git queue UX

- Running-operation elapsed time with an indeterminate progress indicator.
- Queue position and wait duration for pending operations.
- Same-repository move-earlier and move-later controls.
- Persisted `queue_order` in database schema 6.
- Reordering is performed while holding the repository queue lock.
- Active operations and operations from other repositories cannot be reordered.

## Retention

- Pin and unpin indexed runs from Logs cards and exact-run details.
- Automatic cleanup no more than once per 24 hours.
- User controls for completed-run age/count, completed Git-operation age, and repository-event age.
- Active, orphaned, queued, and pinned records are preserved.
- Optional WAL checkpoint and SQLite compaction.
- Cleanup affects Android-indexed metadata only.
- Errors are returned to the UI instead of stranding the callback.

## Credential guidance

- Passive diagnosis remains unchanged.
- Reviewable copy-only suggestions cover Git/OpenSSH installation, known hosts, SSH keys/agents, remotes, and HTTPS helpers.
- Host values are shell quoted in generated guidance.
- Host fingerprints must be independently verified.
- No automatic `ssh -T`, `git ls-remote`, fetch, or credential prompt.

## Verification

- Android/UI regression contracts.
- Database and root-broker strict Java compilation with warnings as errors.
- Full-source Java syntax pass.
- Adaptive/accessibility instrumentation source for phone, tablet, and large-text profiles.
- XML, JSON, shell, Python, version, and artifact-schema validation.
