#include "AppController.h"
#include "ClipboardMonitor.h"
#include "HudStateStore.h"
#include "ProcessRunner.h"
#include "IpcClient.h"

#include <QApplication>
#include <QClipboard>
#include <QCoreApplication>
#include <QDesktopServices>
#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QFileDialog>
#include <QFontDatabase>
#include <QGuiApplication>
#include <QIcon>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMenu>
#include <QProcess>
#include <QRegularExpression>
#include <QProcessEnvironment>
#include <QSaveFile>
#include <QSet>
#include <QStandardPaths>
#include <QIODevice>
#include <QLocalSocket>
#include <QSystemTrayIcon>
#include <QTimer>
#include <QUrl>
#include <QtGlobal>
#include <utility>

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


static QString shellUnquote(QString value) {
    value = value.trimmed();
    if (value.size() >= 2) {
        const QChar first = value.front();
        const QChar last = value.back();
        if ((first == QLatin1Char('"') && last == QLatin1Char('"')) ||
            (first == QLatin1Char('\'') && last == QLatin1Char('\''))) {
            value = value.mid(1, value.size() - 2);
        }
    }
    value.replace(QStringLiteral("$HOME"), QDir::homePath());
    value.replace(QStringLiteral("${HOME}"), QDir::homePath());
    if (value == QStringLiteral("~")) value = QDir::homePath();
    else if (value.startsWith(QStringLiteral("~/"))) value = QDir::home().filePath(value.mid(2));
    return QDir::cleanPath(QDir::fromNativeSeparators(value));
}

