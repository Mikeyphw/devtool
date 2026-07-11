#pragma once

#include <QObject>
#include <QProcess>
#include <QVariantList>
#include <QVariantMap>

class ArtifactInspector final : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString artifactPath READ artifactPath WRITE setArtifactPath NOTIFY artifactPathChanged)
    Q_PROPERTY(QString repositoryPath READ repositoryPath WRITE setRepositoryPath NOTIFY repositoryPathChanged)
    Q_PROPERTY(QString devtoolExecutable READ devtoolExecutable WRITE setDevtoolExecutable NOTIFY devtoolExecutableChanged)
    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    Q_PROPERTY(QString error READ error NOTIFY errorChanged)
    Q_PROPERTY(QVariantMap summary READ summary NOTIFY inspectionChanged)
    Q_PROPERTY(QVariantList inventory READ inventory NOTIFY inspectionChanged)
    Q_PROPERTY(QVariantList overlays READ overlays NOTIFY inspectionChanged)
    Q_PROPERTY(QString diffText READ diffText NOTIFY inspectionChanged)
    Q_PROPERTY(QString manifestText READ manifestText NOTIFY inspectionChanged)
public:
    explicit ArtifactInspector(QObject *parent = nullptr);
    QString artifactPath() const { return m_artifactPath; }
    QString repositoryPath() const { return m_repositoryPath; }
    QString devtoolExecutable() const { return m_devtoolExecutable; }
    bool loading() const { return m_loading; }
    QString error() const { return m_error; }
    QVariantMap summary() const { return m_summary; }
    QVariantList inventory() const { return m_inventory; }
    QVariantList overlays() const { return m_overlays; }
    QString diffText() const { return m_diffText; }
    QString manifestText() const { return m_manifestText; }
    void setArtifactPath(const QString &value);
    void setRepositoryPath(const QString &value);
    void setDevtoolExecutable(const QString &value);
    Q_INVOKABLE void inspect(bool includeDiff = true);
    Q_INVOKABLE void clear();
signals:
    void artifactPathChanged();
    void repositoryPathChanged();
    void devtoolExecutableChanged();
    void loadingChanged();
    void errorChanged();
    void inspectionChanged();
private:
    void setLoading(bool value);
    void setError(const QString &value);
    QProcess m_process;
    QString m_artifactPath;
    QString m_repositoryPath;
    QString m_devtoolExecutable = QStringLiteral("devtool");
    bool m_loading = false;
    QString m_error;
    QVariantMap m_summary;
    QVariantList m_inventory;
    QVariantList m_overlays;
    QString m_diffText;
    QString m_manifestText;
};
