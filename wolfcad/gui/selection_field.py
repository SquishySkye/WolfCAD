# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Auswahlfeld wie in Fusion 360 (Runde 77): ein Knopf „↖ 1 ausgewählt“ mit ✕ und darunter die Namen der gewählten
Körper. Das aktive Feld ist farbig hervorgehoben – in dieses Feld kommt der nächste Klick in der Ansicht. Ein Klick
auf ein Feld macht es aktiv, ✕ leert es.

Die Farbe der kleinen Marke vor dem Feld entspricht der Farbe, in der die gewählten Körper in der Ansicht markiert
werden (z. B. Zielkörper blau, Werkzeugkörper rot beim Ausschneiden).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget


class SelectionField(QWidget):
    """Ein Auswahlfeld; `activated` meldet einen Klick auf das Feld, `cleared` das ✕."""

    activated = Signal()
    cleared = Signal()

    def __init__(self, single: bool, parent=None):
        super().__init__(parent)
        self._single = single               # nur ein Element (Zielkörper) oder beliebig viele
        self._active = False
        self._color = QColor(40, 140, 235)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(4)
        self._swatch = QLabel()             # Farbmarke = Farbe der Markierung in der Ansicht
        self._swatch.setFixedSize(10, 10)
        row.addWidget(self._swatch)
        self.button = QToolButton()
        self.button.setCheckable(True)
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.button.clicked.connect(lambda _checked=False: self.activated.emit())
        row.addWidget(self.button, 1)
        self.clear = QToolButton()
        self.clear.setText("✕")
        self.clear.setToolTip("Auswahl leeren")
        self.clear.clicked.connect(lambda _checked=False: self.cleared.emit())
        row.addWidget(self.clear)
        layout.addLayout(row)
        self.names = QLabel()
        self.names.setObjectName("hint")
        self.names.setWordWrap(True)
        layout.addWidget(self.names)
        self.set_names([])

    def set_color(self, color: tuple[float, float, float]) -> None:
        self._color = QColor.fromRgbF(*color)
        self._restyle()

    def set_names(self, names: list[str]) -> None:
        names = [name for name in names if name]
        count = len(names)
        self.button.setText(f"↖  {count} ausgewählt" if count else "↖  Auswählen")
        self.names.setText(", ".join(names))
        self.names.setVisible(bool(names))
        self.clear.setEnabled(bool(names))
        self._count = count
        self._restyle()

    def set_active(self, active: bool) -> None:
        self._active = active
        self.button.setChecked(active)
        self._restyle()

    @property
    def active(self) -> bool:
        return self._active

    def _restyle(self) -> None:
        """Aktiv: Rahmen und zarter Hintergrund in der Markierungsfarbe; gefüllt: Text fett."""
        palette = QApplication.palette()
        text = palette.color(QPalette.ColorRole.ButtonText).name()
        color = self._color.name()
        tint = QColor(self._color)
        tint.setAlpha(55)
        background = f"rgba({tint.red()},{tint.green()},{tint.blue()},{tint.alpha()})" if self._active else "transparent"
        border = color if self._active else palette.color(QPalette.ColorRole.Mid).name()
        weight = "bold" if getattr(self, "_count", 0) else "normal"
        self.button.setStyleSheet(
            f"QToolButton {{ text-align: left; padding: 3px 8px; border: 1px solid {border}; border-radius: 4px; "
            f"background: {background}; color: {text}; font-weight: {weight}; }}")
        self._swatch.setStyleSheet(f"background: {color}; border-radius: 5px;")
