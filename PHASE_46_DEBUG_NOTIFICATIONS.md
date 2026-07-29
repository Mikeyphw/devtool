# Phase 46 - Debug Notifications

Phase 46 wires the Android debugging system into safe notification cards.

## Scope

- Adds `DebugNotificationController` with a dedicated `devtool_debug_notifications` channel.
- Posts problem-event notifications from `DebugRecorder` after the event has been persisted and assigned an id.
- Classifies debug event families into user-readable notification titles:
  - Devtool launch stalled
  - Bridge roundtrip failed
  - Repo manager needs attention
  - Repo operation orphaned
  - Validation stopped
  - Notification blocked
  - Debug bundle ready
- Adds safe notification actions only:
  - Open Debug
  - Open Doctor
  - Open Validation
  - Copy report
  - Copy health
  - Copy bundle
  - Run bridge probe
- Emits a bundle-ready notification when the Debug Workbench copies or shares Support Bundle v2.
- Extends Support Bundle v2 `notification_health` with the debug-notification channel/group and safe-action labels.

## Safety Contract

Debug notification actions never run destructive Git or filesystem operations. They only open diagnostic screens, copy bounded/redacted text, run the existing read-only bridge probe, or copy/export the redacted support bundle.
