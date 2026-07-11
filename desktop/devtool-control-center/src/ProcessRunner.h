#pragma once
#include <QObject>
#include <QProcess>
#include <QVariantMap>

class ProcessRunner final : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool running READ running NOTIFY runningChanged)
    Q_PROPERTY(QString output READ output NOTIFY outputChanged)
    Q_PROPERTY(QString commandLine READ commandLine NOTIFY commandLineChanged)
    Q_PROPERTY(int exitCode READ exitCode NOTIFY finished)
public:
    explicit ProcessRunner(QObject *parent=nullptr);
    bool running() const { return m_process.state()!=QProcess::NotRunning; }
    QString output() const { return m_output; }
    QString commandLine() const { return m_commandLine; }
    int exitCode() const { return m_exitCode; }
    Q_INVOKABLE void start(const QString &program,const QStringList &arguments,const QString &workingDirectory,const QVariantMap &environment);
    Q_INVOKABLE void cancel(); Q_INVOKABLE void clear();
signals:
    void runningChanged(); void outputChanged(); void commandLineChanged(); void finished(int exitCode);
private:
    void consume(); QProcess m_process; QString m_output,m_commandLine; int m_exitCode=-1;
};
