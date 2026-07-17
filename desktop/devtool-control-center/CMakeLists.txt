cmake_minimum_required(VERSION 3.21)
project(DevtoolControlCenter VERSION 0.4.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
include(GNUInstallDirs)
option(DEVTOOL_ENABLE_TESTS "Enable native smoke tests" ON)

find_package(Qt5 5.15 REQUIRED COMPONENTS Core Gui Qml Quick QuickControls2 Widgets Network)

# Compile the state/history/IPC implementation once. Both desktop binaries
# use these sources, and compiling them independently was one of the largest
# avoidable costs in incremental Control Center builds.
add_library(devtool-control-center-common STATIC
    src/HudStateStore.cpp
    src/HudStateStore.h
    src/HistoryModel.cpp
    src/HistoryModel.h
    src/IpcClient.cpp
    src/IpcClient.h
)
target_include_directories(devtool-control-center-common PUBLIC src)
target_link_libraries(devtool-control-center-common PRIVATE
    Qt5::Core Qt5::Gui Qt5::Network
)

qt5_add_resources(devtool_control_center_resources resources/devtool-control-center.qrc)

add_executable(devtool-control-center
    src/main.cpp
    src/AppController.cpp
    src/AppController.h
    src/ClipboardMonitor.cpp
    src/ClipboardMonitor.h
    src/ProcessRunner.cpp
    src/ProcessRunner.h
    src/ArtifactInspector.cpp
    src/ArtifactInspector.h
    src/RepositoryModel.cpp
    src/RepositoryModel.h
    src/GitRepositoryController.cpp
    src/GitRepositoryController.h
    ${devtool_control_center_resources}
)

add_executable(devtool-control-center-hud
    src/HudMain.cpp
    ${devtool_control_center_resources}
)

target_link_libraries(devtool-control-center PRIVATE
    devtool-control-center-common
    Qt5::Core Qt5::Gui Qt5::Qml Qt5::Quick Qt5::QuickControls2 Qt5::Widgets Qt5::Network
)

target_link_libraries(devtool-control-center-hud PRIVATE
    devtool-control-center-common
    Qt5::Core Qt5::Gui Qt5::Qml Qt5::Quick Qt5::QuickControls2 Qt5::Widgets Qt5::Network
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
install(FILES packaging/io.github.mikeceead.DevtoolControlCenter.svg
        DESTINATION ${CMAKE_INSTALL_DATADIR}/pixmaps)
install(FILES packaging/icons/64x64/io.github.mikeceead.DevtoolControlCenter.png
        DESTINATION ${CMAKE_INSTALL_DATADIR}/icons/hicolor/64x64/apps)
install(FILES packaging/icons/128x128/io.github.mikeceead.DevtoolControlCenter.png
        DESTINATION ${CMAKE_INSTALL_DATADIR}/icons/hicolor/128x128/apps)
install(FILES packaging/icons/256x256/io.github.mikeceead.DevtoolControlCenter.png
        DESTINATION ${CMAKE_INSTALL_DATADIR}/icons/hicolor/256x256/apps)

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
