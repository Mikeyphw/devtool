#include "GitRepositoryController.h"
#include "HistoryModel.h"

#include <QDir>
#include <QFileInfo>
#include <QRegularExpression>
#include <QStandardPaths>
#include <QUrl>

namespace {
QString selectedShell() {
    const QString zsh = QStandardPaths::findExecutable(QStringLiteral("zsh"));
    if (!zsh.isEmpty()) return zsh;
    const QString bash = QStandardPaths::findExecutable(QStringLiteral("bash"));
    return bash.isEmpty() ? QStandardPaths::findExecutable(QStringLiteral("sh")) : bash;
}
}

GitRepositoryController::GitRepositoryController(HistoryModel *history, QObject *parent)
    : QObject(parent), m_history(history) {
    m_process.setProcessChannelMode(QProcess::MergedChannels);
    connect(&m_process, &QProcess::stateChanged, this, [this] { emit busyChanged(); });
    connect(&m_process, &QProcess::errorOccurred, this, [this](QProcess::ProcessError) {
        setError(m_process.errorString());
    });
    connect(&m_process, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this,
            [this](int exitCode, QProcess::ExitStatus exitStatus) {
        const QString text = QString::fromUtf8(m_process.readAllStandardOutput());
        m_output = text.trimmed();
        emit outputChanged();
        if (exitStatus != QProcess::NormalExit || exitCode != 0) {
            setError(m_output.isEmpty() ? QStringLiteral("Git operation failed") : m_output);
            return;
        }
        setError({});
        if (m_mode == Mode::Refresh) {
            parseRefreshOutput(text);
            m_output = QStringLiteral("Repository refreshed");
            emit outputChanged();
        } else {
            if (!m_operationDescription.isEmpty() && m_output.isEmpty()) {
                m_output = m_operationDescription + QStringLiteral(" completed");
                emit outputChanged();
            }
            runRefreshScript();
        }
    });
}

QString GitRepositoryController::shellQuote(const QString &value) {
    QString escaped = value;
    escaped.replace(QLatin1Char('\''), QStringLiteral("'\"'\"'"));
    return QLatin1Char('\'') + escaped + QLatin1Char('\'');
}

void GitRepositoryController::setRepositoryPath(const QString &path) {
    const QString clean = QDir::cleanPath(QDir::fromNativeSeparators(path));
    if (clean == m_repositoryPath) return;
    m_repositoryPath = clean;
    emit repositoryPathChanged();
    refresh();
}

void GitRepositoryController::setError(const QString &value) {
    if (value == m_error) return;
    m_error = value;
    emit errorChanged();
}

void GitRepositoryController::refresh() {
    if (busy()) return;
    if (!QFileInfo(m_repositoryPath).isDir()) {
        setError(QStringLiteral("Select an existing repository"));
        return;
    }
    runRefreshScript();
}

void GitRepositoryController::runRefreshScript() {
    if (busy()) return;
    const QString shell = selectedShell();
    if (shell.isEmpty()) {
        setError(QStringLiteral("zsh, bash, or sh is required"));
        return;
    }
    m_mode = Mode::Refresh;
    m_operationDescription = QStringLiteral("Refresh repository");
    const QString root = shellQuote(m_repositoryPath);
    const QString script = QStringLiteral(
                "printf '__STATUS__\\n'; git -C %1 status --porcelain=v2 --branch; "
        "printf '\\n__REMOTE__\\n'; git -C %1 remote get-url origin 2>/dev/null || true; "
        "printf '\\n__BRANCHES__\\n'; git -C %1 for-each-ref --sort=-committerdate --format='%(refname:short)' refs/heads refs/remotes; "
        "printf '\\n__COMMITS__\\n'; git -C %1 log -n 100 --date=iso-strict --pretty=format:'%H%x1f%h%x1f%an%x1f%aI%x1f%s'")
        .arg(root);
    m_process.start(shell, {QStringLiteral("-lc"), script});
}

void GitRepositoryController::runGit(const QStringList &arguments, const QString &description) {
    if (busy() || m_repositoryPath.isEmpty()) return;
    m_mode = Mode::Operation;
    m_operationDescription = description;
    QStringList args{QStringLiteral("-C"), m_repositoryPath};
    args << arguments;
    m_process.start(QStringLiteral("git"), args);
}

void GitRepositoryController::fetch() { runGit({QStringLiteral("fetch"), QStringLiteral("--all"), QStringLiteral("--prune")}, QStringLiteral("Fetch")); }
void GitRepositoryController::pull() { runGit({QStringLiteral("pull"), QStringLiteral("--ff-only")}, QStringLiteral("Pull")); }
void GitRepositoryController::push() { runGit({QStringLiteral("push")}, QStringLiteral("Push")); }
void GitRepositoryController::stageAll() { runGit({QStringLiteral("add"), QStringLiteral("-A")}, QStringLiteral("Stage all")); }
void GitRepositoryController::unstageAll() { runGit({QStringLiteral("reset")}, QStringLiteral("Unstage all")); }
void GitRepositoryController::stagePath(const QString &path) {
    if (!path.trimmed().isEmpty()) runGit({QStringLiteral("add"), QStringLiteral("--"), path.trimmed()}, QStringLiteral("Stage file"));
}
void GitRepositoryController::unstagePath(const QString &path) {
    if (!path.trimmed().isEmpty()) runGit({QStringLiteral("restore"), QStringLiteral("--staged"), QStringLiteral("--"), path.trimmed()}, QStringLiteral("Unstage file"));
}
void GitRepositoryController::discardPath(const QString &path) {
    if (!path.trimmed().isEmpty()) runGit({QStringLiteral("restore"), QStringLiteral("--worktree"), QStringLiteral("--"), path.trimmed()}, QStringLiteral("Discard file"));
}
void GitRepositoryController::stash() { runGit({QStringLiteral("stash"), QStringLiteral("push"), QStringLiteral("--include-untracked")}, QStringLiteral("Stash")); }
void GitRepositoryController::stashPop() { runGit({QStringLiteral("stash"), QStringLiteral("pop")}, QStringLiteral("Pop stash")); }
void GitRepositoryController::commit(const QString &message) {
    if (message.trimmed().isEmpty()) { setError(QStringLiteral("Commit message is required")); return; }
    runGit({QStringLiteral("commit"), QStringLiteral("-m"), message.trimmed()}, QStringLiteral("Commit"));
}
void GitRepositoryController::checkout(const QString &reference) {
    if (reference.trimmed().isEmpty()) return;
    runGit({QStringLiteral("checkout"), reference.trimmed()}, QStringLiteral("Checkout"));
}
void GitRepositoryController::createBranch(const QString &name) {
    if (name.trimmed().isEmpty()) { setError(QStringLiteral("Branch name is required")); return; }
    runGit({QStringLiteral("checkout"), QStringLiteral("-b"), name.trimmed()}, QStringLiteral("Create branch"));
}
void GitRepositoryController::discardWorkingTree() {
    runGit({QStringLiteral("reset"), QStringLiteral("--hard"), QStringLiteral("HEAD")}, QStringLiteral("Discard changes"));
}

