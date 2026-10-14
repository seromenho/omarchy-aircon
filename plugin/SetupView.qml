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
  signal mideaInstallRequested()

  property var chosen: null
  property bool manual: false
  property bool testFeatures: false
  property bool manualV3: false
  property bool useOptionsFile: false
  property bool showMideaGuide: false
  readonly property bool isMidea: ac && ac.setupBackend === "midea_lan"
  readonly property bool isV3: chosen !== null && chosen.options
    ? chosen.options.protocol_version === 3 : manualV3
  readonly property bool editing: nameField.activeFocus || hostField.activeFocus
    || macField.activeFocus || deviceIdField.activeFocus || optionsFileField.activeFocus
    || emailField.activeFocus || passwordField.activeFocus

  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property var available: (ac ? ac.candidates : []).filter(function(c) {
    return !c.configured && (c.backend === "midea_lan" || (c.state && c.mac))
  })
  readonly property bool canSave: ac && !ac.saving && nameField.text.trim() !== ""
    && (chosen !== null || (manual && hostField.text.trim() !== ""
        && (isMidea ? deviceIdField.text.trim() !== "" : macField.text.trim() !== "")))
    && (!isMidea || !isV3 || (useOptionsFile ? optionsFileField.text.trim() !== ""
        : emailField.text.trim() !== "" && passwordField.text !== ""))

  spacing: Style.space(10)

  function clearPassword() {
    passwordField.text = ""
  }

  function reset() {
    chosen = null
    manual = false
    testFeatures = false
    nameField.text = ""
    hostField.text = ""
    macField.text = ""
    deviceIdField.text = ""
    optionsFileField.text = ""
    emailField.text = ""
    passwordField.text = ""
    useOptionsFile = false
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
      optionsFile: useOptionsFile || !isV3 ? optionsFileField.text.trim() : "",
      credentials: isV3 && !useOptionsFile
        ? { email: emailField.text.trim(), password: passwordField.text } : null
    } : null
    ac.addDevice(hostAddress, mac, nameField.text.trim(), testFeatures, midea)
    passwordField.text = ""
  }

  onVisibleChanged: {
    if (!visible) passwordField.text = ""
    if (visible && ac && !ac.discovering && !ac.discovered) ac.discover()
  }

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
        { backend: "midea_lan", label: "Midea / SmartHome" },
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

  Button {
    visible: view.isMidea
    text: view.showMideaGuide ? "Hide SmartHome setup guide" : "SmartHome setup guide"
    iconText: ""
    foreground: view.foreground
    fontFamily: view.fontFamily
    fontSize: Style.font.bodySmall
    onClicked: view.showMideaGuide = !view.showMideaGuide
  }

  Column {
    visible: view.isMidea && view.showMideaGuide
    width: parent.width
    spacing: Style.space(6)
    Text {
      width: parent.width
      wrapMode: Text.WordWrap
      text: "1. Install SmartHome (formerly MSmartHome) on your phone.\n"
            + "2. Create a fresh account, select your country and set a password. "
            + "Add the AC directly; avoid account migration from NetHome Plus.\n"
            + "3. Confirm the AC works in SmartHome, then select it below and sign in. "
            + "The plugin retrieves and saves its local token/key automatically."
      color: view.foreground
      font.family: view.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
    Flow {
      width: parent.width
      spacing: Style.space(6)
      Button {
        text: "Android app"
        foreground: view.foreground
        fontFamily: view.fontFamily
        fontSize: Style.font.bodySmall
        onClicked: Qt.openUrlExternally("https://play.google.com/store/apps/details?id=com.midea.ai.overseas")
      }
      Button {
        text: "iPhone app"
        foreground: view.foreground
        fontFamily: view.fontFamily
        fontSize: Style.font.bodySmall
        onClicked: Qt.openUrlExternally("https://apps.apple.com/app/id1254346490")
      }
      Button {
        text: "Install Midea support"
        enabled: !view.ac.saving && !view.ac.discovering
        foreground: view.foreground
        fontFamily: view.fontFamily
        fontSize: Style.font.bodySmall
        onClicked: view.mideaInstallRequested()
      }
      Button {
        text: "Full guide"
        foreground: view.foreground
        fontFamily: view.fontFamily
        fontSize: Style.font.bodySmall
        onClicked: view.ac.openMideaGuide()
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
      enabled: !view.ac.saving && !modelData.configured && (modelData.backend === "midea_lan"
               || (!!modelData.state && !!modelData.mac))
      selected: view.chosen !== null && view.chosen.host === modelData.host
      text: view.describe(modelData) + (modelData.configured ? "  (already added)" : "")
      foreground: view.foreground
      fontFamily: view.fontFamily
      fontSize: Style.font.bodySmall
      onClicked: {
        view.chosen = modelData
        view.manual = false
        passwordField.text = ""
      }
    }
  }

  Text {
    visible: view.ac.discovered && !view.ac.discovering && view.available.length === 0
    width: parent.width
    wrapMode: Text.WordWrap
    text: view.isMidea
      ? "No new Midea air conditioner found. Pair it with SmartHome, "
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
      enabled: !view.ac.discovering && !view.ac.saving
      foreground: view.foreground
      fontFamily: view.fontFamily
      fontSize: Style.font.bodySmall
      onClicked: {
        view.chosen = null
        passwordField.text = ""
        view.ac.discover()
      }
    }
    Button {
      text: "Enter address"
      iconText: ""
      bordered: true
      selected: view.manual
      enabled: !view.ac.saving
      foreground: view.foreground
      fontFamily: view.fontFamily
      fontSize: Style.font.bodySmall
      onClicked: {
        view.manual = !view.manual
        passwordField.text = ""
        if (view.manual) view.chosen = null
      }
    }
  }

  RowLayout {
    visible: view.manual
    enabled: !view.ac.saving
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
    enabled: !view.ac.saving
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

    Toggle {
      visible: view.isMidea && view.isV3
      width: parent.width
      label: "Use a saved token/key file"
      checked: view.useOptionsFile
      foreground: view.foreground
      fontFamily: view.fontFamily
      onClicked: {
        view.useOptionsFile = !view.useOptionsFile
        passwordField.text = ""
      }
    }

    TextField {
      id: emailField
      visible: view.isMidea && view.isV3 && !view.useOptionsFile
      width: parent.width
      placeholderText: "SmartHome email"
      inputMethodHints: Qt.ImhEmailCharactersOnly | Qt.ImhNoPredictiveText
      foreground: view.foreground
      font.family: view.fontFamily
      onAccepted: passwordField.forceActiveFocus()
    }

    TextField {
      id: passwordField
      visible: view.isMidea && view.isV3 && !view.useOptionsFile
      width: parent.width
      placeholderText: "SmartHome password"
      password: true
      inputMethodHints: Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
      foreground: view.foreground
      font.family: view.fontFamily
      onAccepted: view.save()
    }

    TextField {
      id: optionsFileField
      visible: view.isMidea && (!view.isV3 || view.useOptionsFile)
      width: parent.width
      placeholderText: "Token/key TOML file path (required for V3)"
      foreground: view.foreground
      font.family: view.fontFamily
    }

    Text {
      visible: view.isMidea && view.isV3
      width: parent.width
      wrapMode: Text.WordWrap
      text: view.useOptionsFile
        ? "Enter your saved token/key TOML file path. No cloud sign-in is needed."
        : "Sign in once to retrieve your AC's local token/key from SmartHome. "
          + "Your password is not saved. Daily control stays on your local network. "
          + "The AC must already be added to this SmartHome account."
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
        text: view.ac.saving
          ? (view.isMidea && view.isV3 && !view.useOptionsFile ? "Connecting to SmartHome…"
             : view.testFeatures ? "Testing… about a minute" : "Saving…")
          : (view.isMidea && view.isV3 && !view.useOptionsFile ? "Sign in and add" : "Add")
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
