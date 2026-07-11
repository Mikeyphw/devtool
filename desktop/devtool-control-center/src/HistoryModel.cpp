#include "HistoryModel.h"

#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QStandardPaths>
#include <utility>

HistoryModel::HistoryModel(QObject *parent) : QAbstractListModel(parent) { load(); }
int HistoryModel::rowCount(const QModelIndex &parent) const { return parent.isValid() ? 0 : m_items.size(); }
QVariant HistoryModel::data(const QModelIndex &index, int role) const {
    if (!index.isValid() || index.row() < 0 || index.row() >= m_items.size()) return {};
    const auto &o = m_items.at(index.row());
    const char *key = nullptr;
    switch (role) {
    case RunIdRole: key="run_id"; break; case TitleRole: key="title"; break;
    case RepoRole: key="repo_name"; break; case TargetRole: key="target"; break;
    case StatusRole: key="status"; break; case StartedRole: key="started_at"; break;
    case DurationRole: key="duration"; break; case ErrorsRole: key="errors"; break;
    case WarningsRole: key="warnings"; break; case SummaryRole: key="summary"; break;
    case CommandRole: key="command"; break; case StepsRole: key="steps"; break;
    }
    return key ? o.value(QLatin1String(key)).toVariant() : QVariant{};
}
QHash<int,QByteArray> HistoryModel::roleNames() const {
    return {{RunIdRole,"runId"},{TitleRole,"title"},{RepoRole,"repo"},{TargetRole,"target"},
            {StatusRole,"status"},{StartedRole,"started"},{DurationRole,"duration"},
            {ErrorsRole,"errors"},{WarningsRole,"warnings"},{SummaryRole,"summary"},
            {CommandRole,"command"},{StepsRole,"steps"}};
}
QVariantMap HistoryModel::get(int row) const {
    if (row < 0 || row >= m_items.size()) return {};
    return m_items.at(row).toVariantMap();
}
QString HistoryModel::storagePath() const {
    const auto dir = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    QDir().mkpath(dir); return dir + "/history.json";
}
void HistoryModel::load() {
    QFile f(storagePath()); if (!f.open(QIODevice::ReadOnly)) return;
    const auto doc = QJsonDocument::fromJson(f.readAll());
    if (!doc.isArray()) return;
    beginResetModel();
    for (const auto &v : doc.array()) if (v.isObject()) m_items.push_back(v.toObject());
    endResetModel();
}
void HistoryModel::save() const {
    QJsonArray a; for (const auto &item : m_items) a.push_back(item);
    QFile f(storagePath()); if (f.open(QIODevice::WriteOnly|QIODevice::Truncate)) f.write(QJsonDocument(a).toJson());
}
void HistoryModel::clear() { beginResetModel(); m_items.clear(); endResetModel(); emit countChanged(); save(); }
void HistoryModel::record(const QJsonObject &state) {
    const QString id = state.value("run_id").toString(); if (id.isEmpty()) return;
    for (const auto &item : std::as_const(m_items)) if (item.value("run_id").toString() == id) return;
    QJsonObject item;
    const auto completion = state.value("completion").toObject();
    const auto totals = state.value("totals").toObject();
    item["run_id"] = id;
    item["title"] = state.value("artifact_name").toString(state.value("command").toString("devtool"));
    item["repo_name"] = state.value("repo_name"); item["target"] = state.value("target");
    item["status"] = state.value("status"); item["started_at"] = state.value("started_at");
    item["duration"] = completion.value("duration_seconds");
    item["errors"] = totals.contains("errors") ? totals.value("errors") : state.value("errors"); item["warnings"] = totals.contains("warnings") ? totals.value("warnings") : state.value("warnings");
    item["summary"] = completion.value("summary").toString(state.value("message").toString());
    item["command"] = state.value("command"); item["steps"] = state.value("phase_progress");
    beginInsertRows({}, 0, 0); m_items.prepend(item); endInsertRows(); if (m_items.size() > 200) { const int last=m_items.size()-1; beginRemoveRows({},last,last); m_items.removeLast(); endRemoveRows(); } emit countChanged(); save();
}
