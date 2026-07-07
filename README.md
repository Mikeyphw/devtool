# Termux host chroot toolchain backend

This overlay makes devtool keep the UI/control plane on the Termux host while wrapping external command phases such as dotnet restore/build/test through the Ubuntu chroot executor.

It keeps desktop HUD flags working: desktop/desktop-window/gnome/gtk/x11-gtk/auto are normalized to android-gui in host-orchestrated mode. Absolute Termux paths under $HOME are translated to /home/termux inside the chroot.

The legacy full-process chroot runner remains available with DEVTOOL_AUTO_CHROOT_MODE=full.
