#include "HudStateStore.h"
#include "HistoryModel.h"

#include <algorithm>
#include <utility>

#include <QClipboard>
#include <QDateTime>
#include <QDesktopServices>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QGuiApplication>
#include <QJsonArray>
#include <QJsonDocument>
#include <QSet>
#include <QHash>
#include <QStandardPaths>
#include <QUrl>
#include <QtMath>

static QString defaultStateDir() {
    const auto env = qEnvironmentVariable("DEVTOOL_TASKER_DIR");
    if (!env.isEmpty()) return QDir::cleanPath(QDir::fromNativeSeparators(env));
    const auto xdg = qEnvironmentVariable("XDG_STATE_HOME");
    return (xdg.isEmpty() ? QDir::homePath()+"/.local/state" : xdg) + "/devtool";
}
static QString baseName(QString name) {
    const int bracket = name.indexOf('['); if (bracket >= 0) name = name.left(bracket);
    name = name.toLower().replace('_','-');
    if (name.contains("extract") || name.contains("apply-overlay")) return "extract";
    if (name.contains("restore")) return "restore"; if (name.contains("build")) return "build";
    if (name.contains("test")) return "test"; if (name.contains("validator") || name.contains("artifact-validation")) return "validate";
    if (name.contains("commit")) return "commit"; if (name.contains("rollback")) return "rollback";
    return name;
}
static QString labelFor(const QString &base) {
    if (base=="extract") return "Extract"; if (base=="restore") return "Restore"; if (base=="build") return "Build";
    if (base=="test") return "Test"; if (base=="validate") return "Validate"; if (base=="commit") return "Commit";
    if (base=="rollback") return "Rollback"; QString s=base; if (!s.isEmpty()) s[0]=s[0].toUpper(); return s;
}
static bool validatorName(const QString &name) {
    const QString normalized = QString(name).toLower().replace('_','-');
    return normalized.contains("validator") || normalized.contains("artifact-validation") || normalized.contains("validate-script");
}
static QString humanCommand(QString command) {
    command = command.trimmed().toLower().replace('_','-');
    if (command == "apply-overlay" || command == "ao") return "Applying overlay";
    if (command == "apply-overlays" || command == "aos") return "Applying overlay bundle";
    if (command == "apply-snapshot" || command == "as") return "Applying snapshot";
    if (command == "extract-overlay") return "Extracting overlay";
    if (command == "inspect-artifact") return "Inspecting artifact";
    if (command == "validate-artifact" || command == "validate") return "Validating repository";
    if (command == "restore") return "Restoring dependencies";
    if (command == "build") return "Building solution";
    if (command == "test") return "Running tests";
    if (command == "doctor") return "Checking repository health";
    if (command == "status") return "Reading repository status";
    if (command.isEmpty()) return "Waiting for progress";
    QString value = command.replace('-', ' ');
    if (!value.isEmpty()) value[0] = value[0].toUpper();
    return value;
}
static QString scopeTypeFor(const QJsonObject &phase) {
    const QString workspace = phase.value("workspace").toString().trimmed();
    if (!workspace.isEmpty()) return "workspace";
    const QString target = phase.value("target").toString().trimmed();
    if (!target.isEmpty()) return "target";
    return "";
}
static QString scopeNameFor(const QJsonObject &phase) {
    const QString workspace = phase.value("workspace").toString().trimmed();
    if (!workspace.isEmpty()) return workspace;
    const QString target = phase.value("target").toString().trimmed();
    if (!target.isEmpty()) return target;
    return "";
}

static bool terminalStatus(const QString &status) {
    return status == "ok" || status == "failed" || status == "cancelled" || status == "interrupted" || status == "skipped";
}

static double phaseProgressScore(const QJsonObject &phase) {
    const auto percent = phase.value("percent");
    if (percent.isDouble()) return qBound(0.0, percent.toDouble(-1.0), 100.0);
    const int completed = phase.value("completed").toInt(phase.value("current").toInt(-1));
    const int total = phase.value("total").toInt();
    if (completed >= 0 && total > 0) return qBound(0.0, static_cast<double>(completed) / static_cast<double>(total) * 100.0, 100.0);
    if (completed >= 0) return static_cast<double>(completed);
    return -1.0;
}

