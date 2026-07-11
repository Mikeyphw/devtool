#include "AppController.h"
#include "ClipboardMonitor.h"
#include "HudStateStore.h"
#include "ProcessRunner.h"
#include "IpcClient.h"

#include <QApplication>
#include <QClipboard>
#include <QCoreApplication>
#include <QDesktopServices>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QGuiApplication>
#include <QIcon>
#include <QMenu>
#include <QProcess>
#include <QSaveFile>
#include <QStandardPaths>
#include <QSystemTrayIcon>
#include <QTimer>
#include <QUrl>

static QString resolveDevtoolExecutable(const QString &configured) {
    const QString value = configured.trimmed().isEmpty() ? QStringLiteral("devtool") : configured.trimmed();
    const QFileInfo explicitFile(value);
    if (explicitFile.isAbsolute() && explicitFile.isFile() && explicitFile.isExecutable())
        return explicitFile.absoluteFilePath();

    const QString fromPath = QStandardPaths::findExecutable(value);
    if (!fromPath.isEmpty()) return fromPath;

    if (value == QStringLiteral("devtool")) {
        const QStringList candidates {
            QDir::home().filePath(QStringLiteral(".local/bin/devtool")),
            QDir::home().filePath(QStringLiteral(".local/pipx/venvs/devtool/bin/devtool")),
            QDir::home().filePath(QStringLiteral(".local/share/pipx/venvs/devtool/bin/devtool"))
        };
        for (const QString &candidate : candidates) {
            const QFileInfo file(candidate);
            if (file.isFile() && file.isExecutable()) return file.absoluteFilePath();
        }
    }
    return value;
}


static QString normalizedClipboardCommand(QString command) {
    command = command.trimmed();
    QStringList lines = command.split(QLatin1Char('\n'));
    for (QString &line : lines) {
        const QString trimmed = line.trimmed();
        if (trimmed.startsWith(QStringLiteral("$ "))) {
            const int marker = line.indexOf(QStringLiteral("$ "));
            line.remove(marker, 2);
        } else if (trimmed.startsWith(QStringLiteral("❯ "))) {
            const int marker = line.indexOf(QStringLiteral("❯ "));
            line.remove(marker, 2);
        }
    }
    return lines.join(QLatin1Char('\n')).trimmed();
}

static QString systemdQuote(QString value) {
    value.replace(QLatin1Char('\\'), QStringLiteral("\\\\"));
    value.replace(QLatin1Char('"'), QStringLiteral("\\\""));
    return QStringLiteral("\"") + value + QStringLiteral("\"");
}

AppController::AppController(HudStateStore *s, ProcessRunner *r, HistoryModel *h, IpcClient *i, bool startCompactOnly, QObject *p)
    : QObject(p), m_store(s), m_runner(r), m_history(h), m_ipc(i), m_startCompactOnly(startCompactOnly),
      m_settings(QStringLiteral("mikeceead"), QStringLiteral("DevtoolControlCenter")) {
    m_repository = m_settings.value(QStringLiteral("repository")).toString();
    m_artifact = m_settings.value(QStringLiteral("artifact")).toString();
    m_devtool = resolveDevtoolExecutable(m_settings.value(QStringLiteral("devtool"), QStringLiteral("devtool")).toString());

    m_clipboardMonitor = new ClipboardMonitor(false, false, this);
    connect(m_clipboardMonitor, &ClipboardMonitor::candidateChanged, this, &AppController::clipboardCandidateChanged);

    m_clipboardStatusTimer = new QTimer(this);
    m_clipboardStatusTimer->setInterval(15000);
    connect(m_clipboardStatusTimer, &QTimer::timeout, this, &AppController::refreshClipboardMonitorStatus);
    m_clipboardStatusTimer->start();
    QTimer::singleShot(0, this, &AppController::refreshClipboardMonitorStatus);

    if (QSystemTrayIcon::isSystemTrayAvailable()) {
        m_tray = new QSystemTrayIcon(QIcon::fromTheme(QStringLiteral("applications-development")), this);
        auto *menu = new QMenu;
        menu->addAction(QStringLiteral("Open"), this, [this] { emit requestMainWindow(); });
        menu->addAction(QStringLiteral("Artifacts"), this, [this] { emit requestMainWindow(); emit requestArtifactInspector(); });
        menu->addAction(QStringLiteral("Compact HUD"), this, [this] { setCompactHudVisible(true); });
        menu->addSeparator();
        menu->addAction(QStringLiteral("Quit"), qApp, &QCoreApplication::quit);
        m_tray->setContextMenu(menu);
        m_tray->setToolTip(QStringLiteral("Devtool Control Center"));
        m_tray->show();
        connect(m_tray, &QSystemTrayIcon::activated, this, [this](auto reason) {
            if (reason == QSystemTrayIcon::Trigger) emit requestMainWindow();
        });
    }

    connect(m_store, &HudStateStore::activeRunsChanged, this, [this] {
        const QVariantList runs = m_store->activeRuns();
        for (const auto &value : runs) {
            const auto run = value.toMap();
            const QString id = run.value(QStringLiteral("run_id")).toString();
            const QString status = run.value(QStringLiteral("status")).toString();
            if (id.isEmpty()) continue;
            const QString previous = m_lastRunStatus.value(id);
            m_lastRunStatus[id] = status;
            if (previous.isEmpty() || previous == status || status == QStringLiteral("running")) continue;
            if (m_tray && (status == QStringLiteral("ok") || status == QStringLiteral("failed") || status == QStringLiteral("cancelled") || status == QStringLiteral("interrupted"))) {
                const QString title = status == QStringLiteral("ok") ? QStringLiteral("Devtool completed") : QStringLiteral("Devtool needs attention");
                QString message = run.value(QStringLiteral("message")).toString();
                if (message.isEmpty()) message = status;
                const QString text = run.value(QStringLiteral("title")).toString() + QStringLiteral(" — ") + message;
                m_tray->showMessage(title, text, status == QStringLiteral("ok") ? QSystemTrayIcon::Information : QSystemTrayIcon::Warning, 7000);
            }
        }
    });
}

