#include "ClipboardMonitor.h"

#include <QClipboard>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QFileInfo>
#include <QGuiApplication>
#include <QProcess>
#include <QRegularExpression>
#include <QSet>
#include <QStandardPaths>
#include <QTimer>
#include <QUrl>

namespace {
QString unquote(QString value) {
    value = value.trimmed();
    if (value.size() >= 2) {
        const QChar first = value.front();
        const QChar last = value.back();
        if ((first == QLatin1Char('"') && last == QLatin1Char('"')) ||
            (first == QLatin1Char('\'') && last == QLatin1Char('\''))) {
            value = value.mid(1, value.size() - 2).trimmed();
        }
    }
    return value;
}

bool looksLikeArtifact(const QString &path) {
    const QString lower = path.toLower();
    return lower.endsWith(QStringLiteral(".zip")) ||
           lower.endsWith(QStringLiteral(".tar")) ||
           lower.endsWith(QStringLiteral(".tar.gz")) ||
           lower.endsWith(QStringLiteral(".tgz")) ||
           lower.endsWith(QStringLiteral(".tar.zst")) ||
           lower.endsWith(QStringLiteral(".zst"));
}

QString compactCommandSummary(const QString &text) {
    QString oneLine = text.simplified();
    if (oneLine.size() > 110) oneLine = oneLine.left(107) + QStringLiteral("…");
    return oneLine;
}

QString stripPrompt(QString line) {
    line = line.trimmed();
    static const QStringList prompts { QStringLiteral("$ "), QStringLiteral("❯ "), QStringLiteral("> ") };
    for (const QString &prompt : prompts) {
        if (line.startsWith(prompt)) return line.mid(prompt.size()).trimmed();
    }
    return line;
}

bool reportHeader(const QString &line) {
    static const QRegularExpression report(
        QStringLiteral(R"(^Devtool\s+(?:failed|completed|succeeded|cancelled|interrupted|finished)\b[\s.!:;-]*$)"),
        QRegularExpression::CaseInsensitiveOption);
    return report.match(line.trimmed()).hasMatch();
}

bool recognizedSubcommand(const QString &command) {
    static const QSet<QString> commands {
        QStringLiteral("apply-overlay"), QStringLiteral("apply-overlays"), QStringLiteral("extract-overlay"),
        QStringLiteral("inspect-artifact"), QStringLiteral("validate-artifact"), QStringLiteral("validate"),
        QStringLiteral("restore"), QStringLiteral("build"), QStringLiteral("test"), QStringLiteral("run"),
        QStringLiteral("status"), QStringLiteral("doctor"), QStringLiteral("repair"), QStringLiteral("rollback"),
        QStringLiteral("hud"), QStringLiteral("control-center"), QStringLiteral("ipc-server"),
        QStringLiteral("bundle-overlays"), QStringLiteral("config"), QStringLiteral("completion"),
        QStringLiteral("handoff"), QStringLiteral("watch"), QStringLiteral("bundle"),
        QStringLiteral("apply"), QStringLiteral("targets"), QStringLiteral("logs"),
        QStringLiteral("history"), QStringLiteral("package"), QStringLiteral("run"),
        QStringLiteral("ao"), QStringLiteral("aos"), QStringLiteral("as"), QStringLiteral("ia"),
        QStringLiteral("rs"), QStringLiteral("b"), QStringLiteral("t"), QStringLiteral("v"),
        QStringLiteral("wv"), QStringLiteral("rn"), QStringLiteral("pkg"), QStringLiteral("st"),
        QStringLiteral("dr"), QStringLiteral("tg"), QStringLiteral("ci"), QStringLiteral("ap")
    };
    return commands.contains(command);
}

QString devtoolSubcommand(const QString &line) {
    // Command names are intentionally case-sensitive. This prevents prose such
    // as "Devtool failed." from being treated as an executable invocation.
    static const QRegularExpression invocation(
        QStringLiteral(R"((?:^|\s)(?:[^\s]*/)?devtool(?:-androidos)?\s+(.+)$)"));
    const auto match = invocation.match(line);
    if (!match.hasMatch()) return {};

    const QStringList tokens = match.captured(1).split(QRegularExpression(QStringLiteral("\\s+")), Qt::SkipEmptyParts);
    static const QSet<QString> optionsWithValue {
        QStringLiteral("-r"), QStringLiteral("--repo-root"), QStringLiteral("--config"),
        QStringLiteral("--target"), QStringLiteral("--solution"), QStringLiteral("--project"),
        QStringLiteral("--hud-mode"), QStringLiteral("--copy-via")
    };
    for (int index = 0; index < tokens.size(); ++index) {
        const QString token = tokens.at(index);
        if (token == QStringLiteral("--")) continue;
        if (optionsWithValue.contains(token)) { ++index; continue; }
        if (token.startsWith(QLatin1Char('-'))) continue;
        return recognizedSubcommand(token) ? token : QString();
    }
    return {};
}

QString extractCommandBlock(const QString &rawText) {
    const QStringList rawLines = rawText.split(QLatin1Char('\n'));
    QStringList lines;
    for (const QString &raw : rawLines) {
        const QString line = stripPrompt(raw);
        if (!line.isEmpty()) lines << line;
    }
    if (lines.isEmpty() || reportHeader(lines.first())) return {};

    QStringList block;
    bool foundDevtool = false;
    for (int index = 0; index < lines.size(); ++index) {
        const QString line = lines.at(index);
        if (!foundDevtool) {
            const bool prelude = line.startsWith(QStringLiteral("cd ")) ||
                line.startsWith(QStringLiteral("pushd ")) ||
                line.startsWith(QStringLiteral("env ")) ||
                QRegularExpression(QStringLiteral(R"(^[A-Za-z_][A-Za-z0-9_]*=\S+(?:\s+.*)?$)")).match(line).hasMatch();
            const QString subcommand = devtoolSubcommand(line);
            if (subcommand.isEmpty()) {
                if (prelude && index < 8) { block << line; continue; }
                return {};
            }
            foundDevtool = true;
            block << line;
            if (!line.endsWith(QLatin1Char('\\'))) break;
        } else {
            block << line;
            if (!line.endsWith(QLatin1Char('\\'))) break;
        }
    }
    return foundDevtool ? block.join(QLatin1Char('\n')).trimmed() : QString();
}

bool looksLikeDevtoolCommand(const QString &text) {
    return !extractCommandBlock(text).isEmpty();
}
}

