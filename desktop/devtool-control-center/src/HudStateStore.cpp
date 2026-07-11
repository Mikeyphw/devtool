#include "HudStateStore.h"
#include "HistoryModel.h"

#include <algorithm>

#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QSet>
#include <QHash>
#include <QStandardPaths>
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
HudStateStore::HudStateStore(HistoryModel *history, QObject *parent) : QObject(parent), m_history(history), m_settings("mikeceead","DevtoolControlCenter") {
    m_stateDirectory = m_settings.value("liveStateDirectory", defaultStateDir()).toString();
    m_poll.setInterval(750); connect(&m_poll,&QTimer::timeout,this,&HudStateStore::refresh); m_poll.start();
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
    struct Step { QString base, scope, status="pending", detail; int progress=0; };
    QList<Step> steps; QHash<QString,int> indexes;
    auto add=[&](QString name, QString scope) -> int { const auto b=baseName(name); if (b.isEmpty()) return -1; const auto key=b+"\x1f"+scope.toLower();
        if (indexes.contains(key)) return indexes.value(key); Step s; s.base=b; s.scope=scope; const int index = static_cast<int>(steps.size()); indexes[key]=index; steps.push_back(s); return index; };
    const auto pipeline=state.value("pipeline").toArray();
    for (const auto &v:pipeline) { auto o=v.toObject(); QString n=o.value("name").toString(); QString scope=o.value("workspace").toString(); if(scope.isEmpty())scope=o.value("target").toString(); add(n,scope); }
    if (state.value("command").toString().startsWith("apply")) { if(!indexes.contains("extract\x1f")) { Step s; s.base="extract"; steps.prepend(s); indexes.clear(); for(int i=0;i<steps.size();++i) indexes[steps[i].base+"\x1f"+steps[i].scope.toLower()]=i; } add("commit",""); }
    for (const auto &v:state.value("phase_progress").toArray()) { auto o=v.toObject(); QString n=o.value("name").toString(); QString scope=o.value("workspace").toString(); if(scope.isEmpty())scope=o.value("target").toString(); int i=add(n,scope); if(i<0)continue;
        auto &s=steps[i]; s.status=o.value("status").toString("running"); s.detail=o.value("detail").toString();
        if(o.value("percent").isDouble())s.progress=qBound(0,o.value("percent").toInt(),100); else { int c=o.value("current").toInt(o.value("completed").toInt()); int t=o.value("total").toInt(); if(t>0)s.progress=qBound(0,qRound(100.0*c/t),100); }
        if((s.status=="ok"||s.status=="failed"||s.status=="skipped")&&s.progress==0)s.progress=100;
    }
    const auto phase=state.value("phase").toObject(); QString currentName=phase.value("name").toString(); QString currentScope=phase.value("workspace").toString(); if(currentScope.isEmpty())currentScope=phase.value("target").toString();
    int ci=add(currentName,currentScope); if(ci>=0 && steps[ci].status=="pending") steps[ci].status=state.value("status").toString()=="running"?"running":state.value("status").toString();
    QVariantList result; for(const auto &s:steps){ QVariantMap m; m["key"]=s.base+":"+s.scope; m["label"]=labelFor(s.base)+(s.scope.isEmpty()?QString():" · "+s.scope); m["status"]=s.status; m["progress"]=s.progress; m["detail"]=s.detail; result<<m; } return result;
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

void HudStateStore::setSelectedRunId(const QString &runId) {
    if(runId==m_selectedRunId)return; m_selectedRunId=runId; emit selectedRunIdChanged(); refresh();
}

QJsonObject HudStateStore::normalize(QJsonObject state) const {
    state["steps"] = QJsonArray::fromVariantList(buildSteps(state));
    const auto totals=state.value("totals").toObject(); int errors=totals.value("errors").toInt(); int warnings=totals.value("warnings").toInt();
    for(const auto &v:state.value("phase_progress").toArray()){auto o=v.toObject(); errors=qMax(errors,o.value("errors").toInt()); warnings=qMax(warnings,o.value("warnings").toInt());}
    state["errors"]=errors; state["warnings"]=warnings;
    const auto completion=state.value("completion").toObject(); state["duration_seconds"]=completion.value("duration_seconds");
    state["title"]=state.value("artifact_name").toString(state.value("command").toString("devtool"));
    const auto targets=state.value("targets").toArray(); if(targets.size()>1) state["target_summary"]=QString::number(targets.size())+" targets"; else state["target_summary"]=state.value("target");
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
    const QString activeDir=resolveActiveDirectory();QList<QJsonObject> objects;QSet<QString> seen;
    auto append=[&](QJsonObject o){if(o.isEmpty())return;const QString id=o.value("run_id").toString();if(id.isEmpty()||seen.contains(id))return;seen.insert(id);objects<<o;};
    QDir runs(activeDir+"/runs");for(const auto&f:runs.entryList({"*.json"},QDir::Files,QDir::Time))append(readObject(runs.filePath(f)));
    append(readObject(activeDir+"/state.json"));applyObjects(objects);rebuildWatchList();
}

void HudStateStore::ingestSnapshot(const QVariantList &runs){m_ipcRuns.clear();for(const auto&value:runs){const auto object=QJsonObject::fromVariantMap(value.toMap());const QString id=object.value("run_id").toString();if(!id.isEmpty())m_ipcRuns[id]=object;}if(m_ipcConnected)applyObjects(m_ipcRuns.values());}
void HudStateStore::ingestRun(const QVariantMap &run){const auto object=QJsonObject::fromVariantMap(run);const QString id=object.value("run_id").toString();if(id.isEmpty())return;m_ipcRuns[id]=object;if(m_ipcConnected)applyObjects(m_ipcRuns.values());}
void HudStateStore::removeRun(const QString &runId){m_ipcRuns.remove(runId);if(m_ipcConnected)applyObjects(m_ipcRuns.values());}
void HudStateStore::setIpcConnected(bool connected){if(connected==m_ipcConnected)return;m_ipcConnected=connected;emit ipcConnectedChanged();if(connected){m_poll.stop();applyObjects(m_ipcRuns.values());}else{m_poll.start();refresh();}}
void HudStateStore::rebuildWatchList() {
    if(!m_watcher.files().isEmpty())m_watcher.removePaths(m_watcher.files()); if(!m_watcher.directories().isEmpty())m_watcher.removePaths(m_watcher.directories());
    QStringList paths; if(QFileInfo::exists(m_stateDirectory))paths<<m_stateDirectory; const QString active=resolveActiveDirectory(); if(active!=m_stateDirectory&&QFileInfo::exists(active))paths<<active; if(QFileInfo::exists(active+"/runs"))paths<<active+"/runs";
    for(const auto &f:{active+"/state.json",m_stateDirectory+"/hud-active.json"})if(QFileInfo::exists(f))paths<<f; if(!paths.isEmpty())m_watcher.addPaths(paths);
}
QVariantList HudStateStore::stepsFor(const QVariantMap &run) const { return run.value("steps").toList(); }
QString HudStateStore::focusedSummary(const QVariantMap &run) const {
    const QString status=run.value("status").toString(); QString out=status=="ok"?"Devtool completed successfully.":"Devtool failed.";
    out+="\n\nRepository: "+run.value("repo_name").toString(); const auto target=run.value("target_summary").toString(); if(!target.isEmpty())out+="\nTarget: "+target;
    out+="\nErrors: "+QString::number(run.value("errors").toInt())+"\nWarnings: "+QString::number(run.value("warnings").toInt());
    const auto msg=run.value("message").toString(); if(!msg.isEmpty())out+="\n\n"+msg; return out;
}
