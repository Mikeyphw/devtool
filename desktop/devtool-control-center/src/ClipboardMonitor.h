#pragma once

#include <QFileSystemWatcher>
#include <QObject>
#include <QSettings>
#include <QString>

class QClipboard;
class QTimer;

class ClipboardMonitor final : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString candidateText READ candidateText NOTIFY candidateChanged)
    Q_PROPERTY(QString candidateKind READ candidateKind NOTIFY candidateChanged)
    Q_PROPERTY(QString candidateSummary READ candidateSummary NOTIFY candidateChanged)
    Q_PROPERTY(QString candidateTimestamp READ candidateTimestamp NOTIFY candidateChanged)
public:
    explicit ClipboardMonitor(bool watchClipboard, bool notificationsEnabled = false, QObject *parent = nullptr);

    QString candidateText() const { return m_candidateText; }
    QString candidateKind() const { return m_candidateKind; }
    QString candidateSummary() const { return m_candidateSummary; }
    QString candidateTimestamp() const { return m_candidateTimestamp; }

    Q_INVOKABLE bool scanCurrentClipboard();
    Q_INVOKABLE void clearCandidate();

signals:
    void candidateChanged();
    void candidateDetected(const QString &kind, const QString &summary);

private:
    void scheduleScan();
    void scheduleSettingsRefresh();
    void refreshSettingsWatch();
    void refreshFromSettings();
    bool processText(const QString &text, bool notify);
    static QString classify(const QString &text, QString *normalized, QString *summary);
    void persistCandidate(const QString &kind, const QString &text, const QString &summary);
    void showNotification(const QString &kind, const QString &summary) const;

    QSettings m_settings;
    QClipboard *m_clipboard = nullptr;
    QTimer *m_scanTimer = nullptr;
    QTimer *m_settingsRefreshTimer = nullptr;
    QFileSystemWatcher m_settingsWatcher;
    QString m_candidateText;
    QString m_candidateKind;
    QString m_candidateSummary;
    QString m_candidateTimestamp;
    QString m_lastProcessed;
    bool m_notificationsEnabled = false;
};