static void copyPhaseProgressFields(QJsonObject &dst, const QJsonObject &src) {
    for (const auto &key : {"percent", "current", "completed", "total"}) {
        if (src.contains(key)) dst[key] = src.value(key);
    }
}

static qint64 stateSequence(const QJsonObject &state) {
    const auto sequence = state.value("sequence");
    if (sequence.isDouble()) return static_cast<qint64>(sequence.toDouble());
    const QString eventId = state.value("event_id").toString();
    const int split = eventId.lastIndexOf('-');
    if (split >= 0) {
        bool ok = false;
        const qint64 parsed = eventId.mid(split + 1).toLongLong(&ok);
        if (ok) return parsed;
    }
    return 0;
}

static QString phaseIdentity(const QJsonObject &phase) {
    return phase.value("name").toString().toLower() + QChar(0x1f)
        + phase.value("workspace").toString().toLower() + QChar(0x1f)
        + phase.value("target").toString().toLower();
}

static bool incomingIsOlder(const QJsonObject &current, const QJsonObject &incoming) {
    if (current.isEmpty()) return false;
    const qint64 currentSequence = stateSequence(current);
    const qint64 incomingSequence = stateSequence(incoming);
    if (currentSequence > 0 && incomingSequence > 0 && incomingSequence != currentSequence)
        return incomingSequence < currentSequence;
    const QString currentUpdated = current.value("updated_at").toString();
    const QString incomingUpdated = incoming.value("updated_at").toString();
    return !currentUpdated.isEmpty() && !incomingUpdated.isEmpty() && incomingUpdated < currentUpdated;
}

static QJsonObject mergePhaseProgress(const QJsonObject &current, const QJsonObject &incoming) {
    if (current.isEmpty()) return incoming;
    if (incoming.isEmpty() || incomingIsOlder(current, incoming)) return current;

    QJsonObject merged = incoming;
    const QString oldStatus = current.value("status").toString();
    const QString newStatus = incoming.value("status").toString();
    const bool oldTerminal = terminalStatus(oldStatus);
    const bool newTerminal = terminalStatus(newStatus);
    const double oldProgress = phaseProgressScore(current);
    const double newProgress = phaseProgressScore(incoming);

    if (oldTerminal && !newTerminal) {
        merged["status"] = oldStatus;
    } else if (newTerminal) {
        merged["status"] = newStatus;
    } else if (oldStatus == QStringLiteral("pending") && newStatus != QStringLiteral("pending")) {
        merged["status"] = newStatus;
    } else if (oldProgress > newProgress) {
        merged["status"] = oldStatus;
    }

    if (oldProgress > newProgress) copyPhaseProgressFields(merged, current);
    for (const auto &key : {"errors", "warnings", "tests_passed", "tests_failed", "tests_skipped", "tests_total", "test_assemblies"}) {
        if (current.value(key).toInt() > incoming.value(key).toInt()) merged[key] = current.value(key);
    }
    for (const auto &key : {"command", "unit_label"}) {
        if (merged.value(key).toString().isEmpty() && !current.value(key).toString().isEmpty()) merged[key] = current.value(key);
    }
    if (merged.value("detail").toString().isEmpty() && !current.value("detail").toString().isEmpty()) merged["detail"] = current.value("detail");
    return merged;
}

