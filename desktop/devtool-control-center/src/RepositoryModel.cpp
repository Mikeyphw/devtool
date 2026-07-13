#include "RepositoryModel.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QProcess>
#include <QRegularExpression>
#include <QStandardPaths>

#include <algorithm>

namespace {
QString findRepositoryRoot(QString path) {
    QFileInfo info(path);
    if (!info.exists()) return {};
    QDir dir(info.isDir() ? info.absoluteFilePath() : info.absolutePath());
    while (true) {
        if (QFileInfo::exists(dir.filePath(QStringLiteral(".git")))) return dir.absolutePath();
        if (!dir.cdUp()) break;
    }
    return {};
}

QString gitDirectory(const QString &root) {
    const QString marker = QDir(root).filePath(QStringLiteral(".git"));
    const QFileInfo info(marker);
    if (info.isDir()) return info.absoluteFilePath();
    QFile file(marker);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) return {};
    const QString line = QString::fromUtf8(file.readLine()).trimmed();
    if (!line.startsWith(QStringLiteral("gitdir:"), Qt::CaseInsensitive)) return {};
    QString value = line.mid(QStringLiteral("gitdir:").size()).trimmed();
    if (QDir::isRelativePath(value)) value = QDir(root).absoluteFilePath(value);
    return QDir::cleanPath(value);
}

QString originRemote(const QString &root) {
    const QString gitDir = gitDirectory(root);
    QFile file(QDir(gitDir).filePath(QStringLiteral("config")));
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) return {};
    const QString text = QString::fromUtf8(file.readAll());
    const QRegularExpression section(QStringLiteral(R"(\[remote\s+"origin"\]([\s\S]*?)(?=\n\[|\z))"),
                                     QRegularExpression::CaseInsensitiveOption);
    const auto match = section.match(text);
    if (!match.hasMatch()) return {};
    const QRegularExpression url(QStringLiteral(R"((?:^|\n)\s*url\s*=\s*(.+)\s*(?:\n|$))"),
                                 QRegularExpression::CaseInsensitiveOption);
    const auto urlMatch = url.match(match.captured(1));
    return urlMatch.hasMatch() ? urlMatch.captured(1).trimmed() : QString();
}

QString branchFromStatus(const QString &output) {
    for (const QString &line : output.split(QLatin1Char('\n'))) {
        if (line.startsWith(QStringLiteral("# branch.head ")))
            return line.mid(QStringLiteral("# branch.head ").size()).trimmed();
    }
    return {};
}

bool dirtyFromStatus(const QString &output) {
    for (const QString &line : output.split(QLatin1Char('\n'))) {
        if (!line.isEmpty() && !line.startsWith(QLatin1Char('#'))) return true;
    }
    return false;
}
}

RepositoryModel::RepositoryModel(QObject *parent) : QAbstractListModel(parent) {
    load();
}

int RepositoryModel::rowCount(const QModelIndex &parent) const { return parent.isValid() ? 0 : m_items.size(); }

QVariant RepositoryModel::data(const QModelIndex &index, int role) const {
    if (!index.isValid() || index.row() < 0 || index.row() >= m_items.size()) return {};
    const auto &item = m_items.at(index.row());
    const char *key = nullptr;
    switch (role) {
    case PathRole: key="path"; break; case NameRole: key="name"; break; case BranchRole: key="branch"; break;
    case RemoteRole: key="remote"; break; case StatusRole: key="status"; break; case DirtyRole: key="dirty"; break;
    case ActiveRunsRole: key="active_runs"; break; case LastUsedRole: key="last_used"; break; case SavedRole: key="saved"; break;
    }
    return key ? item.value(QLatin1String(key)).toVariant() : QVariant{};
}

QHash<int,QByteArray> RepositoryModel::roleNames() const {
    return {{PathRole,"path"},{NameRole,"name"},{BranchRole,"branch"},{RemoteRole,"remote"},
            {StatusRole,"status"},{DirtyRole,"dirty"},{ActiveRunsRole,"activeRuns"},{LastUsedRole,"lastUsed"},{SavedRole,"saved"}};
}

QString RepositoryModel::storagePath() const {
    const QString dir = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    QDir().mkpath(dir);
    return dir + QStringLiteral("/repositories.json");
}

