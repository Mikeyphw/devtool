import QtQuick

Item {
    id: root
    visible: false
    width: 0
    height: 0

    SystemPalette { id: activePalette; colorGroup: SystemPalette.Active }
    SystemPalette { id: disabledPalette; colorGroup: SystemPalette.Disabled }

    function luminance(color) {
        return color.r * 0.299 + color.g * 0.587 + color.b * 0.114
    }

    function mix(first, second, amount) {
        var t = Math.max(0, Math.min(1, amount))
        return Qt.rgba(
            first.r + (second.r - first.r) * t,
            first.g + (second.g - first.g) * t,
            first.b + (second.b - first.b) * t,
            first.a + (second.a - first.a) * t
        )
    }

    function alpha(color, opacity) {
        return Qt.rgba(color.r, color.g, color.b, opacity)
    }

    readonly property bool dark: luminance(activePalette.window) < 0.5
    readonly property color window: activePalette.window
    readonly property color windowText: activePalette.windowText
    readonly property color base: activePalette.base
    readonly property color alternateBase: activePalette.alternateBase
    readonly property color text: activePalette.text
    readonly property color button: activePalette.button
    readonly property color buttonText: activePalette.buttonText
    readonly property color highlight: activePalette.highlight
    readonly property color highlightedText: activePalette.highlightedText
    readonly property color disabledText: disabledPalette.text

    readonly property color sidebar: dark ? mix(window, base, 0.38) : mix(window, base, 0.58)
    readonly property color surface: dark ? mix(window, base, 0.55) : mix(window, base, 0.72)
    readonly property color elevatedSurface: dark ? mix(surface, button, 0.48) : mix(surface, button, 0.38)
    readonly property color recessedSurface: dark ? mix(base, window, 0.25) : mix(base, window, 0.15)
    readonly property color hoverSurface: mix(elevatedSurface, highlight, dark ? 0.12 : 0.08)
    readonly property color pressedSurface: mix(elevatedSurface, highlight, dark ? 0.23 : 0.16)
    readonly property color selectedSurface: mix(surface, highlight, dark ? 0.24 : 0.15)
    readonly property color border: alpha(windowText, dark ? 0.16 : 0.20)
    readonly property color strongBorder: alpha(windowText, dark ? 0.28 : 0.32)
    readonly property color subtleText: mix(windowText, window, dark ? 0.42 : 0.48)
    readonly property color mutedText: mix(windowText, window, dark ? 0.30 : 0.38)

    readonly property color accent: highlight
    readonly property color accentText: highlightedText
    readonly property color success: dark ? "#69d596" : "#187a46"
    readonly property color warning: dark ? "#f2bd66" : "#956000"
    readonly property color error: dark ? "#ff737c" : "#b4232c"
    readonly property color info: dark ? "#69cce3" : "#136f86"
    readonly property color violet: dark ? "#a99bff" : "#6753c7"
    readonly property color successSurface: mix(surface, success, dark ? 0.16 : 0.10)
    readonly property color warningSurface: mix(surface, warning, dark ? 0.14 : 0.09)
    readonly property color errorSurface: mix(surface, error, dark ? 0.16 : 0.09)
    readonly property color infoSurface: mix(surface, info, dark ? 0.14 : 0.09)

    readonly property real pageTitlePointSize: 22
    readonly property real cardTitlePointSize: 13
    readonly property real bodyPointSize: 10
    readonly property real smallPointSize: 9
    readonly property real captionPointSize: 8
    readonly property int controlHeight: 38
    readonly property int compactControlHeight: 32
    readonly property int cardRadius: 10
    readonly property int controlRadius: 7
    readonly property int pageMargin: 24
    readonly property int sectionSpacing: 16
}
