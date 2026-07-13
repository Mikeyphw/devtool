#include "IpcClient.h"

#include <QDir>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QStandardPaths>

QString IpcClient::defaultSocketPath() {
    const QString overridePath = qEnvironmentVariable("DEVTOOL_IPC_SOCKET");
    if (!overridePath.isEmpty()) return QDir::cleanPath(overridePath);
    const QString runtime = qEnvironmentVariable("XDG_RUNTIME_DIR");
    if (!runtime.isEmpty()) return QDir(runtime).filePath("devtool/control-center.sock");
    const QString state = qEnvironmentVariable("XDG_STATE_HOME");
    const QString root = state.isEmpty() ? QDir::homePath()+"/.local/state" : state;
    return QDir(root).filePath("devtool/control-center.sock");
}

IpcClient::IpcClient(QObject *parent):QObject(parent),m_socketPath(defaultSocketPath()) {
    m_reconnectTimer.setSingleShot(true);
    m_reconnectTimer.setInterval(1000);
    connect(&m_reconnectTimer,&QTimer::timeout,this,&IpcClient::start);
    connect(&m_socket,&QLocalSocket::connected,this,[this]{
        m_reconnectAttempt = 0;
        m_reconnectTimer.stop();
        emit connectedChanged();
        QVariantMap hello; hello["type"]="subscribe"; hello["client"]="devtool-control-center"; hello["protocol_version"]=1; send(hello);
    });
    connect(&m_socket,&QLocalSocket::disconnected,this,[this]{ emit connectedChanged(); scheduleReconnect(); });
    connect(&m_socket,&QLocalSocket::readyRead,this,&IpcClient::consume);
    connect(&m_socket,&QLocalSocket::errorOccurred,this,[this](QLocalSocket::LocalSocketError error){
        if(error!=QLocalSocket::ServerNotFoundError && error!=QLocalSocket::ConnectionRefusedError)
            emit protocolError(m_socket.errorString());
        scheduleReconnect();
    });
}

void IpcClient::setSocketPath(const QString &path) {
    const QString clean=QDir::cleanPath(path);
    if(clean.isEmpty()||clean==m_socketPath)return;
    m_socketPath=clean; emit socketPathChanged(); reconnect();
}

void IpcClient::start() {
    if(connected() || m_socket.state()==QLocalSocket::ConnectingState)return;
    m_socket.abort();
    m_socket.connectToServer(m_socketPath,QIODevice::ReadWrite);
}

void IpcClient::reconnect() {
    m_reconnectTimer.stop();
    m_reconnectAttempt = 0;
    m_socket.abort();
    emit connectedChanged();
    start();
}

void IpcClient::scheduleReconnect() {
    if (m_reconnectTimer.isActive()) return;
    static const int delays[] = {1000, 2000, 5000, 10000, 30000};
    constexpr int lastIndex = 4;
    const int index = qMin(m_reconnectAttempt, lastIndex);
    m_reconnectTimer.start(delays[index]);
    if (m_reconnectAttempt < lastIndex) ++m_reconnectAttempt;
}

void IpcClient::send(const QVariantMap &message) {
    if(!connected())return;
    const QByteArray bytes=QJsonDocument(QJsonObject::fromVariantMap(message)).toJson(QJsonDocument::Compact)+"\n";
    m_socket.write(bytes); m_socket.flush();
}

void IpcClient::cancelRun(const QString &runId) {
    if(runId.trimmed().isEmpty())return;
    QVariantMap message; message["type"]="cancel"; message["run_id"]=runId.trimmed(); message["reason"]="Cancelled from Devtool Control Center"; send(message);
}

void IpcClient::consume() {
    m_buffer += m_socket.readAll();
    qsizetype newline=-1;
    while((newline=m_buffer.indexOf('\n'))>=0){
        const QByteArray line=m_buffer.left(newline).trimmed(); m_buffer.remove(0,newline+1);
        if(line.isEmpty())continue;
        QJsonParseError error{}; const QJsonDocument doc=QJsonDocument::fromJson(line,&error);
        if(error.error!=QJsonParseError::NoError||!doc.isObject()){emit protocolError(error.errorString());continue;}
        const QJsonObject object=doc.object(); const QString type=object.value("type").toString();
        if(type=="snapshot")emit snapshotReceived(object.value("runs").toArray().toVariantList());
        else if(type=="run_update")emit runUpdated(object.value("run").toObject().toVariantMap());
        else if(type=="run_removed")emit runRemoved(object.value("run_id").toString());
        else if(type=="cancel_ack")emit cancelAcknowledged(object.value("run_id").toString(),object.value("accepted").toBool());
        else if(type=="cancel_requested")emit cancelRequested(object.value("run_id").toString());
        else if(type=="error")emit protocolError(object.value("message").toString());
    }
}