void AppController::setRepository(const QString &v) { if (v == m_repository) return; m_repository = v; emit repositoryChanged(); }
void AppController::setArtifact(const QString &v) { if (v == m_artifact) return; m_artifact = v; emit artifactChanged(); }
void AppController::setDevtoolExecutable(const QString &v) {
    if (v.trimmed().isEmpty()) return;
    const QString resolved = resolveDevtoolExecutable(v);
    if (resolved == m_devtool) return;
    m_devtool = resolved;
    emit devtoolExecutableChanged();
}

bool AppController::devtoolAvailable() const {
    const QFileInfo file(m_devtool);
    if (file.isAbsolute()) return file.isFile() && file.isExecutable();
    return !QStandardPaths::findExecutable(m_devtool).isEmpty();
}

QString AppController::clipboardCandidateText() const { return m_clipboardMonitor ? m_clipboardMonitor->candidateText() : QString(); }
QString AppController::clipboardCandidateKind() const { return m_clipboardMonitor ? m_clipboardMonitor->candidateKind() : QString(); }
QString AppController::clipboardCandidateSummary() const { return m_clipboardMonitor ? m_clipboardMonitor->candidateSummary() : QString(); }
QString AppController::clipboardCandidateTimestamp() const { return m_clipboardMonitor ? m_clipboardMonitor->candidateTimestamp() : QString(); }

void AppController::setCompactHudVisible(bool v) { if (v == m_compactHudVisible) return; m_compactHudVisible = v; emit compactHudVisibleChanged(); }
void AppController::saveSettings() {
    m_settings.setValue(QStringLiteral("repository"), m_repository);
    m_settings.setValue(QStringLiteral("artifact"), m_artifact);
    m_settings.setValue(QStringLiteral("devtool"), m_devtool);
    m_settings.sync();
}

void AppController::runWorkflow(const QString &operation, const QString &target, bool validate, bool commit, const QString &message, bool keepArtifact) {
    if (m_repository.isEmpty()) return;
    QStringList a{"--auto-hud","--hud-mode","control-center","--yes","-r",m_repository};
    if (!target.trimmed().isEmpty()) a << QStringLiteral("--target") << target.trimmed();
    const QString op = operation.toLower();
    if (op == QStringLiteral("apply overlay")) {
        a << QStringLiteral("apply-overlay") << m_artifact;
        if (validate) a << QStringLiteral("--validate");
        if (commit && !message.trimmed().isEmpty()) a << QStringLiteral("--commit") << message.trimmed();
        if (keepArtifact) a << QStringLiteral("--keep-artifact");
    } else if (op == QStringLiteral("validate")) a << QStringLiteral("validate");
    else if (op == QStringLiteral("restore")) a << QStringLiteral("restore");
    else if (op == QStringLiteral("build")) a << QStringLiteral("build");
    else if (op == QStringLiteral("test")) a << QStringLiteral("test");
    else if (op == QStringLiteral("status")) a << QStringLiteral("status");
    else if (op == QStringLiteral("doctor")) a << QStringLiteral("doctor");
    else return;
    QVariantMap env;
    env[QStringLiteral("DEVTOOL_CONTROL_CENTER_ACTIVE")] = QStringLiteral("1");
    m_runner->start(m_devtool, a, m_repository, env);
    saveSettings();
}

