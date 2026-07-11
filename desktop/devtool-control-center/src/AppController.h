#pragma once
#include <QObject>
#include <QSettings>
#include <QHash>

class ClipboardMonitor;
class HudStateStore;
class ProcessRunner;
class HistoryModel;
class QSystemTrayIcon;
class IpcClient;
class QTimer;

class AppController final : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString repository READ repository WRITE setRepository NOTIFY repositoryChanged)
    Q_PROPERTY(QString artifact READ artifact WRITE setArtifact NOTIFY artifactChanged)
    Q_PROPERTY(QString devtoolExecutable READ devtoolExecutable WRITE setDevtoolExecutable NOTIFY devtoolExecutableChanged)
    Q_PROPERTY(bool compactHudVisible READ compactHudVisible WRITE setCompactHudVisible NOTIFY compactHudVisibleChanged)
    Q_PROPERTY(bool devtoolAvailable READ devtoolAvailable NOTIFY devtoolExecutableChanged)
    Q_PROPERTY(bool trayAvailable READ trayAvailable CONSTANT)
    Q_PROPERTY(QString clipboardCandidateText READ clipboardCandidateText NOTIFY clipboardCandidateChanged)
    Q_PROPERTY(QString clipboardCandidateKind READ clipboardCandidateKind NOTIFY clipboardCandidateChanged)
    Q_PROPERTY(QString clipboardCandidateSummary READ clipboardCandidateSummary NOTIFY clipboardCandidateChanged)
    Q_PROPERTY(QString clipboardCandidateTimestamp READ clipboardCandidateTimestamp NOTIFY clipboardCandidateChanged)
    Q_PROPERTY(bool clipboardMonitorInstalled READ clipboardMonitorInstalled NOTIFY clipboardMonitorStatusChanged)
    Q_PROPERTY(bool clipboardMonitorActive READ clipboardMonitorActive NOTIFY clipboardMonitorStatusChanged)
    Q_PROPERTY(QString clipboardMonitorStatus READ clipboardMonitorStatus NOTIFY clipboardMonitorStatusChanged)
    Q_PROPERTY(bool startCompactOnly READ startCompactOnly CONSTANT)
public:
    AppController(HudStateStore *store, ProcessRunner *runner, HistoryModel *history, IpcClient *ipc, bool startCompactOnly = false, QObject *parent = nullptr);
    QString repository() const { return m_repository; }
    QString artifact() const { return m_artifact; }
    QString devtoolExecutable() const { return m_devtool; }
    bool compactHudVisible() const { return m_compactHudVisible; }
    bool trayAvailable() const { return m_tray != nullptr; }
    bool devtoolAvailable() const;
    QString clipboardCandidateText() const;
    QString clipboardCandidateKind() const;
    QString clipboardCandidateSummary() const;
    QString clipboardCandidateTimestamp() const;
    bool clipboardMonitorInstalled() const { return m_clipboardMonitorInstalled; }
    bool clipboardMonitorActive() const { return m_clipboardMonitorActive; }
    QString clipboardMonitorStatus() const { return m_clipboardMonitorStatus; }
    bool startCompactOnly() const { return m_startCompactOnly; }

    void setRepository(const QString &);
    void setArtifact(const QString &);
    void setDevtoolExecutable(const QString &);
    void setCompactHudVisible(bool);

    Q_INVOKABLE void runWorkflow(const QString &operation, const QString &target, bool validate, bool commit, const QString &commitMessage, bool keepArtifact);
    Q_INVOKABLE void showMainWindow() { emit requestMainWindow(); }
    Q_INVOKABLE void copyText(const QString &);
    Q_INVOKABLE void openPath(const QString &);
    Q_INVOKABLE void saveSettings();
    Q_INVOKABLE void quit();
    Q_INVOKABLE void inspectArtifactRequested() { emit requestArtifactInspector(); }
    Q_INVOKABLE void cancelRun(const QString &runId);

    Q_INVOKABLE void installClipboardMonitor();
    Q_INVOKABLE void enableClipboardMonitor();
    Q_INVOKABLE void disableClipboardMonitor();
    Q_INVOKABLE void removeClipboardMonitor();
    Q_INVOKABLE void refreshClipboardMonitorStatus();
    Q_INVOKABLE void scanClipboardNow();
    Q_INVOKABLE void useClipboardCandidate();
    Q_INVOKABLE void clearClipboardCandidate();
    Q_INVOKABLE void runClipboardCommand();

signals:
    void repositoryChanged();
    void artifactChanged();
    void devtoolExecutableChanged();
    void compactHudVisibleChanged();
    void requestMainWindow();
    void requestArtifactInspector();
    void requestRunPage();
    void clipboardCandidateChanged();
    void clipboardMonitorStatusChanged();

private:
    QString clipboardServicePath() const;
    bool runUserSystemctl(const QStringList &arguments, QString *output = nullptr, int timeoutMs = 8000) const;
    void setClipboardMonitorStatus(bool installed, bool active, const QString &status);

    HudStateStore *m_store;
    ProcessRunner *m_runner;
    HistoryModel *m_history;
    IpcClient *m_ipc;
    QSettings m_settings;
    QString m_repository;
    QString m_artifact;
    QString m_devtool = QStringLiteral("devtool");
    bool m_compactHudVisible = false;
    QSystemTrayIcon *m_tray = nullptr;
    QHash<QString, QString> m_lastRunStatus;
    ClipboardMonitor *m_clipboardMonitor = nullptr;
    QTimer *m_clipboardStatusTimer = nullptr;
    bool m_clipboardMonitorInstalled = false;
    bool m_clipboardMonitorActive = false;
    QString m_clipboardMonitorStatus = QStringLiteral("Not installed");
    bool m_startCompactOnly = false;
};
