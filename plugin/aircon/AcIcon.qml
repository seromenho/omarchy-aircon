import QtQuick
import qs.Commons

// A wall-mounted indoor unit drawn with theme colors, with animated airflow while running.
Item {
  id: root

  property color foreground: Color.foreground
  property color flowColor: foreground
  property bool running: false
  property string swing: "off"

  implicitWidth: 220
  implicitHeight: 112

  readonly property real unitWidth: Math.min(width * 0.82, 260)

  Rectangle {
    id: unit
    width: root.unitWidth
    height: 46
    anchors.horizontalCenter: parent.horizontalCenter
    y: 4
    radius: 12
    color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
    border.width: 1
    border.color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.35)

    Rectangle {
      width: parent.width * 0.84
      height: 3
      radius: 1.5
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.bottom: parent.bottom
      anchors.bottomMargin: 9
      color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.35)
    }

    Rectangle {
      width: 6
      height: 6
      radius: 3
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.margins: 10
      color: root.running ? root.flowColor
                          : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.25)
    }
  }

  Repeater {
    model: 9

    Rectangle {
      id: stream
      required property int index

      readonly property real spread: (index - 4) / 4

      width: 2
      height: 46
      radius: 1
      x: root.width / 2 + spread * root.unitWidth * 0.38
      y: unit.y + unit.height + 4
      transformOrigin: Item.Top
      rotation: spread * 14
      visible: root.running
      opacity: 0
      gradient: Gradient {
        GradientStop { position: 0.0; color: Qt.rgba(root.flowColor.r, root.flowColor.g, root.flowColor.b, 0.75) }
        GradientStop { position: 1.0; color: Qt.rgba(root.flowColor.r, root.flowColor.g, root.flowColor.b, 0.0) }
      }

      SequentialAnimation on opacity {
        running: root.running
        loops: Animation.Infinite
        PauseAnimation { duration: stream.index * 140 }
        NumberAnimation { from: 0; to: 0.9; duration: 600; easing.type: Easing.OutQuad }
        NumberAnimation { from: 0.9; to: 0; duration: 900; easing.type: Easing.InQuad }
      }

      SequentialAnimation on rotation {
        running: root.running && (root.swing === "vertical" || root.swing === "both")
        loops: Animation.Infinite
        NumberAnimation { to: stream.spread * 14 + 10; duration: 1800; easing.type: Easing.InOutSine }
        NumberAnimation { to: stream.spread * 14 - 10; duration: 1800; easing.type: Easing.InOutSine }
      }
    }
  }
}
