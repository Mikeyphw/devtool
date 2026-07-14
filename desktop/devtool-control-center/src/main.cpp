#include "AppController.h"
#include "ArtifactInspector.h"
#include "ClipboardMonitor.h"
#include "HistoryModel.h"
#include "GitRepositoryController.h"
#include "HudStateStore.h"
#include "IpcClient.h"
#include "ProcessRunner.h"
#include "RepositoryModel.h"

#include <iostream>
#include <QApplication>
#include <QCoreApplication>
#include <QIODevice>
#include <QFileInfo>
#include <QIcon>
#include <QFont>
#include <QFontDatabase>
#include <QLocalServer>
#include <QLocalSocket>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QProcess>
#include <QQuickWindow>
#include <QPointer>
#include <QSettings>
#include <memory>
#include <QTimer>
#include <QUrl>
#include <QWindow>

#ifndef DEVTOOL_CC_VERSION
#define DEVTOOL_CC_VERSION "development"
#endif

namespace {
bool hasArgument(int argc, char **argv, const char *value) {
    for (int index = 1; index < argc; ++index) {
        if (QString::fromLocal8Bit(argv[index]) == QString::fromLatin1(value)) return true;
    }
    return false;
}

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

QString preferredNerdFont() {
    const QString systemFixed = QFontDatabase::systemFont(QFontDatabase::FixedFont).family();
    if (!systemFixed.trimmed().isEmpty()) return systemFixed;
    const QStringList preferred {
        QStringLiteral("JetBrainsMono Nerd Font"), QStringLiteral("CaskaydiaCove Nerd Font"),
        QStringLiteral("MesloLGS NF"), QStringLiteral("FiraCode Nerd Font"),
        QStringLiteral("Symbols Nerd Font Mono"), QStringLiteral("Noto Sans Mono")
    };
    const QStringList available = QFontDatabase::families();
    for (const QString &family : preferred) if (available.contains(family)) return family;
    return QFontDatabase::systemFont(QFontDatabase::FixedFont).family();
}

void installActivationServer(QLocalServer *server, AppController *controller) {
    QObject::connect(server, &QLocalServer::newConnection, server, [server, controller] {
        while (QLocalSocket *socket = server->nextPendingConnection()) {
            socket->setParent(server);
            if (!socket->waitForReadyRead(1000)) {
                socket->deleteLater();
                continue;
            }
            const QString command = QString::fromUtf8(socket->readAll()).trimmed();
            if (command == QStringLiteral("show-main")) {
                if (controller) emit controller->requestMainWindow();
            } else if (command == QStringLiteral("show-hud")) {
                if (controller) controller->launchHud();
            }
            socket->deleteLater();
        }
    });
}
}  // namespace

