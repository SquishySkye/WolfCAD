# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Formen-Modus (Sculpting) in der 3D-Ansicht – Bedienung angelehnt an Blenders Sculpt-Modus (Runde 93).

Die Sitzung hängt wie das Skizzieren als `sketch_input` an der Ansicht: die linke Maustaste formt, Drehen und
Schwenken bleiben wie gewohnt (mittlere/rechte Taste, Navigationsleiste). Tasten wie in Blender:
  X Zeichnen · C Tonstreifen · Umschalt+C Ton · I Aufblasen · G Greifen · K Schlangenhaken · S Glätten ·
  Umschalt+T Abflachen · P Kneifen · Umschalt+X Falte · L Schicht · M Maske
  [ / ] Radius kleiner/größer · Umschalt+[ / ] Stärke · Strg beim Formen: umgekehrt · Umschalt beim Formen: Glätten
  Strg+Z / Strg+Umschalt+Z: Strich zurück/wieder · Alt+M Maske löschen · Strg+I Maske umkehren
Die Ansicht zeigt das Netz während des Formens selbst (Ebene „sculpt_mesh“), dazu den Pinselkreis auf der
Oberfläche und die Maske dunkel. Mausschritte werden gesammelt und je Bild einmal verrechnet (wie beim Ziehen der
Pfeile), damit das Formen auch bei großen Netzen flüssig bleibt.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QToolButton, QVBoxLayout, QWidget,
)

from ..core import sculpt
from .icons import icon

# Bildsymbole der Pinsel (icons/brush_*.svg)
_BRUSH_ICONS = {"Zeichnen": "brush_draw", "Ton": "brush_clay", "Tonstreifen": "brush_clay_strips",
                "Aufblasen": "brush_inflate", "Greifen": "brush_grab", "Schlangenhaken": "brush_snake_hook",
                "Glätten": "brush_smooth", "Abflachen": "brush_flatten", "Kneifen": "brush_pinch",
                "Falte": "brush_crease", "Schicht": "brush_layer", "Maske": "brush_mask"}
_KEY_OF = {brush: key for key, brush in sculpt.BRUSH_KEYS.items()}


