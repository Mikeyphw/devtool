# Android notification UI contract

The live-run notification uses custom collapsed and expanded `RemoteViews`; it must not fall back to a stock progress or inbox template for an individual run.

The collapsed surface contains the Devtool mark, state dot and badge, contextual task title, active workspace/status, percentage, and state-colored progress rail. The expanded surface adds the artifact, workspace position, three phase rows centered around the active or failed phase, active/remaining workspace state, Tests/Warnings/Errors/Elapsed metrics, and exactly three context actions.

State colors and action labels are defined in `notification-ui-contract.json`. `Notification.InboxStyle` is allowed only for the grouped multi-run summary. Android System UI may add its own app label, timestamp, expand affordance, and outer padding around the custom content.

## Validation-session presentation

Validation uses the same compact and expanded `RemoteViews` as normal runs. The system progress bar remains overall phase completion, while the active phase row may show its own progress or an indeterminate state. Expanded phase rows are centered around the active or failed phase, yielding previous/current/next context.

Actions are session controls rather than saved-command reruns. They carry the expected session revision and active attempt ID. A stale action opens the current Validation Workbench state and performs no mutation. Rollback is never exposed in the notification shade.

Phase 31.1 also guards actions with repository identity. Recovery-required sessions show `Recover · Open · Copy status`, and stale repository/revision/attempt actions open the current Workbench state without mutation. The expanded current-phase row consumes numeric `phase-progress` events; the system rail remains overall phase completion. Rollback progress is display-only and never gains a shade action.