void RepositoryModel::load() {
    QFile file(storagePath());
    if (!file.open(QIODevice::ReadOnly)) return;
    const auto doc = QJsonDocument::fromJson(file.readAll());
    if (!doc.isArray()) return;
    for (const auto &value : doc.array()) {
        if (!value.isObject()) continue;
        auto item = value.toObject();
        item[QStringLiteral("active_runs")] = 0;
        item[QStringLiteral("saved")] = true;
        m_items.push_back(item);
    }
}

void RepositoryModel::save() const {
    QJsonArray array;
    for (auto item : m_items) {
        // Live-only repositories and active-run counters are transient.  A
        // repository is persisted only after the user explicitly saves it.
        if (!item.value(QStringLiteral("saved")).toBool(true)) continue;
        item.remove(QStringLiteral("active_runs"));
        item.remove(QStringLiteral("saved"));
        array.push_back(item);
    }
    QFile file(storagePath());
    if (file.open(QIODevice::WriteOnly|QIODevice::Truncate))
        file.write(QJsonDocument(array).toJson(QJsonDocument::Compact));
}

int RepositoryModel::indexForPath(const QString &path) const {
    for (int index = 0; index < m_items.size(); ++index)
        if (m_items[index].value(QStringLiteral("path")).toString() == path) return index;
    return -1;
}

void RepositoryModel::refreshPath(const QString &rawPath) {
    const QString clean = QDir::cleanPath(QDir::fromNativeSeparators(rawPath));
    const int row = indexForPath(clean);
    if (row < 0) return;
    const QString root = findRepositoryRoot(clean);
    if (root.isEmpty()) {
        auto &item = m_items[row];
        item[QStringLiteral("name")] = QFileInfo(clean).fileName();
        item[QStringLiteral("branch")] = QString();
        item[QStringLiteral("remote")] = QString();
        item[QStringLiteral("dirty")] = false;
        item[QStringLiteral("status")] = QFileInfo::exists(clean) ? QStringLiteral("not-git") : QStringLiteral("missing");
        emit dataChanged(index(row,0), index(row,0));
        return;
    }

    auto *process = new QProcess(this);
    process->setProgram(QStringLiteral("git"));
    process->setArguments({QStringLiteral("-C"), root, QStringLiteral("status"),
                           QStringLiteral("--porcelain=v2"), QStringLiteral("--branch"),
                           QStringLiteral("--untracked-files=normal")});
    connect(process, qOverload<int,QProcess::ExitStatus>(&QProcess::finished), this,
            [this, process, clean, root](int exitCode, QProcess::ExitStatus exitStatus) {
        const int currentRow = indexForPath(clean);
        if (currentRow < 0) { process->deleteLater(); return; }
        auto &item = m_items[currentRow];
        const QString oldName = item.value(QStringLiteral("name")).toString();
        const QString oldBranch = item.value(QStringLiteral("branch")).toString();
        const QString oldRemote = item.value(QStringLiteral("remote")).toString();
        if (exitStatus != QProcess::NormalExit || exitCode != 0) {
            item[QStringLiteral("status")] = QStringLiteral("not-git");
            item[QStringLiteral("dirty")] = false;
        } else {
            const QString output = QString::fromUtf8(process->readAllStandardOutput());
            const bool dirty = dirtyFromStatus(output);
            item[QStringLiteral("name")] = QFileInfo(root).fileName();
            item[QStringLiteral("branch")] = branchFromStatus(output);
            item[QStringLiteral("remote")] = originRemote(root);
            item[QStringLiteral("dirty")] = dirty;
            item[QStringLiteral("status")] = dirty ? QStringLiteral("modified") : QStringLiteral("clean");
        }
        emit dataChanged(index(currentRow,0), index(currentRow,0));
        if (item.value(QStringLiteral("saved")).toBool(true) &&
            (oldName != item.value(QStringLiteral("name")).toString() ||
             oldBranch != item.value(QStringLiteral("branch")).toString() ||
             oldRemote != item.value(QStringLiteral("remote")).toString())) save();
        process->deleteLater();
    });
    process->start();
}

