#pragma once
#include <QObject>
#include <QSettings>
#include <QHash>
#include <QVariantMap>
#include <QStringList>

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
    Q_PROPERTY(bool artifactExists READ artifactExists NOTIFY artifactChanged)
    Q_PROPERTY(QString preferredShell READ preferredShell CONSTANT)
    Q_PROPERTY(QString preferredTerminal READ preferredTerminal CONSTANT)
    Q_PROPERTY(QString dotnetExecutable READ dotnetExecutable CONSTANT)
    Q_PROPERTY(bool autoCloseTerminal READ autoCloseTerminal WRITE setAutoCloseTerminal NOTIFY autoCloseTerminalChanged)
    Q_PROPERTY(double typographyScale READ typographyScale WRITE setTypographyScale NOTIFY typographyScaleChanged)
    Q_PROPERTY(QString launchStatus READ launchStatus NOTIFY launchStatusChanged)
    Q_PROPERTY(QString devtoolExecutable READ devtoolExecutable WRITE setDevtoolExecutable NOTIFY devtoolExecutableChanged)
    Q_PROPERTY(bool devtoolAvailable READ devtoolAvailable NOTIFY devtoolExecutableChanged)
    Q_PROPERTY(bool trayAvailable READ trayAvailable CONSTANT)
    Q_PROPERTY(QString clipboardCandidateText READ clipboardCandidateText NOTIFY clipboardCandidateChanged)
    Q_PROPERTY(QString clipboardCandidateKind READ clipboardCandidateKind NOTIFY clipboardCandidateChanged)
    Q_PROPERTY(QString clipboardCandidateSummary READ clipboardCandidateSummary NOTIFY clipboardCandidateChanged)
    Q_PROPERTY(QString clipboardCandidateTimestamp READ clipboardCandidateTimestamp NOTIFY clipboardCandidateChanged)
    Q_PROPERTY(QString clipboardCandidateSnapshotText READ clipboardCandidateSnapshotText NOTIFY clipboardCandidateSnapshotChanged)
    Q_PROPERTY(QString clipboardCandidateSnapshotKind READ clipboardCandidateSnapshotKind NOTIFY clipboardCandidateSnapshotChanged)
    Q_PROPERTY(QString clipboardCandidateSnapshotSummary READ clipboardCandidateSnapshotSummary NOTIFY clipboardCandidateSnapshotChanged)
    Q_PROPERTY(QString clipboardCandidateSnapshotTimestamp READ clipboardCandidateSnapshotTimestamp NOTIFY clipboardCandidateSnapshotChanged)
    Q_PROPERTY(bool clipboardMonitorInstalled READ clipboardMonitorInstalled NOTIFY clipboardMonitorStatusChanged)
    Q_PROPERTY(bool clipboardMonitorActive READ clipboardMonitorActive NOTIFY clipboardMonitorStatusChanged)
    Q_PROPERTY(QString clipboardMonitorStatus READ clipboardMonitorStatus NOTIFY clipboardMonitorStatusChanged)
