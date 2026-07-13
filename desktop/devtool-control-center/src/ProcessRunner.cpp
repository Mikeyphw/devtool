#include "ProcessRunner.h"

#include <QProcessEnvironment>

namespace {
QString shellQuote(const QString &value) {
    if (value.isEmpty()) return QStringLiteral("''");
    QString escaped = value;
    escaped.replace(QLatin1Char('\''), QStringLiteral("'\"'\"'"));
    return QLatin1Char('\'') + escaped + QLatin1Char('\'');
}
}

ProcessRunner::ProcessRunner(QObject *parent) : QObject(parent) {
    m_process.setProcessChannelMode(QProcess::MergedChannels);
    m_flushTimer.setSingleShot(true);
    m_flushTimer.setInterval(75);

    connect(&m_flushTimer, &QTimer::timeout, this, &ProcessRunner::flushOutput);
    connect(&m_process, &QProcess::readyReadStandardOutput, this, &ProcessRunner::consume);
    connect(&m_process, &QProcess::stateChanged, this, [this] { emit runningChanged(); });
    connect(&m_process, &QProcess::errorOccurred, this, [this](QProcess::ProcessError) {
        appendPending(QStringLiteral("\nFailed to start or run process: ") + m_process.errorString() + QLatin1Char('\n'));
        flushOutput();
    });
    connect(&m_process, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this,
            [this](int code, QProcess::ExitStatus) {
                consume();
                flushOutput();
                m_exitCode = code;
                emit finished(code);
            });
}

void ProcessRunner::start(const QString &program, const QStringList &arguments,
                          const QString &cwd, const QVariantMap &environment) {
    if (running()) return;
    clear();
    m_commandLine = program;
    for (const auto &argument : arguments) m_commandLine += QLatin1Char(' ') + shellQuote(argument);
    emit commandLineChanged();

    auto processEnvironment = QProcessEnvironment::systemEnvironment();
    for (auto it = environment.begin(); it != environment.end(); ++it)
        processEnvironment.insert(it.key(), it.value().toString());
    m_process.setProcessEnvironment(processEnvironment);
    if (!cwd.isEmpty()) m_process.setWorkingDirectory(cwd);
    m_process.start(program, arguments);
}

void ProcessRunner::appendPending(const QString &text) {
    if (text.isEmpty()) return;
    m_pendingOutput += text;

    const QString combined = m_partialLine + text;
    QStringList lines = combined.split(QLatin1Char('\n'));
    if (!combined.endsWith(QLatin1Char('\n'))) m_partialLine = lines.takeLast();
    else m_partialLine.clear();

    if (!lines.isEmpty()) {
        emit linesAdded(lines);
        m_visibleLines.append(lines);
        constexpr qsizetype kMaximumVisibleLines = 2000;
        if (m_visibleLines.size() > kMaximumVisibleLines) {
            const qsizetype removeCount = m_visibleLines.size() - kMaximumVisibleLines;
            m_visibleLines.erase(m_visibleLines.begin(), m_visibleLines.begin() + removeCount);
        }
    }

    if (!m_flushTimer.isActive()) m_flushTimer.start();
}

void ProcessRunner::consume() {
    appendPending(QString::fromUtf8(m_process.readAllStandardOutput()));
}

void ProcessRunner::flushOutput() {
    if (m_pendingOutput.isEmpty() && m_partialLine.isEmpty()) return;
    m_pendingOutput.clear();

    // Devtool already streams the complete output to its log file. QML only
    // receives a bounded tail, coalesced at most every 75 ms, so verbose .NET
    // output cannot cause repeated multi-megabyte QString copies.
    m_output = m_visibleLines.join(QLatin1Char('\n'));
    if (!m_partialLine.isEmpty()) {
        if (!m_output.isEmpty()) m_output += QLatin1Char('\n');
        m_output += m_partialLine;
    }
    constexpr qsizetype kMaximumVisibleCharacters = 120000;
    if (m_output.size() > kMaximumVisibleCharacters)
        m_output = m_output.right(kMaximumVisibleCharacters);
    emit outputChanged();
}

void ProcessRunner::cancel() {
    if (!running()) return;
    m_process.terminate();
    QTimer::singleShot(2500, &m_process, [this] {
        if (running()) m_process.kill();
    });
}

void ProcessRunner::clear() {
    m_flushTimer.stop();
    m_output.clear();
    m_pendingOutput.clear();
    m_partialLine.clear();
    m_visibleLines.clear();
    m_commandLine.clear();
    m_exitCode = -1;
    emit outputChanged();
    emit commandLineChanged();
}
