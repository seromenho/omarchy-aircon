import QtQuick
import Quickshell
import Quickshell.Io

// Mirrors the aircond snapshot (streamed by `aircon watch`), tracks the selected device and
// runs `aircon` commands, including the in-panel setup.
Item {
  id: root
  visible: false

  readonly property string cli: {
    var home = Quickshell.env("HOME")
    return home ? home + "/.local/bin/aircon" : "aircon"
  }

  // Keep in sync with manifest.json and src/aircon/__init__.py (a test checks this).
  readonly property string pluginVersion: "0.2.0"
  readonly property string installScript:
    Qt.resolvedUrl("../install.sh").toString().replace(/^file:\/\//, "")

  // False until the commands and the user service exist (right after `omarchy plugin add`).
  property bool installed: true
  // The daemon still runs older code after `omarchy plugin update`: refresh it once.
  readonly property bool outdated: serviceUp && snapshot.version !== pluginVersion
  property bool _updateTried: false
  onOutdatedChanged: {
    if (!outdated || _updateTried) return
    _updateTried = true
    updateProcess.command = [installScript, "--update"]
    updateProcess.running = true
  }

  property bool serviceUp: false
  property var snapshot: ({})
  property string selectedId: ""
  property string commandError: ""
  property var _queue: []

  readonly property var devices: snapshot.devices || []
  readonly property bool configured: snapshot.configured === true && devices.length > 0
  readonly property var device: {
    var i
    for (i = 0; i < devices.length; i++) if (devices[i].id === selectedId) return devices[i]
    for (i = 0; i < devices.length; i++) if (devices[i].id === snapshot.default) return devices[i]
    return devices.length > 0 ? devices[0] : ({})
  }
  readonly property string deviceId: device.id || ""
  readonly property int onCount: devices.filter(function(d) { return d.connected && d.power }).length

  readonly property bool connected: serviceUp && device.connected === true
  readonly property bool power: device.power === true
  readonly property string mode: device.mode || "cool"
  readonly property string fan: device.fan || "auto"
  readonly property int targetTemp: device.target_temp !== undefined ? device.target_temp : 24
  readonly property var roomTemp: device.room_temp !== undefined ? device.room_temp : null
  readonly property var roomHumidity: device.room_humidity !== undefined ? device.room_humidity : null
  readonly property string swing: device.swing || "off"
  readonly property bool health: device.health === true
  readonly property bool sleep: device.sleep === true
  readonly property var caps: device.capabilities || ({
    modes: [], fan_speeds: [], swing: [], temp_min: 16, temp_max: 30, temp_step: 1, features: []
  })
  readonly property var timers: device.timers || ({})
  readonly property var sleepCurve: device.sleep_curve || null
  readonly property var filter: device.filter || ({ hours: 0, threshold: 0, due: false })
  readonly property string lastError: device.last_error || snapshot.last_error || ""
  readonly property string deviceName: device.name || "Air conditioner"
  readonly property string host: device.host || ""
  readonly property string backendName: device.backend || ""
  readonly property string alarm: device.alarm || ""
  readonly property string updatedAt: snapshot.updated_at || ""

  function select(id) {
    selectedId = id
    commandError = ""
  }

  function hasFeature(name) {
    return (caps.features || []).indexOf(name) >= 0
  }

  function clampTemp(value) {
    return Math.max(caps.temp_min, Math.min(caps.temp_max, Math.round(value)))
  }

  // Show a change immediately; the next snapshot from the daemon confirms or corrects it.
  function patch(changes) {
    var id = deviceId
    var next = Object.assign({}, snapshot)
    next.devices = devices.map(function(d) {
      return d.id === id ? Object.assign({}, d, changes) : d
    })
    snapshot = next
  }

  // Runs `aircon -d <selected device> <args>`.
  function run(args, optimistic) {
    if (optimistic) patch(optimistic)
    enqueue(["-d", deviceId].concat(args))
  }

  function enqueue(args) {
    commandError = ""
    _queue = _queue.concat([args])
    if (!commandProcess.running) _next()
  }

  function _next() {
    if (_queue.length === 0) return
    var args = _queue[0]
    _queue = _queue.slice(1)
    commandProcess.command = [root.cli].concat(args)
    commandProcess.running = true
  }

  Process {
    id: commandProcess
    running: false
    command: []
    stderr: StdioCollector { id: commandStderr; waitForEnd: true }
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        var message = String(commandStderr.text || "").trim().replace(/^aircon: /, "")
        root.commandError = message || "Command failed"
      }
      root._next()
    }
  }

  Process {
    id: watchProcess
    running: true
    command: [root.cli, "watch"]
    stdout: SplitParser {
      onRead: function(line) {
        try {
          root.snapshot = JSON.parse(line)
          root.serviceUp = true
        } catch (e) {
          console.warn("aircon: bad state line", e)
        }
      }
    }
    onExited: function(exitCode) {
      root.serviceUp = false
      restartTimer.restart()
    }
  }

  Timer {
    id: restartTimer
    interval: 1500
    repeat: false
    onTriggered: {
      installCheck.running = true
      watchProcess.running = true
    }
  }

  Process {
    id: installCheck
    running: true
    command: ["sh", "-c",
              "test -x \"$HOME/.local/bin/aircon\" && systemctl --user is-enabled --quiet aircond.service"]
    onExited: function(exitCode) { root.installed = exitCode === 0 }
  }

  Process {
    id: updateProcess
    running: false
    command: []
    stderr: StdioCollector { id: updateStderr; waitForEnd: true }
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.commandError = "Update failed: " + String(updateStderr.text || "").trim().split("\n").pop()
      }
    }
  }

  // -- in-panel setup ---------------------------------------------------------------------

  property bool discovering: false
  property bool discovered: false
  property var candidates: []
  property bool saving: false
  property string setupError: ""
  property string savedId: ""

  function discover() {
    if (discoverProcess.running) return
    discovering = true
    discovered = false
    candidates = []
    setupError = ""
    _discoverDone = { exited: false, output: false }
    discoverProcess.command = [root.cli, "setup", "discover", "--json"]
    discoverProcess.running = true
  }

  function addDevice(hostAddress, mac, name, validate) {
    if (saveProcess.running) return
    saving = true
    setupError = ""
    savedId = ""
    _saveDone = { exited: false, output: false, code: 0 }
    var args = [root.cli, "setup", "apply", "--host", hostAddress, "--mac", mac,
                "--name", name, "--json"]
    if (validate) args.push("--validate")
    saveProcess.command = args
    saveProcess.running = true
  }

  // A Process can report its exit before its output stream is drained, so each run
  // finishes only once both have happened.
  property var _discoverDone: ({ exited: false, output: false })
  property var _saveDone: ({ exited: false, output: false, code: 0 })

  function _finishDiscover() {
    if (!_discoverDone.exited || !_discoverDone.output) return
    discovering = false
    discovered = true
    try {
      candidates = JSON.parse(String(discoverStdout.text || "[]"))
    } catch (e) {
      candidates = []
      setupError = "Could not search the network"
    }
  }

  function _finishSave() {
    if (!_saveDone.exited || !_saveDone.output) return
    saving = false
    var result = null
    try { result = JSON.parse(String(saveStdout.text || "")) } catch (e) {}
    if (_saveDone.code === 0 && result && result.ok) {
      selectedId = result.id
      savedId = result.id
    } else {
      setupError = (result && result.error) || String(saveStderr.text || "").trim()
                   || "Could not add the air conditioner"
    }
  }

  Process {
    id: discoverProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: discoverStdout
      waitForEnd: true
      onStreamFinished: { root._discoverDone.output = true; root._finishDiscover() }
    }
    onExited: function(exitCode) { root._discoverDone.exited = true; root._finishDiscover() }
  }

  Process {
    id: saveProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: saveStdout
      waitForEnd: true
      onStreamFinished: { root._saveDone.output = true; root._finishSave() }
    }
    stderr: StdioCollector { id: saveStderr; waitForEnd: true }
    onExited: function(exitCode) {
      root._saveDone.code = exitCode
      root._saveDone.exited = true
      root._finishSave()
    }
  }
}