void RepositoryModel::addRepository(const QString &path) {
    const QString clean = QDir::cleanPath(QDir::fromNativeSeparators(path));
    if (clean.isEmpty()) return;
    const int existing = indexForPath(clean);
    if (existing >= 0) {
        auto &item = m_items[existing];
        item[QStringLiteral("saved")] = true;
        item[QStringLiteral("last_used")] = QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs);
        emit dataChanged(index(existing,0), index(existing,0), {SavedRole, LastUsedRole});
        save();
        refreshPath(clean);
        return;
    }
    beginInsertRows({}, m_items.size(), m_items.size());
    QJsonObject item;
    item[QStringLiteral("path")] = clean;
    item[QStringLiteral("name")] = QFileInfo(clean).fileName();
    item[QStringLiteral("status")] = QFileInfo::exists(clean) ? QStringLiteral("checking") : QStringLiteral("missing");
    item[QStringLiteral("active_runs")] = 0;
    item[QStringLiteral("saved")] = true;
    item[QStringLiteral("last_used")] = QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs);
    m_items.push_back(item);
    endInsertRows();
    emit countChanged();
    save();
    refreshPath(clean);
}

void RepositoryModel::removeRepository(int row) {
    if (row<0 || row>=m_items.size()) return;
    beginRemoveRows({},row,row);
    m_items.removeAt(row);
    endRemoveRows();
    emit countChanged();
    save();
}

void RepositoryModel::refresh() {
    const QStringList paths = [this] {
        QStringList values;
        for (const auto &item : m_items) values << item.value(QStringLiteral("path")).toString();
        return values;
    }();
    for (const QString &path : paths) refreshPath(path);
}

QVariantMap RepositoryModel::get(int row) const {
    return row>=0&&row<m_items.size()?m_items[row].toVariantMap():QVariantMap{};
}

void RepositoryModel::markUsed(const QString &path) {
    const int row = indexForPath(path);
    if (row >= 0) {
        m_items[row][QStringLiteral("last_used")] = QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs);
        emit dataChanged(index(row,0),index(row,0),{LastUsedRole});
        if (m_items[row].value(QStringLiteral("saved")).toBool(true)) save();
        refreshPath(path);
        return;
    }
    // Repositories are opt-in. Merely using a path must not register it.
}

void RepositoryModel::updateActiveRuns(const QVariantList &runs) {
    QHash<QString,int> counts;
    for (const auto &value : runs) {
        const auto map = value.toMap();
        if (map.value(QStringLiteral("status")).toString() != QStringLiteral("running")) continue;
        const QString path = QDir::cleanPath(QDir::fromNativeSeparators(
            map.value(QStringLiteral("repo_root")).toString()));
        if (!path.isEmpty() && path != QStringLiteral(".")) counts[path]++;
    }

    // Drop live-only rows once their final active run disappears. Saved rows
    // remain in the registry and simply return to zero active runs.
    for (int row = m_items.size() - 1; row >= 0; --row) {
        const auto &item = m_items.at(row);
        const QString path = item.value(QStringLiteral("path")).toString();
        if (!item.value(QStringLiteral("saved")).toBool(true) && !counts.contains(path)) {
            beginRemoveRows({}, row, row);
            m_items.removeAt(row);
            endRemoveRows();
            emit countChanged();
        }
    }

    for (auto it = counts.cbegin(); it != counts.cend(); ++it) {
        int row = indexForPath(it.key());
        if (row < 0) {
            row = m_items.size();
            beginInsertRows({}, row, row);
            QJsonObject item;
            item[QStringLiteral("path")] = it.key();
            item[QStringLiteral("name")] = QFileInfo(it.key()).fileName();
            item[QStringLiteral("status")] = QStringLiteral("checking");
            item[QStringLiteral("dirty")] = false;
            item[QStringLiteral("active_runs")] = it.value();
            item[QStringLiteral("saved")] = false;
            item[QStringLiteral("last_used")] = QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs);
            m_items.push_back(item);
            endInsertRows();
            emit countChanged();
            refreshPath(it.key());
            continue;
        }
        if (m_items[row].value(QStringLiteral("active_runs")).toInt() != it.value()) {
            m_items[row][QStringLiteral("active_runs")] = it.value();
            emit dataChanged(index(row,0), index(row,0), {ActiveRunsRole});
        }
    }

    for (int row = 0; row < m_items.size(); ++row) {
        const QString path = m_items[row].value(QStringLiteral("path")).toString();
        const int value = counts.value(path);
        if (m_items[row].value(QStringLiteral("active_runs")).toInt() != value) {
            m_items[row][QStringLiteral("active_runs")] = value;
            emit dataChanged(index(row,0), index(row,0), {ActiveRunsRole});
        }
    }
}