class SculptPanel(QWidget):
    """Werkzeugleiste des Formen-Modus im Eigenschaftenfeld: Pinsel, Radius, Stärke, Abfall, Symmetrie, Maske."""

    def __init__(self, session: "SculptSession"):
        super().__init__()
        self._session = session
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QToolButton] = {}
        for number, name in enumerate(sculpt.BRUSHES):
            button = QToolButton()
            button.setIcon(icon(_BRUSH_ICONS[name]))
            button.setText(name)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setCheckable(True)
            button.setMinimumWidth(78)
            button.setToolTip(f"{name} (Taste {_KEY_OF.get(name, '–')})")
            button.clicked.connect(lambda _checked=False, chosen=name: session.set_brush(chosen))
            self._group.addButton(button)
            self.buttons[name] = button
            grid.addWidget(button, number // 3, number % 3)
        layout.addLayout(grid)
        form = QFormLayout()
        self.radius = QDoubleSpinBox()
        self.radius.setRange(0.05, 1e4)
        self.radius.setDecimals(2)
        self.radius.setSuffix(" mm")
        self.radius.setToolTip("Pinselradius im Modell ( [ und ] )")
        self.radius.valueChanged.connect(lambda value: setattr(session.brush, "radius", value) or session.show_cursor())
        form.addRow("Radius", self.radius)
        self.strength = QSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(1, 100)
        self.strength.setToolTip("Stärke (Umschalt+[ und Umschalt+])")
        self.strength.valueChanged.connect(lambda value: setattr(session.brush, "strength", value / 100))
        form.addRow("Stärke", self.strength)
        self.falloff = QComboBox()
        self.falloff.addItems(sculpt.FALLOFFS)
        self.falloff.setToolTip("Abfall vom Pinselrand zur Mitte (wie Blenders Abfallkurve)")
        self.falloff.currentTextChanged.connect(lambda text: setattr(session.brush, "falloff", text))
        form.addRow("Abfall", self.falloff)
        self.layer = QDoubleSpinBox()
        self.layer.setRange(0.01, 1e3)
        self.layer.setSuffix(" mm")
        self.layer.setToolTip("Schicht: so hoch wächst ein Strich höchstens")
        self.layer.valueChanged.connect(lambda value: setattr(session.brush, "layer_height", value))
        form.addRow("Schichthöhe", self.layer)
        symmetry = QWidget()
        line = QHBoxLayout(symmetry)
        line.setContentsMargins(0, 0, 0, 0)
        self.mirror = []
        for axis in "XYZ":
            box = QCheckBox(axis)
            box.setToolTip(f"Symmetrie: jeden Tupfer an der Ebene senkrecht zu {axis} (durch den Ursprung) spiegeln")
            box.toggled.connect(lambda _on: session.set_symmetry(tuple(b.isChecked() for b in self.mirror)))
            self.mirror.append(box)
            line.addWidget(box)
        line.addStretch(1)
        form.addRow("Symmetrie", symmetry)
        self.voxel = QDoubleSpinBox()
        self.voxel.setRange(0.01, 1e3)
        self.voxel.setDecimals(2)
        self.voxel.setSuffix(" mm")
        self.voxel.setToolTip("Voxelgröße für „Neu vernetzen“ (kleiner = feiner)")
        form.addRow("Voxelgröße", self.voxel)
        # Dynamische Topologie wie Blender (vereinfacht): nach jedem Strich werden zu lange Kanten im bearbeiteten
        # Bereich halbiert – Details entstehen dort, wo geformt wird (Strg+D schaltet um)
        self.dyntopo = QCheckBox("Dynamische Topologie")
        self.dyntopo.setToolTip("Nach jedem Strich das Netz im bearbeiteten Bereich bis zur Detailgröße verfeinern "
                                "(Strg+D)")
        form.addRow(self.dyntopo)
        self.detail = QDoubleSpinBox()
        self.detail.setRange(0.01, 100)
        self.detail.setDecimals(2)
        self.detail.setSuffix(" mm")
        self.detail.setToolTip("Längste Kante, die beim Formen mit dynamischer Topologie stehen bleibt")
        form.addRow("Detailgröße", self.detail)
        layout.addLayout(form)
        actions = QGridLayout()
        for number, (text, slot, tip) in enumerate((
                ("Rückgängig", session.undo, "Letzten Strich zurücknehmen (Strg+Z)"),
                ("Wiederholen", session.redo, "Zurückgenommenen Strich wiederholen (Strg+Umschalt+Z)"),
                ("Maske umkehren", session.invert_mask, "Geschütztes frei, freies geschützt (Strg+I)"),
                ("Maske löschen", session.clear_mask, "Alles wieder frei (Alt+M)"),
                ("Unterteilen", session.subdivide, "Jedes Dreieck in vier – mehr Feinheit zum Formen"),
                ("Neu vernetzen", session.remesh, "Voxel-Remesh wie Blender: gleichmäßiges, geschlossenes Netz mit "
                                                  "der Voxelgröße als Kantenlänge (Strg+R)"))):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            actions.addWidget(button, number // 2, number % 2)
        layout.addLayout(actions)
        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        keys = QLabel("Linke Maustaste formt · Strg: umgekehrt · Umschalt: glätten · Tasten wie in Blender (X, C, "
                      "I, G, K, S, P, L, M …) · [ ] Radius · Umschalt+[ ] Stärke")
        keys.setWordWrap(True)
        keys.setObjectName("hint")
        layout.addWidget(keys)

    def show_brush(self, brush: sculpt.Brush) -> None:
        """Zeigt die Einstellungen des Pinsels an (ohne Rückmeldung an die Sitzung)."""
        for widget in (self.radius, self.strength, self.falloff, self.layer):
            widget.blockSignals(True)
        self.buttons[brush.kind].setChecked(True)
        self.radius.setValue(brush.radius)
        self.strength.setValue(int(round(brush.strength * 100)))
        self.falloff.setCurrentText(brush.falloff)
        self.layer.setValue(brush.layer_height)
        for widget in (self.radius, self.strength, self.falloff, self.layer):
            widget.blockSignals(False)


class SculptSession:
    """Formen am Netzkörper `mesh_name` (siehe Moduldoku). `index`: der Schritt „Formen“, der bearbeitet wird
    (None: ein neuer entsteht beim Übernehmen)."""

    uses_ground = True          # das Bodenraster bleibt sichtbar

    def __init__(self, window, mesh_name: str, mesh: sculpt.SculptMesh, index: int | None):
        self.window, self.viewport = window, window.viewport
        self.mesh_name, self.mesh, self.index = mesh_name, mesh, index
        low, high = mesh.vertices.min(axis=0), mesh.vertices.max(axis=0)
        size = float(np.linalg.norm(high - low)) or 10.0
        self.brush = sculpt.Brush(radius=round(size * 0.06, 2), strength=0.5, layer_height=round(size * 0.01, 2))
        self._stroke: sculpt.Stroke | None = None
        self._stroke_kind = None            # Pinsel des laufenden Strichs (Umschalt: Glätten auf Zeit)
        self._pending = None                # letzter, noch nicht verrechneter Mauspunkt beim Formen
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._process)
        self._undo: list[np.ndarray] = []   # Lagen vor jedem Strich (bzw. ganze Netze vor dem Unterteilen)
        self._redo: list[np.ndarray] = []
        self._cursor = None                 # (Punkt, Normale) des Pinsels auf der Oberfläche
        self.changed = False
        self.panel = SculptPanel(self)
        self.panel.show_brush(self.brush)
        self.panel.voxel.setValue(round(size / 120, 2) or 0.1)     # wie Blender: rund 1 % der Größe
        self.panel.detail.setValue(round(size / 200, 2) or 0.05)
        self.redraw()

    # --- Pinsel ---------------------------------------------------------------------------------------------

    def set_brush(self, kind: str) -> None:
        self.brush.kind = kind
        self.panel.show_brush(self.brush)
        self.window.statusBar().showMessage(f"Pinsel: {kind}", 2500)

    def set_symmetry(self, axes: tuple) -> None:
        self.brush.symmetry = axes

    # --- Ansicht --------------------------------------------------------------------------------------------

    def redraw(self) -> None:
        """Netz, Maske und Pinselkreis an die Ansicht geben. Indiziert und glatt schattiert: je Bild gehen nur die
        Ecken (und ihre Normalen) zur Grafikkarte, die Dreiecke nur nach einer Änderung der Topologie."""
        mesh = self.mesh
        topology = mesh.topology if mesh.topology != getattr(self, "_shown_topology", None) else None
        self._shown_topology = mesh.topology
        self.viewport.set_indexed("sculpt_mesh", mesh._vertices32, mesh._normals32,
                                  mesh.faces if topology is not None else None)
        # maskierte Dreiecke nur neu bestimmen, wenn sich die Maske oder das Netz geändert hat
        key = (mesh.topology, getattr(self, "_mask_version", 0))
        if getattr(self, "_masked_key", None) != key:
            self._masked = np.nonzero(mesh.mask[mesh.faces].mean(axis=1) > 0.3)[0]
            self._masked_key = key
        masked = self._masked
        if len(masked):
            # geschützte Dreiecke dunkel und halb durchsichtig, ein Stück entlang der Normale über dem Netz
            # (sonst flimmerten Maske und Oberfläche ineinander)
            lifted = mesh._vertices32 + mesh._normals32 * np.float32(self.brush.radius * 0.01)
            self.viewport._layers["sculpt_mask"].alpha = 0.55
            self.viewport.set_indexed("sculpt_mask", lifted, mesh._normals32, mesh.faces[masked])
        else:
            self.viewport.set_geometry("sculpt_mask", np.zeros((0, 3), np.float32))
        self.panel.info.setText(f"{len(mesh.faces):,} Dreiecke · {len(mesh.vertices):,} Ecken".replace(",", "."))
        self.show_cursor()

    def show_cursor(self) -> None:
        """Pinselkreis auf der Oberfläche (in der Ebene senkrecht zur Normale an der Stelle)."""
        if self._cursor is None:
            self.viewport.set_geometry("sculpt_brush", np.zeros((0, 3), np.float32))
            return
        point, normal = self._cursor
        helper = np.array([0.0, 0.0, 1.0]) if abs(normal[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
        u = np.cross(normal, helper)
        u /= np.linalg.norm(u) or 1.0
        v = np.cross(normal, u)
        angles = np.linspace(0, 2 * np.pi, 49)
        ring = point + self.brush.radius * (np.outer(np.cos(angles), u) + np.outer(np.sin(angles), v))
        lines = np.repeat(ring, 2, axis=0)[1:-1]
        self.viewport.set_geometry("sculpt_brush", lines.astype(np.float32))

    # --- Maus (Schnittstelle wie die Skizzen-Sitzung) -------------------------------------------------------

    def _hit(self, x: float, y: float):
        origin, direction, _pixel = self.viewport.pick_ray(x, y)
        found = self.mesh.ray_hit(origin, direction)
        return (origin, direction, found)

    def drag_start(self, x: float, y: float) -> bool:
        """Linke Maustaste gedrückt: auf dem Netz beginnt ein Strich (sonst gehört der Klick der Ansicht)."""
        origin, direction, found = self._hit(x, y)
        if found is None:
            return False
        modifiers = QApplication.keyboardModifiers()
        stroke_brush = sculpt.Brush(**vars(self.brush))
        if modifiers & Qt.KeyboardModifier.ShiftModifier and self.brush.kind != "Maske":
            stroke_brush.kind = "Glätten"           # wie Blender: Umschalt glättet mit jedem Pinsel
        stroke_brush.invert = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        if stroke_brush.kind == "Maske":
            self._mask_changed()
        mask_before = self.mesh.mask.copy() if stroke_brush.kind == "Maske" else None
        self._stroke = sculpt.Stroke(self.mesh, stroke_brush, found[0], direction)
        # Rückgängig: die Lagen zu Strichbeginn hält der Strich schon (keine zweite Kopie des ganzen Netzes); mit
        # dynamischer Topologie ändern sich die Dreiecke, dann das ganze Netz merken
        if mask_before is not None:
            self._push_undo(("Maske", mask_before))
        elif self.panel.dyntopo.isChecked():
            self._push_undo(("Netz", self._stroke.start_vertices, self.mesh.faces.copy(), self.mesh.mask.copy()))
        else:
            self._push_undo(self._stroke.start_vertices)
        self._redo.clear()
        self.changed = True
        self._cursor = (found[0], self.mesh.face_normals[found[1]] / (np.linalg.norm(self.mesh.face_normals[found[1]]) or 1))
        self.redraw()
        return True

    def move(self, x: float, y: float) -> None:
        origin, direction, found = self._hit(x, y)
        if self._stroke is None:
            # nur zeigen, wo der Pinsel ansetzen würde
            if found is None:
                self._cursor = None
            else:
                normal = self.mesh.face_normals[found[1]]
                self._cursor = (found[0], normal / (np.linalg.norm(normal) or 1.0))
            self.show_cursor()
            return
        if found is not None and self._stroke.brush.kind not in ("Greifen", "Schlangenhaken"):
            point = found[0]
        else:
            # Greifen (bzw. neben dem Netz): Punkt auf der Ebene durch die letzte Stelle, quer zum Blick
            last = self._stroke.last
            point = np.asarray(origin) + np.asarray(direction) * float((last - origin) @ direction)
        self._pending = point
        if not self._timer.isActive():
            self._timer.start()

    def _process(self) -> None:
        """Gesammelte Mausschritte einmal verrechnen und neu zeichnen (einmal je Ereignisrunde)."""
        if self._stroke is None or self._pending is None:
            return
        point, self._pending = self._pending, None
        if self._stroke.brush.kind == "Maske":
            self._mask_changed()
        if self._stroke.move_to(point):
            if self._cursor is not None:
                self._cursor = (point, self._cursor[1])
            self.redraw()

    def _mask_changed(self) -> None:
        """Die Maske hat sich geändert: maskierte Dreiecke beim nächsten Zeichnen neu bestimmen."""
        self._mask_version = getattr(self, "_mask_version", 0) + 1

    def drag_end(self) -> None:
        if self._stroke is None:
            return
        if self._stroke.brush.kind == "Maske":
            self._mask_changed()
        self._process()
        stroke, self._stroke = self._stroke, None
        if self.panel.dyntopo.isChecked() and stroke.brush.kind != "Maske":
            # bearbeiteter Bereich: bewegte Ecken und ihre Nachbarn
            moved = np.linalg.norm(self.mesh.vertices - stroke.start_vertices, axis=1) > 1e-9
            region = moved | (np.asarray(self.mesh._adjacency @ moved.astype(float)).ravel() > 0)
            from ..core.features import FeatureError
            try:
                added = self.mesh.refine(region, self.panel.detail.value())
            except FeatureError as error:
                added = 0
                self.window.statusBar().showMessage(str(error), 5000)
            if added:
                self._mask_changed()
        self.mesh.refresh_tree()
        self.redraw()

    def press(self, x: float, y: float) -> None:
        """Klick ohne Ziehen: der Tupfer beim Drücken genügt."""

    def finish(self) -> None:
        """Rechtsklick: nichts (das Kontextmenü der Ansicht bleibt)."""

    def typed(self, text) -> bool:
        return False

    def set_additive(self, on: bool) -> None:
        pass

    def double_click(self, x: float, y: float) -> None:
        pass

    def is_drawing(self) -> bool:
        return self._stroke is not None

    # --- Tasten (wie Blender) -------------------------------------------------------------------------------

    def _key_name(self, event) -> str:
        sequence = QKeySequence(event.keyCombination()).toString()
        return sequence

    def wants_key(self, event) -> bool:
        """Beansprucht die Sitzung diese Taste (vor den fensterweiten Kürzeln)?"""
        name = self._key_name(event)
        return name in sculpt.BRUSH_KEYS or name in ("[", "]", "Shift+[", "Shift+]", "{", "}", "Ctrl+Z",
                                                     "Ctrl+Shift+Z", "Alt+M", "Ctrl+I", "Ctrl+R", "Ctrl+D")

    def key_pressed(self, event) -> bool:
        name = self._key_name(event)
        if name in sculpt.BRUSH_KEYS:
            self.set_brush(sculpt.BRUSH_KEYS[name])
        elif name in ("[", "]"):
            self.brush.radius = max(0.05, self.brush.radius * (0.9 if name == "[" else 1.1))
        elif name in ("Shift+[", "{", "Shift+]", "}"):
            down = name in ("Shift+[", "{")
            self.brush.strength = float(np.clip(self.brush.strength + (-0.05 if down else 0.05), 0.01, 1.0))
        elif name == "Ctrl+Z":
            self.undo()
        elif name == "Ctrl+Shift+Z":
            self.redo()
        elif name == "Alt+M":
            self.clear_mask()
        elif name == "Ctrl+I":
            self.invert_mask()
        elif name == "Ctrl+R":
            self.remesh()
        elif name == "Ctrl+D":
            self.panel.dyntopo.toggle()
            self.window.statusBar().showMessage("Dynamische Topologie " + ("an" if self.panel.dyntopo.isChecked()
                                                                           else "aus"), 2500)
        else:
            return False
        self.panel.show_brush(self.brush)
        self.show_cursor()
        return True

    # --- Befehle --------------------------------------------------------------------------------------------

    UNDO_STEPS = 40         # so viele Schritte zurück (wie Blenders Voreinstellung 32; je Schritt eine Netzkopie)

    def _push_undo(self, entry) -> None:
        self._undo.append(entry)
        del self._undo[:-self.UNDO_STEPS]

    def _snapshot(self):
        return ("Netz", self.mesh.vertices.copy(), self.mesh.faces.copy(), self.mesh.mask.copy())

    def _restore(self, entry):
        """Stellt einen Rückgängig-Eintrag wieder her und gibt den Gegen-Eintrag zurück."""
        if isinstance(entry, tuple) and entry[0] == "Maske":
            current = ("Maske", self.mesh.mask.copy())
            self.mesh.mask = entry[1]
        elif isinstance(entry, tuple) and entry[0] == "Netz":
            current = self._snapshot()
            self.mesh.__init__(entry[1], entry[2], entry[3])
        else:
            current = self.mesh.vertices.copy()
            self.mesh.vertices[:] = entry
            self.mesh.sync_vertices()
            self.mesh.update_normals()
            self.mesh.refresh_tree(force=True)
        return current

    def undo(self) -> None:
        self._mask_changed()
        if self._undo:
            self._redo.append(self._restore(self._undo.pop()))
            self.redraw()

    def redo(self) -> None:
        self._mask_changed()
        if self._redo:
            self._undo.append(self._restore(self._redo.pop()))
            self.redraw()

    def invert_mask(self) -> None:
        self._push_undo(("Maske", self.mesh.mask.copy()))
        self.mesh.mask = 1.0 - self.mesh.mask
        self._mask_changed()
        self.redraw()

    def clear_mask(self) -> None:
        self._push_undo(("Maske", self.mesh.mask.copy()))
        self.mesh.mask = np.zeros(len(self.mesh.vertices))
        self._mask_changed()
        self.redraw()

    def subdivide(self) -> None:
        from ..core.features import FeatureError
        entry = self._snapshot()
        try:
            self.mesh.subdivide()
        except FeatureError as error:
            self.window.statusBar().showMessage(str(error), 6000)
            return
        self._push_undo(entry)
        self.changed = True
        self.redraw()

    def remesh(self) -> None:
        """Voxel-Remesh (Strg+R wie Blender): das Netz gleichmäßig neu aufbauen; die Maske fällt dabei weg."""
        from ..core.features import FeatureError
        entry = self._snapshot()
        try:
            result = sculpt.voxel_remesh(self.mesh, self.panel.voxel.value())
        except FeatureError as error:
            self.window.statusBar().showMessage(str(error), 6000)
            return
        self.mesh.__init__(result.vertices, result.faces)
        self._push_undo(entry)
        self._mask_changed()
        self.changed = True
        self.redraw()
        self.window.statusBar().showMessage(f"Neu vernetzt: {len(self.mesh.faces):,} Dreiecke".replace(",", "."), 4000)

    def close(self) -> None:
        self._timer.stop()
        for layer in ("sculpt_mesh", "sculpt_mask", "sculpt_brush"):
            self.viewport.set_geometry(layer, np.zeros((0, 3), np.float32))
