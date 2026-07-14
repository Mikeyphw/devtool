cmake_minimum_required(VERSION 3.21)
project(DevtoolControlCenter VERSION 0.4.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
include(GNUInstallDirs)
option(DEVTOOL_ENABLE_TESTS "Enable native smoke tests" ON)

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
    src/GitRepositoryController.cpp
    src/GitRepositoryController.h
    src/IpcClient.cpp
    src/IpcClient.h
)

qt_add_executable(devtool-control-center-hud
    src/HudMain.cpp
    src/HudStateStore.cpp
    src/HudStateStore.h
    src/HistoryModel.cpp
    src/HistoryModel.h
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
        qml/components/StatusMark.qml
        qml/pages/OverviewPage.qml
        qml/pages/RunPage.qml
        qml/pages/HistoryPage.qml
        qml/pages/ArtifactsPage.qml
        qml/pages/DiagnosticsPage.qml
        qml/pages/RepositoriesPage.qml
        qml/pages/SettingsPage.qml
    RESOURCES
        qml/icons/overview.svg
        qml/icons/run.svg
        qml/icons/artifacts.svg
        qml/icons/diagnostics.svg
        qml/icons/history.svg
        qml/icons/repositories.svg
        qml/icons/settings.svg
        qml/icons/artifact.svg
        qml/icons/arrow-down.svg
        qml/icons/copy.svg
        qml/icons/done.svg
        qml/icons/close.svg
        qml/icons/chevron-up.svg
        qml/icons/chevron-down.svg
        qml/icons/log.svg
        qml/icons/details.svg
)

qt_add_qml_module(devtool-control-center-hud
    URI Devtool.ControlCenter.Hud
    RESOURCE_PREFIX /qt/qml
    VERSION 1.0
    QML_FILES
        qml/CompactHud.qml
        qml/components/Theme.qml
        qml/components/AppButton.qml
        qml/components/PipelineView.qml
        qml/components/StatusMark.qml
    RESOURCES
        qml/icons/copy.svg
        qml/icons/done.svg
        qml/icons/close.svg
        qml/icons/chevron-up.svg
        qml/icons/chevron-down.svg
        qml/icons/log.svg
        qml/icons/details.svg
)

set_source_files_properties(resources/devtool-control-center.svg PROPERTIES
    QT_RESOURCE_ALIAS "devtool-control-center.svg"
)
set_source_files_properties(resources/devtool-control-center-tray.svg PROPERTIES
    QT_RESOURCE_ALIAS "devtool-control-center-tray.svg"
)

qt_add_resources(devtool-control-center devtool_icons
    PREFIX /icons
    FILES
        resources/devtool-control-center.svg
        resources/devtool-control-center-tray.svg
)

qt_add_resources(devtool-control-center-hud devtool_icons
    PREFIX /icons
    FILES
        resources/devtool-control-center.svg
        resources/devtool-control-center-tray.svg
)

target_link_libraries(devtool-control-center PRIVATE
    Qt6::Core Qt6::Gui Qt6::Qml Qt6::Quick Qt6::QuickControls2 Qt6::Widgets Qt6::Network
)

target_link_libraries(devtool-control-center-hud PRIVATE
    Qt6::Core Qt6::Gui Qt6::Qml Qt6::Quick Qt6::QuickControls2 Qt6::Widgets Qt6::Network
)

target_compile_definitions(devtool-control-center PRIVATE
    DEVTOOL_CC_VERSION=\"${PROJECT_VERSION}\"
)
target_compile_definitions(devtool-control-center-hud PRIVATE
    DEVTOOL_CC_VERSION=\"${PROJECT_VERSION}\"
)

install(TARGETS devtool-control-center RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(TARGETS devtool-control-center-hud RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(FILES packaging/io.github.mikeceead.DevtoolControlCenter.desktop
        DESTINATION ${CMAKE_INSTALL_DATADIR}/applications)
install(FILES packaging/io.github.mikeceead.DevtoolControlCenter.svg
        DESTINATION ${CMAKE_INSTALL_DATADIR}/icons/hicolor/scalable/apps)

if(DEVTOOL_ENABLE_TESTS)
    enable_testing()
    add_test(NAME control-center-version COMMAND devtool-control-center --version)
    add_test(NAME control-center-hud-version COMMAND devtool-control-center-hud --version)
    add_test(NAME control-center-qml-smoke
             COMMAND ${CMAKE_CURRENT_SOURCE_DIR}/tests/run-headless-smoke.sh
                     $<TARGET_FILE:devtool-control-center> --exit-when-idle)
    add_test(NAME control-center-hud-qml-smoke
             COMMAND ${CMAKE_CURRENT_SOURCE_DIR}/tests/run-headless-smoke.sh
                     $<TARGET_FILE:devtool-control-center-hud> --exit-when-idle)
    set_tests_properties(control-center-version control-center-hud-version
        PROPERTIES PASS_REGULAR_EXPRESSION "Devtool Control Center 0.4.0")
    set_tests_properties(control-center-qml-smoke control-center-hud-qml-smoke
        PROPERTIES TIMEOUT 20)
endif()
