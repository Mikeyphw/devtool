#include "HistoryModel.h"
#include "HudStateStore.h"
#include "IpcClient.h"

#include <iostream>
#include <QApplication>
#include <QCoreApplication>
#include <QFileInfo>
#include <QFont>
#include <QFontDatabase>
#include <QGuiApplication>
#include <QIcon>
#include <QIODevice>
#include <QLocalServer>
#include <QLocalSocket>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QProcess>
#include <QScreen>
#include <QSettings>
#include <QSurfaceFormat>
#include <QStandardPaths>
#include <QTimer>
#include <QUrl>
#include <QtGlobal>
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

constexpr const char *kHudServerName = "io.github.mikeceead.DevtoolControlCenter.hud";
constexpr const char *kHudPositionKey = "hud/windowPosition";
constexpr int kHudScreenMargin = 24;
constexpr int kMinimumVisibleWidth = 96;
constexpr int kMinimumVisibleHeight = 64;

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

QSettings hudSettings() {
    return QSettings(QStringLiteral("mikeceead"), QStringLiteral("DevtoolControlCenter"));
}

QPoint defaultHudPosition(const QWindow *window) {
    QScreen *screen = window ? window->screen() : nullptr;
    if (!screen) screen = QGuiApplication::primaryScreen();
    if (!screen || !window) return {};

    const QRect available = screen->availableGeometry();
    return QPoint(available.right() - window->width() - kHudScreenMargin + 1,
                  available.top() + kHudScreenMargin);
}

bool hudPositionIsVisible(const QPoint &position, const QSize &size) {
    const QRect candidate(position, size);
    for (QScreen *screen : QGuiApplication::screens()) {
        const QRect visible = candidate.intersected(screen->availableGeometry());
        if (visible.width() >= kMinimumVisibleWidth && visible.height() >= kMinimumVisibleHeight)
            return true;
    }
    return false;
}

void ensureHudIsVisible(QWindow *window) {
    if (!window) return;
    if (!hudPositionIsVisible(window->position(), window->size()))
        window->setPosition(defaultHudPosition(window));
}

void restoreHudPosition(QWindow *window) {
    if (!window) return;
    QSettings settings = hudSettings();
    if (!settings.contains(QString::fromLatin1(kHudPositionKey))) {
        window->setPosition(defaultHudPosition(window));
        return;
    }

    const QPoint saved = settings.value(QString::fromLatin1(kHudPositionKey)).toPoint();
    window->setPosition(hudPositionIsVisible(saved, window->size())
                            ? saved
                            : defaultHudPosition(window));
}

void saveHudPosition(const QWindow *window) {
    if (!window) return;
    QSettings settings = hudSettings();
    settings.setValue(QString::fromLatin1(kHudPositionKey), window->position());
    settings.sync();
}

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
                ensureHudIsVisible(window);
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
    if (hasArgument(argc, argv, "--version")) {
        std::cout << "Devtool Control Center " << DEVTOOL_CC_VERSION << " (HUD)\n";
        return 0;
    }
    QSurfaceFormat format = QSurfaceFormat::defaultFormat();
    format.setAlphaBufferSize(8);
    QSurfaceFormat::setDefaultFormat(format);

    QApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("Devtool Control Center HUD"));
    app.setOrganizationName(QStringLiteral("mikeceead"));
    app.setDesktopFileName(QStringLiteral("io.github.mikeceead.DevtoolControlCenter"));
    app.setWindowIcon(hudIcon());
    QFont uiFont = QFontDatabase::systemFont(QFontDatabase::GeneralFont);
    const double basePointSize = uiFont.pointSizeF() > 0.0 ? uiFont.pointSizeF() : 11.0;
    const double typographyScale = qBound(1.0, hudSettings().value(QStringLiteral("appearance/typographyScale"), 1.0).toDouble(), 2.0);
    uiFont.setPointSizeF(basePointSize * typographyScale);
    app.setFont(uiFont);
    app.setQuitOnLastWindowClosed(true);
    const bool exitWhenIdle = app.arguments().contains(QStringLiteral("--exit-when-idle"));

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
    engine.rootContext()->setContextProperty(QStringLiteral("uiMonospaceFont"), preferredNerdFont());
    engine.load(QUrl(QStringLiteral("qrc:/qt/qml/Devtool/ControlCenter/Hud/qml/CompactHud.qml")));
    if (engine.rootObjects().isEmpty()) return 1;

    auto *window = qobject_cast<QWindow *>(engine.rootObjects().first());
    if (!window) return 1;

    bool restoringPosition = true;
    QTimer positionSaveTimer;
    positionSaveTimer.setSingleShot(true);
    positionSaveTimer.setInterval(250);
    QObject::connect(&positionSaveTimer, &QTimer::timeout, &app, [window, &restoringPosition] {
        if (!restoringPosition) saveHudPosition(window);
    });
    const auto schedulePositionSave = [&positionSaveTimer, &restoringPosition](int) {
        if (!restoringPosition) positionSaveTimer.start();
    };
    QObject::connect(window, &QWindow::xChanged, &app, schedulePositionSave);
    QObject::connect(window, &QWindow::yChanged, &app, schedulePositionSave);
    QObject::connect(&app, &QCoreApplication::aboutToQuit, &app, [window] {
        saveHudPosition(window);
    });
    QObject::connect(&app, &QGuiApplication::screenRemoved, &app, [window](QScreen *) {
        QTimer::singleShot(0, window, [window] { ensureHudIsVisible(window); });
    });
    QTimer::singleShot(0, window, [window, &restoringPosition] {
        restoreHudPosition(window);
        restoringPosition = false;
    });

    if (exitWhenIdle) QTimer::singleShot(1200, &app, &QCoreApplication::quit);

    QLocalServer::removeServer(QString::fromLatin1(kHudServerName));
    auto *server = new QLocalServer(&app);
    if (server->listen(QString::fromLatin1(kHudServerName))) {
        installActivationServer(server, window);
    }

    return app.exec();
}
