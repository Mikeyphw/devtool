# devtool 0.5.5

- Bump Devtool Control Center to 0.3.5.
- Add the complete Qt 6 QML runtime dependency closure to the Debian package.
- Explicitly depend on QtQml, QtQml.Models, QtQml.WorkerScript, QtQuick.Window, and QtQuick.Templates.
- Document the Ubuntu runtime packages needed by the native Control Center.
- Add regression coverage so implicit QML runtime modules cannot be omitted again.
