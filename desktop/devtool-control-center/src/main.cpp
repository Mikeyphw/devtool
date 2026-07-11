#include "AppController.h"
#include "ArtifactInspector.h"
#include "RepositoryModel.h"
#include "HistoryModel.h"
#include "HudStateStore.h"
#include "ProcessRunner.h"
#include "IpcClient.h"
#include "ClipboardMonitor.h"
#include <QApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QProcess>
#include <QTimer>
#include <QWindow>
#include <QUrl>
int main(int argc,char**argv){
    QApplication app(argc,argv);
    // Keep the platform palette, font, scale, and Qt Quick Controls style.
    // Users can override the control style with -style or QT_QUICK_CONTROLS_STYLE.
    app.setApplicationName("Devtool Control Center");app.setOrganizationName("mikeceead");app.setDesktopFileName("io.github.mikeceead.DevtoolControlCenter");
    if (app.arguments().contains(QStringLiteral("--clipboard-monitor"))) {
        app.setQuitOnLastWindowClosed(false);
        ClipboardMonitor clipboardMonitor(true, true);
        return app.exec();
    }
HistoryModel history;HudStateStore hud(&history);ProcessRunner runner;RepositoryModel repositories;ArtifactInspector inspector;IpcClient ipc;AppController controller(&hud,&runner,&history,&ipc);
    inspector.setDevtoolExecutable(controller.devtoolExecutable());inspector.setRepositoryPath(controller.repository());inspector.setArtifactPath(controller.artifact());
    QObject::connect(&controller,&AppController::devtoolExecutableChanged,&inspector,[&]{inspector.setDevtoolExecutable(controller.devtoolExecutable());});
    QObject::connect(&controller,&AppController::repositoryChanged,&inspector,[&]{inspector.setRepositoryPath(controller.repository());repositories.markUsed(controller.repository());});
    QObject::connect(&controller,&AppController::artifactChanged,&inspector,[&]{inspector.setArtifactPath(controller.artifact());});
    QObject::connect(&hud,&HudStateStore::activeRunsChanged,&repositories,[&]{repositories.updateActiveRuns(hud.activeRuns());});
    QObject::connect(&ipc,&IpcClient::snapshotReceived,&hud,&HudStateStore::ingestSnapshot);
    QObject::connect(&ipc,&IpcClient::runUpdated,&hud,&HudStateStore::ingestRun);
    QObject::connect(&ipc,&IpcClient::runRemoved,&hud,&HudStateStore::removeRun);
    QObject::connect(&ipc,&IpcClient::connectedChanged,&hud,[&]{hud.setIpcConnected(ipc.connected());});
    if(!controller.repository().isEmpty())repositories.addRepository(controller.repository());
    if(controller.devtoolAvailable())QProcess::startDetached(controller.devtoolExecutable(), QStringList{"ipc-server","--state-dir",hud.stateDirectory()});
    QTimer::singleShot(150,&ipc,&IpcClient::start);
    QQmlApplicationEngine engine;engine.rootContext()->setContextProperty("appController",&controller);engine.rootContext()->setContextProperty("hudStore",&hud);engine.rootContext()->setContextProperty("processRunner",&runner);engine.rootContext()->setContextProperty("historyModel",&history);engine.rootContext()->setContextProperty("artifactInspector",&inspector);engine.rootContext()->setContextProperty("repositoryModel",&repositories);engine.rootContext()->setContextProperty("ipcClient",&ipc);
    engine.load(QUrl(QStringLiteral("qrc:/qt/qml/Devtool/ControlCenter/qml/Main.qml")));if(engine.rootObjects().isEmpty())return 1;
    auto *mainWindow=qobject_cast<QWindow*>(engine.rootObjects().first());
    QObject::connect(&controller,&AppController::requestMainWindow,mainWindow,[mainWindow]{if(!mainWindow)return;mainWindow->show();mainWindow->raise();mainWindow->requestActivate();});
    return app.exec();
}
