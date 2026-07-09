#!/system/bin/sh
# Remove executable access immediately while preserving logs for manual review.
rm -rf /data/adb/devtool-bridge/bin
printf '%s\n' "uninstalled" > /data/adb/devtool-bridge/state 2>/dev/null || true
exit 0