void GitRepositoryController::parseRefreshOutput(const QString &text) {
    const int statusStart = text.indexOf(QStringLiteral("__STATUS__\n"));
    const int remoteStart = text.indexOf(QStringLiteral("\n__REMOTE__\n"));
    const int branchesStart = text.indexOf(QStringLiteral("\n__BRANCHES__\n"));
    const int commitsStart = text.indexOf(QStringLiteral("\n__COMMITS__\n"));
    if (statusStart < 0 || remoteStart < 0 || branchesStart < 0 || commitsStart < 0) {
        setError(QStringLiteral("Could not parse git output"));
        return;
    }

    const QString statusSection = text.mid(statusStart + 11, remoteStart - (statusStart + 11)).trimmed();
    m_remote = text.mid(remoteStart + 12, branchesStart - (remoteStart + 12)).trimmed();
    const QString branchesSection = text.mid(branchesStart + 14, commitsStart - (branchesStart + 14)).trimmed();
    const QString commitsSection = text.mid(commitsStart + 13).trimmed();

    m_branch.clear();
    m_dirty = false;
    m_statusEntries.clear();
    for (const QString &line : statusSection.split(QLatin1Char('\n'), Qt::SkipEmptyParts)) {
        if (line.startsWith(QStringLiteral("# branch.head "))) {
            m_branch = line.mid(14).trimmed();
            continue;
        }
        if (line.startsWith(QLatin1Char('#'))) continue;
        m_dirty = true;
        QVariantMap entry;
        entry[QStringLiteral("raw")] = line;
        const QStringList fields = line.split(QLatin1Char(' '), Qt::SkipEmptyParts);
        entry[QStringLiteral("code")] = fields.isEmpty() ? QString() : fields.first();
        entry[QStringLiteral("path")] = fields.isEmpty() ? line : fields.last();
        m_statusEntries << entry;
    }
    m_statusText = m_dirty ? QStringLiteral("Modified") : QStringLiteral("Clean");

    m_branches.clear();
    for (const QString &branch : branchesSection.split(QLatin1Char('\n'), Qt::SkipEmptyParts))
        m_branches << branch.trimmed();

    m_commits.clear();
    for (const QString &line : commitsSection.split(QLatin1Char('\n'), Qt::SkipEmptyParts)) {
        const QStringList fields = line.split(QChar(0x1f));
        if (fields.size() < 5) continue;
        QVariantMap commit;
        commit[QStringLiteral("hash")] = fields.at(0);
        commit[QStringLiteral("shortHash")] = fields.at(1);
        commit[QStringLiteral("author")] = fields.at(2);
        commit[QStringLiteral("date")] = fields.at(3);
        commit[QStringLiteral("subject")] = fields.mid(4).join(QString(QChar(0x1f)));
        commit[QStringLiteral("url")] = commitUrl(fields.at(0));
        if (m_history) {
            const QVariantMap artifact = m_history->artifactForCommit(fields.at(0), m_repositoryPath);
            commit[QStringLiteral("artifactName")] = artifact.value(QStringLiteral("artifactName"));
            commit[QStringLiteral("artifactPath")] = artifact.value(QStringLiteral("artifactPath"));
            commit[QStringLiteral("runId")] = artifact.value(QStringLiteral("runId"));
        }
        m_commits << commit;
    }
    emit refreshed();
}

QString GitRepositoryController::commitUrl(const QString &hash) const {
    QString value = m_remote.trimmed();
    if (value.isEmpty() || hash.isEmpty()) return {};
    if (value.startsWith(QStringLiteral("git@"))) {
        const int colon = value.indexOf(QLatin1Char(':'));
        if (colon > 4) value = QStringLiteral("https://") + value.mid(4, colon - 4) + QLatin1Char('/') + value.mid(colon + 1);
    }
    if (value.startsWith(QStringLiteral("ssh://git@"))) value.replace(QStringLiteral("ssh://git@"), QStringLiteral("https://"));
    if (value.endsWith(QStringLiteral(".git"))) value.chop(4);
    if (!value.startsWith(QStringLiteral("http://")) && !value.startsWith(QStringLiteral("https://"))) return {};
    if (value.contains(QStringLiteral("gitlab"), Qt::CaseInsensitive))
        return value + QStringLiteral("/-/commit/") + hash;
    return value + QStringLiteral("/commit/") + hash;
}
