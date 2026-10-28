# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Rundes Markierungsmenü an der Mausposition (rechte Maustaste), nach dem Vorbild von Fusion.

Bis zu acht Einträge liegen als Felder im Kreis um die Maus; die Mitte öffnet die vollständige Liste.
Zwei Arten der Bedienung: kurzer Rechtsklick öffnet den Ring, ein Klick auf ein Feld löst es aus – oder die
rechte Taste gedrückt halten und in die Richtung eines Felds wischen, Loslassen löst es aus (Wischgeste).

Der Ring ist ein Kind der 3D-Ansicht (kein eigenes Fenster): so bleibt er auch unter Wayland stehen und die
Ansicht kann ihm beim Wischen die Mausposition weiterreichen.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QWidget

from . import theme

_INNER, _OUTER = 46, 168        # Radien des Rings in Bildpunkten (bei 1000 Bildpunkten Fensterhöhe)
_SECTORS = 8


class MarkingMenu(QWidget):
    """Ring aus bis zu acht Feldern; `entries` sind (Beschriftung, Symbol, Aufruf), oben beginnend im Uhrzeigersinn."""

    def __init__(self, entries: list, more=None, parent=None, scale: float = 1.0):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.entries = list(entries)[:_SECTORS]
        self._more = more                       # Aufruf für die Mitte: die vollständige Liste
        self._inner, self._outer = _INNER * scale, _OUTER * scale
        self._hover: int | None = None          # Nummer des Felds unter der Maus, -1 für die Mitte
        side = int(2 * self._outer + 8)
        self.resize(side, side)
        self.setMask(QRegion(0, 0, side, side, QRegion.RegionType.Ellipse))     # nur der Kreis fängt Klicks

    def popup_at(self, position: QPoint) -> None:
        """Zeigt das Menü mit der Mitte an dieser Stelle der Ansicht."""
        self.move(position - QPoint(self.width() // 2, self.height() // 2))
        self.show()
        self.raise_()

    def labels(self) -> list[str]:
        return [entry[0] for entry in self.entries]

    def close(self) -> bool:
        """Blendet den Ring aus (er wird beim nächsten Öffnen ersetzt)."""
        self.hide()
        return True

    def choose(self, label: str) -> None:
        """Löst den Eintrag mit dieser Beschriftung aus (wie ein Klick auf sein Feld)."""
        for text, _icon, action in self.entries:
            if text == label:
                self.close()
                action()
                return

    def _sector_at(self, point: QPointF, gesture: bool = False) -> int | None:
        """Feld an dieser Stelle des Rings; beim Wischen zählt jede Stelle außerhalb der Mitte, auch jenseits des Rands."""
        dx, dy = point.x() - self.width() / 2, point.y() - self.height() / 2
        distance = math.hypot(dx, dy)
        if distance <= self._inner:
            return None if gesture else (-1 if self._more is not None else None)
        if distance > self._outer and not gesture:
            return None
        # Winkel ab „oben“ im Uhrzeigersinn; jedes Feld ist 45° breit und um seine Richtung zentriert
        angle = (math.degrees(math.atan2(dx, -dy)) + 360.0 + 180.0 / _SECTORS) % 360.0
        number = int(angle // (360.0 / _SECTORS))
        return number if number < len(self.entries) else None

    # --- Wischgeste: die Ansicht reicht die Mausposition (in ihren Koordinaten) weiter -----------

    def hover_at(self, position: QPointF) -> None:
        """Hebt beim Wischen das Feld in Richtung der Maus hervor."""
        hover = self._sector_at(QPointF(self.mapFromParent(position.toPoint())), gesture=True)
        if hover != self._hover:
            self._hover = hover
            self.update()

    def release_at(self, position: QPointF) -> bool:
        """Loslassen nach dem Wischen: löst das Feld in dieser Richtung aus. False, wenn die Maus noch in der
        Mitte war – dann bleibt der Ring wie nach einem Klick offen."""
        chosen = self._sector_at(QPointF(self.mapFromParent(position.toPoint())), gesture=True)
        if chosen is None:
            self._hover = None
            self.update()
            return False
        self.close()
        self.entries[chosen][2]()
        return True

    # --- Bedienung per Klick -------------------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:
        hover = self._sector_at(event.position())
        if hover != self._hover:
            self._hover = hover
            self.update()

    def mousePressEvent(self, event) -> None:
        event.accept()          # der Klick gehört dem Ring, nicht der Ansicht darunter

    def mouseReleaseEvent(self, event) -> None:
        chosen = self._sector_at(event.position())
        self.close()
        if chosen == -1:
            self._more()
        elif chosen is not None:
            self.entries[chosen][2]()

    def paintEvent(self, _event) -> None:
        colours = theme.current().extra.get("colours", {})
        surface = QColor(colours.get("surface", "#1b1e24"))
        text = QColor(colours.get("text", "#e6e9ef"))
        accent = QColor(colours.get("accent", "#2d9bf0"))
        on_accent = QColor(colours.get("on_accent", "#ffffff"))
        outline = QColor(colours.get("outline", "#3a3d44"))
        surface.setAlpha(238)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        centre = QPointF(self.width() / 2, self.height() / 2)
        outer = QRectF(centre.x() - self._outer, centre.y() - self._outer, 2 * self._outer, 2 * self._outer)
        inner = QRectF(centre.x() - self._inner, centre.y() - self._inner, 2 * self._inner, 2 * self._inner)
        span = 360.0 / _SECTORS
        font = QFont(self.font())
        font.setPixelSize(max(12, int(self._outer * 0.085)))
        font.setBold(True)
        painter.setFont(font)
        for number in range(_SECTORS):
            # Qt misst Winkel gegen den Uhrzeigersinn ab „rechts“; Feld 0 liegt oben
            start = 90.0 - number * span + span / 2
            path = QPainterPath()
            path.arcMoveTo(outer, start)
            path.arcTo(outer, start, -span)
            path.arcTo(inner, start - span, span)
            path.closeSubpath()
            filled = number < len(self.entries)
            painter.setPen(QPen(outline, 1.5))
            painter.setBrush(accent if filled and number == self._hover else surface)
            painter.drawPath(path)
            if not filled:
                continue
            label, symbol, _action = self.entries[number]
            direction = math.radians(number * span)
            middle = (self._inner + self._outer) / 2
            spot = QPointF(centre.x() + math.sin(direction) * middle, centre.y() - math.cos(direction) * middle)
            size = self._outer * 0.22
            if symbol is not None and not symbol.isNull():
                symbol.paint(painter, QRectF(spot.x() - size / 2, spot.y() - size * 0.95, size, size).toRect())
            painter.setPen(on_accent if number == self._hover else text)
            painter.drawText(QRectF(spot.x() - self._outer * 0.36, spot.y() + size * 0.12, self._outer * 0.72, size * 0.9),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, label)
        if self._more is not None:
            painter.setPen(QPen(outline, 1.5))
            painter.setBrush(accent if self._hover == -1 else surface)
            painter.drawEllipse(inner)
            painter.setPen(on_accent if self._hover == -1 else text)
            painter.drawText(inner, Qt.AlignmentFlag.AlignCenter, "Mehr")
        painter.end()
