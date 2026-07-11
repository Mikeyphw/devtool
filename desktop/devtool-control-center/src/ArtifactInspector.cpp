#include "ArtifactInspector.h"

#include <QJsonDocument>
#include <QJsonArray>
#include <QJsonObject>

ArtifactInspector::ArtifactInspector(QObject *parent) : QObject(parent) {
    m_process.setProcessChannelMode(QProcess::SeparateChannels);
    connect(&m_process, &QProcess::errorOccurred, this, [this](QProcess::ProcessError) {
        setLoading(false);
        setError(m_process.errorString());
    });
    connect(&m_process, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this,
            [this](int, QProcess::ExitStatus) {
        const QByteArray output = m_process.readAllStandardOutput();
        const QByteArray errors = m_process.readAllStandardError();
        QJsonParseError parseError{};
        const auto document = QJsonDocument::fromJson(output, &parseError);
        if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
            setError(QString::fromUtf8(errors.isEmpty() ? output : errors).trimmed().isEmpty()
                         ? parseError.errorString()
                         : QString::fromUtf8(errors.isEmpty() ? output : errors).trimmed());
            setLoading(false);
            return;
        }
        const auto object = document.object();
        if (object.contains("error")) {
            setError(object.value("error").toString());
            setLoading(false);
            return;
        }
        m_summary = object.toVariantMap();
        m_inventory = object.value("inventory").toArray().toVariantList();
        m_overlays = object.value("overlays").toArray().toVariantList();
        m_diffText = object.value("diff").toString();
        m_manifestText = object.value("manifest_json").toString();
        setError({});
        setLoading(false);
        emit inspectionChanged();
    });
}

void ArtifactInspector::setArtifactPath(const QString &value) {
    if (value == m_artifactPath) return;
    m_artifactPath = value;
    emit artifactPathChanged();
}
void ArtifactInspector::setRepositoryPath(const QString &value) {
    if (value == m_repositoryPath) return;
    m_repositoryPath = value;
    emit repositoryPathChanged();
}
void ArtifactInspector::setDevtoolExecutable(const QString &value) {
    if (value.trimmed().isEmpty() || value == m_devtoolExecutable) return;
    m_devtoolExecutable = value;
    emit devtoolExecutableChanged();
}
void ArtifactInspector::setLoading(bool value) {
    if (value == m_loading) return;
    m_loading = value;
    emit loadingChanged();
}
void ArtifactInspector::setError(const QString &value) {
    if (value == m_error) return;
    m_error = value;
    emit errorChanged();
}
void ArtifactInspector::clear() {
    m_summary.clear(); m_inventory.clear(); m_overlays.clear(); m_diffText.clear(); m_manifestText.clear();
    setError({}); emit inspectionChanged();
}
void ArtifactInspector::inspect(bool includeDiff) {
    if (m_loading || m_artifactPath.trimmed().isEmpty()) return;
    clear();
    QStringList arguments;
    if (!m_repositoryPath.trimmed().isEmpty()) arguments << "-r" << m_repositoryPath.trimmed();
    arguments << "inspect-artifact" << m_artifactPath.trimmed();
    if (includeDiff && !m_repositoryPath.trimmed().isEmpty()) arguments << "--diff";
    setLoading(true);
    m_process.start(m_devtoolExecutable, arguments);
}