static QJsonObject mergeRunState(const QJsonObject &current, const QJsonObject &incoming) {
    if (current.isEmpty()) return incoming;
    if (incoming.isEmpty()) return current;
    if (current.value("run_id").toString() != incoming.value("run_id").toString()) return incoming;
    if (incomingIsOlder(current, incoming)) return current;

    QJsonObject merged = incoming;
    QHash<QString, QJsonObject> phases;
    QStringList order;
    const auto remember = [&](const QJsonArray &items, bool mergeIncoming) {
        for (const auto &value : items) {
            if (!value.isObject()) continue;
            const QJsonObject item = value.toObject();
            const QString key = phaseIdentity(item);
            if (key.trimmed().isEmpty()) continue;
            if (!phases.contains(key)) order << key;
            phases[key] = mergeIncoming ? mergePhaseProgress(phases.value(key), item) : item;
        }
    };
    remember(current.value("phase_progress").toArray(), false);
    remember(incoming.value("phase_progress").toArray(), true);
    QJsonArray phaseArray;
    for (const auto &key : order) phaseArray.append(phases.value(key));
    merged["phase_progress"] = phaseArray;

    const QString oldStatus = current.value("status").toString();
    const QString newStatus = incoming.value("status").toString();
    if (terminalStatus(oldStatus) && !terminalStatus(newStatus)) merged["status"] = oldStatus;
    if (current.value("errors").toInt() > incoming.value("errors").toInt()) merged["errors"] = current.value("errors");
    if (current.value("warnings").toInt() > incoming.value("warnings").toInt()) merged["warnings"] = current.value("warnings");
    return merged;
}
HudStateStore::HudStateStore(HistoryModel *history, QObject *parent) : QObject(parent), m_history(history), m_settings("mikeceead","DevtoolControlCenter") {
    m_stateDirectory = m_settings.value("liveStateDirectory", defaultStateDir()).toString();
    m_poll.setInterval(10000);
    connect(&m_poll,&QTimer::timeout,this,&HudStateStore::refresh);
    m_poll.start();
    connect(&m_watcher,&QFileSystemWatcher::fileChanged,this,&HudStateStore::refresh);
    connect(&m_watcher,&QFileSystemWatcher::directoryChanged,this,&HudStateStore::refresh);
    refresh();
}
void HudStateStore::setStateDirectory(const QString &path) {
    const QString clean=QDir::cleanPath(QDir::fromNativeSeparators(path)); if (clean==m_stateDirectory || clean.isEmpty()) return;
    m_stateDirectory=clean; m_settings.setValue("liveStateDirectory", clean); emit stateDirectoryChanged(); rebuildWatchList(); refresh();
}
QJsonObject HudStateStore::readObject(const QString &path) const {
    QFile f(path); if (!f.open(QIODevice::ReadOnly)) return {};
    QJsonParseError e{}; auto doc=QJsonDocument::fromJson(f.readAll(),&e); return e.error==QJsonParseError::NoError&&doc.isObject()?doc.object():QJsonObject{};
}
QString HudStateStore::resolveActiveDirectory() const {
    const auto active=readObject(m_stateDirectory+"/hud-active.json");
    const QString p=active.value("directory").toString(); return p.isEmpty()?m_stateDirectory:p;
}
QVariantList HudStateStore::buildSteps(const QJsonObject &state) const {
    struct Step {
        QString base;
        QString name;
        QString scopeType;
        QString scope;
        QString workspace;
        QString target;
        QString workspaceRoot;
        QString status = "pending";
        QString detail;
        QString command;
        QString unitLabel;
        QString updatedAt;
        int progress = 0;
        int completed = -1;
        int total = -1;
        int warnings = 0;
        int errors = 0;
        int testsPassed = 0;
        int testsFailed = 0;
        int testsSkipped = 0;
        int testsTotal = 0;
        int testAssemblies = 0;
        bool validator = false;
    };
    QList<Step> steps;
    QHash<QString,int> indexes;
    auto add=[&](const QJsonObject &source) -> int {
        const QString name = source.value("name").toString();
        const QString base = baseName(name);
        if (base.isEmpty()) return -1;
        const QString scopeType = scopeTypeFor(source);
        const QString scope = scopeNameFor(source);
        const QString key = base + QChar(0x1f) + scopeType.toLower() + QChar(0x1f) + scope.toLower();
        if (indexes.contains(key)) {
            auto &existing = steps[indexes.value(key)];
            if (existing.name.isEmpty()) existing.name = name;
            if (existing.workspace.isEmpty()) existing.workspace = source.value("workspace").toString().trimmed();
            if (existing.target.isEmpty()) existing.target = source.value("target").toString().trimmed();
            if (existing.workspaceRoot.isEmpty()) existing.workspaceRoot = source.value("workspace_root").toString().trimmed();
            existing.validator = existing.validator || validatorName(name);
            return indexes.value(key);
        }
        Step step;
        step.base = base;
        step.name = name;
        step.scopeType = scopeType;
        step.scope = scope;
        step.workspace = source.value("workspace").toString().trimmed();
        step.target = source.value("target").toString().trimmed();
        step.workspaceRoot = source.value("workspace_root").toString().trimmed();
        step.validator = validatorName(name);
        const int index = static_cast<int>(steps.size());
        indexes[key] = index;
        steps.push_back(step);
        return index;
    };
    const auto pipeline = state.value("pipeline").toArray();
    for (const auto &value : pipeline) if (value.isObject()) add(value.toObject());
    if (state.value("command").toString().startsWith("apply")) {
        const QString extractKey = QStringLiteral("extract") + QChar(0x1f) + QChar(0x1f);
        if (!indexes.contains(extractKey)) {
            Step step; step.base = "extract"; step.name = "extract";
            steps.prepend(step);
            indexes.clear();
            for (int i=0; i<steps.size(); ++i)
                indexes[steps[i].base + QChar(0x1f) + steps[i].scopeType.toLower() + QChar(0x1f) + steps[i].scope.toLower()] = i;
        }
        QJsonObject commit; commit["name"] = QStringLiteral("commit"); add(commit);
    }
    for (const auto &value : state.value("phase_progress").toArray()) {
        if (!value.isObject()) continue;
        const QJsonObject object = value.toObject();
        const int index = add(object);
        if (index < 0) continue;
        auto &step = steps[index];
        step.status = object.value("status").toString("running");
        step.detail = object.value("detail").toString();
        step.command = object.value("command").toString();
        step.unitLabel = object.value("unit_label").toString();
        step.updatedAt = object.value("updated_at").toString();
        step.completed = object.value("completed").toInt(object.value("current").toInt(-1));
        step.total = object.value("total").toInt(-1);
        step.warnings = object.value("warnings").toInt();
        step.errors = object.value("errors").toInt();
        step.testsPassed = object.value("tests_passed").toInt();
        step.testsFailed = object.value("tests_failed").toInt();
        step.testsSkipped = object.value("tests_skipped").toInt();
        step.testsTotal = object.value("tests_total").toInt();
        step.testAssemblies = object.value("test_assemblies").toInt();
        if (object.value("percent").isDouble()) {
            const double percent = object.value("percent").toDouble(-1.0);
            if (percent >= 0.0) step.progress = qBound(0, qCeil(percent), 100);
        } else if (step.completed >= 0 && step.total > 0) {
            step.progress = qBound(0, qRound(100.0 * step.completed / step.total), 100);
        }
        if (terminalStatus(step.status) && step.progress == 0) step.progress = 100;
    }
    const QJsonObject phase = state.value("phase").toObject();
    const int currentIndex = add(phase);
    if (currentIndex >= 0 && steps[currentIndex].status == "pending")
        steps[currentIndex].status = state.value("status").toString() == "running" ? "running" : state.value("status").toString();

    // A successful terminal run is authoritative. Some producers do not emit a
    // final commit/workspace phase update, which previously left the HUD at 80%
    // even though the run itself had completed successfully.
    const QString runStatus = state.value("status").toString().trimmed().toLower();
    if (runStatus == QStringLiteral("ok") || runStatus == QStringLiteral("success") || runStatus == QStringLiteral("completed")) {
        for (auto &step : steps) {
            if (step.status != QStringLiteral("failed") && step.status != QStringLiteral("cancelled") && step.status != QStringLiteral("interrupted")) {
                step.status = QStringLiteral("ok");
                step.progress = 100;
                if (step.total > 0) step.completed = step.total;
            }
        }
    }

    QVariantList result;
    for (const auto &step : steps) {
        QVariantMap map;
        const QString phaseLabel = step.validator ? QStringLiteral("Validator") : labelFor(step.base);
        map["key"] = step.base+":"+step.scopeType+":"+step.scope;
        map["name"] = step.name;
        map["base"] = step.base;
        map["phaseLabel"] = phaseLabel;
        map["label"] = phaseLabel + (step.scope.isEmpty() ? QString() : QStringLiteral(" · ") + step.scope);
        map["scopeType"] = step.scopeType;
        map["scope"] = step.scope;
        map["workspace"] = step.workspace;
        map["target"] = step.target;
        map["workspaceRoot"] = step.workspaceRoot;
        map["status"] = step.status;
        map["progress"] = step.progress;
        map["detail"] = step.detail;
        map["command"] = step.command;
        map["unitLabel"] = step.unitLabel;
        map["updatedAt"] = step.updatedAt;
        map["completed"] = step.completed;
        map["total"] = step.total;
        map["warnings"] = step.warnings;
        map["errors"] = step.errors;
        map["testsPassed"] = step.testsPassed;
        map["testsFailed"] = step.testsFailed;
        map["testsSkipped"] = step.testsSkipped;
        map["testsTotal"] = step.testsTotal;
        map["testAssemblies"] = step.testAssemblies;
        map["validator"] = step.validator;
        result << map;
    }
    return result;
}

