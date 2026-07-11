#include "ProcessRunner.h"
#include <QProcessEnvironment>
#include <QTimer>

static QString shellQuote(const QString &value) {
    if (value.isEmpty()) return "''";
    QString escaped = value; escaped.replace("'", "'\"'\"'");
    return "'" + escaped + "'";
}
ProcessRunner::ProcessRunner(QObject *parent):QObject(parent){
    m_process.setProcessChannelMode(QProcess::MergedChannels);
    connect(&m_process,&QProcess::readyReadStandardOutput,this,&ProcessRunner::consume);
    connect(&m_process,&QProcess::stateChanged,this,[this]{emit runningChanged();});
    connect(&m_process,&QProcess::errorOccurred,this,[this](QProcess::ProcessError){m_output += "\nFailed to start or run process: "+m_process.errorString()+"\n"; emit outputChanged();});
    connect(&m_process,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,[this](int code,QProcess::ExitStatus){consume();m_exitCode=code;emit finished(code);});
}
void ProcessRunner::start(const QString &program,const QStringList &arguments,const QString &cwd,const QVariantMap &environment){
    if(running())return; clear(); m_commandLine=program; for(const auto &a:arguments)m_commandLine+=" "+shellQuote(a); emit commandLineChanged();
    auto env=QProcessEnvironment::systemEnvironment(); for(auto it=environment.begin();it!=environment.end();++it)env.insert(it.key(),it.value().toString());
    m_process.setProcessEnvironment(env); if(!cwd.isEmpty())m_process.setWorkingDirectory(cwd); m_process.start(program,arguments);
}
void ProcessRunner::consume(){const auto chunk=QString::fromUtf8(m_process.readAllStandardOutput());if(chunk.isEmpty())return;m_output+=chunk;if(m_output.size()>1000000)m_output=m_output.right(1000000);emit outputChanged();}
void ProcessRunner::cancel(){if(!running())return;m_process.terminate();QTimer::singleShot(2500,&m_process,[this]{if(running())m_process.kill();});}
void ProcessRunner::clear(){m_output.clear();m_exitCode=-1;emit outputChanged();}
