#!/system/bin/sh
MODDIR=${0%/*}
exec "$MODDIR/bin/devtool-root-bridge" status --human
