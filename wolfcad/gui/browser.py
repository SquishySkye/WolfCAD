# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Browser wie in Fusion 360: ein schwebendes Feld oben links auf der 3D-Ansicht (nicht mehr am Fensterrand),
mit Titelzeile „BROWSER“, Einklapp-Pfeil und dem Baum aus Körpern, Gruppen, Skizzen, Ebenen …

Das Feld ist ein Kind der Ansicht und liegt über der 3D-Grafik; es wächst mit seinem Inhalt bis höchstens
gut zur Hälfte der Ansichtshöhe und lässt sich auf die Titelzeile einklappen.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QTreeWidget, QVBoxLayout

_MARGIN = 10            # Abstand zum Rand der Ansicht (Bildpunkte)
_WIDTH = 270            # Breite des Felds
_MAX_SHARE = 0.6        # höchstens dieser Anteil der Ansichtshöhe


class BrowserOverlay(QFrame):
    """Schwebender Browser über der Ansicht; `tree` ist der eigentliche Baum (QTreeWidget)."""

    def __init__(self, viewport):
        super().__init__(viewport)
        self.setObjectName("browserOverlay")        # Aussehen (halb durchsichtiger Hintergrund) aus dem Design
        self._viewport = viewport
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(4)
        self._toggle = QToolButton()
        self._toggle.setObjectName("browserToggle")
        self._toggle.setAutoRaise(True)
        self._toggle.setToolTip("Browser ein- oder ausklappen")
        self._toggle.clicked.connect(lambda: self.set_collapsed(not self.collapsed))
        head.addWidget(self._toggle)
        title = QLabel("BROWSER")
        title.setObjectName("browserTitle")
        head.addWidget(title)
        head.addStretch(1)
        layout.addLayout(head)
        self.tree = QTreeWidget()
        self.tree.setObjectName("browserTree")
        self.tree.setHeaderHidden(True)
        self.tree.setFrameShape(QFrame.Shape.NoFrame)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        layout.addWidget(self.tree)
        self.collapsed = False
        self._set_arrow()
        viewport.installEventFilter(self)       # bei Größenänderung der Ansicht neu einpassen
        self.tree.itemExpanded.connect(lambda _item: self.place())
        self.tree.itemCollapsed.connect(lambda _item: self.place())
        self.place()
        self.show()

    def set_collapsed(self, collapsed: bool) -> None:
        """Klappt den Browser auf die Titelzeile ein (wie Fusions Pfeil am Browser) oder wieder auf."""
        self.collapsed = collapsed
        self.tree.setVisible(not collapsed)
        self._set_arrow()
        self.place()

    def _set_arrow(self) -> None:
        self._toggle.setText("▸" if self.collapsed else "▾")

    def place(self) -> None:
        """Oben links in die Ansicht legen; die Höhe folgt dem Inhalt (mit Obergrenze)."""
        limit = max(120, int(self._viewport.height() * _MAX_SHARE))
        if self.collapsed:
            height = self.sizeHint().height()
        else:
            # so hoch wie die sichtbaren Zeilen des Baums, aber nicht höher als erlaubt
            rows, item = 0, self.tree.topLevelItem(0)
            while item is not None:
                rows += 1
                item = self.tree.itemBelow(item)
            row_height = max(18, self.tree.sizeHintForRow(0)) if rows else 18
            height = min(limit, 40 + rows * row_height)
        self.setGeometry(_MARGIN, _MARGIN, _WIDTH, height)
        self.raise_()
        # Hinweise der Ansicht (oben links) rücken neben den Browser
        self._viewport.info_offset = _MARGIN + _WIDTH + 12

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._viewport and event.type() == QEvent.Type.Resize:
            self.place()
        return False
