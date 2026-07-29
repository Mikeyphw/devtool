# Phase 47C-2/47C-3 Android HUD lint cleanup

This overlay cleans the next two lint buckets after the Phase 47C-1 security-lint hotfix.

## 47C-2: obsolete SDK checks

The Android HUD minSdk is 26, so guards for SDK 21, 23 and 26 were removed where they only selected dead code paths:

- notification builders/channels now use the API 26 channel constructor directly
- overlay permission checks no longer carry pre-M runtime fallback logic
- foreground-service starts no longer branch around pre-O behavior
- progress tint setters no longer branch around pre-L behavior
- Tasker completion foreground service callback no longer branches around pre-O behavior
- Termux RUN_COMMAND permission checks no longer carry pre-M fallback logic
- obsolete `mipmap-anydpi-v26` resources are deleted; adaptive icons remain in `mipmap`

## 47C-3: notification XML strings and baseline alignment

Notification custom-layout hardcoded text was moved into `strings.xml`, and the expanded notification metric row now disables baseline alignment.

This patch is lint-only. It does not change repository actions, Git behavior, debug recorder semantics, bridge authentication, root/chroot execution or support-bundle redaction.
