"""Small Qt list models used by the transcript and activity surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Slot


class DictListModel(QAbstractListModel):
    def __init__(self, role_names: tuple[str, ...], parent=None) -> None:
        super().__init__(parent)
        self._items: list[dict[str, Any]] = []
        self._roles = {
            int(Qt.ItemDataRole.UserRole) + index + 1: name.encode("utf-8")
            for index, name in enumerate(role_names)
        }
        self._role_lookup = {value.decode(): key for key, value in self._roles.items()}

    def roleNames(self) -> dict[int, bytes]:
        return self._roles

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        name = self._roles.get(int(role))
        return self._items[index.row()].get(name.decode()) if name else None

    def append(self, item: Mapping[str, Any]) -> int:
        row = len(self._items)
        self.beginInsertRows(QModelIndex(), row, row)
        self._items.append(dict(item))
        self.endInsertRows()
        return row

    def update(self, row: int, values: Mapping[str, Any]) -> None:
        if not 0 <= row < len(self._items):
            return
        self._items[row].update(values)
        changed_roles = [self._role_lookup[key] for key in values if key in self._role_lookup]
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, changed_roles)

    @Slot()
    def clear(self) -> None:
        if not self._items:
            return
        self.beginResetModel()
        self._items.clear()
        self.endResetModel()

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._items]


class ConversationModel(DictListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(("role", "text", "streaming", "error", "timestamp"), parent)


class ActivityModel(DictListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(("id", "label", "detail", "status", "kind"), parent)

    def upsert(self, activity_id: str, values: Mapping[str, Any]) -> int:
        for index, item in enumerate(self._items):
            if item.get("id") == activity_id:
                self.update(index, values)
                return index
        return self.append({"id": activity_id, **dict(values)})


class ApprovalModel(DictListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(("requestId", "title", "detail", "risk", "resolved"), parent)
