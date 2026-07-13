#pragma once

#include <QObject>
#include <QLocalSocket>
#include <QTimer>
#include <QVariantList>
#include <QVariantMap>

class IpcClient final : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool connected READ connected NOTIFY connectedChanged)
    Q_PROPERTY(QString socketPath READ socketPath WRITE setSocketPath NOTIFY socketPathChanged)
public:
    explicit IpcClient(QObject *parent=nullptr);
    bool connected() const { return m_socket.state() == QLocalSocket::ConnectedState; }
    QString socketPath() const { return m_socketPath; }
    void setSocketPath(const QString &path);
    Q_INVOKABLE void start();
    Q_INVOKABLE void reconnect();
    Q_INVOKABLE void cancelRun(const QString &runId);
signals:
    void connectedChanged();
    void socketPathChanged();
    void snapshotReceived(const QVariantList &runs);
    void runUpdated(const QVariantMap &run);
    void runRemoved(const QString &runId);
    void cancelAcknowledged(const QString &runId, bool accepted);
    void cancelRequested(const QString &runId);
    void protocolError(const QString &message);
private:
    void send(const QVariantMap &message);
    void consume();
    void scheduleReconnect();
    static QString defaultSocketPath();
    QLocalSocket m_socket;
    QTimer m_reconnectTimer;
    QByteArray m_buffer;
    QString m_socketPath;
    int m_reconnectAttempt = 0;
};
