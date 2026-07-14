#include "HistoryModel.h"

#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QSet>
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

    const auto &item = m_items.at(index.row());
    const char *key = nullptr;
    switch (role) {
    case RunIdRole: key = "run_id"; break;
    case TitleRole: key = "title"; break;
    case RepoRole: key = "repo_name"; break;
    case TargetRole: key = "target"; break;
    case StatusRole: key = "status"; break;
    case StartedRole: key = "started_at"; break;
    case DurationRole: key = "duration"; break;
    case ErrorsRole: key = "errors"; break;
    case WarningsRole: key = "warnings"; break;
    case SummaryRole: key = "summary"; break;
    case CommandRole: key = "command"; break;
    case StepsRole: key = "steps"; break;
    case RepoRootRole: key = "repo_root"; break;
    case ArtifactNameRole: key = "artifact_name"; break;
    case ArtifactPathRole: key = "artifact_path"; break;
    case GitCommitRole: key = "git_commit"; break;
    case LogPathRole: key = "log_path"; break;
    case ProjectTypeRole: key = "project_type"; break;
    case ExecutionEnvironmentRole: key = "execution_environment"; break;
    }
    return key ? item.value(QLatin1String(key)).toVariant() : QVariant{};
}

QHash<int, QByteArray> HistoryModel::roleNames() const {
    return {
        {RunIdRole, "runId"}, {TitleRole, "title"}, {RepoRole, "repo"},
        {TargetRole, "target"}, {StatusRole, "status"}, {StartedRole, "started"},
        {DurationRole, "duration"}, {ErrorsRole, "errors"}, {WarningsRole, "warnings"},
        {SummaryRole, "summary"}, {CommandRole, "command"}, {StepsRole, "steps"},
        {RepoRootRole, "repoRoot"}, {ArtifactNameRole, "artifactName"},
        {ArtifactPathRole, "artifactPath"}, {GitCommitRole, "gitCommit"},
        {LogPathRole, "logPath"}, {ProjectTypeRole, "projectType"},
        {ExecutionEnvironmentRole, "executionEnvironment"}
    };
}

QVariantMap HistoryModel::get(int row) const {
    const_cast<HistoryModel *>(this)->ensureLoaded();
    return row >= 0 && row < m_items.size() ? m_items.at(row).toVariantMap() : QVariantMap{};
}

QVariantMap HistoryModel::getByRunId(const QString &id) const {
    const_cast<HistoryModel *>(this)->ensureLoaded();
    for (const auto &item : m_items) {
        if (item.value(QStringLiteral("run_id")).toString() == id) return item.toVariantMap();
    }
    return {};
}

QVariantList HistoryModel::search(
    const QString &query,
    const QString &status,
    const QString &repository) const {
    const_cast<HistoryModel *>(this)->ensureLoaded();
    QVariantList result;
    const QString needle = query.trimmed().toCaseFolded();
    const QString wanted = status.trimmed().toLower();
    const QString repo = repository.trimmed();
    result.reserve(m_items.size());

    for (int index = 0; index < m_items.size(); ++index) {
        const auto &item = m_items.at(index);
        const QString itemStatus = item.value(QStringLiteral("status")).toString().toLower();
        const QString root = item.value(QStringLiteral("repo_root")).toString();
        if (!wanted.isEmpty() && wanted != QStringLiteral("all") && itemStatus != wanted) continue;
        if (!repo.isEmpty() && root != repo) continue;
        if (!needle.isEmpty() && !m_searchText.value(index).contains(needle)) continue;
        result << item.toVariantMap();
    }
    return result;
}

QVariantList HistoryModel::repositories() const {
    const_cast<HistoryModel *>(this)->ensureLoaded();
    QSet<QString> seen;
    QVariantList result;
    for (const auto &item : m_items) {
        const QString root = item.value(QStringLiteral("repo_root")).toString().trimmed();
        if (root.isEmpty() || seen.contains(root)) continue;
        seen.insert(root);
        result << QVariantMap{
            {QStringLiteral("label"), item.value(QStringLiteral("repo_name")).toString(root)},
            {QStringLiteral("path"), root}
        };
    }
    return result;
}

bool HistoryModel::remove(const QString &id) {
    ensureLoaded();
    for (int index = 0; index < m_items.size(); ++index) {
        if (m_items[index].value(QStringLiteral("run_id")).toString() != id) continue;
        beginRemoveRows({}, index, index);
        m_items.removeAt(index);
        m_searchText.removeAt(index);
        endRemoveRows();
        emit countChanged();
        save();
        return true;
    }
    return false;
}

QVariantList HistoryModel::forRepository(const QString &path) const {
    const_cast<HistoryModel *>(this)->ensureLoaded();
    const QString clean = QDir::cleanPath(QDir::fromNativeSeparators(path));
    QVariantList result;
    for (const auto &item : m_items) {
        if (QDir::cleanPath(QDir::fromNativeSeparators(item.value(QStringLiteral("repo_root")).toString())) == clean) {
            result << item.toVariantMap();
        }
    }
    return result;
}