QVariantList HudStateStore::buildDiagnostics(const QJsonObject &state) const {
    QVariantList result;
    QSet<QString> seen;
    auto append=[&](QVariantMap item){
        QString detail=item.value("detail").toString().trimmed();
        if(detail.isEmpty()) detail=item.value("message").toString().trimmed();
        if(detail.isEmpty())return;
        const QString key=(item.value("severity").toString()+"|"+item.value("code").toString()+"|"+detail).toLower();
        if(seen.contains(key))return; seen.insert(key); item["detail"]=detail; result<<item;
    };
    const auto completion=state.value("completion").toObject();
    for(const auto &value:completion.value("diagnostics").toArray()) if(value.isObject()) append(value.toObject().toVariantMap());
    for(const auto &resultValue:state.value("results").toArray()){
        const auto resultObject=resultValue.toObject();
        const QString phase=resultObject.value("name").toString();
        for(const auto &issueValue:resultObject.value("issues").toArray()){
            if(!issueValue.isObject())continue; auto item=issueValue.toObject().toVariantMap(); item["phase"]=phase; append(item);
        }
    }
    if(result.isEmpty()) for(const auto &value:completion.value("warning_details").toArray()){QVariantMap item;item["severity"]="warning";item["message"]=value.toString();append(item);}
    return result;
}