ClipboardMonitor::ClipboardMonitor(bool watchClipboard, bool notificationsEnabled, QObject *parent)
    : QObject(parent),
      m_settings(QStringLiteral("mikeceead"), QStringLiteral("DevtoolControlCenter")),
      m_notificationsEnabled(notificationsEnabled) {
    refreshFromSettings();

    m_scanTimer = new QTimer(this);
    m_scanTimer->setSingleShot(true);
    m_scanTimer->setInterval(180);
    connect(m_scanTimer, &QTimer::timeout, this, [this] { scanCurrentClipboard(); });

    m_settingsRefreshTimer = new QTimer(this);
    m_settingsRefreshTimer->setSingleShot(true);
    m_settingsRefreshTimer->setInterval(120);
    connect(m_settingsRefreshTimer, &QTimer::timeout, this, [this] {
        refreshFromSettings();
        refreshSettingsWatch();
    });
    connect(&m_settingsWatcher, &QFileSystemWatcher::fileChanged, this, &ClipboardMonitor::scheduleSettingsRefresh);
    connect(&m_settingsWatcher, &QFileSystemWatcher::directoryChanged, this, &ClipboardMonitor::scheduleSettingsRefresh);
    refreshSettingsWatch();

    if (watchClipboard) {
        m_clipboard = QGuiApplication::clipboard();
        connect(m_clipboard, &QClipboard::dataChanged, this, &ClipboardMonitor::scheduleScan);
        QTimer::singleShot(250, this, [this] { scanCurrentClipboard(); });
    }
}

void ClipboardMonitor::scheduleScan() {
    if (m_scanTimer) m_scanTimer->start();
}

void ClipboardMonitor::scheduleSettingsRefresh() {
    if (m_settingsRefreshTimer) m_settingsRefreshTimer->start();
}

void ClipboardMonitor::refreshSettingsWatch() {
    const QString filePath = m_settings.fileName();
    const QString directory = QFileInfo(filePath).absolutePath();
    if (QFileInfo::exists(directory) && !m_settingsWatcher.directories().contains(directory))
        m_settingsWatcher.addPath(directory);
    if (QFileInfo::exists(filePath) && !m_settingsWatcher.files().contains(filePath))
        m_settingsWatcher.addPath(filePath);
}

bool ClipboardMonitor::scanCurrentClipboard() {
    if (!m_clipboard) m_clipboard = QGuiApplication::clipboard();
    if (!m_clipboard) return false;
    return processText(m_clipboard->text(QClipboard::Clipboard), m_notificationsEnabled);
}