static QStringList shellTokens(QString command) {
    command.replace(QStringLiteral("\\\r\n"), QStringLiteral(" "));
    command.replace(QStringLiteral("\\\n"), QStringLiteral(" "));
    static const QRegularExpression tokenPattern(
        QStringLiteral(R"((?:"(?:\\.|[^"])*"|'[^']*'|[^\s]+))"));
    QStringList values;
    auto it = tokenPattern.globalMatch(command);
    while (it.hasNext()) values << it.next().captured(0);
    return values;
}

static QString operationLabel(const QString &value) {
    const QString token = value.toLower();
    if (token == QStringLiteral("apply-overlay") || token == QStringLiteral("ao")) return QStringLiteral("Apply overlay");
    if (token == QStringLiteral("apply-overlays") || token == QStringLiteral("aos")) return QStringLiteral("Apply bundle");
    if (token == QStringLiteral("validate") || token == QStringLiteral("validate-artifact") || token == QStringLiteral("v")) return QStringLiteral("Validate");
    if (token == QStringLiteral("restore") || token == QStringLiteral("rs")) return QStringLiteral("Restore");
    if (token == QStringLiteral("build") || token == QStringLiteral("b")) return QStringLiteral("Build");
    if (token == QStringLiteral("test") || token == QStringLiteral("t")) return QStringLiteral("Test");
    if (token == QStringLiteral("lint") || token == QStringLiteral("l")) return QStringLiteral("Lint");
    if (token == QStringLiteral("run") || token == QStringLiteral("r")) return QStringLiteral("Run project");
    if (token == QStringLiteral("package") || token == QStringLiteral("pkg")) return QStringLiteral("Package");
    if (token == QStringLiteral("clean") || token == QStringLiteral("cl")) return QStringLiteral("Clean");
    if (token == QStringLiteral("inspect") || token == QStringLiteral("i")) return QStringLiteral("Inspect");
    if (token == QStringLiteral("commit") || token == QStringLiteral("c")) return QStringLiteral("Commit");
    if (token == QStringLiteral("status") || token == QStringLiteral("st")) return QStringLiteral("Status");
    if (token == QStringLiteral("doctor") || token == QStringLiteral("dr")) return QStringLiteral("Doctor");
    return {};
}

static QString shellQuote(QString value) {
    value.replace(QLatin1Char('\''), QStringLiteral("'\"'\"'"));
    return QLatin1Char('\'') + value + QLatin1Char('\'');
}

static QString systemdQuote(QString value) {
    value.replace(QLatin1Char('\\'), QStringLiteral("\\\\"));
    value.replace(QLatin1Char('"'), QStringLiteral("\\\""));
    return QStringLiteral("\"") + value + QStringLiteral("\"");
}

static QString resolveHudExecutable() {
    const QStringList candidates {
        QCoreApplication::applicationDirPath() + QStringLiteral("/devtool-control-center-hud"),
        QCoreApplication::applicationDirPath() + QStringLiteral("/devtool-control-center-hud.exe"),
    };
    for (const QString &candidate : candidates) {
        const QFileInfo file(candidate);
        if (file.isFile() && file.isExecutable()) return file.absoluteFilePath();
    }
    return QStandardPaths::findExecutable(QStringLiteral("devtool-control-center-hud"));
}

static bool sendHudActivation() {
    QLocalSocket socket;
    socket.connectToServer(QStringLiteral("io.github.mikeceead.DevtoolControlCenter.hud"), QIODevice::ReadWrite);
    if (!socket.waitForConnected(500)) return false;
    socket.write("show-hud");
    if (!socket.waitForBytesWritten(500)) return false;
    if (!socket.waitForReadyRead(1000)) return false;
    return QString::fromUtf8(socket.readAll()).trimmed() == QStringLiteral("shown");
}

AppController::AppController(HudStateStore *s, ProcessRunner *r, HistoryModel *h, IpcClient *i, QObject *p)
    : QObject(p), m_store(s), m_runner(r), m_history(h), m_ipc(i),
      m_settings(QStringLiteral("mikeceead"), QStringLiteral("DevtoolControlCenter")) {
    m_repository = m_settings.value(QStringLiteral("repository")).toString();
    m_artifact = m_settings.value(QStringLiteral("artifact")).toString();
    m_devtool = resolveDevtoolExecutable(m_settings.value(QStringLiteral("devtool"), QStringLiteral("devtool")).toString());
    m_autoCloseTerminal = m_settings.value(QStringLiteral("terminal/autoClose"), false).toBool();
    m_typographyScale = qBound(1.0, m_settings.value(QStringLiteral("appearance/typographyScale"), 1.0).toDouble(), 2.0);
    QFont uiFont = QFontDatabase::systemFont(QFontDatabase::GeneralFont);
    m_baseFontPointSize = uiFont.pointSizeF() > 0.0 ? uiFont.pointSizeF() : 11.0;
    uiFont.setPointSizeF(m_baseFontPointSize * m_typographyScale);
    QGuiApplication::setFont(uiFont);

    m_clipboardMonitor = new ClipboardMonitor(false, false, this);
    connect(m_clipboardMonitor, &ClipboardMonitor::candidateChanged, this, &AppController::clipboardCandidateChanged);

    // Service state is refreshed on startup, when Settings is opened, and
    // after install/enable/disable actions. Avoid a permanent 15-second poll.
    QTimer::singleShot(0, this, &AppController::refreshClipboardMonitorStatus);

    if (QSystemTrayIcon::isSystemTrayAvailable()) {
        m_tray = new QSystemTrayIcon(QIcon(QStringLiteral(":/qt/qml/Devtool/ControlCenter/icons/devtool-control-center-tray.svg")), this);
        auto *menu = new QMenu;
        menu->addAction(QStringLiteral("Open"), this, [this] { emit requestMainWindow(); });
        menu->addAction(QStringLiteral("Artifacts"), this, [this] { emit requestMainWindow(); emit requestArtifactInspector(); });
        menu->addAction(QStringLiteral("Compact HUD"), this, [this] { launchHud(); });
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
    const QString resolved = resolveDevtoolExecutable(v);
    if (resolved == m_devtool) return;
    m_devtool = resolved;
    emit devtoolExecutableChanged();
}

void AppController::setAutoCloseTerminal(bool value) {
    if (value == m_autoCloseTerminal) return;
    m_autoCloseTerminal = value;
    emit autoCloseTerminalChanged();
}

void AppController::setTypographyScale(double value) {
    value = qBound(1.0, value, 2.0);
    if (qFuzzyCompare(m_typographyScale, value)) return;
    m_typographyScale = value;
    QFont uiFont = QFontDatabase::systemFont(QFontDatabase::GeneralFont);
    uiFont.setPointSizeF(m_baseFontPointSize * m_typographyScale);
    QGuiApplication::setFont(uiFont);
    m_settings.setValue(QStringLiteral("appearance/typographyScale"), m_typographyScale);
    m_settings.sync();
    emit typographyScaleChanged();
}

void AppController::setLaunchStatus(const QString &status) {
    if (status == m_launchStatus) return;
    m_launchStatus = status;
    emit launchStatusChanged();
}

bool AppController::devtoolAvailable() const {
    const QFileInfo file(m_devtool);
    if (file.isAbsolute()) return file.isFile() && file.isExecutable();
    return !QStandardPaths::findExecutable(m_devtool).isEmpty();
}


bool AppController::artifactExists() const {
    return !m_artifact.trimmed().isEmpty() && QFileInfo(m_artifact.trimmed()).isFile();
}

QString AppController::preferredShell() const {
    const QString zsh = QStandardPaths::findExecutable(QStringLiteral("zsh"));
    if (!zsh.isEmpty()) return zsh;
    const QFileInfo systemZsh(QStringLiteral("/usr/bin/zsh"));
    if (systemZsh.isFile() && systemZsh.isExecutable()) return systemZsh.absoluteFilePath();
    const QString configured = qEnvironmentVariable("SHELL");
    if (!configured.isEmpty() && QFileInfo(configured).isExecutable()) return configured;
    const QString bash = QStandardPaths::findExecutable(QStringLiteral("bash"));
    return bash.isEmpty() ? QStandardPaths::findExecutable(QStringLiteral("sh")) : bash;
}

QString AppController::preferredTerminal() const {
    const QStringList candidates {
        QStringLiteral("kitty"), QStringLiteral("wezterm"), QStringLiteral("foot"),
        QStringLiteral("konsole"), QStringLiteral("gnome-terminal"),
        QStringLiteral("xfce4-terminal"), QStringLiteral("xterm")
    };
    for (const QString &candidate : candidates) {
        const QString executable = QStandardPaths::findExecutable(candidate);
        if (!executable.isEmpty()) return executable;
        const QFileInfo localExecutable(QDir::home().filePath(QStringLiteral(".local/bin/") + candidate));
        if (localExecutable.isFile() && localExecutable.isExecutable()) return localExecutable.absoluteFilePath();
    }
    return {};
}

QString AppController::dotnetExecutable() const {
    const QString miseDotnet = QDir::home().filePath(QStringLiteral(".local/share/mise/shims/dotnet"));
    const QFileInfo miseFile(miseDotnet);
    if (miseFile.isFile() && miseFile.isExecutable()) return miseFile.absoluteFilePath();
    return QStandardPaths::findExecutable(QStringLiteral("dotnet"));
}

QString AppController::augmentedPath() const {
    QStringList entries;
    const QString miseShims = QDir::home().filePath(QStringLiteral(".local/share/mise/shims"));
    if (QFileInfo(miseShims).isDir()) entries << miseShims;
    const QString localBin = QDir::home().filePath(QStringLiteral(".local/bin"));
    if (QFileInfo(localBin).isDir()) entries << localBin;
    const QString current = qEnvironmentVariable("PATH");
    if (!current.isEmpty()) entries << current;
    return entries.join(QLatin1Char(':'));
}

bool AppController::launchExternalTerminal(const QString &command, const QString &workingDirectory, const QString &title) {
    const QString terminal = preferredTerminal();
    const QString shell = preferredShell();
    if (terminal.isEmpty()) {
        setLaunchStatus(QStringLiteral("No supported terminal was found. Install kitty or configure a desktop terminal."));
        return false;
    }
    if (shell.isEmpty()) {
        setLaunchStatus(QStringLiteral("No supported shell was found."));
        return false;
    }

    QString wrapped = QStringLiteral("export PATH=") + shellQuote(augmentedPath()) + QStringLiteral("; ") + command;
    if (!m_autoCloseTerminal) {
        wrapped += QStringLiteral(
            "; __devtool_status=$?; printf '\n\nDevtool command finished with exit %s. Press Enter to close…' \"$__devtool_status\"; "
            "read -r __devtool_close; exit \"$__devtool_status\"");
    }

    const QString base = QFileInfo(terminal).fileName();
    QStringList arguments;
    if (base == QStringLiteral("kitty")) {
        arguments << QStringLiteral("--directory") << workingDirectory
                  << QStringLiteral("--title") << title
                  << shell << QStringLiteral("-lc") << wrapped;
    } else if (base == QStringLiteral("wezterm")) {
        arguments << QStringLiteral("start") << QStringLiteral("--always-new-process")
                  << QStringLiteral("--cwd") << workingDirectory << QStringLiteral("--")
                  << shell << QStringLiteral("-lc") << wrapped;
    } else if (base == QStringLiteral("foot")) {
        arguments << QStringLiteral("--working-directory") << workingDirectory
                  << QStringLiteral("--title") << title
                  << shell << QStringLiteral("-lc") << wrapped;
    } else if (base == QStringLiteral("konsole")) {
        arguments << QStringLiteral("--workdir") << workingDirectory
                  << QStringLiteral("-p") << QStringLiteral("tabtitle=%1").arg(title)
                  << QStringLiteral("-e") << shell << QStringLiteral("-lc") << wrapped;
    } else if (base == QStringLiteral("gnome-terminal")) {
        arguments << QStringLiteral("--working-directory=%1").arg(workingDirectory)
                  << QStringLiteral("--title=%1").arg(title) << QStringLiteral("--")
                  << shell << QStringLiteral("-lc") << wrapped;
    } else if (base == QStringLiteral("xfce4-terminal")) {
        arguments << QStringLiteral("--working-directory") << workingDirectory
                  << QStringLiteral("--title") << title << QStringLiteral("-x")
                  << shell << QStringLiteral("-lc") << wrapped;
    } else {
        arguments << QStringLiteral("-T") << title << QStringLiteral("-e")
                  << shell << QStringLiteral("-lc") << wrapped;
    }

    const bool launched = QProcess::startDetached(terminal, arguments, workingDirectory);
    if (launched) {
        setLaunchStatus(QStringLiteral("Opened in %1%2.")
            .arg(base, m_autoCloseTerminal ? QStringLiteral("; it will close when finished") : QStringLiteral("")));
    } else {
        setLaunchStatus(QStringLiteral("Could not launch %1.").arg(base));
    }
    return launched;
}

QString AppController::chooseRepositoryDirectory(const QString &initialPath) {
    QString start = initialPath.trimmed();
    if (!QFileInfo(start).isDir()) start = QFileInfo(m_repository).isDir() ? m_repository : QDir::homePath();
    return QFileDialog::getExistingDirectory(nullptr, QStringLiteral("Add repository"), start,
                                             QFileDialog::ShowDirsOnly | QFileDialog::DontResolveSymlinks);
}

QVariantMap AppController::parseDevtoolCommand(const QString &rawCommand) const {
    const QString command = normalizedClipboardCommand(rawCommand);
    QVariantMap result;
    result[QStringLiteral("command")] = command;
    if (command.isEmpty()) return result;

    QString repository;
    static const QRegularExpression cdPattern(
        QStringLiteral(R"((?:^|&&|;)\s*cd\s+((?:"[^"]+")|(?:'[^']+')|(?:[^\s;&]+)))"),
        QRegularExpression::MultilineOption);
    const auto cdMatch = cdPattern.match(command);
    if (cdMatch.hasMatch()) repository = shellUnquote(cdMatch.captured(1));

    const QStringList rawTokens = shellTokens(command);
    QStringList tokens;
    for (QString token : rawTokens) {
        if (token == QStringLiteral("\\") || token == QStringLiteral("&&") || token == QStringLiteral(";")) continue;
        tokens << token;
    }

    int devtoolIndex = -1;
    for (int index = 0; index < tokens.size(); ++index) {
        const QString base = QFileInfo(shellUnquote(tokens.at(index))).fileName();
        if (base == QStringLiteral("devtool") || base == QStringLiteral("devtool-androidos")) {
            devtoolIndex = index;
            break;
        }
    }
    if (devtoolIndex < 0) return result;

    QString operation;
    QString artifact;
    QString target;
    QString commitMessage;
    bool validate = false;
    bool commit = false;
    bool keepArtifact = false;
    QString copyMode = QStringLiteral("summary");
    bool autoHud = true;
    QString hudMode = QStringLiteral("control-center");
    bool validateExplicit = false;
    bool commitExplicit = false;
    bool keepArtifactExplicit = false;
    bool targetExplicit = false;
    bool copyExplicit = false;
    bool hudExplicit = false;
    static const QSet<QString> optionsWithValue {
        QStringLiteral("-r"), QStringLiteral("--repo-root"), QStringLiteral("--config"),
        QStringLiteral("--target"), QStringLiteral("--solution"), QStringLiteral("--project"),
        QStringLiteral("--copy-via")
    };

    for (int index = devtoolIndex + 1; index < tokens.size(); ++index) {
        const QString token = shellUnquote(tokens.at(index));
        if ((token == QStringLiteral("-r") || token == QStringLiteral("--repo-root")) && index + 1 < tokens.size()) {
            repository = shellUnquote(tokens.at(++index));
            continue;
        }
        if ((token == QStringLiteral("--target") || token == QStringLiteral("--solution") || token == QStringLiteral("--project")) && index + 1 < tokens.size()) {
            target = shellUnquote(tokens.at(++index));
            targetExplicit = true;
            continue;
        }
        if (token == QStringLiteral("--copy") ||
            token == QStringLiteral("--copy-summary")) {
            copyMode = QStringLiteral("summary");
            copyExplicit = true;
            continue;
        }

        if (token == QStringLiteral("--copy-json")) {
            copyMode = QStringLiteral("json");
            copyExplicit = true;
            continue;
        }

        if (token == QStringLiteral("--no-clipboard")) {
            copyMode = QStringLiteral("none");
            copyExplicit = true;
            continue;
        }

        if (token == QStringLiteral("--auto-hud") ||
            token == QStringLiteral("--hud-auto")) {
            autoHud = true;
            hudExplicit = true;
            continue;
        }

        if (token == QStringLiteral("--no-hud")) {
            autoHud = false;
            hudMode = QStringLiteral("none");
            hudExplicit = true;
            continue;
        }

        if (token == QStringLiteral("--hud-mode") &&
            index + 1 < tokens.size()) {
            hudMode = shellUnquote(tokens.at(++index));
            autoHud =
                hudMode.compare(
                    QStringLiteral("none"),
                    Qt::CaseInsensitive
                ) != 0;
            hudExplicit = true;
            continue;
        }

        if (token == QStringLiteral("--commit")) {
            commit = true;
            commitExplicit = true;

            // Bare --commit means that devtool must use the message embedded
            // in devtool-artifact.json. Do not consume the following flag as
            // though it were a commit message.
            if (index + 1 < tokens.size()) {
                const QString candidate =
                    shellUnquote(tokens.at(index + 1));

                if (!candidate.startsWith(QLatin1Char('-')) &&
                    candidate != QStringLiteral("&&") &&
                    candidate != QStringLiteral(";")) {
                    commitMessage =
                        shellUnquote(tokens.at(++index));
                }
            }
            continue;
        }
        if (token == QStringLiteral("--no-commit")) { commit = false; commitExplicit = true; continue; }
        if (token == QStringLiteral("--validate")) { validate = true; validateExplicit = true; continue; }
        if (token == QStringLiteral("--no-validate")) { validate = false; validateExplicit = true; continue; }
        if (token == QStringLiteral("--keep-artifact")) { keepArtifact = true; keepArtifactExplicit = true; continue; }
        if (optionsWithValue.contains(token)) { if (index + 1 < tokens.size()) ++index; continue; }
        if (token.startsWith(QLatin1Char('-'))) continue;
        if (operation.isEmpty()) {
            operation = operationLabel(token);
            if (operation.isEmpty()) continue;
            if (operation == QStringLiteral("Apply overlay") && index + 1 < tokens.size())
                artifact = shellUnquote(tokens.at(++index));
            continue;
        }
    }

    if (operation.isEmpty()) return result;
    result[QStringLiteral("valid")] = true;
    result[QStringLiteral("repository")] = repository;
    result[QStringLiteral("operation")] = operation;
    result[QStringLiteral("artifact")] = artifact;
    result[QStringLiteral("target")] = target;
    result[QStringLiteral("validate")] = validate;
    result[QStringLiteral("commit")] = commit;
    result[QStringLiteral("commitMessage")] = commitMessage;
    result[QStringLiteral("keepArtifact")] = keepArtifact;
    result[QStringLiteral("copyMode")] = copyMode;
    result[QStringLiteral("autoHud")] = autoHud;
    result[QStringLiteral("hudMode")] = hudMode;
    result[QStringLiteral("targetExplicit")] = targetExplicit;
    result[QStringLiteral("validateExplicit")] = validateExplicit;
    result[QStringLiteral("commitExplicit")] = commitExplicit;
    result[QStringLiteral("keepArtifactExplicit")] = keepArtifactExplicit;
    result[QStringLiteral("copyExplicit")] = copyExplicit;
    result[QStringLiteral("hudExplicit")] = hudExplicit;
    return result;
}

QVariantMap AppController::pasteCommandFromClipboard() {
    QClipboard *clipboard = QGuiApplication::clipboard();
    if (!clipboard) return {};
    const QString text = clipboard->text(QClipboard::Clipboard);
    const QVariantMap parsed = parseDevtoolCommand(text);
    if (!parsed.value(QStringLiteral("valid")).toBool()) return parsed;
    const QString repo = parsed.value(QStringLiteral("repository")).toString();
    const QString artifactPath = parsed.value(QStringLiteral("artifact")).toString();
    if (!repo.isEmpty()) setRepository(repo);
    if (!artifactPath.isEmpty()) setArtifact(artifactPath);
    return parsed;
}

QString AppController::clipboardCandidateText() const { return m_clipboardMonitor ? m_clipboardMonitor->candidateText() : QString(); }
QString AppController::clipboardCandidateCopyText() const {
    if (!m_clipboardMonitor) return QString();
    const QString text = m_clipboardMonitor->candidateText();
    if (m_clipboardMonitor->candidateKind() == QStringLiteral("command")) return normalizedClipboardCommand(text);
    return text;
}
QString AppController::clipboardCandidateKind() const { return m_clipboardMonitor ? m_clipboardMonitor->candidateKind() : QString(); }
QString AppController::clipboardCandidateSummary() const { return m_clipboardMonitor ? m_clipboardMonitor->candidateSummary() : QString(); }
QString AppController::clipboardCandidateTimestamp() const { return m_clipboardMonitor ? m_clipboardMonitor->candidateTimestamp() : QString(); }
QString AppController::clipboardCandidateSnapshotText() const { return m_clipboardCandidateSnapshotText; }
QString AppController::clipboardCandidateSnapshotKind() const { return m_clipboardCandidateSnapshotKind; }
QString AppController::clipboardCandidateSnapshotSummary() const { return m_clipboardCandidateSnapshotSummary; }
QString AppController::clipboardCandidateSnapshotTimestamp() const { return m_clipboardCandidateSnapshotTimestamp; }

void AppController::saveSettings() {
    m_settings.setValue(QStringLiteral("repository"), m_repository);
    m_settings.setValue(QStringLiteral("artifact"), m_artifact);
    m_settings.setValue(QStringLiteral("devtool"), m_devtool);
    m_settings.setValue(QStringLiteral("terminal/autoClose"), m_autoCloseTerminal);
    m_settings.setValue(QStringLiteral("appearance/typographyScale"), m_typographyScale);
    m_settings.sync();
}

QString AppController::buildWorkflowCommand(
    const QString &operation, const QString &target, const QString &projectType,
    const QString &executionEnvironment, const QString &testProfile,
    const QString &selectors, const QString &tags, const QString &excludeTags,
    const QString &testPath, const QString &testClass, const QString &testMethod,
    bool listTests, bool instrumented, bool validate, bool commit,
    const QString &message, bool keepArtifact, bool noManifestCheck,
    const QString &copyMode, bool autoHud, const QString &hudMode) const {

    if (m_repository.isEmpty()) return {};
    QStringList a;
    const QString normalizedProjectType = projectType.trimmed().toLower();
    if (!normalizedProjectType.isEmpty() && normalizedProjectType != QStringLiteral("auto"))
        a << QStringLiteral("--project-type") << normalizedProjectType;
    const QString normalizedEnvironment = executionEnvironment.trimmed().toLower();
    if (!normalizedEnvironment.isEmpty() && normalizedEnvironment != QStringLiteral("auto"))
        a << QStringLiteral("--execution-environment") << normalizedEnvironment;
    const QString normalizedCopyMode = copyMode.trimmed().toLower();
    if (normalizedCopyMode == QStringLiteral("json")) a << QStringLiteral("--copy-json");
    else if (normalizedCopyMode == QStringLiteral("none")) a << QStringLiteral("--no-clipboard");
    else a << QStringLiteral("--copy");
    const QString normalizedHudMode = hudMode.trimmed().isEmpty() ? QStringLiteral("control-center") : hudMode.trimmed();
    if (autoHud && normalizedHudMode.compare(QStringLiteral("none"), Qt::CaseInsensitive) != 0)
        // Stable control-center HUD invocation retained for compatibility: "--hud-mode","control-center".
        a << QStringLiteral("--auto-hud") << QStringLiteral("--hud-mode") << normalizedHudMode;
    else a << QStringLiteral("--no-hud");
    a << QStringLiteral("--yes") << QStringLiteral("-r") << m_repository;
    if (!target.trimmed().isEmpty()) a << QStringLiteral("--target") << target.trimmed();

    const QString op = operation.toLower();
    if (op == QStringLiteral("apply overlay") || op == QStringLiteral("apply bundle")) {
        a << (op == QStringLiteral("apply bundle") ? QStringLiteral("apply-overlays") : QStringLiteral("apply-overlay")) << m_artifact;
        if (validate) a << QStringLiteral("--validate");
        if (commit && !message.trimmed().isEmpty()) a << QStringLiteral("--commit") << message.trimmed();
        if (keepArtifact) a << QStringLiteral("--keep-artifact");
        if (noManifestCheck) a << QStringLiteral("--no-manifest-check");
    } else if (op == QStringLiteral("validate")) a << QStringLiteral("validate");
    else if (op == QStringLiteral("restore")) a << QStringLiteral("restore");
    else if (op == QStringLiteral("build")) a << QStringLiteral("build");
    else if (op == QStringLiteral("test")) a << QStringLiteral("test");
    else if (op == QStringLiteral("lint")) a << QStringLiteral("lint");
    else if (op == QStringLiteral("run project")) a << QStringLiteral("run");
    else if (op == QStringLiteral("package")) a << QStringLiteral("package");
    else if (op == QStringLiteral("clean")) a << QStringLiteral("clean");
    else if (op == QStringLiteral("inspect")) a << QStringLiteral("inspect");
    else if (op == QStringLiteral("commit")) {
        if (message.trimmed().isEmpty()) return {};
        a << QStringLiteral("commit") << message.trimmed();
    } else if (op == QStringLiteral("status")) a << QStringLiteral("status");
    else if (op == QStringLiteral("doctor")) a << QStringLiteral("doctor");
    else return {};

    const bool isTest = op == QStringLiteral("test");
    const bool isValidation = isTest || op == QStringLiteral("validate") || op == QStringLiteral("apply overlay") || op == QStringLiteral("apply bundle");
    if (isValidation && !testProfile.trimmed().isEmpty())
        a << (isTest ? QStringLiteral("--profile") : QStringLiteral("--test-profile")) << testProfile.trimmed();
    if (isTest) {
        if (listTests) a << QStringLiteral("--list-tests");
        const auto appendMany = [&a](const QString &flag, const QString &raw) {
            const QStringList entries = raw.split(QRegularExpression(QStringLiteral("[,\\n]")), Qt::SkipEmptyParts);
            for (const QString &entry : entries) {
                const QString value = entry.trimmed();
                if (!value.isEmpty()) a << flag << value;
            }
        };
        appendMany(QStringLiteral("--select"), selectors);
        appendMany(QStringLiteral("--tag"), tags);
        appendMany(QStringLiteral("--exclude-tag"), excludeTags);
        if (!testPath.trimmed().isEmpty()) a << QStringLiteral("--path") << testPath.trimmed();
        if (!testClass.trimmed().isEmpty()) a << QStringLiteral("--class") << testClass.trimmed();
        if (!testMethod.trimmed().isEmpty()) a << QStringLiteral("--method") << testMethod.trimmed();
        if (instrumented) a << QStringLiteral("--instrumented");
    }
    QString command = QStringLiteral("export DEVTOOL_CONTROL_CENTER_ACTIVE=1; cd ") + shellQuote(m_repository)
        + QStringLiteral(" && ") + shellQuote(m_devtool);
    for (const QString &argument : std::as_const(a)) command += QLatin1Char(' ') + shellQuote(argument);
    return command;
}

void AppController::runWorkflow(
    const QString &operation, const QString &target, const QString &projectType,
    const QString &executionEnvironment, const QString &testProfile,
    const QString &selectors, const QString &tags, const QString &excludeTags,
    const QString &testPath, const QString &testClass, const QString &testMethod,
    bool listTests, bool instrumented, bool validate, bool commit,
    const QString &message, bool keepArtifact, bool noManifestCheck,
    const QString &copyMode, bool autoHud, const QString &hudMode) {
    const QString command = buildWorkflowCommand(operation, target, projectType, executionEnvironment, testProfile,
        selectors, tags, excludeTags, testPath, testClass, testMethod, listTests, instrumented,
        validate, commit, message, keepArtifact, noManifestCheck, copyMode, autoHud, hudMode);
    if (command.isEmpty()) return;
    launchExternalTerminal(command, m_repository, QStringLiteral("Devtool — %1").arg(operation));
    saveSettings();
}

static void collectTestNames(const QJsonValue &value, QStringList *names, const QString &context = QString()) {
    if (!names) return;
    if (value.isArray()) { for (const QJsonValue &entry : value.toArray()) collectTestNames(entry, names, context); return; }
    if (value.isObject()) {
        const QJsonObject object = value.toObject();
        const bool testContext = context.contains(QStringLiteral("test"), Qt::CaseInsensitive)
            || object.contains(QStringLiteral("nodeid")) || object.contains(QStringLiteral("test"));
        if (testContext) {
            for (const QString &key : {QStringLiteral("id"), QStringLiteral("nodeid"), QStringLiteral("name"), QStringLiteral("test"), QStringLiteral("display_name")}) {
                const QString candidate = object.value(key).toString().trimmed();
                if (!candidate.isEmpty() && !names->contains(candidate)) { names->append(candidate); break; }
            }
        }
        for (auto it = object.constBegin(); it != object.constEnd(); ++it) collectTestNames(it.value(), names, context + QLatin1Char('/') + it.key());
    } else if (value.isString() && context.contains(QStringLiteral("test"), Qt::CaseInsensitive)) {
        const QString candidate = value.toString().trimmed();
        if (!candidate.isEmpty() && !names->contains(candidate)) names->append(candidate);
    }
}

QVariantMap AppController::discoverTests(const QString &repository, const QString &target, const QString &projectType,
                                         const QString &testProfile, const QString &query) const {
    QVariantMap result;
    const QString repo = repository.trimmed().isEmpty() ? m_repository : repository.trimmed();
    if (repo.isEmpty() || !QFileInfo(repo).isDir()) {
        result[QStringLiteral("ok")] = false;
        result[QStringLiteral("error")] = QStringLiteral("Choose a valid repository before discovering tests.");
        return result;
    }
    QStringList arguments { QStringLiteral("--machine"), QStringLiteral("pretty"), QStringLiteral("--no-clipboard"),
        QStringLiteral("--no-hud"), QStringLiteral("--yes"), QStringLiteral("-r"), repo };
    const QString type = projectType.trimmed().toLower();
    if (!type.isEmpty() && type != QStringLiteral("auto")) arguments << QStringLiteral("--project-type") << type;
    if (!target.trimmed().isEmpty()) arguments << QStringLiteral("--target") << target.trimmed();
    arguments << QStringLiteral("test") << QStringLiteral("--list-tests");
    if (!testProfile.trimmed().isEmpty()) arguments << QStringLiteral("--profile") << testProfile.trimmed();
    QProcess process; process.setWorkingDirectory(repo); process.start(m_devtool, arguments);
    if (!process.waitForStarted(3000)) { result[QStringLiteral("ok")]=false; result[QStringLiteral("error")]=process.errorString(); return result; }
    if (!process.waitForFinished(30000)) { process.kill(); process.waitForFinished(1000); result[QStringLiteral("ok")]=false; result[QStringLiteral("error")]=QStringLiteral("Test discovery timed out after 30 seconds."); return result; }
    const QString output = QString::fromUtf8(process.readAllStandardOutput());
    const QString errors = QString::fromUtf8(process.readAllStandardError()).trimmed();
    QStringList names; QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(output.toUtf8(), &parseError);
    if (parseError.error == QJsonParseError::NoError && !document.isNull())
        collectTestNames(document.isObject() ? QJsonValue(document.object()) : QJsonValue(document.array()), &names, QStringLiteral("tests"));
    if (names.isEmpty()) {
        const QStringList lines = output.split(QLatin1Char('\n'), Qt::SkipEmptyParts);
        for (QString line : lines) {
            line = line.trimmed(); line.remove(QRegularExpression(QStringLiteral("^[•*+\\-\\s]+")));
            if ((line.contains(QStringLiteral("::")) || line.contains(QLatin1Char('/'))) && !names.contains(line)) names.append(line);
        }
    }
    QVariantList items; const QString filter = query.trimmed();
    for (const QString &name : std::as_const(names)) {
        if (!filter.isEmpty() && !name.contains(filter, Qt::CaseInsensitive)) continue;
        const int marker = name.lastIndexOf(QStringLiteral("::"));
        const QString suite = marker > 0 ? name.left(marker) : QString();
        items << QVariantMap{{QStringLiteral("id"), name}, {QStringLiteral("label"), name}, {QStringLiteral("suite"), suite}, {QStringLiteral("durationSeconds"), 0.0}};
    }
    const bool ok = process.exitStatus() == QProcess::NormalExit && process.exitCode() == 0;
    result[QStringLiteral("ok")] = ok; result[QStringLiteral("items")] = items; result[QStringLiteral("count")] = items.size();
    if (!ok) result[QStringLiteral("error")] = errors.isEmpty() ? QStringLiteral("devtool test discovery failed.") : errors;
    else if (items.isEmpty()) result[QStringLiteral("warning")] = QStringLiteral("Discovery completed, but no test identifiers were found.");
    return result;
}

void AppController::runHistoricalCommand(const QString &command, const QString &repository) {
    const QString cleaned = normalizedClipboardCommand(command);
    if (cleaned.isEmpty()) return;
    launchExternalTerminal(cleaned, repository.trimmed().isEmpty() ? m_repository : repository.trimmed(), QStringLiteral("Devtool — rerun"));
}

void AppController::copyText(const QString &t) { QGuiApplication::clipboard()->setText(t); }
void AppController::openPath(const QString &p) {
    if (p.isEmpty()) return;
    QString resolved = p;
    if (QDir::isRelativePath(resolved) && !m_repository.isEmpty()) resolved = QDir(m_repository).filePath(resolved);
    QDesktopServices::openUrl(QUrl::fromLocalFile(resolved));
}
void AppController::openUrl(const QString &url) {
    const QUrl parsed = QUrl::fromUserInput(url);
    if (parsed.isValid()) QDesktopServices::openUrl(parsed);
}
void AppController::quit() { qApp->quit(); }
void AppController::cancelRun(const QString &runId) { if (m_ipc) m_ipc->cancelRun(runId); }
void AppController::launchHud() {
    if (sendHudActivation()) {
        qInfo() << "Devtool HUD: activated existing window";
        return;
    }
    const QString executable = resolveHudExecutable();
    if (executable.isEmpty()) {
        qCritical() << "Devtool HUD executable was not found beside the Control Center or on PATH";
        if (m_tray) m_tray->showMessage(QStringLiteral("Devtool HUD could not open"),
                                        QStringLiteral("devtool-control-center-hud was not found"),
                                        QSystemTrayIcon::Critical, 7000);
        return;
    }
    qint64 processId = 0;
    if (!QProcess::startDetached(executable, QStringList{}, QString(), &processId)) {
        qCritical() << "Devtool HUD failed to start:" << executable;
        if (m_tray) m_tray->showMessage(QStringLiteral("Devtool HUD could not open"),
                                        QStringLiteral("The HUD process failed to start"),
                                        QSystemTrayIcon::Critical, 7000);
        return;
    }
    qInfo() << "Devtool HUD started with PID" << processId;
}

void AppController::setStandby(bool standby) {
    if (m_standby == standby) return;
    m_standby = standby;
    if (m_store) m_store->setStandby(standby);
    emit standbyChanged(standby);
}

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

void AppController::captureClipboardCandidate() {
    const QString text = clipboardCandidateText();
    const QString kind = clipboardCandidateKind();
    const QString summary = clipboardCandidateSummary();
    const QString timestamp = clipboardCandidateTimestamp();
    if (text == m_clipboardCandidateSnapshotText
        && kind == m_clipboardCandidateSnapshotKind
        && summary == m_clipboardCandidateSnapshotSummary
        && timestamp == m_clipboardCandidateSnapshotTimestamp) {
        return;
    }
    m_clipboardCandidateSnapshotText = text;
    m_clipboardCandidateSnapshotKind = kind;
    m_clipboardCandidateSnapshotSummary = summary;
    m_clipboardCandidateSnapshotTimestamp = timestamp;
    emit clipboardCandidateSnapshotChanged();
}

void AppController::clearClipboardCandidateSnapshot() {
    if (m_clipboardCandidateSnapshotText.isEmpty() && m_clipboardCandidateSnapshotKind.isEmpty() && m_clipboardCandidateSnapshotSummary.isEmpty() && m_clipboardCandidateSnapshotTimestamp.isEmpty()) {
        return;
    }
    m_clipboardCandidateSnapshotText.clear();
    m_clipboardCandidateSnapshotKind.clear();
    m_clipboardCandidateSnapshotSummary.clear();
    m_clipboardCandidateSnapshotTimestamp.clear();
    emit clipboardCandidateSnapshotChanged();
}

void AppController::useClipboardCandidate() {
    if (!m_clipboardMonitor) return;
    captureClipboardCandidate();
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
    clearClipboardCandidateSnapshot();
}

void AppController::runClipboardCommand() {
    if (m_clipboardCandidateSnapshotKind != QStringLiteral("command")) captureClipboardCandidate();
    if (m_clipboardCandidateSnapshotKind != QStringLiteral("command")) return;
    const QString command = normalizedClipboardCommand(m_clipboardCandidateSnapshotText);
    if (command.isEmpty()) return;
    const QString workingDirectory = QFileInfo(m_repository).isDir() ? m_repository : QDir::homePath();
    if (!launchExternalTerminal(QStringLiteral("export DEVTOOL_CONTROL_CENTER_ACTIVE=1; ") + command,
                                workingDirectory, QStringLiteral("Devtool command"))) return;
    m_clipboardCandidateSnapshotKind.clear();
    m_clipboardCandidateSnapshotText.clear();
    m_clipboardCandidateSnapshotSummary.clear();
    m_clipboardCandidateSnapshotTimestamp.clear();
    emit clipboardCandidateSnapshotChanged();
}
