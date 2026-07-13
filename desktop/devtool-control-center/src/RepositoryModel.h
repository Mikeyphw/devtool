#pragma once

#include <QAbstractListModel>
#include <QJsonObject>
#include <QVariantList>

class RepositoryModel final : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ count NOTIFY countChanged)
public:
    enum Roles { PathRole = Qt::UserRole + 1, NameRole, BranchRole, RemoteRole, StatusRole,
                 DirtyRole, ActiveRunsRole, LastUsedRole };
    explicit RepositoryModel(QObject *parent = nullptr);
    int rowCount(const QModelIndex &parent = {}) const override;
    QVariant data(const QModelIndex &index, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
    int count() const { return m_items.size(); }
    Q_INVOKABLE void addRepository(const QString &path);
    Q_INVOKABLE void removeRepository(int row);
    Q_INVOKABLE void refresh();
    Q_INVOKABLE QVariantMap get(int row) const;
    Q_INVOKABLE void markUsed(const QString &path);
public slots:
    void updateActiveRuns(const QVariantList &runs);
signals:
    void countChanged();
private:
    QString storagePath() const;
    void load();
    void save() const;
    void refreshPath(const QString &path);
    int indexForPath(const QString &path) const;
    QVector<QJsonObject> m_items;
};
