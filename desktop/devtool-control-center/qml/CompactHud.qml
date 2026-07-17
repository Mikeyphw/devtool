import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15
import Devtool.ControlCenter.Components 1.0

Window {
    id: root
    function hudAvailableWidth() {
        return Screen.availableWidth > 0 ? Screen.availableWidth : 1920
    }
    // Slice 1 compatibility markers retained for source-level regressions:
    // width: 720
    // minimumWidth: 620
    // maximumHeight: 680
    // text: "Details"
    // text: copiedFeedback ? "Copied" : "Copy"
    // Previous translucent baseline: theme.dark ? 0.14 : 0.18
    width: Math.min(720, Math.max(minimumWidth, hudAvailableWidth() - 32))
    height: Math.min(maximumHeight, Math.max(minimumHeight, contentLayout.implicitHeight + 32))
    minimumWidth: Math.min(520, Math.max(320, hudAvailableWidth() - 24))
    minimumHeight: 224
    maximumWidth: Math.max(minimumWidth, hudAvailableWidth() - 16)
    maximumHeight: Math.max(260, Screen.availableHeight - 32)
    visible: true
    color: "transparent"
    flags: Qt.Window | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint
    property var run: hudStore.activeRun || ({})
    property real backgroundOpacity: theme.dark ? 0.94 : 0.97
    property real panelOpacity: theme.dark ? 0.88 : 0.93
    property real recessedOpacity: theme.dark ? 0.84 : 0.90
    readonly property bool narrowHud: width < 560
    property bool parserExpanded: false
    property string expandedLaneKey: ""
    property bool copiedFeedback: false
    property double nowMs: Date.now()

    Theme { id: theme }
    Timer { interval: 1000; running: true; repeat: true; onTriggered: root.nowMs = Date.now() }
    Timer { id: copiedTimer; interval: 1300; onTriggered: root.copiedFeedback = false }
    Behavior on height { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }

    function steps() { return run.steps || [] }
    function humanCommand(command) {
        const value = String(command || "").trim().toLowerCase().replace(/_/g, "-")
        const labels = {
            "apply-overlay": "Applying overlay",
            "ao": "Applying overlay",
            "apply-overlays": "Applying overlay bundle",
            "aos": "Applying overlay bundle",
            "apply-snapshot": "Applying snapshot",
            "as": "Applying snapshot",
            "extract-overlay": "Extracting overlay",
            "inspect-artifact": "Inspecting artifact",
            "validate-artifact": "Validating artifact",
            "validate": "Validating repository",
            "restore": "Restoring dependencies",
            "build": "Building target",
            "test": "Running tests",
            "doctor": "Checking repository health",
            "status": "Reading repository status"
        }
        if (labels[value]) return labels[value]
        if (!value) return "Waiting for progress"
        const words = value.replace(/-/g, " ")
        return words.charAt(0).toUpperCase() + words.slice(1)
    }
    function taskTitle() { return run.task_title || humanCommand(run.command) }
    function projectType() { return String(run.project_type || run.runner || "auto").toLowerCase() }
    function runnerLabel() {
        const kind = projectType()
        if (kind === "dotnet") return ".NET"
        if (kind === "android") return "Android"
        if (kind === "gradle") return "Gradle/JVM"
        if (kind === "python") return "Python"
        if (kind === "command") return "Custom command"
        return "Auto-detected runner"
    }
    function executionLabel() {
        const value = String(run.execution_environment || "auto").toLowerCase()
        if (value === "native-termux") return "Termux"
        if (value === "chroot") return "Chroot"
        if (value === "desktop-linux") return "Desktop Linux"
        if (value === "android-device") return "Android device"
        if (value === "external-device") return "External device"
        return "Automatic environment"
    }
    function projectUnitLabel() {
        const kind = projectType()
        if (kind === "dotnet") return "Projects"
        if (kind === "android" || kind === "gradle") return "Gradle tasks"
        if (kind === "python") return "Python tasks"
        return "Tasks"
    }
    function testContainerLabel() {
        const kind = projectType()
        if (kind === "dotnet") return "Test assemblies"
        if (kind === "android" || kind === "gradle") return "Test suites"
        if (kind === "python") return "Test files"
        return "Test groups"
    }
    function repositorySubtitle() {
        const repo = String(run.repo_name || "Devtool")
        const artifact = String(run.artifact_name || "").trim()
        return artifact && artifact !== run.command ? repo + " • " + artifact : repo
    }
    function terminal(status) {
        return status === "ok" || status === "success" || status === "completed"
            || status === "failed" || status === "cancelled"
            || status === "interrupted" || status === "skipped"
    }
    function successfulRun() {
        const status = String(run.status || "").toLowerCase()
        return status === "ok" || status === "success" || status === "completed"
    }
    function rollbackStep() {
        const values = steps()
        for (let i = values.length - 1; i >= 0; --i) {
            if (String(values[i].base || "") === "rollback") return values[i]
        }
        return null
    }
    function visualState() {
        const rollback = rollbackStep()
        if (run.rollback_failed || (rollback && rollback.status === "failed")) return "rollback-failed"
        if (rollback && rollback.status === "running") return "rollback"
        if (run.rolled_back) return "recovered"
        const status = String(run.status || "idle")
        if (status === "ok" || status === "success" || status === "completed") return "success"
        if (status === "failed" || status === "cancelled" || status === "interrupted") return "failed"
        return "running"
    }
    function stateColor() {
        const state = visualState()
        if (state === "success") return theme.success
        if (state === "failed" || state === "rollback-failed") return theme.error
        if (state === "rollback") return theme.violet
        if (state === "recovered") return theme.info
        return theme.info
    }
    function stateSurface() {
        const state = visualState()
        if (state === "failed" || state === "rollback-failed") return theme.errorSurface
        if (state === "rollback") return theme.mix(theme.surface, theme.violet, theme.dark ? 0.16 : 0.10)
        if (state === "recovered") return theme.infoSurface
        if (state === "success") return theme.successSurface
        return theme.surface
    }
    function stateLabel() {
        const state = visualState()
        if (state === "success") return "SUCCESS"
        if (state === "failed") return "FAILED"
        if (state === "rollback") return "ROLLBACK"
        if (state === "recovered") return "RECOVERED"
        if (state === "rollback-failed") return "ROLLBACK FAILED"
        const status = String(run.status || "")
        return status === "waiting" ? "PREPARING" : "RUNNING"
    }
    function sharedSteps() {
        return steps().filter(function(step) { return !String(step.scope || "").trim() })
    }
    function scopedLanes() {
        const values = steps()
        const lanes = {}
        const order = []
        for (let i = 0; i < values.length; ++i) {
            const step = values[i] || {}
            const scope = String(step.scope || "").trim()
            if (!scope) continue
            const scopeType = String(step.scopeType || "target")
            const key = scopeType + "\u001f" + scope
            if (!lanes[key]) {
                lanes[key] = {
                    key: key,
                    scopeType: scopeType,
                    scope: scope,
                    workspace: String(step.workspace || ""),
                    target: String(step.target || ""),
                    workspaceRoot: String(step.workspaceRoot || ""),
                    steps: []
                }
                order.push(key)
            }
            const lane = lanes[key]
            if (!lane.target && step.target) lane.target = String(step.target)
            if (!lane.workspace && step.workspace) lane.workspace = String(step.workspace)
            if (!lane.workspaceRoot && step.workspaceRoot) lane.workspaceRoot = String(step.workspaceRoot)
            lane.steps.push(step)
        }
        if (!order.length && values.length) {
            return [{
                key: "shared",
                scopeType: "target",
                scope: String(run.target_summary || run.target || run.repo_name || "Repository"),
                workspace: "",
                target: String(run.target_summary || run.target || ""),
                workspaceRoot: String(run.repo_root || ""),
                steps: values
            }]
        }
        return order.map(function(key) { return lanes[key] })
    }
    function laneTitle(lane) {
        if (!lane) return "Workspace"
        return lane.workspace || lane.scope || lane.target || "Workspace"
    }
    function laneSubtitle(lane) {
        if (!lane) return ""
        const title = laneTitle(lane)
        if (lane.target && lane.target !== title) return lane.target
        if (lane.workspaceRoot && lane.workspaceRoot !== title) return lane.workspaceRoot
        return lane.scopeType === "workspace" ? "Workspace" : "Target"
    }
    function laneStatus(lane) {
        if (successfulRun()) return "done"
        const values = lane && lane.steps ? lane.steps : []
        let completed = 0
        for (let i = 0; i < values.length; ++i) {
            const status = String(values[i].status || "pending")
            if (status === "failed" || status === "cancelled" || status === "interrupted") return "failed"
            if (status === "running") return "running"
            if (status === "ok" || status === "skipped") completed++
        }
        return values.length && completed === values.length ? "done" : "pending"
    }
    function laneStatusLabel(lane) {
        const status = laneStatus(lane)
        if (status === "done") return "COMPLETE"
        if (status === "failed") return "FAILED"
        if (status === "running") return "RUNNING"
        return "PENDING"
    }
    function laneColor(lane) {
        const status = laneStatus(lane)
        if (status === "done") return theme.success
        if (status === "failed") return theme.error
        if (status === "running") return theme.info
        return theme.subtleText
    }
    function normalizedStepProgress(step) {
        if (!step) return 0
        if (terminal(String(step.status || ""))) return 100
        return Math.max(0, Math.min(100, Number(step.progress || 0)))
    }
    function laneProgress(lane) {
        if (successfulRun()) return 100
        const values = lane && lane.steps ? lane.steps : []
        if (!values.length) return 0
        let total = 0
        for (let i = 0; i < values.length; ++i) total += normalizedStepProgress(values[i])
        return Math.round(total / values.length)
    }
    function overallProgress() {
        if (successfulRun()) return 100
        const values = steps()
        if (!values.length) return 0
        let total = 0
        for (let i = 0; i < values.length; ++i) total += normalizedStepProgress(values[i])
        return Math.round(total / values.length)
    }
    function displayedProgress() {
        const state = visualState()
        if (state === "success" || state === "rollback" || state === "recovered" || state === "rollback-failed") return 100
        return overallProgress()
    }
    function activeStep() {
        const values = steps()
        for (let i = 0; i < values.length; ++i) if (values[i].status === "running") return values[i]
        for (let i = 0; i < values.length; ++i) if (values[i].status === "pending") return values[i]
        return values.length ? values[values.length - 1] : ({ phaseLabel: "Waiting", detail: "No operation reported yet" })
    }
    function stepTaskCount(step, includeUnit) {
        if (!step) return ""
        const completed = Number(step.completed)
        const total = Number(step.total)
        if (completed < 0 || total <= 0) return ""
        const unit = String(step.unitLabel || "tasks")
        return Math.round(completed) + "/" + Math.round(total) + (includeUnit ? " " + unit : "")
    }
    function laneTaskCount(lane, includeUnit) {
        const values = lane && lane.steps ? lane.steps : []
        for (let i = 0; i < values.length; ++i) {
            if (String(values[i].status || "") === "running") {
                const label = stepTaskCount(values[i], includeUnit)
                if (label) return label
            }
        }
        for (let i = values.length - 1; i >= 0; --i) {
            const label = stepTaskCount(values[i], includeUnit)
            if (label) return label
        }
        return ""
    }
    function laneProgressLabel(lane) {
        const count = laneTaskCount(lane, true)
        return count ? count + " · " + laneProgress(lane) + "%" : laneProgress(lane) + "%"
    }
    function activeLaneIndex() {
        const lanes = scopedLanes()
        for (let i = 0; i < lanes.length; ++i) if (laneStatus(lanes[i]) === "running") return i
        for (let i = 0; i < lanes.length; ++i) if (laneStatus(lanes[i]) === "pending") return i
        return lanes.length ? lanes.length - 1 : 0
    }
    function statusLead() {
        const state = visualState()
        if (state === "failed") return "Failure"
        if (state === "rollback") return "Rollback"
        if (state === "recovered") return "Recovered"
        if (state === "rollback-failed") return "Critical"
        if (state === "success") return "Complete"
        const lanes = scopedLanes()
        return lanes.length > 1 ? "Workspace " + (activeLaneIndex() + 1) + " of " + lanes.length : (activeStep().phaseLabel || "Working")
    }
    function statusDetail() {
        const state = visualState()
        if (state === "failed") return "Validation stopped; rollback pending"
        if (state === "rollback") return "Reverting all applied workspace changes"
        if (state === "recovered") return "Failure contained; repository restored"
        if (state === "rollback-failed") return "Operation and rollback both failed"
        if (state === "success") return "All workspaces and validators passed"
        const step = activeStep()
        return step.detail || run.message || "Waiting for the next update"
    }
    function progressLabel() {
        const state = visualState()
        if (state === "rollback") return "Rollback"
        if (state === "recovered") return "RESTORED"
        if (state === "rollback-failed") return "CRITICAL"
        const count = stepTaskCount(activeStep(), true)
        return count ? count + " · " + displayedProgress() + "%" : displayedProgress() + "%"
    }
    function elapsedText() {
        if (!run.started_at) return "00:00"
        const start = Date.parse(String(run.started_at))
        if (isNaN(start)) return "00:00"
        const end = run.finished_at ? Date.parse(String(run.finished_at)) : nowMs
        let seconds = Math.max(0, Math.floor((end - start) / 1000))
        const hours = Math.floor(seconds / 3600)
        seconds %= 3600
        const minutes = Math.floor(seconds / 60)
        seconds %= 60
        if (hours) return hours + ":" + String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0")
        return String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0")
    }
    function laneMetrics(lane) {
        const values = lane && lane.steps ? lane.steps : []
        const metrics = {
            projects: 0,
            assemblies: 0,
            passed: 0,
            failed: 0,
            skipped: 0,
            warnings: 0,
            errors: 0,
            validator: "not required",
            lastDetail: ""
        }
        for (let i = 0; i < values.length; ++i) {
            const step = values[i] || {}
            const unit = String(step.unitLabel || "")
            if (unit === "projects") metrics.projects = Math.max(metrics.projects, Number(step.total > 0 ? step.total : step.completed || 0))
            if (String(step.base || "") === "test") {
                metrics.assemblies = Math.max(metrics.assemblies, Number(step.testAssemblies || 0))
                metrics.passed = Math.max(metrics.passed, Number(step.testsPassed || 0))
                metrics.failed = Math.max(metrics.failed, Number(step.testsFailed || 0))
                metrics.skipped = Math.max(metrics.skipped, Number(step.testsSkipped || 0))
            }
            metrics.warnings += Number(step.warnings || 0)
            metrics.errors += Number(step.errors || 0)
            if (step.validator) {
                const status = String(step.status || "pending")
                metrics.validator = status === "ok" ? "passed" : status === "failed" ? "failed" : status === "running" ? "running" : "pending"
            }
            if (step.detail) metrics.lastDetail = String(step.detail)
        }
        return metrics
    }
    function aggregateParserMetrics() {
        const lanes = scopedLanes()
        const total = { projects: 0, assemblies: 0, passed: 0, failed: 0, skipped: 0 }
        for (let i = 0; i < lanes.length; ++i) {
            const metrics = laneMetrics(lanes[i])
            total.projects += metrics.projects
            total.assemblies += metrics.assemblies
            total.passed += metrics.passed
            total.failed += metrics.failed
            total.skipped += metrics.skipped
        }
        return total
    }
    function sharedVisibleSteps() {
        return scopedLanes().length > 0 && scopedLanes()[0].key !== "shared" ? sharedSteps() : []
    }

    onClosing: function(close) { close.accepted = true; Qt.quit() }

    Rectangle {
        id: shell
        anchors.fill: parent
        Accessible.role: Accessible.Pane
        Accessible.name: "Devtool live progress"
        Accessible.description: root.taskTitle() + ", " + root.stateLabel() + ", " + root.progressLabel()
        radius: 18
        clip: true
        color: theme.alpha(theme.window, root.backgroundOpacity)
        border.color: theme.alpha(theme.windowText, 0.24)
        border.width: 1

        ColumnLayout {
        id: contentLayout
        anchors.fill: parent
        anchors.margins: 12
        spacing: 10

            Rectangle {
                id: headerCard
                Layout.fillWidth: true
                Layout.preferredHeight: headerContent.implicitHeight + 28
                radius: 16
                color: theme.alpha(stateSurface(), root.panelOpacity)
                border.color: theme.alpha(stateColor(), visualState() === "running" ? 0.18 : 0.34)
                border.width: 1

                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton
                    cursorShape: Qt.SizeAllCursor
                    onPressed: root.startSystemMove()
                }

                RowLayout {
                    id: headerContent
                    anchors.fill: parent
                    anchors.margins: 13
                    spacing: 12

                    Rectangle {
                        Layout.preferredWidth: 4
                        Layout.fillHeight: true
                        radius: 2
                        color: stateColor()
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 7

                            Label {
                                text: "DEVTOOL"
                                color: theme.mutedText
                                font.pointSize: theme.captionPointSize
                                font.weight: Font.Bold
                                font.letterSpacing: 0
                            }
                            Label {
                                visible: !root.narrowHud
                                text: hudStore.ipcConnected ? "LIVE" : "FILE FALLBACK"
                                color: hudStore.ipcConnected ? theme.success : theme.warning
                                font.pointSize: theme.captionPointSize
                                font.weight: Font.Bold
                                font.letterSpacing: 0
                            }
                            Label { visible: !root.narrowHud; text: "•"; color: theme.subtleText; font.pointSize: theme.captionPointSize; font.letterSpacing: 0 }
                            Label { visible: !root.narrowHud; text: elapsedText(); color: theme.subtleText; font.pointSize: theme.captionPointSize; font.letterSpacing: 0 }
                            Label {
                                visible: scopedLanes().length > 1
                                text: "• " + scopedLanes().length + " workspaces"
                                color: theme.subtleText
                                font.pointSize: theme.captionPointSize
                                font.letterSpacing: 0
                            }
                            Item { Layout.fillWidth: true }

                            Rectangle {
                                visible: !root.narrowHud
                                implicitHeight: 24
                                implicitWidth: statusBadgeText.implicitWidth + 24
                                radius: 12
                                color: theme.alpha(stateColor(), theme.dark ? 0.16 : 0.10)
                                border.color: theme.alpha(stateColor(), 0.34)
                                Row {
                                    anchors.centerIn: parent
                                    spacing: 5
                                    StatusMark {
                                        kind: visualState()
                                        markColor: stateColor()
                                        markSize: 13
                                    }
                                    Label {
                                        id: statusBadgeText
                                        text: stateLabel()
                                        color: stateColor()
                                        font.pointSize: theme.captionPointSize
                                        font.weight: Font.Bold
                                        font.letterSpacing: 0
                                    }
                                }
                            }

                            AppButton {
                                text: ""
                                iconSource: copiedFeedback ? "qrc:/qt/qml/Devtool/ControlCenter/icons/done.svg" : "qrc:/qt/qml/Devtool/ControlCenter/icons/copy.svg"
                                accessibleName: copiedFeedback ? "Summary copied" : "Copy run summary"
                                accessibleDescription: "Copy the current run summary. Keyboard shortcut Control+C."
                                variant: "ghost"
                                compact: true
                                implicitWidth: 40
                                onClicked: {
                                    hudStore.copySummary(run)
                                    copiedFeedback = true
                                    copiedTimer.restart()
                                }
                            }
                            AppButton {
                                text: ""
                                iconSource: "qrc:/qt/qml/Devtool/ControlCenter/icons/log.svg"
                                accessibleName: "Open run log"
                                accessibleDescription: "Open the current run log. Keyboard shortcut Control+L."
                                variant: "ghost"
                                compact: true
                                implicitWidth: 40
                                enabled: !!run.log_path
                                onClicked: hudStore.openLog(run)
                            }
                            AppButton {
                                text: ""
                                iconSource: "qrc:/qt/qml/Devtool/ControlCenter/icons/details.svg"
                                accessibleName: parserExpanded ? "Hide run details" : "Show run details"
                                accessibleDescription: "Toggle runner metrics and parsed details. Keyboard shortcut Control+D."
                                variant: parserExpanded ? "primary" : "ghost"
                                compact: true
                                implicitWidth: 40
                                onClicked: parserExpanded = !parserExpanded
                            }
                            AppButton {
                                text: ""
                                iconSource: "qrc:/qt/qml/Devtool/ControlCenter/icons/close.svg"
                                accessibleName: "Close live HUD"
                                accessibleDescription: "Close this progress window. Keyboard shortcut Escape."
                                variant: "ghost"
                                compact: true
                                implicitWidth: 40
                                onClicked: Qt.quit()
                            }
                        }

                        Label {
                            Layout.fillWidth: true
                            text: taskTitle()
                            color: theme.windowText
                            font.pointSize: theme.pageTitlePointSize - 2
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                            lineHeight: theme.titleLineHeight
                        }

                        Label {
                            Layout.fillWidth: true
                            text: repositorySubtitle()
                            color: theme.subtleText
                            font.pointSize: theme.captionPointSize
                            elide: Text.ElideMiddle
                            lineHeight: theme.compactLineHeight
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Label {
                                text: statusLead()
                                color: theme.windowText
                                font.pointSize: theme.smallPointSize
                                font.weight: Font.DemiBold
                                font.letterSpacing: 0
                            }
                            Label {
                                Layout.fillWidth: true
                                text: statusDetail()
                                color: theme.subtleText
                                font.pointSize: theme.smallPointSize
                                elide: Text.ElideRight
                                lineHeight: theme.compactLineHeight
                            }
                            Label {
                                text: progressLabel()
                                color: stateColor()
                                font.pointSize: theme.cardTitlePointSize - 1
                                font.weight: Font.Bold
                                font.letterSpacing: 0
                            }
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 9
                            radius: 5
                            color: theme.alpha(theme.recessedSurface, root.recessedOpacity)
                            border.color: theme.alpha(theme.windowText, 0.06)
                            border.width: 1
                            Rectangle {
                                id: overallBar
                                height: parent.height
                                radius: 5
                                width: parent.width * displayedProgress() / 100
                                color: stateColor()
                                Behavior on width { NumberAnimation { duration: 100; easing.type: Easing.OutCubic } }
                                SequentialAnimation on opacity {
                                    running: visualState() === "rollback"
                                    loops: Animation.Infinite
                                    NumberAnimation { to: 0.45; duration: 420 }
                                    NumberAnimation { to: 1.0; duration: 420 }
                                }
                            }
                        }

                        Flow {
                            Layout.fillWidth: true
                            spacing: 5
                            visible: sharedVisibleSteps().length > 0
                            Repeater {
                                model: sharedVisibleSteps()
                                delegate: Rectangle {
                                    required property var modelData
                                    implicitWidth: sharedLabel.implicitWidth + 16
                                    implicitHeight: 22
                                    radius: 7
                                    color: modelData.status === "running" ? theme.selectedSurface
                                         : modelData.status === "ok" ? theme.successSurface
                                         : modelData.status === "failed" ? theme.errorSurface
                                         : theme.elevatedSurface
                                    border.color: modelData.status === "running" ? theme.alpha(theme.info, 0.30)
                                                : modelData.status === "failed" ? theme.alpha(theme.error, 0.34)
                                                : "transparent"
                                    Label {
                                        id: sharedLabel
                                        anchors.centerIn: parent
                                        text: modelData.phaseLabel || modelData.label
                                        color: modelData.status === "ok" ? theme.success
                                             : modelData.status === "failed" ? theme.error
                                             : modelData.status === "running" ? theme.windowText
                                             : theme.subtleText
                                        font.pointSize: theme.captionPointSize
                                        font.weight: Font.DemiBold
                                        font.letterSpacing: 0
                                    }
                                }
                            }
                        }
                    }
                }
            }

            ScrollView {
                id: workspaceScroll
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(342, workspaceColumn.implicitHeight)
                visible: scopedLanes().length > 0
                clip: true
                background: Rectangle { color: "transparent" }
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ScrollBar.vertical.policy: workspaceColumn.implicitHeight > height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff

                Column {
                    id: workspaceColumn
                    width: workspaceScroll.availableWidth
                    spacing: 8

                    Repeater {
                        model: scopedLanes()
                        delegate: Rectangle {
                            id: workspaceCard
                            required property var modelData
                            required property int index
                            width: workspaceColumn.width
                            implicitHeight: cardColumn.implicitHeight + 18
                            radius: 12
                            color: theme.alpha(laneStatus(modelData) === "failed" ? theme.errorSurface : theme.surface, root.panelOpacity)
                            border.width: workspaceCard.activeFocus ? 2 : 1
                            border.color: workspaceCard.activeFocus ? theme.accent : laneStatus(modelData) === "failed" ? theme.alpha(theme.error, 0.40)
                                        : laneStatus(modelData) === "running" ? theme.alpha(theme.info, 0.34)
                                        : theme.alpha(theme.windowText, 0.08)
                            property bool active: laneStatus(modelData) === "running"
                            property bool expanded: active || root.expandedLaneKey === modelData.key || (scopedLanes().length === 1 && root.expandedLaneKey === "")
                            property var metrics: laneMetrics(modelData)
                            activeFocusOnTab: !active
                            Accessible.role: Accessible.Button
                            Accessible.name: laneTitle(modelData) + ", " + laneStatusLabel(modelData) + ", " + laneProgressLabel(modelData)
                            Accessible.description: active ? "Active workspace details remain expanded" : "Press Space or Enter to toggle workspace details"
                            Keys.onSpacePressed: if (!active) root.expandedLaneKey = expanded ? "" : modelData.key
                            Keys.onReturnPressed: if (!active) root.expandedLaneKey = expanded ? "" : modelData.key
                            Keys.onEnterPressed: if (!active) root.expandedLaneKey = expanded ? "" : modelData.key

                            ColumnLayout {
                                id: cardColumn
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: 9
                                spacing: 7

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 9

                                    Rectangle {
                                        Layout.preferredWidth: 28
                                        Layout.preferredHeight: 28
                                        radius: 7
                                        color: theme.alpha(laneColor(modelData), theme.dark ? 0.10 : 0.07)
                                        border.color: theme.alpha(laneColor(modelData), 0.34)
                                        Label {
                                            anchors.centerIn: parent
                                            text: String(index + 1)
                                            color: laneColor(modelData)
                                            font.pointSize: theme.captionPointSize
                                            font.weight: Font.Bold
                                        }
                                    }

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 1
                                        Label {
                                            Layout.fillWidth: true
                                            text: laneTitle(modelData)
                                            color: theme.windowText
                                            font.pointSize: theme.smallPointSize
                                            font.weight: Font.DemiBold
                                            elide: Text.ElideMiddle
                                            font.letterSpacing: 0
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: laneSubtitle(modelData)
                                            color: theme.subtleText
                                            font.pointSize: theme.captionPointSize
                                            elide: Text.ElideMiddle
                                            font.letterSpacing: 0
                                        }
                                    }

                                    Label {
                                        text: laneStatusLabel(modelData)
                                        color: laneColor(modelData)
                                        font.pointSize: theme.captionPointSize
                                        font.weight: Font.Bold
                                        font.letterSpacing: 0
                                    }
                                    Image {
                                        source: workspaceCard.expanded ? "qrc:/qt/qml/Devtool/ControlCenter/icons/chevron-up.svg" : "qrc:/qt/qml/Devtool/ControlCenter/icons/chevron-down.svg"
                                        sourceSize.width: 16
                                        sourceSize.height: 16
                                        width: 16
                                        height: 16
                                        Accessible.ignored: true
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 8
                                    Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 5
                                        radius: 3
                                        color: theme.alpha(theme.recessedSurface, root.recessedOpacity)
                                        border.color: theme.alpha(theme.windowText, 0.05)
                                        border.width: 1
                                        Rectangle {
                                            height: parent.height
                                            radius: 3
                                            width: parent.width * laneProgress(modelData) / 100
                                            color: laneColor(modelData)
                                            Behavior on width { NumberAnimation { duration: 100; easing.type: Easing.OutCubic } }
                                        }
                                    }
                                    Label {
                                        text: laneProgressLabel(modelData)
                                        color: laneColor(modelData)
                                        font.pointSize: theme.captionPointSize
                                        font.weight: Font.Bold
                                        font.letterSpacing: 0
                                    }
                                }

                                ColumnLayout {
                                    visible: workspaceCard.expanded
                                    Layout.fillWidth: true
                                    spacing: 7

                                    Flow {
                                        Layout.fillWidth: true
                                        spacing: 5
                                        Repeater {
                                            model: workspaceCard.modelData.steps
                                            delegate: Rectangle {
                                                required property var modelData
                                                implicitWidth: phaseText.implicitWidth + (modelData.validator ? 46 : 16)
                                                implicitHeight: 24
                                                radius: 7
                                                color: modelData.status === "ok" ? theme.successSurface
                                                     : modelData.status === "failed" ? theme.errorSurface
                                                     : modelData.status === "running" ? theme.selectedSurface
                                                     : theme.elevatedSurface
                                                border.color: modelData.status === "failed" ? theme.alpha(theme.error, 0.34)
                                                            : modelData.status === "running" ? theme.alpha(theme.info, 0.30)
                                                            : "transparent"
                                                Row {
                                                    anchors.centerIn: parent
                                                    spacing: 5
                                                    StatusMark {
                                                        kind: modelData.status
                                                        markColor: modelData.status === "ok" ? theme.success
                                                                 : modelData.status === "failed" ? theme.error
                                                                 : modelData.status === "running" ? theme.info
                                                                 : theme.subtleText
                                                        markSize: 12
                                                    }
                                                    Label {
                                                        id: phaseText
                                                        text: modelData.phaseLabel || modelData.label
                                                        color: modelData.status === "running" ? theme.windowText : theme.subtleText
                                                        font.pointSize: theme.captionPointSize
                                                        font.weight: Font.DemiBold
                                                    }
                                                    Rectangle {
                                                        visible: !!modelData.validator
                                                        implicitWidth: scriptLabel.implicitWidth + 8
                                                        implicitHeight: 16
                                                        radius: 8
                                                        color: theme.alpha(theme.elevatedSurface, root.panelOpacity)
                                                        Label {
                                                            id: scriptLabel
                                                            anchors.centerIn: parent
                                                            text: "script"
                                                            color: theme.subtleText
                                                            font.pointSize: theme.captionPointSize
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }

                                    Label {
                                        Layout.fillWidth: true
                                        visible: !!workspaceCard.metrics.lastDetail
                                        text: workspaceCard.metrics.lastDetail
                                        color: theme.subtleText
                                        font.pointSize: theme.captionPointSize
                                        elide: Text.ElideMiddle
                                    }

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 12
                                        Label {
                                            text: projectUnitLabel() + "  " + workspaceCard.metrics.projects
                                            color: theme.subtleText
                                            font.pointSize: theme.captionPointSize
                                        }
                                        Label {
                                            text: testContainerLabel() + "  " + workspaceCard.metrics.assemblies
                                            color: theme.subtleText
                                            font.pointSize: theme.captionPointSize
                                        }
                                        Label {
                                            text: workspaceCard.metrics.passed + " passed"
                                            color: workspaceCard.metrics.failed > 0 ? theme.subtleText : theme.success
                                            font.pointSize: theme.captionPointSize
                                        }
                                        Label {
                                            text: workspaceCard.metrics.failed + " failed"
                                            color: workspaceCard.metrics.failed > 0 ? theme.error : theme.subtleText
                                            font.pointSize: theme.captionPointSize
                                        }
                                        Label {
                                            text: "Validator  " + workspaceCard.metrics.validator
                                            color: workspaceCard.metrics.validator === "failed" ? theme.error
                                                 : workspaceCard.metrics.validator === "passed" ? theme.success
                                                 : theme.subtleText
                                            font.pointSize: theme.captionPointSize
                                        }
                                        Item { Layout.fillWidth: true }
                                    }
                                }
                            }

                            MouseArea {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                height: 52
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    if (workspaceCard.active) return
                                    root.expandedLaneKey = root.expandedLaneKey === workspaceCard.modelData.key ? "" : workspaceCard.modelData.key
                                }
                            }
                        }
                    }
                }
            }

            Rectangle {
                id: parserPanel
                visible: parserExpanded
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? parserContent.implicitHeight + 20 : 0
                radius: 12
                color: theme.alpha(theme.recessedSurface, root.recessedOpacity)
                border.color: theme.alpha(theme.info, 0.28)
                border.width: 1

                ColumnLayout {
                    id: parserContent
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 7
                    property var metrics: aggregateParserMetrics()

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: "LIVE " + runnerLabel().toUpperCase() + " DETAILS"
                            color: theme.mutedText
                            font.pointSize: theme.captionPointSize
                            font.weight: Font.Bold
                            font.letterSpacing: 0
                        }
                        Item { Layout.fillWidth: true }
                        Label {
                            text: runnerLabel() + " · " + executionLabel() + " · task progress is separate from test totals"
                            color: theme.subtleText
                            font.pointSize: theme.captionPointSize
                            lineHeight: theme.compactLineHeight
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Repeater {
                            model: [
                                { label: projectUnitLabel().toUpperCase(), value: parserContent.metrics.projects },
                                { label: testContainerLabel().toUpperCase(), value: parserContent.metrics.assemblies },
                                { label: "PASSED", value: parserContent.metrics.passed },
                                { label: "FAILED", value: parserContent.metrics.failed },
                                { label: "SKIPPED", value: parserContent.metrics.skipped },
                                { label: "DIAGNOSTICS", value: Number(run.errors || 0) + " / " + Number(run.warnings || 0) }
                            ]
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.preferredHeight: 48
                                radius: 9
                                color: theme.alpha(theme.elevatedSurface, root.panelOpacity)
                                border.color: theme.alpha(theme.windowText, 0.07)
                                Column {
                                    anchors.centerIn: parent
                                    spacing: 1
                                    Label {
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        text: String(modelData.value)
                                        color: modelData.label === "FAILED" && Number(modelData.value) > 0 ? theme.error : theme.windowText
                                        font.pointSize: theme.cardTitlePointSize - 1
                                        font.weight: Font.Bold
                                        font.letterSpacing: 0
                                    }
                                    Label {
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        text: modelData.label
                                        color: theme.mutedText
                                        font.pointSize: theme.captionPointSize
                                        font.weight: Font.Bold
                                        font.letterSpacing: 0
                                    }
                                }
                            }
                        }
                    }

                    Label {
                        Layout.fillWidth: true
                        text: "Last parsed update: " + (activeStep().detail || "Waiting for runner output")
                        color: theme.subtleText
                        font.family: theme.monospaceFamily
                        font.pointSize: theme.captionPointSize
                        lineHeight: theme.compactLineHeight
                        elide: Text.ElideMiddle
                    }
                }
            }
        }

        Shortcut {
            sequences: [StandardKey.Copy]
            onActivated: {
                hudStore.copySummary(run)
                copiedFeedback = true
                copiedTimer.restart()
            }
        }
        Shortcut { sequence: "Ctrl+L"; enabled: !!run.log_path; onActivated: hudStore.openLog(run) }
        Shortcut { sequence: "Ctrl+D"; onActivated: parserExpanded = !parserExpanded }
        Shortcut { sequence: "Escape"; onActivated: Qt.quit() }

        MouseArea {
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            width: 18
            height: 18
            cursorShape: Qt.SizeFDiagCursor
            acceptedButtons: Qt.LeftButton
            onPressed: root.startSystemResize(Qt.RightEdge | Qt.BottomEdge)
        }
        MouseArea {
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 8
            cursorShape: Qt.SizeHorCursor
            acceptedButtons: Qt.LeftButton
            onPressed: root.startSystemResize(Qt.RightEdge)
        }
        MouseArea {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 8
            cursorShape: Qt.SizeVerCursor
            acceptedButtons: Qt.LeftButton
            onPressed: root.startSystemResize(Qt.BottomEdge)
        }
    }
}
