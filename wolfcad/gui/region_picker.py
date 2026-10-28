# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Auswahl einzelner Profilbereiche einer Skizze per Klick (für Extrusion und Drehen)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

_FILL, _CHOSEN, _OUTLINE = QColor("#3a4150"), QColor("#3d6fb5"), QColor("#c8ccd4")


class _RegionView(QWidget):
    """Zeichnet die Bereiche als Flächen; ein Klick schaltet den getroffenen Bereich um."""

    def __init__(self, regions: list, chosen: set[int], parent=None):
        super().__init__(parent)
        self.regions, self.chosen = regions, chosen
        self.setMinimumSize(640, 480)

    def _mapping(self) -> tuple[float, float, float]:
        """Maßstab und Verschiebung, mit denen alle Bereiche ins Fenster passen."""
        points = [point for region in self.regions for triangle in region["triangles"] for point in triangle]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        width, height = max(xs) - min(xs) or 1.0, max(ys) - min(ys) or 1.0
        scale = 0.85 * min(self.width() / width, self.height() / height)
        return (scale, self.width() / 2 - (min(xs) + max(xs)) / 2 * scale,
                self.height() / 2 + (min(ys) + max(ys)) / 2 * scale)

    def _screen(self, point: tuple[float, float]) -> QPointF:
        scale, shift_x, shift_y = self._mapping()
        return QPointF(point[0] * scale + shift_x, shift_y - point[1] * scale)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#22252b"))
        for index, region in enumerate(self.regions):
            color = _CHOSEN if index in self.chosen else _FILL
            painter.setPen(QPen(color, 1))      # gleiche Farbe: keine sichtbaren Dreiecksnähte
            painter.setBrush(color)
            for triangle in region["triangles"]:
                painter.drawPolygon(QPolygonF([self._screen(point) for point in triangle]))
        painter.setPen(QPen(_OUTLINE, 1.5))
        for region in self.regions:
            for start, end in region["outline"]:
                painter.drawLine(self._screen(start), self._screen(end))
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        scale, shift_x, shift_y = self._mapping()
        point = ((event.position().x() - shift_x) / scale, (shift_y - event.position().y()) / scale)
        # Kleinster getroffener Bereich zuerst: so lässt sich auch eine Insel im Rahmen treffen
        for index in sorted(range(len(self.regions)), key=lambda i: len(self.regions[i]["triangles"])):
            if any(_inside(point, triangle) for triangle in self.regions[index]["triangles"]):
                self.chosen.symmetric_difference_update({index})
                self.update()
                return


def _inside(point, triangle) -> bool:
    """Liegt der Punkt im Dreieck? (Vorzeichen der drei Kantenfunktionen stimmen überein)"""
    def side(a, b):
        return (point[0] - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (point[1] - b[1])
    values = [side(triangle[0], triangle[1]), side(triangle[1], triangle[2]), side(triangle[2], triangle[0])]
    return not (min(values) < 0 and max(values) > 0)


class RegionPicker(QDialog):
    """Dialog: Bereiche der Skizze anklicken, die das Profil bilden sollen."""

    def __init__(self, regions: list, preselected: set[int], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Profil wählen")
        self.view = _RegionView(regions, set(preselected))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Bereiche anklicken, die das Profil bilden sollen (blau = gewählt):"))
        layout.addWidget(self.view, 1)
        layout.addWidget(buttons)

    def chosen(self) -> list[int]:
        return sorted(self.view.chosen)
