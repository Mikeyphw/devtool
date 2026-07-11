#include "RepositoryModel.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <algorithm>
#include <QJsonArray>
#include <QJsonDocument>
#include <QProcess>
#include <QStandardPaths>

static QString runGit(const QString &path, const QStringList &args, int timeout = 2500) {
    QProcess process;
    process.setProgram("git");
    process.setArguments(QStringList{"-C", path} + args);
    process.start();
    if (!process.waitForFinished(timeout)) { process.kill(); process.waitForFinished(); return {}; }
    if (process.exitStatus() != QProcess::NormalExit || process.exitCode() != 0) return {};
    return QString::fromUtf8(process.readAllStandardOutput()).trimmed();
}

RepositoryModel::RepositoryModel(QObject *parent) : QAbstractListModel(parent) { load(); refresh(); }
int RepositoryModel::rowCount(const QModelIndex &parent) const { return parent.isValid() ? 0 : m_items.size(); }
QVariant RepositoryModel::data(const QModelIndex &index, int role) const {
    if (!index.isValid() || index.row() < 0 || index.row() >= m_items.size()) return {};
    const auto &item = m_items.at(index.row());
    const char *key = nullptr;
    switch (role) {
    case PathRole: key="path"; break; case NameRole: key="name"; break; case BranchRole: key="branch"; break;
    case RemoteRole: key="remote"; break; case StatusRole: key="status"; break; case DirtyRole: key="dirty"; break;
    case ActiveRunsRole: key="active_runs"; break; case LastUsedRole: key="last_used"; break;
    }
    return key ? item.value(QLatin1String(key)).toVariant() : QVariant{};
}
QHash<int,QByteArray> RepositoryModel::roleNames() const {
    return {{PathRole,"path"},{NameRole,"name"},{BranchRole,"branch"},{RemoteRole,"remote"},
            {StatusRole,"status"},{DirtyRole,"dirty"},{ActiveRunsRole,"activeRuns"},{LastUsedRole,"lastUsed"}};
}
QString RepositoryModel::storagePath() const {
    const QString dir = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    QDir().mkpath(dir); return dir + "/repositories.json";
}
void RepositoryModel::load() {
    QFile file(storagePath()); if (!file.open(QIODevice::ReadOnly)) return;
    const auto doc = QJsonDocument::fromJson(file.readAll()); if (!doc.isArray()) return;
    for (const auto &value : doc.array()) if (value.isObject()) m_items.push_back(value.toObject());
}
void RepositoryModel::save() const {
    QJsonArray array; for (const auto &item : m_items) array.push_back(item);
    QFile file(storagePath()); if (file.open(QIODevice::WriteOnly|QIODevice::Truncate)) file.write(QJsonDocument(array).toJson());
}
QJsonObject RepositoryModel::inspectPath(const QString &rawPath, const QJsonObject &existing) const {
    const QString clean = QDir::cleanPath(QDir::fromNativeSeparators(rawPath));
    QJsonObject item = existing; item["path"] = clean;
    const QString top = runGit(clean, {"rev-parse", "--show-toplevel"});
    const bool valid = !top.isEmpty();
    item["name"] = valid ? QFileInfo(top).fileName() : QFileInfo(clean).fileName();
    item["branch"] = valid ? runGit(clean, {"branch", "--show-current"}) : QString();
    item["remote"] = valid ? runGit(clean, {"remote", "get-url", "origin"}) : QString();
    const QString porcelain = valid ? runGit(clean, {"status", "--porcelain", "--untracked-files=normal"}) : QString();
    item["dirty"] = valid && !porcelain.isEmpty();
    item["status"] = !QFileInfo::exists(clean) ? "missing" : !valid ? "not-git" : porcelain.isEmpty() ? "clean" : "modified";
    if (!item.contains("active_runs")) item["active_runs"] = 0;
    return item;
}
void RepositoryModel::addRepository(const QString &path) {
    const QString clean = QDir::cleanPath(QDir::fromNativeSeparators(path)); if (clean.isEmpty()) return;
    for (int i=0;i<m_items.size();++i) if (m_items[i].value("path").toString()==clean) { markUsed(clean); return; }
    beginInsertRows({}, m_items.size(), m_items.size());
    QJsonObject item; item["last_used"] = QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs);
    m_items.push_back(inspectPath(clean, item)); endInsertRows(); emit countChanged(); save();
}
void RepositoryModel::removeRepository(int row) {
    if (row<0 || row>=m_items.size()) return; beginRemoveRows({},row,row);m_items.removeAt(row);endRemoveRows();emit countChanged();save();
}
void RepositoryModel::refresh() {
    if (m_items.isEmpty()) return; beginResetModel();
    for (int i=0;i<m_items.size();++i) m_items[i]=inspectPath(m_items[i].value("path").toString(),m_items[i]);
    std::sort(m_items.begin(),m_items.end(),[](const QJsonObject&a,const QJsonObject&b){return a.value("last_used").toString()>b.value("last_used").toString();});
    endResetModel(); save();
}
QVariantMap RepositoryModel::get(int row) const { return row>=0&&row<m_items.size()?m_items[row].toVariantMap():QVariantMap{}; }
void RepositoryModel::markUsed(const QString &path) {
    for (int i=0;i<m_items.size();++i) if(m_items[i].value("path").toString()==path){m_items[i]["last_used"]=QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs);emit dataChanged(index(i,0),index(i,0));save();return;}
    addRepository(path);
}
void RepositoryModel::updateActiveRuns(const QVariantList &runs) {
    QHash<QString,int> counts;
    for (const auto &value:runs) { const auto map=value.toMap(); if(map.value("status").toString()=="running") counts[map.value("repo_root").toString()]++; }
    bool changed=false; for(int i=0;i<m_items.size();++i){const int value=counts.value(m_items[i].value("path").toString());if(m_items[i].value("active_runs").toInt()!=value){m_items[i]["active_runs"]=value;emit dataChanged(index(i,0),index(i,0),{ActiveRunsRole});changed=true;}}
    if(changed)save();
}
