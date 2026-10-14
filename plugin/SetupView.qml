import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// In-panel flow to add an air conditioner: search, pick, name, optionally test, save.
Column {
  id: view

  property var ac: null
  property color foreground: Color.foreground
  property color urgent: Color.urgent
  property string fontFamily: Style.font.family
  property bool firstRun: false

  signal finished()
  signal terminalRequested()

  property var chosen: null
  property bool manual: false
  property bool testFeatures: false
  property bool manualV3: false
  readonly property bool isMidea: ac && ac.setupBackend === "midea_lan"
  readonly property bool isV3: chosen !== null && chosen.options
    ? chosen.options.protocol_version === 3 : manualV3

  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property var available: (ac ? ac.candidates : []).filter(function(c) {
    return !c.configured && (c.backend === "midea_lan" || (c.state && c.mac))
  })
  readonly property bool canSave: ac && !ac.saving && nameField.text.trim() !== ""
    && (chosen !== null || (manual && hostField.text.trim() !== ""
        && (isMidea ? deviceIdField.text.trim() !== "" : macField.text.trim() !== "")))
    && (!isMidea || !isV3 || optionsFileField.text.trim() !== "")

  spacing: Style.space(10)

  function reset() {
    chosen = null
    manual = false
    testFeatures = false
    nameField.text = ""
    hostField.text = ""
    macField.text = ""
    deviceIdField.text = ""
    optionsFileField.text = ""
    manualV3 = false
  }

  function chooseBrand(backend) {
    reset()
    ac.setupBackend = backend
    ac.discover()
  }

  function describe(c) {
    if (c.backend === "midea_lan")
      return "Midea · " + c.host + " · V" + c.options.protocol_version + " · ID " + c.options.device_id
    var s = c.state
    var status = !s ? "no answer" : (s.power ? s.mode + " · " + s.target_temp + "°C" : "off")
    if (s && s.room_temp !== null && s.room_temp !== undefined) status += " · room " + s.room_temp + "°C"
    return (c.is_haier ? "Haier" : "Unknown") + " · " + c.host + " · " + status
  }

  function save() {
    if (!canSave) return
    var hostAddress = chosen ? chosen.host : hostField.text.trim()
    var mac = chosen ? chosen.mac : macField.text.trim()
    var midea = isMidea ? {
      deviceId: chosen ? chosen.options.device_id : deviceIdField.text.trim(),
      version: isV3 ? 3 : 2,
      port: chosen ? chosen.port : 6444,
      optionsFile: optionsFileField.text.trim()
    } : null
    ac.addDevice(hostAddress, mac, nameField.text.trim(), testFeatures, midea)
  }

  onVisibleChanged: if (visible && ac && !ac.discovering && !ac.discovered) ac.discover()

  Connections {
    target: view.ac
    function onSavedIdChanged() {
      if (view.ac.savedId !== "") {
        view.reset()
        view.finished()
      }
    }
  }

  PanelSectionHeader {
    text: view.firstRun ? "SET UP YOUR AIR CONDITIONER" : "ADD AIR CONDITIONER"
    foreground: view.foreground
    fontFamily: view.fontFamily
  }

  Text {
    visible: view.firstRun
    width: parent.width
    wrapMode: Text.WordWrap
    text: "Choose your brand, then pick an air conditioner found on the local network."
    color: view.foreground
    font.family: view.fontFamily
    font.pixelSize: Style.font.body
  }

  Flow {
    width: parent.width
    spacing: Style.space(6)
    Repeater {
      model: [
        { backend: "midea_lan", label: "Midea / NetHome Plus" },
        { backend: "haier_smartair2", label: "Haier / SmartAir2" }
      ]
      Button {
        required property var modelData
        text: modelData.label
        bordered: true
        selected: view.ac.setupBackend === modelData.backend
        enabled: !view.ac.discovering && !view.ac.saving
        foreground: view.foreground
        fontFamily: view.fontFamily
        fontSize: Style.font.bodySmall
        onClicked: view.chooseBrand(modelData.backend)
      }
    }
  }

  Text {
    visible: view.ac.discovering
    text: "  Searching the local network…"
    color: view.dim
    font.family: view.fontFamily
    font.pixelSize: Style.font.body
  }

  Repeater {
    model: view.ac.discovering ? [] : view.ac.candidates
    Button {
      required property var modelData
      width: view.width
      leftAlign: true
      bordered: true
      enabled: !modelData.configured && (modelData.backend === "midea_lan"
               || (!!modelData.state && !!modelData.mac))
      selected: view.chosen !== null && view.chosen.host === modelData.host
      text: view.describe(modelData) + (modelData.configured ? "  (already added)" : "")
      foreground: view.foreground
      fontFamily: view.fontFamily
      fontSize: Style.font.bodySmall
      onClicked: {
        view.chosen = modelData
        view.manual = false
      }
    }
  }

  Text {
    visible: view.ac.discovered && !view.ac.discovering && view.available.length === 0
    width: parent.width
    wrapMode: Text.WordWrap
    text: view.isMidea
      ? "No new Midea air conditioner found. Pair it with NetHome Plus or Midea Air, "
        + "make sure it is on this network and plugged in, then search again or enter its address."
      : "No new Haier air conditioner found. Pair its Wi-Fi module with HaierSmartAir2, "
        + "make sure it is on this network and plugged in, then search again or enter its address."
    color: view.dim
    font.family: view.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  Flow {
    visible: view.ac.setupBackend !== ""
    width: parent.width
    spacing: Style.space(6)
    Button {
      text: "Search again"
      iconText: ""
      bordered: true
      enabled: !view.ac.discovering
      foreground: view.foreground
      fontFamily: view.fontFamily
      fontSize: Style.font.bodySmall
      onClicked: {
        view.chosen = null
        view.ac.discover()
      }
    }
    Button {
      text: "Enter address"
      iconText: ""
      bordered: true
      selected: view.manual
      foreground: view.foreground
      fontFamily: view.fontFamily
      fontSize: Style.font.bodySmall
      onClicked: {
        view.manual = !view.manual
        if (view.manual) view.chosen = null
      }
    }
  }

  RowLayout {
    visible: view.manual
    width: parent.width
    spacing: Style.space(6)
    TextField {
      id: hostField
      Layout.fillWidth: true
      placeholderText: "IP, e.g. 192.168.1.50"
      foreground: view.foreground
      font.family: view.fontFamily
    }
    TextField {
      id: macField
      visible: !view.isMidea
      Layout.fillWidth: true
      placeholderText: "MAC on the module label"
      foreground: view.foreground
      font.family: view.fontFamily
    }
    TextField {
      id: deviceIdField
      visible: view.isMidea
      Layout.fillWidth: true
      placeholderText: "Midea device ID"
      foreground: view.foreground
      font.family: view.fontFamily
    }
  }

  Column {
    visible: view.chosen !== null || view.manual
    width: parent.width
    spacing: Style.space(8)

    TextField {
      id: nameField
      width: parent.width
      placeholderText: "Name, e.g. Bedroom"
      foreground: view.foreground
      font.family: view.fontFamily
      onAccepted: view.save()
    }

    Toggle {
      visible: view.isMidea && view.manual
      width: parent.width
      label: "Requires a token and key (V3)"
      checked: view.manualV3
      foreground: view.foreground
      fontFamily: view.fontFamily
      onClicked: view.manualV3 = !view.manualV3
    }

    TextField {
      id: optionsFileField
      visible: view.isMidea
      width: parent.width
      placeholderText: "Token/key TOML file path (required for V3)"
      foreground: view.foreground
      font.family: view.fontFamily
    }

    Text {
      visible: view.isMidea && view.isV3
      width: parent.width
      wrapMode: Text.WordWrap
      text: "Save your local token and key in a private TOML file, then enter its path above. "
            + "See the Midea setup guide for pairing and credentials."
      color: view.dim
      font.family: view.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Toggle {
      visible: !view.isMidea
      width: parent.width
      label: "Test optional features"
      description: "Checks left/right swing, health and sleep. Changes the unit for about a minute, then restores it."
      checked: view.testFeatures
      foreground: view.foreground
      fontFamily: view.fontFamily
      onClicked: view.testFeatures = !view.testFeatures
    }

    Flow {
      width: parent.width
      spacing: Style.space(6)
      Button {
        text: view.ac.saving ? (view.testFeatures ? "Testing… about a minute" : "Saving…") : "Add"
        iconText: ""
        bordered: true
        enabled: view.canSave
        foreground: view.foreground
        fontFamily: view.fontFamily
        onClicked: view.save()
      }
      Button {
        visible: !view.firstRun
        text: "Cancel"
        bordered: true
        enabled: !view.ac.saving
        foreground: view.foreground
        fontFamily: view.fontFamily
        onClicked: {
          view.reset()
          view.finished()
        }
      }
    }
  }

  Text {
    visible: view.ac.setupError !== ""
    width: parent.width
    wrapMode: Text.WordWrap
    text: view.ac.setupError
    color: view.urgent
    font.family: view.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  Button {
    text: "Set up in a terminal instead"
    iconText: ""
    foreground: view.foreground
    fontFamily: view.fontFamily
    fontSize: Style.font.caption
    onClicked: view.terminalRequested()
  }
}
