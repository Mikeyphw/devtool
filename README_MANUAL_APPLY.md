# Desktop external-terminal and artifact-import patch

This patch:

- prepends `~/.local/share/mise/shims` to PATH so desktop-launched devtool runs find the mise-managed .NET SDK;
- prefers Kitty, then WezTerm, Foot, Konsole, GNOME Terminal, XFCE Terminal, and xterm;
- uses zsh when available;
- removes the embedded Run-page terminal and launches a real external terminal;
- adds a persistent auto-close-terminal option;
- uses a native Qt directory chooser for Add repository;
- reads artifact inspection metadata after clipboard import and fills target, validation, commit message, and checkboxes unless the copied command explicitly overrides them.

Apply from the repository root:

```bash
unzip -o "$HOME/Downloads/devtool_desktop_external_terminal_manifest_patch.zip" \
  -d "$HOME/Code/devtool"

rm -rf "$HOME/.cache/devtool-control-center/package-build-0.3.8-amd64"

cd "$HOME/Code/devtool/desktop/devtool-control-center"
./package-deb.sh --install --delete-after-install
```
