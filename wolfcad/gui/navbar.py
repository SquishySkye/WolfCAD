# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Navigationsleiste unten in der Mitte der 3D-Ansicht (wie Fusions Navigationsleiste).

Knöpfe von links nach rechts: Orbit, Anschauen, Schwenken, Zoomen, Einpassen | Darstellung (Menü: schattiert mit
Kanten, schattiert, Drahtgitter) | Raster | Auswahlfilter (Menü). Orbit, Schwenken und Zoomen sind ein
gegenseitig ausschließender Modus für die linke Maustaste; ohne gewählten Modus dreht die linke Taste wie bisher.

Die Leiste meldet nur Wünsche über Signale; was sie bewirken, legt das Hauptfenster fest.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMenu, QToolButton

from .icons import icon

_MARGIN = 10            # Abstand zum unteren Rand der Ansicht

# Darstellungsarten: Schlüssel -> Beschriftung (Fusion: Visueller Stil)
DISPLAY_STYLES = {"shaded_edges": "Schattiert mit sichtbaren Kanten", "shaded": "Schattiert",
                  "wireframe": "Drahtgitter"}
# Auswahlfilter: Schlüssel -> Beschriftung (Fusion: Auswahlfilter)
SELECTION_FILTERS = {"body": "Körper", "face": "Flächen", "edge": "Kanten", "vertex": "Eckpunkte",
                     "sketch": "Skizzenprofile"}
# Modi der linken Maustaste
NAV_MODES = {"orbit": "Orbit", "pan": "Schwenken", "zoom": "Zoomen"}


class NavigationBar(QFrame):
    """Schwebende Leiste über der Ansicht; ein Kind der Ansicht, das sich bei Größenänderung neu einpasst."""

    mode_changed = Signal(str)              # "" (Auswählen/Standard), "orbit", "pan" oder "zoom"
    look_at_requested = Signal()
    fit_requested = Signal()
    style_changed = Signal(str)             # Schlüssel aus DISPLAY_STYLES
    grid_toggled = Signal(bool)
    filter_changed = Signal(set)            # Menge der erlaubten Auswahlarten

    def __init__(self, viewport, grid_on: bool = True):
        super().__init__(viewport)
        self.setObjectName("browserOverlay")        # gleiches halbdurchsichtiges Aussehen wie der Browser
        self._viewport = viewport
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(2)
        self.buttons: dict[str, QToolButton] = {}

        def button(key: str, symbol: str, tip: str, checkable: bool = False) -> QToolButton:
            item = QToolButton()
            item.setIcon(icon(symbol))
            item.setToolTip(tip)
            item.setAutoRaise(True)
            item.setCheckable(checkable)
            layout.addWidget(item)
            self.buttons[key] = item
            return item

        # Orbit/Schwenken/Zoomen: höchstens einer aktiv; ein zweiter Klick schaltet den Modus wieder ab
        for key, symbol in (("orbit", "nav_orbit"), ("look_at", "nav_look_at"), ("pan", "nav_pan"),
                            ("zoom", "nav_zoom"), ("fit", "nav_fit")):
            if key in NAV_MODES:
                item = button(key, symbol, f"{NAV_MODES[key]} – linke Maustaste zieht so (erneut klicken: aus)", True)
                item.toggled.connect(lambda on, chosen=key: self._mode_toggled(chosen, on))
            elif key == "look_at":
                button(key, symbol, "Anschauen – senkrecht auf die gewählte Fläche blicken").clicked.connect(
                    self.look_at_requested)
            else:
                button(key, symbol, "Einpassen – ganzes Modell zeigen").clicked.connect(self.fit_requested)
        layout.addSpacing(6)

        # Darstellung: Menü mit den visuellen Stilen
        style = button("style", "display_style", "Darstellung (visueller Stil)")
        style.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(style)
        group = QActionGroup(menu)
        self.style_actions: dict[str, QAction] = {}
        for key, text in DISPLAY_STYLES.items():
            entry = menu.addAction(text)
            entry.setCheckable(True)
            entry.setChecked(key == "shaded_edges")
            group.addAction(entry)
            entry.triggered.connect(lambda _checked=False, chosen=key: self.style_changed.emit(chosen))
            self.style_actions[key] = entry
        style.setMenu(menu)

        grid = button("grid", "grid", "Raster ein/aus", True)
        grid.setChecked(grid_on)
        grid.toggled.connect(self.grid_toggled)

        # Auswahlfilter: welche Arten ein Klick in der Ansicht wählen darf
        chooser = button("filter", "selection_filter", "Auswahlfilter – was ein Klick wählen darf")
        chooser.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        filters = QMenu(chooser)
        self.filter_actions: dict[str, QAction] = {}
        for key, text in SELECTION_FILTERS.items():
            entry = filters.addAction(text)
            entry.setCheckable(True)
            entry.setChecked(True)
            entry.toggled.connect(lambda _on: self.filter_changed.emit(self.allowed()))
            self.filter_actions[key] = entry
        filters.addSeparator()
        filters.addAction("Alle auswählbar", lambda: self.set_allowed(set(SELECTION_FILTERS)))
        chooser.setMenu(filters)

        viewport.installEventFilter(self)
        self.adjustSize()
        self.place()
        self.show()

    # --- Zustand --------------------------------------------------------------

    def _mode_toggled(self, key: str, on: bool) -> None:
        """Hält Orbit/Schwenken/Zoomen gegenseitig ausschließend und meldet den neuen Modus."""
        if on:
            for other in NAV_MODES:
                if other != key and self.buttons[other].isChecked():
                    self.buttons[other].blockSignals(True)
                    self.buttons[other].setChecked(False)
                    self.buttons[other].blockSignals(False)
        self.mode_changed.emit(key if on else "")

    def allowed(self) -> set:
        """Die im Auswahlfilter angehakten Arten."""
        return {key for key, entry in self.filter_actions.items() if entry.isChecked()}

    def set_allowed(self, kinds: set) -> None:
        for key, entry in self.filter_actions.items():
            entry.blockSignals(True)
            entry.setChecked(key in kinds)
            entry.blockSignals(False)
        self.filter_changed.emit(self.allowed())

    def set_style(self, key: str) -> None:
        if key in self.style_actions:
            self.style_actions[key].setChecked(True)

    # --- Lage -----------------------------------------------------------------

    def place(self) -> None:
        """Unten in der Mitte der Ansicht."""
        self.adjustSize()
        self.move(max(0, (self._viewport.width() - self.width()) // 2),
                  max(0, self._viewport.height() - self.height() - _MARGIN))
        self.raise_()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._viewport and event.type() == QEvent.Type.Resize:
            self.place()
        return False
