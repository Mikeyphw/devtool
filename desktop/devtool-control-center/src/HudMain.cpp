#include "HistoryModel.h"
#include "HudStateStore.h"
#include "IpcClient.h"

#include <QApplication>
#include <QCoreApplication>
#include <QFileInfo>
#include <QIcon>
#include <QIODevice>
#include <QLocalServer>
#include <QLocalSocket>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QProcess>
#include <QSettings>
#include <QStandardPaths>
#include <QTimer>
#include <QUrl>
#include <QWindow>

namespace {
constexpr const char *kHudServerName = "io.github.mikeceead.DevtoolControlCenter.hud";

QIcon hudIcon() {
    return QIcon(QStringLiteral(":/icons/devtool-control-center-tray.svg"));
}

QString resolveDevtoolExecutable() {
    QSettings settings(QStringLiteral("mikeceead"), QStringLiteral("DevtoolControlCenter"));
    const QString configured = settings.value(QStringLiteral("devtool"), QStringLiteral("devtool")).toString().trimmed();
    if (configured.isEmpty()) return QStringLiteral("devtool");
    const QFileInfo file(configured);
    if (file.isAbsolute() && file.isFile() && file.isExecutable()) return file.absoluteFilePath();
    const QString fromPath = QStandardPaths::findExecutable(configured);
    return fromPath.isEmpty() ? configured : fromPath;
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

void installActivationServer(QLocalServer *server, QWindow *window) {
    QObject::connect(server, &QLocalServer::newConnection, server, [server, window] {
        while (QLocalSocket *socket = server->nextPendingConnection()) {
            socket->setParent(server);
            if (!socket->waitForReadyRead(1000)) {
                socket->deleteLater();
                continue;
            }
            const QString command = QString::fromUtf8(socket->readAll()).trimmed();
            if (command == QStringLiteral("show-hud") && window) {
                window->show();
                window->raise();
                window->requestActivate();
            }
            socket->deleteLater();
        }
    });
}
}  // namespace

int main(int argc, char **argv) {
    QApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("Devtool Control Center HUD"));
    app.setOrganizationName(QStringLiteral("mikeceead"));
    app.setDesktopFileName(QStringLiteral("io.github.mikeceead.DevtoolControlCenter"));
    app.setWindowIcon(hudIcon());
    app.setQuitOnLastWindowClosed(true);

    if (sendMessage(QString::fromLatin1(kHudServerName), QStringLiteral("show-hud"))) return 0;

    HistoryModel history;
    HudStateStore hud(&history);
    IpcClient ipc;
    QObject::connect(&ipc, &IpcClient::snapshotReceived, &hud, &HudStateStore::ingestSnapshot);
    QObject::connect(&ipc, &IpcClient::runUpdated, &hud, &HudStateStore::ingestRun);
    QObject::connect(&ipc, &IpcClient::runRemoved, &hud, &HudStateStore::removeRun);
    QObject::connect(&ipc, &IpcClient::connectedChanged, &hud,
                     [&] { hud.setIpcConnected(ipc.connected()); });

    const QString devtool = resolveDevtoolExecutable();
    if (!devtool.trimmed().isEmpty()) {
        QProcess::startDetached(devtool,
                                QStringList{QStringLiteral("ipc-server"), QStringLiteral("--state-dir"), hud.stateDirectory()});
    }
    QTimer::singleShot(150, &ipc, &IpcClient::start);

    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty(QStringLiteral("hudStore"), &hud);
    engine.load(QUrl(QStringLiteral("qrc:/qt/qml/Devtool/ControlCenter/Hud/qml/CompactHud.qml")));
    if (engine.rootObjects().isEmpty()) return 1;

    auto *window = qobject_cast<QWindow *>(engine.rootObjects().first());
    QLocalServer::removeServer(QString::fromLatin1(kHudServerName));
    auto *server = new QLocalServer(&app);
    if (server->listen(QString::fromLatin1(kHudServerName))) {
        installActivationServer(server, window);
    }

    return app.exec();
}