public:
    AppController(HudStateStore *store, ProcessRunner *runner, HistoryModel *history, IpcClient *ipc, QObject *parent = nullptr);
    QString repository() const { return m_repository; }
    QString artifact() const { return m_artifact; }
    bool artifactExists() const;
    QString preferredShell() const;
    QString preferredTerminal() const;
    QString dotnetExecutable() const;
    bool autoCloseTerminal() const { return m_autoCloseTerminal; }
    double typographyScale() const { return m_typographyScale; }
    QString launchStatus() const { return m_launchStatus; }
    QString devtoolExecutable() const { return m_devtool; }
    bool trayAvailable() const { return m_tray != nullptr; }
    bool devtoolAvailable() const;
    QString clipboardCandidateText() const;
    Q_INVOKABLE QString clipboardCandidateCopyText() const;
    QString clipboardCandidateKind() const;
    QString clipboardCandidateSummary() const;
    QString clipboardCandidateTimestamp() const;
    QString clipboardCandidateSnapshotText() const;
    QString clipboardCandidateSnapshotKind() const;
    QString clipboardCandidateSnapshotSummary() const;
    QString clipboardCandidateSnapshotTimestamp() const;
    bool clipboardMonitorInstalled() const { return m_clipboardMonitorInstalled; }
    bool clipboardMonitorActive() const { return m_clipboardMonitorActive; }
    QString clipboardMonitorStatus() const { return m_clipboardMonitorStatus; }
    void setRepository(const QString &);
    void setArtifact(const QString &);
    void setDevtoolExecutable(const QString &);
    void setAutoCloseTerminal(bool value);
    void setTypographyScale(double value);

    Q_INVOKABLE QString buildWorkflowCommand(
        const QString &operation, const QString &target, const QString &projectType,
        const QString &executionEnvironment, const QString &testProfile,
        const QString &selectors, const QString &tags, const QString &excludeTags,
        const QString &testPath, const QString &testClass, const QString &testMethod,
        bool listTests, bool instrumented, bool validate, bool commit,
        const QString &commitMessage, bool keepArtifact, bool noManifestCheck,
        const QString &copyMode, bool autoHud, const QString &hudMode) const;
    Q_INVOKABLE void runWorkflow(
        const QString &operation, const QString &target, const QString &projectType,
        const QString &executionEnvironment, const QString &testProfile,
        const QString &selectors, const QString &tags, const QString &excludeTags,
        const QString &testPath, const QString &testClass, const QString &testMethod,
        bool listTests, bool instrumented, bool validate, bool commit,
        const QString &commitMessage, bool keepArtifact, bool noManifestCheck,
        const QString &copyMode, bool autoHud, const QString &hudMode);
    Q_INVOKABLE QVariantMap discoverTests(
        const QString &repository, const QString &target, const QString &projectType,
        const QString &testProfile, const QString &query = QString()) const;
    Q_INVOKABLE void runHistoricalCommand(const QString &command, const QString &repository);
    Q_INVOKABLE void showMainWindow() { emit requestMainWindow(); }
    Q_INVOKABLE void copyText(const QString &);
    Q_INVOKABLE void openPath(const QString &);
    Q_INVOKABLE void openUrl(const QString &);
    Q_INVOKABLE QVariantMap pasteCommandFromClipboard();
    Q_INVOKABLE QString chooseRepositoryDirectory(const QString &initialPath);
    Q_INVOKABLE QVariantMap parseDevtoolCommand(const QString &command) const;
    Q_INVOKABLE void refreshArtifactState() { emit artifactChanged(); }
    Q_INVOKABLE void saveSettings();
    Q_INVOKABLE void quit();
    Q_INVOKABLE void inspectArtifactRequested() { emit requestArtifactInspector(); }
    Q_INVOKABLE void cancelRun(const QString &runId);
    Q_INVOKABLE void launchHud();
    Q_INVOKABLE void setStandby(bool standby);

    Q_INVOKABLE void installClipboardMonitor();
    Q_INVOKABLE void enableClipboardMonitor();
    Q_INVOKABLE void disableClipboardMonitor();
    Q_INVOKABLE void removeClipboardMonitor();
    Q_INVOKABLE void refreshClipboardMonitorStatus();
    Q_INVOKABLE void scanClipboardNow();
    Q_INVOKABLE void captureClipboardCandidate();
    Q_INVOKABLE void clearClipboardCandidateSnapshot();
    Q_INVOKABLE void useClipboardCandidate();
    Q_INVOKABLE void clearClipboardCandidate();
    Q_INVOKABLE void runClipboardCommand();

signals:
    void repositoryChanged();
    void artifactChanged();
    void devtoolExecutableChanged();
    void requestMainWindow();
    void requestArtifactInspector();
    void requestRunPage();
    void clipboardCandidateChanged();
    void clipboardCandidateSnapshotChanged();
    void clipboardMonitorStatusChanged();
    void standbyChanged(bool standby);
    void autoCloseTerminalChanged();
    void typographyScaleChanged();
    void launchStatusChanged();

private:
    QString clipboardServicePath() const;
    bool runUserSystemctl(const QStringList &arguments, QString *output = nullptr, int timeoutMs = 8000) const;
    void setClipboardMonitorStatus(bool installed, bool active, const QString &status);
    QString augmentedPath() const;
    bool launchExternalTerminal(const QString &command, const QString &workingDirectory, const QString &title);
    void setLaunchStatus(const QString &status);

    HudStateStore *m_store;
    ProcessRunner *m_runner;
    HistoryModel *m_history;
    IpcClient *m_ipc;
    QSettings m_settings;
    QString m_repository;
    QString m_artifact;
    QString m_devtool = QStringLiteral("devtool");
    QSystemTrayIcon *m_tray = nullptr;
    QHash<QString, QString> m_lastRunStatus;
    ClipboardMonitor *m_clipboardMonitor = nullptr;
    QString m_clipboardCandidateSnapshotText;
    QString m_clipboardCandidateSnapshotKind;
    QString m_clipboardCandidateSnapshotSummary;
    QString m_clipboardCandidateSnapshotTimestamp;
    bool m_clipboardMonitorInstalled = false;
    bool m_clipboardMonitorActive = false;
    QString m_clipboardMonitorStatus = QStringLiteral("Not installed");
    bool m_standby = false;
    bool m_autoCloseTerminal = false;
    double m_typographyScale = 1.0;
    double m_baseFontPointSize = 11.0;
    QString m_launchStatus;
};
