# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Bedienung der Benutzerparameter: Zahlenfeld, das auch Ausdrücke annimmt, und der
Dialog „Parameter ändern“ mit der Parameterliste."""

from __future__ import annotations

from PySide6.QtCore import QLocale, Qt, Signal
from PySide6.QtGui import QColor, QValidator
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..core import parameters as pm


class ExpressionBox(QDoubleSpinBox):
    """Zahlenfeld wie in Fusion: nimmt eine Zahl oder einen Ausdruck mit Parametern an.

    Ist ein Ausdruck eingetragen, zeigt das Feld den Ausdruck; der berechnete Wert steht
    im Tooltip. `resolver(text)` rechnet einen Ausdruck aus oder wirft `ParameterError`.
    """

    edited = Signal()       # Wert oder Ausdruck wurden durch eine fertige Eingabe geändert

    def __init__(self, resolver, parent=None, live: bool = False):
        super().__init__(parent)
        self.expression = ""
        self.problem = ""               # Fehlermeldung zur letzten Eingabe (leer: Eingabe war brauchbar)
        self._resolver = resolver
        self._last = None
        self.setRange(-1e6, 1e6)
        self.setDecimals(3)
        # Ohne `live` erst die fertig getippte Eingabe übernehmen; mit `live` (Werkzeugmaße) läuft das Modell beim
        # Tippen mit – wie in Fusion sieht man die neue Rundung schon, während man den Radius eintippt
        self.setKeyboardTracking(live)
        self.editingFinished.connect(self._finished)
        self.valueChanged.connect(lambda _value: self._finished())

    def set_expression(self, expression: str, value: float) -> None:
        """Trägt Ausdruck (leer = feste Zahl) und Wert ein, ohne eine Änderung zu melden."""
        self.blockSignals(True)
        self.expression = expression
        self.setValue(value)
        # Der Text wird nur bei geändertem Wert neu gebildet – hier ausdrücklich nachziehen
        self.lineEdit().setText(self.textFromValue(self.value()))
        self.blockSignals(False)
        self._show_state("")
        self._last = (self.expression, self.value())

    # --- Umrechnung Text <-> Wert (von Qt aufgerufen) -----------------------

    def validate(self, text: str, position: int):
        return QValidator.State.Acceptable, text, position     # geprüft wird erst beim Übernehmen

    def valueFromText(self, text: str) -> float:
        text = text.strip()
        # Qt reicht den Text samt angehängter Einheit durch
        unit = self.suffix().strip()
        if unit and text.endswith(unit):
            text = text[:-len(unit)].strip()
        if pm.is_number(text):
            self.expression = ""
            self._show_state("")
            return float(text.replace(",", "."))
        try:
            value = self._resolver(text)
        except pm.ParameterError as error:
            self._show_state(str(error))
            return self.value()         # unbrauchbare Eingabe: der bisherige Wert bleibt
        self.expression = text
        self._show_state("")
        return value

    def textFromValue(self, value: float) -> str:
        if self.expression:
            return self.expression
        return QLocale().toString(value, "f", self.decimals())

    def stepBy(self, steps: int) -> None:
        self.expression = ""            # Hoch-/Runterzählen macht aus dem Ausdruck eine feste Zahl
        super().stepBy(steps)

    # --- Rückmeldung ----------------------------------------------------------

    def _show_state(self, problem: str) -> None:
        self.problem = problem
        if problem:
            self.setStyleSheet("QDoubleSpinBox { border: 1px solid #e05555; }")
            self.setToolTip(problem)
        elif self.expression:
            self.setStyleSheet("QDoubleSpinBox { color: #ffd24a; }")       # an Parameter gebunden
            self.setToolTip(f"Ausdruck – aktueller Wert: {QLocale().toString(self.value(), 'f', 3)}")
        else:
            self.setStyleSheet("")
            self.setToolTip("Zahl oder Ausdruck mit Parametern, z. B. breite / 2")

    def _finished(self) -> None:
        self._show_state(self.problem)      # Tooltip mit dem neuen Wert auffrischen
        current = (self.expression, self.value())
        if current != self._last:
            self._last = current
            self.edited.emit()


class ParameterDialog(QDialog):
    """Parameterliste: Name, Ausdruck und berechneter Wert; Änderungen wirken sofort auf das Modell.

    `apply(parameters, renames)` übernimmt die Liste ins Modell und gibt (Werte, Fehler) zurück;
    `renames` nennt umbenannte Parameter (alter Name -> neuer Name), damit Ausdrücke mitziehen.
    """

    def __init__(self, parameters: dict[str, str], apply, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Parameter ändern")
        self.resize(560, 380)
        self._apply = apply
        self._filling = False
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Ausdruck", "Wert"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._changed)
        add = QPushButton("+ Parameter")
        add.clicked.connect(self._add)
        remove = QPushButton("Entfernen")
        remove.clicked.connect(self._remove)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        hint = QLabel("Ausdrücke dürfen rechnen und andere Parameter verwenden, z. B. „breite / 2 + 3“. "
                      "Die Namen lassen sich in jedes Zahlenfeld der Werkzeuge eintragen.")
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self.table)
        layout.addLayout(row)
        layout.addWidget(buttons)
        for name, expression in parameters.items():
            self._append(name, expression)
        self._changed()

    def parameters(self) -> dict[str, str]:
        """Die Liste, wie sie in der Tabelle steht (Zeilen ohne Namen zählen nicht)."""
        result = {}
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text().strip()
            if name:
                result[name] = self.table.item(row, 1).text().strip()
        return result

    def _append(self, name: str, expression: str) -> None:
        self._filling = True
        row = self.table.rowCount()
        self.table.insertRow(row)
        cell = QTableWidgetItem(name)
        cell.setData(Qt.ItemDataRole.UserRole, name)       # bisheriger Name, um Umbenennungen zu erkennen
        self.table.setItem(row, 0, cell)
        self.table.setItem(row, 1, QTableWidgetItem(expression))
        value = QTableWidgetItem("")
        value.setFlags(value.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 2, value)
        self._filling = False

    def _add(self) -> None:
        existing = set(self.parameters())
        number = 1
        while f"d{number}" in existing:
            number += 1
        self._append(f"d{number}", "10")
        self._changed()
        self.table.setCurrentCell(self.table.rowCount() - 1, 0)

    def _remove(self) -> None:
        if self.table.currentRow() >= 0:
            self.table.removeRow(self.table.currentRow())
            self._changed()

    def _changed(self, _item=None) -> None:
        """Übernimmt die Liste ins Modell und zeigt je Zeile den Wert oder die Fehlermeldung."""
        if self._filling:
            return
        self._filling = True
        # Umbenannte Parameter: die Ausdrücke der anderen Zeilen ziehen sofort mit
        renames = {}
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            old, new = cell.data(Qt.ItemDataRole.UserRole), cell.text().strip()
            if not new or old == new:
                continue
            try:
                pm.check_name(new)
            except pm.ParameterError:
                continue                # ungültiger Name: wird unten als Fehler angezeigt
            renames[old] = new
            cell.setData(Qt.ItemDataRole.UserRole, new)
            for other in range(self.table.rowCount()):
                expression = self.table.item(other, 1)
                expression.setText(pm.rename(expression.text(), old, new))
        self._filling = False
        values, errors = self._apply(self.parameters(), renames)
        self._filling = True
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text().strip()
            cell = self.table.item(row, 2)
            if name in values:
                cell.setText(QLocale().toString(values[name], "f", 3))
                cell.setForeground(QColor("#e6e9ef"))
            else:
                cell.setText(errors.get(name, "Name fehlt"))
                cell.setForeground(QColor("#ff7b7b"))
        self._filling = False
