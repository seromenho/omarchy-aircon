import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "aircon"
  ipcTarget: "aircon"

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property color modeColor: themeColors.modeColor(ac.mode)

  readonly property var modeLabels: ({ auto: "Auto", cool: "Cool", heat: "Heat", fan: "Fan", dry: "Dry" })
  readonly property var fanLabels: ({ low: "Low", mid: "Medium", high: "High", auto: "Auto" })
  readonly property var swingLabels: ({ off: "Off", vertical: "Vertical", horizontal: "Horizontal", both: "Both" })
  // Nerd Font glyphs: Font Awesome plus Material Design for the fan.
  readonly property var modeGlyphs: ({ auto: "\uf021", cool: "\uf2dc", heat: "\uf185", fan: "󰈐", dry: "\uf043" })
  readonly property string glyph: modeGlyphs[ac.mode] || "\uf2dc"

  readonly property string roomText: {
    if (!ac.configured) return "Not configured"
    var parts = []
    if (ac.roomTemp !== null) parts.push(ac.roomTemp + "°C")
    if (ac.roomHumidity !== null) parts.push(ac.roomHumidity + "%")
    return parts.length ? "Room " + parts.join(" · ") : "Room —"
  }

  readonly property bool needsInstall: !ac.installed

  readonly property string statusText: {
    if (needsInstall) return ""
    if (!ac.serviceUp) return "Service stopped — run: systemctl --user start aircond"
    if (!ac.configured) return "Not configured"
    if (!ac.connected) return ac.lastError || "Air conditioner unreachable"
    return "Connected to " + ac.host
  }

  function options(labels, allowed) {
    var out = []
    for (var i = 0; i < (allowed || []).length; i++) {
      out.push({ value: allowed[i], label: labels[allowed[i]] || allowed[i] })
    }
    return out
  }

  function setTemp(value) {
    var target = ac.clampTemp(value)
    if (target === ac.targetTemp) return
    ac.run(["set", "--temp", String(target)], { target_temp: target, power: true })
  }

  // "device" shows the selected unit, "settings" its settings, "add" the in-panel setup.
  property string view: "device"
  property bool timerOpen: false
  property bool confirmRemove: false
  readonly property bool showSetup: ac.serviceUp && (!ac.configured || view === "add")
  readonly property bool showSettings: ac.configured && !showSetup && view === "settings"
  readonly property bool showDevice: ac.configured && !showSetup && view === "device"
  readonly property string badge: ac.onCount > 1 ? " ·" + ac.onCount : ""

  readonly property string timerSummary: {
    var keys = Object.keys(ac.timers)
    if (keys.length === 0) return "Timer"
    if (keys.length > 1) return keys.length + " timers"
    return (keys[0] === "on" ? "On at " : "Off at ") + clock(ac.timers[keys[0]])
  }

  function finishInstall() {
    Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", ac.installScript])
    root.close()
  }

  function launchSetup() {
    Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", ac.cli + " setup"])
    root.close()
  }

  function clock(iso) {
    return iso ? String(iso).substring(11, 16) : ""
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    if (panelFlick) panelFlick.contentY = 0
    confirmRemove = false
    timerOpen = false
    if (view === "settings") view = "device"
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Service { id: ac }

  QtObject {
    id: actions
    property color modeColor: root.modeColor
    function openSettings() { root.view = "settings" }
    function togglePower() { ac.run(["toggle"], { power: !ac.power }) }
  }
  // Exposed so child components with their own `ac` property can be handed the service.
  readonly property alias service: ac
  AcPalette { id: themeColors }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.needsInstall ? "\uf019"
          : ac.serviceUp && !ac.configured ? "\uf013"
          : !ac.connected ? "\uf071"
          : (ac.power ? root.glyph + " " + ac.targetTemp + "°" : root.glyph + " off") + root.badge
    foreground: root.needsInstall || (ac.serviceUp && !ac.configured)
                ? Qt.alpha(root.barForeground, 0.7)
              : !ac.connected ? root.urgent
                              : (ac.power ? root.modeColor : Qt.alpha(root.barForeground, 0.7))
    slotSize: Style.bar.iconSlot * ((ac.connected ? 2.4 : 1) + (root.badge !== "" ? 1 : 0))
    tooltipText: root.needsInstall ? "Air conditioner: click to finish installing"
               : ac.serviceUp && !ac.configured ? "Air conditioner: not configured — click to set up"
               : ac.connected
      ? (ac.power ? ac.deviceName + ": " + root.modeLabels[ac.mode] + " " + ac.targetTemp + "°C"
                  : ac.deviceName + ": off")
      : root.statusText
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton && ac.connected) ac.run(["toggle"], { power: !ac.power })
      else root.toggle()
    }
    onWheelMoved: function(delta) {
      if (ac.connected && ac.power) root.setTemp(ac.targetTemp + (delta > 0 ? 1 : -1))
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(400))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(760))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (!ac.connected) return
        if (t === "+" || t === "=") root.setTemp(ac.targetTemp + 1)
        else if (t === "-") root.setTemp(ac.targetTemp - 1)
        else if (t === "p" || t === "P") ac.run(["toggle"], { power: !ac.power })
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          // -- device tabs --------------------------------------------------------
          Flow {
            visible: ac.configured
            width: parent.width
            spacing: Style.space(6)
            Repeater {
              model: ac.devices
              Button {
                required property var modelData
                text: modelData.name
                iconText: modelData.connected ? (modelData.power ? root.modeGlyphs[modelData.mode] || "" : "\uf011") : "\uf071"
                bordered: true
                selected: root.view !== "add" && modelData.id === ac.deviceId
                foreground: root.foreground
                fontFamily: root.fontFamily
                fontSize: Style.font.bodySmall
                onClicked: {
                  ac.select(modelData.id)
                  root.view = "device"
                  root.confirmRemove = false
                }
              }
            }
            Button {
              text: "Add"
              iconText: "\uf067"
              bordered: true
              selected: root.view === "add"
              foreground: root.foreground
              fontFamily: root.fontFamily
              fontSize: Style.font.bodySmall
              onClicked: {
                root.view = "add"
                ac.discover()
              }
            }
          }

          // -- header -------------------------------------------------------------
          PanelHero {
            id: hero
            visible: root.showDevice
            width: parent.width
            title: ac.deviceName
            meta: root.roomText
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              Text {
                text: "\uf2c9"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }
            trailingControl: Component {
              Row {
                spacing: Style.space(8)
                PanelActionButton {
                  anchors.verticalCenter: parent.verticalCenter
                  iconText: "\uf013"
                  tooltipText: "Settings"
                  foreground: hero.foreground
                  fontFamily: hero.fontFamily
                  onClicked: actions.openSettings()
                }
                ToggleSwitch {
                  anchors.verticalCenter: parent.verticalCenter
                  checked: ac.power
                  interactive: ac.connected
                  foreground: hero.foreground
                  accent: actions.modeColor
                  onToggled: actions.togglePower()
                }
              }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(12)
            visible: root.showDevice

            AcIcon {
              width: parent.width
              height: implicitHeight
              foreground: root.foreground
              flowColor: root.modeColor
              running: ac.connected && ac.power
              swing: ac.swing
            }

            // -- temperature --------------------------------------------------------
            Column {
              width: parent.width
              spacing: Style.space(6)
              enabled: ac.connected
              opacity: enabled ? 1 : 0.5

              RowLayout {
                width: parent.width
                spacing: Style.space(4)

                Text {
                  text: ac.targetTemp
                  color: ac.power ? root.foreground : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.displayLarge * 2
                  font.weight: Font.Light
                  Layout.alignment: Qt.AlignBottom
                }
                Text {
                  text: "°C"
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.heading
                  Layout.alignment: Qt.AlignBottom
                  Layout.bottomMargin: Style.space(10)
                }
                Item { Layout.fillWidth: true }
                Text {
                  text: ac.power ? root.glyph + "  " + root.modeLabels[ac.mode] : "Off"
                  color: ac.power ? root.modeColor : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.title
                  Layout.alignment: Qt.AlignBottom
                  Layout.bottomMargin: Style.space(12)
                }
              }

              RowLayout {
                width: parent.width
                spacing: Style.space(10)

                PanelActionButton {
                  iconText: "\uf068"
                  tooltipText: "Cooler"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  bordered: true
                  onClicked: root.setTemp(ac.targetTemp - 1)
                }
                PanelSlider {
                  id: tempSlider
                  Layout.fillWidth: true
                  bar: root.bar
                  minimum: ac.caps.temp_min
                  maximum: ac.caps.temp_max
                  step: ac.caps.temp_step || 1
                  integer: true
                  value: ac.targetTemp
                  fillColor: themeColors.mix(themeColors.cold, themeColors.hot, progress)
                  knobColor: root.foreground
                  onReleased: function(v) { root.setTemp(v) }
                }
                PanelActionButton {
                  iconText: "\uf067"
                  tooltipText: "Warmer"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  bordered: true
                  onClicked: root.setTemp(ac.targetTemp + 1)
                }
              }

              RowLayout {
                width: parent.width
                Text {
                  text: ac.caps.temp_min + "°"
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  Layout.leftMargin: Style.space(40)
                }
                Item { Layout.fillWidth: true }
                Text {
                  text: ac.caps.temp_max + "°"
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  Layout.rightMargin: Style.space(40)
                }
              }
            }

            // -- tiles --------------------------------------------------------------
            GridLayout {
              width: parent.width
              columns: 2
              columnSpacing: Style.space(8)
              rowSpacing: Style.space(8)
              enabled: ac.connected
              opacity: enabled ? 1 : 0.5

              Tile {
                glyph: root.glyph
                label: "Mode"
                accentColor: root.modeColor
                Dropdown {
                  width: parent.width
                  showLabel: false
                  value: ac.mode
                  options: root.options(root.modeLabels, ac.caps.modes)
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onChanged: function(v) { ac.run(["set", "--mode", v], { mode: v, power: true }) }
                }
              }

              Tile {
                glyph: "󰈐"
                label: "Fan speed"
                Dropdown {
                  width: parent.width
                  showLabel: false
                  value: ac.fan
                  options: root.options(root.fanLabels, ac.caps.fan_speeds)
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onChanged: function(v) { ac.run(["set", "--fan", v], { fan: v, power: true }) }
                }
              }

              Tile {
                glyph: "\uf07d"
                label: "Airflow"
                Dropdown {
                  width: parent.width
                  showLabel: false
                  value: ac.swing
                  options: root.options(root.swingLabels, ac.caps.swing)
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onChanged: function(v) { ac.run(["set", "--swing", v], { swing: v, power: true }) }
                }
              }

              Tile {
                glyph: "\uf186"
                label: "Sleep"
                RowLayout {
                  width: parent.width
                  Text {
                    Layout.fillWidth: true
                    text: ac.sleepCurve
                      ? ac.sleepCurve.target + "°C · until " + root.clock(ac.sleepCurve.ends_at)
                      : "Off"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    elide: Text.ElideRight
                  }
                  ToggleSwitch {
                    checked: ac.sleepCurve !== null
                    foreground: root.foreground
                    accent: root.modeColor
                    cursorRing: false
                    onToggled: {
                      if (ac.sleepCurve) ac.run(["sleep-curve", "cancel"], { sleep_curve: null })
                      else ac.run(["sleep-curve", "start"])
                    }
                  }
                }
              }
            }

            Flow {
              width: parent.width
              spacing: Style.space(6)

              Button {
                text: root.timerSummary
                iconText: "\uf017"
                bordered: true
                selected: root.timerOpen || Object.keys(ac.timers).length > 0
                foreground: root.foreground
                fontFamily: root.fontFamily
                fontSize: Style.font.bodySmall
                onClicked: root.timerOpen = !root.timerOpen
              }
              Button {
                visible: ac.hasFeature("health")
                text: "Health"
                iconText: "\uf06c"
                bordered: true
                selected: ac.health
                foreground: root.foreground
                fontFamily: root.fontFamily
                fontSize: Style.font.bodySmall
                onClicked: ac.run(["set", "--health", ac.health ? "off" : "on"], { health: !ac.health })
              }
              Button {
                visible: ac.filter.due
                text: "Clean filter"
                iconText: "\uf071"
                bordered: true
                foreground: root.urgent
                fontFamily: root.fontFamily
                fontSize: Style.font.bodySmall
                onClicked: root.view = "settings"
              }
            }

            Column {
              visible: root.timerOpen
              width: parent.width
              spacing: Style.space(8)

              Repeater {
                model: Object.keys(ac.timers)
                RowLayout {
                  required property string modelData
                  width: column.width
                  Text {
                    Layout.fillWidth: true
                    text: "\uf017  Power " + modelData + " at " + root.clock(ac.timers[modelData])
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                  }
                  PanelActionButton {
                    iconText: "\uf00d"
                    tooltipText: "Cancel"
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    onClicked: ac.run(["timer", "cancel", modelData])
                  }
                }
              }

              Flow {
                width: parent.width
                spacing: Style.space(6)
                enabled: ac.connected
                Repeater {
                  model: [["30m", "30 min"], ["1h", "1 h"], ["2h", "2 h"], ["4h", "4 h"], ["8h", "8 h"]]
                  Button {
                    required property var modelData
                    text: "Off in " + modelData[1]
                    bordered: true
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    fontSize: Style.font.bodySmall
                    onClicked: ac.run(["timer", "off", "--in", modelData[0]])
                  }
                }
              }

              RowLayout {
                width: parent.width
                spacing: Style.space(6)
                enabled: ac.connected
                TextField {
                  id: onAtField
                  Layout.preferredWidth: Style.space(90)
                  placeholderText: "07:30"
                  foreground: root.foreground
                  font.family: root.fontFamily
                }
                Button {
                  text: "Power on at this time"
                  bordered: true
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  fontSize: Style.font.bodySmall
                  onClicked: {
                    if (/^\d\d:\d\d$/.test(onAtField.text)) ac.run(["timer", "on", "--at", onAtField.text])
                    else ac.commandError = "Enter the time as HH:MM"
                  }
                }
              }
            }

          }

          // -- settings -------------------------------------------------------------
          Column {
            width: parent.width
            spacing: Style.space(12)
            visible: root.showSettings

            RowLayout {
              width: parent.width
              spacing: Style.space(8)
              PanelActionButton {
                iconText: "\uf060"
                tooltipText: "Back"
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.view = "device"
              }
              Text {
                Layout.fillWidth: true
                text: ac.deviceName + " · Settings"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.title
                elide: Text.ElideRight
              }
            }

            // -- device -------------------------------------------------------------
            PanelSectionHeader {
              text: "DEVICE"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }
            RowLayout {
              width: parent.width
              spacing: Style.space(6)
              TextField {
                id: renameField
                Layout.fillWidth: true
                placeholderText: "New name for " + ac.deviceName
                foreground: root.foreground
                font.family: root.fontFamily
                onAccepted: renameButton.clicked()
              }
              Button {
                id: renameButton
                text: "Rename"
                bordered: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                fontSize: Style.font.bodySmall
                onClicked: {
                  var name = renameField.text.trim()
                  if (name === "") return
                  ac.enqueue(["rename", ac.deviceId, name])
                  renameField.text = ""
                }
              }
            }

            Button {
              text: root.confirmRemove ? "Click again to remove " + ac.deviceName : "Remove this air conditioner"
              iconText: "\uf1f8"
              bordered: true
              foreground: root.confirmRemove ? root.urgent : root.foreground
              fontFamily: root.fontFamily
              fontSize: Style.font.bodySmall
              onClicked: {
                if (root.confirmRemove) {
                  ac.enqueue(["remove", ac.deviceId])
                  root.confirmRemove = false
                } else {
                  root.confirmRemove = true
                  confirmTimer.restart()
                }
              }
            }

            PanelSeparator { foreground: root.foreground }

            // -- filter -------------------------------------------------------------
            PanelSectionHeader {
              text: "FILTER"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            RowLayout {
              width: parent.width
              Text {
                Layout.fillWidth: true
                text: ac.filter.due
                  ? "\uf0b0  Clean the filter (" + Math.round(ac.filter.hours) + " h of use)"
                  : "\uf0b0  " + Math.round(ac.filter.hours) + " h of " + ac.filter.threshold + " h"
                color: ac.filter.due ? root.urgent : root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                elide: Text.ElideRight
              }
              Button {
                text: "Cleaned"
                bordered: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                fontSize: Style.font.bodySmall
                onClicked: ac.run(["filter", "reset"], { filter: { hours: 0, threshold: ac.filter.threshold, due: false } })
              }
            }

            PanelSeparator { foreground: root.foreground }

            // -- diagnostics ----------------------------------------------------------
            PanelSectionHeader {
              text: "DIAGNOSTICS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            InfoPair { label: "Connection"; value: ac.connected ? "OK" : (ac.serviceUp ? "Unreachable" : "Service stopped") }
            InfoPair { label: "Device"; value: ac.host + " · " + ac.backendName }
            InfoPair { label: "Alarm"; value: ac.alarm || "None" }
            InfoPair { label: "Last update"; value: root.clock(ac.updatedAt) || "—" }

            PanelSeparator { foreground: root.foreground }

            Button {
              text: "Run setup in a terminal"
              iconText: "\uf120"
              foreground: root.foreground
              fontFamily: root.fontFamily
              fontSize: Style.font.caption
              onClicked: root.launchSetup()
            }
          }

          // -- finish install (right after `omarchy plugin add`) --------------------
          Column {
            width: parent.width
            spacing: Style.space(12)
            visible: root.needsInstall

            PanelSectionHeader {
              text: "FINISH INSTALLING"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }
            Text {
              width: parent.width
              wrapMode: Text.WordWrap
              text: "aircon needs a small background service to talk to your air conditioner. "
                    + "Installing it takes a few seconds and needs no password."
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }
            Button {
              text: "Finish install"
              iconText: "\uf019"
              bordered: true
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.finishInstall()
            }
          }

          // -- setup (first run and "Add") -----------------------------------------
          SetupView {
            width: parent.width
            visible: root.showSetup
            ac: root.service
            firstRun: !ac.configured
            foreground: root.foreground
            urgent: root.urgent
            fontFamily: root.fontFamily
            onFinished: root.view = "device"
            onTerminalRequested: root.launchSetup()
          }

          // -- footer -------------------------------------------------------------
          Text {
            visible: ac.commandError !== "" || (ac.installed && !ac.serviceUp)
                     || (ac.configured && !ac.connected)
            width: parent.width
            text: ac.commandError !== "" ? ac.commandError : root.statusText
            color: ac.commandError !== "" || (ac.configured && !ac.connected) ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }
        }
      }
    }
  }

  Timer {
    id: confirmTimer
    interval: 4000
    onTriggered: root.confirmRemove = false
  }

  component Tile: Rectangle {
    id: tile
    property string glyph: ""
    property string label: ""
    property color accentColor: root.foreground
    default property alias content: body.data

    Layout.fillWidth: true
    implicitHeight: tileColumn.implicitHeight + Style.space(20)
    radius: Style.cornerRadius
    color: Style.hoverFillFor(root.foreground, Color.accent)
    border.width: Style.normalBorderWidth
    border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.15)

    Column {
      id: tileColumn
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.margins: Style.space(10)
      spacing: Style.space(6)

      Row {
        spacing: Style.space(6)
        Text {
          text: tile.glyph
          color: tile.accentColor
          font.family: root.fontFamily
          font.pixelSize: Style.font.icon
        }
        Text {
          text: tile.label
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          anchors.verticalCenter: parent.verticalCenter
        }
      }

      Item {
        id: body
        width: parent.width
        implicitHeight: childrenRect.height
        height: implicitHeight
      }
    }
  }

  component InfoPair: RowLayout {
    property string label: ""
    property string value: ""
    width: column.width

    Text {
      text: parent.label
      color: root.foreground
      opacity: 0.6
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
    Item { Layout.fillWidth: true }
    Text {
      text: parent.value
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
      Layout.maximumWidth: column.width * 0.65
    }
  }
}