void HudStateStore::setStandby(bool standby) {
    if (m_standby == standby) return;
    m_standby = standby;
    if (m_ipcConnected) return;
    const int interval = m_standby ? 30000 : 1000;
    if (m_poll.interval() != interval) m_poll.setInterval(interval);
    if (!m_poll.isActive()) m_poll.start();
    if (!m_standby) refresh();
}

void HudStateStore::setSelectedRunId(const QString &runId) {
    if(runId==m_selectedRunId)return; m_selectedRunId=runId; emit selectedRunIdChanged(); refresh();
}

QJsonObject HudStateStore::normalize(QJsonObject state) const {
    const QVariantList steps = buildSteps(state);
    state["steps"] = QJsonArray::fromVariantList(steps);
    const auto totals = state.value("totals").toObject();
    int errors = totals.value("errors").toInt();
    int warnings = totals.value("warnings").toInt();
    for (const auto &value : state.value("phase_progress").toArray()) {
        const auto object = value.toObject();
        errors = qMax(errors, object.value("errors").toInt());
        warnings = qMax(warnings, object.value("warnings").toInt());
    }
    state["errors"] = errors;
    state["warnings"] = warnings;
    const auto completion = state.value("completion").toObject();
    QJsonValue duration = completion.value("duration_seconds");
    if (duration.isUndefined() || duration.isNull()) duration = completion.value("duration_s");
    state["duration_seconds"] = duration;
    state["completion_headline"] = completion.value("headline");
    state["completion_detail"] = completion.value("detail");
    state["rolled_back"] = completion.value("rolled_back").toBool();
    state["rollback_failed"] = completion.value("rollback_failed").toBool();
    state["failure_phase"] = completion.value("failure_phase");
    state["tests"] = completion.value("tests");
    state["task_title"] = humanCommand(state.value("command").toString());
    state["title"] = state.value("artifact_name").toString(state.value("command").toString("devtool"));

    QStringList targetLabels;
    for (const auto &value : state.value("targets").toArray()) {
        const QString label = value.toString().trimmed();
        if (!label.isEmpty() && !targetLabels.contains(label)) targetLabels << label;
    }
    const QString fallbackTarget = state.value("target").toString().trimmed();
    if (!targetLabels.isEmpty()) state["target_summary"] = targetLabels.join(" • ");
    else state["target_summary"] = fallbackTarget;

    QString logPath = state.value("log_path").toString().trimmed();
    if (logPath.isEmpty()) logPath = state.value("result").toObject().value("log_path").toString().trimmed();
    if (logPath.isEmpty()) {
        const auto results = state.value("results").toArray();
        for (qsizetype index = results.size(); index > 0; --index) {
            const QString candidate = results.at(index - 1).toObject().value("log_path").toString().trimmed();
            if (!candidate.isEmpty()) { logPath = candidate; break; }
        }
    }
    state["log_path"] = logPath;
    return state;
}
void HudStateStore::applyObjects(const QList<QJsonObject> &source) {
    QList<QJsonObject> objects; QSet<QString> seen;
    for(auto object:source){if(object.isEmpty())continue;object=normalize(object);const QString id=object.value("run_id").toString();if(id.isEmpty()||seen.contains(id))continue;seen.insert(id);objects<<object;}
    std::sort(objects.begin(),objects.end(),[](const QJsonObject&a,const QJsonObject&b){return a.value("updated_at").toString()>b.value("updated_at").toString();});
    QVariantList list;for(const auto&o:objects)list<<o.toVariantMap();
    QJsonObject primary;
    if(!m_selectedRunId.isEmpty())for(const auto&o:objects)if(o.value("run_id").toString()==m_selectedRunId){primary=o;break;}
    if(primary.isEmpty()&&!objects.isEmpty()){primary=objects.first();const QString fallback=primary.value("run_id").toString();if(fallback!=m_selectedRunId){m_selectedRunId=fallback;emit selectedRunIdChanged();}}
    const bool c=!primary.isEmpty();
    if(list!=m_activeRuns){m_activeRuns=list;emit activeRunsChanged();}
    if(primary!=m_activeRun){m_activeRun=primary;emit activeRunChanged();}
    const QVariantList diagnosticList=buildDiagnostics(primary);if(diagnosticList!=m_diagnostics){m_diagnostics=diagnosticList;emit diagnosticsChanged();}
    if(c!=m_connected){m_connected=c;emit connectedChanged();}
    for(const auto&o:objects){const QString st=o.value("status").toString();if(st=="ok"||st=="failed"||st=="cancelled"||st=="interrupted")m_history->record(o);}
}

