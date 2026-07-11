#pragma once

#include <QObject>
#include <QFileSystemWatcher>
#include <QJsonObject>
#include <QHash>
#include <QList>
#include <QTimer>
#include <QSettings>
#include <QVariantList>

class HistoryModel;

class HudStateStore final : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString stateDirectory READ stateDirectory WRITE setStateDirectory NOTIFY stateDirectoryChanged)
    Q_PROPERTY(QVariantMap activeRun READ activeRun NOTIFY activeRunChanged)
    Q_PROPERTY(QVariantList activeRuns READ activeRuns NOTIFY activeRunsChanged)
    Q_PROPERTY(bool connected READ connected NOTIFY connectedChanged)
    Q_PROPERTY(bool ipcConnected READ ipcConnected NOTIFY ipcConnectedChanged)
    Q_PROPERTY(QString selectedRunId READ selectedRunId WRITE setSelectedRunId NOTIFY selectedRunIdChanged)
    Q_PROPERTY(QVariantList diagnostics READ diagnostics NOTIFY diagnosticsChanged)
public:
    explicit HudStateStore(HistoryModel *history, QObject *parent = nullptr);
    QString stateDirectory() const { return m_stateDirectory; }
    QVariantMap activeRun() const { return m_activeRun.toVariantMap(); }
    QVariantList activeRuns() const { return m_activeRuns; }
    bool connected() const { return m_connected; }
    bool ipcConnected() const { return m_ipcConnected; }
    QString selectedRunId() const { return m_selectedRunId; }
    QVariantList diagnostics() const { return m_diagnostics; }
    Q_INVOKABLE void refresh();
    Q_INVOKABLE void setStateDirectory(const QString &path);
    Q_INVOKABLE void setSelectedRunId(const QString &runId);
    Q_INVOKABLE QVariantList stepsFor(const QVariantMap &run) const;
    Q_INVOKABLE QString focusedSummary(const QVariantMap &run) const;
public slots:
    void ingestSnapshot(const QVariantList &runs);
    void ingestRun(const QVariantMap &run);
    void removeRun(const QString &runId);
    void setIpcConnected(bool connected);
signals:
    void stateDirectoryChanged(); void activeRunChanged(); void activeRunsChanged(); void connectedChanged();
    void selectedRunIdChanged(); void diagnosticsChanged(); void ipcConnectedChanged();
private:
    QString resolveActiveDirectory() const;
    QJsonObject readObject(const QString &path) const;
    QJsonObject normalize(QJsonObject state) const;
    QVariantList buildSteps(const QJsonObject &state) const;
    QVariantList buildDiagnostics(const QJsonObject &state) const;
    void rebuildWatchList();
    void applyObjects(const QList<QJsonObject> &objects);
    HistoryModel *m_history;
    QString m_stateDirectory;
    QJsonObject m_activeRun;
    QVariantList m_activeRuns;
    QVariantList m_diagnostics;
    QString m_selectedRunId;
    bool m_connected = false;
    bool m_ipcConnected = false;
    QHash<QString,QJsonObject> m_ipcRuns;
    QFileSystemWatcher m_watcher;
    QTimer m_poll;
    QSettings m_settings;
};
