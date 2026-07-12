cmake_minimum_required(VERSION 3.21)
project(DevtoolControlCenter VERSION 0.3.8 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 6.4 REQUIRED COMPONENTS Core Gui Qml Quick QuickControls2 Widgets Network)
qt_standard_project_setup(REQUIRES 6.4)

qt_add_executable(devtool-control-center
    src/main.cpp
    src/AppController.cpp
    src/AppController.h
    src/ClipboardMonitor.cpp
    src/ClipboardMonitor.h
    src/HudStateStore.cpp
    src/HudStateStore.h
    src/ProcessRunner.cpp
    src/ProcessRunner.h
    src/HistoryModel.cpp
    src/HistoryModel.h
    src/ArtifactInspector.cpp
    src/ArtifactInspector.h
    src/RepositoryModel.cpp
    src/RepositoryModel.h
    src/IpcClient.cpp
    src/IpcClient.h
)

qt_add_qml_module(devtool-control-center
    URI Devtool.ControlCenter
    RESOURCE_PREFIX /qt/qml
    VERSION 1.0
    QML_FILES
        qml/Main.qml
        qml/CompactHud.qml
        qml/components/Theme.qml
        qml/components/AppScrollView.qml
        qml/components/AppButton.qml
        qml/components/AppTextField.qml
        qml/components/AppTextArea.qml
        qml/components/AppComboBox.qml
        qml/components/AppCheckBox.qml
        qml/components/AppTabButton.qml
        qml/components/PageHeading.qml
        qml/components/SectionHeading.qml
        qml/components/SurfaceCard.qml
        qml/components/MetricCard.qml
        qml/components/PipelineView.qml
        qml/components/RunCard.qml
        qml/components/SidebarButton.qml
        qml/pages/OverviewPage.qml
        qml/pages/RunPage.qml
        qml/pages/HistoryPage.qml
        qml/pages/ArtifactsPage.qml
        qml/pages/DiagnosticsPage.qml
        qml/pages/RepositoriesPage.qml
        qml/pages/SettingsPage.qml
)

qt_add_resources(devtool-control-center devtool_icons
    PREFIX /icons
    FILES
        resources/devtool-control-center.svg
        resources/devtool-control-center-tray.svg
)

target_link_libraries(devtool-control-center PRIVATE
    Qt6::Core Qt6::Gui Qt6::Qml Qt6::Quick Qt6::QuickControls2 Qt6::Widgets Qt6::Network
)

target_compile_definitions(devtool-control-center PRIVATE
    DEVTOOL_CC_VERSION=\"${PROJECT_VERSION}\"
)

install(TARGETS devtool-control-center RUNTIME DESTINATION bin)
install(FILES packaging/io.github.mikeceead.DevtoolControlCenter.desktop
        DESTINATION share/applications)
install(FILES packaging/io.github.mikeceead.DevtoolControlCenter.svg
        DESTINATION share/icons/hicolor/scalable/apps)
