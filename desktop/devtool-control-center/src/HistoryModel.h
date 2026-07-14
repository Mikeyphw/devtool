#pragma once

#include <QAbstractListModel>
#include <QHash>
#include <QJsonObject>
#include <QStringList>
#include <QVariantList>
#include <QVector>

class HistoryModel final : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ count NOTIFY countChanged)

public:
    enum Roles {
        RunIdRole = Qt::UserRole + 1,
        TitleRole,
        RepoRole,
        TargetRole,
        StatusRole,
        StartedRole,
        DurationRole,
        ErrorsRole,
        WarningsRole,
        SummaryRole,
        CommandRole,
        StepsRole,
        RepoRootRole,
        ArtifactNameRole,
        ArtifactPathRole,
        GitCommitRole,
        LogPathRole,
        ProjectTypeRole,
        ExecutionEnvironmentRole
    };

    explicit HistoryModel(QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = {}) const override;
    int count() const;
    QVariant data(const QModelIndex &index, int role) const override;
    QHash<int, QByteArray> roleNames() const override;

    Q_INVOKABLE void clear();
    Q_INVOKABLE bool remove(const QString &runId);
    Q_INVOKABLE QVariantMap get(int row) const;
    Q_INVOKABLE QVariantMap getByRunId(const QString &runId) const;
    Q_INVOKABLE QVariantList search(
        const QString &query = QString(),
        const QString &status = QStringLiteral("all"),
        const QString &repository = QString()) const;
    Q_INVOKABLE QVariantList repositories() const;
    Q_INVOKABLE QVariantList forRepository(const QString &path) const;
    Q_INVOKABLE QVariantMap artifactForCommit(
        const QString &hash,
        const QString &repoPath = QString()) const;

    void record(const QJsonObject &state);

signals:
    void countChanged();

private:
    QString storagePath() const;
    void ensureLoaded();
    void load();
    void save() const;
    QString buildSearchText(const QJsonObject &item) const;
    void rebuildSearchIndex();

    QVector<QJsonObject> m_items;
    QVector<QString> m_searchText;
    bool m_loaded = false;
};
