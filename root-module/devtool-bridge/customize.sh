#!/system/bin/sh
SKIPUNZIP=0
ui_print "- Installing optional Devtool Root Bridge"
ui_print "- No Zygisk injection and no system partition files"
ui_print "- The Devtool Live HUD app must still be granted root access"
set_perm_recursive "$MODPATH" 0 0 0755 0755
set_perm "$MODPATH/bin/devtool-root-bridge" 0 0 0755
set_perm "$MODPATH/action.sh" 0 0 0755
set_perm "$MODPATH/uninstall.sh" 0 0 0755