QVariantMap HistoryModel::artifactForCommit(const QString &hash, const QString &repoPath) const {
    const_cast<HistoryModel *>(this)->ensureLoaded();
    for (const auto &item : m_items) {
        const QString commit = item.value(QStringLiteral("git_commit")).toString();
        const QString root = item.value(QStringLiteral("repo_root")).toString();
        const bool hashMatches = commit == hash || hash.startsWith(commit) || commit.startsWith(hash);
        const bool repoMatches = repoPath.isEmpty() || QDir::cleanPath(root) == QDir::cleanPath(repoPath);
        if (hashMatches && repoMatches) {
            return {
                {QStringLiteral("artifactName"), item.value(QStringLiteral("artifact_name")).toString()},
                {QStringLiteral("artifactPath"), item.value(QStringLiteral("artifact_path")).toString()},
                {QStringLiteral("runId"), item.value(QStringLiteral("run_id")).toString()}
            };
        }
    }
    return {};
}

QString HistoryModel::storagePath() const {
    const auto directory = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    QDir().mkpath(directory);
    return directory + QStringLiteral("/history.json");
}

void HistoryModel::ensureLoaded() {
    if (m_loaded) return;
    m_loaded = true;
    load();
}

void HistoryModel::load() {
    QFile file(storagePath());
    if (!file.open(QIODevice::ReadOnly)) return;
    const auto document = QJsonDocument::fromJson(file.readAll());
    if (!document.isArray()) return;

    beginResetModel();
    m_items.clear();
    for (const auto &value : document.array()) {
        if (value.isObject()) m_items.push_back(value.toObject());
    }
    rebuildSearchIndex();
    endResetModel();
}

void HistoryModel::save() const {
    QJsonArray values;
    for (const auto &item : m_items) values.push_back(item);
    QFile file(storagePath());
    if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        file.write(QJsonDocument(values).toJson());
    }
}

void HistoryModel::clear() {
    ensureLoaded();
    beginResetModel();
    m_items.clear();
    m_searchText.clear();
    endResetModel();
    emit countChanged();
    save();
}

QString HistoryModel::buildSearchText(const QJsonObject &item) const {
    const QStringList values = {
        item.value(QStringLiteral("title")).toString(),
        item.value(QStringLiteral("repo_name")).toString(),
        item.value(QStringLiteral("repo_root")).toString(),
        item.value(QStringLiteral("target")).toString(),
        item.value(QStringLiteral("summary")).toString(),
        item.value(QStringLiteral("command")).toString(),
        item.value(QStringLiteral("artifact_name")).toString(),
        item.value(QStringLiteral("git_commit")).toString(),
        item.value(QStringLiteral("status")).toString()
    };
    return values.join(QChar('\n')).toCaseFolded();
}

void HistoryModel::rebuildSearchIndex() {
    m_searchText.clear();
    m_searchText.reserve(m_items.size());
    for (const auto &item : m_items) m_searchText.push_back(buildSearchText(item));
}

void HistoryModel::record(const QJsonObject &state) {
    ensureLoaded();
    const QString id = state.value(QStringLiteral("run_id")).toString();
    if (id.isEmpty()) return;
    for (const auto &existing : std::as_const(m_items)) {
        if (existing.value(QStringLiteral("run_id")).toString() == id) return;
    }

    QJsonObject item;
    const auto completion = state.value(QStringLiteral("completion")).toObject();
    const auto totals = state.value(QStringLiteral("totals")).toObject();
    item[QStringLiteral("run_id")] = id;
    item[QStringLiteral("title")] = state.value(QStringLiteral("artifact_name")).toString(
        state.value(QStringLiteral("command")).toString(QStringLiteral("devtool")));
    item[QStringLiteral("repo_name")] = state.value(QStringLiteral("repo_name"));
    item[QStringLiteral("target")] = state.value(QStringLiteral("target"));
    item[QStringLiteral("status")] = state.value(QStringLiteral("status"));
    item[QStringLiteral("started_at")] = state.value(QStringLiteral("started_at"));
    item[QStringLiteral("duration")] = completion.value(QStringLiteral("duration_seconds"));
    item[QStringLiteral("errors")] = totals.contains(QStringLiteral("errors"))
        ? totals.value(QStringLiteral("errors")) : state.value(QStringLiteral("errors"));
    item[QStringLiteral("warnings")] = totals.contains(QStringLiteral("warnings"))
        ? totals.value(QStringLiteral("warnings")) : state.value(QStringLiteral("warnings"));
    item[QStringLiteral("summary")] = completion.value(QStringLiteral("summary")).toString(
        state.value(QStringLiteral("message")).toString());
    item[QStringLiteral("command")] = state.value(QStringLiteral("command"));
    item[QStringLiteral("steps")] = state.value(QStringLiteral("phase_progress"));
    item[QStringLiteral("repo_root")] = state.value(QStringLiteral("repo_root"));
    item[QStringLiteral("artifact_name")] = state.value(QStringLiteral("artifact_name"));
    item[QStringLiteral("artifact_path")] = state.value(QStringLiteral("artifact_path"));
    item[QStringLiteral("git_commit")] = completion.value(QStringLiteral("git_commit")).toString(
        state.value(QStringLiteral("git_commit")).toString());
    item[QStringLiteral("log_path")] = completion.value(QStringLiteral("log_path")).toString(
        state.value(QStringLiteral("log_path")).toString());
    item[QStringLiteral("project_type")] = state.value(QStringLiteral("project_type"));
    item[QStringLiteral("execution_environment")] = state.value(QStringLiteral("execution_environment"));

    beginInsertRows({}, 0, 0);
    m_items.prepend(item);
    m_searchText.prepend(buildSearchText(item));
    endInsertRows();
    if (m_items.size() > 200) {
        const int last = m_items.size() - 1;
        beginRemoveRows({}, last, last);
        m_items.removeLast();
        m_searchText.removeLast();
        endRemoveRows();
    }
    emit countChanged();
    save();
}
