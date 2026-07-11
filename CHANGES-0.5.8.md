# devtool 0.5.8

- Bump Devtool Control Center to 0.3.7.
- Add an optional clipboard monitoring mode to the native Qt 6 application.
- Add one-click install, enable, disable, reinstall, remove, and status controls for a per-user systemd service.
- Detect only local artifact archive paths and commands containing `devtool`; all unrelated clipboard content is ignored.
- Never execute clipboard content automatically.
- Persist the latest recognized item in user-only settings for review from the Control Center.
- Add passive desktop notifications through `notify-send` when available.
- Add a detected-command review card to the Run page and detected-item controls to Settings.
- Import graphical-session environment variables before starting the service for improved X11/Wayland reliability.
