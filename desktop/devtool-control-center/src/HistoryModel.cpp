#include "HistoryModel.h"

#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QStandardPaths>
#include <utility>

HistoryModel::HistoryModel(QObject *parent) : QAbstractListModel(parent) {}
int HistoryModel::rowCount(const QModelIndex &parent) const {
    const_cast<HistoryModel *>(this)->ensureLoaded();
    return parent.isValid() ? 0 : m_items.size();
}
int HistoryModel::count() const {
    const_cast<HistoryModel *>(this)->ensureLoaded();
    return m_items.size();
}
QVariant HistoryModel::data(const QModelIndex &index, int role) const {
    const_cast<HistoryModel *>(this)->ensureLoaded();
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
    case RepoRootRole: key="repo_root"; break; case ArtifactNameRole: key="artifact_name"; break;
    case ArtifactPathRole: key="artifact_path"; break; case GitCommitRole: key="git_commit"; break;
    }
    return key ? o.value(QLatin1String(key)).toVariant() : QVariant{};
}
QHash<int,QByteArray> HistoryModel::roleNames() const {
    return {{RunIdRole,"runId"},{TitleRole,"title"},{RepoRole,"repo"},{TargetRole,"target"},
            {StatusRole,"status"},{StartedRole,"started"},{DurationRole,"duration"},
            {ErrorsRole,"errors"},{WarningsRole,"warnings"},{SummaryRole,"summary"},
            {CommandRole,"command"},{StepsRole,"steps"},{RepoRootRole,"repoRoot"},
            {ArtifactNameRole,"artifactName"},{ArtifactPathRole,"artifactPath"},{GitCommitRole,"gitCommit"}};
}
QVariantMap HistoryModel::get(int row) const {
    const_cast<HistoryModel *>(this)->ensureLoaded();
    if (row < 0 || row >= m_items.size()) return {};
    return m_items.at(row).toVariantMap();
}
QVariantList HistoryModel::forRepository(const QString &path) const {
    const_cast<HistoryModel *>(this)->ensureLoaded();
    const QString clean = QDir::cleanPath(QDir::fromNativeSeparators(path));
    QVariantList result;
    for (const auto &item : m_items) {
        const QString root = QDir::cleanPath(QDir::fromNativeSeparators(item.value(QStringLiteral("repo_root")).toString()));
        if (!clean.isEmpty() && root == clean) result << item.toVariantMap();
    }
    return result;
}
QVariantMap HistoryModel::artifactForCommit(const QString &hash, const QString &repoPath) const {
    const_cast<HistoryModel *>(this)->ensureLoaded();
    const QString clean = repoPath.trimmed().isEmpty()
        ? QString()
        : QDir::cleanPath(QDir::fromNativeSeparators(repoPath));
    for (const auto &item : m_items) {
        const QString commit = item.value(QStringLiteral("git_commit")).toString();
        const QString root = QDir::cleanPath(QDir::fromNativeSeparators(item.value(QStringLiteral("repo_root")).toString()));
        if ((commit == hash || (!commit.isEmpty() && hash.startsWith(commit)) || commit.startsWith(hash)) &&
            (clean.isEmpty() || clean == root)) {
            return {{QStringLiteral("artifactName"), item.value(QStringLiteral("artifact_name")).toString()},
                    {QStringLiteral("artifactPath"), item.value(QStringLiteral("artifact_path")).toString()},
                    {QStringLiteral("runId"), item.value(QStringLiteral("run_id")).toString()}};
        }
    }
    return {};
}
QString HistoryModel::storagePath() const {
    const auto dir = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    QDir().mkpath(dir); return dir + "/history.json";
}
void HistoryModel::ensureLoaded() {
    if (m_loaded) return;
    m_loaded = true;
    load();
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
void HistoryModel::clear() { ensureLoaded(); beginResetModel(); m_items.clear(); endResetModel(); emit countChanged(); save(); }
void HistoryModel::record(const QJsonObject &state) {
    ensureLoaded();
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
    item["repo_root"] = state.value("repo_root");
    item["artifact_name"] = state.value("artifact_name");
    item["artifact_path"] = state.value("artifact_path");
    item["git_commit"] = completion.value("git_commit").toString(state.value("git_commit").toString());
    beginInsertRows({}, 0, 0); m_items.prepend(item); endInsertRows(); if (m_items.size() > 200) { const int last=m_items.size()-1; beginRemoveRows({},last,last); m_items.removeLast(); endRemoveRows(); } emit countChanged(); save();
}
