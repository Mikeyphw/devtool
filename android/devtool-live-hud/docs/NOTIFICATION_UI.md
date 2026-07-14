# Android notification UI contract

The live-run notification uses custom collapsed and expanded `RemoteViews`; it must not fall back to a stock progress or inbox template for an individual run.

The collapsed surface contains the Devtool mark, state dot and badge, contextual task title, active workspace/status, percentage, and state-colored progress rail. The expanded surface adds the artifact, workspace position, three phase rows centered around the active or failed phase, active/remaining workspace state, Tests/Warnings/Errors/Elapsed metrics, and exactly three context actions.

State colors and action labels are defined in `notification-ui-contract.json`. `Notification.InboxStyle` is allowed only for the grouped multi-run summary. Android System UI may add its own app label, timestamp, expand affordance, and outer padding around the custom content.
