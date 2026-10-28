# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Einstellungen des Programms: Mausbelegung der Ansicht und Tastenkürzel der Werkzeuge.

Gespeichert wird über QSettings; ohne eigene Wahl gelten die Vorgaben, die sich an Fusion 360 anlehnen.
"""

from __future__ import annotations

import copy
import json

from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QKeySequenceEdit,
    QLabel, QLineEdit, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

# Womit die Ansicht gedreht wird (zusätzlich zum Ziehen mit der linken Taste im Modell): Kennung -> Beschriftung
ORBIT_BINDINGS = {
    "shift_middle": "Umschalt + mittlere Maustaste (wie Fusion 360)",
    "ctrl_middle": "Strg + mittlere Maustaste",
    "right": "Rechte Maustaste ziehen (dann ohne Wischgeste im Menü)",
}
DEFAULT_ORBIT = "shift_middle"
# Was Ziehen mit der linken Taste im Modell tut: Kennung -> Beschriftung
LEFT_DRAG = {
    "select": "Rahmenauswahl (wie Fusion 360)",
    "orbit": "Ansicht drehen (frühere WolfCAD-Art)",
}
DEFAULT_LEFT_DRAG = "select"
# Vorgaben der Tastenkürzel: Werkzeug -> Taste, getrennt nach Modell und Skizze (dieselbe Taste darf in beiden vorkommen)
DEFAULT_MODEL_KEYS = {"Drücken/Ziehen": "Q", "Extrusion": "E", "Bohrung": "H", "Abrunden": "F", "Verschieben": "M",
                      "Gelenk": "J", "Messen": "I", "Skizze": "S"}
DEFAULT_SKETCH_KEYS = {"Linie": "L", "Rechteck": "R", "Kreis": "C", "Bogen": "A", "Bemaßung": "D", "Stutzen": "T",
                       "Versatz": "O", "Konstruktion": "X", "Projizieren": "P", "Spline": "S", "Abrunden": "F"}


# Makros: eine Taste führt mehrere Befehle nacheinander aus (Werkzeuge der Leiste, Ansichten, Umschalter). Die
# Vorgaben lassen sich in den Einstellungen ändern, löschen und ergänzen.
MACRO_SEPARATOR = "; "
# Befehle außer den Werkzeugen der Leiste, die ein Makro ausführen kann (das Hauptfenster setzt sie um)
VIEW_COMMANDS = ("Ansicht: Vorne", "Ansicht: Oben", "Ansicht: Rechts", "Ansicht: Isometrisch", "Darstellung umschalten",
                 "Raster umschalten", "Auswahl aufheben", "Werkzeug bestätigen")
DEFAULT_MACROS = [
    {"name": "Skizze von oben", "key": "Ctrl+Shift+1", "steps": ["Ansicht: Oben", "Skizze"]},
    {"name": "Neu berechnen und einpassen", "key": "Ctrl+Shift+2", "steps": ["Alle berechnen", "Einpassen"]},
    {"name": "Drahtgitter umschalten", "key": "Ctrl+Shift+3", "steps": ["Darstellung umschalten"]},
    {"name": "Isometrisch einpassen", "key": "Ctrl+Shift+4", "steps": ["Ansicht: Isometrisch", "Einpassen"]},
    {"name": "Für 3D-Druck exportieren", "key": "Ctrl+Shift+5", "steps": ["Alle berechnen", "Export"]},
]


def macros() -> list[dict]:
    """Die eingestellten Makros (je Eintrag Name, Taste, Befehle); ohne eigene Wahl die Vorgaben."""
    saved = _store().value("makros/liste")
    if saved:
        try:
            found = json.loads(saved)
            if isinstance(found, list):
                return [{"name": str(m.get("name", "")), "key": str(m.get("key", "")),
                         "steps": [str(step) for step in m.get("steps", [])]} for m in found if isinstance(m, dict)]
        except (ValueError, TypeError):
            pass
    return copy.deepcopy(DEFAULT_MACROS)


def set_macros(entries: list[dict]) -> None:
    _store().setValue("makros/liste", json.dumps(entries, ensure_ascii=False))


def _store() -> QSettings:
    return QSettings("WolfCAD", "WolfCAD")


def orbit_binding() -> str:
    """Kennung der gewählten Mausbelegung zum Drehen der Ansicht."""
    value = _store().value("maus/drehen", DEFAULT_ORBIT)
    return value if value in ORBIT_BINDINGS else DEFAULT_ORBIT


def set_orbit_binding(value: str) -> None:
    _store().setValue("maus/drehen", value if value in ORBIT_BINDINGS else DEFAULT_ORBIT)


def left_drag() -> str:
    """Kennung der gewählten Wirkung beim Ziehen mit der linken Taste."""
    value = _store().value("maus/links", DEFAULT_LEFT_DRAG)
    return value if value in LEFT_DRAG else DEFAULT_LEFT_DRAG


def set_left_drag(value: str) -> None:
    _store().setValue("maus/links", value if value in LEFT_DRAG else DEFAULT_LEFT_DRAG)


def tool_hints() -> bool:
    """Erklärkarten beim Überfahren der Werkzeuge zeigen (Vorgabe: ja)."""
    return str(_store().value("oberflaeche/werkzeughilfe", "true")).lower() in ("true", "1")


def set_tool_hints(value: bool) -> None:
    _store().setValue("oberflaeche/werkzeughilfe", "true" if value else "false")


def _keys(group: str, defaults: dict[str, str], tools: list[str] | None = None) -> dict[str, str]:
    """Werkzeug -> Taste: die Vorgaben, überschrieben von den gespeicherten Einträgen (leer = kein Kürzel)."""
    store, result = _store(), dict(defaults)
    for tool in tools or defaults:
        saved = store.value(f"kuerzel/{group}/{tool}")
        if saved is not None:
            result[tool] = str(saved)
    return {tool: key for tool, key in result.items() if key}


def model_keys(tools: list[str] | None = None) -> dict[str, str]:
    return _keys("modell", DEFAULT_MODEL_KEYS, tools)


def sketch_keys(tools: list[str] | None = None) -> dict[str, str]:
    return _keys("skizze", DEFAULT_SKETCH_KEYS, tools)


def set_keys(group: str, keys: dict[str, str]) -> None:
    """Speichert die Kürzel einer Gruppe ("modell" oder "skizze"): Werkzeug -> Taste, leer löscht das Kürzel."""
    store = _store()
    for tool, key in keys.items():
        store.setValue(f"kuerzel/{group}/{tool}", key)


def reset() -> None:
    """Stellt Mausbelegung und alle Tastenkürzel auf die Vorgaben zurück."""
    store = _store()
    store.remove("maus")
    store.remove("kuerzel")
    store.remove("makros")


class SettingsDialog(QDialog):
    """Einstellungen: Mausbelegung und Tastenkürzel für die Werkzeuge im Modell und in der Skizze."""

    def __init__(self, model_tools: list[str], sketch_tools: list[str], parent=None, commands: list[str] | None = None):
        super().__init__(parent)
        # was ein Makro ausführen kann (ohne Angabe: Zusatzbefehle und die Werkzeuge der Leiste)
        self._commands = list(commands) if commands else [*VIEW_COMMANDS, *model_tools]
        self.setWindowTitle("Einstellungen")
        self.resize(560, 640)
        self._model_tools, self._sketch_tools = model_tools, sketch_tools
        tabs = QTabWidget()
        # --- Maus ---
        mouse = QWidget()
        mouse_form = QFormLayout(mouse)
        self._orbit = QComboBox()
        for key, label in ORBIT_BINDINGS.items():
            self._orbit.addItem(label, key)
        mouse_form.addRow("Ansicht drehen", self._orbit)
        self._left = QComboBox()
        for key, label in LEFT_DRAG.items():
            self._left.addItem(label, key)
        mouse_form.addRow("Linke Maustaste ziehen", self._left)
        # Erklärkarten der Werkzeugleiste (Runde 77)
        self._hints = QCheckBox("Beim Überfahren erklären, was ein Werkzeug tut und wie es geht")
        self._hints.setChecked(tool_hints())
        mouse_form.addRow("Werkzeughilfe", self._hints)
        hint = QLabel("Rahmenauswahl: von links nach rechts wird nur ganz Umschlossenes gewählt, von rechts nach links "
                      "alles Berührte. Die mittlere Taste verschiebt, das Rad zoomt.\n"
                      "Rechte Maustaste: kurzer Klick öffnet das runde Menü; gedrückt halten und in eine Richtung "
                      "wischen wählt das Feld sofort.")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        mouse_form.addRow(hint)
        tabs.addTab(mouse, "Maus")
        # --- Tastenkürzel ---
        self._editors: dict[tuple[str, str], QKeySequenceEdit] = {}
        for group, title, tools in (("modell", "Tasten im Modell", model_tools), ("skizze", "Tasten in der Skizze", sketch_tools)):
            page = QWidget()
            form = QFormLayout(page)
            for tool in tools:
                editor = QKeySequenceEdit()
                editor.setMaximumSequenceLength(1)
                editor.setClearButtonEnabled(True)
                self._editors[(group, tool)] = editor
                form.addRow(tool, editor)
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setWidget(page)
            tabs.addTab(area, title)
        tabs.addTab(self._macro_page(), "Makros")
        self._warning = QLabel()
        self._warning.setObjectName("hint")
        defaults = QPushButton("Vorgaben wiederherstellen")
        defaults.clicked.connect(self._load_defaults)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(defaults, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs, 1)
        layout.addWidget(self._warning)
        layout.addWidget(buttons)
        self._fill(orbit_binding(), model_keys(model_tools), sketch_keys(sketch_tools))
        self._fill_macros(macros())

    # --- Makros ---------------------------------------------------------------

    def _macro_page(self) -> QWidget:
        """Reiter „Makros“: Tabelle aus Name, Taste und Befehlen; darunter Befehl anhängen, Makro neu/löschen."""
        page = QWidget()
        layout = QVBoxLayout(page)
        hint = QLabel("Ein Makro führt seine Befehle nacheinander aus (getrennt durch „;“). Befehle sind die Werkzeuge "
                      "der Leiste sowie Ansichten und Umschalter aus der Liste unten.")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        layout.addWidget(hint)
        self._macro_table = QTableWidget(0, 3)
        self._macro_table.setHorizontalHeaderLabels(["Name", "Taste", "Befehle"])
        self._macro_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._macro_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self._macro_table, 1)
        row = QHBoxLayout()
        self._macro_command = QComboBox()
        self._macro_command.addItems(self._commands)
        append = QPushButton("Befehl anhängen")
        append.clicked.connect(self._append_command)
        row.addWidget(self._macro_command, 1)
        row.addWidget(append)
        layout.addLayout(row)
        row = QHBoxLayout()
        add = QPushButton("Neues Makro")
        add.clicked.connect(lambda: self._add_macro_row({"name": "Neues Makro", "key": "", "steps": []}))
        remove = QPushButton("Makro löschen")
        remove.clicked.connect(self._remove_macro_row)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        return page

    def _add_macro_row(self, macro: dict) -> None:
        number = self._macro_table.rowCount()
        self._macro_table.insertRow(number)
        self._macro_table.setItem(number, 0, QTableWidgetItem(macro["name"]))
        key = QKeySequenceEdit(QKeySequence(macro["key"]))
        key.setMaximumSequenceLength(1)
        key.setClearButtonEnabled(True)
        self._macro_table.setCellWidget(number, 1, key)
        steps = QLineEdit(MACRO_SEPARATOR.join(macro["steps"]))
        self._macro_table.setCellWidget(number, 2, steps)

    def _fill_macros(self, entries: list[dict]) -> None:
        self._macro_table.setRowCount(0)
        for macro in entries:
            self._add_macro_row(macro)

    def _append_command(self) -> None:
        """Hängt den gewählten Befehl an das markierte Makro an (ohne Markierung an das letzte)."""
        number = self._macro_table.currentRow()
        if number < 0:
            number = self._macro_table.rowCount() - 1
        if number < 0:
            return
        editor = self._macro_table.cellWidget(number, 2)
        text = editor.text().strip()
        editor.setText(f"{text}{MACRO_SEPARATOR if text else ''}{self._macro_command.currentText()}")

    def _remove_macro_row(self) -> None:
        number = self._macro_table.currentRow()
        if number >= 0:
            self._macro_table.removeRow(number)

    def macro_values(self) -> list[dict]:
        """Die Makros aus der Tabelle (leere Namen fallen weg)."""
        found = []
        for number in range(self._macro_table.rowCount()):
            name = (self._macro_table.item(number, 0).text() if self._macro_table.item(number, 0) else "").strip()
            key = self._macro_table.cellWidget(number, 1).keySequence().toString()
            steps = [step.strip() for step in self._macro_table.cellWidget(number, 2).text().split(";") if step.strip()]
            if name:
                found.append({"name": name, "key": key, "steps": steps})
        return found

    def _fill(self, orbit: str, model: dict[str, str], sketch: dict[str, str], left: str | None = None) -> None:
        self._orbit.setCurrentIndex(max(0, self._orbit.findData(orbit)))
        self._left.setCurrentIndex(max(0, self._left.findData(left or left_drag())))
        for (group, tool), editor in self._editors.items():
            editor.setKeySequence(QKeySequence((model if group == "modell" else sketch).get(tool, "")))

    def _load_defaults(self) -> None:
        self._fill(DEFAULT_ORBIT, DEFAULT_MODEL_KEYS, DEFAULT_SKETCH_KEYS, DEFAULT_LEFT_DRAG)
        self._fill_macros(copy.deepcopy(DEFAULT_MACROS))

    def values(self) -> tuple[str, dict[str, str], dict[str, str]]:
        """Die eingestellte Mausbelegung und die Kürzel für Modell und Skizze (Werkzeug -> Taste, leer = keins)."""
        groups: dict[str, dict[str, str]] = {"modell": {}, "skizze": {}}
        for (group, tool), editor in self._editors.items():
            groups[group][tool] = editor.keySequence().toString()
        return self._orbit.currentData(), groups["modell"], groups["skizze"]

    def _accept(self) -> None:
        orbit, model, sketch = self.values()
        for title, keys in (("im Modell", model), ("in der Skizze", sketch)):
            used = [key for key in keys.values() if key]
            double = sorted({key for key in used if used.count(key) > 1})
            if double:
                self._warning.setText(f"Die Taste {', '.join(double)} ist {title} doppelt vergeben – bitte ändern.")
                return
        entries = self.macro_values()
        keys = [entry["key"] for entry in entries if entry["key"]]
        double = sorted({key for key in keys if keys.count(key) > 1})
        clash = sorted({key for key in keys if key in model.values() or key in sketch.values()})
        unknown = sorted({step for entry in entries for step in entry["steps"] if step not in self._commands})
        if double or clash or unknown:
            problems = []
            if double:
                problems.append(f"Taste {', '.join(double)} ist mehreren Makros zugeordnet")
            if clash:
                problems.append(f"Taste {', '.join(clash)} ist schon ein Werkzeugkürzel")
            if unknown:
                problems.append(f"unbekannte Befehle: {', '.join(unknown)}")
            self._warning.setText("Makros: " + "; ".join(problems) + " – bitte ändern.")
            return
        set_orbit_binding(orbit)
        set_left_drag(self._left.currentData())
        set_tool_hints(self._hints.isChecked())
        set_macros(entries)
        set_keys("modell", model)
        set_keys("skizze", sketch)
        self.accept()
