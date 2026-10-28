# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Werkzeugleiste im Aufbau von Fusion 360: oben der Arbeitsbereich und die Reiter, darunter je Reiter die Bereiche
(ERSTELLEN, ÄNDERN, …). Jeder Bereich zeigt die häufigsten Werkzeuge als Symbole ohne Text (Name im Tooltip) und
darunter seinen Namen mit ▾ – der öffnet die Liste aller Werkzeuge des Bereichs.

Nur der Aufbau ist nachgebildet; Symbole und Grafik sind eigene (keine Autodesk-Grafik).
Bleibt die Maus auf einem Werkzeug oder einem Listeneintrag, erklärt eine animierte Karte, was es tut und wie es geht
(siehe tool_hints.py).
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QMenu, QStackedWidget, QTabBar, QToolButton, QVBoxLayout, QWidget,
)

from . import tool_hints

_ICON_SIZE = QSize(32, 32)      # Symbolgröße der Werkzeugknöpfe in den Bereichen


class RibbonPanel(QWidget):
    """Ein Bereich: oben die hervorgehobenen Werkzeuge als Symbole, unten der Bereichsname mit Liste aller Werkzeuge."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title = title
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 0, 4, 0)
        outer.setSpacing(0)
        self._row = QHBoxLayout()
        self._row.setSpacing(1)
        outer.addLayout(self._row)
        # Bereichsname mit ▾: öffnet die vollständige Liste (wie Fusions Bereichsmenüs)
        self.menu = QMenu(self)
        tool_hints.instance().watch_menu(self.menu)     # Erklärkarte auch neben den Listeneinträgen
        self._title = QToolButton()
        self._title.setObjectName("ribbonTitle")       # Aussehen kommt aus dem Design
        self._title.setText(f"{title} ▾")
        self._title.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._title.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._title.setMenu(self.menu)
        outer.addWidget(self._title, alignment=Qt.AlignmentFlag.AlignHCenter)

    def add(self, action: QAction, featured: bool) -> None:
        """Nimmt ein Werkzeug in die Liste auf; hervorgehobene erscheinen zusätzlich als Symbol."""
        self.menu.addAction(action)
        if featured:
            button = QToolButton()
            button.setDefaultAction(action)
            button.setIconSize(_ICON_SIZE)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setAutoRaise(True)
            tool_hints.instance().watch_button(button)
            self._row.addWidget(button)

    def add_button(self, button: QToolButton) -> None:
        """Ein fertiger Knopf (z. B. mit eigenem Untermenü) als hervorgehobenes Werkzeug; sein Menü kommt auch in
        die Liste des Bereichs."""
        button.setIconSize(_ICON_SIZE)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setAutoRaise(True)
        tool_hints.instance().watch_button(button)
        self._row.addWidget(button)
        if button.menu() is not None:
            self.menu.addMenu(button.menu()).setText(button.text())

    def add_separator(self) -> None:
        self.menu.addSeparator()


class Ribbon(QWidget):
    """Arbeitsbereich-Knopf, Reiter und je Reiter eine Reihe von Bereichen."""

    tab_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(10)
        # Arbeitsbereich wie Fusions „KONSTRUKTION ▾“ ganz links
        self.workspace = QToolButton()
        self.workspace.setObjectName("ribbonWorkspace")
        self.workspace.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.workspace.setMenu(QMenu(self.workspace))
        head.addWidget(self.workspace)
        self.tabs = QTabBar()
        self.tabs.setObjectName("ribbonTabs")
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        # alle Reiter immer ganz zeigen (keine Pfeile, keine gekürzten Namen)
        self.tabs.setUsesScrollButtons(False)
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.tabs.currentChanged.connect(self._show_tab)
        head.addWidget(self.tabs)
        head.addStretch(1)
        layout.addLayout(head)
        self._pages = QStackedWidget()
        layout.addWidget(self._pages)
        self._rows: dict[str, QHBoxLayout] = {}
        self._names: list[str] = []
        self._hidden: set[str] = set()      # Reiter, die nur in einem Zusammenhang erscheinen (z. B. SKIZZE)

    def set_workspace(self, title: str) -> None:
        self.workspace.setText(f"{title} ▾")

    def add_tab(self, name: str, contextual: bool = False) -> None:
        """Neuer Reiter; ein Zusammenhangs-Reiter (SKIZZE) erscheint erst mit `show_context`."""
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addStretch(1)
        self._pages.addWidget(page)
        self._rows[name] = row
        self._names.append(name)
        self.tabs.addTab(name)
        if contextual:
            self._hidden.add(name)
            self.tabs.setTabVisible(len(self._names) - 1, False)
        self._fit_tabs()

    def _fit_tabs(self) -> None:
        """Die Reiterzeile so breit wie alle sichtbaren Reiter (sonst schiebt Qt den ersten Reiter aus dem Bild)."""
        self.tabs.setMinimumWidth(self.tabs.sizeHint().width())

    def add_panel(self, tab: str, title: str) -> RibbonPanel:
        """Neuer Bereich am Ende des Reiters (mit Trennlinie zum vorigen)."""
        row = self._rows[tab]
        if row.count() > 1:
            line = QFrame()
            line.setFrameShape(QFrame.Shape.VLine)
            line.setObjectName("ribbonSeparator")
            row.insertWidget(row.count() - 1, line)
        panel = RibbonPanel(title)
        row.insertWidget(row.count() - 1, panel)
        return panel

    def add_widget(self, tab: str, widget: QWidget) -> None:
        """Ein freies Bedienelement am Ende des Reiters (z. B. „Skizze fertig stellen“)."""
        row = self._rows[tab]
        row.insertWidget(row.count() - 1, widget)
        if isinstance(widget, QToolButton):
            tool_hints.instance().watch_button(widget)

    def current(self) -> str:
        return self._names[self.tabs.currentIndex()]

    def select(self, name: str) -> None:
        self.tabs.setCurrentIndex(self._names.index(name))

    def show_context(self, name: str | None) -> None:
        """Blendet einen Zusammenhangs-Reiter ein und wählt ihn (None: alle ausblenden, zurück zum ersten Reiter)."""
        if name is not None and self.current() not in self._hidden:
            self._before_context = self.current()       # dorthin geht es danach zurück
        for number, tab in enumerate(self._names):
            if tab in self._hidden:
                self.tabs.setTabVisible(number, tab == name)
        self._fit_tabs()
        if name is not None:
            self.select(name)
        else:
            # Qt wählt beim Ausblenden den Nachbarreiter – zurück zu dem Reiter, der vorher aktiv war
            fallback = next(tab for tab in self._names if tab not in self._hidden)
            self.select(getattr(self, "_before_context", fallback))

    def _show_tab(self, number: int) -> None:
        if 0 <= number < len(self._names):
            self._pages.setCurrentIndex(number)
            self.tab_changed.emit(self._names[number])
