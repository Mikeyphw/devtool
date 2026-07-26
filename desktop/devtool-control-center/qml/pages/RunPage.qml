import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import Devtool.ControlCenter.Components 1.0

Item {
    id: root
    property string operation: "Apply overlay"
    property string pasteFeedback: ""
    property string importedCommand: ""
    property bool manifestDefaultsPending: false
    property bool importedTargetExplicit: false
    property bool importedValidateExplicit: false
    property bool importedCommitExplicit: false
    property bool importedCopyExplicit: false
    property bool importedHudExplicit: false
    property int currentStage: 0
    readonly property bool narrowMode: width < 760
    property bool projectAdvancedOpen: false
    property bool operationAdvancedOpen: false
    property bool reviewAdvancedOpen: false
    property bool commandPreviewOpen: false
    property var testCatalog: []
    property var selectedTests: ({})
    property string catalogMessage: ""
    readonly property var allStages: ["Project", "Operation", "Tests", "Review"]
    readonly property var compactStages: ["Project", "Operation", "Review"]
    readonly property var stages: root.needsTests() ? root.allStages : root.compactStages
    readonly property var operations: ["Apply overlay", "Apply bundle", "Validate", "Restore", "Build", "Test", "Lint", "Run project", "Package", "Clean", "Inspect", "Commit", "Status", "Doctor"]
    Theme { id: theme }
    Accessible.role: Accessible.Pane
    Accessible.name: "Run workflow, " + stageName(currentStage) + " step"
    Accessible.description: "Workflow builder with progressive disclosure. Use Alt+Left and Alt+Right to move between steps, and Control+Enter to run from Review."

    function copyModeIndex(value) { const v=String(value||"summary").toLowerCase(); return v==="json"?1:v==="none"?2:0 }
    function selectedCopyMode() { return copyMode.currentIndex===1?"json":copyMode.currentIndex===2?"none":"summary" }
    function hudModeIndex(value) { const v=["control-center","desktop-window","android-bridge","none"]; const i=v.indexOf(String(value||"control-center").toLowerCase()); return i<0?0:i }
    function operationIndex(label) { const i=root.operations.indexOf(label); return i<0?0:i }
    function isApplyOperation() { return operation==="Apply overlay" || operation==="Apply bundle" }
    function needsTests() { return operation==="Test" || operation==="Validate" || isApplyOperation() }
    function stageName(stage) { return stage===0 ? "Project" : stage===1 ? "Operation" : stage===2 ? "Tests" : "Review" }
    function stageForVisibleIndex(index) { return !needsTests() && index===2 ? 3 : index }
    function visibleIndexForStage(stage) { return needsTests() ? stage : (stage < 2 ? stage : 2) }
    function visibleStepCount() { return needsTests() ? 4 : 3 }
    function isDangerousOperation() { return isApplyOperation() || operation==="Restore" || operation==="Clean" || operation==="Commit" }
    function dangerExplanation() {
        if (isApplyOperation()) return "This writes artifact files into the selected repository and may create a commit."
        if (operation==="Restore") return "Restore may replace generated or working files."
        if (operation==="Clean") return "Clean removes build outputs and generated caches."
        return "This operation changes repository state."
    }
    function inferredArtifactTarget(summary) {
        if (!summary) return ""; if (summary.target) return String(summary.target)
        const values=[]; const groups=[summary.workspaces||[],summary.overlays||[]]
        for (let g=0;g<groups.length;++g) for(let i=0;i<groups[g].length;++i){const v=String(groups[g][i].target||"");if(v&&values.indexOf(v)<0)values.push(v)}
        return values.length===1?values[0]:""
    }
    function applyArtifactManifestDefaults() {
        if (!manifestDefaultsPending || !artifactInspector.summary || !artifactInspector.summary.manifest_present) return
        const summary=artifactInspector.summary
        if(!importedTargetExplicit){const t=inferredArtifactTarget(summary);if(t)target.text=t}
        if(!importedValidateExplicit)validate.checked=!!((summary.validation||{}).policy||{}).required
        if(!importedCommitExplicit){const policy=summary.commit||{};message.text=String(policy.message||"");commit.checked=!!policy.enabled||message.text.trim()!==""}
        manifestDefaultsPending=false
        pasteFeedback="Command and artifact manifest loaded. Target, validation, commit message, and checkboxes are ready."
    }
    function applyImportedCommand(values) {
        if(!values||!values.valid){pasteFeedback="Clipboard does not contain a supported devtool command.";return}
        importedCommand=values.command||""; if(values.repository)appController.repository=values.repository;if(values.artifact)appController.artifact=values.artifact
        operationCombo.currentIndex=operationIndex(values.operation); importedTargetExplicit=!!values.targetExplicit; importedValidateExplicit=!!values.validateExplicit; importedCommitExplicit=!!values.commitExplicit; importedCopyExplicit=!!values.copyExplicit; importedHudExplicit=!!values.hudExplicit; manifestDefaultsPending=!!values.artifact
        target.text=values.target||"";message.text=values.commitMessage||"";validate.checked=!!values.validate;commit.checked=!!values.commit;keep.checked=!!values.keepArtifact
        if(importedCopyExplicit)copyMode.currentIndex=copyModeIndex(values.copyMode)
        if(importedHudExplicit){autoHud.checked=!!values.autoHud;hudMode.currentIndex=hudModeIndex(values.hudMode)}
        pasteFeedback=values.artifact?"Command fields filled. Reading target, validation, and commit defaults from the artifact manifest…":"Command fields filled. The original cd + devtool command is preserved for direct execution."
        artifactVerification.restart()
    }
    function filteredTests() { const q=testSearch.text.trim().toLowerCase(); return q?testCatalog.filter(function(i){return String(i.label||i.id||"").toLowerCase().indexOf(q)>=0||String(i.suite||"").toLowerCase().indexOf(q)>=0}):testCatalog }
    function updateSelectedTest(id, checked) { const next=Object.assign({},selectedTests);if(checked)next[id]=true;else delete next[id];selectedTests=next;const v=[];for(const k in next)if(next[k])v.push(k);testSelectors.text=v.join(", ") }
    function selectedCount(){let n=0;for(const k in selectedTests)if(selectedTests[k])n++;return n}
    function refreshTestCatalog(){catalogMessage="Discovering tests…";const r=appController.discoverTests(appController.repository,target.text,projectType.currentText,testProfile.text,"");testCatalog=r.items||[];catalogMessage=r.error||r.warning||(testCatalog.length+" tests discovered")}
    function commandText(){return appController.buildWorkflowCommand(operation,target.text,projectType.currentText,executionEnvironment.currentText,testProfile.text,testSelectors.text,testTags.text,testExcludeTags.text,testPath.text,testClass.text,testMethod.text,listTests.checked,instrumented.checked,validate.checked,commit.checked,message.text,keep.checked,manifestOverride.checked,selectedCopyMode(),autoHud.checked,hudMode.currentText)}
    function canRun(){
        if(!appController.repository)return false
        if(operation==="Commit"&&message.text.trim()==="")return false
        if(isApplyOperation()){
            const verified=artifactInspector.summary.manifest_present===true&&artifactInspector.summary.manifest_json_valid===true&&((artifactInspector.summary.validation||{}).ok)===true
            return appController.artifactExists&&!artifactInspector.loading&&(verified||manifestOverride.checked)
        }
        return commandText()!==""
    }
    function currentHomePrefix() {
        const repo = String(appController.repository || "")
        if (repo.indexOf("/home/") === 0) {
            const parts = repo.split("/")
            return parts.length > 2 ? "/home/" + parts[2] : ""
        }
        if (repo.indexOf("/data/data/com.termux/files/home") === 0)
            return "/data/data/com.termux/files/home"
        return ""
    }
    function normalizedRepoPath(path) {
        let value = String(path || "")
        const home = currentHomePrefix()
        if (home) {
            if (value.indexOf("$HOME") === 0) value = home + value.slice(5)
            if (value.indexOf("~") === 0) value = home + value.slice(1)
        }
        while (value.length > 1 && value.endsWith("/")) value = value.slice(0, -1)
        return value
    }
    function clipboardCandidateInfo() {
        return appController.parseDevtoolCommand(appController.clipboardCandidateText || "")
    }
    function clipboardCandidateRepo() {
        return normalizedRepoPath((clipboardCandidateInfo() || {}).repository || "")
    }
    function clipboardCandidateRepoMismatch() {
        const commandRepo = clipboardCandidateRepo()
        const currentRepo = normalizedRepoPath(appController.repository || "")
        return commandRepo !== "" && currentRepo !== "" && commandRepo !== currentRepo
    }
    function clipboardCandidateMismatchText() {
        return "Clipboard command targets " + clipboardCandidateRepo() + ", while this form is set to " + normalizedRepoPath(appController.repository || "") + ". Review before running."
    }

    function runWorkflowNow(){appController.runWorkflow(operation,target.text,projectType.currentText,executionEnvironment.currentText,testProfile.text,testSelectors.text,testTags.text,testExcludeTags.text,testPath.text,testClass.text,testMethod.text,listTests.checked,instrumented.checked,validate.checked,commit.checked,message.text,keep.checked,manifestOverride.checked,selectedCopyMode(),autoHud.checked,hudMode.currentText)}
    function requestRun(){if(isDangerousOperation())dangerConfirmation.open();else runWorkflowNow()}

    Timer { id: artifactVerification; interval: 350; repeat: false; onTriggered: { artifactInspector.clear(); artifactInspector.artifactPath=appController.artifact; artifactInspector.repositoryPath=appController.repository; if(appController.artifactExists)artifactInspector.inspect(false) } }
    Connections { target: appController; function onArtifactChanged(){artifactVerification.restart()} }
    Connections { target: artifactInspector; function onInspectionChanged(){root.applyArtifactManifestDefaults()} }
    Component.onCompleted: artifactVerification.restart()

    PathPickerDialog {
        id: repoDialog
        selectFolder: true
        initialPath: appController.repository
        onAccepted: function(path) { appController.repository = path }
    }
    PathPickerDialog {
        id: artifactDialog
        selectFolder: false
        initialPath: appController.artifact
        nameFilters: ["Archives (*.zip *.tar *.gz *.zst)", "All files (*)"]
        onAccepted: function(path) { appController.artifact = path; artifactVerification.restart() }
    }

    Popup {
        id: commandConfirmation; parent: Overlay.overlay; modal: true; focus: true; closePolicy: Popup.CloseOnEscape|Popup.CloseOnPressOutside
        width: Math.min(760,Overlay.overlay.width-48);height:Math.min(480,Overlay.overlay.height-48);x:(Overlay.overlay.width-width)/2;y:(Overlay.overlay.height-height)/2
        background: Rectangle{color:theme.elevatedSurface;radius:theme.cardRadius;border.color:theme.strongBorder;border.width:1}
        contentItem: ColumnLayout {
            anchors.fill:parent;anchors.margins:18;spacing:12
            Label{text:"Review detected command";color:theme.windowText;font.pointSize:theme.cardTitlePointSize;font.weight:Font.DemiBold}
            Label{Layout.fillWidth:true;text:root.clipboardCandidateRepoMismatch()?root.clipboardCandidateMismatchText():"Review every argument first. You can fill the workflow form or run the reviewed command in your preferred terminal.";color:root.clipboardCandidateRepoMismatch()?theme.warning:theme.mutedText;wrapMode:Text.Wrap}
            Label {
                text: "Cleaned command"
                color: theme.mutedText
                font.letterSpacing: 0.25
            }
            Rectangle{Layout.fillWidth:true;Layout.fillHeight:true;color:theme.recessedSurface;radius:theme.controlRadius
                ScrollView{anchors.fill:parent;anchors.margins:8;AppTextArea{text:root.importedCommand;readOnly:true;wrapMode:TextEdit.WrapAnywhere;background:null}}
            }
            RowLayout{Layout.fillWidth:true;AppButton{text:"Cancel";variant:"ghost";onClicked:commandConfirmation.close()}
Item{Layout.fillWidth:true}
AppButton {
                    text: "Use command values"
                    variant: "default"
                    onClicked: {
                        root.applyImportedCommand(appController.parseDevtoolCommand(root.importedCommand))
                        root.currentStage = 3
                        commandConfirmation.close()
                    }
                }
AppButton {
                    text: "Run reviewed command"
                    variant: root.clipboardCandidateRepoMismatch() ? "danger" : "primary"
                    onClicked: {
                        commandConfirmation.close()
                        appController.runHistoricalCommand(root.importedCommand, appController.repository)
                    }
                }}
        }
    }
    Popup {
        id: dangerConfirmation; parent: Overlay.overlay; modal:true;focus:true;closePolicy:Popup.CloseOnEscape
        width:Math.min(460,Overlay.overlay.width-40);height:220;x:(Overlay.overlay.width-width)/2;y:(Overlay.overlay.height-height)/2
        background:Rectangle{color:theme.elevatedSurface;radius:theme.cardRadius;border.color:theme.strongBorder;border.width:1}
        contentItem:ColumnLayout{anchors.fill:parent;anchors.margins:18;spacing:12
            Label{text:"Confirm "+root.operation;color:theme.windowText;font.pointSize:theme.cardTitlePointSize;font.weight:Font.DemiBold}
            Label{Layout.fillWidth:true;text:root.dangerExplanation();color:theme.mutedText;wrapMode:Text.Wrap}
            Label{Layout.fillWidth:true;text:appController.repository;color:theme.subtleText;font.family:theme.monospaceFamily;elide:Text.ElideMiddle}
            Item{Layout.fillHeight:true}
            RowLayout{Layout.fillWidth:true;Item{Layout.fillWidth:true}
AppButton{text:"Cancel";variant:"ghost";onClicked:dangerConfirmation.close()}
AppButton{text:"Continue";variant:"danger";onClicked:{dangerConfirmation.close();root.runWorkflowNow()}}}
        }
    }

    AppScrollView {
        id: workflowScroll
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: stickyBar.top
        contentHeight: workflowColumn.implicitHeight + 20
        ColumnLayout {
            id: workflowColumn
            width: Math.max(0, workflowScroll.availableWidth)
            spacing: 14
            RowLayout { Layout.fillWidth:true;Layout.leftMargin:theme.pageMargin;Layout.rightMargin:theme.pageMargin;Layout.topMargin:20
                PageHeading{text:"Run"}
Item{Layout.fillWidth:true}
AppButton {
                    text: "Paste from clipboard"
                    variant: "ghost"
                    onClicked: {
                        const v = appController.pasteCommandFromClipboard()
                        root.applyImportedCommand(v)
                        if (v && v.valid) {
                            root.importedCommand = v.command || ""
                            commandConfirmation.open()
                        }
                    }
                }
            }
            Label{visible:root.pasteFeedback!=="";Layout.fillWidth:true;Layout.leftMargin:theme.pageMargin;Layout.rightMargin:theme.pageMargin;text:root.pasteFeedback;color:theme.mutedText;wrapMode:Text.Wrap}
            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: theme.pageMargin
                Layout.rightMargin: theme.pageMargin
                visible: appController.clipboardCandidateText !== ""
                implicitHeight: clipboardCandidateColumn.implicitHeight + 28
                color: root.clipboardCandidateRepoMismatch() ? theme.warningSurface : theme.surface
                border.color: root.clipboardCandidateRepoMismatch() ? theme.alpha(theme.warning, 0.55) : theme.border
                ColumnLayout {
                    id: clipboardCandidateColumn
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 8
                    Label {
                        text: "Detected clipboard command"
                        color: theme.windowText
                        font.weight: Font.DemiBold
                    }
                    Label {
                        Layout.fillWidth: true
                        text: appController.clipboardCandidateText
                        color: theme.mutedText
                        font.family: theme.monospaceFamily
                        elide: Text.ElideMiddle
                    }
                    Label {
                        visible: root.clipboardCandidateRepoMismatch()
                        Layout.fillWidth: true
                        text: root.clipboardCandidateMismatchText()
                        color: theme.warning
                        wrapMode: Text.Wrap
                    }
                    AppButton {
                        text: "Review command…"
                        variant: "primary"
                        onClicked: {
                            appController.captureClipboardCandidate()
                            root.importedCommand = appController.clipboardCandidateSnapshotText
                            commandConfirmation.open()
                        }
                    }
                }
            }
            GridLayout {
                Layout.fillWidth: true
                Layout.leftMargin: theme.pageMargin
                Layout.rightMargin: theme.pageMargin
                columns: root.narrowMode ? 2 : 4
                columnSpacing: 8
                rowSpacing: 8
                Repeater {
                    model: root.stages
                    delegate: Rectangle {
                        required property string modelData
                        required property int index
                        readonly property int actualStage: root.stageForVisibleIndex(index)
                        readonly property bool current: actualStage === root.currentStage
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        implicitHeight: Math.max(42, stageLabel.implicitHeight + 16)
                        radius: theme.controlRadius
                        color: current ? theme.selectedSurface : theme.recessedSurface
                        border.color: current ? theme.accent : theme.border
                        border.width: 1
                        Accessible.role: Accessible.StaticText
                        Accessible.name: "Step " + (index + 1) + " of " + root.visibleStepCount() + ", " + modelData + (current ? ", current" : "")
                        Label {
                            id: stageLabel
                            anchors.centerIn: parent
                            width: parent.width - 16
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                            text: (index + 1) + " · " + modelData
                            color: current ? theme.windowText : theme.mutedText
                            font.weight: current ? Font.DemiBold : Font.Normal
                        }
                    }
                }
            }

            SurfaceCard { Layout.fillWidth:true;Layout.leftMargin:theme.pageMargin;Layout.rightMargin:theme.pageMargin;visible:root.currentStage===0;implicitHeight:projectColumn.implicitHeight+32
                ColumnLayout{id:projectColumn;anchors.fill:parent;anchors.margins:16;spacing:12
                    Label{text:"Basic";color:theme.windowText;font.pointSize:theme.cardTitlePointSize;font.weight:Font.DemiBold}
                    GridLayout{Layout.fillWidth:true;columns:root.narrowMode?1:3;columnSpacing:12;rowSpacing:12
                        Label{text:"Repository";color:theme.mutedText}
AppTextField{accessibleName:"Repository root";accessibleDescription:"Repository where devtool will run";Layout.fillWidth:true;text:appController.repository;onTextEdited:appController.repository=text;placeholderText:"Repository root"}
AppButton{text:"Browse…";onClicked:repoDialog.open()}
                    }
                    AppButton{text:root.projectAdvancedOpen?"▾ Hide project options":"▸ Show project options";variant:"default";onClicked:root.projectAdvancedOpen=!root.projectAdvancedOpen}
                    GridLayout{visible:root.projectAdvancedOpen;Layout.fillWidth:true;columns:root.narrowMode?1:3;columnSpacing:12;rowSpacing:12
                        Label{text:"Project type";color:theme.mutedText}
AppComboBox{id:projectType;accessibleName:"Project type";accessibleDescription:"Select the project runner";Layout.fillWidth:true;model:["auto","dotnet","android","gradle","python","command"]}
Label{text:"Auto is recommended unless a repo needs a forced runner.";color:theme.subtleText;wrapMode:Text.Wrap}
                    }
                }
            }
            SurfaceCard { Layout.fillWidth:true;Layout.leftMargin:theme.pageMargin;Layout.rightMargin:theme.pageMargin;visible:root.currentStage===0;implicitHeight:projectContextColumn.implicitHeight+28
                ColumnLayout{id:projectContextColumn;anchors.fill:parent;anchors.margins:14;spacing:8
                    Label{text:"Project context";color:theme.windowText;font.weight:Font.DemiBold}
                    Label{Layout.fillWidth:true;text:appController.repository?"Selected repository: "+appController.repository:"Choose a repository to continue.";color:theme.mutedText;font.family:appController.repository?theme.monospaceFamily:"";elide:Text.ElideMiddle}
                    Label{Layout.fillWidth:true;text:root.clipboardCandidateRepoMismatch()?"Detected clipboard command points to another repository. Review it before applying values.":"Use project options only when auto detection needs help.";color:root.clipboardCandidateRepoMismatch()?theme.warning:theme.subtleText;wrapMode:Text.Wrap}
                }
            }
            SurfaceCard { Layout.fillWidth:true;Layout.leftMargin:theme.pageMargin;Layout.rightMargin:theme.pageMargin;visible:root.currentStage===1;implicitHeight:operationColumn.implicitHeight+32
                ColumnLayout{id:operationColumn;anchors.fill:parent;anchors.margins:16;spacing:12
                    Label{text:"Basic";color:theme.windowText;font.pointSize:theme.cardTitlePointSize;font.weight:Font.DemiBold}
                    GridLayout{Layout.fillWidth:true;columns:root.narrowMode?1:3;columnSpacing:12;rowSpacing:12
                        Label{text:"Operation";color:theme.mutedText}
AppComboBox{id:operationCombo;accessibleName:"Operation";accessibleDescription:"Select the devtool operation";Layout.fillWidth:true;model:root.operations;onCurrentTextChanged:root.operation=currentText}
Label{text:root.needsTests()?"This workflow includes validation/test selection.":"This workflow skips the Tests step.";color:theme.subtleText;wrapMode:Text.Wrap}
                        Label{text:"Artifact";color:theme.mutedText;visible:root.isApplyOperation()}
AppTextField{accessibleName:"Overlay or bundle archive";Layout.fillWidth:true;visible:root.isApplyOperation();text:appController.artifact;onTextEdited:{
    appController.artifact = text
    artifactVerification.restart()
}
placeholderText:"Overlay or bundle archive"}
AppButton{text:"Browse…";visible:root.isApplyOperation();onClicked:artifactDialog.open()}
                        Label{text:"Target";color:theme.mutedText}
AppTextField{id:target;accessibleName:"Target or profile";Layout.fillWidth:true;placeholderText:"Optional target/profile"}
Item{}
                        Label{text:"Commit message";color:theme.mutedText;visible:root.isApplyOperation()||root.operation==="Commit"}
AppTextField{id:message;accessibleName:"Commit message";Layout.fillWidth:true;visible:root.isApplyOperation()||root.operation==="Commit";placeholderText:"Artifact manifest may provide this"}
Item{}
                    }
                    AppButton{text:root.operationAdvancedOpen?"▾ Hide advanced execution":"▸ Show advanced execution";variant:"default";onClicked:root.operationAdvancedOpen=!root.operationAdvancedOpen}
                    GridLayout{visible:root.operationAdvancedOpen;Layout.fillWidth:true;columns:root.narrowMode?1:3;columnSpacing:12;rowSpacing:12
                        Label{text:"Execution";color:theme.mutedText}
AppComboBox{id:executionEnvironment;accessibleName:"Execution environment";accessibleDescription:"Select where devtool should execute";Layout.fillWidth:true;model:["auto","native-termux","chroot","desktop-linux","android-device","external-device"]}
Label{text:"Leave on auto for normal local runs.";color:theme.subtleText;wrapMode:Text.Wrap}
                        Label{text:"Custom arguments";color:theme.mutedText}
AppTextField{id:customArgs;accessibleName:"Custom operation arguments";Layout.fillWidth:true;placeholderText:"Used only with custom or manual operations"}
Item{}
                    }
                }
            }
            RowLayout { Layout.fillWidth:true;Layout.leftMargin:theme.pageMargin;Layout.rightMargin:theme.pageMargin;visible:root.currentStage===1&&root.isApplyOperation()&&appController.artifact!=="";spacing:8
                Rectangle{width:8;height:8;radius:4;color:!appController.artifactExists?theme.error:artifactInspector.loading?theme.info:artifactInspector.summary.manifest_present&&artifactInspector.summary.manifest_json_valid&&((artifactInspector.summary.validation||{}).ok)?theme.success:theme.warning}
                Label{Layout.fillWidth:true;text:!appController.artifactExists?"Artifact file does not exist.":artifactInspector.loading?"Checking artifact and devtool-artifact.json…":artifactInspector.error!==""?"Artifact inspection failed: "+artifactInspector.error:!artifactInspector.summary.manifest_present?"Artifact exists, but no devtool-artifact.json or supported sidecar manifest was found.":!artifactInspector.summary.manifest_json_valid?"Artifact manifest JSON is invalid.":!((artifactInspector.summary.validation||{}).ok)?"Artifact manifest validation failed.":"Artifact and manifest are valid";color:theme.mutedText;wrapMode:Text.Wrap}
                AppButton{text:"Recheck";compact:true;onClicked:{manifestOverride.checked=false;artifactVerification.restart()}}
            }
            ColumnLayout { Layout.fillWidth:true;Layout.leftMargin:theme.pageMargin;Layout.rightMargin:theme.pageMargin;visible:root.currentStage===1&&root.isApplyOperation()&&appController.artifactExists&&!artifactInspector.loading&&!(artifactInspector.summary.manifest_present===true&&artifactInspector.summary.manifest_json_valid===true&&((artifactInspector.summary.validation||{}).ok)===true)
                AppCheckBox{id:manifestOverride;text:"Advanced: apply without manifest check";checked:false}
                Label{Layout.fillWidth:true;text:"Equivalent to --no-manifest-check. Repository identity and manifest policy checks are bypassed.";color:manifestOverride.checked?theme.warning:theme.subtleText;wrapMode:Text.Wrap}
            }

            SurfaceCard { Layout.fillWidth:true;Layout.leftMargin:theme.pageMargin;Layout.rightMargin:theme.pageMargin;visible:root.currentStage===2;implicitHeight:testsColumn.implicitHeight+32
                ColumnLayout{id:testsColumn;anchors.fill:parent;anchors.margins:16;spacing:12
                    Label{text:root.needsTests()?"Test and validation selection":"No test selection required";color:theme.windowText;font.pointSize:theme.cardTitlePointSize;font.weight:Font.DemiBold}
                    Label{Layout.fillWidth:true;text:root.needsTests()?"Discover the real test catalog, search it, and select exact tests.":"Continue to Review.";color:theme.mutedText;wrapMode:Text.Wrap}
                    GridLayout{visible:root.needsTests();Layout.fillWidth:true;columns:root.narrowMode?1:3;columnSpacing:10;rowSpacing:10
                        Label{text:"Test profile";color:theme.mutedText}
AppTextField{id:testProfile;accessibleName:"Test profile";Layout.fillWidth:true;placeholderText:"quick, full…"}
AppButton{text:"Discover tests";variant:"primary";onClicked:root.refreshTestCatalog()}
                        Label{text:"Search";color:theme.mutedText}
AppTextField{id:testSearch;accessibleName:"Search discovered tests";accessibleDescription:"Filter by test identifier or suite";Layout.fillWidth:true;placeholderText:"Filter test IDs and suites"}
Label{text:root.selectedCount()+" selected";color:theme.info}
                    }
                    Label{visible:root.catalogMessage!=="";Layout.fillWidth:true;text:root.catalogMessage;color:theme.mutedText;wrapMode:Text.Wrap}
                    Rectangle{visible:root.needsTests();Layout.fillWidth:true;Layout.preferredHeight:Math.min(300,Math.max(120,testList.contentHeight+16));color:theme.recessedSurface;radius:theme.controlRadius
                        ListView{id:testList;anchors.fill:parent;anchors.margins:8;clip:true;spacing:4;model:root.filteredTests();reuseItems:true
                            delegate:RowLayout{required property var modelData;width:testList.width;height:Math.max(48,implicitHeight);AppCheckBox{accessibleName:"Select test "+String(modelData.label||modelData.id);checked:!!root.selectedTests[modelData.id];onToggled:root.updateSelectedTest(modelData.id,checked)}
ColumnLayout{Layout.fillWidth:true;Label{Layout.fillWidth:true;text:modelData.label||modelData.id;color:theme.text;elide:Text.ElideMiddle}
Label{Layout.fillWidth:true;text:modelData.suite||"";color:theme.subtleText;elide:Text.ElideMiddle}}}
                        }
                    }
                    GridLayout{visible:root.needsTests();Layout.fillWidth:true;columns:root.narrowMode?1:3;columnSpacing:10;rowSpacing:10
                        Label{text:"Selectors";color:theme.mutedText}
AppTextField{id:testSelectors;accessibleName:"Test selectors";Layout.fillWidth:true;placeholderText:"Selected IDs or manual selectors"}
AppButton{text:"Clear selection";variant:"ghost";onClicked:{root.selectedTests=({});testSelectors.text=""}}
                        Label{text:"Tags";color:theme.mutedText}
AppTextField{id:testTags;Layout.fillWidth:true}
AppTextField{id:testExcludeTags;placeholderText:"Exclude tags"}
                        Label{text:"Path / class / method";color:theme.mutedText}
AppTextField{id:testPath;Layout.fillWidth:true}
RowLayout{AppTextField{id:testClass;Layout.fillWidth:true;placeholderText:"Class"}
AppTextField{id:testMethod;Layout.fillWidth:true;placeholderText:"Method"}}
                        Label{text:"Instrumentation";color:theme.mutedText}
RowLayout{AppCheckBox{id:listTests;text:"List matching tests"}
AppCheckBox{id:instrumented;text:"Instrumentation"}}
Item{}
                    }
                }
            }

            SurfaceCard { Layout.fillWidth:true;Layout.leftMargin:theme.pageMargin;Layout.rightMargin:theme.pageMargin;visible:root.currentStage===3;implicitHeight:reviewColumn.implicitHeight+32
                ColumnLayout{id:reviewColumn;anchors.fill:parent;anchors.margins:16;spacing:12
                    Label{text:"Review and run";color:theme.windowText;font.pointSize:theme.cardTitlePointSize;font.weight:Font.DemiBold}
                    Label{Layout.fillWidth:true;text:"Confirm the workflow shape first. Output routing, environment flags, and exact command details are still available below.";color:theme.mutedText;wrapMode:Text.Wrap}
                    Label{text:"External terminal execution";color:theme.windowText;font.weight:Font.DemiBold}
                    Label{Layout.fillWidth:true;text:"Running opens the reviewed command in " + (appController.preferredTerminal ? appController.preferredTerminal.split("/").pop() : "your preferred terminal") + ". Devtool output remains visible there while the Control Center tracks progress.";color:theme.mutedText;wrapMode:Text.Wrap}
                    RowLayout{visible:root.isApplyOperation();AppCheckBox{id:validate;text:"Validate";checked:true}
AppCheckBox{id:commit;text:"Commit";checked:true}
AppCheckBox{id:keep;text:"Keep artifact"}
Item{Layout.fillWidth:true}}
                    RowLayout {
                        Layout.fillWidth: true
                        AppButton{text:root.commandPreviewOpen?"Hide exact command":"Show exact command";variant:"ghost";onClicked:root.commandPreviewOpen=!root.commandPreviewOpen}
                        AppButton{text:root.reviewAdvancedOpen?"Hide advanced output":"Show advanced output";variant:"ghost";onClicked:root.reviewAdvancedOpen=!root.reviewAdvancedOpen}
                        Item { Layout.fillWidth: true }
                    }
                    GridLayout{visible:root.reviewAdvancedOpen;Layout.fillWidth:true;columns:root.narrowMode?1:3;columnSpacing:10;rowSpacing:10
                        Label{text:"Clipboard";color:theme.mutedText}
AppComboBox{id:copyMode;accessibleName:"Clipboard result mode";Layout.fillWidth:true;model:["Copy summary","Copy JSON","Do not copy"]}
Item{}
                        Label{text:"HUD mode";color:theme.mutedText}
AppComboBox{id:hudMode;accessibleName:"HUD mode";Layout.fillWidth:true;model:["control-center","desktop-window","android-bridge","none"];enabled:autoHud.checked}
AppCheckBox{id:autoHud;text:"Auto HUD";checked:true}
                        Label{text:"Environment";color:theme.mutedText}
AppTextField{id:environmentAssignments;accessibleName:"Environment assignments";Layout.fillWidth:true;placeholderText:"NAME=value"}
Item{}
                        Label{text:"Extra global args";color:theme.mutedText}
AppTextField{id:extraGlobalArguments;accessibleName:"Additional global arguments";Layout.fillWidth:true;placeholderText:"Flags before the subcommand"}
Item{}
                        Label{text:"Extra operation args";color:theme.mutedText}
AppTextField{id:extraOperationArguments;accessibleName:"Additional operation arguments";Layout.fillWidth:true;placeholderText:"Arguments after the subcommand"}
Item{}
                    }
                    GridLayout{Layout.fillWidth:true;columns:root.narrowMode?1:3;columnSpacing:10;rowSpacing:10
                        Label{text:"Danger zone";color:root.isDangerousOperation()?theme.warning:theme.mutedText}
AppCheckBox{id:yes;text:"Non-interactive --yes";checked:false}
Label{text:root.isDangerousOperation()?root.dangerExplanation():"This operation is not expected to modify repository state.";color:root.isDangerousOperation()?theme.warning:theme.subtleText;wrapMode:Text.Wrap}
                    }
                    Label{visible:root.commandPreviewOpen;text:"Exact command";color:theme.windowText;font.weight:Font.DemiBold}
                    Rectangle{visible:root.commandPreviewOpen;Layout.fillWidth:true;implicitHeight:120;color:theme.recessedSurface;radius:theme.controlRadius;Label{anchors.fill:parent;anchors.margins:12;text:root.commandText();color:theme.mutedText;font.family:theme.monospaceFamily;wrapMode:Text.WrapAnywhere}}
                    RowLayout {
                        AppButton{text:"Copy command";onClicked:appController.copyText(root.commandText())}
                        AppButton {
                            text: "Open HUD"
                            variant: "ghost"
                            onClicked: appController.launchHud()
                        }
                        Item { Layout.fillWidth: true }
                    }
                }
            }
            Item{height:20}
        }
    }

    Rectangle {
        id:stickyBar;anchors.left:parent.left;anchors.right:parent.right;anchors.bottom:parent.bottom;height:root.narrowMode?88:72;color:theme.elevatedSurface;border.color:theme.border;border.width:1
        RowLayout{anchors.fill:parent;anchors.leftMargin:theme.pageMargin;anchors.rightMargin:theme.pageMargin;spacing:10
            AppButton{text:"Back";accessibleDescription:"Go to the previous workflow step. Keyboard shortcut Alt+Left.";variant:"ghost";enabled:root.currentStage>0;onClicked:root.currentStage=Math.max(0,root.currentStage-1)}
            Label{text:"Step "+(root.visibleIndexForStage(root.currentStage)+1)+" of "+root.visibleStepCount()+" · "+root.stageName(root.currentStage);color:theme.mutedText}
            Item{Layout.fillWidth:true}
            AppButton{visible:root.currentStage<3;text:root.currentStage===1&&!root.needsTests()?"Skip tests":"Next";accessibleDescription:"Go to the next workflow step. Keyboard shortcut Alt+Right.";variant:"primary";onClicked:root.currentStage=Math.min(3,root.currentStage+(root.currentStage===1&&!root.needsTests()?2:1))}
            AppButton{visible:root.currentStage===3;text:root.narrowMode?"Run":"Open in "+(appController.preferredTerminal?appController.preferredTerminal.split("/").pop():"terminal");accessibleName:"Run devtool workflow";accessibleDescription:"Run the reviewed workflow. Keyboard shortcut Control+Enter.";variant:"primary";enabled:root.canRun();onClicked:root.requestRun()}
        }
    }

    Shortcut { sequence: "Alt+Left"; enabled: root.currentStage > 0; onActivated: root.currentStage = Math.max(0, root.currentStage - 1) }
    Shortcut { sequence: "Alt+Right"; enabled: root.currentStage < 3; onActivated: root.currentStage = Math.min(3, root.currentStage + (root.currentStage === 1 && !root.needsTests() ? 2 : 1)) }
    Shortcut { sequence: "Ctrl+Return"; enabled: root.currentStage === 3 && root.canRun(); onActivated: root.requestRun() }
    Shortcut { sequence: "Ctrl+F"; enabled: root.currentStage === 2 && root.needsTests(); onActivated: testSearch.forceActiveFocus() }
}
