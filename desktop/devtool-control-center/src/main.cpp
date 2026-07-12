#include "AppController.h"
#include "ArtifactInspector.h"
#include "ClipboardMonitor.h"
#include "HistoryModel.h"
#include "HudStateStore.h"
#include "IpcClient.h"
#include "ProcessRunner.h"
#include "RepositoryModel.h"

#include <QApplication>
#include <QCoreApplication>
#include <QIODevice>
#include <QFileInfo>
#include <QIcon>
#include <QLocalServer>
#include <QLocalSocket>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QProcess>
#include <QTimer>
#include <QUrl>
#include <QWindow>

namespace {
constexpr const char *kMainServerName = "io.github.mikeceead.DevtoolControlCenter.main";

QIcon controlCenterIcon() {
    return QIcon(QStringLiteral(":/icons/devtool-control-center.svg"));
}

QString resolveHudExecutable() {
    const QStringList candidates {
        QCoreApplication::applicationDirPath() + QStringLiteral("/devtool-control-center-hud"),
        QCoreApplication::applicationDirPath() + QStringLiteral("/devtool-control-center-hud.exe"),
    };
    for (const QString &candidate : candidates) {
        const QFileInfo file(candidate);
        if (file.isFile() && file.isExecutable()) return file.absoluteFilePath();
    }
    return QString();
}

bool sendMessage(const QString &serverName, const QString &message) {
    QLocalSocket socket;
    socket.connectToServer(serverName, QIODevice::WriteOnly);
    if (!socket.waitForConnected(300)) return false;
    socket.write(message.toUtf8());
    socket.flush();
    socket.waitForBytesWritten(300);
    socket.disconnectFromServer();
    return true;
}

void installActivationServer(QLocalServer *server, QWindow *mainWindow, AppController *controller) {
    QObject::connect(server, &QLocalServer::newConnection, server, [server, mainWindow, controller] {
        while (QLocalSocket *socket = server->nextPendingConnection()) {
            socket->setParent(server);
            if (!socket->waitForReadyRead(1000)) {
                socket->deleteLater();
                continue;
            }
            const QString command = QString::fromUtf8(socket->readAll()).trimmed();
            if (command == QStringLiteral("show-main")) {
                if (mainWindow) {
                    mainWindow->show();
                    mainWindow->raise();
                    mainWindow->requestActivate();
                }
            } else if (command == QStringLiteral("show-hud")) {
                if (controller) controller->launchHud();
            }
            socket->deleteLater();
        }
    });
}
}  // namespace

int main(int argc, char **argv) {
    QApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("Devtool Control Center"));
    app.setOrganizationName(QStringLiteral("mikeceead"));
    app.setDesktopFileName(QStringLiteral("io.github.mikeceead.DevtoolControlCenter"));
    app.setWindowIcon(controlCenterIcon());

    if (app.arguments().contains(QStringLiteral("--clipboard-monitor"))) {
        app.setQuitOnLastWindowClosed(false);
        ClipboardMonitor clipboardMonitor(true, true);
        return app.exec();
    }

    if (app.arguments().contains(QStringLiteral("--compact-hud"))) {
        if (sendMessage(QString::fromLatin1(kMainServerName), QStringLiteral("show-hud"))) return 0;
        return QProcess::startDetached(resolveHudExecutable(), QStringList{}) ? 0 : 1;
    }

    if (sendMessage(QString::fromLatin1(kMainServerName), QStringLiteral("show-main"))) return 0;

    const bool exitWhenIdle = app.arguments().contains(QStringLiteral("--exit-when-idle"));

    HistoryModel history;
    HudStateStore hud(&history);
    ProcessRunner runner;
    RepositoryModel repositories;
    ArtifactInspector inspector;
    IpcClient ipc;
    AppController controller(&hud, &runner, &history, &ipc);

    inspector.setDevtoolExecutable(controller.devtoolExecutable());
    inspector.setRepositoryPath(controller.repository());
    inspector.setArtifactPath(controller.artifact());

    QObject::connect(&controller, &AppController::devtoolExecutableChanged, &inspector,
                     [&] { inspector.setDevtoolExecutable(controller.devtoolExecutable()); });
    QObject::connect(&controller, &AppController::repositoryChanged, &inspector,
                     [&] { inspector.setRepositoryPath(controller.repository()); repositories.markUsed(controller.repository()); });
    QObject::connect(&controller, &AppController::artifactChanged, &inspector,
                     [&] { inspector.setArtifactPath(controller.artifact()); });
    QObject::connect(&hud, &HudStateStore::activeRunsChanged, &repositories,
                     [&] { repositories.updateActiveRuns(hud.activeRuns()); });
    QObject::connect(&ipc, &IpcClient::snapshotReceived, &hud, &HudStateStore::ingestSnapshot);
    QObject::connect(&ipc, &IpcClient::runUpdated, &hud, &HudStateStore::ingestRun);
    QObject::connect(&ipc, &IpcClient::runRemoved, &hud, &HudStateStore::removeRun);
    QObject::connect(&ipc, &IpcClient::connectedChanged, &hud,
                     [&] { hud.setIpcConnected(ipc.connected()); });

    if (!controller.repository().isEmpty()) repositories.addRepository(controller.repository());
    if (controller.devtoolAvailable()) {
        QProcess::startDetached(controller.devtoolExecutable(),
                                QStringList{QStringLiteral("ipc-server"), QStringLiteral("--state-dir"), hud.stateDirectory()});
    }
    QTimer::singleShot(150, &ipc, &IpcClient::start);
    if (exitWhenIdle) QTimer::singleShot(1200, &app, &QCoreApplication::quit);

    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty(QStringLiteral("appController"), &controller);
    engine.rootContext()->setContextProperty(QStringLiteral("hudStore"), &hud);
    engine.rootContext()->setContextProperty(QStringLiteral("processRunner"), &runner);
    engine.rootContext()->setContextProperty(QStringLiteral("historyModel"), &history);
    engine.rootContext()->setContextProperty(QStringLiteral("artifactInspector"), &inspector);
    engine.rootContext()->setContextProperty(QStringLiteral("repositoryModel"), &repositories);
    engine.rootContext()->setContextProperty(QStringLiteral("ipcClient"), &ipc);
    engine.load(QUrl(QStringLiteral("qrc:/qt/qml/Devtool/ControlCenter/qml/Main.qml")));
    if (engine.rootObjects().isEmpty()) return 1;

    auto *mainWindow = qobject_cast<QWindow *>(engine.rootObjects().first());
    QObject::connect(&controller, &AppController::requestMainWindow, mainWindow,
                     [mainWindow] {
                         if (!mainWindow) return;
                         mainWindow->show();
                         mainWindow->raise();
                         mainWindow->requestActivate();
                     });

    QLocalServer::removeServer(QString::fromLatin1(kMainServerName));
    auto *server = new QLocalServer(&app);
    if (server->listen(QString::fromLatin1(kMainServerName))) {
        installActivationServer(server, mainWindow, &controller);
    }

    return app.exec();
}