int main(int argc, char **argv) {
    if (hasArgument(argc, argv, "--version")) {
        std::cout << "Devtool Control Center " << DEVTOOL_CC_VERSION << '\n';
        return 0;
    }
    QApplication app(argc, argv);

    QCoreApplication::setOrganizationName(
        QStringLiteral("io.github.mikeceead")
    );
    QCoreApplication::setApplicationName(
        QStringLiteral("Devtool Control Center")
    );
    QGuiApplication::setApplicationDisplayName(
        QStringLiteral("Devtool Control Center")
    );
    QGuiApplication::setDesktopFileName(
        QStringLiteral("io.github.mikeceead.DevtoolControlCenter")
    );

    const QIcon applicationIcon = QIcon::fromTheme(
        QStringLiteral("io.github.mikeceead.DevtoolControlCenter")
    );
    if (!applicationIcon.isNull())
        QGuiApplication::setWindowIcon(applicationIcon);

    app.setApplicationName(QStringLiteral("Devtool Control Center"));
    app.setOrganizationName(QStringLiteral("mikeceead"));
    app.setDesktopFileName(QStringLiteral("io.github.mikeceead.DevtoolControlCenter"));
    app.setWindowIcon(controlCenterIcon());
    app.setFont(QFontDatabase::systemFont(QFontDatabase::GeneralFont));

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
    GitRepositoryController gitRepository(&history);
    ArtifactInspector inspector;
    IpcClient ipc;
    AppController controller(&hud, &runner, &history, &ipc);

    inspector.setDevtoolExecutable(controller.devtoolExecutable());
    inspector.setRepositoryPath(controller.repository());
    inspector.setArtifactPath(controller.artifact());

    QObject::connect(&controller, &AppController::devtoolExecutableChanged, &inspector,
                     [&] { inspector.setDevtoolExecutable(controller.devtoolExecutable()); });
    QObject::connect(&controller, &AppController::repositoryChanged, &inspector,
                     [&] { inspector.setRepositoryPath(controller.repository()); });
    QObject::connect(&controller, &AppController::artifactChanged, &inspector,
                     [&] { inspector.setArtifactPath(controller.artifact()); });
    QObject::connect(&hud, &HudStateStore::activeRunsChanged, &repositories,
                     [&] { repositories.updateActiveRuns(hud.activeRuns()); });
    QObject::connect(&ipc, &IpcClient::snapshotReceived, &hud, &HudStateStore::ingestSnapshot);
    QObject::connect(&ipc, &IpcClient::runUpdated, &hud, &HudStateStore::ingestRun);
    QObject::connect(&ipc, &IpcClient::runRemoved, &hud, &HudStateStore::removeRun);
    QObject::connect(&ipc, &IpcClient::connectedChanged, &hud,
                     [&] { hud.setIpcConnected(ipc.connected()); });

    if (controller.devtoolAvailable()) {
        QProcess::startDetached(controller.devtoolExecutable(),
                                QStringList{QStringLiteral("ipc-server"), QStringLiteral("--state-dir"), hud.stateDirectory()});
    }
    QTimer::singleShot(150, &ipc, &IpcClient::start);
    if (exitWhenIdle) QTimer::singleShot(1200, &app, &QCoreApplication::quit);

    QSettings uiSettings(QStringLiteral("mikeceead"), QStringLiteral("DevtoolControlCenter"));
    std::unique_ptr<QQmlApplicationEngine> engine;
    QPointer<QWindow> mainWindow;
    QTimer unloadUiTimer;
    unloadUiTimer.setSingleShot(true);
    unloadUiTimer.setInterval(7000);

    auto saveUiState = [&] {
        if (!mainWindow) return;
        uiSettings.setValue(QStringLiteral("window/x"), mainWindow->x());
        uiSettings.setValue(QStringLiteral("window/y"), mainWindow->y());
        uiSettings.setValue(QStringLiteral("window/width"), mainWindow->width());
        uiSettings.setValue(QStringLiteral("window/height"), mainWindow->height());
        uiSettings.setValue(QStringLiteral("window/page"), mainWindow->property("pageIndex"));
        uiSettings.sync();
    };

    auto loadUi = [&]() -> bool {
        if (engine && mainWindow) return true;

        auto nextEngine = std::make_unique<QQmlApplicationEngine>();
        nextEngine->rootContext()->setContextProperty(QStringLiteral("appController"), &controller);
        nextEngine->rootContext()->setContextProperty(QStringLiteral("hudStore"), &hud);
        nextEngine->rootContext()->setContextProperty(QStringLiteral("processRunner"), &runner);
        nextEngine->rootContext()->setContextProperty(QStringLiteral("historyModel"), &history);
        nextEngine->rootContext()->setContextProperty(QStringLiteral("artifactInspector"), &inspector);
        nextEngine->rootContext()->setContextProperty(QStringLiteral("repositoryModel"), &repositories);
        nextEngine->rootContext()->setContextProperty(QStringLiteral("gitRepository"), &gitRepository);
        nextEngine->rootContext()->setContextProperty(QStringLiteral("uiMonospaceFont"), preferredNerdFont());
        nextEngine->rootContext()->setContextProperty(QStringLiteral("ipcClient"), &ipc);
        nextEngine->load(QUrl(QStringLiteral("qrc:/qt/qml/Devtool/ControlCenter/qml/Main.qml")));
        if (nextEngine->rootObjects().isEmpty()) return false;

        auto *window = qobject_cast<QWindow *>(nextEngine->rootObjects().first());
        if (!window) return false;
        mainWindow = window;
        engine = std::move(nextEngine);

        const int savedWidth = uiSettings.value(QStringLiteral("window/width"), 1260).toInt();
        const int savedHeight = uiSettings.value(QStringLiteral("window/height"), 800).toInt();
        window->resize(qMax(940, savedWidth), qMax(620, savedHeight));
        if (uiSettings.contains(QStringLiteral("window/x")) && uiSettings.contains(QStringLiteral("window/y")))
            window->setPosition(uiSettings.value(QStringLiteral("window/x")).toInt(), uiSettings.value(QStringLiteral("window/y")).toInt());
        window->setProperty("pageIndex", uiSettings.value(QStringLiteral("window/page"), 0));

        if (auto *quickWindow = qobject_cast<QQuickWindow *>(window)) {
            quickWindow->setPersistentSceneGraph(false);
            quickWindow->setPersistentGraphics(false);
            QObject::connect(quickWindow, &QWindow::visibilityChanged, quickWindow,
                             [quickWindow](QWindow::Visibility visibility) {
                if (visibility == QWindow::Hidden || visibility == QWindow::Minimized)
                    QTimer::singleShot(0, quickWindow, [quickWindow] { quickWindow->releaseResources(); });
            });
        }
        controller.setStandby(false);
        return true;
    };

    QObject::connect(&unloadUiTimer, &QTimer::timeout, &app, [&] {
        if (!mainWindow || mainWindow->isVisible()) return;
        saveUiState();
        mainWindow.clear();
        engine.reset();
        // The tray, IPC client, run store and activation socket stay resident;
        // the expensive QML object tree and scene graph are recreated on demand.
    });

    QObject::connect(&controller, &AppController::standbyChanged, &app, [&](bool standby) {
        if (standby) unloadUiTimer.start();
        else unloadUiTimer.stop();
    });
    QObject::connect(&controller, &AppController::requestMainWindow, &app, [&] {
        unloadUiTimer.stop();
        if (!loadUi() || !mainWindow) return;
        mainWindow->show();
        mainWindow->raise();
        mainWindow->requestActivate();
    });

    if (!loadUi()) return 1;

    QLocalServer::removeServer(QString::fromLatin1(kMainServerName));
    auto *server = new QLocalServer(&app);
    if (server->listen(QString::fromLatin1(kMainServerName)))
        installActivationServer(server, &controller);

    return app.exec();
}
