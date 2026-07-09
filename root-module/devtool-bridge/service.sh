#!/system/bin/sh
MODDIR=${0%/*}
DATA_DIR=/data/adb/devtool-bridge
mkdir -p "$DATA_DIR/runs" "$DATA_DIR/logs" "$DATA_DIR/bin"
chmod 0700 "$DATA_DIR" "$DATA_DIR/runs" "$DATA_DIR/logs" "$DATA_DIR/bin"
cp -f "$MODDIR/bin/devtool-root-bridge" "$DATA_DIR/bin/devtool-root-bridge"
chmod 0700 "$DATA_DIR/bin/devtool-root-bridge"
printf '%s\n' "${MODDIR}" > "$DATA_DIR/module_path"
printf '%s\n' "ready" > "$DATA_DIR/state"
chmod 0600 "$DATA_DIR/module_path" "$DATA_DIR/state"