void HudStateStore::refresh() {
    if(m_ipcConnected){applyObjects(m_ipcRuns.values());return;}
    const QString activeDir=resolveActiveDirectory();
    QHash<QString,QJsonObject> nextFileRuns;
    auto remember=[&](const QJsonObject &object){
        if(object.isEmpty())return;
        const QString id=object.value("run_id").toString();
        if(id.isEmpty())return;
        nextFileRuns[id]=mergeRunState(m_fileRuns.value(id),object);
    };
    QDir runs(activeDir+"/runs");
    for(const auto&f:runs.entryList({"*.json"},QDir::Files,QDir::Time))remember(readObject(runs.filePath(f)));
    remember(readObject(activeDir+"/state.json"));
    m_fileRuns = nextFileRuns;
    applyObjects(m_fileRuns.values());
    rebuildWatchList();
    bool hasRunning = false;
    for (const auto &object : std::as_const(m_fileRuns)) {
        if (object.value("status").toString() == QStringLiteral("running")) { hasRunning = true; break; }
    }
    const int desiredInterval = m_standby ? 30000 : (hasRunning ? 1000 : 10000);
    if (m_poll.interval() != desiredInterval) m_poll.setInterval(desiredInterval);
}

void HudStateStore::ingestSnapshot(const QVariantList &runs){
    m_ipcRuns.clear();
    for(const auto&value:runs){
        const auto object=QJsonObject::fromVariantMap(value.toMap());
        const QString id=object.value("run_id").toString();
        if(!id.isEmpty())m_ipcRuns[id]=mergeRunState(m_ipcRuns.value(id),object);
    }
    if(m_ipcConnected)applyObjects(m_ipcRuns.values());
}
void HudStateStore::ingestRun(const QVariantMap &run){
    const auto object=QJsonObject::fromVariantMap(run);
    const QString id=object.value("run_id").toString();
    if(id.isEmpty())return;
    m_ipcRuns[id]=mergeRunState(m_ipcRuns.value(id),object);
    if(m_ipcConnected)applyObjects(m_ipcRuns.values());
}
void HudStateStore::removeRun(const QString &runId){m_ipcRuns.remove(runId);if(m_ipcConnected)applyObjects(m_ipcRuns.values());}
void HudStateStore::setIpcConnected(bool connected){
    if(connected==m_ipcConnected)return;
    m_ipcConnected=connected;
    emit ipcConnectedChanged();
    if(connected){
        m_poll.stop();
        applyObjects(m_ipcRuns.values());
    }else{
        m_poll.setInterval(1000);
        m_poll.start();
        refresh();
    }
}
void HudStateStore::rebuildWatchList() {
    if(!m_watcher.files().isEmpty())m_watcher.removePaths(m_watcher.files()); if(!m_watcher.directories().isEmpty())m_watcher.removePaths(m_watcher.directories());
    QStringList paths; if(QFileInfo::exists(m_stateDirectory))paths<<m_stateDirectory; const QString active=resolveActiveDirectory(); if(active!=m_stateDirectory&&QFileInfo::exists(active))paths<<active; if(QFileInfo::exists(active+"/runs"))paths<<active+"/runs";
    for(const auto &f:{active+"/state.json",m_stateDirectory+"/hud-active.json"})if(QFileInfo::exists(f))paths<<f; if(!paths.isEmpty())m_watcher.addPaths(paths);
}
QVariantList HudStateStore::stepsFor(const QVariantMap &run) const { return run.value("steps").toList(); }
QString HudStateStore::focusedSummary(const QVariantMap &run) const {
    const QString command = run.value("command").toString();
    QString statusLabel;
    if (run.value("rollback_failed").toBool()) statusLabel = QStringLiteral("Failed; rollback also failed");
    else if (run.value("rolled_back").toBool()) statusLabel = QStringLiteral("Failed; repository restored by rollback");
    else if (run.value("status").toString() == QStringLiteral("ok")) statusLabel = QStringLiteral("Completed successfully");
    else if (run.value("status").toString() == QStringLiteral("running")) statusLabel = QStringLiteral("Running");
    else statusLabel = QStringLiteral("Failed");

    QString output = QStringLiteral("Devtool — ") + humanCommand(command);
    output += QStringLiteral("\nStatus: ") + statusLabel;
    const QString repository = run.value("repo_name").toString();
    if (!repository.isEmpty()) output += QStringLiteral("\nRepository: ") + repository;
    const QString artifact = run.value("artifact_name").toString();
    if (!artifact.isEmpty()) output += QStringLiteral("\nArtifact: ") + artifact;

    struct Lane {
        QString title;
        QString target;
        QString status = "pending";
        int stepCount = 0;
        int completedSteps = 0;
        bool running = false;
        bool failedStatus = false;
        int passed = 0;
        int failed = 0;
        int skipped = 0;
        int assemblies = 0;
        int warnings = 0;
        int errors = 0;
        QString validator = "not required";
    };
    QHash<QString, Lane> lanes;
    QStringList order;
    for (const auto &value : run.value("steps").toList()) {
        const QVariantMap step = value.toMap();
        const QString scopeType = step.value("scopeType").toString();
        const QString scope = step.value("scope").toString();
        if (scope.isEmpty()) continue;
        const QString key = scopeType + QChar(0x1f) + scope;
        if (!lanes.contains(key)) {
            Lane lane;
            lane.title = scope;
            lane.target = step.value("target").toString();
            lanes.insert(key, lane);
            order << key;
        }
        Lane &lane = lanes[key];
        if (lane.target.isEmpty()) lane.target = step.value("target").toString();
        const QString stepStatus = step.value("status").toString();
        lane.stepCount++;
        if (stepStatus == "failed" || stepStatus == "cancelled" || stepStatus == "interrupted") lane.failedStatus = true;
        else if (stepStatus == "running") lane.running = true;
        else if (stepStatus == "ok" || stepStatus == "skipped") lane.completedSteps++;
        if (step.value("base").toString() == "test") {
            lane.passed = qMax(lane.passed, step.value("testsPassed").toInt());
            lane.failed = qMax(lane.failed, step.value("testsFailed").toInt());
            lane.skipped = qMax(lane.skipped, step.value("testsSkipped").toInt());
            lane.assemblies = qMax(lane.assemblies, step.value("testAssemblies").toInt());
        }
        lane.warnings += step.value("warnings").toInt();
        lane.errors += step.value("errors").toInt();
        if (step.value("validator").toBool()) {
            lane.validator = stepStatus == "ok" ? "passed" : stepStatus == "failed" ? "failed" : stepStatus == "running" ? "running" : "pending";
        }
    }
    if (!order.isEmpty()) {
        output += QStringLiteral("\n\nWorkspaces:");
        for (const auto &key : order) {
            Lane lane = lanes.value(key);
            if (lane.failedStatus) lane.status = QStringLiteral("failed");
            else if (lane.running) lane.status = QStringLiteral("running");
            else if (lane.stepCount > 0 && lane.completedSteps == lane.stepCount) lane.status = QStringLiteral("complete");
            else lane.status = QStringLiteral("pending");
            output += QStringLiteral("\n- ") + lane.title;
            if (!lane.target.isEmpty() && lane.target != lane.title) output += QStringLiteral(" [") + lane.target + QStringLiteral("]");
            output += QStringLiteral(" — ") + lane.status;
            if (lane.assemblies || lane.passed || lane.failed || lane.skipped)
                output += QStringLiteral("; %1 passed, %2 failed, %3 skipped across %4 test assemblies")
                    .arg(lane.passed).arg(lane.failed).arg(lane.skipped).arg(lane.assemblies);
            output += QStringLiteral("; validator: ") + lane.validator;
        }
    }

    const QVariantMap tests = run.value("tests").toMap();
    if (!tests.isEmpty()) {
        output += QStringLiteral("\n\nTests: %1 passed, %2 failed, %3 skipped (%4 total)")
            .arg(tests.value("passed").toInt()).arg(tests.value("failed").toInt())
            .arg(tests.value("skipped").toInt()).arg(tests.value("total").toInt());
    }
    output += QStringLiteral("\nDiagnostics: %1 errors, %2 warnings")
        .arg(run.value("errors").toInt()).arg(run.value("warnings").toInt());
    if (run.value("rolled_back").toBool()) output += QStringLiteral("\nRollback: succeeded");
    else if (run.value("rollback_failed").toBool()) output += QStringLiteral("\nRollback: failed");
    else output += QStringLiteral("\nRollback: not required");
    QString message = run.value("completion_detail").toString();
    if (message.isEmpty()) message = run.value("message").toString();
    if (!message.isEmpty()) output += QStringLiteral("\n\n") + message;
    return output;
}
void HudStateStore::copySummary(const QVariantMap &run) const {
    if (auto *clipboard = QGuiApplication::clipboard()) clipboard->setText(focusedSummary(run));
}
bool HudStateStore::openLog(const QVariantMap &run) const {
    QString path = run.value("log_path").toString().trimmed();
    if (path.isEmpty()) return false;
    if (QDir::isRelativePath(path)) {
        const QString root = run.value("repo_root").toString().trimmed();
        if (!root.isEmpty()) path = QDir(root).filePath(path);
    }
    return QDesktopServices::openUrl(QUrl::fromLocalFile(QFileInfo(path).absoluteFilePath()));
}