void AppController::copyText(const QString &t) { QGuiApplication::clipboard()->setText(t); }
void AppController::openPath(const QString &p) {
    if (p.isEmpty()) return;
    QString resolved = p;
    if (QDir::isRelativePath(resolved) && !m_repository.isEmpty()) resolved = QDir(m_repository).filePath(resolved);
    QDesktopServices::openUrl(QUrl::fromLocalFile(resolved));
}
void AppController::quit() { qApp->quit(); }
void AppController::cancelRun(const QString &runId) { if (m_ipc) m_ipc->cancelRun(runId); }

QString AppController::clipboardServicePath() const {
    const QString configRoot = QStandardPaths::writableLocation(QStandardPaths::GenericConfigLocation);
    return QDir(configRoot).filePath(QStringLiteral("systemd/user/devtool-control-center-clipboard.service"));
}

bool AppController::runUserSystemctl(const QStringList &arguments, QString *output, int timeoutMs) const {
    const QString executable = QStandardPaths::findExecutable(QStringLiteral("systemctl"));
    if (executable.isEmpty()) {
        if (output) *output = QStringLiteral("systemctl is not available");
        return false;
    }
    QProcess process;
    QStringList args { QStringLiteral("--user") };
    args << arguments;
    process.start(executable, args);
    if (!process.waitForStarted(2500)) {
        if (output) *output = process.errorString();
        return false;
    }
    if (!process.waitForFinished(timeoutMs)) {
        process.kill();
        process.waitForFinished(1000);
        if (output) *output = QStringLiteral("systemctl timed out");
        return false;
    }
    if (output) {
        *output = QString::fromUtf8(process.readAllStandardOutput() + process.readAllStandardError()).trimmed();
    }
    return process.exitStatus() == QProcess::NormalExit && process.exitCode() == 0;
}

void AppController::setClipboardMonitorStatus(bool installed, bool active, const QString &status) {
    if (installed == m_clipboardMonitorInstalled && active == m_clipboardMonitorActive && status == m_clipboardMonitorStatus) return;
    m_clipboardMonitorInstalled = installed;
    m_clipboardMonitorActive = active;
    m_clipboardMonitorStatus = status;
    emit clipboardMonitorStatusChanged();
}

void AppController::refreshClipboardMonitorStatus() {
    const bool installed = QFileInfo::exists(clipboardServicePath());
    if (!installed) {
        setClipboardMonitorStatus(false, false, QStringLiteral("Not installed"));
        return;
    }
    QString output;
    const bool active = runUserSystemctl({ QStringLiteral("is-active"), QStringLiteral("devtool-control-center-clipboard.service") }, &output, 1500);
    if (active) setClipboardMonitorStatus(true, true, QStringLiteral("Installed and running"));
    else setClipboardMonitorStatus(true, false, output.isEmpty() ? QStringLiteral("Installed but stopped") : QStringLiteral("Installed but stopped: %1").arg(output));
}

void AppController::installClipboardMonitor() {
    const QString servicePath = clipboardServicePath();
    if (!QDir().mkpath(QFileInfo(servicePath).absolutePath())) {
        setClipboardMonitorStatus(false, false, QStringLiteral("Could not create the user service directory"));
        return;
    }
    QString executable = QCoreApplication::applicationFilePath();
    const QString canonical = QFileInfo(executable).canonicalFilePath();
    if (!canonical.isEmpty()) executable = canonical;
    const QString unit = QStringLiteral(
        "[Unit]\n"
        "Description=Devtool Control Center clipboard monitor\n"
        "After=graphical-session.target\n"
        "PartOf=graphical-session.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        "ExecStart=%1 --clipboard-monitor\n"
        "Restart=on-failure\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=graphical-session.target\n").arg(systemdQuote(executable));
    QSaveFile file(servicePath);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Text) || file.write(unit.toUtf8()) < 0 || !file.commit()) {
        setClipboardMonitorStatus(false, false, QStringLiteral("Could not write the user service"));
        return;
    }

    // Desktop environments normally import these values into the user manager,
    // but doing it here makes installation reliable in minimal sessions too.
    runUserSystemctl({ QStringLiteral("import-environment"), QStringLiteral("DISPLAY"), QStringLiteral("WAYLAND_DISPLAY"),
                       QStringLiteral("XDG_CURRENT_DESKTOP"), QStringLiteral("XDG_SESSION_TYPE"), QStringLiteral("DBUS_SESSION_BUS_ADDRESS") }, nullptr, 4000);
    QString output;
    runUserSystemctl({ QStringLiteral("daemon-reload") }, nullptr);
    const bool enabled = runUserSystemctl({ QStringLiteral("enable"), QStringLiteral("--now"), QStringLiteral("devtool-control-center-clipboard.service") }, &output);
    if (!enabled) {
        setClipboardMonitorStatus(true, false, output.isEmpty() ? QStringLiteral("Installed, but could not start") : output);
        return;
    }
    refreshClipboardMonitorStatus();
}

