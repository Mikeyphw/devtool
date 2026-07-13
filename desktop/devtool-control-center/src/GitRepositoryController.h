#pragma once

#include <QObject>
#include <QProcess>
#include <QVariantList>

class HistoryModel;

class GitRepositoryController final : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString repositoryPath READ repositoryPath WRITE setRepositoryPath NOTIFY repositoryPathChanged)
    Q_PROPERTY(bool busy READ busy NOTIFY busyChanged)
    Q_PROPERTY(QString branch READ branch NOTIFY refreshed)
    Q_PROPERTY(QString remote READ remote NOTIFY refreshed)
    Q_PROPERTY(bool dirty READ dirty NOTIFY refreshed)
    Q_PROPERTY(QString statusText READ statusText NOTIFY refreshed)
    Q_PROPERTY(QString output READ output NOTIFY outputChanged)
    Q_PROPERTY(QString error READ error NOTIFY errorChanged)
    Q_PROPERTY(QVariantList branches READ branches NOTIFY refreshed)
    Q_PROPERTY(QVariantList statusEntries READ statusEntries NOTIFY refreshed)
    Q_PROPERTY(QVariantList commits READ commits NOTIFY refreshed)
public:
    explicit GitRepositoryController(HistoryModel *history, QObject *parent = nullptr);

    QString repositoryPath() const { return m_repositoryPath; }
    bool busy() const { return m_process.state() != QProcess::NotRunning; }
    QString branch() const { return m_branch; }
    QString remote() const { return m_remote; }
    bool dirty() const { return m_dirty; }
    QString statusText() const { return m_statusText; }
    QString output() const { return m_output; }
    QString error() const { return m_error; }
    QVariantList branches() const { return m_branches; }
    QVariantList statusEntries() const { return m_statusEntries; }
    QVariantList commits() const { return m_commits; }

    void setRepositoryPath(const QString &path);

    Q_INVOKABLE void refresh();
    Q_INVOKABLE void fetch();
    Q_INVOKABLE void pull();
    Q_INVOKABLE void push();
    Q_INVOKABLE void stageAll();
    Q_INVOKABLE void unstageAll();
    Q_INVOKABLE void stagePath(const QString &path);
    Q_INVOKABLE void unstagePath(const QString &path);
    Q_INVOKABLE void discardPath(const QString &path);
    Q_INVOKABLE void stash();
    Q_INVOKABLE void stashPop();
    Q_INVOKABLE void commit(const QString &message);
    Q_INVOKABLE void checkout(const QString &reference);
    Q_INVOKABLE void createBranch(const QString &name);
    Q_INVOKABLE void discardWorkingTree();
    Q_INVOKABLE QString commitUrl(const QString &hash) const;

signals:
    void repositoryPathChanged();
    void busyChanged();
    void refreshed();
    void outputChanged();
    void errorChanged();

private:
    enum class Mode { Refresh, Operation };
    void runGit(const QStringList &arguments, const QString &description);
    void runRefreshScript();
    void parseRefreshOutput(const QString &text);
    void setError(const QString &value);
    static QString shellQuote(const QString &value);

    HistoryModel *m_history = nullptr;
    QProcess m_process;
    Mode m_mode = Mode::Refresh;
    QString m_repositoryPath;
    QString m_branch;
    QString m_remote;
    QString m_statusText;
    QString m_output;
    QString m_error;
    QString m_operationDescription;
    bool m_dirty = false;
    QVariantList m_branches;
    QVariantList m_statusEntries;
    QVariantList m_commits;
};
