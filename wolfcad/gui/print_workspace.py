# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Druck-Reiter: Nachbau des Arbeitsablaufs von Creality Print direkt in WolfCAD.

Aufbau wie im Original:
  oben     Werkzeugleiste – Vorbereiten/Vorschau, Objekte hinzufügen, anordnen, auf Fläche legen, Slicen, Drucken
  links    Objektliste mit Lage, Drehung, Maßstab und Filament je Objekt
  Mitte    Druckbett in 3D (Objekte anklicken und mit der Maus verschieben); nach dem Slicen die Bahnen mit
           Schichtregler und Legende der Bahnarten
  rechts   Drucker (Profil, Netzwerkdrucker mit Zustand), Filamente (CFS-Fächer), Prozess mit den Reitern
           Qualität/Festigkeit/Geschwindigkeit/Stützen/Sonstiges sowie Filament- und Druckereinstellungen

Gerechnet wird mit dem Kern von Creality Print (printing/slicer.py), gedruckt über Moonraker (printing/moonraker.py).
Bedienung, Darstellung und Verwaltung der Einstellungen sind eigene WolfCAD-Teile – keine Grafiken des Originals.
"""
from __future__ import annotations

import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QProcess, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QMenu, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from ..core import meshes
from ..printing import catalog, gcode, moonraker, plate, profiles, slicer
from .icons import icon
from .viewport import GL_LINES, GL_TRIANGLES, Viewport, _Layer

WORK_ROOT = Path(tempfile.gettempdir()) / "wolfcad-druck"     # Arbeitsordner für Modelle, Profile und G-Code
STATUS_SECONDS = 4                                             # Abfrageabstand des Druckerzustands
# Einstellungen, die sich je Objekt ändern lassen (Objekteinstellungen)
OBJECT_KEYS = ["sparse_infill_density", "sparse_infill_pattern", "wall_loops", "top_shell_layers",
               "bottom_shell_layers", "enable_support", "brim_type", "print_flow_ratio", "outer_wall_speed"]
# Farben der Filamente 1 … 8 für die Bemalung (wie die Fachfarben im Original: Weiß, Schwarz, Rot, Blau …)
PAINT_COLOURS = [(0.95, 0.95, 0.95), (0.15, 0.15, 0.15), (0.90, 0.22, 0.21), (0.12, 0.53, 0.90),
                 (0.26, 0.63, 0.28), (0.99, 0.85, 0.21), (0.56, 0.14, 0.67), (0.98, 0.55, 0.0)]
_SETTINGS = ("WolfCAD", "WolfCAD")


def _settings() -> QSettings:
    return QSettings(*_SETTINGS)


class _Relay(QObject):
    """Bringt Ergebnisse aus dem Hintergrund-Thread (Druckerabfrage, Hochladen) in den Oberflächen-Thread: ein
    Signal über Thread-Grenzen wird von Qt in die Ereignisschleife des Empfängers eingereiht."""
    deliver = Signal(object)

    def __init__(self):
        super().__init__()
        self.alive = True           # nach dem Schließen des Reiters kommen späte Ergebnisse nicht mehr an
        self.deliver.connect(lambda call: call() if self.alive else None)

    def later(self, call) -> None:
        if self.alive:
            self.deliver.emit(call)


# --------------------------------------------------------------------------------------------------------------
# 3D-Ansicht des Druckbetts
# --------------------------------------------------------------------------------------------------------------

class PrintView(Viewport):
    """Die Ansicht mit Druckbett, Objekten und (nach dem Slicen) den Druckbahnen."""

    object_clicked = Signal(int)            # Nummer des angeklickten Objekts (−1: daneben)
    object_dragged = Signal(int, float, float)  # Objekt, neue Mitte x/y auf dem Bett
    drag_finished = Signal()
    painted = Signal(int, object)           # Objekt, Treffpunkt des Pinsels (beim Malen)
    paint_finished = Signal()
    lay_requested = Signal(object)          # Außennormale der angeklickten Auflagefläche („Auf Fläche legen“)

    def __init__(self, parent=None):
        super().__init__(parent)
        extra = {
            "bed_fill": _Layer(GL_TRIANGLES, (0.16, 0.18, 0.22), False),
            "bed_grid": _Layer(GL_LINES, (0.27, 0.30, 0.36), False),
            "bed_border": _Layer(GL_LINES, (0.45, 0.62, 0.95), False, line_width=2.0),
            "bed_volume": _Layer(GL_LINES, (0.30, 0.36, 0.48), False, line_width=1.0),
            "print_objects": _Layer(GL_TRIANGLES, (0.70, 0.74, 0.80), True),
            "print_selected": _Layer(GL_TRIANGLES, (0.30, 0.62, 1.00), True),
            "print_outside": _Layer(GL_TRIANGLES, (0.92, 0.32, 0.28), True),
            "gc_travel": _Layer(GL_LINES, (0.35, 0.55, 0.95), False, line_width=0.6),
        }
        for kind, (_label, colour) in gcode.FEATURES.items():
            extra["gc_" + kind] = _Layer(GL_LINES, colour, False, line_width=1.4)
        extra["gc_current"] = _Layer(GL_LINES, (1.0, 1.0, 1.0), False, line_width=1.8)
        # Bemalung: Stützen erzwingen (grün) / blockieren (rot), Farben je Filament (Palette des Reiters)
        extra["paint_enforce"] = _Layer(GL_TRIANGLES, (0.25, 0.85, 0.35), True)
        extra["paint_block"] = _Layer(GL_TRIANGLES, (0.95, 0.30, 0.30), True)
        for number, colour in enumerate(PAINT_COLOURS, 1):
            extra[f"paint_colour_{number}"] = _Layer(GL_TRIANGLES, colour, True)
        self._layers.update(extra)
        self.paint_mode: str | None = None     # None, "support" oder "color": dann malt die linke Maustaste
        # „Auf Fläche legen“ (Runde 87): Auflageflächen des gewählten Objekts, je (Normale, Dreiecke); die unter der
        # Maus leuchtet, ein Klick meldet ihre Normale (lay_requested)
        self.lay_faces: list | None = None
        self._lay_hover: int | None = None
        extra_lay = {"lay_faces": _Layer(GL_TRIANGLES, (0.30, 0.70, 1.00), False),
                     "lay_hover": _Layer(GL_TRIANGLES, (1.00, 0.60, 0.15), False)}
        extra_lay["lay_faces"].alpha, extra_lay["lay_hover"].alpha = 0.35, 0.75
        self._layers.update(extra_lay)
        self._painting = False
        self._objects: list[np.ndarray] = []
        self._dragging: int | None = None
        self._drag_offset = (0.0, 0.0)
        self._drag_height = 0.0
        self.set_ground_visible(False)

    def set_bed(self, width: float, depth: float, height: float) -> None:
        """Bettfläche mit 10-mm-Raster, Rand und Umriss des Bauraums."""
        quad = np.array([[0, 0, -0.05], [width, 0, -0.05], [width, depth, -0.05],
                         [0, 0, -0.05], [width, depth, -0.05], [0, depth, -0.05]], float)
        self.set_geometry("bed_fill", quad)
        lines = []
        for x in np.arange(0, width + 1e-6, 10.0):
            lines += [[x, 0, 0], [x, depth, 0]]
        for y in np.arange(0, depth + 1e-6, 10.0):
            lines += [[0, y, 0], [width, y, 0]]
        self.set_geometry("bed_grid", np.array(lines, float))
        corners = [[0, 0], [width, 0], [width, depth], [0, depth]]
        border = []
        for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1]):
            border += [[ax, ay, 0], [bx, by, 0]]
        self.set_geometry("bed_border", np.array(border, float))
        volume = []
        for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1]):
            volume += [[ax, ay, height], [bx, by, height], [ax, ay, 0], [ax, ay, height]]
        self.set_geometry("bed_volume", np.array(volume, float))
        self.fit(np.array([[0, 0, 0], [width, depth, height * 0.4]], float))

    def set_objects(self, placed: list[np.ndarray], selected: int, outside: list[bool], visible: bool = True) -> None:
        """Objekte zeichnen: gewähltes blau, außerhalb des Bauraums rot."""
        self._objects = placed
        groups = {"print_objects": [], "print_selected": [], "print_outside": []}
        for index, triangles in enumerate(placed):
            layer = "print_outside" if outside[index] else ("print_selected" if index == selected else
                                                            "print_objects")
            groups[layer].append(triangles.reshape(-1, 3))
        for name, parts in groups.items():
            data = np.concatenate(parts) if parts and visible else np.zeros((0, 3))
            self.set_geometry(name, data)

    def set_paint(self, items: list, visible: bool = True) -> None:
        """Bemalte Dreiecke aller Objekte zeigen (liegen deckungsgleich über dem Objekt)."""
        groups: dict[str, list] = {"paint_enforce": [], "paint_block": []}
        groups.update({f"paint_colour_{n}": [] for n in range(1, len(PAINT_COLOURS) + 1)})
        for triangles, supports, colours in items:
            if supports is not None:
                groups["paint_enforce"].append(triangles[supports == 1].reshape(-1, 3))
                groups["paint_block"].append(triangles[supports == 2].reshape(-1, 3))
            if colours is not None:
                for number in range(1, len(PAINT_COLOURS) + 1):
                    groups[f"paint_colour_{number}"].append(triangles[colours == number].reshape(-1, 3))
        for name, parts in groups.items():
            parts = [part for part in parts if len(part)]
            self.set_geometry(name, np.concatenate(parts) if parts and visible else np.zeros((0, 3)))

    def set_lay_faces(self, faces: list | None) -> None:
        """Auflageflächen zeigen (None beendet den Modus). Die Flächen liegen ein Stück außen vor dem Objekt, damit
        sie nicht mit seiner Oberfläche flimmern."""
        self.lay_faces = [(normal, triangles + normal * 0.08) for normal, triangles, _area in faces] if faces else None
        self._lay_hover = None
        self._show_lay_faces()

    def _show_lay_faces(self) -> None:
        faces = self.lay_faces or []
        rest = [triangles.reshape(-1, 3) for number, (_n, triangles) in enumerate(faces) if number != self._lay_hover]
        hover = faces[self._lay_hover][1].reshape(-1, 3) if self._lay_hover is not None and faces else np.zeros((0, 3))
        self.set_geometry("lay_faces", np.concatenate(rest) if rest else np.zeros((0, 3)))
        self.set_geometry("lay_hover", hover)

    def _lay_face_at(self, x: float, y: float) -> int | None:
        """Die vorderste Auflagefläche unter dem Fensterpunkt."""
        if not self.lay_faces:
            return None
        eye, direction, _pixel = self.pick_ray(x, y)
        best, best_distance = None, np.inf
        for number, (_normal, triangles) in enumerate(self.lay_faces):
            distance = _ray_triangles(eye, direction, triangles)
            if distance < best_distance:
                best, best_distance = number, distance
        return best

    def _hit_point(self, x: float, y: float):
        """Getroffenes Objekt und Treffpunkt im Raum (für den Pinsel); (−1, None) ohne Treffer."""
        eye, direction, _pixel = self.pick_ray(x, y)
        best, best_distance = -1, np.inf
        for index, triangles in enumerate(self._objects):
            distance = _ray_triangles(eye, direction, triangles)
            if distance < best_distance:
                best, best_distance = index, distance
        return (best, eye + best_distance * direction) if best >= 0 else (-1, None)

    def set_toolpath(self, path: gcode.Toolpath | None, upto: int, kinds: set[str], travel: bool) -> None:
        """Bahnen bis zur Schicht `upto` (einschließlich); die oberste Schicht hell hervorgehoben."""
        for kind in gcode.FEATURES:
            data = np.zeros((0, 3))
            if path is not None and kind in path.segments and kind in kinds:
                mask = path.layers[kind] <= upto
                data = path.segments[kind][mask].reshape(-1, 3)
            self.set_geometry("gc_" + kind, data)
        travel_data = np.zeros((0, 3))
        if path is not None and travel and len(path.travel):
            travel_data = path.travel[path.travel_layers == upto].reshape(-1, 3)
        self.set_geometry("gc_travel", travel_data)
        current = []
        if path is not None:
            for kind in kinds:
                if kind in path.segments and kind == "Outer wall":
                    current.append(path.segments[kind][path.layers[kind] == upto].reshape(-1, 3))
        self.set_geometry("gc_current", np.concatenate(current) if current else np.zeros((0, 3)))

    # --- Objekte anklicken und ziehen ---------------------------------------------------------------------

    def _plane_point(self, x: float, y: float, height: float) -> tuple[float, float] | None:
        """Stelle unter dem Fensterpunkt auf der waagrechten Ebene z = height."""
        eye, direction, _pixel = self.pick_ray(x, y)
        if abs(direction[2]) < 1e-6:
            return None
        along = (height - eye[2]) / direction[2]
        if along <= 0:
            return None
        return float(eye[0] + along * direction[0]), float(eye[1] + along * direction[1])

    def _object_at(self, x: float, y: float) -> tuple[int, float]:
        """Das vorderste Objekt unter dem Fensterpunkt und die Höhe des Treffpunkts (Strahl gegen Dreiecke,
        Möller–Trumbore); (−1, 0), wenn keins getroffen wird."""
        eye, direction, _pixel = self.pick_ray(x, y)
        best, best_distance = -1, np.inf
        for index, triangles in enumerate(self._objects):
            low, high = triangles.reshape(-1, 3).min(0), triangles.reshape(-1, 3).max(0)
            if not _ray_hits_box(eye, direction, low, high):
                continue
            distance = _ray_triangles(eye, direction, triangles)
            if distance < best_distance:
                best, best_distance = index, distance
        height = float(eye[2] + best_distance * direction[2]) if best >= 0 else 0.0
        return best, height

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.lay_faces \
                and not self._orbiting(event.buttons(), event.modifiers()):
            number = self._lay_face_at(event.position().x(), event.position().y())
            if number is not None:
                self.lay_requested.emit(self.lay_faces[number][0])
                return
        if event.button() == Qt.MouseButton.LeftButton and self.paint_mode \
                and not self._orbiting(event.buttons(), event.modifiers()):
            index, point = self._hit_point(event.position().x(), event.position().y())
            if index >= 0:
                self._painting = True
                self.painted.emit(index, point)
                return
        if event.button() == Qt.MouseButton.LeftButton and not self._orbiting(event.buttons(), event.modifiers()):
            x, y = event.position().x(), event.position().y()
            index, height = self._object_at(x, y)
            self.object_clicked.emit(index)
            if index >= 0:
                # gezogen wird in der Höhe, an der das Objekt gegriffen wurde: so bleibt der Griffpunkt unter der
                # Maus (auf Bodenhöhe gerechnet wanderte ein hohes Objekt schräg mit)
                self._drag_height = height
                point = self._plane_point(x, y, height)
                low, high = self._objects[index].reshape(-1, 3).min(0), self._objects[index].reshape(-1, 3).max(0)
                centre = (low[:2] + high[:2]) / 2
                if point is not None:
                    self._dragging = index
                    self._drag_offset = (centre[0] - point[0], centre[1] - point[1])
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.lay_faces and not event.buttons():
            hover = self._lay_face_at(event.position().x(), event.position().y())
            if hover != self._lay_hover:
                self._lay_hover = hover
                self._show_lay_faces()
        if self._painting:
            index, point = self._hit_point(event.position().x(), event.position().y())
            if index >= 0:
                self.painted.emit(index, point)
            return
        if self._dragging is not None:
            point = self._plane_point(event.position().x(), event.position().y(), self._drag_height)
            if point is not None:
                self.object_dragged.emit(self._dragging, point[0] + self._drag_offset[0],
                                         point[1] + self._drag_offset[1])
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._painting:
            self._painting = False
            self.paint_finished.emit()
            return
        if self._dragging is not None:
            self._dragging = None
            self.drag_finished.emit()
            return
        super().mouseReleaseEvent(event)


def _ray_hits_box(origin, direction, low, high) -> bool:
    with np.errstate(divide="ignore", invalid="ignore"):
        t1, t2 = (low - origin) / direction, (high - origin) / direction
    near = np.nanmax(np.minimum(t1, t2))
    far = np.nanmin(np.maximum(t1, t2))
    return bool(far >= max(near, 0.0))


def _ray_triangles(origin, direction, triangles) -> float:
    """Abstand zum nächsten getroffenen Dreieck (inf, wenn keins)."""
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    e1, e2 = b - a, c - a
    p = np.cross(direction, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    t_vec = origin - a
    u = np.einsum("ij,ij->i", t_vec, p) * inv
    q = np.cross(t_vec, e1)
    v = (q @ direction) * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0)
    return float(t[hit].min()) if hit.any() else np.inf


# --------------------------------------------------------------------------------------------------------------
# Einstellungsseiten
# --------------------------------------------------------------------------------------------------------------

class SettingsPage(QScrollArea):
    """Eine Gruppe von Profilwerten als Formular (Zahl/Text, Auswahl oder Ja/Nein)."""

    changed = Signal(str, object)       # Schlüssel, neuer Wert (in der Form des Profils)

    def __init__(self, items: list, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        self._form = QFormLayout(body)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.setWidget(body)
        self._items = items
        self._editors: dict[str, QWidget] = {}
        self._values: dict = {}

    def show_profile(self, profile: profiles.Profile) -> None:
        """Füllt das Formular neu (geänderte Werte fett, wie das Original sie markiert)."""
        while self._form.rowCount():
            self._form.removeRow(0)
        self._editors.clear()
        for key, label, unit, choices in self._items:
            value = profile.get(key, catalog.DEFAULTS.get(key))
            if value is None:
                continue
            self._values[key] = value
            editor = self._editor(key, value, choices, unit)
            caption = QLabel(label)
            if key in profile.changes:
                font = caption.font()
                font.setBold(True)
                caption.setFont(font)
                caption.setToolTip("Gegenüber dem Profil geändert")
            self._form.addRow(caption, editor)
            self._editors[key] = editor

    def _editor(self, key: str, value, choices, unit: str) -> QWidget:
        if choices:
            box = QComboBox()
            current = catalog.scalar(value)
            for raw, text in choices.items():
                box.addItem(text, raw)
            if box.findData(current) < 0:
                box.addItem(current, current)
            box.setCurrentIndex(box.findData(current))
            box.currentIndexChanged.connect(
                lambda _i, k=key, b=box: self.changed.emit(k, catalog.like(self._values[k], b.currentData())))
            return box
        if catalog.is_flag(value) and not unit:     # „0“ mit Einheit ist eine Zahl (z. B. 0 mm), kein Schalter
            check = QCheckBox()
            check.setChecked(catalog.scalar(value) == "1")
            check.toggled.connect(lambda on, k=key: self.changed.emit(k, catalog.like(self._values[k],
                                                                                     "1" if on else "0")))
            return check
        line = QLineEdit(catalog.scalar(value))
        if unit:
            line.setPlaceholderText(unit)
            line.setToolTip(unit)
        line.editingFinished.connect(lambda k=key, l=line: self.changed.emit(k, catalog.like(self._values[k],
                                                                                            l.text())))
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line, 1)
        if unit:
            layout.addWidget(QLabel(unit))
        return row


class AllValuesPage(QWidget):
    """„Alle Werte“: jeder Schlüssel des Profils als Tabelle (für Einstellungen, die der Katalog nicht kennt)."""

    changed = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Suchen …")
        self._filter.textChanged.connect(self._apply_filter)
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Schlüssel", "Wert"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.itemChanged.connect(self._edited)
        layout.addWidget(self._filter)
        layout.addWidget(self._table, 1)
        self._values: dict = {}
        self._filling = False

    def show_profile(self, profile: profiles.Profile) -> None:
        self._filling = True
        self._values = {**profile.values, **profile.changes}
        keys = sorted(key for key in self._values if key not in ("name", "inherits", "from", "type",
                                                                "setting_id", "instantiation"))
        self._table.setRowCount(len(keys))
        for row, key in enumerate(keys):
            name = QTableWidgetItem(key)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if key in profile.changes:
                font = name.font()
                font.setBold(True)
                name.setFont(font)
            self._table.setItem(row, 0, name)
            value = self._values[key]
            text = catalog.scalar(value) if not isinstance(value, (dict, list)) or isinstance(value, list) \
                else json.dumps(value)
            self._table.setItem(row, 1, QTableWidgetItem(text))
        self._filling = False
        self._apply_filter(self._filter.text())

    def _apply_filter(self, text: str) -> None:
        text = text.lower()
        for row in range(self._table.rowCount()):
            key = self._table.item(row, 0).text()
            self._table.setRowHidden(row, bool(text) and text not in key.lower())

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() != 1:
            return
        key = self._table.item(item.row(), 0).text()
        self.changed.emit(key, catalog.like(self._values.get(key, ""), item.text()))


class ProfileEditor(QWidget):
    """Profilwahl plus Reiter mit den Katalog-Gruppen und „Alle Werte“ für eine Profilart."""

    profile_changed = Signal()

    def __init__(self, kind: str, library: profiles.Library, parent=None):
        super().__init__(parent)
        self.kind, self.library = kind, library
        self.profile: profiles.Profile | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.choice = QComboBox()
        self.choice.currentTextChanged.connect(self._choose)
        reset = QToolButton()
        reset.setText("↺")
        reset.setToolTip("Eigene Änderungen an diesem Profil verwerfen")
        reset.clicked.connect(self.reset)
        top.addWidget(self.choice, 1)
        top.addWidget(reset)
        layout.addLayout(top)
        self.tabs = QTabWidget()
        self.pages: list = []
        for group, items in catalog.CATALOGS[kind].items():
            page = SettingsPage(items)
            page.changed.connect(self._set)
            self.tabs.addTab(page, group)
            self.pages.append(page)
        every = AllValuesPage()
        every.changed.connect(self._set)
        self.tabs.addTab(every, "Alle Werte")
        self.pages.append(every)
        layout.addWidget(self.tabs, 1)

    def set_names(self, names: list[str], current: str) -> None:
        self.choice.blockSignals(True)
        self.choice.clear()
        self.choice.addItems(names)
        if current in names:
            self.choice.setCurrentText(current)
        self.choice.blockSignals(False)
        self._choose(self.choice.currentText())

    def _choose(self, name: str) -> None:
        if not name:
            return
        self.profile = self.library.profile(self.kind, name)
        stored = _settings().value(f"druck/aenderungen/{self.kind}/{name}", "")
        if stored:
            try:
                self.profile.changes = json.loads(stored)
            except json.JSONDecodeError:
                pass
        self._show()
        self.profile_changed.emit()

    def _show(self) -> None:
        for page in self.pages:
            page.show_profile(self.profile)

    def _set(self, key: str, value) -> None:
        if self.profile is None:
            return
        if value == self.profile.values.get(key):
            self.profile.changes.pop(key, None)
        else:
            self.profile.changes[key] = value
        _settings().setValue(f"druck/aenderungen/{self.kind}/{self.profile.name}",
                             json.dumps(self.profile.changes, ensure_ascii=False))
        QTimer.singleShot(0, self._show)       # fett markieren (nach dem Signal, das Feld lebt noch)
        self.profile_changed.emit()

    def reset(self) -> None:
        if self.profile is None:
            return
        self.profile.changes.clear()
        _settings().remove(f"druck/aenderungen/{self.kind}/{self.profile.name}")
        self._show()
        self.profile_changed.emit()


# --------------------------------------------------------------------------------------------------------------
# Der Reiter
# --------------------------------------------------------------------------------------------------------------

class PrintWorkspace(QWidget):
    """Vorbereiten, Slicen und Drucken – ein Reiter im Hauptfenster."""

    def __init__(self, bodies_provider=None, parent=None):
        super().__init__(parent)
        # liefert [(Name, Dreiecke)] der Körper des Entwurfs; mit only_selected=True nur die gewählten
        self._bodies_provider = bodies_provider
        self.library = profiles.Library()
        self.plate = plate.Plate()
        self.plates = [self.plate]          # wie im Original mehrere Druckplatten; gerechnet wird die gezeigte
        self.selected = -1
        self._calibration = None            # laufender Kalibrierdruck (Nachbearbeitung des G-Codes)
        self.toolpath: gcode.Toolpath | None = None
        self.gcode_path: Path | None = None
        self._process: QProcess | None = None
        self._job: slicer.Job | None = None
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._relay = _Relay()
        self._status_future = None
        self.slots: list[str] = []          # Filamentprofil je CFS-Fach
        self._build()
        self._load_profiles()
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(STATUS_SECONDS * 1000)
        self._status_timer.timeout.connect(self._poll_status)
        if self.host.text().strip():
            self._status_timer.start()
            QTimer.singleShot(200, self._poll_status)
        elif self.library.available:
            # noch kein Drucker bekannt: im Hintergrund suchen (wie das Original beim ersten Start) und den
            # gefundenen samt passendem Profil übernehmen
            future = self._pool.submit(moonraker.discover)
            future.add_done_callback(lambda result: self._relay.later(lambda: self._discovered(result)))

    # --- Aufbau -------------------------------------------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.addLayout(self._build_toolbar())
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_objects())
        splitter.addWidget(self._build_view())
        splitter.addWidget(self._build_settings())
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 900, 380])
        outer.addWidget(splitter, 1)

    def _button(self, text: str, tip: str, slot, icon_name: str | None = None) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        if icon_name:
            button.setIcon(icon(icon_name))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.clicked.connect(slot)
        return button

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self.prepare_button = QPushButton("Vorbereiten")
        self.preview_button = QPushButton("Vorschau")
        for button in (self.prepare_button, self.preview_button):
            button.setCheckable(True)
            bar.addWidget(button)
        self.prepare_button.setChecked(True)
        self.prepare_button.clicked.connect(lambda: self._set_mode(False))
        self.preview_button.clicked.connect(lambda: self._set_mode(True))
        bar.addSpacing(12)
        bar.addWidget(QLabel("Platte"))
        self.plate_choice = QComboBox()
        self.plate_choice.addItem("1")
        self.plate_choice.currentIndexChanged.connect(self._show_plate)
        bar.addWidget(self.plate_choice)
        bar.addWidget(self._button("+", "Neue Druckplatte", self._add_plate))
        bar.addWidget(self._button("−", "Gezeigte Druckplatte entfernen (Objekte gehen auf Platte 1)",
                                   self._remove_plate))
        bar.addSpacing(12)
        for text, tip, slot in (
                ("Alle Körper", "Alle sichtbaren Körper des Entwurfs aufs Bett legen", lambda: self.add_from_design(False)),
                ("Gewählte Körper", "Nur die gewählten Körper aufs Bett legen", lambda: self.add_from_design(True)),
                ("Datei …", "STL- oder 3MF-Datei hinzufügen", self._add_file),
                ("Entfernen", "Gewähltes Objekt vom Bett nehmen", self._remove),
                ("Vervielfältigen …", "Kopien des gewählten Objekts anlegen", self._duplicate),
                ("Anordnen", "Alle Objekte automatisch auf dem Bett verteilen", self._arrange)):
            bar.addWidget(self._button(text, tip, slot))
        # Wie im Original: Modus „Auf Fläche legen“ – Auflageflächen anzeigen, gewünschte Fläche anklicken
        self.lay_button = QPushButton("Auf Fläche legen")
        self.lay_button.setCheckable(True)
        self.lay_button.setToolTip("Auflageflächen des gewählten Objekts zeigen; die angeklickte Fläche kommt nach "
                                   "unten auf das Bett (Esc oder erneuter Klick beendet)")
        self.lay_button.toggled.connect(self._lay_mode)
        bar.addWidget(self.lay_button)
        bar.addSpacing(12)
        # Malen wie im Original: Stützen (erzwingen/blockieren) und Farbe (Filament je Bereich)
        self.paint_support = QPushButton("Stützen malen")
        self.paint_colour = QPushButton("Farbe malen")
        for button in (self.paint_support, self.paint_colour):
            button.setCheckable(True)
            bar.addWidget(button)
        self.paint_support.toggled.connect(lambda on: self._paint_mode("support" if on else None))
        self.paint_colour.toggled.connect(lambda on: self._paint_mode("color" if on else None))
        self.paint_value = QComboBox()
        self.paint_value.setVisible(False)
        bar.addWidget(self.paint_value)
        self.brush = QDoubleSpinBox()
        self.brush.setRange(0.5, 50)
        self.brush.setValue(4.0)
        self.brush.setSuffix(" mm")
        self.brush.setToolTip("Pinselradius")
        self.brush.setVisible(False)
        bar.addWidget(self.brush)
        calibrate = QToolButton()
        calibrate.setText("Kalibrierung")
        calibrate.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(calibrate)
        from ..printing import calibration
        for title in calibration.CALIBRATIONS:
            menu.addAction(title, lambda t=title: self.start_calibration(t))
        calibrate.setMenu(menu)
        bar.addWidget(calibrate)
        bar.addStretch(1)
        bar.addWidget(self._button("In Creality Print öffnen", "Das Bett als Projekt an Creality Print übergeben",
                                   self._open_original))
        self.slice_button = QPushButton("Slicen")
        self.slice_button.setToolTip("Bahnen berechnen (Creality-Print-Kern)")
        self.slice_button.clicked.connect(self.slice)
        self.print_button = QPushButton("Drucken")
        self.print_button.setToolTip("G-Code an den Drucker senden und den Druck starten")
        self.print_button.clicked.connect(lambda: self.send(True))
        self.upload_button = QToolButton()
        self.upload_button.setText("Nur hochladen")
        self.upload_button.clicked.connect(lambda: self.send(False))
        export = self._button("G-Code speichern …", "Den berechneten G-Code als Datei speichern", self._save_gcode)
        for widget in (self.slice_button, self.print_button, self.upload_button, export):
            bar.addWidget(widget)
        return bar

    def _build_objects(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Objekte"))
        self.object_list = QListWidget()
        self.object_list.currentRowChanged.connect(self._select)
        layout.addWidget(self.object_list, 1)
        group = QGroupBox("Gewähltes Objekt")
        form = QFormLayout(group)
        self.pos_x, self.pos_y = QDoubleSpinBox(), QDoubleSpinBox()
        for box in (self.pos_x, self.pos_y):
            box.setRange(-1000, 2000)
            box.setDecimals(2)
            box.setSuffix(" mm")
            box.valueChanged.connect(self._moved_by_fields)
        form.addRow("X", self.pos_x)
        form.addRow("Y", self.pos_y)
        turn = QWidget()
        turn_layout = QGridLayout(turn)
        turn_layout.setContentsMargins(0, 0, 0, 0)
        self.turn_angle = QDoubleSpinBox()
        self.turn_angle.setRange(-360, 360)
        self.turn_angle.setValue(90)
        self.turn_angle.setSuffix(" °")
        turn_layout.addWidget(self.turn_angle, 0, 0, 1, 3)
        for column, axis in enumerate("XYZ"):
            button = QPushButton(f"um {axis}")
            button.clicked.connect(lambda _c=False, a=axis: self._rotate(a))
            turn_layout.addWidget(button, 1, column)
        form.addRow("Drehen", turn)
        self.scale = QDoubleSpinBox()
        self.scale.setRange(1, 1000)
        self.scale.setSuffix(" %")
        self.scale.setValue(100)
        self.scale.valueChanged.connect(self._scaled)
        form.addRow("Maßstab", self.scale)
        self.size_label = QLabel("–")
        form.addRow("Größe", self.size_label)
        self.filament_slot = QComboBox()
        self.filament_slot.currentIndexChanged.connect(self._slot_chosen)
        form.addRow("Filament", self.filament_slot)
        layout.addWidget(group)
        tools = QGroupBox("Werkzeuge")
        grid = QGridLayout(tools)
        entries = [("Spiegeln X", lambda: self._mirror("X")), ("Spiegeln Y", lambda: self._mirror("Y")),
                   ("Spiegeln Z", lambda: self._mirror("Z")), ("Schneiden …", self._cut),
                   ("In Teile zerlegen", self._split_parts), ("Vereinfachen …", self._simplify),
                   ("Objekteinstellungen …", self._object_settings), ("Bemalung löschen", self._clear_paint)]
        for number, (text, slot) in enumerate(entries):
            button = QPushButton(text)
            button.clicked.connect(slot)
            grid.addWidget(button, number // 2, number % 2)
        layout.addWidget(tools)
        return panel

    def _build_view(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.view = PrintView()
        self.view.object_clicked.connect(self._clicked)
        self.view.lay_requested.connect(self._lay_on_clicked)
        self.view.object_dragged.connect(self._dragged)
        self.view.drag_finished.connect(self._refresh_objects)
        self.view.painted.connect(self._paint_at)
        self.view.paint_finished.connect(self._refresh_objects)
        row.addWidget(self.view, 1)
        self.layer_slider = QSlider(Qt.Orientation.Vertical)
        self.layer_slider.setRange(0, 0)
        self.layer_slider.valueChanged.connect(self._layer_moved)
        self.layer_slider.setVisible(False)
        row.addWidget(self.layer_slider)
        layout.addLayout(row, 1)
        self.legend = QWidget()
        legend_layout = QHBoxLayout(self.legend)
        legend_layout.setContentsMargins(0, 0, 0, 0)
        self.kind_boxes: dict[str, QCheckBox] = {}
        for kind, (label, colour) in gcode.FEATURES.items():
            box = QCheckBox(label)
            box.setChecked(True)
            box.setStyleSheet(f"QCheckBox {{ color: {QColor.fromRgbF(*colour).name()}; }}")
            box.toggled.connect(self._show_toolpath)
            self.kind_boxes[kind] = box
            box.setVisible(False)
            legend_layout.addWidget(box)
        self.travel_box = QCheckBox("Fahrwege")
        self.travel_box.toggled.connect(self._show_toolpath)
        legend_layout.addWidget(self.travel_box)
        legend_layout.addStretch(1)
        self.legend.setVisible(False)
        layout.addWidget(self.legend)
        self.info = QLabel("Objekte aus dem Entwurf hinzufügen, dann „Slicen“.")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        # Fortschritt beim Hochladen zum Drucker (Runde 83); nur während des Sendens sichtbar
        self.upload_bar = QProgressBar()
        self.upload_bar.setRange(0, 1000)
        self.upload_bar.setTextVisible(True)
        self.upload_bar.setVisible(False)
        layout.addWidget(self.upload_bar)
        return panel

    def _build_settings(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        # Drucker
        printer_box = QGroupBox("Drucker")
        printer_form = QFormLayout(printer_box)
        self.machine = ProfileEditor("machine", self.library)
        self.machine.profile_changed.connect(self._printer_changed)
        self.host = QLineEdit(_settings().value("druck/host", "") or "")
        self.host.setPlaceholderText("IP-Adresse oder Name, z. B. K2Plus-1A2B.local")
        self.host.editingFinished.connect(self._host_changed)
        find = QToolButton()
        find.setText("Suchen")
        find.setToolTip("Creality-Drucker im Netz suchen")
        find.clicked.connect(self._discover)
        host_row = QWidget()
        host_layout = QHBoxLayout(host_row)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.addWidget(self.host, 1)
        host_layout.addWidget(find)
        printer_form.addRow("Netzwerk", host_row)
        self.status_label = QLabel("–")
        self.status_label.setWordWrap(True)
        printer_form.addRow("Zustand", self.status_label)
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        for text, action in (("Pause", "pause"), ("Fortsetzen", "resume"), ("Abbrechen", "cancel")):
            button = QPushButton(text)
            button.clicked.connect(lambda _c=False, a=action: self._printer_command(a))
            controls_layout.addWidget(button)
        printer_form.addRow("", controls)
        layout.addWidget(printer_box)
        # Kamerabild wie auf der Geräteseite des Originals; erst auf Wunsch (kostet Netz und Rechenzeit)
        from .printer_camera import PrinterCamera
        self.camera_box = QGroupBox("Kamera")
        self.camera_box.setCheckable(True)
        self.camera_box.setChecked(False)
        camera_layout = QVBoxLayout(self.camera_box)
        self.camera = PrinterCamera()
        self.camera.setVisible(False)
        camera_layout.addWidget(self.camera)
        self.camera_box.toggled.connect(self._camera_toggled)
        layout.addWidget(self.camera_box)
        # Filamente (CFS-Fächer)
        filament_box = QGroupBox("Filamente")
        filament_layout = QVBoxLayout(filament_box)
        self.slot_list = QListWidget()
        self.slot_list.setMaximumHeight(110)
        self.slot_list.currentRowChanged.connect(self._slot_selected)
        slot_buttons = QHBoxLayout()
        slot_buttons.addWidget(self._button("+", "Filament (Fach) hinzufügen", self._add_slot))
        slot_buttons.addWidget(self._button("−", "Gewähltes Filament entfernen", self._remove_slot))
        slot_buttons.addStretch(1)
        filament_layout.addWidget(self.slot_list)
        filament_layout.addLayout(slot_buttons)
        layout.addWidget(filament_box)
        # Profile
        self.settings_tabs = QTabWidget()
        self.process = ProfileEditor("process", self.library)
        self.filament = ProfileEditor("filament", self.library)
        self.filament.profile_changed.connect(self._filament_changed)
        self.settings_tabs.addTab(self.process, "Prozess")
        self.settings_tabs.addTab(self.filament, "Filament")
        self.settings_tabs.addTab(self.machine, "Drucker")
        layout.addWidget(self.settings_tabs, 1)
        return panel

    # --- Profile ------------------------------------------------------------------------------------------

    def _load_profiles(self) -> None:
        if not self.library.available:
            self.info.setText("Creality Print wurde nicht gefunden – ohne seine Profile und seinen Kern kann nicht "
                              f"geslict werden. Erwartet unter: {profiles.install_dir()}")
            return
        settings = _settings()
        printer = settings.value("druck/drucker", "") or profiles.find_printer(self.library, self.host.text())
        self.machine.set_names(self.library.printers(), printer)
        stored = settings.value("druck/filamente", "")
        try:
            self.slots = json.loads(stored) if stored else []
        except json.JSONDecodeError:
            self.slots = []
        if not self.slots:
            self.slots = [self.library.default_filament(self.machine.choice.currentText())]
        self._fill_slots()

    def _printer_changed(self) -> None:
        name = self.machine.choice.currentText()
        if not name:
            return
        _settings().setValue("druck/drucker", name)
        outline, height = self.library.bed(name)
        xs, ys = [p[0] for p in outline], [p[1] for p in outline]
        self.plate.width, self.plate.depth, self.plate.height = max(xs) - min(xs), max(ys) - min(ys), height
        self.view.set_bed(self.plate.width, self.plate.depth, self.plate.height)
        current = self.process.choice.currentText() or _settings().value("druck/prozess", "")
        names = self.library.compatible("process", name)
        self.process.set_names(names, current if current in names else self.library.default_process(name))
        self._refresh_objects()

    def _fill_slots(self) -> None:
        printer = self.machine.choice.currentText()
        self.slot_list.blockSignals(True)
        self.slot_list.clear()
        self.filament_slot.blockSignals(True)
        self.filament_slot.clear()
        for number, name in enumerate(self.slots, 1):
            self.slot_list.addItem(f"{number}: {name.split(' @')[0]}")
            self.filament_slot.addItem(f"{number}: {name.split(' @')[0]}", number - 1)
        self.slot_list.blockSignals(False)
        self.filament_slot.blockSignals(False)
        self.slot_list.setCurrentRow(0)
        self.filament.set_names(self.library.compatible("filament", printer), self.slots[0])
        _settings().setValue("druck/filamente", json.dumps(self.slots, ensure_ascii=False))

    def _slot_selected(self, row: int) -> None:
        if 0 <= row < len(self.slots):
            self.filament.choice.setCurrentText(self.slots[row])

    def _filament_changed(self) -> None:
        row = self.slot_list.currentRow()
        name = self.filament.choice.currentText()
        if 0 <= row < len(self.slots) and name and self.slots[row] != name:
            self.slots[row] = name
            self.slot_list.item(row).setText(f"{row + 1}: {name.split(' @')[0]}")
            self.filament_slot.setItemText(row, f"{row + 1}: {name.split(' @')[0]}")
            _settings().setValue("druck/filamente", json.dumps(self.slots, ensure_ascii=False))

    def _add_slot(self) -> None:
        if len(self.slots) >= 16:
            return
        self.slots.append(self.slots[-1])
        self._fill_slots()
        self.slot_list.setCurrentRow(len(self.slots) - 1)

    def _remove_slot(self) -> None:
        row = self.slot_list.currentRow()
        if len(self.slots) <= 1 or row < 0:
            return
        del self.slots[row]
        for item in self.plate.objects:
            item.filament = min(item.filament, len(self.slots) - 1)
        self._fill_slots()

    # --- Objekte ------------------------------------------------------------------------------------------

    def add_from_design(self, only_selected: bool) -> None:
        if self._bodies_provider is None:
            return
        bodies = self._bodies_provider(only_selected)
        if not bodies:
            QMessageBox.information(self, "Druck", "Es gibt keine (gewählten) Körper, die gedruckt werden könnten.")
            return
        self.add_meshes(bodies)

    def add_meshes(self, bodies: list[tuple[str, np.ndarray]]) -> None:
        for name, triangles in bodies:
            self.plate.add(name, triangles)
        if not self.plate.arrange():
            self.info.setText("Nicht alle Objekte passen auf das Bett – rot markierte liegen außerhalb.")
        self.selected = len(self.plate.objects) - 1
        self._invalidate()
        self._refresh_objects()

    def _add_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Modell hinzufügen", str(Path.home()), "Netze (*.stl)")
        if path:
            self.add_meshes([(Path(path).stem, meshes.read_stl(path))])

    def _current(self) -> plate.PlateObject | None:
        return self.plate.objects[self.selected] if 0 <= self.selected < len(self.plate.objects) else None

    def _remove(self) -> None:
        item = self._current()
        if item is not None:
            self.plate.remove(item)
            self.selected = min(self.selected, len(self.plate.objects) - 1)
            self._invalidate()
            self._refresh_objects()

    def _duplicate(self) -> None:
        item = self._current()
        if item is None:
            return
        count, ok = QInputDialog.getInt(self, "Vervielfältigen", "Anzahl der Kopien:", 1, 1, 100)
        if ok:
            self.plate.duplicate(item, count)
            self._invalidate()
            self._refresh_objects()

    def _arrange(self) -> None:
        if not self.plate.arrange():
            QMessageBox.warning(self, "Anordnen", "Nicht alle Objekte passen auf das Druckbett.")
        self._invalidate()
        self._refresh_objects()

    def escape(self) -> bool:
        """Esc im Druck-Reiter (vom Hauptfenster weitergereicht – ein eigenes Esc-Kürzel hier würde sich mit dem des
        Fensters gegenseitig blockieren): beendet „Auf Fläche legen“ bzw. das Malen. True, wenn etwas beendet wurde."""
        if self.lay_button.isChecked():
            self.lay_button.setChecked(False)
            return True
        if self.view.paint_mode:
            self._paint_mode(None)
            return True
        return False

    def _lay_mode(self, on: bool) -> None:
        """Modus „Auf Fläche legen“ ein/aus; Malen endet dabei (nur ein Modus der linken Taste zugleich)."""
        if on:
            if self._current() is None:
                self.info.setText("Erst ein Objekt anklicken, dann „Auf Fläche legen“.")
                self.lay_button.setChecked(False)
                return
            self._paint_mode(None)
            self._show_lay_candidates()
            self.info.setText("Auf Fläche legen: die blau markierte Fläche anklicken, auf der das Objekt liegen soll "
                              "(orange = unter der Maus). Esc beendet.")
        else:
            self.view.set_lay_faces(None)

    def _show_lay_candidates(self) -> None:
        item = self._current()
        self.view.set_lay_faces(item.support_faces() if item is not None else None)

    def _lay_on_clicked(self, normal) -> None:
        """Angeklickte Auflagefläche nach unten; der Modus bleibt für weitere Versuche (wie im Original)."""
        item = self._current()
        if item is None:
            return
        item.lay_on(normal)
        self._invalidate()
        self._refresh_objects()
        self._show_lay_candidates()
        self.info.setText("Liegt auf der gewählten Fläche. Weitere Fläche anklicken oder Esc.")

    def _lay_flat(self) -> None:
        item = self._current()
        if item is None:
            return
        faces = item.flat_faces()
        if not faces:
            return
        # wie das Original: größte ebene Fläche nach unten; erneuter Klick nimmt die nächstgrößere
        current = getattr(item, "_lay_index", -1) + 1
        normal, _area = faces[current % len(faces)]
        item.lay_on(normal)
        item._lay_index = current
        self._invalidate()
        self._refresh_objects()

    def _rotate(self, axis: str) -> None:
        item = self._current()
        if item is not None:
            item.rotate(axis, self.turn_angle.value())
            self._invalidate()
            self._refresh_objects()

    def _scaled(self, value: float) -> None:
        item = self._current()
        if item is not None and abs(item.scale * 100 - value) > 1e-6:
            item.scale = value / 100
            self._invalidate()
            self._refresh_objects()

    def _slot_chosen(self, index: int) -> None:
        item = self._current()
        if item is not None and index >= 0:
            item.filament = index
            self._invalidate()

    def _moved_by_fields(self) -> None:
        item = self._current()
        if item is None or getattr(self, "_filling", False):
            return
        item.x, item.y = self.pos_x.value(), self.pos_y.value()
        self._invalidate()
        self._refresh_objects()

    def _clicked(self, index: int) -> None:
        self.selected = index
        self._refresh_objects()
        if self.lay_button.isChecked():
            # anderes Objekt gewählt: dessen Auflageflächen zeigen (daneben geklickt: Modus endet)
            if self._current() is None:
                self.lay_button.setChecked(False)
            else:
                self._show_lay_candidates()

    def _dragged(self, index: int, x: float, y: float) -> None:
        item = self.plate.objects[index]
        item.x, item.y = x, y
        self._invalidate()
        self._draw_objects()

    def _select(self, row: int) -> None:
        if row != self.selected:
            self.selected = row
            self._refresh_objects()

    def _refresh_objects(self) -> None:
        """Liste, Felder und Ansicht nach einer Änderung."""
        self._filling = True
        self.object_list.blockSignals(True)
        self.object_list.clear()
        for item in self.plate.objects:
            entry = QListWidgetItem(item.name)
            if not self.plate.inside(item):
                entry.setForeground(QColor("#ff6b5b"))
                entry.setToolTip("Liegt nicht vollständig im Bauraum")
            self.object_list.addItem(entry)
        self.object_list.setCurrentRow(self.selected)
        self.object_list.blockSignals(False)
        item = self._current()
        if item is not None:
            self.pos_x.setValue(item.x)
            self.pos_y.setValue(item.y)
            self.scale.setValue(item.scale * 100)
            size = item.size()
            self.size_label.setText(f"{size[0]:.1f} × {size[1]:.1f} × {size[2]:.1f} mm")
            self.filament_slot.setCurrentIndex(min(item.filament, self.filament_slot.count() - 1))
        else:
            self.size_label.setText("–")
        self._filling = False
        self._draw_objects()

    def _draw_objects(self) -> None:
        placed = [item.placed() for item in self.plate.objects]
        outside = [not self.plate.inside(item) for item in self.plate.objects]
        visible = not self.preview_button.isChecked()
        self.view.set_objects(placed, self.selected, outside, visible=visible)
        self.view.set_paint([(triangles, item.support_paint, item.color_paint)
                             for triangles, item in zip(placed, self.plate.objects)], visible)

    def _invalidate(self) -> None:
        """Etwas am Bett hat sich geändert: der alte G-Code gilt nicht mehr."""
        if self.toolpath is not None:
            self.toolpath, self.gcode_path = None, None
            self._set_mode(False)

    def _active_calibration(self):
        """Die Kalibrierung der gezeigten Platte (nur die Kalibrierplatte rechnet mit ihren Sonderwerten)."""
        if self._calibration is not None and self.plate is getattr(self, "_calibration_plate", None):
            return self._calibration
        return None

    def _project_objects(self) -> list:
        """Objekte der gezeigten Platte für das Projekt: Lage, Filament, eigene Einstellungen, Bemalung."""
        result = []
        for item in self.plate.objects:
            extra = {"settings": dict(item.settings)}
            if item.support_paint is not None:
                extra["supports"] = item.support_paint
            if item.color_paint is not None:
                extra["colours"] = item.color_paint
            result.append((item.name, item.placed(), item.filament + 1, extra))
        return result

    # --- Platten ------------------------------------------------------------------------------------------

    def _add_plate(self) -> None:
        self.plates.append(plate.Plate(self.plate.width, self.plate.depth, self.plate.height))
        self.plate_choice.addItem(str(len(self.plates)))
        self.plate_choice.setCurrentIndex(len(self.plates) - 1)

    def _remove_plate(self) -> None:
        index = self.plate_choice.currentIndex()
        if len(self.plates) <= 1:
            return
        removed = self.plates.pop(index)
        self.plates[0].objects += removed.objects
        self.plates[0].arrange()
        self.plate_choice.blockSignals(True)
        self.plate_choice.clear()
        self.plate_choice.addItems([str(n) for n in range(1, len(self.plates) + 1)])
        self.plate_choice.blockSignals(False)
        self.plate_choice.setCurrentIndex(0)
        self._show_plate(0)

    def _show_plate(self, index: int) -> None:
        if not 0 <= index < len(self.plates):
            return
        self.plate = self.plates[index]
        self.selected = -1
        self.toolpath, self.gcode_path = None, None
        self._set_mode(False)
        self._refresh_objects()

    # --- Werkzeuge am Objekt ------------------------------------------------------------------------------

    def _mirror(self, axis: str) -> None:
        item = self._current()
        if item is not None:
            item.mirror(axis)
            self._invalidate()
            self._refresh_objects()

    def _cut(self) -> None:
        item = self._current()
        if item is None:
            return
        height, ok = QInputDialog.getDouble(self, "Schneiden", "Schnitthöhe über dem Bett (mm):",
                                            round(float(item.size()[2]) / 2, 2), 0.1, float(item.size()[2]), 2)
        if not ok:
            return
        keep, ok = QInputDialog.getItem(self, "Schneiden", "Behalten:", ["beide", "oben", "unten"], 0, False)
        if not ok:
            return
        try:
            created = self.plate.cut(item, height, keep)
        except Exception as error:
            QMessageBox.warning(self, "Schneiden", f"Das Objekt ließ sich nicht schneiden: {error}")
            return
        self.selected = self.plate.objects.index(created[0])
        self._invalidate()
        self._refresh_objects()

    def _split_parts(self) -> None:
        item = self._current()
        if item is None:
            return
        parts = self.plate.split_parts(item)
        if len(parts) < 2:
            QMessageBox.information(self, "In Teile zerlegen", "Das Objekt besteht nur aus einem Teil.")
        self._invalidate()
        self._refresh_objects()

    def _simplify(self) -> None:
        item = self._current()
        if item is None:
            return
        cell, ok = QInputDialog.getDouble(self, "Vereinfachen", "Rasterweite (mm) – größer = weniger Dreiecke:",
                                          0.2, 0.01, 10.0, 2)
        if ok:
            before = len(item.mesh)
            item.set_mesh(meshes.reduce(item.placed(), cell))
            self.info.setText(f"„{item.name}“: {before} → {len(item.mesh)} Dreiecke.")
            self._invalidate()
            self._refresh_objects()

    def _object_settings(self) -> None:
        """Einstellungen nur für das gewählte Objekt (wie „Objekteinstellungen“ im Original)."""
        item = self._current()
        if item is None:
            return
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Objekteinstellungen – {item.name}")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Leer lassen = Wert des Prozesses."))
        form = QFormLayout()
        fields = {}
        for key in OBJECT_KEYS:
            entry = catalog.entry("process", key)
            line = QLineEdit(str(item.settings.get(key, "")))
            line.setPlaceholderText(catalog.scalar(self.process.profile.get(key, "")) if self.process.profile else "")
            form.addRow(f"{entry[1]} ({entry[2]})" if entry and entry[2] else (entry[1] if entry else key), line)
            fields[key] = line
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            item.settings = {key: line.text().strip() for key, line in fields.items() if line.text().strip()}
            self._invalidate()

    def _clear_paint(self) -> None:
        item = self._current()
        if item is not None:
            item.support_paint = item.color_paint = None
            self._invalidate()
            self._refresh_objects()

    # --- Malen --------------------------------------------------------------------------------------------

    def _paint_mode(self, mode: str | None) -> None:
        """Malmodus ein/aus; die beiden Malknöpfe schließen sich gegenseitig aus."""
        if mode is not None and getattr(self, "lay_button", None) is not None and self.lay_button.isChecked():
            self.lay_button.setChecked(False)           # nur ein Modus der linken Taste zugleich
        for button, own in ((self.paint_support, "support"), (self.paint_colour, "color")):
            button.blockSignals(True)
            button.setChecked(mode == own)
            button.blockSignals(False)
        self.view.paint_mode = mode
        self.paint_value.clear()
        if mode == "support":
            self.paint_value.addItem("Erzwingen", 1)
            self.paint_value.addItem("Blockieren", 2)
            self.paint_value.addItem("Radieren", 0)
        elif mode == "color":
            for number, name in enumerate(self.slots, 1):
                self.paint_value.addItem(f"{number}: {name.split(' @')[0]}", number)
            self.paint_value.addItem("Radieren", 0)
        self.paint_value.setVisible(mode is not None)
        self.brush.setVisible(mode is not None)
        if mode is not None:
            self._set_mode(False)
            self.info.setText("Malen: mit gedrückter linker Taste über das Objekt streichen (Ansicht drehen wie "
                              "gewohnt). Feinere Bereiche: kleinerer Pinsel.")

    def _paint_at(self, index: int, point) -> None:
        item = self.plate.objects[index]
        if len(item.mesh) < 20000 and not getattr(item, "_refined", False):
            # beim ersten Strich fein unterteilen, damit nicht ganze Seitenflächen auf einmal gefärbt werden
            item.subdivide(max(self.brush.value() / 2, 0.5))
            item._refined = True
        which = "support" if self.view.paint_mode == "support" else "color"
        item.paint(which, point, self.brush.value(), int(self.paint_value.currentData() or 0))
        self.selected = index
        self._invalidate()
        self._draw_objects()

    # --- Kalibrierung -------------------------------------------------------------------------------------

    def start_calibration(self, title: str) -> None:
        """Legt den Kalibrierdruck auf eine eigene, neue Platte (wie das Original ein eigenes Projekt öffnet)."""
        from ..printing import calibration
        centre = (self.plate.width / 2, self.plate.depth / 2)
        cal = calibration.CALIBRATIONS[title](centre)
        self._add_plate()
        for name, triangles, _filament, extra in cal.objects:
            # direkt an die vorgesehene Stelle (ohne automatisches Anordnen, das die Blöcke verschieben würde)
            item = plate.PlateObject(name, triangles, settings=dict(extra.get("settings", {})))
            item.set_mesh(triangles)
            self.plate.objects.append(item)
        self._calibration = cal
        self._calibration_plate = self.plate
        self.info.setText(f"{cal.title}: {cal.hint}")
        self._refresh_objects()

    # --- Vorschau -----------------------------------------------------------------------------------------

    def _set_mode(self, preview: bool) -> None:
        if preview and self.toolpath is None:
            preview = False
            self.info.setText("Für die Vorschau zuerst „Slicen“.")
        self.prepare_button.setChecked(not preview)
        self.preview_button.setChecked(preview)
        self.layer_slider.setVisible(preview)
        self.legend.setVisible(preview)
        self._draw_objects()
        if preview:
            self._show_toolpath()
        else:
            self.view.set_toolpath(None, 0, set(), False)

    def _layer_moved(self, value: int) -> None:
        self._show_toolpath()
        if self.toolpath is not None and self.toolpath.layer_count:
            height = self.toolpath.layer_heights[min(value, self.toolpath.layer_count - 1)]
            self.layer_slider.setToolTip(f"Schicht {value + 1} von {self.toolpath.layer_count} – {height:.2f} mm")

    def _show_toolpath(self) -> None:
        if self.toolpath is None or not self.preview_button.isChecked():
            return
        kinds = {kind for kind, box in self.kind_boxes.items() if box.isChecked()}
        self.view.set_toolpath(self.toolpath, self.layer_slider.value(), kinds, self.travel_box.isChecked())

    # --- Slicen -------------------------------------------------------------------------------------------

    def slice(self) -> None:
        if not self.plate.objects:
            QMessageBox.information(self, "Slicen", "Es liegt nichts auf dem Druckbett.")
            return
        problems = self.plate.problems()
        if problems:
            QMessageBox.warning(self, "Slicen", "\n".join(problems))
            return
        if self._process is not None:
            return
        folder = WORK_ROOT / time.strftime("%Y%m%d-%H%M%S")
        objects = self._project_objects()
        filaments = self._filament_profiles()
        process = profiles.Profile("process", self.process.profile.name, self.process.profile.values,
                                   dict(self.process.profile.changes))
        calibration = self._active_calibration()
        if calibration is not None:
            process.changes.update(calibration.process)
        if any(item.support_paint is not None and (item.support_paint == 1).any() for item in self.plate.objects) \
                and process.get("enable_support") != "1":
            # gemalte Stützen wirken nur bei eingeschalteten Stützen im Modus „manuell“ (am Slicer geprüft):
            # wie im Original werden sie dafür eingeschaltet – nur an den bemalten Stellen
            style = "tree(manual)" if str(process.get("support_type", "")).startswith("tree") else "normal(manual)"
            process.changes.update({"enable_support": "1", "support_type": style})
        try:
            job = slicer.prepare(folder, objects, self.machine.profile, process, filaments)
        except slicer.SlicerError as error:
            QMessageBox.warning(self, "Slicen", str(error))
            return
        self._job = job
        self._log = []
        self._process = QProcess(self)
        environment = slicer.cli_environment()
        from PySide6.QtCore import QProcessEnvironment
        env = QProcessEnvironment()
        for key, value in environment.items():
            env.insert(key, value)
        self._process.setProcessEnvironment(env)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyRead.connect(lambda: self._log.append(
            bytes(self._process.readAll()).decode("utf-8", "replace")))
        self._process.finished.connect(self._sliced)
        self.slice_button.setEnabled(False)
        self.info.setText("Slicen läuft …")
        self._process.start(job.arguments[0], job.arguments[1:])

    def _sliced(self, code: int, _status) -> None:
        process, job = self._process, self._job
        self._process = None
        self.slice_button.setEnabled(True)
        log = "".join(self._log)
        (job.folder / "slicer.log").write_text(log, encoding="utf-8")
        target = job.gcode
        if not target.exists() and job.project.exists():
            target = slicer.gcode_from_project(job.project, job.folder / "plate_1.gcode") or target
        if not target.exists():
            # die Meldung des Slicers (result.json) ist meist aussagekräftiger als das Protokoll
            message = job.result().get("error_string") or "\n".join(
                line for line in log.strip().splitlines()[-12:] if "Gtk-CRITICAL" not in line)
            QMessageBox.warning(self, "Slicen", f"Creality Print hat keinen G-Code geliefert (Code {code}):\n\n{message}")
            self.info.setText(f"Slicen fehlgeschlagen – Protokoll: {job.folder / 'slicer.log'}")
            return
        calibration = self._active_calibration()
        if calibration is not None and calibration.post is not None:
            # Kalibrierung: je Höhe den zu prüfenden Wert in den G-Code schreiben
            target.write_text(calibration.post(target.read_text(errors="replace")), encoding="utf-8")
        self.gcode_path = target
        self.toolpath = gcode.read(target)
        count = max(self.toolpath.layer_count, 1)
        self.layer_slider.setRange(0, count - 1)
        self.layer_slider.setValue(count - 1)
        for kind, box in self.kind_boxes.items():
            box.setVisible(kind in self.toolpath.segments)
        info = self.toolpath.info
        text = "  ·  ".join(f"{key}: {value}" for key, value in info.items()) or "Fertig geslict."
        if calibration is not None:
            text += "\n" + calibration.title + ": " + calibration.hint
        self.info.setText(text)
        self._set_mode(True)

    def _save_gcode(self) -> None:
        if self.gcode_path is None:
            QMessageBox.information(self, "G-Code speichern", "Zuerst slicen.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "G-Code speichern", str(Path.home() / "wolfcad.gcode"),
                                              "G-Code (*.gcode)")
        if path:
            Path(path).write_bytes(self.gcode_path.read_bytes())

    def _open_original(self) -> None:
        """Übergabe an das Original: das Bett als Projekt (Lage, Filamente, alle Einstellungen) in Creality Print
        öffnen – dort lässt es sich weiter bearbeiten, wie es in WolfCAD vorbereitet wurde."""
        from ..printing import project3mf
        if not self.plate.objects:
            QMessageBox.information(self, "Creality Print", "Es liegt nichts auf dem Druckbett.")
            return
        folder = WORK_ROOT / ("uebergabe-" + time.strftime("%Y%m%d-%H%M%S"))
        objects = self._project_objects()
        try:
            path = project3mf.write(folder / "projekt.3mf", objects, project3mf.project_settings(
                self.machine.profile, self.process.profile, self._filament_profiles()))
            slicer.open_in_creality_print([path])
        except slicer.SlicerError as error:
            QMessageBox.warning(self, "Creality Print", str(error))

    def _filament_profiles(self) -> list[profiles.Profile]:
        """Die Filamentprofile der Fächer samt eigener Änderungen."""
        result = []
        for name in self.slots:
            profile = self.library.profile("filament", name)
            stored = _settings().value(f"druck/aenderungen/filament/{name}", "")
            if stored:
                try:
                    profile.changes = json.loads(stored)
                except json.JSONDecodeError:
                    pass
            result.append(profile)
        return result

    # --- Drucker ------------------------------------------------------------------------------------------

    def _printer(self) -> moonraker.Moonraker | None:
        host = self.host.text().strip()
        return moonraker.Moonraker(host) if host else None

    def _host_changed(self) -> None:
        _settings().setValue("druck/host", self.host.text().strip())
        if self.camera_box.isChecked():
            self.camera.show_printer(self.host.text().strip())
        if self.host.text().strip():
            self._status_timer.start()
            self._poll_status()
        else:
            self._status_timer.stop()

    def _discover(self) -> None:
        """„Suchen“: die Netzsuche dauert bis zu 6 s – sie läuft im Hintergrund, die Oberfläche bleibt bedienbar."""
        self.info.setText("Suche Drucker im Netz …")
        future = self._pool.submit(moonraker.discover)
        future.add_done_callback(lambda result: self._relay.later(lambda: self._discover_done(result)))

    def _discover_done(self, future) -> None:
        try:
            found = future.result()
        except Exception:
            found = []
        self.info.setText("")
        if not found:
            QMessageBox.information(self, "Drucker suchen", "Kein Creality-Drucker im Netz gefunden – Adresse "
                                                            "bitte von Hand eintragen.")
            return
        names = [f"{name} ({address})" for name, address in found]
        choice, ok = QInputDialog.getItem(self, "Drucker suchen", "Gefundene Drucker:", names, 0, False)
        if ok:
            name, address = found[names.index(choice)]
            self.host.setText(address)
            self._host_changed()
            printer = profiles.find_printer(self.library, name)
            if printer:
                self.machine.choice.setCurrentText(printer)

    def _discovered(self, future) -> None:
        try:
            found = future.result()
        except Exception:
            return
        if not found or self.host.text().strip():
            return
        name, address = found[0]
        self.host.setText(address)
        self._host_changed()
        printer = profiles.find_printer(self.library, name)
        if printer and printer != self.machine.choice.currentText():
            self.machine.choice.setCurrentText(printer)
            self.slots = [self.library.default_filament(printer)]
            self._fill_slots()
        self.info.setText(f"Drucker gefunden: {name} ({address}) – Profil „{printer}“.")

    def _poll_status(self) -> None:
        printer = self._printer()
        if printer is None or (self._status_future is not None and not self._status_future.done()):
            return
        self._status_future = self._pool.submit(printer.status)
        self._status_future.add_done_callback(lambda future: self._relay.later(lambda: self._show_status(future)))

    def _show_status(self, future) -> None:
        try:
            status = future.result()
        except moonraker.PrinterError as error:
            self.status_label.setText(str(error))
            return
        states = {"standby": "Bereit", "printing": "Druckt", "paused": "Pausiert", "complete": "Fertig",
                  "cancelled": "Abgebrochen", "error": "Fehler"}
        text = states.get(status.state, status.state)
        if status.state in ("printing", "paused"):
            text += f" – {status.progress * 100:.0f} % ({status.file})"
        temps = "  ".join(f"{name} {now:.0f}/{target:.0f} °C" for name, (now, target) in status.temperatures.items())
        self.status_label.setText(text + ("\n" + temps if temps else ""))

    def _camera_toggled(self, on: bool) -> None:
        self.camera.setVisible(on)
        if on:
            self.camera.show_printer(self.host.text().strip())
        else:
            self.camera.stop()

    def _printer_command(self, action: str) -> None:
        printer = self._printer()
        if printer is None:
            return
        if action == "cancel" and QMessageBox.question(self, "Druck abbrechen",
                                                       "Den laufenden Druck wirklich abbrechen?") \
                != QMessageBox.StandardButton.Yes:
            return
        # im Hintergrund: der Drucker antwortet mitunter erst nach Sekunden (die Oberfläche fror sonst ein)
        future = self._pool.submit(getattr(printer, action))

        def done(result) -> None:
            try:
                result.result()
            except moonraker.PrinterError as error:
                QMessageBox.warning(self, "Drucker", str(error))
            self._poll_status()

        future.add_done_callback(lambda result: self._relay.later(lambda: done(result)))

    def send(self, start: bool) -> None:
        printer = self._printer()
        if self.gcode_path is None:
            QMessageBox.information(self, "Drucken", "Zuerst slicen.")
            return
        if printer is None:
            QMessageBox.information(self, "Drucken", "Rechts unter „Drucker“ die Netzwerkadresse eintragen "
                                                     "oder „Suchen“.")
            return
        name = (self.plate.objects[0].name if len(self.plate.objects) == 1 else "WolfCAD") + ".gcode"
        name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
        if start and QMessageBox.question(self, "Drucken", f"„{name}“ an {printer.host} senden und den Druck "
                                                           "starten?") != QMessageBox.StandardButton.Yes:
            return
        self.info.setText(f"Sende „{name}“ an {printer.host} …")
        self.upload_bar.setValue(0)
        self.upload_bar.setFormat("0 %")
        self.upload_bar.setVisible(True)
        for button in (self.print_button, self.upload_button):
            button.setEnabled(False)            # kein zweites Senden, solange eines läuft
        started = time.monotonic()
        shown = [-1]                            # zuletzt gezeigter Stand in Promille (Oberfläche nicht fluten)

        def progress(sent: int, total: int) -> None:
            # läuft im Sende-Faden: nur bei sichtbarer Änderung an die Oberfläche weiterreichen
            share = int(1000 * sent / max(total, 1))
            if share != shown[0]:
                shown[0] = share
                rate = sent / max(time.monotonic() - started, 1e-3) / 1e6
                self._relay.later(lambda: self._show_upload(share, sent, total, rate))

        future = self._pool.submit(printer.upload, self.gcode_path, start, name, progress)

        def done(result) -> None:
            self.upload_bar.setVisible(False)
            for button in (self.print_button, self.upload_button):
                button.setEnabled(True)
            try:
                path = result.result()
                self.info.setText(f"„{path}“ " + ("wird gedruckt." if start else "liegt auf dem Drucker."))
            except moonraker.PrinterError as error:
                QMessageBox.warning(self, "Drucken", str(error))
                self.info.setText("Senden fehlgeschlagen.")
            self._poll_status()

        future.add_done_callback(lambda result: self._relay.later(lambda: done(result)))

    def _show_upload(self, share: int, sent: int, total: int, rate: float) -> None:
        """Stand des Hochladens: Balken mit Prozent, gesendete und gesamte Größe, Tempo."""
        self.upload_bar.setValue(share)
        mb = lambda value: f"{value / 1e6:.1f}".replace(".", ",")
        self.upload_bar.setFormat(f"{share / 10:.0f} %  –  {mb(sent)} von {mb(total)} MB  ({mb(rate * 1e6)} MB/s)")

    def shutdown(self) -> None:
        """Beim Schließen: Abfragen beenden, laufendes Slicen abbrechen."""
        self._relay.alive = False
        self._status_timer.stop()
        self.camera.stop()
        if self._process is not None:
            self._process.kill()
            self._process.waitForFinished(2000)
        self._pool.shutdown(wait=False, cancel_futures=True)