void AppController::enableClipboardMonitor() {
    QString output;
    runUserSystemctl({ QStringLiteral("import-environment"), QStringLiteral("DISPLAY"), QStringLiteral("WAYLAND_DISPLAY"),
                       QStringLiteral("XDG_CURRENT_DESKTOP"), QStringLiteral("XDG_SESSION_TYPE"), QStringLiteral("DBUS_SESSION_BUS_ADDRESS") }, nullptr, 4000);
    if (!runUserSystemctl({ QStringLiteral("enable"), QStringLiteral("--now"), QStringLiteral("devtool-control-center-clipboard.service") }, &output))
        setClipboardMonitorStatus(true, false, output.isEmpty() ? QStringLiteral("Could not start the service") : output);
    else
        refreshClipboardMonitorStatus();
}

void AppController::disableClipboardMonitor() {
    QString output;
    if (!runUserSystemctl({ QStringLiteral("disable"), QStringLiteral("--now"), QStringLiteral("devtool-control-center-clipboard.service") }, &output))
        setClipboardMonitorStatus(true, false, output.isEmpty() ? QStringLiteral("Could not stop the service") : output);
    else
        refreshClipboardMonitorStatus();
}

void AppController::removeClipboardMonitor() {
    runUserSystemctl({ QStringLiteral("disable"), QStringLiteral("--now"), QStringLiteral("devtool-control-center-clipboard.service") }, nullptr);
    QFile::remove(clipboardServicePath());
    runUserSystemctl({ QStringLiteral("daemon-reload") }, nullptr);
    runUserSystemctl({ QStringLiteral("reset-failed"), QStringLiteral("devtool-control-center-clipboard.service") }, nullptr);
    refreshClipboardMonitorStatus();
}

void AppController::scanClipboardNow() {
    if (m_clipboardMonitor) m_clipboardMonitor->scanCurrentClipboard();
}

void AppController::useClipboardCandidate() {
    if (!m_clipboardMonitor) return;
    if (m_clipboardMonitor->candidateKind() == QStringLiteral("artifact")) {
        setArtifact(m_clipboardMonitor->candidateText());
        emit requestMainWindow();
        emit requestArtifactInspector();
    } else if (m_clipboardMonitor->candidateKind() == QStringLiteral("command")) {
        emit requestMainWindow();
        emit requestRunPage();
    }
}

void AppController::clearClipboardCandidate() {
    if (m_clipboardMonitor) m_clipboardMonitor->clearCandidate();
}

void AppController::runClipboardCommand() {
    if (!m_clipboardMonitor || m_clipboardMonitor->candidateKind() != QStringLiteral("command") || m_runner->running()) return;
    const QString command = normalizedClipboardCommand(m_clipboardMonitor->candidateText());
    if (command.isEmpty()) return;
    QString shell = qEnvironmentVariable("SHELL");
    if (shell.isEmpty() || !QFileInfo(shell).isExecutable()) shell = QStandardPaths::findExecutable(QStringLiteral("bash"));
    if (shell.isEmpty()) shell = QStandardPaths::findExecutable(QStringLiteral("sh"));
    if (shell.isEmpty()) return;
    const QString workingDirectory = QFileInfo(m_repository).isDir() ? m_repository : QDir::homePath();
    QVariantMap env;
    env[QStringLiteral("DEVTOOL_CONTROL_CENTER_ACTIVE")] = QStringLiteral("1");
    m_runner->start(shell, QStringList{QStringLiteral("-lc"), command}, workingDirectory, env);
    emit requestMainWindow();
    emit requestRunPage();
}

