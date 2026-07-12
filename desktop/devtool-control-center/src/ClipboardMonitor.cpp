#include "ClipboardMonitor.h"

#include <QClipboard>
#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDesktopServices>
#include <QDir>
#include <QFileInfo>
#include <QGuiApplication>
#include <QProcess>
#include <QRegularExpression>
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

bool looksLikeDevtoolCommand(const QString &text) {
    static const QRegularExpression devtoolCommand(
        QStringLiteral(R"((^|[;&|\n]\s*)(?:env\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=[^\s]+\s+)*(?:[^\s]*/)?devtool(?:-androidos)?(?:\s+.+))"),
        QRegularExpression::CaseInsensitiveOption);
    return devtoolCommand.match(text).hasMatch();
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

    m_refreshTimer = new QTimer(this);
    m_refreshTimer->setInterval(1500);
    connect(m_refreshTimer, &QTimer::timeout, this, &ClipboardMonitor::refreshFromSettings);
    m_refreshTimer->start();

    if (watchClipboard) {
        m_clipboard = QGuiApplication::clipboard();
        connect(m_clipboard, &QClipboard::dataChanged, this, &ClipboardMonitor::scheduleScan);
        QTimer::singleShot(250, this, [this] { scanCurrentClipboard(); });
    }
}

void ClipboardMonitor::scheduleScan() {
    if (m_scanTimer) m_scanTimer->start();
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
    QString text = rawText.trimmed();
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

    if (looksLikeDevtoolCommand(text)) {
        if (normalized) *normalized = text.left(16384);
        if (summary) *summary = QStringLiteral("Devtool command: %1").arg(compactCommandSummary(text));
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
}

void ClipboardMonitor::clearCandidate() {
    m_settings.remove(QStringLiteral("clipboard/latest"));
    m_settings.sync();
    m_lastProcessed.clear();
    refreshFromSettings();
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