void ClipboardMonitor::refreshFromSettings() {
    m_settings.sync();
    const QString timestamp = m_settings.value(QStringLiteral("clipboard/latest/timestamp")).toString();
    const QString text = m_settings.value(QStringLiteral("clipboard/latest/text")).toString();
    const QString kind = m_settings.value(QStringLiteral("clipboard/latest/kind")).toString();
    const QString summary = m_settings.value(QStringLiteral("clipboard/latest/summary")).toString();
    if (timestamp == m_candidateTimestamp && text == m_candidateText && kind == m_candidateKind && summary == m_candidateSummary)
        return;
    m_candidateTimestamp = timestamp;
    m_candidateText = text;
    m_candidateKind = kind;
    m_candidateSummary = summary;
    emit candidateChanged();
}

QString ClipboardMonitor::classify(const QString &rawText, QString *normalized, QString *summary) {
    const QString text = rawText.trimmed();
    if (text.isEmpty() || text.size() > 65536) return {};

    const QString firstLine = unquote(text.section(QLatin1Char('\n'), 0, 0));
    QString path = firstLine;
    const QUrl url(firstLine);
    if (url.isValid() && url.isLocalFile()) path = url.toLocalFile();
    path = QDir::cleanPath(QDir::fromNativeSeparators(path));
    const QFileInfo file(path);
    if (looksLikeArtifact(path) && file.isFile()) {
        if (normalized) *normalized = file.absoluteFilePath();
        if (summary) *summary = QStringLiteral("Artifact: %1").arg(file.fileName());
        return QStringLiteral("artifact");
    }

    if (!looksLikeDevtoolCommand(text)) return {};
    const QString command = extractCommandBlock(text);
    if (!command.isEmpty()) {
        if (normalized) *normalized = command.left(16384);
        if (summary) *summary = QStringLiteral("Devtool command: %1").arg(compactCommandSummary(command));
        return QStringLiteral("command");
    }

    return {};
}

bool ClipboardMonitor::processText(const QString &text, bool notify) {
    QString normalized;
    QString summary;
    const QString kind = classify(text, &normalized, &summary);
    if (kind.isEmpty()) return false;

    const QString fingerprint = QString::fromLatin1(
        QCryptographicHash::hash((kind + QLatin1Char('\n') + normalized).toUtf8(), QCryptographicHash::Sha256).toHex());
    if (fingerprint == m_lastProcessed || fingerprint == m_settings.value(QStringLiteral("clipboard/latest/fingerprint")).toString())
        return false;
    m_lastProcessed = fingerprint;
    persistCandidate(kind, normalized, summary);
    m_settings.setValue(QStringLiteral("clipboard/latest/fingerprint"), fingerprint);
    m_settings.sync();
    if (notify) showNotification(kind, summary);
    emit candidateDetected(kind, summary);
    return true;
}

void ClipboardMonitor::persistCandidate(const QString &kind, const QString &text, const QString &summary) {
    const QString timestamp = QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs);
    m_settings.setValue(QStringLiteral("clipboard/latest/text"), text);
    m_settings.setValue(QStringLiteral("clipboard/latest/kind"), kind);
    m_settings.setValue(QStringLiteral("clipboard/latest/summary"), summary);
    m_settings.setValue(QStringLiteral("clipboard/latest/timestamp"), timestamp);
    m_settings.sync();
    refreshFromSettings();
    refreshSettingsWatch();
}

void ClipboardMonitor::clearCandidate() {
    m_settings.remove(QStringLiteral("clipboard/latest"));
    m_settings.sync();
    m_lastProcessed.clear();
    refreshFromSettings();
    refreshSettingsWatch();
}

void ClipboardMonitor::showNotification(const QString &kind, const QString &summary) const {
    const QString notifySend = QStandardPaths::findExecutable(QStringLiteral("notify-send"));
    if (notifySend.isEmpty()) return;
    const QString title = kind == QStringLiteral("artifact")
        ? QStringLiteral("Devtool artifact detected")
        : QStringLiteral("Devtool command detected");
    const QString body = kind == QStringLiteral("artifact")
        ? summary
        : QStringLiteral("A copied devtool command is ready to review.");
    QProcess::startDetached(notifySend, QStringList{
        QStringLiteral("--app-name=Devtool Control Center"),
        QStringLiteral("--icon=io.github.mikeceead.DevtoolControlCenter"),
        title,
        body + QStringLiteral("\nOpen Control Center → Settings to review it.")
    });
}
