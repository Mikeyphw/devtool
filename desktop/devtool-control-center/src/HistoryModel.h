#pragma once

#include <QAbstractListModel>
#include <QJsonObject>
#include <QVector>
#include <QHash>

class HistoryModel final : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ count NOTIFY countChanged)
public:
    enum Roles { RunIdRole = Qt::UserRole + 1, TitleRole, RepoRole, TargetRole,
                 StatusRole, StartedRole, DurationRole, ErrorsRole, WarningsRole,
                 SummaryRole, CommandRole, StepsRole };
    explicit HistoryModel(QObject *parent = nullptr);
    int rowCount(const QModelIndex &parent = {}) const override;
    int count() const { return m_items.size(); }
    QVariant data(const QModelIndex &index, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
    Q_INVOKABLE void clear();
    Q_INVOKABLE QVariantMap get(int row) const;
    void record(const QJsonObject &state);
signals:
    void countChanged();
private:
    QString storagePath() const;
    void load();
    void save() const;
    QVector<QJsonObject> m_items;
};
