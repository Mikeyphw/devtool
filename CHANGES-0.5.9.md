# devtool 0.5.9

- Replaces the default GTK desktop HUD with the native Qt 6 compact HUD when `devtool-control-center` is installed.
- Removes GTK from automatic desktop HUD selection. The former renderer remains available only through the explicit legacy mode `--hud-mode gtk-window`.
- Adds `--compact-hud` startup support to Devtool Control Center so auto-HUD runs open only the detachable Qt HUD.
- Adds reviewed execution for clipboard-detected devtool commands. Commands remain inert until the user opens the confirmation dialog and presses **Run command**.
- Removes light/native `ScrollView` viewport leaks by using a palette-aware application scroll surface and a themed page container.
- Makes the Run, Overview, History, Settings, sidebar navigation, output, artifact bundle, diagnostics, repository, and history overflow areas scrollable.
- Keeps all command execution output inside the Run page and uses the selected repository as the working directory when available.
- Avoids loading GTK for the normal desktop HUD path, eliminating GTK theme parser warnings from standard HUD use.
