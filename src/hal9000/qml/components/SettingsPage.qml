import QtQuick
import QtQuick.Controls
import "."

Flickable {
    id: root
    default property alias contents: column.data
    contentWidth: width
    contentHeight: column.implicitHeight + 36
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

    Column {
        id: column
        width: Math.max(0, root.width - root.leftMargin - root.rightMargin)
        spacing: HalTheme.spacing3
        topPadding: 18
        bottomPadding: 18
    }
}
