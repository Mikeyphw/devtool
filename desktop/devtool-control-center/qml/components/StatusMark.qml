import QtQuick

Item {
    id: root
    property string kind: "pending"
    property color markColor: "white"
    property real markSize: 14
    width: markSize
    height: markSize
    Accessible.ignored: true

    Canvas {
        id: canvas
        anchors.fill: parent
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            ctx.strokeStyle = root.markColor
            ctx.fillStyle = root.markColor
            ctx.lineWidth = Math.max(1.6, width * 0.14)
            ctx.lineCap = "round"
            ctx.lineJoin = "round"
            var k = root.kind
            if (k === "ok" || k === "success" || k === "done" || k === "recovered") {
                ctx.beginPath(); ctx.moveTo(width * 0.12, height * 0.52); ctx.lineTo(width * 0.40, height * 0.80); ctx.lineTo(width * 0.88, height * 0.18); ctx.stroke()
            } else if (k === "failed" || k === "critical" || k === "rollback-failed") {
                ctx.beginPath(); ctx.moveTo(width * 0.22, height * 0.22); ctx.lineTo(width * 0.78, height * 0.78); ctx.moveTo(width * 0.78, height * 0.22); ctx.lineTo(width * 0.22, height * 0.78); ctx.stroke()
            } else if (k === "rollback") {
                ctx.beginPath(); ctx.arc(width * 0.55, height * 0.55, width * 0.32, Math.PI * 0.15, Math.PI * 1.65, true); ctx.stroke()
                ctx.beginPath(); ctx.moveTo(width * 0.10, height * 0.48); ctx.lineTo(width * 0.30, height * 0.28); ctx.lineTo(width * 0.33, height * 0.56); ctx.fill()
            } else if (k === "running") {
                ctx.beginPath(); ctx.arc(width / 2, height / 2, width * 0.27, 0, Math.PI * 2); ctx.fill()
            } else if (k === "skipped") {
                ctx.beginPath(); ctx.moveTo(width * 0.18, height * 0.50); ctx.lineTo(width * 0.82, height * 0.50); ctx.stroke()
            } else {
                ctx.beginPath(); ctx.arc(width / 2, height / 2, width * 0.30, 0, Math.PI * 2); ctx.stroke()
            }
        }
        Connections {
            target: root
            function onKindChanged() { canvas.requestPaint() }
            function onMarkColorChanged() { canvas.requestPaint() }
            function onMarkSizeChanged() { canvas.requestPaint() }
        }
    }
}
