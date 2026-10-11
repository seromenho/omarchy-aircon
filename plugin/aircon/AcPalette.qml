import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons

// Semantic colors from the active Omarchy theme's colors.toml, falling back to the accent.
Item {
  id: root
  visible: false

  property var values: ({})

  readonly property color cool: pick(["blue", "cyan"])
  readonly property color heat: pick(["orange", "red"])
  readonly property color dry: pick(["yellow"])
  readonly property color fan: pick(["green"])
  readonly property color auto: Color.accent
  readonly property color cold: pick(["blue", "cyan"])
  readonly property color hot: pick(["red", "orange"])

  function pick(keys) {
    for (var i = 0; i < keys.length; i++) {
      if (values[keys[i]]) return values[keys[i]]
    }
    return Color.accent
  }

  function modeColor(mode) {
    switch (mode) {
    case "cool": return cool
    case "heat": return heat
    case "dry": return dry
    case "fan": return fan
    default: return auto
    }
  }

  function mix(a, b, t) {
    var k = Math.max(0, Math.min(1, t))
    return Qt.rgba(a.r + (b.r - a.r) * k, a.g + (b.g - a.g) * k, a.b + (b.b - a.b) * k, 1)
  }

  function parse(text) {
    var parsed = {}
    var lines = String(text || "").split("\n")
    for (var i = 0; i < lines.length; i++) {
      var match = lines[i].match(/^\s*([A-Za-z0-9_]+)\s*=\s*"(#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?)"/)
      if (match) parsed[match[1]] = match[2]
    }
    values = parsed
  }

  FileView {
    id: colorsFile
    path: Quickshell.env("HOME") + "/.local/state/omarchy/current/theme/colors.toml"
    watchChanges: true
    printErrors: false
    onLoaded: root.parse(text())
    onFileChanged: reload()
  }

  // Theme switches reach the shell over IPC; re-read the file whenever the palette moves.
  Connections {
    target: Color
    function onAccentChanged() { colorsFile.reload() }
    function onBackgroundChanged() { colorsFile.reload() }
    function onForegroundChanged() { colorsFile.reload() }
  }
}
