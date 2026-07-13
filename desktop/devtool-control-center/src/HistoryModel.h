#pragma once

#include <QAbstractListModel>
#include <QJsonObject>
#include <QVector>
#include <QHash>
#include <QVariantList>

class HistoryModel final : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ count NOTIFY countChanged)
public:
    enum Roles { RunIdRole = Qt::UserRole + 1, TitleRole, RepoRole, TargetRole,
                 StatusRole, StartedRole, DurationRole, ErrorsRole, WarningsRole,
                 SummaryRole, CommandRole, StepsRole, RepoRootRole, ArtifactNameRole,
                 ArtifactPathRole, GitCommitRole };
    explicit HistoryModel(QObject *parent = nullptr);
    int rowCount(const QModelIndex &parent = {}) const override;
    int count() const;
    QVariant data(const QModelIndex &index, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
    Q_INVOKABLE void clear();
    Q_INVOKABLE QVariantMap get(int row) const;
    Q_INVOKABLE QVariantList forRepository(const QString &path) const;
    Q_INVOKABLE QVariantMap artifactForCommit(const QString &hash, const QString &repoPath = QString()) const;
    void record(const QJsonObject &state);
signals:
    void countChanged();
private:
    QString storagePath() const;
    void ensureLoaded();
    void load();
    void save() const;
    QVector<QJsonObject> m_items;
    bool m_loaded = false;
};
