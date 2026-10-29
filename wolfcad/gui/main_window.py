# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Hauptfenster: Werkzeugleiste, Browser, 3D-Ansicht und Timeline-Leiste."""

from __future__ import annotations

from pathlib import Path

import math
import re
import time
import os

import numpy as np
import dataclasses
import json

from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRep import BRep_Tool
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
from PySide6.QtCore import (QEvent, QFileSystemWatcher, QMimeData, QObject, QPoint, QPointF, QRectF, QSettings,
                            QSize, QTimer, Qt, Signal)
from PySide6.QtGui import QAction, QColor, QCursor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox,
    QProgressDialog, QPushButton, QScrollArea, QTabWidget, QToolBar, QToolButton, QToolTip, QTreeWidget, QVBoxLayout,
    QTreeWidgetItem, QWidget,
)

from ..core import constraints as cs
from ..core import features as ft
from ..core import exchange, guard, measure, replay, sketch, solids
from ..core import sheetmetal as sm
from ..core import surfaces as sf
from ..core import meshes as ms
from ..core import extras as ex
from ..core import more_tools as mt
from ..core import inserts
from ..core import parameters as pm
from ..core.timeline import GEOMETRY_LEVEL, ROBUST_KINDS, Timeline
from ..f3d.container import F3dArchive
from . import handles, mesh, picking, theme
from .drawing_ui import DrawingDialog
from .icons import icon, logo_pixmap
from .parameters_ui import ExpressionBox, ParameterDialog
from .region_picker import RegionPicker, _inside as _inside_triangle
from .ribbon import Ribbon
from .selection_field import SelectionField
from .physics_ui import PHYSICS_TAB, PANELS as _PHYSICS_PANELS, PhysicsTools
from . import tool_hints
from .browser import BrowserOverlay
from .data_panel import DataPanel, preview_path
from . import sketch_editor as se
from . import sketch3d
from . import draw3d
from .draw3d import PrimitiveDraw
from .marking_menu import MarkingMenu
from .navbar import SELECTION_FILTERS, NavigationBar
from . import settings
from .sketch3d import SketchPanel, SketchSession
from .viewport import Viewport

# Deutsche Beschriftungen der Parameterfelder
_LABELS = {
    "plane": "Ebene", "offset": "Ebenenversatz (mm)", "width": "Breite (mm)",
    "height": "Höhe (mm)", "radius": "Radius (mm)", "center_x": "Mittelpunkt X (mm)",
    "center_y": "Mittelpunkt Y (mm)", "sketch": "Skizze", "distance": "Abstand (mm)",
    "operation": "Operation", "source": "Quelldatei", "blob": "Geometrie-Blob",
    "length": "Länge (mm)", "center_z": "Mittelpunkt Z (mm)", "axis": "Achse", "angle": "Winkel (°)",
    "move_x": "Verschiebung X (mm)", "move_y": "Verschiebung Y (mm)", "move_z": "Verschiebung Z (mm)",
    "body": "Körper", "target": "Zielkörper", "tool": "Werkzeugkörper",
    "thickness": "Wandstärke (mm)", "opening": "Offene Seite (ohne Auswahl)",
    "edge_points": "Kanten (leer = alle scharfen)", "face_points": "Zu entfernende Flächen",
    "regions": "Profil", "region_points": "Gewählte Bereiche", "frame": "Lage",
    "extra_profiles": "Weitere Skizzenprofile", "placement": "Lage des Profils",
    "extent": "Richtung", "distance2": "Zweiter Abstand (mm)", "taper": "Verjüngung (°)",
    "chamfer_type": "Art", "radius2": "Radius am Ende (mm, 0 = gleich)", "tapped": "Gewinde", "points_sketch": "An Punkten der Skizze",
    "object_type": "Objekttyp", "source_feature": "Feature", "compute": "Berechnung",
    "rotate_axis": "Drehachse (durch die Flächenmitte)", "rotate_angle": "Drehwinkel (°)",
    "guide": "Führungsschiene", "guide_mode": "Führung",
    "target_body": "Körper der Ersatzfläche", "target_points": "Ersatzfläche",
    "move_x": "Verschiebung X (mm)", "move_y": "Verschiebung Y (mm)", "move_z": "Verschiebung Z (mm)",
    "orientation": "Ausrichtung",
    "start": "Startversatz (mm)", "target_points": "Zielfläche",
    "count": "Anzahl", "spacing": "Abstand (mm)", "second_axis": "Zweite Achse",
    "second_count": "Anzahl (zweite Achse)", "second_spacing": "Abstand zweite Achse (mm)", "position_x": "Position X (mm)", "position_y": "Position Y (mm)",
    "depth": "Tiefe (mm, 0 = durchgehend)",
    "fusion_id": "Fusion-ID", "content": "Inhalt", "up_to_step": "Bis Fusion-Schritt (0 = alle)", "length_x": "Länge X (mm)", "length_y": "Länge Y (mm)",
    "offset_x": "Versatz X (mm)", "offset_y": "Versatz Y (mm)", "offset_z": "Versatz Z (mm)",
    "diameter": "Durchmesser (mm)",
    "pitch": "Steigung (mm, 0 = ISO-Regelgewinde)",
    "head_angle": "Senkwinkel (°, Kegelsenkung)", "size": "Normgröße",
    "factor_y": "Faktor Y (0 = wie oben)", "factor_z": "Faktor Z (0 = wie oben)",
    "scope": "Gilt für",
    "color": "Farbe", "head": "Senkung", "head_diameter": "Senkdurchmesser (mm)",
    "head_depth": "Senktiefe (mm, Zylindersenkung)",
    "parent": "Gehört zu (Unterbaugruppe von)",
    "minimum": "Untere Grenze der Bewegung", "maximum": "Obere Grenze (beide 0 = frei)",
    "tilt": "Kippwinkel (°)", "tilt_axis": "Kippen um", "bodies": "Weitere Körper", "kinds": "Bezugsarten",
    "base": "Konstruktionsebene",
    "component": "Komponente", "joint": "Gelenkart", "facing": "Ausrichtung",
    "keep": "Behalten", "factor": "Faktor", "reference": "Bezugspunkt", "neutral": "Neutrale Ebene",
    "path": "Pfad (Skizze)", "sketch2": "Zweite Skizze", "sketch3": "Dritte Skizze (wahlfrei)",
}
# Beschriftungen, die nur bei einer Feature-Art anders lauten (Art, Feldname) -> Text
_KIND_LABELS = {
    ("Flansch", "thickness"): "Blechdicke (mm)", ("Flansch", "bend_radius"): "Biegeradius innen (mm)",
    ("Flansch", "k_factor"): "K-Faktor (0 … 1)", ("Flansch", "flip"): "Dicke zur anderen Seite",
    ("Flansch an Kante", "length"): "Länge gerader Teil (mm)", ("Flansch an Kante", "angle"): "Biegewinkel (°)",
    ("Flansch an Kante", "bend_radius"): "Biegeradius (mm, 0 = Blechregel)",
    ("Flansch an Kante", "flip"): "Zur anderen Seite biegen", ("Flansch an Kante", "edge_points"): "Kanten",
    ("Abwicklung", "body"): "Blechteil", ("Abwickeln", "body"): "Blechteil", ("Zurückbiegen", "body"): "Blechteil",
    ("In Blech umwandeln", "body"): "Körper (flache Platte)",
    ("Fläche extrudieren", "direction"): "Richtung", ("Fläche versetzen", "distance"): "Abstand (mm, negativ = andere Seite)",
    ("Aufdicken", "thickness"): "Dicke (mm, negativ = andere Seite)", ("Vernähen", "surfaces"): "Flächenkörper (anklicken)",
    ("Vernähen", "tolerance"): "Toleranz (mm)", ("Fläche versetzen", "surface"): "Flächenkörper",
    ("Aufdicken", "surface"): "Flächenkörper", ("Fläche drehen", "angle"): "Winkel (°)",
    ("Aufdicken", "flip"): "Zur anderen Seite", ("Fläche versetzen", "flip"): "Zur anderen Seite",
    ("Fläche sweepen", "sketch"): "Profil (Skizze)", ("Fläche erheben", "sketch"): "Erste Skizze",
    ("Fläche stutzen", "surface"): "Flächenkörper", ("Fläche stutzen", "tool"): "Schnittwerkzeug",
    ("Fläche stutzen", "remove_points"): "Wegfallende Stücke (anklicken)",
    ("Fläche verlängern", "surface"): "Flächenkörper", ("Trennen", "surface"): "Körper",
    ("Begrenzungsfüllung", "tools"): "Begrenzungen (anklicken)", ("Begrenzungsfüllung", "cell_points"): "Nur diese Zellen",
    ("Begrenzungsfüllung", "remove_points"): "Nicht gefüllte Zellen (anklicken)",
    ("Netz einfügen", "source"): "Datei", ("Körper einfügen", "source"): "Kopiert aus",
    ("Netz einfügen", "scale"): "Maßstab (z. B. 25,4 für Zoll)",
    ("Netz in Körper umwandeln", "mesh"): "Netzkörper", ("Netz verkleinern", "mesh"): "Netzkörper",
    ("Netz verkleinern", "cell"): "Rasterweite (mm)", ("Netz reparieren", "mesh"): "Netzkörper",
    ("Netz glätten", "mesh"): "Netzkörper", ("Netz glätten", "iterations"): "Durchgänge",
    ("Netz glätten", "strength"): "Stärke (0 … 1)", ("Körper in Netz umwandeln", "deflection"): "Feinheit (mm)",
    ("In Blech umwandeln", "bend_radius"): "Biegeradius innen (mm)", ("In Blech umwandeln", "k_factor"): "K-Faktor (0 … 1)",
    ("Biegen", "sketch"): "Skizze mit Biegelinie", ("Biegen", "angle"): "Biegewinkel (°)",
    ("Biegen", "bend_radius"): "Biegeradius (mm, 0 = Blechregel)", ("Biegen", "flip"): "Zur anderen Seite biegen",
    ("Biegen", "flip_side"): "Größeres Stück klappt", ("Biegen", "body"): "Blechteil",
    ("Flansch an Kante", "relief"): "Biegefreistellung",
    ("Flansch an Kante", "corner"): "Ecke zum Nachbarflansch", ("Flansch an Kante", "corner_gap"): "Spalt an der Ecke (mm)",
    ("Flansch an Kante", "offset_start"): "Abstand am Kantenanfang (mm)",
    ("Flansch an Kante", "offset_end"): "Abstand am Kantenende (mm)",
    ("Torus", "diameter"): "Ringdurchmesser (mm)", ("Torus", "section"): "Querschnitt (mm)",
    ("Spule", "diameter"): "Windungsdurchmesser (mm)", ("Spule", "pitch"): "Steigung (mm)",
    ("Spule", "turns"): "Windungen", ("Spule", "section"): "Drahtdurchmesser (mm)",
    ("Rohr", "diameter"): "Außendurchmesser (mm)", ("Rohr", "wall"): "Wandstärke (mm, 0 = voll)",
    ("Fläche teilen", "face_points"): "Zu teilende Flächen", ("Fläche teilen", "plane"): "Trennebene",
    ("Fläche teilen", "offset"): "Abstand der Ebene (mm)",
    ("Ausrichten", "source"): "Fläche am Körper (anklicken)", ("Ausrichten", "target"): "Zielfläche (anklicken)",
    ("Ausrichten", "flip"): "Gleich ausgerichtet statt gegeneinander",
    ("Regelfläche", "sketch"): "Erste Skizze", ("Normale umkehren", "surface"): "Flächenkörper",
    ("Netz schneiden", "mesh"): "Netzkörper", ("Netz schneiden", "plane"): "Schnittebene",
    ("Netz schneiden", "offset"): "Abstand der Ebene (mm)", ("Netz schneiden", "flip"): "Andere Seite behalten",
    ("Netz schneiden", "fill"): "Schnitt schließen", ("Netz trennen", "mesh"): "Netzkörper",
    ("Netze kombinieren", "meshes"): "Netzkörper (anklicken)", ("Netznormalen umkehren", "mesh"): "Netzkörper",
    ("Saum", "edge_points"): "Kanten", ("Saum", "length"): "Saumlänge (mm)", ("Saum", "gap"): "Spalt (mm)",
    ("Saum", "flip"): "Zur anderen Seite falten",
    ("Verrundung", "wall_aware"): "Gleich dicke Wand (außen Radius + Wandstärke)",
    ("Verschieben", "copy"): "Kopie erstellen", ("Kombinieren", "keep_tool"): "Werkzeugkörper behalten",
    ("Verschieben", "axis"): "Achse des Winkels", ("Verschieben", "angle"): "Drehen um diese Achse (°)",
    ("Verschieben", "angle_x"): "Drehen um X (°)", ("Verschieben", "angle_y"): "Drehen um Y (°)",
    ("Verschieben", "angle_z"): "Drehen um Z (°)",
    ("Kombinieren", "tools"): "Werkzeugkörper",
    ("Teilen", "tool"): "Trennwerkzeug (Körper/Fläche; leer = Ebene)", ("Schale", "direction"): "Richtung",
    ("Teilen", "tool_body"): "Körper der Trennfläche", ("Teilen", "tool_points"): "Trennflächen (anklicken)",
    ("Teilen", "extend"): "Trennwerkzeug verlängern",
    ("Drehen", "extent"): "Richtung", ("Drehen", "angle2"): "Winkel der zweiten Seite (°)",
    ("Prägen", "face_points"): "Fläche (anklicken)", ("Prägen", "depth"): "Tiefe (mm)", ("Prägen", "mode"): "Art",
    ("Prägen", "sketch"): "Profil (Skizze)",
    ("Steg", "thickness"): "Dicke (mm)", ("Steg", "depth"): "Tiefe (mm)", ("Steg", "flip"): "Zur anderen Seite",
    ("Konturflansch", "width"): "Breite (mm)", ("Konturflansch", "thickness"): "Blechdicke (mm)",
    ("Konturflansch", "bend_radius"): "Biegeradius innen (mm)", ("Konturflansch", "k_factor"): "K-Faktor (0 … 1)",
    ("Konturflansch", "flip"): "Blech auf der anderen Seite der Linie",
    ("Konturflansch", "flip_width"): "Breite zur anderen Seite", ("Konturflansch", "sketch"): "Linienzug (Skizze)",
    ("Aufreißen", "edge_points"): "Kanten (äußere Ecke)", ("Aufreißen", "gap"): "Spalt (mm)",
    ("Ebene entlang Pfad", "position"): "Stelle (0 = Anfang, 1 = Ende)",
    ("Achse senkrecht zur Fläche", "point"): "Stelle (anklicken)",
    ("Gelenkursprung", "point"): "Stelle (anklicken)", ("Gelenkursprung", "offset"): "Abstand entlang der Richtung (mm)",
    ("Gelenkursprung", "flip"): "Richtung umkehren",
    ("Gelenk", "origin"): "Gelenkursprung am Körper", ("Gelenk", "target_origin"): "Gelenkursprung am Ziel",
    ("Starre Gruppe", "bodies"): "Körper (anklicken)",
    ("Leinwand", "width"): "Breite (mm)", ("Leinwand", "angle"): "Drehung (°)", ("Leinwand", "opacity"): "Deckkraft (0 … 1)",
    ("Leinwand", "flip"): "Spiegeln", ("Leinwand", "frame"): "Auf Körperfläche (anklicken)",
    ("Verschieben", "pivot"): "Drehpunkt", ("Verschieben", "scale"): "Skalieren (Faktor)",
    ("Leinwand", "center_x"): "Mitte x (mm)", ("Leinwand", "center_y"): "Mitte y (mm)",
}
_SKETCH_TYPES = (ft.RectangleSketch, ft.CircleSketch, sketch.Sketch)
_SURFACE_COLOUR = "#d9b26a"     # Farbe der Flächenkörper in der Ansicht
# Farben für „Komponentenfarben“ (Fusion: Komponentenfarbwechsel): gut unterscheidbar, der Reihe nach vergeben
_COMPONENT_PALETTE = ("#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#46c0c0", "#f032e6", "#bcf60c",
                      "#9a6324", "#808000")
_MESH_COLOUR = "#7fb3a0"        # Farbe der Netzkörper


def _mesh_pick(triangles) -> "mesh.PickMesh":
    """Anzeige und Auswahl eines Netzkörpers: alle Dreiecke gehören zu einer „Fläche“, Kanten gibt es keine."""
    points = np.asarray(triangles, dtype=np.float32).reshape(-1, 3)
    centre = tuple(float(c) for c in points.mean(axis=0)) if len(points) else (0.0, 0.0, 0.0)
    return mesh.PickMesh(triangles=points, triangle_face=np.zeros(len(points) // 3, dtype=int), face_points=[centre],
                         segments=np.zeros((0, 3), dtype=np.float32), segment_edge=np.zeros(0, dtype=int),
                         edge_points=[])
# Datenrolle eines Browser-Eintrags: (Art, Name bzw. Nummer) – für das Rechtsklick-Menü
_BROWSER_KIND = Qt.ItemDataRole.UserRole + 1
# Werkzeugleiste wie Fusion 360: Reiter und ihre Bereiche in Fusions Reihenfolge …
SOLID_TAB, SURFACE_TAB, MESH_TAB, SHEET_TAB, UTILITY_TAB, SKETCH_TAB = (
    "VOLUMENKÖRPER", "FLÄCHE", "NETZ", "BLECH", "DIENSTPROGRAMME", "SKIZZE")
_RIBBON_PANELS = {
    SOLID_TAB: ("ERSTELLEN", "ÄNDERN", "ZUSAMMENFÜGEN", "KONSTRUIEREN", "PRÜFEN", "EINFÜGEN"),
    SURFACE_TAB: ("ERSTELLEN", "ÄNDERN", "PRÜFEN"),
    MESH_TAB: ("ERSTELLEN", "ÄNDERN", "AUSGABE"),
    SHEET_TAB: ("ERSTELLEN", "ÄNDERN", "PRÜFEN", "AUSGABE"),
    UTILITY_TAB: ("ANSICHT", "AUSGABE", "FUSION"),
    PHYSICS_TAB: _PHYSICS_PANELS,       # Runde 83: Physik-Tests (gui/physics_ui.py)
}
# … und wo jedes Werkzeug steht: Name -> [(Reiter, Bereich, als Symbol sichtbar)]; ein Werkzeug kann wie in
# Fusion in mehreren Reitern stehen (Skizze, Messen)
_RIBBON_PLACES = {
    "Skizze": [(SOLID_TAB, "ERSTELLEN", True), (SURFACE_TAB, "ERSTELLEN", True), (SHEET_TAB, "ERSTELLEN", True)],
    "Fläche extrudieren": [(SURFACE_TAB, "ERSTELLEN", True)], "Fläche drehen": [(SURFACE_TAB, "ERSTELLEN", True)],
    "Flicken": [(SURFACE_TAB, "ERSTELLEN", True)], "Fläche versetzen": [(SURFACE_TAB, "ERSTELLEN", True)],
    "Aufdicken": [(SURFACE_TAB, "ÄNDERN", True)], "Vernähen": [(SURFACE_TAB, "ÄNDERN", True)],
    "Fläche sweepen": [(SURFACE_TAB, "ERSTELLEN", False)], "Fläche erheben": [(SURFACE_TAB, "ERSTELLEN", False)],
    "Begrenzungsfüllung": [(SURFACE_TAB, "ERSTELLEN", False)],
    "Fläche stutzen": [(SURFACE_TAB, "ÄNDERN", True)], "Fläche verlängern": [(SURFACE_TAB, "ÄNDERN", False)],
    "Trennen": [(SURFACE_TAB, "ÄNDERN", False)],
    "Netz einfügen": [(MESH_TAB, "ERSTELLEN", True)], "Körper in Netz umwandeln": [(MESH_TAB, "ERSTELLEN", True)],
    "Netz in Körper umwandeln": [(MESH_TAB, "ÄNDERN", True)], "Netz verkleinern": [(MESH_TAB, "ÄNDERN", True)],
    "Netz reparieren": [(MESH_TAB, "ÄNDERN", True)], "Netz glätten": [(MESH_TAB, "ÄNDERN", True)],
    "Netz als STL speichern": [(MESH_TAB, "AUSGABE", True)],
    "Extrusion": [(SOLID_TAB, "ERSTELLEN", True)], "Drehen": [(SOLID_TAB, "ERSTELLEN", True)],
    "Sweep": [(SOLID_TAB, "ERSTELLEN", False)], "Erhebung": [(SOLID_TAB, "ERSTELLEN", False)],
    "Rippe": [(SOLID_TAB, "ERSTELLEN", False)], "Bohrung": [(SOLID_TAB, "ERSTELLEN", True)],
    "Gewinde": [(SOLID_TAB, "ERSTELLEN", False)], "Quader": [(SOLID_TAB, "ERSTELLEN", False)],
    "Zylinder": [(SOLID_TAB, "ERSTELLEN", False)], "Kugel": [(SOLID_TAB, "ERSTELLEN", False)],
    "Muster": [(SOLID_TAB, "ERSTELLEN", True)], "Kreismuster": [(SOLID_TAB, "ERSTELLEN", False)],
    "Pfadmuster": [(SOLID_TAB, "ERSTELLEN", False)], "Spiegeln": [(SOLID_TAB, "ERSTELLEN", False)],
    "Drücken/Ziehen": [(SOLID_TAB, "ÄNDERN", True)], "Abrunden": [(SOLID_TAB, "ÄNDERN", True)],
    "Fase": [(SOLID_TAB, "ÄNDERN", True)], "Schale": [(SOLID_TAB, "ÄNDERN", True)],
    "Schräge": [(SOLID_TAB, "ÄNDERN", False)], "Skalieren": [(SOLID_TAB, "ÄNDERN", False)],
    "Kombinieren": [(SOLID_TAB, "ÄNDERN", True)], "Fläche ersetzen": [(SOLID_TAB, "ÄNDERN", False)],
    "Fläche löschen": [(SOLID_TAB, "ÄNDERN", False)], "Fläche verschieben": [(SOLID_TAB, "ÄNDERN", False)],
    "Teilen": [(SOLID_TAB, "ÄNDERN", False)], "Verschieben": [(SOLID_TAB, "ÄNDERN", False)],
    "Farbe": [(SOLID_TAB, "ÄNDERN", False)], "Parameter": [(SOLID_TAB, "ÄNDERN", False)],
    "Komponente": [(SOLID_TAB, "ZUSAMMENFÜGEN", True)], "Gelenk": [(SOLID_TAB, "ZUSAMMENFÜGEN", True)],
    "Bewegen": [(SOLID_TAB, "ZUSAMMENFÜGEN", False)],
    "Ebene": [(SOLID_TAB, "KONSTRUIEREN", True)], "Achse": [(SOLID_TAB, "KONSTRUIEREN", False)],
    "Messen": [(SOLID_TAB, "PRÜFEN", True), (SURFACE_TAB, "PRÜFEN", True), (SHEET_TAB, "PRÜFEN", True)],
    "Kollision": [(SOLID_TAB, "PRÜFEN", True)], "Schnitt": [(SOLID_TAB, "PRÜFEN", True)],
    "Eigenschaften": [(SOLID_TAB, "PRÜFEN", False)],
    "Flansch": [(SHEET_TAB, "ERSTELLEN", True)], "Flansch an Kante": [(SHEET_TAB, "ERSTELLEN", True)],
    "Abwicklung": [(SHEET_TAB, "ERSTELLEN", True)], "In Blech umwandeln": [(SHEET_TAB, "ERSTELLEN", False)],
    "Zuschnitt (DXF)": [(SHEET_TAB, "AUSGABE", True)],
    "Abwickeln": [(SHEET_TAB, "ÄNDERN", True)], "Zurückbiegen": [(SHEET_TAB, "ÄNDERN", True)],
    "Biegen": [(SHEET_TAB, "ÄNDERN", True)],
    "3D-Druck": [(UTILITY_TAB, "AUSGABE", True), (MESH_TAB, "AUSGABE", True)],
    "Einpassen": [(UTILITY_TAB, "ANSICHT", True)], "Raster": [(UTILITY_TAB, "ANSICHT", True)],
    "Abspielen": [(UTILITY_TAB, "FUSION", True)], "Hilfskörper": [(UTILITY_TAB, "FUSION", True)],
    "Torus": [(SOLID_TAB, "ERSTELLEN", False)], "Spule": [(SOLID_TAB, "ERSTELLEN", False)],
    "Rohr": [(SOLID_TAB, "ERSTELLEN", False)],
    "Fläche teilen": [(SOLID_TAB, "ÄNDERN", False)], "Ausrichten": [(SOLID_TAB, "ÄNDERN", False)],
    "Physisches Material": [(SOLID_TAB, "ÄNDERN", False)], "Alle berechnen": [(SOLID_TAB, "ÄNDERN", False)],
    "Punkt": [(SOLID_TAB, "KONSTRUIEREN", False)],
    "Schwerpunkt": [(SOLID_TAB, "PRÜFEN", False)],
    "DXF/SVG einfügen": [(SOLID_TAB, "EINFÜGEN", True)],
    "Körper kopieren": [(SOLID_TAB, "EINFÜGEN", False)], "Körper einfügen": [(SOLID_TAB, "EINFÜGEN", True)],
    "Regelfläche": [(SURFACE_TAB, "ERSTELLEN", False)], "Normale umkehren": [(SURFACE_TAB, "ÄNDERN", False)],
    "Netz schneiden": [(MESH_TAB, "ÄNDERN", True)], "Netz trennen": [(MESH_TAB, "ÄNDERN", False)],
    "Netze kombinieren": [(MESH_TAB, "ÄNDERN", False)], "Netznormalen umkehren": [(MESH_TAB, "ÄNDERN", False)],
    "Saum": [(SHEET_TAB, "ERSTELLEN", False)],
    "Prägen": [(SOLID_TAB, "ERSTELLEN", False)], "Steg": [(SOLID_TAB, "ERSTELLEN", False)],
    "Konturflansch": [(SHEET_TAB, "ERSTELLEN", True)], "Aufreißen": [(SHEET_TAB, "ÄNDERN", False)],
    "Ebene entlang Pfad": [(SOLID_TAB, "KONSTRUIEREN", False)],
    "Achse senkrecht zur Fläche": [(SOLID_TAB, "KONSTRUIEREN", False)],
    "Gelenkursprung": [(SOLID_TAB, "ZUSAMMENFÜGEN", False)], "Starre Gruppe": [(SOLID_TAB, "ZUSAMMENFÜGEN", False)],
    "Leinwand": [(SOLID_TAB, "EINFÜGEN", False)], "Komponentenfarben": [(SOLID_TAB, "PRÜFEN", False)],
}
# Knöpfe mit eigenem Menü: Name -> (Reiter, Bereich)
_RIBBON_BUTTONS = {"Analyse": (SOLID_TAB, "PRÜFEN"), "Ansichten": (UTILITY_TAB, "ANSICHT")}
# Werkzeuge aus der schmalen Leiste oben, die Fusion zusätzlich in einem Bereich zeigt
_RIBBON_EXTRA = {"Import": (SOLID_TAB, "EINFÜGEN"), "Zeichnung": (UTILITY_TAB, "AUSGABE"),
                 "Export": (UTILITY_TAB, "AUSGABE")}
# Körper-Zwischenablage: eigenes Format in der System-Zwischenablage, dazu eine Datei, damit kopierte Körper
# auch nach dem Schließen des Programms in einen anderen Entwurf eingefügt werden können
_BODY_CLIPBOARD_MIME = "application/x-wolfcad-koerper"
_BODY_CLIPBOARD_FILE = Path.home() / ".local/share/wolfcad/zwischenablage.json"
_TEXT_FIELDS = {"component": "leer = Name dieses Schritts"}     # Feldname -> Hinweis im leeren Feld
# Tastenkürzel nach dem Vorbild von Fusion: beim Skizzieren Werkzeuge der Skizze, sonst die des Modells
_SKETCH_KEYS = {"L": "Linie", "R": "Rechteck", "C": "Kreis", "A": "Bogen", "D": "Bemaßung", "T": "Stutzen",
                "O": "Versatz", "X": "Konstruktion", "P": "Projizieren", "S": "Spline", "F": "Abrunden"}
# Das Werkzeug, das die Klassenzuordnung aus einer eigenen Fusion-Installation erzeugt, gibt es nur in der lokalen
# Fassung (nicht im öffentlichen Repository und nicht in den Paketen) – den Hinweis darauf nur zeigen, wenn es da ist
_SCHEMA_HINT = (" Erzeugen mit: python -m wolfcad.f3d.schema_extract <Fusion-Installationsordner>"
                if (Path(__file__).resolve().parent.parent / "f3d" / "schema_extract.py").exists() else "")
# Ablage für die Wiederherstellung nach einem Absturz und für das Fehlerprotokoll
# Programmzustand (Wiederherstellung, Fehlerprotokoll) nach XDG-Regel; ohne Angabe ~/.local/state/wolfcad,
# unter Windows %LOCALAPPDATA%\WolfCAD
STATE_FOLDER = (Path(os.environ["LOCALAPPDATA"]) / "WolfCAD" if os.name == "nt" and os.environ.get("LOCALAPPDATA")
                else Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "wolfcad")
RECOVERY_FILE = STATE_FOLDER / "wiederherstellung.json"
_RECOVERY_SECONDS = 20      # so oft wird der laufende Stand für die Wiederherstellung gesichert
_CANVAS_PIXELS = 2048       # größere Bilder für die Leinwand werden auf diese Kantenlänge verkleinert
_HOLE_SNAP_PIXELS = 14      # so nah (Bildpunkte) muss die Maus an Ecke/Kantenmitte, damit die Bohrung einrastet
# Knöpfe in der Ansicht für die Operation: Bezeichnung wie in Fusion
_OPERATION_LABELS = {"Vereinigen": "Verbinden", "Ausschneiden": "Ausschneiden", "Schnittmenge": "Schnittmenge",
                     "Neuer Körper": "Neuer Körper"}
_OPERATION_TIPS = {"Vereinigen": "Mit den berührten Körpern zu einem Körper verbinden",
                   "Ausschneiden": "Das neue Volumen aus den berührten Körpern herausschneiden",
                   "Schnittmenge": "Nur behalten, was im neuen Volumen und im berührten Körper liegt",
                   "Neuer Körper": "Als eigenen Körper stehen lassen"}
# Farbe des durchscheinenden Werkzeugvolumens je Operation (nur wo etwas wegfällt oder beschränkt wird)
_OPERATION_COLORS = {"Ausschneiden": (0.95, 0.25, 0.22), "Schnittmenge": (1.00, 0.74, 0.16)}
# Kombinieren (wie Fusion): Zielkörper blau markiert, Werkzeugkörper durchscheinend in der Farbe der Operation
_COMBINE_TARGET_COLOR = (0.16, 0.55, 0.95)
_COMBINE_TOOL_COLORS = {"Vereinigen": (0.22, 0.78, 0.45), **_OPERATION_COLORS}
_OPERATION_BAR_BODIES = 40      # bei mehr Körpern entfällt die Berührungsprüfung für die Knöpfe (zu aufwendig)
# Rechtsklick-Menü ohne offene Skizze und Trennstellen im Skizzenmenü
_PULL_FACE = "Senkrecht zur gewählten Fläche"      # Eintrag im Menü „Zugrichtung“
_MODEL_MENU = ("Skizze", "Extrusion", "Quader", "Zylinder", "Bohrung", "Abrunden", "Fase", "Verschieben", "Messen")
_SKETCH_MENU_BREAKS = ("Auswählen", "Text")
# Felder des runden Markierungsmenüs, oben beginnend im Uhrzeigersinn (unten liegt „fertigstellen“ bzw. Bohrung)
_SKETCH_RING = ("Linie", "Rechteck", "Kreis", "Bemaßung", "Stutzen", "Auswählen", "Bogen")
_MODEL_RING = ("Extrusion", "Quader", "Zylinder", "Abrunden", "Bohrung", "Verschieben", "Skizze", "Messen")
_MODEL_KEYS = {"Q": "Drücken/Ziehen", "E": "Extrusion", "H": "Bohrung", "F": "Abrunden", "M": "Verschieben", "J": "Gelenk",
               "I": "Messen", "S": "Skizze"}
_DENSITIES = {"PLA (1,24 g/cm³)": 1.24, "ABS (1,05 g/cm³)": 1.05, "PETG (1,27 g/cm³)": 1.27,
              "Aluminium (2,70 g/cm³)": 2.70, "Stahl (7,85 g/cm³)": 7.85, "Messing (8,50 g/cm³)": 8.50}
# Dateitypen des Exportdialogs für STL: Beschriftung -> größte Abweichung der Dreiecke in mm
_STL_FILTERS = {f"STL {name}, Abweichung {str(value).replace('.', ',')} mm (*.stl)": value
                for name, value in exchange.STL_QUALITY.items()}
_MOTION_STEPS = 72          # Bilder je Umlauf bzw. je Hin und Her bei „Bewegen“
_PLANE_PREFIX = "Ebene:"    # kennzeichnet im Ebenenmenü eine Konstruktionsebene
_UNDO_LIMIT = 50    # Zahl der gemerkten Rückgängig-Schritte
_VERTEX_PICK_PIXELS = 7  # Abstand in Bildpunkten, in dem ein Klick noch einen Eckpunkt trifft
_EDGE_PICK_PIXELS = 6    # Abstand in Bildpunkten, in dem ein Klick noch eine Kante trifft
# Farben der Timeline-Einträge je Zustand


def cs_tools() -> list[str]:
    """Die Abhängigkeits-Werkzeuge der Skizze (Bemaßung ausgenommen), in der Reihenfolge der Werkzeugliste."""
    start = se.TOOLS.index(se.DIMENSION_TOOL)
    # Abstand und Winkel sind Maße: sie stehen wie die Bemaßung unter ERSTELLEN
    return [tool for tool in se.TOOLS[start + 1:]
            if tool not in se.EDIT_TOOLS and tool not in (cs.DISTANCE, cs.ANGLE)]


class FeatureForm(QWidget):
    """Eingabefelder für die Parameter eines Features; meldet jede Änderung sofort.

    Das Formular sitzt im Eigenschaftenfeld neben der 3D-Ansicht. Weil jede Änderung
    gemeldet wird, kann das Modell beim Einstellen mitlaufen.
    """

    changed = Signal()
    cleared = Signal(str)       # Name eines Auswahlfelds, dessen ✕ geklickt wurde
    field_activated = Signal(str)   # Name eines Auswahlfelds (Kombinieren), das angeklickt und damit aktiv wurde

    def __init__(self, feature: ft.Feature, sketch_names: list[str], parent=None,
                 body_names: list[str] | None = None, resolver=None,
                 component_names: list[str] | None = None, plane_names: list[str] | None = None,
                 axis_names: list[str] | None = None, feature_names: list[str] | None = None,
                 surface_names: list[str] | None = None, mesh_names: list[str] | None = None,
                 origin_names: list[str] | None = None):
        super().__init__(parent)
        self._feature = feature
        # Rechnet Ausdrücke mit den Benutzerparametern aus; ohne Angabe gibt es keine Parameter
        resolver = resolver or pm.evaluate
        self._editors: dict[str, QWidget] = {}
        self._row_widgets: dict[str, QWidget] = {}      # Feld -> Widget seiner Formularzeile, wo es abweicht
        # Auswahlfelder wie in Fusion (Kombinieren: Zielkörper und Werkzeugkörper); sie gehören nicht zu den
        # Editoren, weil ihr Inhalt nur durch Klicks in der Ansicht entsteht und `values()` ihn nicht zurückschreibt
        self._selectors: dict[str, SelectionField] = {}
        form = QFormLayout(self)

        self._name = QLineEdit(feature.name)
        self._name.editingFinished.connect(self.changed)
        form.addRow("Name", self._name)
        bodies = list(body_names or [])
        choices = {"plane": list(ft.MAIN_PLANES), "tilt_axis": list(sketch.TILT_AXES), "operation": [ft.AUTO_OPERATION, *ft.OPERATIONS],
                   "sketch": sketch_names, "second_axis": list(solids.AXES),
                   "path": sketch_names, "sketch2": sketch_names,
                   "sketch3": [solids.NO_SKETCH, *sketch_names],
                   "points_sketch": [solids.NO_SKETCH, *sketch_names],
                   "guide": [solids.NO_SKETCH, *sketch_names], "guide_mode": list(solids.GUIDE_MODES),
                   # Muster von Features: frühere Schritte, die Körper verändert haben
                   "object_type": list(solids.PATTERN_OBJECTS), "source_feature": list(feature_names or []),
                   "compute": list(solids.PATTERN_COMPUTE), "rotate_axis": list(solids.AXES),
                   "content": list(ft.IMPORT_CONTENTS),
                   # Werkzeuge mit Körperwahl können auch eine ganze Komponente ansprechen
                   "body": [ft.ALL_BODIES, *bodies, *(component_names or [])],
                   "joint": list(solids.JOINT_KINDS), "facing": list(solids.JOINT_FACING),
                   "color": list(solids.BODY_COLORS), "head": list(solids.HOLE_HEADS),
                   "size": list(solids.THREAD_SIZES), "scope": list(solids.APPEARANCE_SCOPES),
                   "base": [ft.NO_BASE, *(plane_names or [])],
                   # übergeordnete Komponente (für Unterbaugruppen), nie die Komponente selbst
                   "parent": [ft.NO_BASE, *[name for name in (component_names or []) if name != feature.name]],
                   "target": bodies, "tool": bodies, "axis": list(solids.AXES),
                   "regions": list(ft.PROFILE_MODES), "placement": list(solids.SWEEP_PLACEMENTS),
                   "extent": list(ft.EXTENTS),
                   "chamfer_type": list(ft.CHAMFER_KINDS), "tapped": list(solids.TAP_OPTIONS),
                   "orientation": list(solids.PATH_ORIENTATIONS),
                   "opening": list(solids.OPENINGS), "keep": list(solids.SPLIT_KEEP),
                   "reference": list(solids.SCALE_REFERENCES), "neutral": list(solids.DRAFT_NEUTRAL)}
        # Einige Werkzeuge erlauben nur einen Teil der Auswahl
        # Drehen und Kreismuster können sich auch um eine Konstruktionsachse drehen
        if isinstance(feature, (solids.Revolve, sf.SurfaceRevolve)):
            choices["axis"] = [*solids.REVOLVE_AXES, *(axis_names or [])]
        # Flächen-Werkzeuge wählen einen Flächenkörper (auch durch Anklicken in der Ansicht)
        choices["surface"] = list(surface_names or [])
        choices["mesh"] = list(mesh_names or [])         # Netz-Werkzeuge wählen einen Netzkörper
        if isinstance(feature, sf.Unstitch):
            choices["surface"] = [*(surface_names or []), *bodies]      # Trennen zerlegt auch Volumenkörper
        if isinstance(feature, sf.TrimSurface):
            choices["tool"] = ["", *(surface_names or []), *bodies]      # Schnittwerkzeug
        choices["direction"] = list(sf.DIRECTIONS)
        choices["mode"] = list(mt.EMBOSS_MODES)                 # Prägen: erhaben oder vertieft
        if isinstance(feature, solids.Revolve):
            choices["extent"] = list(solids.REVOLVE_EXTENTS)     # Drehen: eine Seite, symmetrisch, zwei Seiten
        if isinstance(feature, solids.Shell):
            choices["direction"] = list(solids.SHELL_DIRECTIONS)  # Schale: innen, außen, beidseitig
        if isinstance(feature, solids.SplitBody):
            choices["tool"] = [*bodies, *(surface_names or [])]  # Trennwerkzeug: Körper oder Flächenkörper
            choices["tool_body"] = ["", *bodies, *(surface_names or [])]   # Herkunft der angeklickten Trennflächen
        # Gelenk an einem Gelenkursprung (sonst an der angeklickten Fläche)
        choices["origin"] = choices["target_origin"] = [ft.NO_BASE, *(origin_names or [])]
        choices["corner"] = list(sm.CORNERS)               # Ecke zwischen Blechflanschen
        if isinstance(feature, solids.CircularPattern):
            choices["axis"] = [*solids.AXES, *(axis_names or [])]
        if isinstance(feature, solids.ReplaceFace):
            choices["target"] = [*(plane_names or []), solids.TARGET_FACE]
            choices["target_body"] = bodies
        if isinstance(feature, (solids.Component, solids.Joint)):
            choices["body"] = bodies            # hier ist immer genau ein Körper gemeint
        if isinstance(feature, solids.CombineBodies):
            choices["operation"] = list(solids.COMBINE_OPERATIONS)
        choices["pivot"] = list(solids.MOVE_PIVOTS)         # Verschieben: Drehpunkt für Drehen und Skalieren
        if isinstance(feature, ex.Align):
            del choices["target"]           # hier ein angeklickter Punkt mit Normale, kein Körpername
        if isinstance(feature, (solids.MirrorBody, solids.RectangularPattern, solids.CircularPattern)):
            choices["operation"] = list(ft.OPERATIONS[:2])
        for item in feature.parameters():
            value = getattr(feature, item.name)
            if isinstance(feature, solids.CombineBodies) and item.name in ("target", "tools"):
                field = SelectionField(single=item.name == "target")
                field.set_names([value] if item.name == "target" else feature.tool_names())
                field.activated.connect(lambda name=item.name: self.field_activated.emit(name))
                field.cleared.connect(lambda name=item.name: self.cleared.emit(name))
                self._selectors[item.name] = field
                form.addRow(_KIND_LABELS.get((feature.KIND, item.name), _LABELS.get(item.name, item.name)), field)
                continue
            if item.name in choices:
                editor = QComboBox()
                editor.addItems(choices[item.name])
                # Ein gespeicherter Wert bleibt wählbar, auch wenn es ihn gerade nicht gibt
                if value and editor.findText(value) < 0:
                    editor.addItem(value)
                if value:
                    editor.setCurrentText(value)
                elif editor.findText("") < 0:
                    # leerer Wert (z. B. Extrusion einer Körperfläche ohne Skizze): ein leerer Eintrag bleibt gewählt –
                    # sonst stünde der erste Eintrag da und würde bei der nächsten Änderung stillschweigend übernommen
                    editor.insertItem(0, "")
                    editor.setCurrentIndex(0)
                editor.currentTextChanged.connect(lambda _text: self.changed.emit())
            elif isinstance(value, bool):
                # Ja/Nein-Schalter (z. B. „Zur anderen Seite biegen“)
                editor = QCheckBox()
                editor.setChecked(value)
                editor.toggled.connect(lambda _checked: self.changed.emit())
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                # Zahlenfeld, das auch Ausdrücke mit Parametern annimmt (z. B. "breite / 2")
                editor = ExpressionBox(resolver, live=True)      # Modell läuft beim Tippen mit
                editor.set_expression(feature.expressions.get(item.name, ""), value)
                editor.edited.connect(self.changed)
            elif isinstance(value, list):
                # Auswahl aus der 3D-Ansicht: wird nur angezeigt
                if item.name == "frame":
                    text = "gewählte Körperfläche" if value else "Hauptebene (siehe oben)"
                elif value and all(isinstance(entry, str) for entry in value):
                    text = ", ".join(value)             # Körpernamen (Werkzeugkörper beim Kombinieren)
                else:
                    text = f"{len(value)} gewählt" if value else "keine Auswahl"
                editor = QLineEdit(text)
                editor.setReadOnly(True)
                # ✕ auch an der gewählten Körperfläche: zurück auf die Hauptebene (Runde 81)
                if item.name.endswith("_points") or (item.name == "frame" and value):
                    editor.setToolTip("In der Ansicht anklicken: dazunehmen; nochmals anklicken: herausnehmen")
                    # ✕ wie in Fusion: die ganze Auswahl leeren, danach neu anklicken
                    clear = QToolButton()
                    clear.setText("✕")
                    clear.setToolTip("Auswahl leeren")
                    clear.clicked.connect(lambda _checked=False, name=item.name: self.cleared.emit(name))
                    row = QWidget()
                    line = QHBoxLayout(row)
                    line.setContentsMargins(0, 0, 0, 0)
                    line.addWidget(editor, 1)
                    line.addWidget(clear)
                    self._editors[item.name] = editor
                    self._row_widgets[item.name] = row      # die Formularzeile ist die Zeile mit dem ✕
                    label = _KIND_LABELS.get((feature.KIND, item.name), _LABELS.get(item.name, item.name))
                    form.addRow(label, row)
                    continue
            elif item.name in _TEXT_FIELDS:
                # frei eintragbarer Text
                editor = QLineEdit(str(value))
                editor.setPlaceholderText(_TEXT_FIELDS[item.name])
                editor.editingFinished.connect(self.changed)
            else:
                # Herkunftsangaben eines Imports werden nur angezeigt
                editor = QLineEdit(str(value))
                editor.setReadOnly(True)
            self._editors[item.name] = editor
            label = _KIND_LABELS.get((feature.KIND, item.name), _LABELS.get(item.name, item.name))
            form.addRow(label, editor)
        self._form = form
        # Nur die Felder zeigen, die zur gewählten Variante gehören (wie in Fusion): Richtung der Extrusion,
        # Art der Fase
        for chooser, table in (("extent", ft.EXTENT_FIELDS), ("chamfer_type", ft.CHAMFER_FIELDS),
                               ("object_type", solids.PATTERN_OBJECT_FIELDS)):
            editor = self._editors.get(chooser)
            if isinstance(editor, QComboBox):
                editor.currentTextChanged.connect(lambda text, rows=table: self._show_variant_fields(rows, text))
                self._show_variant_fields(table, editor.currentText())

    def _show_variant_fields(self, table: dict, chosen: str) -> None:
        for name, variants in table.items():
            editor = self._row_widgets.get(name, self._editors.get(name))
            if editor is not None:
                self._form.setRowVisible(editor, chosen in variants)

    def set_selection(self, name: str, names: list[str]) -> None:
        """Zeigt im Auswahlfeld `name` die gewählten Körper an."""
        if name in self._selectors:
            self._selectors[name].set_names(names)

    def set_active_field(self, name: str) -> None:
        """Hebt das Auswahlfeld hervor, in das der nächste Klick in der Ansicht geht."""
        for key, field in self._selectors.items():
            field.set_active(key == name)

    def set_field_color(self, name: str, color: tuple) -> None:
        if name in self._selectors:
            self._selectors[name].set_color(color)

    def set_list(self, name: str, value: list) -> None:
        """Zeigt eine in der Ansicht getroffene Auswahl (z. B. die Zielfläche) im Nur-Lese-Feld an."""
        editor = self._editors.get(name)
        if isinstance(editor, QLineEdit):
            if value and all(isinstance(item, str) for item in value):
                editor.setText(", ".join(value))        # Körpernamen (z. B. Werkzeugkörper) lesbar zeigen
            else:
                editor.setText(f"{len(value)} gewählt" if value else "keine Auswahl")

    def set_value(self, name: str, value: float) -> None:
        """Trägt einen Zahlenwert ein, ohne eine Änderung zu melden (z. B. beim Ziehen des Pfeils)."""
        editor = self._editors.get(name)
        if isinstance(editor, ExpressionBox) and not editor.hasFocus():
            # (tippt man gerade in diesem Feld, bleibt der getippte Text stehen)
            editor.set_expression("", value)    # ein gezogener Wert ist eine feste Zahl

    def set_choice(self, name: str, text: str) -> None:
        """Stellt ein Auswahlfeld um (z. B. die Operation über die Knöpfe in der Ansicht); das meldet eine Änderung."""
        editor = self._editors.get(name)
        if isinstance(editor, QComboBox) and editor.findText(text) >= 0:
            editor.setCurrentText(text)

    def commit(self) -> None:
        """Übernimmt gerade getippte, noch nicht bestätigte Zahlen."""
        for editor in self._editors.values():
            if isinstance(editor, QDoubleSpinBox):
                editor.interpretText()

    def expressions(self) -> dict:
        """Feldname -> Ausdruck für alle Zahlenfelder, in denen ein Ausdruck steht."""
        return {key: editor.expression for key, editor in self._editors.items()
                if isinstance(editor, ExpressionBox) and editor.expression}

    def values(self) -> dict:
        result = {"name": self._name.text().strip() or self._feature.name}
        for key, editor in self._editors.items():
            if isinstance(editor, QComboBox):
                result[key] = editor.currentText()
            elif isinstance(editor, QCheckBox):
                result[key] = editor.isChecked()
            elif isinstance(editor, QDoubleSpinBox):
                result[key] = editor.value()
            elif key in _TEXT_FIELDS:
                result[key] = editor.text().strip()
        return result


_PICK_CACHE_LIMIT = 3000        # so viele Körperzerlegungen bleiben höchstens gemerkt (siehe MainWindow._refresh)


class TimelineStrip(QWidget):
    """Die Einträge der Timeline als selbst gezeichneter Streifen.

    Früher war jeder Eintrag eine eigene Schaltfläche mit Stylesheet; bei einer übernommenen Fusion-Timeline mit
    über 5000 Schritten dauerte schon das Neuaufbauen nach jeder Änderung sekundenlang. Der Streifen merkt sich nur
    Text, Zustand und Lage je Eintrag und zeichnet nur, was gerade sichtbar ist."""

    _HEIGHT, _GAP, _PAD, _MARKER = 36, 3, 10, 5

    def __init__(self, bar: "TimelineBar"):
        super().__init__()
        self._bar = bar
        self._items: list[tuple[str, str, str]] = []     # (Text, Tooltip, Zustand) je Feature
        self._left: list[int] = []                         # linke Kante je Eintrag
        self._width: list[int] = []
        self._marker = 0
        self._highlighted = -1
        self.setFixedHeight(self._HEIGHT + 8)
        self.setMouseTracking(True)

    def set_items(self, items: list[tuple[str, str, str]], marker: int) -> None:
        self._items, self._marker = items, marker
        metrics = self.fontMetrics()
        x = 2
        self._left, self._width = [], []
        for index, (text, _tip, _state) in enumerate(items):
            if index == marker:
                x += self._MARKER + self._GAP              # Platz für die Rücksetzmarke
            width = metrics.horizontalAdvance(text) + 2 * self._PAD
            self._left.append(x)
            self._width.append(width)
            x += width + self._GAP
        if marker >= len(items):
            x += self._MARKER + self._GAP
        self.setFixedWidth(max(x + 2, 10))
        self.update()

    def marker_x(self) -> int:
        """Linke Kante der Rücksetzmarke."""
        if self._marker < len(self._left):
            return self._left[self._marker] - self._MARKER - self._GAP
        return (self._left[-1] + self._width[-1] + self._GAP) if self._left else 2

    def index_at(self, x: int) -> int:
        """Eintrag unter der Stelle x (−1, wenn keiner)."""
        import bisect
        position = bisect.bisect_right(self._left, x) - 1
        if 0 <= position < len(self._left) and x <= self._left[position] + self._width[position]:
            return position
        return -1

    def bounds_of(self, index: int) -> tuple[int, int]:
        return self._left[index], self._width[index]

    def set_highlight(self, index: int) -> None:
        self._highlighted = index
        self.update()

    @staticmethod
    def _colours(style: str) -> tuple[QColor, QColor, bool]:
        """Hintergrund, Schrift und Durchstreichen aus den Stylesheet-Angaben des Designs."""
        background = re.search(r"background:\s*([^;]+);", style)
        text = re.search(r"(?<!-)color:\s*([^;]+);", style)
        return (QColor(background.group(1).strip()) if background else QColor("#3d6fb5"),
                QColor(text.group(1).strip()) if text else QColor("white"), "line-through" in style)

    def paintEvent(self, event) -> None:
        import bisect
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        chips = theme.current().chips
        styles = {state: self._colours(chips.get(state, chips["rolled_back"]))
                  for state in ("active", "rolled_back", "suppressed", "error")}
        visible = event.rect()
        first = max(0, bisect.bisect_right(self._left, visible.left()) - 1)
        top = 4
        for index in range(first, len(self._items)):
            left, width = self._left[index], self._width[index]
            if left > visible.right():
                break
            text, _tip, state = self._items[index]
            placeholder = state == "placeholder"
            background, foreground, strike = styles["rolled_back" if placeholder else state]
            rect = QRectF(left, top, width, self._HEIGHT)
            painter.setPen(QPen(foreground, 1.2, Qt.PenStyle.DashLine) if placeholder else Qt.PenStyle.NoPen)
            painter.setBrush(Qt.BrushStyle.NoBrush if placeholder else background)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
            if index == self._highlighted:                # „In Timeline suchen“: gelber Rahmen
                painter.setPen(QPen(QColor("#f0c040"), 3))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), 4, 4)
            font = painter.font()
            font.setStrikeOut(strike)
            painter.setFont(font)
            painter.setPen(foreground)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        # Rücksetzmarke
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.current().marker))
        painter.drawRoundedRect(QRectF(self.marker_x(), top, self._MARKER, self._HEIGHT), 2, 2)
        painter.end()

    def mousePressEvent(self, event) -> None:
        index = self.index_at(int(event.position().x()))
        if index < 0:
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._bar.menu_requested.emit(index, event.globalPosition().toPoint())
        elif event.button() == Qt.MouseButton.LeftButton:
            # Klick setzt die Marke hinter diesen Eintrag
            self._bar.marker_requested.emit(index + 1)

    def mouseDoubleClickEvent(self, event) -> None:
        index = self.index_at(int(event.position().x()))
        if index >= 0 and event.button() == Qt.MouseButton.LeftButton:
            self._bar.edit_requested.emit(index)

    def wheelEvent(self, event) -> None:
        """Mausrad rollt die Timeline waagrecht (die Leiste ist nur eine Zeile hoch – senkrecht gibt es nichts)."""
        self._bar.scroll_by(event.angleDelta().y() or event.angleDelta().x())
        event.accept()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            index = self.index_at(int(event.pos().x()))
            if index >= 0:
                QToolTip.showText(event.globalPos(), self._items[index][1], self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)


class TimelineBar(QWidget):
    """Leiste am unteren Rand: ein Eintrag pro Feature plus Rücksetzmarke."""

    marker_requested = Signal(int)          # neue Position der Rücksetzmarke
    edit_requested = Signal(int)
    menu_requested = Signal(int, object)    # Feature-Index, Bildschirmposition

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        self._count = 0
        self._marker = 0

        # Schaltflächen zum schrittweisen Bewegen der Rücksetzmarke
        for text, tip, target in (
            ("⏮", "An den Anfang", lambda: 0),
            ("◀", "Einen Schritt zurück", lambda: self._marker - 1),
            ("▶", "Einen Schritt vor", lambda: self._marker + 1),
            ("⏭", "An das Ende", lambda: self._count),
        ):
            button = QPushButton(text)
            button.setFixedWidth(34)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, t=target: self.marker_requested.emit(t()))
            outer.addWidget(button)

        self.strip = TimelineStrip(self)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self.strip)
        self._scroll.setFixedHeight(58)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # die Bildlaufleiste immer zeigen, damit erkennbar ist, dass sich die Timeline rollen lässt
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        outer.addWidget(self._scroll, 1)

    def refresh(self, timeline: Timeline, awaiting: set | None = None) -> None:
        """Übernimmt Namen und Zustände aller Features (schnell, auch bei Tausenden von Schritten)."""
        self._count, self._marker = len(timeline.features), timeline.marker
        items = []
        for index, feature in enumerate(timeline.features):
            if index >= timeline.marker:
                state = "rolled_back"
            elif feature.suppressed:
                state = "suppressed"
            elif index in timeline.errors and timeline.errors[index] not in ft.HINT_MESSAGES \
                    and index not in (awaiting or set()):
                state = "error"
            elif getattr(feature, "placeholder", False):
                state = "placeholder"       # Fusion-Schritt, der (noch) nicht nachgebaut wird
            else:
                state = "active"
            tip = timeline.errors.get(index) or getattr(feature, "describe", lambda: feature.KIND)()
            items.append((feature.name, tip, state))
        moved = timeline.marker != self.strip._marker or len(items) != len(self.strip._items)
        self.strip.set_items(items, timeline.marker)
        if moved:
            # neuer Schritt oder Marke versetzt: die Marke sichtbar halten (sonst verschwindet das Ende der Timeline
            # bei langen Verläufen rechts aus dem Bild)
            # mit Bezugsobjekt: wird die Leiste vorher gelöscht (Fenster geschlossen), entfällt der Aufruf
            QTimer.singleShot(0, self._scroll, lambda: self._scroll.ensureVisible(self.strip.marker_x(), 20, 60, 0))

    def scroll_by(self, wheel_delta: int) -> None:
        """Rollt die Timeline um einen Mausradschritt (eine Rasterstufe ≈ drei Einträge)."""
        bar = self._scroll.horizontalScrollBar()
        bar.setValue(bar.value() - int(wheel_delta * 1.5))

    def wheelEvent(self, event) -> None:
        self.scroll_by(event.angleDelta().y() or event.angleDelta().x())
        event.accept()

    def highlight(self, index: int) -> None:
        """Hebt den Eintrag `index` einige Sekunden hervor und rollt ihn ins Bild (Fusion: „In Timeline suchen“)."""
        if not 0 <= index < self._count:
            return
        left, width = self.strip.bounds_of(index)
        self._scroll.ensureVisible(left + width // 2, 20, width, 0)
        self.strip.set_highlight(index)
        QTimer.singleShot(3000, lambda: self.strip.set_highlight(-1))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.timeline = Timeline()
        self._path: Path | None = None

        self.viewport = Viewport()
        # Ziehwerte sammeln und nur den neuesten rechnen (siehe _handle_changed)
        self._pending_handle = None
        self._handle_timer = QTimer(self)
        self._handle_timer.setSingleShot(True)
        self._handle_timer.setInterval(0)
        self._handle_timer.timeout.connect(self._apply_pending_handle)
        # Zebrastreifen hängen von der Blickrichtung ab: nach dem Drehen (gedrosselt) neu einfärben
        self._zebra_timer = QTimer(self)
        self._zebra_timer.setSingleShot(True)
        self._zebra_timer.setInterval(60)
        self._zebra_timer.timeout.connect(self._recolour_zebra)
        self.viewport.view_changed.connect(
            lambda: self._zebra_timer.start() if (getattr(self, "_analysis", None) or {}).get("art") == "Zebra" else None)
        # Mittlerer Bereich als Reiter: „Entwurf“ (die 3D-Ansicht) und – sobald geöffnet – „Druck“ (Nachbau des
        # Creality-Print-Ablaufs). Mit nur einem Reiter ist die Reiterleiste ausgeblendet (sieht aus wie bisher).
        self._documents = QTabWidget()
        self._documents.setDocumentMode(True)
        self._documents.setTabBarAutoHide(True)
        self._documents.addTab(self.viewport, "Entwurf")
        self._documents.setTabsClosable(True)
        self._documents.tabCloseRequested.connect(self._close_document_tab)
        self._documents.tabBar().setTabButton(0, self._documents.tabBar().ButtonPosition.RightSide, None)
        self._print_workspace = None
        self.setCentralWidget(self._documents)

        # Browser wie in Fusion: schwebend oben links auf der 3D-Ansicht
        self._browser_overlay = BrowserOverlay(self.viewport)
        # Navigationsleiste wie in Fusion: unten mittig auf der Ansicht (Orbit, Schwenken, Zoom, Darstellung …)
        self._select_filter = set(SELECTION_FILTERS)        # was ein Klick wählen darf (Auswahlfilter)
        self._navbar = NavigationBar(self.viewport, QSettings("WolfCAD", "WolfCAD").value("raster", "ja") != "nein")
        self._navbar.mode_changed.connect(lambda mode: setattr(self.viewport, "left_mode", mode))
        self._navbar.look_at_requested.connect(self._look_at_selection)
        self._navbar.fit_requested.connect(lambda: self._refresh(fit=True))
        self._navbar.style_changed.connect(self._set_display_style)
        self._navbar.filter_changed.connect(self._set_selection_filter)
        style = QSettings("WolfCAD", "WolfCAD").value("darstellung", "shaded_edges")
        self.viewport.display_style = style if style in ("shaded_edges", "shaded", "wireframe") else "shaded_edges"
        self._navbar.set_style(self.viewport.display_style)
        self.browser = self._browser_overlay.tree
        # Häkchen vor einem Körper blendet ihn ein oder aus (wie das Auge im Fusion-Browser)
        self._hidden_bodies: set[str] = set()
        self._isolated_before: set[str] | None = None      # Sichtbarkeit vor „Isolieren“
        self._filling_browser = False
        self.browser.itemChanged.connect(self._browser_item_changed)
        self.browser.itemClicked.connect(self._browser_clicked)        # Klick markiert in der 3D-Ansicht
        self._highlighted_sketch: str | None = None
        self.browser.customContextMenuRequested.connect(self._browser_menu)
        # An der alten Stelle die Projektmappe (Fusions Datenpanel): Projekte und Entwürfe zum schnellen Öffnen
        self.data_panel = DataPanel()
        self.data_panel.open_requested.connect(self._open_path)
        dock = QDockWidget("Projektmappe")
        dock.setObjectName("dataPanel")
        dock.setWidget(self.data_panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        self._physics = PhysicsTools(self)          # Reiter PHYSIK: Lasten, Festigkeit, Strömung, Schwimmen
        self.bar = TimelineBar()
        self.bar.marker_requested.connect(self._set_marker)
        self.bar.menu_requested.connect(self._show_feature_menu)
        self.bar.edit_requested.connect(self._edit)          # Doppelklick auf einen Eintrag: bearbeiten
        timeline_dock = QDockWidget("Timeline")
        timeline_dock.setWidget(self.bar)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, timeline_dock)
        # Physik-Timeline (Runde 86): Lager, Lasten, Rechnungen – nur im Reiter PHYSIK sichtbar, unter der Timeline
        physics_dock = QDockWidget("Physik-Timeline")
        physics_dock.setObjectName("physicsTimeline")
        physics_dock.setWidget(self._physics.strip())
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, physics_dock)
        # unter (nicht neben) der Timeline, über die volle Breite
        self.splitDockWidget(timeline_dock, physics_dock, Qt.Orientation.Vertical)
        physics_dock.hide()
        self._physics.dock = physics_dock

        # Nur lesbare Liste der Konstruktionsschritte einer importierten Fusion-Datei
        self.history = QListWidget()
        self.history.itemDoubleClicked.connect(lambda item: self._adopt_step(self.history.row(item)))
        self.history.itemClicked.connect(lambda item: self._show_fusion_state(self.history.row(item)))
        self._mesh_cache: dict[int, tuple] = {}
        self._undo_stack: list[dict] = []
        # Laufende Bearbeitung eines Features: Index, ob neu, alte Werte, Formular (None = keine)
        self._live: dict | None = None
        self._handles: list = []
        self.viewport.handle_changed.connect(self._handle_changed)
        self.viewport.handle_released.connect(self._restore_light_handle)
        # Bohrung wie in Fusion verschieben: Griffpunkt in der Bohrungsebene, rastet an Ecken, Kantenmitten und Raster
        self.viewport.point_dragged.connect(self._hole_dragged)
        self.viewport.point_released.connect(lambda: self.viewport.show_hint(""))
        # leichte Vorschau beim Ziehen am Pfeil (siehe _apply_handle_value): {"index", "operation"} oder None
        self._light_handle: dict | None = None
        self._build_panel()
        self._picks: dict[str, mesh.PickMesh] = {}      # Körpername -> Zerlegung mit Zuordnung
        self._pick_cache: dict[int, tuple] = {}
        self._selection: list[tuple[str, str, int]] = []    # (Art, Körper, Nummer); Art: "face"/"edge"
        self.viewport.clicked.connect(self._on_click)
        self.viewport.box_selected.connect(self._box_select)
        # Ebenenwahl in der Ansicht (z. B. für eine neue Skizze): Kandidaten und Rückruf, sonst None
        self._plane_pick: dict | None = None
        self._draw: PrimitiveDraw | None = None     # läuft gerade das Aufziehen eines Grundkörpers?
        self._analysis: dict | None = None          # Prüfmodus: {"art": "Entformung"/"Wandstärke", "wert": …}
        # Schutz vor Datenverlust: nur im echten Programm eingeschaltet (siehe __main__), nicht in Tests
        self.safeguards = False
        self._saved_text = ""                       # der Entwurf, wie er zuletzt gespeichert oder geladen wurde
        self._recovery_text = ""                    # der zuletzt für die Wiederherstellung gesicherte Stand
        self._recovery_timer = QTimer(self)
        self._recovery_timer.setInterval(_RECOVERY_SECONDS * 1000)
        self._recovery_timer.timeout.connect(self.write_recovery)
        self._profile_pick: dict | None = None      # Profilwahl für die Extrusion: Bereiche und gewählte Nummern
        self._pending_presspull = False             # Drücken/Ziehen wartet auf den Klick auf Fläche oder Profil
        self._selected_profiles: list[dict] = []    # im Modell angeklickte Profile (vor „Extrusion“)
        self._operation_bar: QWidget | None = None  # Knöpfe „Verbinden/Ausschneiden/…“ in der Ansicht
        self._operation_buttons: dict[str, QPushButton] = {}
        self.viewport.hovered.connect(self._profile_hover)
        self.viewport.context_requested.connect(self._context_menu)
        # Farbdatei der Shell beobachten, damit das Design „System“ einem Wechsel des Farbschemas folgt
        self._colour_watch = QFileSystemWatcher([str(path) for path in [*theme.watched_files(),
                                                                       *theme.watched_folders()]], self)
        self._colour_watch.fileChanged.connect(lambda path: QTimer.singleShot(200, lambda: self._desktop_colours_changed(path)))
        self._colour_watch.directoryChanged.connect(
            lambda _folder: QTimer.singleShot(200, lambda: self._desktop_colours_changed(None)))
        # Aussehen des Betriebssystems (hell/dunkel, Akzentfarbe – Linux wie Windows): auch dem folgt „System“ sofort
        self._system_watch = theme.SystemWatcher(QApplication.instance(), self)
        self._system_watch.changed.connect(lambda: self._desktop_colours_changed(None))
        self._vertex_cache: dict[str, tuple] = {}       # Körpername -> (Kennung der Form, Eckpunkte)
        # Körpername -> je fangbarem Punkt, wie weit er hinter der Fläche liegen darf (0 = Ecke, Radius = Kreismitte)
        self._vertex_depth: dict[str, np.ndarray] = {}
        self.viewport.hovered.connect(self._plane_hover)
        # Ausdrücke in Eingabefeldern der Ansicht rechnen mit den Benutzerparametern
        self.viewport.resolver = lambda text: pm.evaluate(text, self.timeline.parameter_values)
        self.viewport.home_requested.connect(lambda: self._refresh(fit=True))
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._escape)
        # Tastenkürzel wie in Fusion: eine Taste wählt ein Werkzeug (in den Einstellungen änderbar)
        self._key_shortcuts: list[QShortcut] = []
        self._macro_shortcuts: list[QShortcut] = []     # Tasten der Makros (siehe settings.macros)
        self._model_keys: dict[str, str] = {}       # Taste -> Werkzeug im Modell
        self._sketch_keys: dict[str, str] = {}      # Taste -> Werkzeug in der Skizze
        self._apply_settings()
        self.viewport.gesture_moved.connect(self._gesture_moved)
        self.viewport.gesture_released.connect(self._gesture_released)
        self.viewport.pressed.connect(self._close_marking)
        # Strg+C/Strg+V: beim Skizzieren Skizzenelemente, sonst die gewählten Körper (auch zwischen Entwürfen)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Copy), self, activated=self._copy)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Paste), self, activated=self._paste)
        QShortcut(QKeySequence(QKeySequence.StandardKey.SelectAll), self, activated=self._select_all_sketch)
        self._sketch_clipboard: dict | None = None      # in einer Skizze kopierte Elemente
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, activated=self._delete_pressed)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._next_plane)
        QShortcut(QKeySequence(Qt.Key.Key_Return), self, activated=lambda: self._finish_live(True))
        QShortcut(QKeySequence(Qt.Key.Key_Enter), self, activated=lambda: self._finish_live(True))
        self._redo_stack: list[dict] = []
        self._history_entries: list = []
        self._history_source = ""
        self.history_dock = QDockWidget("Fusion-Verlauf")
        self.history_dock.setWidget(self.history)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.history_dock)
        self.history_dock.hide()

        self._build_toolbar()
        self._apply_settings()      # erst jetzt sind alle Werkzeuge bekannt, denen eine Taste gehören kann
        self.resize(1300, 820)
        self._refresh(fit=True)

    def _desktop_colours_changed(self, path: str | None) -> None:
        """Shell oder Betriebssystem haben ihr Farbschema geändert: im Design „System“ zieht das Programm sofort nach."""
        # manche Programme ersetzen die Datei, dann endet ihre Beobachtung; neu angelegte Dateien kommen dazu
        for watched in [*theme.watched_files(), *theme.watched_folders()]:
            if str(watched) not in [*self._colour_watch.files(), *self._colour_watch.directories()]:
                self._colour_watch.addPath(str(watched))
        if theme.current().name == theme.SYSTEM:
            self.set_design(theme.SYSTEM, remember=False)

    def _update_logo(self) -> None:
        """Zeichnet das Logo oben links in Blautönen, passend zu hellem oder dunklem Design."""
        window = theme.current().extra.get("colours", {}).get("window", "#202024")
        if QColor(window).lightness() > 140:
            strong, soft = "#0b4a8f", "#4f9be0"         # heller Grund: tiefes Blau, mittleres Blau
        else:
            strong, soft = "#3fa2ff", "#b9e0ff"         # dunkler Grund: leuchtendes Blau, Eisblau
        self._logo.setPixmap(logo_pixmap(34, strong, soft))

    def set_design(self, name: str, remember: bool = True) -> None:
        """Wechselt das Design im laufenden Programm (siehe gui/theme.py)."""
        theme.apply(QApplication.instance(), name, remember)
        for entry_name, entry in self._design_actions.items():
            entry.setChecked(entry_name == theme.current().name)
        self.viewport.apply_theme()
        self._update_logo()
        self._refresh()             # Timeline-Einträge neu einfärben

    # --- Aufbau -----------------------------------------------------------

    def _build_toolbar(self) -> None:
        """Zwei Leisten wie in Fusion: oben schmal Datei, Rückgängig und Design, darunter die Werkzeuge,
        gegliedert in Skizze, Erstellen, Ändern, Baugruppe, Prüfen und Ansicht."""
        # Schmale Leiste nur mit Symbolen (Name im Tooltip) – so passen die Werkzeuge darunter in eine Zeile
        top = self.addToolBar("Datei")
        top.setMovable(False)
        top.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        top.setIconSize(QSize(26, 26))
        # ganz links das Logo des Nutzers, einfarbig in der Textfarbe des Designs
        self._logo = QLabel()
        self._logo.setContentsMargins(6, 0, 8, 0)
        self._logo.setToolTip("WolfCAD")
        top.addWidget(self._logo)
        self._update_logo()
        self.addToolBarBreak()
        bar = self.addToolBar("Werkzeuge")
        bar.setMovable(False)
        # Darunter die Werkzeugleiste wie in Fusion 360: Arbeitsbereich, Reiter, Bereiche mit ▾-Listen
        ribbon = self._ribbon = Ribbon()
        ribbon.set_workspace("KONSTRUKTION")
        ribbon.workspace.menu().addAction("Konstruktion").setEnabled(False)
        ribbon.workspace.menu().addAction("Zeichnung …", lambda: self._make_drawing())
        ribbon.workspace.menu().addSeparator()
        ribbon.workspace.menu().addAction("Über WolfCAD …", self._show_about)     # Lizenzhinweise (GPL-3.0 §5d)
        panels = {}
        for tab, titles in _RIBBON_PANELS.items():
            ribbon.add_tab(tab)
            for title in titles:
                panels[(tab, title)] = ribbon.add_panel(tab, title)
        bar.addWidget(ribbon)
        ribbon.tab_changed.connect(self._physics.tab_changed)     # Physik-Timeline mit dem Reiter PHYSIK

        # Name -> Werkzeug des Modells (für Tastenkürzel, Markierungsmenü und die Liste der Einstellungen); die
        # gleichnamigen Werkzeuge des Reiters SKIZZE (Abrunden, Spiegeln, Muster …) stehen bewusst nicht darin
        self._tool_actions: dict[str, QAction] = {}

        def action(text: str, symbol: str, handler, tip: str = "", shortcut=None, where=None) -> QAction:
            item = QAction(icon(symbol), text, self)
            self._tool_actions.setdefault(text, item)
            # Die Symbole stehen ohne Text (wie in Fusion): der Tooltip nennt immer auch den Namen
            item.setToolTip(f"{text} – {tip}" if tip else text)
            if shortcut is not None:
                item.setShortcut(shortcut)
            # über die Oberfläche gestartet (Leiste, Menü, Taste, Markierungsmenü): Werte beginnen bei 0
            item.triggered.connect(lambda _checked=False, run=handler: self._start_tool(run))
            self.addAction(item)            # Tastenkürzel wirken auch, wenn das Werkzeug nur in einer Liste steht
            if where is not None:
                where.addAction(item)
                if text in _RIBBON_EXTRA:
                    panels[_RIBBON_EXTRA[text]].add(item, True)
            for tab, title, featured in _RIBBON_PLACES.get(text, ()):
                panels[(tab, title)].add(item, featured)
            return item

        def group(title: str) -> None:
            """Früher Gruppen der flachen Leiste; die Bereiche legt jetzt `_RIBBON_PLACES` fest."""

        class _ButtonPlacer:
            """Nimmt die Knöpfe mit eigenem Menü (Analyse, Ansichten) an der richtigen Stelle der Leiste auf."""

            @staticmethod
            def addWidget(button) -> None:
                panels[_RIBBON_BUTTONS[button.text()]].add_button(button)

        bar = _ButtonPlacer()

        self._physics.build(action, panels)     # Reiter PHYSIK (eigene Bereiche, siehe physics_ui.TOOLS)
        action("Neu", "new", self._new, shortcut=QKeySequence.StandardKey.New, where=top)
        action("Öffnen", "open", self._open, shortcut=QKeySequence.StandardKey.Open, where=top)
        action("Speichern", "save", self._save, shortcut=QKeySequence.StandardKey.Save, where=top)
        action("Import", "import", self._import_f3d, "Fusion-Datei (.f3d) oder STEP-Datei importieren", where=top)
        action("Zeichnung", "drawing", self._make_drawing,
               "Zeichnungsblatt mit Ansichten der sichtbaren Körper als PDF oder SVG", where=top)
        action("Export", "export", self._export,
               "Sichtbare Körper als STEP oder STL speichern (z. B. für den 3D-Druck)", where=top)
        action("3D-Druck", "print3d", lambda: self.open_print_workspace(False),
               "Druck-Reiter öffnen: Körper aufs Druckbett legen, slicen und an den Drucker senden", where=top)
        top.addSeparator()
        action("Rückgängig", "undo", self._undo, shortcut=QKeySequence.StandardKey.Undo, where=top)
        action("Wiederholen", "redo", self._redo, shortcut=QKeySequence.StandardKey.Redo, where=top)
        top.addSeparator()

        group("SKIZZE")
        action("Ebene", "plane", self._create_plane,
               "Konstruktionsebene: versetzt zu einer ebenen Fläche oder Hauptebene; zwei Flächen gewählt: "
               "Mittelebene; gekrümmte Fläche: Tangentialebene; drei Punkte oder zwei Kanten: Ebene hindurch")
        action("Achse", "axis", self._create_axis,
               "Konstruktionsachse: durch eine gewählte gerade Kante, die Achse einer Bohrung/Welle, "
               "den Mittelpunkt einer Kreiskante oder zwei Eckpunkte")
        action("Punkt", "construction_point", self._create_point,
               "Konstruktionspunkt: am gewählten Eckpunkt, an der Kanten- oder Flächenmitte, sonst im Ursprung")
        action("Ebene entlang Pfad", "plane_path", self._create_plane_along_path,
               "Konstruktionsebene senkrecht zum Pfad der letzten Skizze, an einer Stelle zwischen Anfang und Ende")
        action("Achse senkrecht zur Fläche", "axis_normal", lambda: self._create(mt.AxisNormalToFace()),
               "Konstruktionsachse durch eine angeklickte Stelle einer Fläche, senkrecht zu ihr")
        action("Skizze", "sketch", self._create_sketch,
               "Skizze zeichnen – auf einer Hauptebene oder auf der gewählten ebenen Fläche")

        group("ERSTELLEN")
        action("Extrusion", "extrude", self._create_extrude, "Profil einer Skizze in die Höhe ziehen")
        action("Drehen", "revolve", self._create_revolve, "Profil einer Skizze um eine Achse drehen")
        action("Sweep", "sweep", self._create_sweep, "Profil einer Skizze entlang einer zweiten Skizze (Pfad) ziehen")
        action("Erhebung", "loft", self._create_loft, "Profile mehrerer Skizzen zu einem Körper verbinden")
        action("Rippe", "rib", self._create_rib, "Wand zwischen den Linien einer Skizze und dem Körper")
        # Grundkörper werden wie in Fusion direkt auf dem Bodenraster aufgezogen
        action("Quader", "box", lambda: self._draw_primitive(solids.Box(operation=self._default_operation())),
               "Quader: erste Ecke auf dem Raster anklicken, zur zweiten ziehen; die Höhe danach am Pfeil")
        action("Zylinder", "cylinder", lambda: self._draw_primitive(solids.Cylinder(operation=self._default_operation())),
               "Zylinder: Mittelpunkt auf dem Raster anklicken, den Rand aufziehen; die Höhe danach am Pfeil")
        action("Kugel", "sphere", lambda: self._draw_primitive(solids.Sphere(operation=self._default_operation())),
               "Kugel: Mittelpunkt auf dem Raster anklicken und den Rand aufziehen")
        action("Torus", "torus", lambda: self._create(ex.Torus(operation=self._default_operation())),
               "Ring: Ring- und Querschnittsdurchmesser einstellen")
        action("Spule", "coil", lambda: self._create(ex.Coil(operation=self._default_operation())),
               "Schraubenfeder: Windungsdurchmesser, Steigung, Windungen und Drahtdurchmesser")
        action("Rohr", "pipe", self._create_pipe,
               "Rohr entlang der letzten Skizze (Pfad): voll oder mit Wandstärke")
        action("Prägen", "emboss", lambda: self._create_from_sketch(mt.Emboss),
               "Profil der letzten Skizze auf eine ebene oder zylindrische Fläche prägen (erhaben oder vertieft)")
        action("Steg", "web", lambda: self._create_from_sketch(mt.Web),
               "Offene Linien der letzten Skizze werden zu Wänden mit Dicke und Tiefe")
        action("Bohrung", "hole", self._create_hole,
               "Bohrung auf der gewählten ebenen Fläche (ohne Auswahl: auf einer Hauptebene)")
        action("Spiegeln", "mirror", lambda: self._create(solids.MirrorBody()))
        action("Muster", "pattern", lambda: self._create(solids.RectangularPattern()),
               "Körper in einem Raster wiederholen")
        action("Kreismuster", "pattern_circular", lambda: self._create(solids.CircularPattern()),
               "Körper um eine Achse herum wiederholen")
        action("Pfadmuster", "pattern_path", self._create_path_pattern,
               "Muster entlang einer Kurve: Körper gleichmäßig auf dem Pfad einer Skizze verteilen")

        group("ÄNDERN")
        action("Abrunden", "fillet", lambda: self._create(ft.Fillet(require_pick=True, wall_aware=True)),
               "Gewählte Kanten abrunden (ohne Auswahl: alle scharfen Kanten)")
        action("Fase", "chamfer", lambda: self._create(ft.Chamfer(require_pick=True)),
               "Gewählte Kanten fasen (ohne Auswahl: alle scharfen Kanten)")
        action("Schale", "shell", lambda: self._create(solids.Shell(require_pick=True)),
               "Körper aushöhlen; die gewählten Flächen werden geöffnet")
        action("Fläche ersetzen", "replace_face", self._create_replace_face,
               "Gewählte ebene Fläche ersetzen: durch eine Konstruktionsebene oder – zwei Flächen anklicken – durch eine (auch gekrümmte) Fläche eines anderen Körpers")
        action("Fläche löschen", "delete_face", lambda: self._create(solids.DeleteFace()),
               "Gewählte Flächen entfernen (Verrundung, Fase, Bohrung …) – die Nachbarflächen schließen die Lücke")
        action("Fläche verschieben", "move_face", lambda: self._create(solids.MoveFace()),
               "Gewählte Flächen verschieben (z. B. eine Bohrung oder Tasche versetzen, eine Oberseite anheben)")
        action("Drücken/Ziehen", "presspull", self._create_presspull,
               "Drücken/Ziehen (Q): eine ebene Fläche anklicken, dann am Pfeil herausziehen oder hineindrücken")
        action("Schräge", "draft", lambda: self._create(solids.Draft(require_pick=True)),
               "Entformungsschräge: gewählte Flächen neigen (ohne Auswahl: alle Seitenwände)")
        action("Teilen", "split", self._create_split,
               "Körper an einer Ebene teilen: an der gewählten ebenen Fläche oder einer Hauptebene")
        action("Skalieren", "scale", lambda: self._create(solids.ScaleBody()), "Körper gleichmäßig vergrößern oder verkleinern")
        action("Gewinde", "thread", lambda: self._create(solids.Thread()),
               "Gewinde in die gewählte Zylinderfläche schneiden (außen oder in einer Bohrung)")
        action("Fläche teilen", "split_face", lambda: self._create(ex.SplitFace()),
               "Gewählte Flächen an einer Ebene in zwei Flächen teilen (z. B. für Schräge oder Farbe)")
        action("Ausrichten", "align", lambda: self._create(ex.Align()),
               "Erst eine Fläche am zu bewegenden Körper, dann die Zielfläche anklicken: der Körper legt sich an")
        action("Physisches Material", "material", self._choose_material,
               "Werkstoff der gewählten (sonst aller sichtbaren) Körper für Masse und Schwerpunkt")
        action("Alle berechnen", "compute_all", self._compute_all,
               "Das ganze Modell von vorn neu berechnen (Strg+R)", shortcut=QKeySequence("Ctrl+R"))
        action("Kombinieren", "combine", self._create_combine, "Zwei Körper vereinigen, ausschneiden oder schneiden")
        # neu angelegt: drehen und skalieren um die Körpermitte (ältere Schritte behalten den Ursprung)
        action("Verschieben", "move", lambda: self._create(solids.MoveBody(pivot=solids.MOVE_PIVOTS[0])),
               "Körper verschieben, drehen und skalieren")
        action("Farbe", "appearance", lambda: self._create(solids.Appearance()),
               "Erscheinungsbild: gewählten Körper (oder alle) einfärben")
        action("Parameter", "parameters", self._edit_parameters,
               "Parameter ändern: benannte Werte und Ausdrücke, die in den Werkzeugen verwendet werden")

        group("FLÄCHE")
        action("Fläche extrudieren", "surface_extrude", lambda: self._create_from_sketch(sf.SurfaceExtrude),
               "Kurven der letzten Skizze (auch offene Linienzüge) zu Flächen ziehen – ohne Volumen")
        action("Fläche drehen", "surface_revolve", lambda: self._create_from_sketch(sf.SurfaceRevolve),
               "Kurven der letzten Skizze um eine Achse zu einer Fläche drehen")
        action("Flicken", "patch", lambda: self._create_from_sketch(sf.Patch),
               "Geschlossene Skizze (auch eine räumliche aus der 3D-Skizze) mit einer Fläche füllen")
        action("Fläche versetzen", "surface_offset", lambda: self._create(sf.OffsetSurface()),
               "Flächenkörper parallel im Abstand verdoppeln (Flächenkörper anklicken)")
        action("Fläche sweepen", "surface_sweep", lambda: self._create_two_sketches(sf.SurfaceSweep, "path"),
               "Vorletzte Skizze (Profil, auch offen) entlang der letzten Skizze (Pfad) zu einer Fläche ziehen")
        action("Fläche erheben", "surface_loft", lambda: self._create_two_sketches(sf.SurfaceLoft, "sketch2"),
               "Die Kurvenzüge der beiden letzten Skizzen zu einer Fläche verbinden")
        action("Begrenzungsfüllung", "boundary_fill", lambda: self._create(sf.BoundaryFill()),
               "Flächen- und Volumenkörper anklicken, die zusammen Zellen einschließen: daraus wird ein Volumenkörper")
        action("Fläche stutzen", "surface_trim", lambda: self._create(sf.TrimSurface()),
               "Fläche anklicken, dann das Schnittwerkzeug; danach Stücke anklicken, die wegfallen sollen")
        action("Fläche verlängern", "surface_extend", lambda: self._create(sf.ExtendSurface()),
               "Flächenkörper an seinen Rändern weiterführen (anklicken)")
        action("Trennen", "unstitch", lambda: self._create(sf.Unstitch()),
               "Flächen- oder Volumenkörper in seine einzelnen Flächen zerlegen (anklicken)")
        action("Regelfläche", "ruled_surface", lambda: self._create_two_sketches(ex.RuledSurface, "sketch2"),
               "Die Kurvenzüge der beiden letzten Skizzen mit geraden Linien zu einer Fläche verbinden")
        action("Normale umkehren", "reverse_normal", lambda: self._create(ex.ReverseNormal()),
               "Vorder- und Rückseite eines Flächenkörpers tauschen (anklicken)")
        action("Aufdicken", "thicken", lambda: self._create(sf.Thicken()),
               "Aus einem Flächenkörper einen Volumenkörper bestimmter Dicke machen (anklicken)")
        action("Vernähen", "stitch", lambda: self._create(sf.Stitch()),
               "Flächenkörper, die zusammen dicht sind, zu einem Volumenkörper verbinden (alle anklicken)")

        group("NETZ")
        action("Netz einfügen", "mesh_insert", self._insert_mesh,
               "STL-Datei (z. B. 3D-Scan oder Druckmodell) als Netzkörper einfügen – im Entwurf gespeichert")
        action("Körper in Netz umwandeln", "body_to_mesh", lambda: self._create(ms.BodyToMesh()),
               "Volumenkörper in ein Dreiecksnetz umwandeln (Körper anklicken)")
        action("Netz in Körper umwandeln", "mesh_to_body", lambda: self._create(ms.MeshToBody()),
               "Geschlossenes Netz in einen facettierten Volumenkörper umwandeln (Netz anklicken)")
        action("Netz verkleinern", "mesh_reduce", lambda: self._create(ms.MeshReduce()),
               "Weniger Dreiecke: Ecken auf ein Raster zusammenlegen (Netz anklicken)")
        action("Netz reparieren", "mesh_repair", lambda: self._create(ms.MeshRepair()),
               "Doppelte und entartete Dreiecke entfernen, Ausrichtung vereinheitlichen, Löcher schließen")
        action("Netz glätten", "mesh_smooth", lambda: self._create(ms.MeshSmooth()),
               "Netz glätten (Rauschen und Treppen eines Scans), ohne dass es schrumpft")
        action("Netz schneiden", "mesh_plane_cut", lambda: self._create(ex.MeshPlaneCut()),
               "Netz an einer Ebene abschneiden; der Schnitt wird auf Wunsch geschlossen")
        action("Netz trennen", "mesh_separate", lambda: self._create(ex.MeshSeparate()),
               "Netz in seine nicht zusammenhängenden Teile zerlegen")
        action("Netze kombinieren", "mesh_combine", lambda: self._create(ex.MeshCombine()),
               "Mehrere Netzkörper zu einem zusammenfassen (alle anklicken)")
        action("Netznormalen umkehren", "mesh_reverse", lambda: self._create(ex.MeshReverse()),
               "Innen und außen eines Netzes tauschen (anklicken)")
        action("Netz als STL speichern", "export", lambda: self._save_mesh(None),
               "Gewählten bzw. einzigen Netzkörper als STL-Datei speichern")

        group("BLECH")
        action("Flansch", "sheet_flange", self._create_base_flange,
               "Blechteil aus dem Profil einer Skizze: um die Blechdicke aufziehen (mit Biegeradius und K-Faktor)")
        action("Flansch an Kante", "sheet_edge_flange", lambda: self._create(sm.EdgeFlange()),
               "Gerade Kante eines Blechteils wählen: dort entsteht eine Biegung mit geradem Flansch")
        action("Konturflansch", "sheet_contour", lambda: self._create_from_sketch(mt.ContourFlange),
               "Offener Linienzug der letzten Skizze wird Blechprofil: Ecken werden gebogen, Breite senkrecht zur Skizze")
        action("Aufreißen", "sheet_rip", lambda: self._create(mt.Rip()),
               "Blechteil an einer äußeren Ecke mit einem Spalt auftrennen (z. B. Kasten zum Abwickeln)")
        action("Saum", "sheet_hem", lambda: self._create(ex.Hem()),
               "Kante eines Blechteils um 180° umlegen (Saum mit Spalt)")
        action("Abwicklung", "sheet_flat", lambda: self._create(sm.FlatPattern()),
               "Blechteil flach abwickeln (Zuschnitt mit Biegezugaben) – als Körper neben dem gefalteten Teil")
        action("In Blech umwandeln", "sheet_convert", lambda: self._create(sm.ConvertToSheet()),
               "Plattenförmigen Körper gleicher Dicke zum Blechteil machen (Körper anklicken)")
        action("Biegen", "sheet_bend", self._create_sketch_bend,
               "An einer Skizzenlinie auf dem Blech biegen: das kleinere Stück klappt hoch (Biegezone mittig auf der Linie)")
        action("Abwickeln", "sheet_unfold", lambda: self._create(sm.Unfold()),
               "Blechteil an Ort und Stelle flach legen – dann z. B. Löcher über Biegungen hinweg setzen")
        action("Zurückbiegen", "sheet_refold", lambda: self._create(sm.Refold()),
               "Abgewickeltes Blechteil wieder falten; Ausschnitte aus der flachen Lage gehen mit")
        action("Zuschnitt (DXF)", "sheet_dxf", lambda: self._export_dxf(None),
               "Zuschnitt des Blechteils als DXF speichern: Außenkontur und Biegelinien (z. B. für den Laser)")

        group("BAUGRUPPE")
        action("Komponente", "component", self._create_component,
               "Gewählte Körper zu einer Komponente (Bauteil) zusammenfassen")
        action("Gelenk", "joint", self._create_joint,
               "Zwei Flächen oder Kanten wählen: der erste Körper wird an den zweiten gefügt "
               "(starr, drehbar oder verschiebbar)")
        action("Gelenkursprung", "joint_origin", lambda: self._create(mt.JointOrigin()),
               "Bezug (Punkt + Richtung) an einer angeklickten Stelle eines Körpers; ein Gelenk kann ihn nutzen")
        action("Starre Gruppe", "rigid_group", lambda: self._create(mt.RigidGroup()),
               "Gewählte bzw. angeklickte Körper bewegen sich ab jetzt gemeinsam")
        self._motion_action = action("Bewegen", "motion", lambda: None,
                                     "Lässt Drehgelenke umlaufen und Schieber hin- und herfahren; Kollisionen "
                                     "werden dabei angezeigt. Erneut klicken hält an.")
        self._motion_action.setCheckable(True)
        self._motion_action.toggled.connect(self._toggle_motion)
        self._motion: dict | None = None
        action("Kollision", "collision", self._show_collisions,
               "Prüft, welche sichtbaren Körper sich durchdringen")

        group("PRÜFEN")
        self._measure = action("Messen", "measure", lambda: None,
                               "Zeigt Länge, Fläche, Radius der Auswahl; bei zwei Elementen Abstand und Winkel")
        self._measure.setCheckable(True)
        self._measure.toggled.connect(lambda _checked: self._show_selection())
        action("Schwerpunkt", "centre_of_mass", self._show_centre_of_mass,
               "Massenschwerpunkt der gewählten (sonst aller sichtbaren) Körper anzeigen")
        action("Leinwand", "canvas", self._insert_canvas,
               "Bild (PNG, JPG) als halbdurchsichtige Vorlage auf eine Ebene legen – zum Nachzeichnen")
        self._component_colour_action = action("Komponentenfarben", "component_colours", lambda: self._refresh(),
                                               "Jede Komponente in einer eigenen Farbe zeigen (ein/aus)")
        self._component_colour_action.setCheckable(True)
        # Kürzel Strg+C/Strg+V stehen an eigenen QShortcuts (unterscheiden Skizze und Modell), nicht hier
        action("Körper kopieren", "copy_bodies", lambda: self._copy_bodies(),
               "Gewählte Körper in die Zwischenablage kopieren (Strg+C) – auch für andere Entwürfe")
        action("Körper einfügen", "paste_bodies", self._paste_bodies,
               "Kopierte Körper einfügen (Strg+V), auch aus einem anderen Entwurf; danach an den Pfeilen verschieben")
        action("DXF/SVG einfügen", "insert_drawing", self._insert_drawing,
               "Zeichnung (DXF oder SVG) als neue Skizze auf einer Ebene einfügen")
        action("Eigenschaften", "properties", self._show_properties,
               "Volumen, Oberfläche, Schwerpunkt, Abmessungen und Masse der sichtbaren Körper")
        # Schnittanalyse: schneidet die Anzeige auf, das Modell selbst bleibt unverändert
        self._section = {"axis": "X", "offset": 0.0, "flip": False}
        # Prüfwerkzeuge: Entformung und Wandstärke als Einfärbung der Körper
        analysis = QToolButton()
        analysis.setIcon(icon("analysis"))
        analysis.setText("Analyse")
        analysis.setToolTip("Prüfen: Entformungsschräge oder Wandstärke farbig anzeigen")
        analysis.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        analysis.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        analysis_menu = QMenu(analysis)
        analysis_menu.addAction("Entformung …").triggered.connect(lambda: self._start_analysis("Entformung"))
        # Zugrichtung der Entformung (wie in Fusion wählbar)
        self._pull = "+Z"
        self._pull_face_vector = (0.0, 0.0, 1.0)       # Zugrichtung, wenn sie einer Fläche folgt
        pull_menu = analysis_menu.addMenu("Zugrichtung")
        for name in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
            entry = pull_menu.addAction(name)
            entry.setCheckable(True)
            entry.setChecked(name == self._pull)
            entry.triggered.connect(lambda _c=False, chosen=name: self._set_pull(chosen))
        # wie in Fusion: Zugrichtung senkrecht zu einer vorher angeklickten Fläche
        entry = pull_menu.addAction(_PULL_FACE)
        entry.setCheckable(True)
        entry.triggered.connect(lambda _c=False: self._set_pull_from_face())
        analysis_menu.addAction("Zebra …").triggered.connect(lambda: self._start_analysis("Zebra"))
        analysis_menu.addAction("Wandstärke …").triggered.connect(lambda: self._start_analysis("Wandstärke"))
        analysis_menu.addAction("Krümmung …").triggered.connect(lambda: self._start_analysis("Krümmung"))
        analysis_menu.addAction("Isokurven …").triggered.connect(self._toggle_isolines)
        analysis_menu.addAction("Analyse aus").triggered.connect(lambda: self._start_analysis(None))
        analysis.setMenu(analysis_menu)
        bar.addWidget(analysis)
        self._section_action = action("Schnitt", "section", lambda: None,
                                      "Schnittanalyse: Modell aufgeschnitten anzeigen; die Ebene am Pfeil ziehen, "
                                      "erneut klicken wechselt die Achse (X → Y → Z → aus)")
        self._section_action.setCheckable(True)
        self._section_action.triggered.connect(self._section_clicked)

        group("ANSICHT")
        action("Einpassen", "fit", lambda: self._refresh(fit=True), "Ansicht auf das Modell einpassen")
        # Bodenraster mit Ursprung ein- und ausblenden (die Wahl wird gemerkt)
        grid = action("Raster", "grid", lambda: None, "Bodenraster mit Ursprungsmarkierung anzeigen")
        grid.setCheckable(True)
        grid.setChecked(QSettings("WolfCAD", "WolfCAD").value("raster", "ja") != "nein")
        grid.toggled.connect(self._set_grid)
        self.viewport.set_ground_visible(grid.isChecked())
        self._navbar.grid_toggled.connect(grid.setChecked)
        grid.toggled.connect(lambda on: (self._navbar.buttons["grid"].blockSignals(True),
                                         self._navbar.buttons["grid"].setChecked(on),
                                         self._navbar.buttons["grid"].blockSignals(False)))
        # Design wählen: das des Betriebssystems oder das ursprüngliche WolfCAD-Aussehen
        design = QToolButton()
        design.setIcon(icon("theme"))
        design.setText("Design")
        design.setToolTip("Aussehen des Programms: „System“ folgt dem Betriebssystem, „Klassisch“ ist das alte Design")
        design.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        design_menu = QMenu(design)
        self._design_actions = {}
        for name in theme.NAMES:
            entry = design_menu.addAction(name)
            entry.setCheckable(True)
            entry.setChecked(name == theme.current().name)
            entry.triggered.connect(lambda _checked=False, chosen=name: self.set_design(chosen))
            self._design_actions[name] = entry
        design.setMenu(design_menu)
        top.addWidget(design)
        action("Einstellungen", "settings", self._open_settings, "Mausbelegung und Tastenkürzel ändern", where=top)
        views = QToolButton()
        views.setIcon(icon("view"))
        views.setText("Ansichten")
        views.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        views.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        popup = QMenu(views)
        for name, yaw, pitch in (("Vorne", 0, 0), ("Oben", 0, 90), ("Rechts", 90, 0), ("Isometrisch", 35, 25)):
            popup.addAction(name, lambda yaw=yaw, pitch=pitch: self.viewport.set_view(yaw, pitch))
        views.setMenu(popup)
        bar.addWidget(views)

        group("FUSION")
        action("Abspielen", "play", self._replay_history,
               "Schritte des Fusion-Verlaufs am Stück in die Timeline holen")
        self._show_helpers = action("Hilfskörper", "helper", lambda: None,
                                    "Körper anzeigen, die im Original am Ende nicht mehr vorhanden sind")
        self._show_helpers.setCheckable(True)
        self._show_helpers.toggled.connect(lambda _checked: self._refresh())
        self._build_sketch_tab(ribbon)

    def _build_sketch_tab(self, ribbon: Ribbon) -> None:
        """Zusammenhangs-Reiter SKIZZE wie in Fusion: erscheint beim Skizzieren mit Erstellen, Ändern,
        Abhängigkeiten, Auswählen und „Skizze fertig stellen“; die Werkzeuge schalten das Skizzenwerkzeug um."""
        ribbon.add_tab(SKETCH_TAB, contextual=True)
        # Die Aktionen des Reiters gehören einem eigenen Objekt außerhalb des Fensters: eine Suche nach
        # Werkzeugnamen im Fenster soll die gleichnamigen Modellwerkzeuge finden (z. B. „Abrunden“)
        self._sketch_action_owner = QObject()
        draw = [tool for tool in se.TOOLS if tool not in se.EDIT_TOOLS and tool not in cs_tools()
                and tool not in ("Auswählen", se.DIMENSION_TOOL)]
        layout = (("ERSTELLEN", [*draw, se.DIMENSION_TOOL],
                   {"Linie", "Rechteck", "Kreis", "Bogen", "Polygon", sketch.SPLINE, se.DIMENSION_TOOL}),
                  ("ÄNDERN", list(se.EDIT_TOOLS), {se.FILLET_TOOL, se.TRIM_TOOL, se.OFFSET_TOOL}),
                  ("ABHÄNGIGKEITEN", cs_tools(), set(cs_tools())),
                  ("AUSWÄHLEN", ["Auswählen"], {"Auswählen"}))
        self._sketch_tool_actions = {}
        for title, tools, featured in layout:
            panel = ribbon.add_panel(SKETCH_TAB, title)
            for tool in tools:
                item = QAction(icon(se._TOOL_ICONS.get(tool, "sketch")), tool, self._sketch_action_owner)
                item.setToolTip(tool)
                tool_hints.mark(item, tool_hints.SKETCH)    # Erklärung aus den Texten der Skizze
                item.triggered.connect(lambda _checked=False, name=tool: self._choose_sketch_tool(name))
                panel.add(item, tool in featured)
                self._sketch_tool_actions[tool] = item
            if title == "ÄNDERN":
                # Löschen (wie Entf): gewählte Skizzenelemente entfernen
                remove = QAction(icon("delete"), "Löschen", self._sketch_action_owner)
                remove.setToolTip("Gewählte Skizzenelemente löschen (Entf)")
                tool_hints.mark(remove, tool_hints.SKETCH)
                remove.triggered.connect(lambda: self._live and self._live.get("session")
                                         and self._live["session"].canvas.delete_selected())
                panel.add(remove, False)
        # grüner Haken wie Fusions „SKIZZE FERTIG STELLEN“
        finish = QToolButton()
        finish.setObjectName("ribbonFinish")
        finish.setIcon(icon("sketch_finish"))
        finish.setIconSize(QSize(30, 30))
        finish.setText("SKIZZE FERTIG STELLEN")
        finish.setToolTip("Skizze fertig stellen (Eingabe)")
        finish.setProperty("werkzeughilfe", tool_hints.SKETCH)
        finish.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        finish.setAutoRaise(True)
        finish.clicked.connect(lambda: self._finish_live(True))
        ribbon.add_widget(SKETCH_TAB, finish)

    def _choose_sketch_tool(self, tool: str) -> None:
        """Werkzeug aus dem Reiter SKIZZE: wählt es im laufenden Skizzenfeld."""
        if self._live is not None and self._live.get("session") is not None:
            self._live["form"].select_tool(tool)

    # --- Anzeige ----------------------------------------------------------

    def _active_sketch_names(self) -> list[str]:
        """Skizzen, die vor der Rücksetzmarke liegen und nicht unterdrückt sind."""
        return [f.name for f in self.timeline.features[:self.timeline.marker]
                if isinstance(f, _SKETCH_TYPES) and not f.suppressed]

    def _refresh(self, fit: bool = False) -> None:
        """Überträgt den berechneten Modellstand in Ansicht, Browser und Timeline."""
        state = self.timeline.state
        # Hilfskörper (im Original am Ende nicht mehr vorhanden) nur auf Wunsch zeigen
        hidden = set() if self._show_helpers.isChecked() else set(state.helpers)
        # verschwundene Körper vergessen (Flächen-, Netzkörper und Leinwände können ebenfalls ausgeblendet sein)
        self._hidden_bodies &= {*state.bodies, *state.surfaces, *state.meshes, *state.canvases}
        hidden |= self._hidden_bodies
        # Je Körper eine Zerlegung, die Dreiecke und Segmente ihren Flächen und Kanten zuordnet;
        # sie dient der Anzeige und der Auswahl per Mausklick und wird je Körper gemerkt.
        self._picks = {}
        for name, shape in {**state.bodies, **state.surfaces}.items():
            if name in hidden:
                continue
            key = id(shape)
            entry = self._pick_cache.pop(key, None) or (shape, mesh.pick_mesh(shape))
            self._pick_cache[key] = entry           # ans Ende: zuletzt benutzt
            self._picks[name] = entry[1]
        for name, triangles in state.meshes.items():
            if name in hidden:
                continue
            key = id(triangles)
            entry = self._pick_cache.pop(key, None) or (triangles, _mesh_pick(triangles))
            self._pick_cache[key] = entry
            self._picks[name] = entry[1]
        # Zerlegungen gerade nicht vorhandener Körper bis zu einer Obergrenze behalten: beim Hin- und Herrollen der
        # Timeline kommen dieselben Körper wieder (bei einer Fusion-Timeline sonst 15 s Neuaufbau je Klick).
        # Zuerst fallen die am längsten unbenutzten weg (der Eintrag hält seine Form fest, die id bleibt eindeutig).
        alive = {id(shape) for shape in [*state.bodies.values(), *state.surfaces.values(), *state.meshes.values()]}
        for key in list(self._pick_cache):
            if len(self._pick_cache) <= max(_PICK_CACHE_LIMIT, len(alive)):
                break
            if key not in alive:
                del self._pick_cache[key]
        # Ausdehnung des Modells aus den gemerkten Hüllquadern der Körper (je zwei Punkte) – nicht mehr aus allen
        # Dreiecken: das Zusammenkleben und Durchsuchen kostete bei großen Modellen bei jedem Neuzeichnen Sekunden
        extent = _stack([mesh.pick_bounds(pick) for pick in self._picks.values()])
        # Körper mit eigenem Erscheinungsbild werden nach Farbe getrennt gezeichnet
        by_color: dict[str, list] = {}
        component_colours = getattr(self, "_component_colour_action", None)
        components = list(dict.fromkeys(state.components.values()))
        replaced = self._physics.hidden_bodies()       # zeigt gerade ein Physik-Ergebnis statt des Körpers
        for name, pick in self._picks.items():
            if name in replaced:
                continue
            if component_colours is not None and component_colours.isChecked() and name in state.components:
                colour = _COMPONENT_PALETTE[components.index(state.components[name]) % len(_COMPONENT_PALETTE)]
                by_color.setdefault(colour, []).append(pick.triangles)
                continue
            painted = state.face_colors.get(name)
            if not painted:
                # Flächenkörper in eigener Farbe (wie in Fusion von Volumenkörpern zu unterscheiden)
                colour = (_SURFACE_COLOUR if name in state.surfaces else _MESH_COLOUR if name in state.meshes
                          else state.colors.get(name, ""))
                by_color.setdefault(colour, []).append(pick.triangles)
                continue
            # Einzelne Flächen tragen eine eigene Farbe: jede Fläche über ihren gemerkten Punkt zuordnen
            triangles = pick.triangles.reshape(-1, 3, 3)
            colours = [state.colors.get(name, "")] * len(pick.face_points)
            for point, colour in painted:
                distances = [math.dist(point, known) for known in pick.face_points]
                if distances and min(distances) <= ft.SELECTION_TOLERANCE:
                    colours[distances.index(min(distances))] = colour
            for colour in set(colours):
                wanted = np.isin(pick.triangle_face, [i for i, c in enumerate(colours) if c == colour])
                by_color.setdefault(colour, []).append(triangles[wanted].reshape(-1, 3))
        plain = _stack(by_color.pop("", []))
        groups = [(solids.colour_rgb(color), _stack(parts)) for color, parts in by_color.items() if solids.colour_rgb(color)]
        if self._analysis is not None:
            # Prüfmodus: alle Flächen nach der Analyse eingefärbt statt nach ihrer Farbe
            plain, groups = np.zeros((0, 3), dtype=np.float32), self._analysis_groups(state)
        groups = self._physics.decorate(groups)         # Physik-Ergebnis, Stromlinien, Wasser, Lager und Lasten
        self.viewport.set_face_groups(groups)
        edges = _stack([pick.segments for pick in self._picks.values()])
        # Nach einer Neuberechnung passen gemerkte Flächen- und Kantennummern nicht mehr
        self._selection = []
        self._selected_profiles = []        # ebenso die im Modell angeklickten Profile
        self.viewport.set_geometry("selected_profiles", np.zeros((0, 3), dtype=np.float32))
        self._show_selection()
        sketches = mesh.lines(_compound([*state.profiles.values(), *state.sketch_curves]))
        # Konstruktionslinien der Skizzen: auch im Modell zu sehen, in eigener Farbe
        self.viewport.set_geometry("construction_model", mesh.lines(_compound(state.construction_curves)))
        import_faces, import_edges = self._import_mesh(state.references)
        for name, data in (("faces", plain), ("edges", edges), ("sketches", sketches),
                           ("import_faces", import_faces), ("import_edges", import_edges)):
            self.viewport.set_geometry(name, data)
        self.viewport.set_geometry("planes", self._plane_outlines(state, extent))
        self._show_extras(state, extent)
        self.viewport.set_ground_extent(extent, sketches)      # das Rasterfeld wächst mit dem Modell
        if fit:
            self.viewport.fit(extent, sketches, import_edges)

        self._update_handle()
        awaiting = {self._live["index"]} if self._live is not None and self._live.get("session") is None \
            and 0 <= self._live["index"] < len(self.timeline.features) \
            and self._awaiting_selection(self.timeline.features[self._live["index"]]) else set()
        self.bar.refresh(self.timeline, awaiting)
        self._filling_browser = True
        self.browser.clear()
        grouped = {name for name in state.components if name in state.bodies}
        groups = {
            # nur die obersten Komponenten; Unterbaugruppen hängen darunter
            "Komponenten": [name for name in dict.fromkeys([*(state.components[b] for b in state.bodies if b in grouped),
                                                            *state.component_parents.values()])
                            if state.component_parents.get(name, ft.NO_BASE) in ("", ft.NO_BASE)],
            "Körper": [name for name in state.bodies if name not in state.helpers and name not in grouped],
            "Hilfskörper": [name for name in state.bodies if name in state.helpers],
            "Flächenkörper": list(state.surfaces),
            "Netzkörper": list(state.meshes),
            # Auch Skizzen ohne geschlossenes Profil gehören in die Liste
            "Skizzen": list(dict.fromkeys([*state.frames, *state.profiles])),
            "Ebenen": [*state.planes, *state.axes, *state.points],     # Konstruktionsebenen, -achsen, -punkte
            "Leinwände": list(state.canvases),          # Runde 84: Bilder ein-/ausblenden und entfernen
            "Importe": [f.name for f in self.timeline.features[:self.timeline.marker]
                        if isinstance(f, ft.ImportedReference) and not f.suppressed],
        }
        aliases = self.timeline.view_state.get("namen", {})
        body_groups = self._body_groups()
        grouped_bodies = {body for members in body_groups.values() for body in members}
        for title, names in groups.items():
            if title == "Komponenten" and not names:
                continue        # ohne Baugruppe bleibt der Browser, wie man ihn kennt
            group = QTreeWidgetItem(self.browser, [title])
            group.setData(0, _BROWSER_KIND, ("folder", title))
            parents = {}
            if title == "Körper":
                # eigene Gruppen (Rechtsklick „Neue Gruppe“) als Ordner unter „Körper“
                for group_name, members in body_groups.items():
                    alive = [body for body in members if body in state.bodies]
                    folder = QTreeWidgetItem(group, [group_name])
                    folder.setData(0, _BROWSER_KIND, ("group", group_name))
                    folder.setData(0, Qt.ItemDataRole.UserRole, alive)      # Häkchen schaltet alle Körper darin
                    folder.setFlags(folder.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    folder.setCheckState(0, Qt.CheckState.Unchecked if alive and all(
                        body in self._hidden_bodies for body in alive) else Qt.CheckState.Checked)
                    for body in alive:
                        parents[body] = folder
            for name in names:
                item = QTreeWidgetItem(parents.get(name, group), [aliases.get(name, name)])
                if title == "Komponenten":
                    item.setData(0, _BROWSER_KIND, ("component", name))
                    self._fill_component(item, name, state)
                elif title in ("Körper", "Hilfskörper", "Flächenkörper", "Netzkörper"):
                    item.setData(0, _BROWSER_KIND, ({"Flächenkörper": "surface", "Netzkörper": "mesh"}.get(title, "body"), name))
                    if title == "Netzkörper":
                        item.setToolTip(0, f"{len(state.meshes[name])} Dreiecke")
                    item.setData(0, Qt.ItemDataRole.UserRole, [name])     # der echte Name (Anzeige kann abweichen)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.CheckState.Unchecked if name in self._hidden_bodies
                                       else Qt.CheckState.Checked)
                    item.setToolTip(0, "Häkchen entfernen blendet den Körper aus – Rechtsklick für weitere Befehle")
                elif title == "Leinwände":
                    item.setData(0, _BROWSER_KIND, ("canvas", name))
                    item.setData(0, Qt.ItemDataRole.UserRole, [name])
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.CheckState.Unchecked if name in self._hidden_bodies
                                       else Qt.CheckState.Checked)
                    item.setToolTip(0, "Häkchen entfernen blendet das Bild aus – Rechtsklick: bearbeiten, entfernen")
                else:
                    number = next((i for i, f in enumerate(self.timeline.features) if f.name == name), None)
                    item.setData(0, _BROWSER_KIND, ("feature", number))
        self.browser.expandAll()
        self._filling_browser = False
        self._browser_overlay.place()

        name = self._path.name if self._path else "Unbenannt"
        self.setWindowTitle(f"WolfCAD – {name}")
        if self.timeline.errors:
            index, message = next(iter(self.timeline.errors.items()))
            self.statusBar().showMessage(f"{self.timeline.features[index].name}: {message}")
        else:
            self.statusBar().clearMessage()

    @staticmethod
    def _plane_outlines(state: ft.BuildState, faces: np.ndarray) -> np.ndarray:
        """Rahmen der Konstruktionsebenen als Linien; ihre Größe richtet sich nach dem Modell."""
        reach = 40.0
        if len(faces):
            reach = max(reach, float(np.max(faces.max(axis=0) - faces.min(axis=0))) * 0.6)
        lines = []
        for (x, y, z), (dx, dy, dz) in state.axes.values():
            # Konstruktionsachse: Strich in beide Richtungen, so lang wie ein Ebenenrahmen
            lines += [np.array([x - dx * reach, y - dy * reach, z - dz * reach]),
                      np.array([x + dx * reach, y + dy * reach, z + dz * reach])]
        for x, y, z in state.points.values():
            # Konstruktionspunkt: kleines räumliches Kreuz
            size = reach * 0.04
            lines += [np.array([x - size, y, z]), np.array([x + size, y, z]), np.array([x, y - size, z]),
                      np.array([x, y + size, z]), np.array([x, y, z - size]), np.array([x, y, z + size])]
        for axes in state.planes.values():
            origin = np.array([axes.Location().X(), axes.Location().Y(), axes.Location().Z()])
            x = np.array([axes.XDirection().X(), axes.XDirection().Y(), axes.XDirection().Z()]) * reach
            y = np.array([axes.YDirection().X(), axes.YDirection().Y(), axes.YDirection().Z()]) * reach
            corners = [origin - x - y, origin + x - y, origin + x + y, origin - x + y]
            for start, end in zip(corners, corners[1:] + corners[:1]):
                lines += [start, end]
            # kleines Kreuz in der Mitte: dort liegt der Ursprung der Ebene
            lines += [origin - x * 0.08, origin + x * 0.08, origin - y * 0.08, origin + y * 0.08]
        return np.array(lines, dtype=np.float32).reshape(-1, 3)

    def _fill_component(self, item: QTreeWidgetItem, name: str, state: ft.BuildState) -> None:
        """Trägt unter einer Komponente ihre Unterbaugruppen und ihre eigenen Körper ein.

        Das Häkchen der Komponente schaltet alles darunter gemeinsam.
        """
        members = state.component_bodies(name)
        item.setData(0, Qt.ItemDataRole.UserRole, members)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Unchecked if members and all(m in self._hidden_bodies for m in members)
                           else Qt.CheckState.Checked)
        for child_name, parent in state.component_parents.items():
            if parent == name:
                self._fill_component(QTreeWidgetItem(item, [child_name]), child_name, state)
        for member in members:
            if state.components.get(member) != name:
                continue        # gehört zu einer Unterbaugruppe und steht dort
            child = QTreeWidgetItem(item, [member])
            child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            child.setCheckState(0, Qt.CheckState.Unchecked if member in self._hidden_bodies
                                else Qt.CheckState.Checked)

    # --- Browser: Gruppen und Rechtsklick-Menü (wie in Fusion) -----------------------------

    def _body_groups(self) -> dict[str, list[str]]:
        """Eigene Gruppen von Körpern: Gruppenname -> Körper (gehört zum Entwurf, wird mit ihm gespeichert)."""
        return {name: list(members) for name, members in self.timeline.view_state.get("gruppen", {}).items()}

    def _set_body_groups(self, groups: dict[str, list[str]]) -> None:
        self.timeline.view_state["gruppen"] = groups
        self._refresh()

    def _set_hidden(self, hidden: set[str]) -> None:
        self._hidden_bodies = set(hidden)
        self.timeline.view_state["hidden"] = sorted(self._hidden_bodies)
        self._refresh()

    def _browser_menu(self, position) -> None:
        """Rechtsklick im Browser: Befehle je nach Eintrag (Körper, Gruppe, Ordner, Skizze/Ebene, Komponente)."""
        item = self.browser.itemAt(position)
        if item is None:
            return
        kind, key = item.data(0, _BROWSER_KIND) or (None, None)
        state = self.timeline.state
        menu = QMenu(self)
        if kind == "body":
            self._body_menu(menu, key)
        elif kind == "mesh":
            self._visibility_entries(menu, [key])
            menu.addSeparator()
            menu.addAction(icon("mesh_to_body"), "In Volumenkörper umwandeln", lambda: self._create(ms.MeshToBody(mesh=key)))
            menu.addAction(icon("mesh_reduce"), "Verkleinern …", lambda: self._create(ms.MeshReduce(mesh=key)))
            menu.addAction(icon("mesh_repair"), "Reparieren", lambda: self._create(ms.MeshRepair(mesh=key)))
            menu.addAction(icon("mesh_smooth"), "Glätten …", lambda: self._create(ms.MeshSmooth(mesh=key)))
            menu.addAction(icon("export"), "Als STL speichern …", lambda: self._save_mesh(key))
        elif kind == "surface":
            self._visibility_entries(menu, [key])
            menu.addSeparator()
            menu.addAction(icon("thicken"), "Aufdicken …", lambda: self._create(sf.Thicken(surface=key)))
            menu.addAction(icon("surface_offset"), "Versetzen …", lambda: self._create(sf.OffsetSurface(surface=key)))
        elif kind == "canvas":
            self._canvas_entries(menu, key)
        elif kind == "group":
            members = [body for body in self._body_groups().get(key, []) if body in state.bodies]
            self._visibility_entries(menu, members)
            menu.addSeparator()
            menu.addAction("Gruppe umbenennen …", lambda: self._rename_group(key))
            menu.addAction("Gruppe auflösen", lambda: self._set_body_groups(
                {name: members for name, members in self._body_groups().items() if name != key}))
        elif kind == "folder":
            members = item.data(0, Qt.ItemDataRole.UserRole) or []
            if key in ("Körper", "Hilfskörper"):
                members = [body for body in state.bodies if (body in state.helpers) == (key == "Hilfskörper")]
                self._visibility_entries(menu, members)
            if key == "Leinwände" and state.canvases:
                hidden = all(name in self._hidden_bodies for name in state.canvases)
                menu.addAction("Alle einblenden" if hidden else "Alle ausblenden",
                               lambda: self._set_hidden(self._hidden_bodies - set(state.canvases) if hidden
                                                        else self._hidden_bodies | set(state.canvases)))
            if key == "Körper":
                menu.addSeparator()
                menu.addAction("Neue Gruppe …", lambda: self._new_group([]))
            if self._isolated_before is not None:
                menu.addAction("Isolieren aufheben", self._unisolate)
        elif kind == "component":
            members = state.component_bodies(key)
            self._visibility_entries(menu, members)
            menu.addAction("Eigenschaften", lambda: self._properties_of(members))
        elif kind == "feature" and key is not None:
            # Skizzen, Ebenen, Achsen, Importe: dieselben Befehle wie an ihrem Eintrag in der Timeline
            self._show_feature_menu(key, self.browser.viewport().mapToGlobal(position))
            return
        if not menu.isEmpty():
            menu.exec(self.browser.viewport().mapToGlobal(position))

    def _canvas_entries(self, menu: QMenu, name: str) -> None:
        """Befehle an einer Leinwand (Browser und Rechtsklick auf das Bild): ein-/ausblenden, bearbeiten, entfernen."""
        for label, handler in self._canvas_actions(name):
            menu.addAction(icon({"Bearbeiten …": "canvas", "Entfernen": "delete"}.get(label, "")), label, handler)

    def _canvas_actions(self, name: str) -> list[tuple[str, object]]:
        """(Beschriftung, Befehl) für eine Leinwand. Entfernen nimmt ihren Schritt aus der Timeline (Rückgängig geht)."""
        index = next((i for i, f in enumerate(self.timeline.features) if f.name == name), None)
        hidden = name in self._hidden_bodies
        actions = [("Einblenden" if hidden else "Ausblenden",
                    lambda: self._set_hidden(self._hidden_bodies - {name} if hidden else self._hidden_bodies | {name}))]
        if index is not None:
            actions += [("Bearbeiten …", lambda: self._edit(index)),
                        ("Entfernen", lambda: self._apply(self.timeline.remove, index))]
        return actions

    def _canvas_at(self, x: float, y: float) -> str | None:
        """Die sichtbare Leinwand unter dem Fensterpunkt, wenn kein Körper davor liegt (sonst None)."""
        origin, direction, _angle = self.viewport.pick_ray(x, y)
        best = None
        for name, item in self.timeline.state.canvases.items():
            if name in self._hidden_bodies:
                continue
            corners = np.array(item["corners"], dtype=float)
            for triangle in (corners[[0, 1, 2]], corners[[0, 2, 3]]):
                hit = picking.ray_triangles(origin, direction, triangle.reshape(1, 3, 3))
                if hit is not None and (best is None or hit[1] < best[1]):
                    best = (name, hit[1])
        if best is None:
            return None
        body = self._hit_at(x, y)
        if body is not None and self._last_hit_point is not None:
            # die Leinwand liegt meist auf einer Fläche: nur ein deutlich näherer Körper verdeckt sie
            if math.dist(origin, self._last_hit_point) < best[1] - 0.01 * float(self.viewport._distance):
                return None
        return best[0]

    def _visibility_entries(self, menu: QMenu, members: list[str]) -> None:
        """Ein-/Ausblenden und Isolieren für eine Menge von Körpern."""
        if not members:
            return
        hidden = all(body in self._hidden_bodies for body in members)
        menu.addAction("Einblenden" if hidden else "Ausblenden",
                       lambda: self._set_hidden(self._hidden_bodies - set(members) if hidden
                                                else self._hidden_bodies | set(members)))
        menu.addAction("Isolieren", lambda: self._isolate(members))
        if self._isolated_before is not None:
            menu.addAction("Isolieren aufheben", self._unisolate)

    def _body_menu(self, menu: QMenu, body: str) -> None:
        """Befehle an einem Körper wie im Fusion-Browser."""
        menu.addAction(icon("move"), "Verschieben/Kopieren",
                       lambda: self._create(solids.MoveBody(body=body, pivot=solids.MOVE_PIVOTS[0])))
        # Kopieren nimmt die ganze Auswahl mit, wenn der Körper dazugehört – sonst nur diesen Körper
        menu.addAction(icon("copy_bodies"), "Kopieren", lambda: self._copy_bodies(
            None if any(chosen == body for _kind, chosen, _index in self._selection) else [body]))
        menu.addAction(icon("appearance"), "Erscheinungsbild …", lambda: self._create(solids.Appearance(body=body)))
        menu.addAction(icon("properties"), "Eigenschaften", lambda: self._properties_of([body]))
        materials = menu.addMenu("Physisches Material")
        assigned = self.timeline.view_state.get("material", {}).get(body)
        for name in _DENSITIES:
            entry = materials.addAction(name, lambda chosen=name: self._set_material(body, chosen))
            entry.setCheckable(True)
            entry.setChecked(name == assigned)
        menu.addAction("In Timeline suchen", lambda: self._find_in_timeline(body))
        menu.addSeparator()
        self._visibility_entries(menu, [body])
        menu.addSeparator()
        groups = self._body_groups()
        into = menu.addMenu("In Gruppe verschieben")
        for name in groups:
            into.addAction(name, lambda target=name: self._move_to_group(body, target))
        into.addSeparator()
        into.addAction("Neue Gruppe …", lambda: self._new_group([body]))
        if any(body in members for members in groups.values()):
            menu.addAction("Aus der Gruppe nehmen", lambda: self._move_to_group(body, None))
        menu.addAction("Umbenennen …", lambda: self._rename_body(body))
        menu.addSeparator()
        menu.addAction(icon("component"), "Neue Komponente aus Körper",
                       lambda: self._create(solids.Component(body=body)))
        menu.addAction(icon("export"), "Als Netz speichern (STL) …", lambda: self._export_only(body, "stl"))
        menu.addAction(icon("export"), "Als STEP speichern …", lambda: self._export_only(body, "step"))
        menu.addAction(icon("print3d"), "3D-Druck …", lambda: self.open_print_workspace(bodies=[body]))
        menu.addAction(icon("print3d"), "In Creality Print öffnen", lambda: self._open_in_creality(body))
        if body in self.timeline.state.sheets:
            menu.addAction(icon("sheet_dxf"), "Zuschnitt als DXF speichern …", lambda: self._export_dxf(body))
        menu.addSeparator()
        menu.addAction(icon("delete"), "Entfernen", lambda: self._remove_body(body))

    def _set_material(self, body: str, material: str) -> None:
        """Werkstoff eines Körpers (Fusion: Physisches Material) – gilt für Masse in den Eigenschaften."""
        materials = dict(self.timeline.view_state.get("material", {}))
        materials[body] = material
        self.timeline.view_state["material"] = materials
        self.statusBar().showMessage(f"{body}: {material}", 5000)

    def _find_in_timeline(self, body: str) -> None:
        """Hebt den Schritt hervor, der den Körper angelegt hat (sonst den letzten, der ihn verändert hat)."""
        created = changed = None
        for number, feature in enumerate(self.timeline.features[:self.timeline.marker]):
            entry = self.timeline.state.history.get(feature.name)
            if entry is None:
                continue
            before, after, _feature = entry
            if body in after and body not in before and created is None:
                created = number
            if body in after and (body not in before or not after[body].IsSame(before[body])):
                changed = number
        target = created if created is not None else changed
        if target is None:
            self.statusBar().showMessage(f"Für {body} wurde kein Schritt gefunden", 5000)
            return
        self.bar.highlight(target)
        self.statusBar().showMessage(f"{body} entsteht in „{self.timeline.features[target].name}“", 6000)

    def _isolate(self, members: list[str]) -> None:
        """Nur diese Körper zeigen (Fusion: Isolieren); „Isolieren aufheben“ stellt die Sichtbarkeit wieder her."""
        if self._isolated_before is None:
            self._isolated_before = set(self._hidden_bodies)
        self._set_hidden({body for body in self.timeline.state.bodies if body not in members})

    def _unisolate(self) -> None:
        before, self._isolated_before = self._isolated_before, None
        self._set_hidden(before or set())

    def _new_group(self, bodies: list[str]) -> None:
        groups = self._body_groups()
        suggestion = next(f"Gruppe{n}" for n in range(1, 1000) if f"Gruppe{n}" not in groups)
        name, ok = QInputDialog.getText(self, "Neue Gruppe", "Name der Gruppe:", text=suggestion)
        name = name.strip()
        if not ok or not name:
            return
        for members in groups.values():         # ein Körper steht in höchstens einer Gruppe
            members[:] = [body for body in members if body not in bodies]
        groups[name] = [*groups.get(name, []), *bodies]
        self._set_body_groups(groups)

    def _move_to_group(self, body: str, target: str | None) -> None:
        groups = self._body_groups()
        for members in groups.values():
            members[:] = [other for other in members if other != body]
        if target is not None:
            groups.setdefault(target, []).append(body)
        self._set_body_groups(groups)

    def _rename_group(self, old: str) -> None:
        name, ok = QInputDialog.getText(self, "Gruppe umbenennen", "Neuer Name:", text=old)
        name = name.strip()
        if ok and name and name != old:
            self._set_body_groups({(name if key == old else key): members
                                   for key, members in self._body_groups().items()})

    def _rename_body(self, body: str) -> None:
        """Anzeigename eines Körpers im Browser (der interne Name in den Werkzeugen bleibt)."""
        aliases = dict(self.timeline.view_state.get("namen", {}))
        name, ok = QInputDialog.getText(self, "Umbenennen", "Neuer Name:", text=aliases.get(body, body))
        if not ok:
            return
        name = name.strip()
        if name and name != body:
            aliases[body] = name
        else:
            aliases.pop(body, None)
        self.timeline.view_state["namen"] = aliases
        self._refresh()

    def _properties_of(self, bodies: list[str]) -> None:
        """Eigenschaften nur dieser Körper (die übrigen gelten dafür kurz als ausgeblendet)."""
        kept = set(self._hidden_bodies)
        self._hidden_bodies = {body for body in self.timeline.state.bodies if body not in bodies}
        try:
            self._show_properties()
        finally:
            self._hidden_bodies = kept

    def _export_only(self, body: str, kind: str) -> None:
        """Einen einzelnen Körper als STL-Netz oder STEP speichern."""
        suggestion = f"{self.timeline.view_state.get('namen', {}).get(body, body)}.{kind}"
        pattern = "STL-Netz (*.stl)" if kind == "stl" else "STEP (*.step *.stp)"
        path, _ = QFileDialog.getSaveFileName(self, "Körper speichern", suggestion, pattern)
        if not path:
            return
        if Path(path).suffix.lower() not in exchange.EXPORT_FORMATS:
            path += "." + kind
        kept = set(self._hidden_bodies)
        self._hidden_bodies = {other for other in self.timeline.state.bodies if other != body}
        try:
            count = self.export_bodies(path)
        except exchange.ExchangeError as error:
            QMessageBox.critical(self, "Speichern", str(error))
            return
        finally:
            self._hidden_bodies = kept
        self.statusBar().showMessage(f"{count} Körper nach {path} gespeichert", 8000)

    def _export_dxf(self, body: str | None) -> None:
        """Zuschnitt eines Blechteils als DXF; ohne Angabe das gewählte bzw. das einzige Blechteil."""
        self._finish_live(True)
        sheets = self.timeline.state.sheets
        if body is None:
            chosen = [name for _kind, name, _index in self._selection if name in sheets]
            body = chosen[0] if chosen else (next(iter(sheets)) if len(sheets) == 1 else None)
        if body is None:
            QMessageBox.information(self, "Zuschnitt (DXF)", "Bitte ein Blechteil wählen (im Modell oder Browser)."
                                    if sheets else "Es gibt noch kein Blechteil (erst „Flansch“ aus einer Skizze).")
            return
        name = self.timeline.view_state.get("namen", {}).get(body, body)
        folder = self.data_panel.folder
        suggestion = str(folder / f"{name}.dxf") if folder else f"{name}.dxf"
        path, _ = QFileDialog.getSaveFileName(self, "Zuschnitt als DXF speichern", suggestion, "DXF (*.dxf)")
        if not path:
            return
        if not path.lower().endswith(".dxf"):
            path += ".dxf"
        try:
            counts = sm.write_dxf(sheets[body], path, self.timeline.state.bodies.get(body))
        except (OSError, ft.FeatureError) as error:
            QMessageBox.critical(self, "Zuschnitt (DXF)", str(error))
            return
        self.statusBar().showMessage(f"Zuschnitt gespeichert: {counts['KONTUR']} Konturkanten, "
                                     f"{counts['BIEGELINIEN']} Biegelinien – {path}", 8000)

    def _remove_body(self, body: str) -> None:
        """Körper entfernen: wie Fusion als eigener Schritt in der Timeline (lässt sich rückgängig machen)."""
        self._finish_live(True)
        self._remember()
        removal = solids.RemoveBody(body=body)
        removal.name = self.timeline.unique_name("Entfernen")
        self.timeline.add(removal)          # an der Rücksetzmarke, wie jedes Werkzeug
        self._refresh()

    def _remove_sketch(self, name: str) -> None:
        """Skizze entfernen: eigener Schritt in der Timeline – ab dort ist sie weg, alles davor bleibt (wie Fusion)."""
        self._finish_live(True)
        self._remember()
        removal = solids.RemoveSketch(sketch=name)
        removal.name = self.timeline.unique_name("Entfernen")
        self.timeline.add(removal)
        self._highlighted_sketch = None
        self._refresh()

    def _browser_clicked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        """Klick im Browser: der Körper bzw. die Skizze wird in der 3D-Ansicht markiert (wie Fusion)."""
        kind, key = item.data(0, _BROWSER_KIND) or (None, None)
        state = self.timeline.state
        self._highlighted_sketch = None
        if kind == "body" and self._live is not None and self._live.get("session") is None \
                and isinstance(self.timeline.features[self._live["index"]], solids.CombineBodies) \
                and key in self.timeline.state_before(self._live["index"]).bodies:
            # Kombinieren: der Name im Browser geht ins aktive Feld (auch für Körper, die im Ziel verborgen sind)
            self._combine_choose(self._live["index"], self.timeline.features[self._live["index"]], key)
            return
        if kind in ("body", "surface", "mesh") and key in self._picks:
            # ganzer Körper: alle seine Flächen gewählt (Entf entfernt ihn dann)
            self._selection = [("face", key, number) for number in range(len(self._picks[key].face_points))]
        elif kind == "feature" and key is not None and key < len(self.timeline.features):
            name = self.timeline.features[key].name
            if name in state.frames or name in state.profiles or name in state.curves:
                self._highlighted_sketch = name
            self._selection = []
        else:
            return
        self._show_selection()

    def _sketch_highlight(self) -> tuple[list, list]:
        """Linien und Profilflächen der im Browser angeklickten Skizze (zum Hervorheben)."""
        name = getattr(self, "_highlighted_sketch", None)
        state = self.timeline.state
        if not name:
            return [], []
        shapes = [shape for shape in (state.curves.get(name), state.profiles.get(name)) if shape is not None]
        if not shapes:
            self._highlighted_sketch = None
            return [], []
        lines = [mesh.lines(shape) for shape in shapes]
        faces = [mesh.triangles(state.profiles[name])] if name in state.profiles else []
        return faces, lines

    def _browser_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        """Ein Häkchen im Browser wurde umgeschaltet: Körper ein- oder ausblenden."""
        if self._filling_browser or item.parent() is None:
            return
        # Eine Komponente schaltet alle ihre Körper, sonst gilt das Häkchen dem einen Körper
        names = item.data(0, Qt.ItemDataRole.UserRole) or [item.text(0)]
        for name in names:
            if item.checkState(0) == Qt.CheckState.Checked:
                self._hidden_bodies.discard(name)
            else:
                self._hidden_bodies.add(name)
        # Die Sichtbarkeit gehört zum Entwurf und wird mit ihm gespeichert
        self.timeline.view_state["hidden"] = sorted(self._hidden_bodies)
        # Erst nach diesem Ereignis neu aufbauen: der Browser wird dabei geleert, und der
        # angeklickte Eintrag darf nicht verschwinden, solange der Klick noch verarbeitet wird
        QTimer.singleShot(0, self._refresh)

    # --- Features anlegen und bearbeiten ----------------------------------

    # --- Auswahl in der 3D-Ansicht ------------------------------------------

    def _on_click(self, x: float, y: float, additive: bool) -> None:
        """Wählt die Kante oder Fläche unter dem Mauszeiger; Strg/Umschalt erweitert die Auswahl."""
        self._highlighted_sketch = None         # eine im Browser markierte Skizze gilt nicht mehr
        if self._plane_pick is not None:
            choice = self._plane_at(x, y)
            if choice is not None:
                callback = self._plane_pick["callback"]
                self._cancel_plane_pick()
                callback(choice)
            return
        if self._profile_pick is not None:
            self._profile_clicked(x, y, additive)
            return
        hit = self._hit_at(x, y, centres=self._measure.isChecked())     # beim Messen auch Kreismitten
        if self._pick_target_face(hit):
            return
        if self._live_extrude_click(x, y, hit):
            return
        if self._live_pick(hit):
            return
        if self._pending_presspull:
            # Drücken/Ziehen wartet auf seine Fläche bzw. sein Profil
            if hit is not None and hit[0] == "face":
                self._pending_presspull = False
                self.viewport.show_info("")
                self._selection = [hit]
                self._show_selection()
                self._create(solids.PressPull())
            elif self._select_profile(x, y, False, blocked=hit is not None):
                self._pending_presspull = False
                self.viewport.show_info("")
                self._create_extrude()
            return
        if "sketch" in self._select_filter and self._select_profile(x, y, additive, blocked=hit is not None):
            return
        if hit is not None and hit[0] == "face" and "body" in self._select_filter and "face" not in self._select_filter \
                and hit[1] in self._picks:
            # Auswahlfilter nur „Körper“: alle Flächen des getroffenen Körpers (der Körper als Ganzes)
            whole = [("face", hit[1], number) for number in range(len(self._picks[hit[1]].face_points))]
            if additive and all(item in self._selection for item in whole):
                self._selection = [item for item in self._selection if item not in whole]
            else:
                self._selection = ([item for item in self._selection if item not in whole] if additive else []) + whole
            self._show_selection()
            return
        if not additive:
            self._selection = [hit] if hit else []
        elif hit in self._selection:
            self._selection.remove(hit)
        elif hit:
            self._selection.append(hit)
        self._show_selection()

    def _edges_at(self, x: float, y: float) -> list:
        """Die Körperkanten unter dem Fensterpunkt: die getroffene Kante oder alle Kanten der getroffenen Fläche."""
        hit = self._hit_at(x, y)
        if hit is None or hit[1] not in self.timeline.state.bodies:
            return []
        kind, body, index = hit
        shape = self.timeline.state.bodies[body]
        if kind == "edge":
            edges = ft.unique_edges(shape)
            return [edges[index]] if index < len(edges) else []
        faces = ft.faces_of(shape)
        return ft.unique_edges(faces[index]) if index < len(faces) else []

    def _face_at(self, x: float, y: float) -> tuple[str, int] | None:
        """Die vorderste Körperfläche unter dem Fensterpunkt als (Körper, Nummer) – ohne den Vorrang von Kanten und
        Ecken, den `_hit_at` für die Auswahl hat. Werkzeuge, die auf einer Fläche ansetzen (Aufziehen), sahen sonst
        nahe einer Kante die Kante und fielen auf das Bodenraster zurück (Nutzervideo 2026-10-22)."""
        origin, direction, _pixel_angle = self.viewport.pick_ray(x, y)
        best = None
        for name, pick in self._picks.items():
            face = picking.ray_triangles(origin, direction, pick.triangles.reshape(-1, 3, 3))
            if face and (best is None or face[1] < best[0]):
                best = (face[1], name, int(pick.triangle_face[face[0]]))
        if best is None:
            return None
        self._last_hit_point = tuple(float(c) for c in np.asarray(origin) + best[0] * np.asarray(direction))
        return best[1], best[2]

    def show_hover_face(self, face: tuple[str, int] | None) -> None:
        """Hellt die Fläche (Körper, Nummer) auf, auf der ein Werkzeug gerade ansetzen würde; None löscht."""
        triangles = np.zeros((0, 3), dtype=np.float32)
        if face is not None and face[0] in self._picks:
            pick = self._picks[face[0]]
            triangles = pick.triangles.reshape(-1, 3, 3)[pick.triangle_face == face[1]].reshape(-1, 3)
        self.viewport._layers["hover_faces"].alpha = 0.55       # deutlich hell wie in Fusion, Kanten bleiben sichtbar
        self.viewport.set_geometry("hover_faces", np.asarray(triangles, dtype=np.float32))

    def _hit_at(self, x: float, y: float, centres: bool = False) -> tuple[str, str, int] | None:
        """Kante oder Fläche unter dem Fensterpunkt als (Art, Körper, Nummer); None, wenn dort nichts ist.

        Mit `centres` zählen wie in Fusion auch die Mitten von Kreis- und Bogenkanten als Punkte – nur wo ein Punkt
        gesucht wird (Messen, 3D-Skizze); sonst bliebe die Mitte einer Zylinderoberseite als Fläche unwählbar.

        Der getroffene Raumpunkt steht danach in `self._last_hit_point` (für Werkzeuge, die ihre Auswahl
        im Modell vor ihrer eigenen Wirkung suchen).
        """
        origin, direction, pixel_angle = self.viewport.pick_ray(x, y)
        self._last_hit_point = None
        self._last_ray = (tuple(float(c) for c in origin), tuple(float(c) for c in direction))
        self._last_pixel_angle = float(pixel_angle)
        best_face = best_edge = None      # jeweils (Entfernung, Körper, Nummer)
        for name, pick in self._picks.items():
            face = picking.ray_triangles(origin, direction, pick.triangles.reshape(-1, 3, 3))
            if face and (best_face is None or face[1] < best_face[0]):
                best_face = (face[1], name, int(pick.triangle_face[face[0]]))
            edge = picking.ray_segments(origin, direction, pick.segments.reshape(-1, 2, 3),
                                        _EDGE_PICK_PIXELS * pixel_angle)
            if edge and (best_edge is None or edge[1] < best_edge[0]):
                best_edge = (edge[1], name, int(pick.segment_edge[edge[0]]))

        # Eckpunkte gehen vor: sie liegen genau auf Kanten und wären sonst nie zu treffen
        best_vertex = None
        for name in self._picks:
            points = self._vertex_points(name)
            if not centres:
                points = points[:len(points) - int(np.count_nonzero(self._vertex_depth.get(name, [])))]
            if not len(points):
                continue
            offsets = points - origin
            along = offsets @ direction
            away = np.linalg.norm(offsets - np.outer(along, direction), axis=1)
            allowed = np.where(along > 0, _VERTEX_PICK_PIXELS * pixel_angle * along, -1.0)
            # verdeckte Punkte zählen nicht – außer Kreismitten bis zu einem Radius hinter der Fläche (Rundungen)
            hidden = self._vertex_depth.get(name, np.zeros(len(points)))
            for index in np.nonzero(away <= allowed)[0]:
                if best_face is not None and along[index] > best_face[0] * (
                        1 + 4 * _VERTEX_PICK_PIXELS * pixel_angle) + hidden[index] + 1e-6:
                    continue
                if best_vertex is None or along[index] < best_vertex[0]:
                    best_vertex = (float(along[index]), name, int(index))
        along = lambda distance: tuple(float(c) for c in origin + direction * distance)
        allowed = getattr(self, "_select_filter", None)
        allowed = set(SELECTION_FILTERS) if allowed is None else allowed
        if best_vertex and "vertex" not in allowed:
            best_vertex = None          # Auswahlfilter: Eckpunkte gesperrt
        if best_edge and "edge" not in allowed:
            best_edge = None
        if best_face and "face" not in allowed and "body" not in allowed:
            best_face = None            # ohne Flächen und Körper bleibt eine Fläche unwählbar
        if best_vertex:
            self._last_hit_point = along(best_vertex[0])
            return ("vertex", best_vertex[1], best_vertex[2])
        hit = None
        if best_edge and (best_face is None
                          or best_edge[0] <= best_face[0] * (1 + 2 * _EDGE_PICK_PIXELS * pixel_angle)):
            # Eine Kante gewinnt, solange sie nicht deutlich hinter der getroffenen Fläche liegt
            hit = ("edge", best_edge[1], best_edge[2])
            self._last_hit_point = along(best_edge[0])
        elif best_face:
            hit = ("face", best_face[1], best_face[2])
            self._last_hit_point = along(best_face[0])
        return hit

    def _vertex_at(self, x: float, y: float):
        """Die Körperecke (oder Kreismitte) unter dem Fensterpunkt als Raumpunkt (für die 3D-Skizze); sonst None."""
        hit = self._hit_at(x, y, centres=True)
        if hit is None or hit[0] != "vertex":
            return None
        points = self._vertex_points(hit[1])
        return tuple(float(c) for c in points[hit[2]]) if hit[2] < len(points) else None

    def _vertex_points(self, body: str) -> np.ndarray:
        """Die fangbaren Punkte eines Körpers als Feld (n × 3): erst seine Eckpunkte, dahinter (wie in Fusion) die
        Mittelpunkte seiner Kreis- und Bogenkanten – Zylinder, Bohrungen, Rundungen. Je Form nur einmal bestimmt."""
        shape = self.timeline.state.bodies.get(body)
        if shape is None:
            return np.zeros((0, 3))
        key = id(shape)
        if self._vertex_cache.get(body, (None, None))[0] != key:
            from OCP.BRep import BRep_Tool
            corners = [[p.X(), p.Y(), p.Z()] for p in (BRep_Tool.Pnt_s(v) for v in ft.unique_vertices(shape))]
            centres = ft.circle_centers(shape)
            points = np.array(corners + [list(centre) for centre, _radius, _axis in centres], dtype=float)
            # Wie weit ein Punkt hinter der getroffenen Fläche liegen darf: Ecken liegen auf ihr (0), die Mitte
            # einer Rundung liegt einen Radius dahinter
            depth = np.array([0.0] * len(corners) + [radius for _centre, radius, _axis in centres])
            self._vertex_cache[body] = (key, points.reshape(-1, 3))
            self._vertex_depth[body] = depth
        return self._vertex_cache[body][1]

    def _vertex_shape(self, body: str, index: int):
        """Der Eckpunkt `index` als Form (bei einem Kreismittelpunkt ein neuer Punkt an der Mitte)."""
        corners = ft.unique_vertices(self.timeline.state.bodies[body])
        if index < len(corners):
            return corners[index]
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
        from OCP.gp import gp_Pnt
        return BRepBuilderAPI_MakeVertex(gp_Pnt(*self._vertex_points(body)[index])).Vertex()

    def _clear_selection(self) -> None:
        self._selection = []
        self._selected_profiles = []
        self.viewport.set_geometry("selected_profiles", np.zeros((0, 3), dtype=np.float32))
        self._show_selection()

    def _selected_planar_faces(self) -> list:
        """Merkpunkte der gewählten ebenen Körperflächen (leer, wenn keine oder nicht nur ebene gewählt sind)."""
        found = []
        for kind, body, index in self._selection:
            if kind != "face" or body not in self.timeline.state.bodies:
                return []
            faces = ft.faces_of(self.timeline.state.bodies[body])
            if index >= len(faces) or sketch.face_frame(faces[index]) is None:
                return []
            found.append(list(ft.face_point(faces[index])))
        return found

    def _extrude_regions(self, feature: ft.Extrude, regions: list, before: ft.BuildState) -> list[int]:
        """Nummern der Profilbereiche (aus `regions`), die die Extrusion gerade aufzieht."""
        chosen = []

        def of_sketch(name: str, points: list) -> None:
            own = [number for number, region in enumerate(regions) if region["sketch"] == name]
            if points:
                chosen.extend(number for number in own
                              if any(math.dist(regions[number]["point"], point) < 1e-6 for point in points))
                return
            # ganzes Profil („Mit Löchern“): die Bereiche des Vollprofils, sonst alle Bereiche der Skizze
            if name in before.solid_profiles and name in before.frames:
                kept = [region["point"] for region in
                        sketch.regions_2d(before.solid_profiles[name], before.frames[name])]
                own = [number for number in own if any(math.dist(regions[number]["point"], point) < 1e-6
                                                       for point in kept)]
            chosen.extend(own)

        if feature.sketch:
            of_sketch(feature.sketch, feature.region_points if feature.regions == ft.PROFILE_MODES[2] else [])
        for name, points in feature.extra_profiles:
            of_sketch(name, points)
        return chosen

    def _live_extrude_click(self, x: float, y: float, hit) -> bool:
        """Wie in Fusion: solange die Extrusion offen ist, nimmt jeder Klick ein weiteres Skizzenprofil oder eine
        weitere ebene Körperfläche dazu – ein Klick auf schon Gewähltes nimmt es wieder heraus (ohne Strg).
        Profile und Körperflächen lassen sich mischen. True, wenn der Klick dafür verbraucht wurde."""
        if self._live is None or self._live.get("session") is not None:
            return False
        index = self._live["index"]
        feature = self.timeline.features[index]
        if not isinstance(feature, ft.Extrude) or feature.extent == ft.EXTENTS[3]:
            return False
        before = self.timeline.state_before(index)
        # Treffer auf den Körper, den diese Extrusion selbst gerade bildet, zählen nicht (er liegt über dem Profil)
        body_hit = hit if hit is not None and hit[1] in before.bodies else None
        changes: dict = {}
        sketches = list(dict.fromkeys([*([feature.sketch] if feature.sketch else []),
                                       *[name for name, _points in feature.extra_profiles],
                                       *self._active_sketch_names()]))
        regions = self._profile_regions(sketches)
        profile = self._profile_hit(x, y, regions)
        if profile is not None and body_hit is not None and self._last_hit_point is not None:
            # wie in Fusion: ein Skizzenprofil auf (oder vor) der Körperfläche geht der Fläche vor
            eye = np.array(getattr(self, "_last_ray", ((0, 0, 0),))[0])
            if math.dist(eye, self._last_hit_point) < profile[1] - 1e-3:
                profile = None              # der Körper liegt vor dem Profil
        if profile is not None:
            body_hit = None
        if body_hit is None:
            number = profile[0] if profile is not None else None
            if number is None:
                return False
            picked = self._extrude_regions(feature, regions, before)
            picked = [n for n in picked if n != number] if number in picked else [*picked, number]
            if not picked and not feature.face_points:
                return True                     # das letzte Profil bleibt gewählt
            if picked:
                built = self._extrude_of([regions[n] for n in picked])
                changes = {"sketch": built.sketch, "regions": built.regions, "region_points": built.region_points,
                           "extra_profiles": built.extra_profiles}
            else:
                changes = {"sketch": "", "regions": ft.PROFILE_MODES[0], "region_points": [], "extra_profiles": []}
        else:
            if body_hit[0] != "face":
                return False
            face = ft.faces_of(before.bodies[body_hit[1]])[body_hit[2]]
            target = self._extrude_target(feature, before, face)
            if target is not None:
                # wie Fusion: eine Fläche vor/hinter dem Profil bestimmt, bis wohin aufgezogen wird
                form = self._live["form"]
                for name, value in target.items():
                    editor = form._editors.get(name)
                    if isinstance(editor, QComboBox):
                        editor.blockSignals(True)
                        editor.setCurrentText(value)
                        editor.blockSignals(False)
                    elif isinstance(value, float):
                        form.set_value(name, value)
                    elif isinstance(value, list):
                        form.set_list(name, value)
                self.timeline.update(index, **target)
                self._refresh()
                return True
            if sketch.face_frame(face) is None:
                return False                    # nur ebene Flächen lassen sich aufziehen
            marker = list(ft.face_point(face))
            kept = [point for point in feature.face_points if math.dist(point, marker) > ft.SELECTION_TOLERANCE]
            faces = kept if len(kept) < len(feature.face_points) else [*feature.face_points, marker]
            if not faces and not feature.sketch:
                return True                     # die letzte Fläche bleibt gewählt
            changes = {"face_points": faces}
        form = self._live["form"]
        for name, value in changes.items():
            editor = form._editors.get(name)
            if isinstance(editor, QComboBox):
                editor.blockSignals(True)
                if editor.findText(value) < 0:
                    editor.addItem(value)
                editor.setCurrentText(value)
                editor.blockSignals(False)
            elif isinstance(value, list):
                form.set_list(name, value)
        self.timeline.update(index, **changes)
        self._refresh()
        return True

    def _live_edge_pick(self, index: int, feature, before: ft.BuildState, ray) -> bool:
        """Kantenwahl wie in Fusion für Abrunden, Fase, Flansch, Saum und Aufreißen: die Kante am Mauszeiger – auch
        eine verdeckte (durch den Körper hindurch), wenn keine sichtbare davorliegt. Bei Abrunden und Fase nimmt ein
        Klick auf eine Fläche alle ihre Kanten (erneut klicken nimmt sie wieder heraus)."""
        origin, direction = np.array(ray[0]), np.array(ray[1])
        tolerance = _EDGE_PICK_PIXELS * getattr(self, "_last_pixel_angle", 0.002)
        best = None                     # (Entfernung längs des Strahls, Körper, Kante)
        for name, shape in before.bodies.items():
            if name in self._hidden_bodies:
                continue
            key = id(shape)
            if key not in self._pick_cache:
                self._pick_cache[key] = (shape, mesh.pick_mesh(shape))
            pick = self._pick_cache[key][1]
            found = picking.ray_segments(origin, direction, pick.segments.reshape(-1, 2, 3), tolerance)
            if found and (best is None or found[1] < best[0]):
                edges = ft.unique_edges(shape)
                number = int(pick.segment_edge[found[0]])
                if number < len(edges):
                    best = (found[1], name, [edges[number]])
        if best is None and isinstance(feature, (ft.Fillet, ft.Chamfer)):
            faces = [(math.dist(ray[0], at), name, face) for name, shape in before.bodies.items()
                     if name not in self._hidden_bodies
                     for face, at in [ft.face_on_ray(shape, *ray) or (None, None)] if face is not None]
            if faces:
                _distance, name, face = min(faces, key=lambda item: item[0])
                best = (0.0, name, ft.unique_edges(face))       # die Fläche: alle ihre Kanten
        if best is None:
            return False
        _distance, body, edges = best
        markers = [list(ft.edge_point(edge)) for edge in edges]
        current = list(feature.edge_points) if feature.body == body else []
        known = lambda marker: any(math.dist(marker, other) <= ft.SELECTION_TOLERANCE for other in current)
        if all(known(marker) for marker in markers):
            chosen = [other for other in current
                      if not any(math.dist(marker, other) <= ft.SELECTION_TOLERANCE for marker in markers)]
        else:
            chosen = current + [marker for marker in markers if not known(marker)]
        form = self._live["form"]
        editor = form._editors.get("body")
        if isinstance(editor, QComboBox):
            editor.blockSignals(True)
            if editor.findText(body) < 0:
                editor.addItem(body)
            editor.setCurrentText(body)
            editor.blockSignals(False)
        form.set_list("edge_points", chosen)
        self.timeline.update(index, body=body, edge_points=chosen)
        self._refresh()
        return True

    def _pick_target_face(self, hit) -> bool:
        """Extrusion „Bis Fläche“ in Bearbeitung: ein Klick auf eine Körperfläche macht sie zur Zielfläche."""
        if self._live is None or self._live.get("session") is not None or hit is None or hit[0] != "face":
            return False
        feature = self.timeline.features[self._live["index"]]
        if not isinstance(feature, ft.Extrude) or feature.extent != ft.EXTENTS[3]:
            return False
        state = self.timeline.state_before(self._live["index"])
        if hit[1] not in state.bodies:
            return False
        faces = ft.faces_of(state.bodies[hit[1]])
        if hit[2] >= len(faces):
            return False
        points = [list(ft.face_point(faces[hit[2]]))]
        self.timeline.update(self._live["index"], target_points=points)
        self._live["form"].set_list("target_points", points)
        self._refresh()
        return True

    def _live_pick(self, hit) -> bool:
        """Wie in Fusion „erst Werkzeug, dann Auswahl“: bei offenem Werkzeug nimmt ein Klick ins Modell die Kante,
        Fläche oder den Körper in dieses Werkzeug auf (nochmal anklicken nimmt sie wieder heraus).

        Die Ansicht zeigt schon die Wirkung des Werkzeugs (z. B. die fertige Verrundung). Gesucht wird deshalb im
        Modell davor: die Kante bzw. Fläche, die dem angeklickten Raumpunkt am nächsten liegt.
        Gibt True zurück, wenn der Klick dem Werkzeug galt.
        """
        if self._live is None or self._live.get("session") is not None:
            return False
        point = getattr(self, "_last_hit_point", None)
        index = self._live["index"]
        feature = self.timeline.features[index]
        before = self.timeline.state_before(index)
        ray = getattr(self, "_last_ray", None)
        if isinstance(feature, (ft.Fillet, ft.Chamfer, sm.EdgeFlange, ex.Hem, mt.Rip)) and ray is not None:
            return self._live_edge_pick(index, feature, before, ray)
        if isinstance(feature, solids.CombineBodies):
            return self._combine_pick(index, feature, before, ray)
        if isinstance(feature, solids.SplitBody) and ray is not None:
            return self._split_pick(index, feature, before, ray)
        if isinstance(feature, mt.Canvas) and ray is not None:
            # Leinwand auf die angeklickte ebene Körperfläche legen
            found = [(math.dist(ray[0], at), face, at) for shape in before.bodies.values()
                     for face, at in [ft.face_on_ray(shape, *ray) or (None, None)] if face is not None]
            if not found:
                return False
            _distance, face, at = min(found, key=lambda item: item[0])
            self._place_canvas(index, feature, face, at)
            return True
        if isinstance(feature, solids.MirrorBody) and ray is not None:
            # Spiegelebene wie in Fusion: eine angeklickte ebene Körperfläche
            found = [(math.dist(ray[0], at), shape, face) for shape in before.bodies.values()
                     for face, at in [ft.face_on_ray(shape, *ray) or (None, None)] if face is not None]
            if found:
                _distance, _shape, face = min(found, key=lambda item: item[0])
                frame = sketch.face_frame(face)
                if frame is not None:
                    editor = self._live["form"]._editors.get("base")
                    if isinstance(editor, QComboBox):
                        editor.blockSignals(True)
                        editor.setCurrentText(ft.NO_BASE)
                        editor.blockSignals(False)
                    self.timeline.update(index, frame=frame, base=ft.NO_BASE, offset=0.0)
                    self._live["form"].set_value("offset", 0.0)
                    self._refresh()
                    return True
        if hit is None and ray is not None:
            # In der Vorschau nichts getroffen (z. B. durch die schon geöffnete Fläche einer Schale hindurch):
            # im Körper vor dem Werkzeug nachsehen
            found = [(math.dist(ray[0], at), name, at) for name, shape in before.bodies.items()
                     for _face, at in [ft.face_on_ray(shape, *ray) or (None, None)] if at is not None]
            if found:
                _distance, name, point = min(found)
                hit = ("face", name, -1)
        if hit is None:
            return False
        body = hit[1]
        if isinstance(feature, mt.RigidGroup) and body in before.bodies:
            # Starre Gruppe: angeklickte Körper sammeln, erneut klicken nimmt heraus
            chosen = [name for name in feature.bodies if name != body]
            bodies = chosen if len(chosen) < len(feature.bodies) else [*feature.bodies, body]
            self._live["form"].set_list("bodies", bodies)
            self.timeline.update(index, bodies=bodies)
            self._refresh()
            return True
        if isinstance(feature, ex.MeshCombine) and body in before.meshes:
            # Netze kombinieren: angeklickte Netze sammeln, erneut klicken nimmt heraus
            chosen = [name for name in feature.meshes if name != body]
            meshes = chosen if len(chosen) < len(feature.meshes) else [*feature.meshes, body]
            self._live["form"].set_list("meshes", meshes)
            self.timeline.update(index, meshes=meshes)
            self._refresh()
            return True
        if isinstance(feature, (ms.MeshToBody, ms.MeshReduce, ms.MeshRepair, ms.MeshSmooth, ex.MeshPlaneCut,
                                ex.MeshSeparate, ex.MeshReverse)) and body in before.meshes:
            editor = self._live["form"]._editors.get("mesh")
            if isinstance(editor, QComboBox):
                editor.blockSignals(True)
                editor.setCurrentText(body)
                editor.blockSignals(False)
            self.timeline.update(index, mesh=body)
            self._refresh()
            return True
        if isinstance(feature, sf.BoundaryFill) and ray is not None:
            # Klick auf eine nicht gefüllte Zelle (grauer Geist): sie wird wieder gefüllt
            for cell in self.timeline.state.ghosts.get(feature.name, []):
                found = ft.face_on_ray(cell, *ray)
                if found is None or (point is not None and hit is not None and body in self.timeline.state.bodies
                                     and math.dist(ray[0], found[1]) > math.dist(ray[0], point) + 1e-6):
                    continue        # die Zelle liegt hinter dem getroffenen Körper
                from OCP.BRepClass3d import BRepClass3d_SolidClassifier
                from OCP.TopAbs import TopAbs_IN
                from OCP.gp import gp_Pnt
                kept = [p for p in feature.remove_points
                        if BRepClass3d_SolidClassifier(cell, gp_Pnt(*p), 1e-6).State() != TopAbs_IN]
                self._live["form"].set_list("remove_points", kept)
                self.timeline.update(index, remove_points=kept)
                self._refresh()
                return True
        if isinstance(feature, sf.BoundaryFill) and feature.tools and body not in before.surfaces \
                and body not in before.bodies and ray is not None and point is not None:
            # Klick auf eine Zelle der Vorschau: sie wird nicht gefüllt. Der Trefferpunkt liegt auf ihrem Rand –
            # ein Stück entlang des Sehstrahls weiter liegt er sicher in ihr
            inside = [point[i] + ray[1][i] * 0.05 for i in range(3)]
            changes = {"remove_points": [*feature.remove_points, inside]}
            self._live["form"].set_list("remove_points", changes["remove_points"])
            self.timeline.update(index, **changes)
            self._refresh()
            return True
        if isinstance(feature, (sf.TrimSurface, sf.BoundaryFill, sf.ExtendSurface, sf.Unstitch)) and \
                (body in before.surfaces or body in before.bodies):
            # Stutzen: erst die Fläche, dann das Werkzeug, danach Stücke, die wegfallen sollen;
            # Begrenzungsfüllung: begrenzende Körper sammeln; Verlängern/Trennen: der angeklickte Körper
            form = self._live["form"]
            if isinstance(feature, sf.BoundaryFill):
                chosen = [name for name in feature.tools if name != body]
                changes = {"tools": chosen if len(chosen) < len(feature.tools) else [*feature.tools, body]}
                form.set_list("tools", changes["tools"])
            elif isinstance(feature, sf.TrimSurface) and feature.surface and feature.tool:
                if body != feature.surface or point is None:
                    return False
                changes = {"remove_points": [*feature.remove_points, list(point)]}
                form.set_list("remove_points", changes["remove_points"])
            elif isinstance(feature, sf.TrimSurface) and feature.surface and body != feature.surface:
                changes = {"tool": body}
            else:
                changes = {"surface": body}
            for name, value in changes.items():
                editor = form._editors.get(name)
                if isinstance(editor, QComboBox):
                    editor.blockSignals(True)
                    if editor.findText(value) < 0:
                        editor.addItem(value)
                    editor.setCurrentText(value)
                    editor.blockSignals(False)
            self.timeline.update(index, **changes)
            self._refresh()
            return True
        if body in before.surfaces and isinstance(feature, (sf.Thicken, sf.OffsetSurface, sf.Stitch, ex.ReverseNormal)):
            # Flächen-Werkzeug: der angeklickte Flächenkörper (Vernähen sammelt mehrere, erneut klicken nimmt heraus)
            if isinstance(feature, sf.Stitch):
                chosen = [name for name in feature.surfaces if name != body]
                changes = {"surfaces": chosen if len(chosen) < len(feature.surfaces) else [*feature.surfaces, body]}
                self._live["form"].set_list("surfaces", changes["surfaces"])
            else:
                changes = {"surface": body}
                editor = self._live["form"]._editors.get("surface")
                if isinstance(editor, QComboBox):
                    editor.blockSignals(True)
                    editor.setCurrentText(body)
                    editor.blockSignals(False)
            self.timeline.update(index, **changes)
            self._refresh()
            return True
        if point is None or body not in before.bodies:
            return False                # z. B. ein Körper, den erst dieses Werkzeug erzeugt
        shape = before.bodies[body]
        form = self._live["form"]
        changes: dict = {}

        def toggled(current: list, marker: list) -> list:
            # schon gewählt (gleicher Merkpunkt) → herausnehmen, sonst dazunehmen
            kept = [p for p in current if math.dist(p, marker) > ft.SELECTION_TOLERANCE]
            return kept if len(kept) < len(current) else [*current, marker]

        if isinstance(feature, (mt.AxisNormalToFace, mt.JointOrigin)):
            # Stelle auf einer Fläche mit der Flächennormale dort (Achse bzw. Gelenkursprung)
            ray = getattr(self, "_last_ray", None)
            found = ft.face_on_ray(shape, *ray) if ray is not None else None
            face = found[0] if found is not None else ft.nearest_face(shape, point)
            if face is None:
                return False
            try:
                marker = ex.face_normal_at(face, found[1] if found is not None else point)
            except ft.FeatureError:
                return False
            changes = {"body": body, "point": marker[:3] if isinstance(feature, mt.AxisNormalToFace) else marker}
            form.set_list("point", changes["point"])
        elif isinstance(feature, ex.Align):
            # Ausrichten: erster Klick die Fläche am bewegten Körper, zweiter die Zielfläche (an einem anderen
            # Körper); ein weiterer Klick am bewegten Körper wählt dessen Fläche neu
            ray = getattr(self, "_last_ray", None)
            found = ft.face_on_ray(shape, *ray) if ray is not None else None
            face = found[0] if found is not None else ft.nearest_face(shape, point)
            if face is None:
                return False
            try:
                marker = ex.face_normal_at(face, found[1] if found is not None else point)
            except ft.FeatureError:
                return False
            if not feature.body or not feature.source or body == feature.body:
                changes = {"body": body, "source": marker}
                if body == feature.body and feature.target:
                    changes["target"] = feature.target
                form.set_list("source", marker)
            else:
                changes = {"target": marker}
                form.set_list("target", marker)
        elif isinstance(feature, (ft.Fillet, ft.Chamfer, sm.EdgeFlange, ex.Hem, mt.Rip)):
            edge = ft.nearest_edge(shape, point)
            if edge is None:
                return False
            current = feature.edge_points if feature.body == body else []
            changes = {"body": body, "edge_points": toggled(current, list(ft.edge_point(edge)))}
        elif isinstance(feature, solids.ReplaceFace) and feature.face_points and body != feature.body \
                and feature.body != ft.ALL_BODIES:
            # zweite Fläche an einem anderen Körper: sie wird die Ersatzfläche
            face = ft.nearest_face(shape, point)
            changes = {"target": solids.TARGET_FACE, "target_body": body,
                       "target_points": [list(ft.face_point(face))]}
        elif isinstance(feature, (solids.Shell, solids.Draft, solids.Thread, solids.Appearance, solids.PressPull,
                                  solids.DeleteFace, solids.MoveFace, solids.ReplaceFace, ex.SplitFace,
                                  mt.Emboss)):
            # Die Fläche im Körper vor dem Werkzeug suchen (Sehstrahl an der exakten Form): die Vorschau zeigt
            # schon die Wirkung, bei der Schale z. B. eine geöffnete Oberseite, durch die der Klick sonst fiele
            ray = getattr(self, "_last_ray", None)
            found = ft.face_on_ray(shape, *ray) if ray is not None else None
            face = found[0] if found is not None else ft.nearest_face(shape, point)
            if face is None:
                return False
            current = feature.face_points if feature.body == body else []
            marker = list(ft.face_point(face))
            # Fläche ersetzen und Prägen arbeiten mit genau einer Fläche; Drücken/Ziehen nimmt wie in Fusion mehrere
            # (jeder weitere Klick fügt eine Fläche hinzu bzw. nimmt sie wieder heraus)
            single = isinstance(feature, (solids.ReplaceFace, mt.Emboss))
            changes = {"body": body, "face_points": [marker] if single else toggled(current, marker)}
        elif isinstance(feature, ft.Extrude) and feature.face_points:
            face = ft.nearest_face(shape, point)
            if face is None or sketch.face_frame(face) is None:
                return False
            changes = {"face_points": toggled(feature.face_points, list(ft.face_point(face))) or feature.face_points}
        elif isinstance(feature, solids.Hole):
            # Bohrung: auf die angeklickte ebene Fläche, an die angeklickte Stelle
            face = ft.nearest_face(shape, point)
            frame = sketch.face_frame(face) if face is not None else None
            if frame is None:
                return False
            origin, normal, x_dir = np.array(frame[0:3]), np.array(frame[3:6]), np.array(frame[6:9])
            offset = np.array(point) - origin
            changes = {"frame": frame, "base": ft.NO_BASE, "offset": 0.0, "plane": "XY", "points_sketch": solids.NO_SKETCH,
                       "position_x": float(offset @ x_dir), "position_y": float(offset @ np.cross(normal, x_dir))}
            for name in ("position_x", "position_y", "offset"):
                form.set_value(name, changes[name])
        elif "body" in {item.name for item in feature.parameters()} and not getattr(feature, "object_type", "Körper") \
                == "Features":
            changes = {"body": body}
        else:
            return False
        # Auswahlfelder still nachführen, sonst setzt die nächste Formularänderung den alten Wert zurück
        for name, value in changes.items():
            editor = getattr(form, "_editors", {}).get(name)
            if isinstance(editor, QComboBox):
                editor.blockSignals(True)
                if editor.findText(value) < 0:
                    editor.addItem(value)
                editor.setCurrentText(value)
                editor.blockSignals(False)
            elif isinstance(value, list) and (name.endswith("_points") or name == "tools"):
                form.set_list(name, value)
        self.timeline.update(index, **changes)
        self._refresh()
        return True

    def _split_pick(self, index: int, feature: solids.SplitBody, before: ft.BuildState, ray) -> bool:
        """Teilen offen: die angeklickte Fläche (Körper oder Flächenkörper, eben oder gewölbt) wird Trennwerkzeug.
        Weitere Flächen desselben Körpers kommen dazu, nochmals anklicken nimmt eine heraus; eine Fläche eines
        anderen Körpers ersetzt die Auswahl. Gesucht wird im Stand vor dem Teilen (die Vorschau ist schon geteilt)."""
        found = [(math.dist(ray[0], at), name, face)
                 for name, shape in {**before.bodies, **before.surfaces}.items()
                 for face, at in [ft.face_on_ray(shape, *ray) or (None, None)] if face is not None]
        if not found:
            return False
        _distance, body, face = min(found, key=lambda item: item[0])
        point = list(ft.face_point(face))
        points = [list(entry) for entry in feature.tool_points] if feature.tool_body == body else []
        kept = [entry for entry in points if math.dist(entry[:3], point) > ft.SELECTION_TOLERANCE]
        points = kept if len(kept) < len(points) else [*points, point]
        changes = {"tool_body": body if points else "", "tool_points": points}
        form = self._live["form"]
        editor = form._editors.get("tool_body")
        if isinstance(editor, QComboBox):
            editor.blockSignals(True)
            if editor.findText(changes["tool_body"]) < 0:
                editor.addItem(changes["tool_body"])
            editor.setCurrentText(changes["tool_body"])
            editor.blockSignals(False)
        form.set_list("tool_points", points)
        self.timeline.update(index, **changes)
        self._refresh()
        return True

    def _live_cleared(self, name: str) -> None:
        """✕ an einem Auswahlfeld: das offene Werkzeug verliert seine gewählten Kanten bzw. Flächen."""
        if self._live is None:
            return
        feature = self.timeline.features[self._live["index"]]
        if isinstance(feature, solids.CombineBodies):
            # Kombinieren: Ziel- bzw. Werkzeugfeld leeren und gleich wieder dorthin wählen lassen
            self.timeline.update(self._live["index"], **({"target": ""} if name == "target" else {"tools": [], "tool": ""}))
            self._live["combine_field"] = name
            self._sync_combine_form()
            self._refresh()
            return
        self.timeline.update(self._live["index"], **{name: []})
        self._live["form"].set_list(name, [])
        self._refresh()

    def _select_profile(self, x: float, y: float, additive: bool, blocked: bool) -> bool:
        """Klick im Modell auf ein Skizzenprofil wählt es aus (danach „Extrusion“ oder E); True, wenn eines traf.

        Liegt ein Körper vor der Skizze (`blocked`), gehört der Klick dem Körper.
        """
        if blocked or self._live is not None:
            if not additive:
                self._selected_profiles = []
                self.viewport.set_geometry("selected_profiles", np.zeros((0, 3), dtype=np.float32))
            return False
        regions = self._profile_regions(self._active_sketch_names())
        number = self._profile_at(x, y, regions)
        if number is None:
            self._selected_profiles = []
            self.viewport.set_geometry("selected_profiles", np.zeros((0, 3), dtype=np.float32))
            return False
        chosen = regions[number]
        # mit Strg wird gesammelt – auch über mehrere Skizzen hinweg
        kept = [r for r in self._selected_profiles if additive]
        same = [r for r in kept if r["point"] == chosen["point"]]
        self._selected_profiles = [r for r in kept if r not in same] if same else [*kept, chosen]
        self._selection = []
        self._show_selection()
        self.viewport.set_geometry("selected_profiles", _stack([r["world"] for r in self._selected_profiles]))
        if self._selected_profiles:
            self.statusBar().showMessage(f"Profil in {chosen['sketch']} gewählt – „Extrusion“ (E) zieht es auf", 8000)
        return True

    def _show_selection(self) -> None:
        """Hebt die gewählten Flächen und Kanten hervor und nennt sie in der Statuszeile."""
        faces, edges = [], []
        for kind, body, index in self._selection:
            pick = self._picks.get(body)
            if pick is None:
                continue
            if kind == "face":
                faces.append(pick.triangles.reshape(-1, 3, 3)[pick.triangle_face == index].reshape(-1, 3))
            elif kind == "vertex":
                # Eckpunkt: kleines Kreuz aus drei Strichen, in der Größe der Ansicht angepasst
                points = self._vertex_points(body)
                if index < len(points):
                    size = self.viewport._distance * 0.012
                    for axis in np.eye(3):
                        edges.append(np.array([points[index] - axis * size, points[index] + axis * size],
                                              dtype=np.float32))
            else:
                edges.append(pick.segments.reshape(-1, 2, 3)[pick.segment_edge == index].reshape(-1, 3))
        live_faces, live_edges = self._live_selection_geometry()
        sketch_faces, sketch_lines = self._sketch_highlight()      # im Browser angeklickte Skizze
        live_faces, live_edges = live_faces + sketch_faces, live_edges + sketch_lines
        self.viewport.set_geometry("selected_faces", _stack(faces + live_faces))
        self._show_ghosts()
        self.viewport.set_geometry("selected_edges", _stack(edges + live_edges))
        if self._selection:
            count_edges = sum(1 for kind, _body, _index in self._selection if kind == "edge")
            count_points = sum(1 for kind, _body, _index in self._selection if kind == "vertex")
            count_faces = len(self._selection) - count_edges - count_points
            bodies = ", ".join(dict.fromkeys(body for _kind, body, _index in self._selection))
            self.statusBar().showMessage(
                f"Gewählt: {count_edges} Kante(n), {count_faces} Fläche(n), {count_points} Eckpunkt(e) an {bodies}. "
                "Strg+Klick erweitert, Esc hebt die Auswahl auf.")
        elif not self.timeline.errors:
            self.statusBar().clearMessage()
        self._show_measurement()

    def _show_ghosts(self) -> None:
        """Zeigt bei offener Begrenzungsfüllung die nicht gefüllten Zellen grau durchscheinend (anklickbar)."""
        feature = None
        if self._live is not None and self._live.get("session") is None \
                and 0 <= self._live["index"] < len(self.timeline.features):
            feature = self.timeline.features[self._live["index"]]
        cells = self.timeline.state.ghosts.get(feature.name, []) if isinstance(feature, sf.BoundaryFill) else []
        if not cells:
            self.viewport.set_ghosts(None)
            return
        picks = [mesh.pick_mesh(cell) for cell in cells]
        self.viewport.set_ghosts(_stack([pick.triangles for pick in picks]), _stack([pick.segments for pick in picks]))

    def _live_selection_geometry(self) -> tuple[list, list]:
        """Wie in Fusion beim Erstellen und nachträglichen Bearbeiten: die Kanten und Flächen, die das offene
        Werkzeug gerade bearbeitet (Verrundung, Fase, Schale, Schräge …), hervorgehoben – im Modell vor dem
        Werkzeug, also auch dort, wo die Kante schon verrundet ist. Liefert (Flächen-Dreiecke, Kanten-Striche)."""
        if self._live is None or self._live.get("session") is not None:
            return [], []
        index = self._live["index"]
        if not 0 <= index < len(self.timeline.features):
            return [], []
        feature = self.timeline.features[index]
        if isinstance(feature, solids.CombineBodies):
            # Zielkörper (bzw. das Ergebnis, das aus ihm wird) wie gewählt hervorheben
            pick = self._picks.get(feature.target)
            return ([pick.triangles], [pick.segments]) if pick is not None else ([], [])
        markers = {"edge": getattr(feature, "edge_points", None) or [],
                   "face": getattr(feature, "face_points", None) or []}
        profile_faces = []
        if isinstance(feature, ft.Extrude) and (feature.sketch or feature.extra_profiles):
            sketches = list(dict.fromkeys([*([feature.sketch] if feature.sketch else []),
                                           *[name for name, _points in feature.extra_profiles]]))
            regions = self._profile_regions(sketches)
            profile_faces = [regions[number]["world"] for number in
                             self._extrude_regions(feature, regions, self.timeline.state_before(index))]
        if not markers["edge"] and not markers["face"]:
            return profile_faces, []
        before = self.timeline.state_before(index)
        names = ft.select_bodies(before, feature.body) if getattr(feature, "body", ft.ALL_BODIES) in before.bodies \
            else list(before.bodies)
        faces, edges = [], []
        for name in names:
            shape = before.bodies[name]
            key = id(shape)
            if key not in self._pick_cache:
                self._pick_cache[key] = (shape, mesh.pick_mesh(shape))
            pick = self._pick_cache[key][1]
            # Kanten und Flächen über ihre Merkpunkte wiederfinden (Nummern ändern sich bei jeder Neuberechnung)
            for number, edge in enumerate(ft.unique_edges(shape)) if markers["edge"] else ():
                point = ft.edge_point(edge)
                if any(math.dist(point, marker) <= ft.SELECTION_TOLERANCE for marker in markers["edge"]):
                    edges.append(pick.segments.reshape(-1, 2, 3)[pick.segment_edge == number].reshape(-1, 3))
            for number, face in enumerate(ft.faces_of(shape)) if markers["face"] else ():
                point = ft.face_point(face)
                if any(math.dist(point, marker) <= ft.SELECTION_TOLERANCE for marker in markers["face"]):
                    faces.append(pick.triangles.reshape(-1, 3, 3)[pick.triangle_face == number].reshape(-1, 3))
        return faces + profile_faces, edges

    def _show_measurement(self) -> None:
        """Messen: Werte der Auswahl oben links, bei zwei Elementen dazu die Abstandslinie im Modell."""
        lines, segment = [], None
        if self._measure.isChecked():
            shapes = []
            for kind, body, index in self._selection:
                if body not in self.timeline.state.bodies:
                    continue
                shape = self.timeline.state.bodies[body]
                if kind == "vertex":
                    if index < len(self._vertex_points(body)):
                        shapes.append((kind, self._vertex_shape(body, index)))      # auch Kreismitten
                    continue
                parts = {"edge": ft.unique_edges, "face": ft.faces_of}[kind](shape)
                if index < len(parts):
                    shapes.append((kind, parts[index]))
            names = {"edge": "Kante", "face": "Fläche", "vertex": "Eckpunkt"}
            if len(shapes) == 1:
                lines = [names[shapes[0][0]], *measure.describe(*shapes[0])]
            elif len(shapes) == 2:
                lines, segment = measure.between(*shapes)
                for kind, shape in shapes:
                    lines.append(f"{names[kind]}: " + ", ".join(measure.describe(kind, shape)))
            elif not shapes:
                lines = ["Messen: Kante, Fläche oder Eckpunkt anklicken,", "Strg+Klick wählt ein zweites Element"]
            else:
                lines = ["Messen: höchstens zwei Elemente wählen"]
        self.viewport.show_info("\n".join(lines))
        points = np.array(segment if segment else [], dtype=np.float32).reshape(-1, 3)
        self.viewport.set_geometry("measure", points)

    def _selected_points(self, kind: str) -> tuple[str | None, list]:
        """Körper und Merkpunkte der gewählten Kanten ("edge") oder Flächen ("face").

        Der Körper ist None, wenn die Auswahl dieser Art leer ist oder mehrere Körper betrifft.
        """
        chosen = [(body, index) for found, body, index in self._selection if found == kind]
        bodies = {body for body, _index in chosen}
        if len(bodies) != 1:
            return None, []
        body = bodies.pop()
        source = self._picks[body].edge_points if kind == "edge" else self._picks[body].face_points
        return body, [list(source[index]) for _body, index in chosen]

    def _apply_selection(self, feature: ft.Feature) -> None:
        """Übernimmt die aktuelle Auswahl in ein neues Werkzeug (wie in Fusion: erst wählen, dann Befehl)."""
        selected_bodies = list(dict.fromkeys(body for _kind, body, _index in self._selection))
        if isinstance(feature, (ft.Fillet, ft.Chamfer, sm.EdgeFlange, ex.Hem, mt.Rip)):
            body, points = self._selected_points("edge")
            if body:
                feature.body, feature.edge_points = body, points
                return
        if isinstance(feature, (solids.Shell, solids.Thread, solids.Draft, solids.Appearance, solids.PressPull,
                                solids.DeleteFace, solids.ReplaceFace, solids.MoveFace, ex.SplitFace, mt.Emboss)):
            body, points = self._selected_points("face")
            if body and isinstance(feature, mt.Emboss):
                points = points[:1]             # Prägen wirkt auf genau eine Fläche
            if body:
                feature.body, feature.face_points = body, points
                return
        if isinstance(feature, solids.SplitBody) and feature.tool_points:
            return      # die gewählten Flächen sind das Trennwerkzeug, geteilt werden die übrigen Körper
        # Netz- und Flächenwerkzeuge: der gewählte Netz- bzw. Flächenkörper
        state = self.timeline.state
        if isinstance(feature, mt.RigidGroup):
            feature.bodies = [name for name in selected_bodies if name in state.bodies]
            return
        if isinstance(feature, mt.Canvas):
            # vorher gewählte ebene Fläche: die Leinwand liegt gleich darauf (mittig)
            body, points = self._selected_points("face")
            if body and body in state.bodies:
                face = ft.nearest_face(state.bodies[body], points[0])
                values = self._canvas_on_face(feature, face, ft.face_point(face)) if face is not None else None
                if values is not None:
                    for name, value in values.items():
                        setattr(feature, name, value)
            return
        if isinstance(feature, (mt.AxisNormalToFace, mt.JointOrigin)):
            # gewählte Fläche: ihr Merkpunkt mit der Normale dort
            body, points = self._selected_points("face")
            if body and body in state.bodies:
                face = ft.nearest_face(state.bodies[body], points[0])
                if face is not None:
                    marker = ex.face_normal_at(face, points[0])
                    feature.body = body
                    feature.point = marker[:3] if isinstance(feature, mt.AxisNormalToFace) else marker
            return
        if hasattr(feature, "mesh") and len(selected_bodies) == 1 and selected_bodies[0] in state.meshes:
            feature.mesh = selected_bodies[0]
        if isinstance(feature, ex.MeshCombine):
            feature.meshes = [name for name in selected_bodies if name in state.meshes]
        if hasattr(feature, "surface") and len(selected_bodies) == 1 and selected_bodies[0] in state.surfaces:
            feature.surface = selected_bodies[0]
        # Sonst zählt nur, an welchem Körper etwas gewählt ist
        if len(selected_bodies) == 1:
            if getattr(feature, "body", None) == ft.ALL_BODIES or (
                    getattr(feature, "body", None) == "" and selected_bodies[0] in state.bodies):
                feature.body = selected_bodies[0]       # auch Werkzeuge ohne Vorgabe (z. B. Körper in Netz)
            elif isinstance(feature, solids.CombineBodies):
                feature.target = selected_bodies[0]
        elif len(selected_bodies) >= 2 and isinstance(feature, solids.CombineBodies):
            # vorher gewählte Körper: der erste wird das Ziel, alle weiteren die Werkzeuge
            feature.target, feature.tools, feature.tool = selected_bodies[0], list(selected_bodies[1:]), ""

    # --- Bearbeiten mit Live-Vorschau -----------------------------------------

    def _build_panel(self) -> None:
        """Eigenschaftenfeld am rechten Rand: Parameter des gerade bearbeiteten Features."""
        self._panel_title = QLabel()
        self._panel_title.setStyleSheet("font-weight:bold; font-size:14px; padding:4px 0;")
        self._panel_form = QVBoxLayout()
        hint = QLabel("Blauen Pfeil in der Ansicht ziehen oder Werte eintragen – das Modell "
                      "läuft mit. Eingabe bestätigt, Esc bricht ab.")
        hint.setWordWrap(True)
        hint.setObjectName("hint")          # Farbe kommt aus dem Design
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(lambda: self._finish_live(True))
        buttons.rejected.connect(lambda: self._finish_live(False))
        layout = QVBoxLayout()
        layout.addWidget(self._panel_title)
        layout.addLayout(self._panel_form)
        layout.addWidget(hint)
        layout.addWidget(buttons)
        layout.addStretch(1)
        content = QWidget()
        content.setLayout(layout)
        # Ein hohes Feld (z. B. das Skizzenfeld mit Palette) darf das Fenster nicht über den Bildschirm hinaus
        # strecken: es lässt sich rollen
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)
        self.panel_dock = QDockWidget("Eigenschaften")
        self.panel_dock.setWidget(scroll)
        self.panel_dock.setMinimumWidth(320)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.panel_dock)
        self.panel_dock.hide()

    def _begin_live(self, index: int, is_new: bool, form=None) -> None:
        """Startet das Bearbeiten des Features `index`: Eigenschaftenfeld und Ziehpfeil erscheinen.

        Ohne Angabe wird das allgemeine Formular verwendet; Skizzen bringen ihr eigenes Feld mit.
        """
        feature = self.timeline.features[index]
        if form is None:
            earlier = [f.name for f in self.timeline.features[:index] if isinstance(f, _SKETCH_TYPES)]
            before = self.timeline.state_before(index)
            bodies = [name for name in before.bodies if name not in before.frozen]
            form = FeatureForm(feature, earlier, None, bodies,
                               resolver=lambda text: pm.evaluate(text, self.timeline.parameter_values),
                               component_names=list(dict.fromkeys(before.components.values())),
                               plane_names=list(before.planes), axis_names=list(before.axes),
                               feature_names=list(reversed(before.history)),   # jüngstes zuerst
                               surface_names=list(before.surfaces), mesh_names=list(before.meshes),
                               origin_names=list(before.joint_origins))
        form.changed.connect(self._live_changed)
        if isinstance(form, FeatureForm):
            form.cleared.connect(self._live_cleared)
            form.field_activated.connect(self._combine_field_clicked)
        old = {key: value for key, value in feature.to_dict().items() if key != "kind"}
        # Beim allerersten Schritt gibt es noch nichts, woran sich die Ansicht orientieren könnte;
        # sie wird deshalb nach dem Bestätigen auf das Modell eingepasst
        self._live = {"index": index, "new": is_new, "old": old, "form": form,
                      "fit": is_new and index == 0}
        self._panel_title.setText(feature.KIND)
        self._panel_form.addWidget(form)
        self.panel_dock.show()
        if isinstance(feature, solids.CombineBodies):
            self._sync_combine_form()           # aktives Feld, Farben und Hinweis gleich zu Beginn
        self.viewport.set_handles([], active=0)     # neues Werkzeug: Hauptmaß zuerst
        self._update_handle()

    def _live_changed(self) -> None:
        """Übernimmt die Werte des Eigenschaftenfelds und rechnet das Modell sofort neu."""
        if self._live is None:
            return
        index, values = self._live["index"], self._live["form"].values()
        new_name = values.pop("name")
        if new_name != self.timeline.features[index].name:
            # Umbenennen führt alle Verweise späterer Schritte mit (Skizze, Pfad, Basisebene, Komponente …)
            try:
                self.timeline.rename(index, new_name)
            except ValueError as error:
                self.statusBar().showMessage(str(error), 6000)
        if hasattr(self._live["form"], "expressions"):
            # Ausdrücke der Zahlenfelder gehören zum Feature und werden bei jeder Neuberechnung ausgewertet
            values["expressions"] = self._live["form"].expressions()
        self.timeline.update(index, **values)
        self._refresh()

    def _handle_changed(self, value: float) -> None:
        """Der Ziehpfeil oder sein Wertfeld hat das Hauptmaß geändert.

        Beim Ziehen kommen Mausereignisse schneller, als das Modell neu gerechnet ist: gerechnet wird deshalb
        nur der jeweils neueste Wert, sobald die Ereignisschlange abgearbeitet ist (wie Fusion beim Ziehen).
        """
        self._pending_handle = (self.viewport.active_handle(), value)
        if not self._handle_timer.isActive():
            self._handle_timer.start()

    def _apply_pending_handle(self) -> None:
        if self._pending_handle is None:
            return
        handle, value = self._pending_handle
        self._pending_handle = None
        if handle is not self.viewport.active_handle():
            return                      # der Pfeil ist inzwischen ein anderer (Werkzeug gewechselt)
        self._apply_handle_value(handle, value)

    def _apply_handle_value(self, handle, value: float) -> None:
        if self._live is None and handle is not None and handle.parameter == "section":
            # Der Pfeil der Schnittanalyse verschiebt nur die Schnittebene der Anzeige
            self._section["offset"] = value
            self._apply_section()
            return
        if self._live is None or handle is None:
            return
        feature = self.timeline.features[self._live["index"]]
        expression = self.viewport.take_handle_expression()
        if handle.parameter in ("count", "second_count"):
            value = float(max(1, round(value)))      # eine Anzahl ist ganzzahlig
        editor = getattr(self._live["form"], "_editors", {}).get(handle.parameter)
        if expression and isinstance(editor, ExpressionBox):
            # Am Pfeil wurde ein Ausdruck eingetippt: das Maß hängt ab jetzt daran
            editor.set_expression(expression, value)
            feature.expressions[handle.parameter] = expression
        else:
            self._live["form"].set_value(handle.parameter, value)
            # Wer am Pfeil zieht oder eine Zahl eintippt, löst das Maß von seinem Ausdruck
            feature.expressions.pop(handle.parameter, None)
        index = self._live["index"]
        values = {handle.parameter: value}
        if self._light_handle is not None and self._light_handle["index"] == index:
            values["operation"] = draw3d._PREVIEW_OPERATION      # weiter ohne Verrechnen bis zum Loslassen
        started = time.perf_counter()
        self.timeline.update(index, **values)
        real = getattr(feature, "operation", draw3d._PREVIEW_OPERATION)
        if (self._light_handle is None and self.viewport._handle_drag and real != draw3d._PREVIEW_OPERATION
                and time.perf_counter() - started > draw3d._SLOW_PREVIEW):
            # schweres Modell: bis zum Loslassen ohne Boolesche Operation rechnen (wie beim Aufziehen)
            self._light_handle = {"index": index, "operation": real}
            self.statusBar().showMessage("Vorschau ohne Verrechnen (schweres Modell) – die Operation folgt beim "
                                         "Loslassen.", 5000)
        self._refresh()

    def _restore_light_handle(self) -> None:
        """Pfeil losgelassen bzw. Werkzeug beendet: nach der leichten Vorschau die echte Operation einmal rechnen."""
        if self._handle_timer.isActive():
            self._handle_timer.stop()
            self._apply_pending_handle()        # der letzte gezogene Wert zuerst
        light, self._light_handle = self._light_handle, None
        if light is None or light["index"] >= len(self.timeline.features):
            return
        self.timeline.update(light["index"], operation=light["operation"])
        self._refresh()

    def _update_handle(self) -> None:
        """Berechnet den Ziehpfeil des bearbeiteten Features neu (nach jeder Neuberechnung)."""
        self._handles = []
        if self._live is not None and self._live["index"] < len(self.timeline.features) \
                and not self._awaiting_selection(self.timeline.features[self._live["index"]]):
            index = self._live["index"]
            self._handles = handles.handles_for(self.timeline.features[index],
                                              self.timeline.state_before(index), self.timeline.state)
            for handle in self._handles:
                handle.expression = self.timeline.features[index].expressions.get(handle.parameter, "")
        self._show_live_plane()
        if self._live is None and self._section_action.isChecked():
            # Ohne laufendes Werkzeug gehört der Pfeil der Schnittanalyse
            axis = solids.AXES[self._section["axis"]]
            self._handles = [handles.Handle((0.0, 0.0, 0.0), tuple(float(c) for c in axis), "section",
                                            self._section["offset"], color=(0.36, 0.62, 1.00))]
        # Der aktive Pfeil bleibt aktiv, solange dasselbe Feature bearbeitet wird
        self.viewport.set_handles(self._handles)
        self._update_hole_grip()
        self._update_operation_bar()

    def _hole_plane(self):
        """(Index, Bohrung, Ebene gp_Ax2) der gerade bearbeiteten Bohrung; None, wenn keine offen ist."""
        if self._live is None or self._live.get("session") is not None:
            return None
        index = self._live["index"]
        if not 0 <= index < len(self.timeline.features):
            return None
        feature = self.timeline.features[index]
        if not isinstance(feature, solids.Hole) or feature.points_sketch != solids.NO_SKETCH:
            return None
        try:
            axes = sketch.placed_axes(self.timeline.state_before(index), feature.plane, feature.offset,
                                      feature.frame, feature.base)
        except ft.FeatureError:
            return None
        return index, feature, axes

    def _awaiting_selection(self, feature) -> bool:
        """Wartet das Werkzeug noch auf die Auswahl dessen, was es ändern soll? Dann gibt es wie in Fusion noch
        keinen Pfeil, und die Timeline zeigt den Schritt nicht als Fehler."""
        if isinstance(feature, (ft.Fillet, ft.Chamfer)):
            return feature.require_pick and not feature.edge_points
        if isinstance(feature, (sm.EdgeFlange, ex.Hem, mt.Rip)):
            return not feature.edge_points
        if isinstance(feature, (solids.Shell, solids.Draft)):
            return feature.require_pick and not feature.face_points
        if isinstance(feature, (solids.MoveFace, solids.PressPull, ex.SplitFace, solids.Thread, mt.Emboss,
                                solids.ReplaceFace, solids.DeleteFace)):
            return not feature.face_points
        if isinstance(feature, ft.Extrude):
            return not feature.sketch and not feature.face_points and not feature.extra_profiles
        if isinstance(feature, solids.Hole):
            return (feature.require_pick and not feature.frame and feature.base == ft.NO_BASE
                    and feature.points_sketch == solids.NO_SKETCH)
        return False

    def _update_hole_grip(self) -> None:
        """Griffpunkt auf der Mitte der offenen Bohrung (sonst keiner)."""
        found = self._hole_plane()
        if found is None or self._awaiting_selection(found[1]):
            self.viewport.set_point_grip(None, None)
            return
        _index, feature, axes = found
        centre = solids._in_plane(axes, feature.position_x, feature.position_y)
        normal = axes.Direction()
        self.viewport.set_point_grip((centre.X(), centre.Y(), centre.Z()), (normal.X(), normal.Y(), normal.Z()))

    def _hole_snap_targets(self, index: int, axes) -> list[tuple[np.ndarray, str]]:
        """Fangpunkte für die Bohrung in ihrer Ebene: Körperecken, Kantenmitten und Kreismitten (wie in Fusion)."""
        before = self.timeline.state_before(index)
        origin = np.array([axes.Location().X(), axes.Location().Y(), axes.Location().Z()])
        normal = np.array([axes.Direction().X(), axes.Direction().Y(), axes.Direction().Z()])
        targets = []
        for shape in before.bodies.values():
            for vertex in ft.unique_vertices(shape):
                place = BRep_Tool.Pnt_s(vertex)
                targets.append((np.array([place.X(), place.Y(), place.Z()]), "Ecke"))
            for edge in ft.unique_edges(shape):
                curve = BRepAdaptor_Curve(edge)
                if curve.GetType() == GeomAbs_Line:
                    targets.append((np.array(ft.edge_point(edge)), "Kantenmitte"))
                elif curve.GetType() == GeomAbs_Circle:
                    centre = curve.Circle().Location()
                    targets.append((np.array([centre.X(), centre.Y(), centre.Z()]), "Kreismitte"))
        # nur was (fast) in der Bohrungsebene liegt, dorthin gelegt
        kept = []
        for point, label in targets:
            height = float((point - origin) @ normal)
            if abs(height) < 1e-6:
                kept.append((point, label))
        return kept

    def _hole_dragged(self, x: float, y: float, z: float, mouse_x: float, mouse_y: float) -> None:
        """Bohrung gezogen: in der Ebene verschoben; rastet an Ecken, Kanten- und Kreismitten (in Mausnähe), sonst
        am Raster der Ansicht."""
        found = self._hole_plane()
        if found is None:
            return
        index, feature, axes = found
        point = np.array([x, y, z])
        targets = self._hole_snap_targets(index, axes)
        label = ""
        if targets:
            spots = self.viewport.project_many(np.array([t[0] for t in targets]))
            distances = np.hypot(spots[:, 0] - mouse_x, spots[:, 1] - mouse_y)
            nearest = int(np.nanargmin(distances))
            if distances[nearest] <= _HOLE_SNAP_PIXELS:
                point, label = targets[nearest]
        origin = np.array([axes.Location().X(), axes.Location().Y(), axes.Location().Z()])
        x_dir = np.array([axes.XDirection().X(), axes.XDirection().Y(), axes.XDirection().Z()])
        y_dir = np.array([axes.YDirection().X(), axes.YDirection().Y(), axes.YDirection().Z()])
        local_x, local_y = float((point - origin) @ x_dir), float((point - origin) @ y_dir)
        if not label:
            step = self.viewport.ground_step()          # Rasterweite der Ansicht (wächst mit dem Zoom)
            local_x, local_y = round(local_x / step) * step, round(local_y / step) * step
            label = "Raster"
        form = self._live["form"]
        form.set_value("position_x", local_x)
        form.set_value("position_y", local_y)
        self.timeline.update(index, position_x=local_x, position_y=local_y)
        self._refresh()
        number = lambda value: f"{value:.2f}".replace(".", ",")
        self.viewport.show_hint(f"{label}: {number(local_x)} / {number(local_y)} mm", mouse_x, mouse_y)

    def _show_live_plane(self) -> None:
        """Zeigt beim Teilen die Trennebene als Rahmen, solange das Werkzeug offen ist."""
        outline = np.zeros((0, 3), dtype=np.float32)
        if self._live is not None and self._live["index"] < len(self.timeline.features):
            feature = self.timeline.features[self._live["index"]]
            if isinstance(feature, solids.SplitBody) and not feature.tool_points:
                try:
                    axes = sketch.placed_axes(self.timeline.state_before(self._live["index"]), feature.plane,
                                              feature.offset, feature.frame, feature.base)
                except ft.FeatureError:
                    axes = None
                if axes is not None:
                    state = ft.BuildState(planes={"Trennebene": axes})
                    outline = self._plane_outlines(state, _stack([pick.triangles for pick in self._picks.values()]))
        if self._plane_pick is None:
            self.viewport.set_geometry("pick_planes", outline)

    def _finish_live(self, accept: bool) -> None:
        """Beendet das laufende Bearbeiten: übernehmen oder verwerfen."""
        if accept:
            self.viewport.commit_value_box()    # Enter bestätigt auch eine gerade eingetippte Länge (wie Fusion)
        self._restore_light_handle()    # nie mit der Ersatz-Operation der leichten Vorschau übernehmen
        notice = ""                     # Hinweis für die Statuszeile, erst nach dem Neuzeichnen gezeigt
        if accept and self._live is not None and self._live.get("session") is None \
                and 0 <= self._live["index"] < len(self.timeline.features):
            feature = self.timeline.features[self._live["index"]]
            if isinstance(feature, ft.Extrude) and abs(feature.distance) < 1e-9 \
                    and feature.extent not in (ft.EXTENTS[3], ft.EXTENTS[4]):
                # wie in Fusion: ohne Länge entsteht kein Körper – der Schritt wird nicht übernommen
                accept = False
                notice = "Extrusion ohne Länge verworfen – am Pfeil ziehen oder eine Länge eintippen"
            if self._awaiting_selection(feature):
                accept = False      # wie Fusion: ohne Auswahl entsteht nichts
                notice = f"{feature.KIND} ohne Auswahl verworfen"
            from ..core import start_values
            if accept and self._live.get("new") and start_values.is_neutral(feature):
                # noch bei 0 bestätigt: wie die Extrusion ohne Länge entsteht kein Schritt
                accept = False
                notice = f"{feature.KIND} ohne Wert verworfen – am Pfeil ziehen oder einen Wert eintippen"
        self._cancel_plane_pick()       # ein anderes Werkzeug beendet auch eine offene Ebenenwahl
        self._end_draw(False)           # … und ein angefangenes Aufziehen
        self._cancel_profile_pick()
        if self._pending_presspull:
            self._pending_presspull = False
            self.viewport.show_info("")
        if self._live is None:
            return
        live, self._live = self._live, None
        session = live.get("session")
        if session is not None:
            # Zeichenmodus der Ansicht beenden
            session.close()
            self.viewport.sketch_input = None
            self.viewport.refresh_ground()      # das Bodenraster kommt zurück
            self._ribbon.show_context(None)     # zurück zu den Reitern des Modells
            drawn = [index for index in range(len(session.canvas.entities)) if index not in session.canvas.projected]
            if accept and live["new"] and not drawn and not session.canvas.texts:
                accept = False      # eine neue Skizze ohne eigenen Inhalt (nur der projizierte Umriss) entfällt
        if accept:
            # Noch nicht bestätigte Eingaben mitnehmen
            live["form"].commit()
            self.timeline.update(live["index"], **live["form"].values())
        elif live["new"]:
            self.timeline.remove(live["index"])
            if self._undo_stack:
                self._undo_stack.pop()      # der Rückgängig-Punkt dieses Werkzeugs entfällt
        else:
            self.timeline.update(live["index"], **live["old"])
            if self._undo_stack:
                self._undo_stack.pop()
        live["form"].setParent(None)
        live["form"].deleteLater()
        self.panel_dock.hide()
        self._refresh(fit=accept and live["fit"])
        if notice:
            self.statusBar().showMessage(notice, 6000)      # nach _refresh, das die Statuszeile neu setzt

    def _escape(self) -> None:
        """Esc bricht ein laufendes Werkzeug ab; sonst hebt es die Auswahl auf."""
        marking = getattr(self, "_marking", None)
        if marking is not None and marking.isVisible():
            marking.close()         # ein offenes rundes Menü schließt zuerst
            return
        workspace = getattr(self, "_print_workspace", None)
        if workspace is not None and self._documents.currentWidget() is workspace and workspace.escape():
            return                  # im Druck-Reiter: z. B. „Auf Fläche legen“ beenden
        if self._plane_pick is not None:
            self._cancel_plane_pick()
            return
        if self._draw is not None:
            self._end_draw(False)
            return
        if self._profile_pick is not None:
            self._cancel_profile_pick()
            return
        if self._pending_presspull:
            self._pending_presspull = False
            self.viewport.show_info("")
            return
        session = self._live.get("session") if self._live is not None else None
        if session is not None and session.is_drawing():
            session.finish()        # erst nur das angefangene Element beenden
        elif session is not None and session.canvas.chosen():
            # dann nur die Auswahl in der Skizze aufheben
            session.canvas.selection, session.canvas.selected = set(), None
            session.redraw()
        elif self._live is not None:
            self._finish_live(False)
        else:
            self._clear_selection()

    def _delete_pressed(self) -> None:
        """Entf löscht beim Skizzieren das gewählte Element; im Modell wie in Fusion die gewählten Flächen (auch
        Rundungen, Fasen, herausstehende Teile) – aber nur, wenn der Körper dabei heil bleibt. Sind alle Flächen
        eines Körpers gewählt, wird der Körper entfernt."""
        session = self._live.get("session") if self._live is not None else None
        if session is not None:
            session.canvas.delete_selected()
            return
        if self._live is not None:
            return                      # in einem offenen Werkzeug löscht Entf nichts im Modell
        if getattr(self, "_highlighted_sketch", None) and not self._selection:
            self._remove_sketch(self._highlighted_sketch)       # im Browser markierte Skizze
            return
        state = self.timeline.state
        by_body: dict[str, list] = {}
        for kind, body, index in self._selection:
            if kind == "face" and body in state.bodies and body in self._picks \
                    and index < len(self._picks[body].face_points):
                by_body.setdefault(body, []).append(list(self._picks[body].face_points[index]))
        if not by_body:
            return
        self._remember()
        done, refused = [], []
        # wie Fusion: eine Löschaktion ist ein Schritt je Art – ganz gewählte Körper werden entfernt, die übrigen
        # Flächen (auch an mehreren Körpern) gelöscht
        whole = [body for body, markers in by_body.items() if len(markers) >= len(ft.faces_of(state.bodies[body]))]
        partial = {body: markers for body, markers in by_body.items() if body not in whole}
        steps = []
        if whole:
            steps.append((whole, solids.RemoveBody(body=whole[0], more=whole[1:])))
        if partial:
            markers = [marker for found in partial.values() for marker in found]
            target = next(iter(partial)) if len(partial) == 1 else ft.ALL_BODIES
            steps.append((list(partial), solids.DeleteFace(body=target, face_points=markers)))
        for bodies, feature in steps:
            body = ", ".join(bodies)
            feature.name = self.timeline.unique_name(feature.KIND.replace("-", ""))
            self.timeline.add(feature)
            index = self.timeline.marker - 1
            if index in self.timeline.errors:
                refused.append(body)
                self.timeline.remove(index)     # würde den Körper beschädigen: nicht übernehmen
            else:
                done.append(feature.KIND)
        if not done and self._undo_stack:
            self._undo_stack.pop()              # nichts geändert: kein Rückgängig-Schritt
        self._clear_selection()
        self._refresh()
        if refused:
            self.statusBar().showMessage("Diese Flächen lassen sich nicht entfernen, ohne den Körper zu beschädigen "
                                         "(die Nachbarflächen schließen die Lücke nicht)", 8000)
        else:
            self.statusBar().showMessage(f"{', '.join(dict.fromkeys(done))}: entfernt", 5000)

    def _body_names(self) -> list[str]:
        """Namen der Körper, die sich mit Werkzeugen verändern lassen."""
        state = self.timeline.state
        return [name for name in state.bodies if name not in state.frozen]

    def _default_operation(self) -> str:
        """Wie in Fusion: der erste Körper ist neu; danach richtet sich die Operation nach der
        Lage des neuen Volumens (hinein = ausschneiden, heraus = vereinigen)."""
        return ft.AUTO_OPERATION if self.timeline.state.bodies else ft.OPERATIONS[0]

    def _start_tool(self, run) -> None:
        """Startet ein Werkzeug aus der Oberfläche. Nutzervorgabe (Runde 88): jeder Wert beginnt bei 0 (Faktor und
        Anzahl bei 1) – `_create` setzt die Startwerte nur, solange dieses Kennzeichen gilt (Szenen und Tests, die
        `_create` mit eigenen Werten aufrufen, behalten diese)."""
        self._starting_tool = True
        try:
            run()
        finally:
            self._starting_tool = False

    def _create(self, feature: ft.Feature) -> None:
        """Fügt das Werkzeug sofort ein und öffnet das Live-Bearbeiten. Aus der Oberfläche gestartet, stehen seine
        Werte bei 0 (siehe core/start_values.py) – erst Ziehen am Pfeil oder Eintippen bewirkt etwas.

        Eingestellt wird am Ziehpfeil oder im Eigenschaftenfeld, Abbrechen nimmt das Feature wieder heraus.
        """
        from ..core import start_values
        if getattr(self, "_starting_tool", False):
            start_values.apply_start(feature)
        self._finish_live(True)
        feature.name = self.timeline.unique_name(feature.KIND.replace("-", ""))
        self._apply_selection(feature)
        first = not self.timeline.features
        self._remember()
        self.timeline.add(feature)
        self._refresh(fit=first)
        self._begin_live(self.timeline.marker - 1, True)
        self._show_selection()          # jetzt mit der Hervorhebung dessen, was das neue Werkzeug bearbeitet

    # --- Grundkörper auf dem Raster aufziehen ------------------------------------

    def _set_grid(self, on: bool) -> None:
        QSettings("WolfCAD", "WolfCAD").setValue("raster", "ja" if on else "nein")
        self.viewport.set_ground_visible(on)

    def _draw_primitive(self, feature: ft.Feature) -> None:
        """Startet das Aufziehen eines Grundkörpers auf dem Bodenraster (siehe gui/draw3d.py)."""
        self._finish_live(True)
        self._clear_selection()
        self._draw = PrimitiveDraw(self, feature)
        self.viewport.sketch_input = self._draw
        self.viewport.refresh_ground()
        self.viewport.setFocus()

    def _add_drawn(self, feature: ft.Feature) -> int:
        """Nimmt den gerade aufgezogenen Körper ins Modell auf und gibt seinen Platz in der Timeline zurück."""
        feature.name = self.timeline.unique_name(feature.KIND.replace("-", ""))
        # Rückgängig-Punkt von Hand: `_remember` würde das laufende Aufziehen als anderes Werkzeug beenden
        self._undo_stack.append(self.timeline.to_document())
        del self._undo_stack[:-_UNDO_LIMIT]
        self._redo_stack.clear()
        self.timeline.add(feature)
        self._refresh()
        return self.timeline.marker - 1

    def _end_draw(self, accept: bool) -> None:
        """Beendet das Aufziehen: danach folgt das übliche Live-Bearbeiten, beim Abbruch verschwindet der Körper."""
        draw, self._draw = self._draw, None
        if draw is None:
            return
        draw.close()
        self.viewport.sketch_input = None
        self.viewport.refresh_ground()
        if draw.index is None:
            return
        if accept:
            self._begin_live(draw.index, True)
            # der Körper wurde im sichtbaren Ausschnitt aufgezogen – die Kamera bleibt, wo sie ist
            self._live["fit"] = False
        else:
            self.timeline.remove(draw.index)
            if self._undo_stack:
                self._undo_stack.pop()      # der Rückgängig-Punkt dieses Werkzeugs entfällt
            self._refresh()

    # --- Prüfwerkzeuge -------------------------------------------------------------

    def _start_analysis(self, kind: str | None, value: float | None = None) -> None:
        """Schaltet die farbige Analyse ein (mit Abfrage des Grenzwerts) oder aus."""
        if kind is None:
            self._analysis = None
            self.viewport.show_info("")
            self._refresh()
            return
        if value is None:
            label, default, unit = {"Entformung": ("Mindestschräge", 1.0, "°"),
                                    "Wandstärke": ("Mindestwandstärke", 2.0, "mm"),
                                    "Krümmung": ("Kleinster erlaubter Radius", 5.0, "mm"),
                                    "Zebra": ("Streifenbreite", 10.0, "°")}[kind]
            value, accepted = QInputDialog.getDouble(self, kind, f"{label} ({unit}):", default, 0.01, 89.0, 2)
            if not accepted:
                return
        self._analysis = {"art": kind, "wert": value}
        self._refresh()

    def _set_pull(self, name: str) -> None:
        self._pull = name
        for entry in self.findChildren(QAction):
            if entry.text() in ("+X", "-X", "+Y", "-Y", "+Z", "-Z", _PULL_FACE):
                entry.setChecked(entry.text() == name)
        if self._analysis is not None:
            self._refresh()

    def _recolour_zebra(self) -> None:
        """Färbt nur die Flächen neu (ohne Neuberechnung des Modells), passend zur neuen Blickrichtung."""
        if self._analysis and self._analysis["art"] == "Zebra":
            self.viewport.set_face_groups(self._analysis_groups(self.timeline.state))

    def _set_pull_from_face(self) -> None:
        """Zugrichtung = Normale der angeklickten Fläche (eben oder gekrümmt), wie „Richtung: Fläche“ in Fusion."""
        from ..core import analysis
        faces = [(body, index) for kind, body, index in self._selection if kind == "face"]
        if len(faces) != 1 or faces[0][0] not in self.timeline.state.bodies:
            QMessageBox.information(self, "Zugrichtung", "Bitte zuerst genau eine Fläche anklicken.")
            self._set_pull(self._pull)          # Häkchen wieder auf die bisherige Richtung
            return
        body, index = faces[0]
        try:
            self._pull_face_vector = analysis.face_normal(ft.faces_of(self.timeline.state.bodies[body])[index])
        except ValueError as error:
            QMessageBox.information(self, "Zugrichtung", str(error))
            self._set_pull(self._pull)
            return
        self._set_pull(_PULL_FACE)

    def _pull_vector(self) -> tuple:
        if self._pull == _PULL_FACE:
            return self._pull_face_vector
        axis = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}[self._pull[1]]
        return tuple(c * (-1.0 if self._pull[0] == "-" else 1.0) for c in axis)

    def _analysis_groups(self, state) -> list:
        """Dreiecke aller sichtbaren Körper nach der Analyse in Farbgruppen; dazu die Legende in der Ansicht."""
        from ..core import analysis
        kind, value = self._analysis["art"], self._analysis["wert"]
        parts: dict[int, list] = {}
        for name, pick in self._picks.items():
            triangles = pick.triangles.reshape(-1, 3, 3)
            if kind == "Entformung":
                classes = analysis.draft_classes(pick.triangles, self._pull_vector(), value)
            elif kind == "Zebra":
                # gespiegelte Leuchtstreifen, auf fein unterteilten Dreiecken mit geglätteter Normale (wie in Fusion
                # glatt innerhalb einer Fläche); große Modelle nur einfach unterteilt, damit das Drehen flüssig bleibt
                # Die echten Flächennormalen der Teildreiecke werden je Körperzerlegung einmal bestimmt und gemerkt;
                # beim Drehen wird nur die Spiegelung neu gerechnet
                cache = self.__dict__.setdefault("_zebra_cache", {})
                if id(pick) not in cache:
                    count = len(pick.triangle_face)
                    levels = 3 if count < 1500 else 2 if count < 8000 else 1
                    cache[id(pick)] = (pick, analysis.zebra_surface(ft.faces_of(state.bodies[name]), pick.triangles,
                                                                    pick.triangle_face, levels))
                for key in [key for key, (owner, _data) in cache.items() if owner not in self._picks.values()]:
                    del cache[key]          # Zerlegungen nicht mehr gezeigter Körper vergessen
                triangles, normals = cache[id(pick)][1]
                classes = analysis.zebra_from_normals(normals, (0.0, 0.0, 1.0), value, self.viewport.view_direction())
                triangles = triangles.reshape(-1, 3, 3)
            elif kind == "Krümmung":
                radii = analysis.curvature_radii(ft.faces_of(state.bodies[name]), pick.triangles, pick.triangle_face)
                classes = analysis.curvature_classes(radii, value)
            else:
                classes = analysis.thickness_classes(analysis.wall_thickness(state.bodies[name], pick.triangles), value)
            for klass in set(classes.tolist()):
                parts.setdefault(klass, []).append(triangles[classes == klass].reshape(-1, 3))
        if kind == "Entformung":
            colours = analysis.DRAFT_COLOURS
            direction = ("senkrecht zur gewählten Fläche (" + ", ".join(f"{c:.2f}" for c in self._pull_face_vector) + ")"
                         if self._pull == _PULL_FACE else self._pull)
            legend = (f"Entformung in Richtung {direction}, Mindestschräge {value:g}°\n"
                      "grün: ausreichend   gelb: zu wenig Schräge   rot: Hinterschnitt")
        elif kind == "Zebra":
            colours = analysis.ZEBRA_COLOURS
            legend = (f"Zebra, Streifenbreite {value:g}°\n"
                      "glatt durchlaufende Streifen: tangentialer Übergang   Knick/Sprung: Kante bzw. Lücke")
        elif kind == "Krümmung":
            colours = analysis.CURVATURE_COLOURS
            legend = (f"Krümmung, kleinster erlaubter Radius {value:g} mm\n"
                      "grau: eben   blau: sanft gekrümmt   rot: enger als erlaubt")
        else:
            colours = analysis.THICKNESS_COLOURS
            legend = (f"Wandstärke, Minimum {value:g} mm\n"
                      f"rot: dünner als {value:g} mm   orange: unter {2 * value:g} mm   blau: ausreichend")
        self.viewport.show_info(legend)
        return [(colours[klass], _stack(found)) for klass, found in sorted(parts.items())]

    def _create_replace_face(self) -> None:
        """Fläche ersetzen: zwei Flächen an verschiedenen Körpern gewählt → die erste wird durch die zweite
        (auch gekrümmte) ersetzt; sonst ersetzt die jüngste Konstruktionsebene die gewählte Fläche."""
        faces = [(body, index) for kind, body, index in self._selection if kind == "face"]
        if len(faces) == 2 and faces[0][0] != faces[1][0]:
            (body, index), (other, other_index) = faces
            self._create(solids.ReplaceFace(
                body=body, face_points=[list(self._picks[body].face_points[index])], target=solids.TARGET_FACE,
                target_body=other, target_points=[list(self._picks[other].face_points[other_index])]))
            return
        planes = list(self.timeline.state.planes)
        if not planes:
            QMessageBox.information(self, "Fläche ersetzen",
                                    "Zuerst eine Konstruktionsebene anlegen – oder zwei Flächen anklicken: erst die "
                                    "zu ersetzende, dann die Ersatzfläche an einem anderen Körper.")
            return
        self._create(solids.ReplaceFace(target=planes[-1]))

    def _create_path_pattern(self) -> None:
        sketches = self._active_sketch_names()
        if not sketches:
            QMessageBox.information(self, "Pfadmuster", "Bitte zuerst eine Skizze mit dem Pfad anlegen.")
            return
        self._create(solids.PathPattern(path=sketches[-1]))

    def _create_presspull(self) -> None:
        """Drücken/Ziehen an der gewählten ebenen Fläche; ohne Auswahl sagt ein Hinweis, was zu tun ist."""
        if self._selected_profiles:
            self._create_extrude()          # ein gewähltes Skizzenprofil wird wie in Fusion extrudiert
            return
        if not any(kind == "face" for kind, _body, _index in self._selection):
            # noch nichts gewählt: der nächste Klick auf eine Fläche oder ein Profil startet das Werkzeug
            self._finish_live(True)
            self._pending_presspull = True
            self.viewport.show_info("Drücken/Ziehen: ebene Körperfläche oder Skizzenprofil anklicken. Esc bricht ab.")
            return
        self._create(solids.PressPull())

    def _create_sketch(self) -> None:
        """Beginnt eine neue Skizze in der 3D-Ansicht: auf der gewählten Fläche oder einer Hauptebene."""
        self._finish_live(True)
        feature = sketch.Sketch(name=self.timeline.unique_name("Skizze"))
        # Ist eine ebene Körperfläche gewählt, liegt die Skizze darauf; ihr Umriss dient als Zeichenhilfe
        face = self._selected_planar_face()
        if face is not None:
            frame = sketch.face_frame(face)
            self._sketch_on({"frame": frame, "edges": ft.unique_edges(face), "face_point": list(ft.face_point(face))})
        else:
            # Wie in Fusion: die Ebene wird direkt in der Ansicht angeklickt
            self._start_plane_pick(self._sketch_on, "Skizzenebene wählen: Hauptebene, Konstruktionsebene oder "
                                                    "ebene Körperfläche anklicken (Esc bricht ab)")

    def _sketch_on(self, choice: dict, entities: list | None = None) -> None:
        """Legt die neue Skizze auf der gewählten Ebene an und öffnet sie zum Zeichnen.

        `entities` sind schon vorhandene Elemente (DXF/SVG einfügen)."""
        feature = sketch.Sketch(name=self.timeline.unique_name("Skizze"), entities=list(entities or []))
        if "frame" in choice:
            feature.frame = choice["frame"]
            feature.face_ref = list(choice.get("face_point", []))    # wie Fusion: die Skizze folgt der Fläche
        elif "base" in choice:
            feature.base = choice["base"]       # die Skizze hängt an der Konstruktionsebene
        else:
            feature.plane = choice["plane"]
        self._remember()
        self.timeline.add(feature)
        self._refresh()
        self._start_sketch(self.timeline.marker - 1, True, choice.get("reference"), choice.get("edges"))

    # --- Ebene in der Ansicht wählen -------------------------------------------

    def _start_plane_pick(self, callback, hint: str, any_face: bool = False) -> None:
        """Zeigt die wählbaren Ebenen als Rahmen; ein Klick ruft `callback(auswahl)` auf.

        `auswahl` ist {"plane": "XY"}, {"base": Ebenenname} oder {"frame": …, "reference": …}
        für eine angeklickte ebene Körperfläche. Mit `any_face` (Teilen) zählt auch jede andere Fläche eines Körpers
        oder Flächenkörpers: {"tool_face": (Name, [Merkpunkt])}.
        """
        state = self.timeline.state
        reach = 40.0
        boxes = [solids._bounds(shape) for shape in state.bodies.values()]
        if boxes:
            reach = max(reach, 0.6 * max(max(b[i + 3] - b[i] for i in range(3)) for b in boxes))
        candidates = []
        for name in ft.MAIN_PLANES:
            candidates.append({"label": f"{name}-Ebene", "choice": {"plane": name}, "axes": ft._plane_axes(name, 0.0)})
        for name, axes in state.planes.items():
            candidates.append({"label": name, "choice": {"base": name}, "axes": axes})
        lines = []
        for candidate in candidates:
            axes = candidate["axes"]
            origin = np.array([axes.Location().X(), axes.Location().Y(), axes.Location().Z()])
            x = np.array([axes.XDirection().X(), axes.XDirection().Y(), axes.XDirection().Z()]) * reach
            y = np.array([axes.YDirection().X(), axes.YDirection().Y(), axes.YDirection().Z()]) * reach
            candidate["corners"] = [origin - x - y, origin + x - y, origin + x + y, origin - x + y]
            candidate.update(origin=origin, x=x / reach, y=y / reach, reach=reach)
            for start, end in zip(candidate["corners"], candidate["corners"][1:] + candidate["corners"][:1]):
                lines += [start, end]
        self._plane_pick = {"callback": callback, "candidates": candidates, "hint": hint, "any_face": any_face}
        self._clear_selection()
        outlines = np.array(lines, dtype=np.float32)
        self.viewport.set_geometry("pick_planes", outlines)
        # Alle wählbaren Ebenen und das Modell ins Bild holen – die Hauptebenen liegen sonst oft außerhalb
        self.viewport.fit(outlines, *[pick.triangles for pick in self._picks.values()])
        self.viewport.show_info(hint)

    def _next_plane(self) -> None:
        """Leertaste während der Ebenenwahl: zur nächsten Ebene hinter der hervorgehobenen wechseln."""
        if self._plane_pick is None or "mouse" not in self._plane_pick:
            return
        self._plane_pick["skip"] = self._plane_pick.get("skip", 0) + 1
        mouse = self._plane_pick["mouse"]
        choice = self._plane_at(*mouse)
        self.viewport.set_highlight(choice["corners"] if choice is not None and "corners" in choice else None)
        self.viewport.show_info(self._plane_pick["hint"] + (f"\n→ {choice['label']}" if choice else ""))

    def _cancel_plane_pick(self) -> None:
        if self._plane_pick is None:
            return
        self._plane_pick = None
        empty = np.zeros((0, 3), dtype=np.float32)
        self.viewport.set_geometry("pick_planes", empty)
        self.viewport.set_highlight(None)
        self.viewport.set_geometry("selected_faces", empty)
        self.viewport.show_info("")

    def _plane_at(self, x: float, y: float) -> dict | None:
        """Die Ebene unter dem Fensterpunkt: das Nächstliegende aus Ebenenrahmen und ebenen Körperflächen.

        Zurück kommt die Auswahl (siehe `_start_plane_pick`), ergänzt um "label" und, bei
        Ebenenrahmen, "corners" zum Hervorheben.
        """
        eye, ray, _pixel = self.viewport.pick_ray(x, y)
        hits = []       # alles unter der Maus: (Entfernung entlang des Blicks, Auswahl)
        best = None
        for candidate in self._plane_pick["candidates"]:
            normal = np.cross(candidate["x"], candidate["y"])
            slope = float(ray @ normal)
            if abs(slope) < 1e-9:
                continue
            distance = float((candidate["origin"] - eye) @ normal) / slope
            hit = eye + ray * distance - candidate["origin"]
            inside = abs(float(hit @ candidate["x"])) <= candidate["reach"] and abs(float(hit @ candidate["y"])) <= candidate["reach"]
            if distance > 0 and inside:
                hits.append((distance, {**candidate["choice"], "label": candidate["label"],
                                        "corners": candidate["corners"]}))
        # Eine ebene Körperfläche vor allen Rahmen gewinnt
        hit = self._hit_at(x, y)
        state = self.timeline.state
        any_face = self._plane_pick.get("any_face", False)
        if hit is not None and hit[0] == "face" and (hit[1] in state.bodies or any_face and hit[1] in state.surfaces):
            shape = state.bodies.get(hit[1], state.surfaces.get(hit[1]))
            faces = ft.faces_of(shape)
            frame = sketch.face_frame(faces[hit[2]]) if hit[2] < len(faces) else None
            if hit[1] in state.surfaces and any_face:
                frame = None        # ein Flächenkörper trennt als Fläche (verlängert), nicht als Ebene
            if frame is None and any_face and hit[2] < len(faces) and self._last_hit_point is not None:
                # gewölbte Fläche bzw. Flächenkörper: als Trennwerkzeug, das über seine Ränder verlängert wird
                point = list(ft.face_point(faces[hit[2]]))
                # Der Trefferpunkt stammt aus der Vernetzung, die bei Wölbungen etwas innen liegt: etwas Vorsprung,
                # damit eine Ebene, die genau dort durch die Fläche läuft, die Fläche nicht verdeckt
                distance = math.dist(eye, self._last_hit_point)
                hits.append((distance * 0.995 - 1e-6,
                             {"tool_face": (hit[1], [point]), "face_point": point,
                              "label": f"Fläche an {hit[1]} (verlängert)", "face": hit}))
            if frame is not None:
                normal = np.array(frame[3:6])
                slope = float(ray @ normal)
                distance = float((np.array(frame[0:3]) - eye) @ normal) / slope if abs(slope) > 1e-9 else math.inf
                if 0 < distance:
                    # eine Körperfläche geht bei gleicher Entfernung vor
                    hits.append((distance - 1e-6, {"frame": frame, "edges": ft.unique_edges(faces[hit[2]]),
                                                   "face_point": list(ft.face_point(faces[hit[2]])),
                                                   "label": f"Fläche an {hit[1]}", "face": hit}))
        if not hits:
            return None
        hits.sort(key=lambda entry: entry[0])
        # Die Leertaste blättert zu den dahinterliegenden Ebenen weiter
        chosen = hits[self._plane_pick.get("skip", 0) % len(hits)][1]
        if len(hits) > 1:
            chosen["label"] += f"  ({self._plane_pick.get('skip', 0) % len(hits) + 1} von {len(hits)} – Leertaste: nächste)"
        return chosen

    def _plane_hover(self, x: float, y: float) -> None:
        """Hebt während der Ebenenwahl hervor, was ein Klick an dieser Stelle wählen würde."""
        if self._plane_pick is None:
            return
        if (x, y) != self._plane_pick.get("mouse"):
            # neue Stelle: wieder mit der vordersten Ebene beginnen
            self._plane_pick["mouse"], self._plane_pick["skip"] = (x, y), 0
        choice = self._plane_at(x, y)
        face_fill = np.zeros((0, 3), dtype=np.float32)
        # Ein Ebenenrahmen wird durchscheinend gefüllt, damit das Modell dahinter sichtbar bleibt
        self.viewport.set_highlight(choice["corners"] if choice is not None and "corners" in choice else None)
        if choice is not None and "corners" not in choice:
            _kind, body, index = choice["face"]
            pick = self._picks.get(body)
            if pick is not None:
                face_fill = pick.triangles.reshape(-1, 3, 3)[pick.triangle_face == index].reshape(-1, 3)
        self.viewport.set_geometry("selected_faces", face_fill)
        hint = self._plane_pick["hint"]
        self.viewport.show_info(hint + (f"\n→ {choice['label']}" if choice else ""))

    def _ask_plane(self, with_construction_planes: bool = False, title: str = "Skizzenebene") -> str | None:
        """Lässt eine Hauptebene wählen (Menü an der Mausposition); None bei Abbruch.

        Auf Wunsch stehen auch die Konstruktionsebenen zur Wahl; sie kommen als
        "Ebene:<Name>" zurück.
        """
        menu = QMenu(self)
        menu.addSection(title)
        for plane, hint in (("XY", "XY – Draufsicht"), ("XZ", "XZ – Vorderansicht"), ("YZ", "YZ – Seitenansicht")):
            menu.addAction(hint).setData(plane)
        if with_construction_planes and self.timeline.state.planes:
            menu.addSection("Konstruktionsebenen")
            for name in self.timeline.state.planes:
                menu.addAction(name).setData(_PLANE_PREFIX + name)
        chosen = menu.exec(QCursor.pos())
        return chosen.data() if chosen is not None else None

    # --- Tastenkürzel, Kopieren und Einfügen in der Skizze -----------------------

    def _trigger(self, label: str) -> None:
        """Löst das Werkzeug mit dieser Beschriftung in der Werkzeugleiste aus."""
        found = self._tool_actions.get(label)
        if found is not None:
            found.trigger()

    def _letter_pressed(self, letter: str) -> None:
        session = self._live.get("session") if self._live is not None else None
        if session is not None:
            if letter in self._sketch_keys:
                self._live["form"].select_tool(self._sketch_keys[letter])
        elif letter in self._model_keys and self._plane_pick is None:
            self._trigger(self._model_keys[letter])

    # --- Einstellungen: Mausbelegung und Tastenkürzel ----------------------------------

    def _tool_names(self) -> list[str]:
        """Namen aller Werkzeuge der Leisten (für die Liste der Tastenkürzel)."""
        # alle Modellwerkzeuge in der Reihenfolge der Leiste (vor dem Aufbau der Leiste noch keine)
        return list(getattr(self, "_tool_actions", {}))

    def _apply_settings(self) -> None:
        """Übernimmt Mausbelegung und Tastenkürzel aus den Einstellungen (beim Start und nach Änderungen)."""
        self.viewport.orbit_binding = settings.orbit_binding()
        self.viewport.left_drag = settings.left_drag()
        self._model_keys = {key: tool for tool, key in settings.model_keys(self._tool_names()).items()}
        self._sketch_keys = {key: tool for tool, key in settings.sketch_keys(list(sketch3d.TOOLS)).items()}
        for shortcut in self._key_shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        for shortcut in self._macro_shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._macro_shortcuts = [QShortcut(QKeySequence(macro["key"]), self,
                                           activated=lambda chosen=macro: self.run_macro(chosen))
                                 for macro in settings.macros() if macro["key"]]
        self._key_shortcuts = [QShortcut(QKeySequence(key), self, activated=lambda pressed=key: self._letter_pressed(pressed))
                               for key in sorted(set(self._model_keys) | set(self._sketch_keys))]

    def _open_settings(self) -> None:
        dialog = settings.SettingsDialog(self._tool_names(), list(sketch3d.TOOLS), self,
                                         commands=self.macro_commands())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_settings()

    # --- Makros ---------------------------------------------------------------------------

    def _view_commands(self) -> dict:
        """Befehle für Makros außer den Werkzeugen der Leiste: Ansichten und Umschalter (Name -> Aufruf)."""
        def toggle_style() -> None:
            style = "wireframe" if self.viewport.display_style != "wireframe" else "shaded_edges"
            self._navbar.set_style(style)
            self._set_display_style(style)

        commands = {f"Ansicht: {name}": (lambda yaw=yaw, pitch=pitch: self.viewport.set_view(yaw, pitch))
                    for name, yaw, pitch in (("Vorne", 0, 0), ("Oben", 0, 90), ("Rechts", 90, 0),
                                             ("Isometrisch", 35, 25))}
        commands["Darstellung umschalten"] = toggle_style
        commands["Raster umschalten"] = lambda: self._navbar.buttons["grid"].click()
        commands["Auswahl aufheben"] = self._clear_selection
        commands["Werkzeug bestätigen"] = lambda: self._finish_live(True)
        assert set(commands) == set(settings.VIEW_COMMANDS), "Zusatzbefehle der Makros in settings.py nachtragen"
        return commands

    def macro_commands(self) -> list[str]:
        """Alle Befehle, die ein Makro ausführen kann (für die Einstellungen)."""
        return [*self._view_commands(), *self._tool_actions]

    def run_macro(self, macro: dict) -> None:
        """Führt die Befehle eines Makros nacheinander aus; unbekannte werden gemeldet und übersprungen."""
        views = self._view_commands()
        skipped = []
        for step in macro.get("steps", []):
            if step in views:
                views[step]()
            elif step in self._tool_actions and self._tool_actions[step].isEnabled():
                self._tool_actions[step].trigger()
            else:
                skipped.append(step)
            QApplication.processEvents()
        note = f"Makro „{macro.get('name', '')}“ ausgeführt"
        if skipped:
            note += f" – übersprungen: {', '.join(skipped)}"
        self.statusBar().showMessage(note, 5000)

    # --- Wischgeste im runden Menü -------------------------------------------------------

    def _close_marking(self) -> None:
        marking = getattr(self, "_marking", None)
        if marking is not None and marking.isVisible():
            marking.close()

    def _gesture_moved(self, x: float, y: float) -> None:
        marking = getattr(self, "_marking", None)
        if marking is not None and marking.isVisible():
            marking.hover_at(QPointF(x, y))

    def _gesture_released(self, x: float, y: float) -> None:
        marking = getattr(self, "_marking", None)
        if marking is not None and marking.isVisible():
            marking.release_at(QPointF(x, y))       # in der Mitte losgelassen: der Ring bleibt zum Anklicken offen

    def _sketch_session(self):
        return self._live.get("session") if self._live is not None else None

    def _copy_sketch(self) -> None:
        session = self._sketch_session()
        data = session.canvas.copy_data() if session is not None else None
        if data is not None:
            self._sketch_clipboard = data
            self.statusBar().showMessage(f"{len(data['entities'])} Skizzenelement(e) kopiert", 4000)

    def _paste_sketch(self) -> None:
        session = self._sketch_session()
        if session is None or self._sketch_clipboard is None:
            return
        # Etwas versetzt einfügen, damit die Kopie nicht auf dem Original liegt
        shift = 2 * (session.canvas.grid_step() or 5.0)
        self._live["form"].select_tool("Auswählen")
        session.canvas.paste_data(self._sketch_clipboard, shift, -shift)
        session.redraw()

    # --- Körper kopieren und einfügen (Zwischenablage, auch von Entwurf zu Entwurf) -----------------------------

    def _copy(self) -> None:
        """Strg+C: in der Skizze die Skizzenelemente, im Modell die gewählten Körper."""
        if self._sketch_session() is not None:
            self._copy_sketch()
        else:
            self._copy_bodies()

    def _paste(self) -> None:
        """Strg+V: in der Skizze Skizzenelemente, im Modell kopierte Körper."""
        if self._sketch_session() is not None:
            self._paste_sketch()
        else:
            self._paste_bodies()

    def _copy_bodies(self, names: list[str] | None = None) -> None:
        """Kopiert die gewählten (bzw. die genannten) Körper in die Zwischenablage.

        Der Inhalt geht zweimal raus: in die Zwischenablage des Systems (ein zweites WolfCAD-Fenster sieht ihn
        sofort) und in eine Datei – unter Wayland verschwindet die Zwischenablage mit dem Programm, die Datei
        erlaubt das Einfügen auch nach einem Neustart in einem anderen Entwurf."""
        state = self.timeline.state
        if names is None:
            names = [body for _kind, body, _index in self._selection if body in state.bodies]
        names = list(dict.fromkeys(names))
        if not names:
            self.statusBar().showMessage("Bitte zuerst einen oder mehrere Körper wählen, dann Strg+C", 5000)
            return
        try:
            content = solids.copy_bodies(state, names, self._path.stem if self._path else "Unbenannt")
        except ft.FeatureError as error:
            QMessageBox.warning(self, "Kopieren", str(error))
            return
        text = json.dumps(content)
        mime = QMimeData()
        mime.setData(_BODY_CLIPBOARD_MIME, text.encode("utf-8"))
        mime.setText(f"WolfCAD: {', '.join(names)}")        # lesbarer Hinweis für andere Programme
        QApplication.clipboard().setMimeData(mime)
        try:
            _BODY_CLIPBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
            _BODY_CLIPBOARD_FILE.write_text(text, encoding="utf-8")
        except OSError:
            pass            # ohne Datei klappt das Einfügen trotzdem, solange WolfCAD läuft
        self.statusBar().showMessage(f"{len(names)} Körper kopiert – mit Strg+V einfügen (auch in einem "
                                     "anderen Entwurf)", 5000)

    def _body_clipboard(self) -> dict | None:
        """Kopierte Körper: erst die Zwischenablage des Systems, sonst die zuletzt kopierten aus der Datei."""
        mime = QApplication.clipboard().mimeData()
        raw = bytes(mime.data(_BODY_CLIPBOARD_MIME)).decode("utf-8") if mime and mime.hasFormat(
            _BODY_CLIPBOARD_MIME) else ""
        if not raw:
            try:
                raw = _BODY_CLIPBOARD_FILE.read_text(encoding="utf-8")
            except OSError:
                return None
        try:
            content = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(content, dict) or content.get("format") != solids.CLIPBOARD_FORMAT:
            return None
        return content

    def _paste_bodies(self) -> None:
        """Fügt kopierte Körper als neuen Schritt ein und öffnet gleich die Verschiebepfeile (wie Fusion)."""
        content = self._body_clipboard()
        if content is None:
            self.statusBar().showMessage("Keine kopierten Körper – erst Körper wählen und Strg+C drücken", 5000)
            return
        self._create(solids.PasteBodies(source=content.get("source", ""), data=content.get("data", ""),
                                        bodies=content.get("bodies", []), centre=content.get("centre", [])))
        self.statusBar().showMessage(f"{len(content.get('bodies', []))} Körper eingefügt – an den Pfeilen "
                                     "verschieben, Enter übernimmt", 5000)

    def _select_all_sketch(self) -> None:
        session = self._sketch_session()
        if session is not None:
            self._live["form"].select_tool("Auswählen")
            session.canvas.select_all()
            session.redraw()

    # --- Prüfen: Eigenschaften und Schnittanalyse -----------------------------------

    def body_properties(self) -> list[dict]:
        """Kennwerte der sichtbaren Körper: Volumen, Oberfläche, Schwerpunkt und Abmessungen."""
        state = self.timeline.state
        rows = []
        for name, shape in state.bodies.items():
            if name in state.helpers or name in self._hidden_bodies:
                continue
            bounds = solids._bounds(shape)
            rows.append({"name": name, "volume": solids._volume(shape), "area": solids._area(shape),
                         "centre": handles._volume_centre(shape),
                         "size": tuple(bounds[i + 3] - bounds[i] for i in range(3))})
        return rows

    def _show_properties(self) -> None:
        self._finish_live(True)
        rows = self.body_properties()
        if not rows:
            QMessageBox.information(self, "Eigenschaften", "Es gibt keinen sichtbaren Körper.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Eigenschaften")
        dialog.resize(760, 320)
        material = QComboBox()
        material.addItems(list(_DENSITIES))
        table = QTreeWidget()
        table.setHeaderLabels(["Körper", "Volumen (cm³)", "Oberfläche (cm²)", "Masse (g)", "Abmessungen (mm)",
                               "Schwerpunkt (mm)"])
        table.setRootIsDecorated(False)

        assigned = self.timeline.view_state.get("material", {})     # Physisches Material je Körper

        def fill() -> None:
            table.clear()
            number = lambda value, digits=2: f"{value:.{digits}f}".replace(".", ",")
            total_volume = total_mass = 0.0
            for row in rows:
                # eigener Werkstoff des Körpers, sonst der oben gewählte
                density = _DENSITIES.get(assigned.get(row["name"], ""), _DENSITIES[material.currentText()])
                volume = row["volume"] / 1000.0                 # mm³ -> cm³
                total_volume, total_mass = total_volume + volume, total_mass + volume * density
                QTreeWidgetItem(table, [row["name"], number(volume, 3), number(row["area"] / 100.0),
                                        number(volume * density), " × ".join(number(v) for v in row["size"]),
                                        " / ".join(number(v) for v in row["centre"])])
            if len(rows) > 1:
                QTreeWidgetItem(table, ["Summe", number(total_volume, 3), "", number(total_mass), "", ""])
            for column in range(6):
                table.resizeColumnToContents(column)

        material.currentTextChanged.connect(lambda _text: fill())
        fill()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        top = QHBoxLayout()
        top.addWidget(QLabel("Werkstoff für die Masse (sonst Physisches Material des Körpers):"))
        top.addWidget(material, 1)
        layout.addLayout(top)
        layout.addWidget(table)
        layout.addWidget(close)
        dialog.exec()

    def _section_clicked(self) -> None:
        """Schaltet die Schnittanalyse weiter: aus → X → Y → Z → aus."""
        order = [None, "X", "Y", "Z"]
        # Der Klick hat den Haken schon umgeschaltet: war er vorher gesetzt, lief ein Schnitt
        current = self._section["axis"] if not self._section_action.isChecked() else None
        following = order[(order.index(current) + 1) % len(order)]
        self._section_action.setChecked(following is not None)
        if following is not None:
            self._section["axis"] = following
            # Die Ebene beginnt mitten im Modell
            boxes = [solids._bounds(shape) for shape in self.timeline.state.bodies.values()]
            index = "XYZ".index(following)
            self._section["offset"] = (round((min(b[index] for b in boxes) + max(b[index + 3] for b in boxes)) / 2, 1)
                                       if boxes else 0.0)
        self._apply_section()
        self._update_handle()

    def _apply_section(self) -> None:
        """Überträgt die Schnittebene in die Ansicht (sichtbar bleibt die Seite gegen die Achsrichtung)."""
        if not self._section_action.isChecked():
            self.viewport.set_clip(None)
            self.viewport.set_geometry("section_cap", np.zeros((0, 3), dtype=np.float32))
            self.viewport.show_info("")
            return
        axis = solids.AXES[self._section["axis"]]
        offset = self._section["offset"]
        self.viewport.set_clip((-axis[0], -axis[1], -axis[2], offset))
        # Schnittfläche: die Ebene, mit jedem sichtbaren Körper verschnitten, füllt den Anschnitt
        state = self.timeline.state
        caps = [solids.section_face(shape, axis, offset) for name, shape in state.bodies.items()
                if name in self._picks]
        self.viewport.set_geometry("section_cap", _stack([mesh.pick_mesh(cap).triangles for cap in caps
                                                          if cap is not None]))
        text = f"{offset:.1f}".replace(".", ",")
        self.viewport.show_info(f"Schnittanalyse: {self._section['axis']} = {text} mm – "
                                "Pfeil ziehen verschiebt die Ebene")

    # --- Baugruppe: Bewegung und Kollision -----------------------------------------

    def _collision_text(self) -> str:
        state = self.timeline.state
        visible = [name for name in state.bodies if name in self._picks]
        if Timeline.crash_guard and guard.available():
            # bei sehr vielen oder verwickelten Körpern kann die Prüfung endlos dauern: mit Zeitgrenze rechnen
            status, hits = guard.run(lambda: solids.interferences(state, visible), seconds=30.0, want_result=True)
            if status == guard.TIMEOUT:
                return "Kollision: Prüfung nach 30 s abgebrochen (zu viele oder zu verwickelte Körper)"
            if status != guard.OK:
                return "Kollision: Prüfung fehlgeschlagen (der Geometriekern kam mit den Körpern nicht zurecht)"
        else:
            hits = solids.interferences(state, visible)
        if not hits:
            return "Keine Kollision"
        return "Kollision: " + "; ".join(f"{a} / {b} ({volume / 1000:.2f} cm³)".replace(".", ",")
                                         for a, b, volume in hits)

    def _show_collisions(self) -> None:
        self._finish_live(True)
        text = self._collision_text()
        self.statusBar().showMessage(text, 15000)
        QMessageBox.information(self, "Kollision", text.replace("; ", "\n").replace("Kollision: ", "Es durchdringen "
                                                                                    "sich:\n"))

    def _toggle_motion(self, on: bool) -> None:
        """Startet oder beendet die Bewegung der Gelenke; am Ende stehen alle wieder auf ihrem Wert."""
        if not on:
            if self._motion is not None:
                self._motion["timer"].stop()
                for index, (angle, offset) in self._motion["start"].items():
                    self.timeline.features[index].angle, self.timeline.features[index].offset = angle, offset
                first = min(self._motion["start"])
                self._motion = None
                self.timeline.recompute(start=first)
                self._refresh()
                self.viewport.show_info("")
            return
        self._finish_live(True)
        moving = {index: (f.angle, f.offset) for index, f in enumerate(self.timeline.features[:self.timeline.marker])
                  if isinstance(f, solids.Joint) and not f.suppressed and f.joint != solids.JOINT_KINDS[0]}
        if not moving:
            self._motion_action.setChecked(False)
            QMessageBox.information(self, "Bewegen", "Es gibt kein Drehgelenk und keinen Schieber, die sich "
                                    "bewegen könnten.")
            return
        boxes = [solids._bounds(shape) for shape in self.timeline.state.bodies.values()]
        travel = 0.25 * max(max(b[i + 3] - b[i] for i in range(3)) for b in boxes)
        timer = QTimer(self)
        timer.setInterval(60)
        self._motion = {"start": moving, "step": 0, "timer": timer, "travel": travel}
        timer.timeout.connect(self._motion_step)
        timer.start()

    def _motion_step(self) -> None:
        """Ein Bild der Bewegung: Drehgelenke laufen um, Schieber fahren hin und her."""
        if self._motion is None:
            return
        self._motion["step"] += 1
        phase = self._motion["step"] / _MOTION_STEPS
        for index, (angle, offset) in self._motion["start"].items():
            joint = self.timeline.features[index]
            limits = joint.limits()
            swing = (1 - math.cos(2 * math.pi * phase)) / 2        # 0 → 1 → 0 über einen Durchlauf
            if joint.joint == solids.JOINT_KINDS[1]:
                # mit Grenzen pendelt das Gelenk zwischen ihnen, sonst läuft es um
                joint.angle = limits[0] + (limits[1] - limits[0]) * swing if limits else angle + 360.0 * phase
            else:
                joint.offset = (limits[0] + (limits[1] - limits[0]) * swing if limits
                                else offset + self._motion["travel"] * math.sin(2 * math.pi * phase))
        self.timeline.recompute(start=min(self._motion["start"]))
        self._refresh()
        self.viewport.show_info("Bewegung läuft – " + self._collision_text())

    def _create_component(self) -> None:
        """Fasst die gewählten Körper zu einer Komponente zusammen."""
        bodies = list(dict.fromkeys(body for _kind, body, _index in self._selection))
        if not bodies:
            QMessageBox.information(self, "Komponente", "Bitte zuerst einen oder mehrere Körper anklicken "
                                    "(Strg+Klick wählt weitere).")
            return
        # Ein Schritt in der Timeline, auch bei mehreren Körpern
        self._create(solids.Component(body=bodies[0], bodies=bodies[1:]))

    def _create_joint(self) -> None:
        """Fügt den Körper der ersten gewählten Fläche an die zweite gewählte Fläche."""
        chosen = list(self._selection)
        if len(chosen) != 2 or chosen[0][1] == chosen[1][1]:
            QMessageBox.information(self, "Gelenk", "Bitte zwei Bezüge an zwei verschiedenen Körpern wählen – "
                                    "Flächen oder Kanten (z. B. den Rand einer Bohrung): zuerst am Körper, der "
                                    "bewegt werden soll, dann mit Strg+Klick dort, wo er gefügt wird.")
            return
        points = [list(self._picks[body].face_points[index] if kind == "face" else
                       self._picks[body].edge_points[index] if kind == "edge" else
                       (float(c) for c in self._vertex_points(body)[index]))
                  for kind, body, index in chosen]
        self._create(solids.Joint(body=chosen[0][1], target=chosen[1][1], face_points=points,
                                  kinds=[kind for kind, _body, _index in chosen]))

    def _create_split(self) -> None:
        """Teilt Körper: an der gewählten ebenen Fläche (etwas nach innen versetzt), an gewählten (auch gekrümmten)
        Flächen eines Körpers oder Flächenkörpers, die verlängert werden, oder an einer Hauptebene."""
        face = self._selected_planar_face()
        if face is not None:
            # die Fläche selbst ist der Rand des Körpers: erst innen wird geteilt
            self._create(solids.SplitBody(frame=sketch.face_frame(face), offset=-5.0))
            return
        tool_faces = self._selected_tool_faces()
        if tool_faces is not None:
            body, points = tool_faces
            self._create(solids.SplitBody(tool_body=body, tool_points=points))
            return

        def chosen(choice: dict) -> None:
            feature = solids.SplitBody()
            if "tool_face" in choice:
                feature.tool_body, feature.tool_points = choice["tool_face"]
            elif "frame" in choice:
                feature.frame, feature.offset = choice["frame"], -5.0
            elif "base" in choice:
                feature.base = choice["base"]
            else:
                feature.plane = choice["plane"]
            self._create(feature)

        self._finish_live(True)
        self._start_plane_pick(chosen, "Trennwerkzeug wählen: Hauptebene, Konstruktionsebene, Körperfläche "
                                       "(auch gewölbt) oder Flächenkörper anklicken (Esc bricht ab)", any_face=True)

    def _selected_tool_faces(self) -> tuple[str, list] | None:
        """Gewählte Flächen eines einzigen Körpers oder Flächenkörpers als Trennwerkzeug: (Name, Merkpunkte);
        None, wenn nichts davon (oder Flächen mehrerer Körper) gewählt ist."""
        state = self.timeline.state
        owners = {body for _kind, body, _index in self._selection}
        if not self._selection or len(owners) != 1 or any(kind != "face" for kind, _b, _i in self._selection):
            return None
        body = next(iter(owners))
        shape = state.bodies.get(body, state.surfaces.get(body))
        if shape is None:
            return None
        faces = ft.faces_of(shape)
        points = [list(ft.face_point(faces[index])) for _kind, _body, index in self._selection if index < len(faces)]
        return (body, points) if points else None

    def _selected_faces(self) -> list:
        """Alle gewählten Flächen als Formen (ohne Kanten oder Punkte in der Auswahl: sonst leer)."""
        if any(kind != "face" for kind, _body, _index in self._selection):
            return []
        found = []
        for _kind, body, index in self._selection:
            faces = ft.faces_of(self.timeline.state.bodies[body]) if body in self.timeline.state.bodies else []
            if index < len(faces):
                found.append(faces[index])
        return found

    def _create_axis(self) -> None:
        """Konstruktionsachse: durch eine gerade Kante, die Achse einer Bohrung/Welle oder zwei Eckpunkte."""
        state = self.timeline.state
        points = None
        faces = self._selected_faces()
        edges = [(body, index) for kind, body, index in self._selection if kind == "edge"]
        corners = [(body, index) for kind, body, index in self._selection if kind == "vertex"]
        if len(faces) == 1:
            points = solids.axis_of_face(faces[0])
        elif len(edges) == 1 and len(self._selection) == 1:
            edge = ft.unique_edges(state.bodies[edges[0][0]])[edges[0][1]]
            curve = BRepAdaptor_Curve(edge)
            if curve.GetType() == GeomAbs_Line:
                ends = [curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())]
                points = [[p.X(), p.Y(), p.Z()] for p in ends]
            elif curve.GetType() == GeomAbs_Circle:
                # Kreiskante (z. B. Rand einer Bohrung): Achse durch ihre Mitte senkrecht zum Kreis
                circle = curve.Circle()
                centre, along = circle.Location(), circle.Axis().Direction()
                points = [[centre.X(), centre.Y(), centre.Z()],
                          [centre.X() + along.X() * 10, centre.Y() + along.Y() * 10, centre.Z() + along.Z() * 10]]
        elif len(corners) == 2 and len(self._selection) == 2:
            points = [[float(c) for c in self._vertex_points(body)[index]] for body, index in corners]
        if points is None:
            QMessageBox.information(self, "Achse", "Vor dem Werkzeug wählen: eine gerade oder runde Kante, eine "
                                    "zylindrische Fläche (Bohrung, Welle) oder zwei Eckpunkte.")
            return
        self._create(solids.ConstructionAxis(start=list(points[0]), end=list(points[1])))

    def _create_plane(self) -> None:
        """Legt eine Konstruktionsebene an: versetzt zur gewählten ebenen Fläche oder zu einer Hauptebene."""
        faces = self._selected_faces()
        if len(faces) == 2:
            # zwei ebene Flächen: Mittelebene (bei schrägen Flächen die winkelhalbierende Ebene)
            frames = [sketch.face_frame(face) for face in faces]
            if None in frames:
                QMessageBox.information(self, "Ebene", "Für die Mittelebene zwei ebene Flächen wählen.")
                return
            try:
                frame = sketch.midplane_frame(*frames)
            except ft.FeatureError as error:
                QMessageBox.information(self, "Ebene", str(error))
                return
            self._create(sketch.ConstructionPlane(frame=frame, offset=0.0))
            return
        if len(faces) == 1 and sketch.face_frame(faces[0]) is None:
            # gekrümmte Fläche: Tangentialebene in ihrer Mitte
            frame = sketch.tangent_frame(faces[0])
            if frame is not None:
                self._create(sketch.ConstructionPlane(frame=frame, offset=0.0))
                return
        face = self._selected_planar_face()
        if face is not None:
            self._create(sketch.ConstructionPlane(frame=sketch.face_frame(face)))
            return
        # Drei Eckpunkte gewählt: die Ebene geht durch alle drei
        corners = [(body, index) for kind, body, index in self._selection if kind == "vertex"]
        if len(corners) == 3 and len(self._selection) == 3:
            frame = sketch.points_frame(*[tuple(float(c) for c in self._vertex_points(body)[index])
                                          for body, index in corners])
            if frame is None:
                QMessageBox.information(self, "Ebene", "Die drei Punkte liegen auf einer Geraden.")
                return
            self._create(sketch.ConstructionPlane(frame=frame, offset=0.0))
            return
        # Zwei gerade Kanten gewählt: die Ebene geht durch beide
        edges = [(body, index) for kind, body, index in self._selection if kind == "edge"]
        if len(edges) == 2 and len(self._selection) == 2:
            shapes = [ft.unique_edges(self.timeline.state.bodies[body])[index] for body, index in edges]
            frame = sketch.edges_frame(*shapes)
            if frame is None:
                QMessageBox.information(self, "Ebene", "Durch diese beiden Kanten lässt sich keine Ebene legen "
                                        "(sie müssen gerade sein und dürfen nicht auf einer Linie liegen).")
                return
            self._create(sketch.ConstructionPlane(frame=frame, offset=0.0))
            return

        def chosen(choice: dict) -> None:
            feature = sketch.ConstructionPlane()
            if "frame" in choice:
                feature.frame = choice["frame"]
            elif "base" in choice:
                # von einer Konstruktionsebene aus: deren jetzige Lage wird zur Ausgangslage
                axes = self.timeline.state.planes[choice["base"]]
                feature.frame = [axes.Location().X(), axes.Location().Y(), axes.Location().Z(),
                                 axes.Direction().X(), axes.Direction().Y(), axes.Direction().Z(),
                                 axes.XDirection().X(), axes.XDirection().Y(), axes.XDirection().Z()]
            else:
                feature.plane = choice["plane"]
            self._create(feature)

        self._finish_live(True)
        self._start_plane_pick(chosen, "Ausgangsebene wählen: Hauptebene, Konstruktionsebene oder ebene "
                                       "Körperfläche anklicken (Esc bricht ab)")

    def _start_sketch(self, index: int, is_new: bool, reference: list | None, face_edges: list | None = None) -> None:
        """Schaltet die Ansicht in den Zeichenmodus für die Skizze `index`.

        `face_edges`: Kanten der Körperfläche, auf der eine neue Skizze liegt – sie werden projiziert."""
        feature = self.timeline.features[index]
        session = SketchSession(self.viewport, feature, [list(entity) for entity in feature.entities],
                                reference, [list(rule) for rule in feature.constraints],
                                axes=self.timeline.state.frames.get(feature.name),
                                resolver=lambda text: pm.evaluate(text, self.timeline.parameter_values),
                                construction=list(feature.construction), edge_picker=self._edges_at,
                                vertex_picker=self._vertex_at,
                                texts=[list(entry) for entry in feature.texts],
                                centerlines=list(feature.centerlines), projected=list(feature.projected))
        if face_edges:
            session.project_face_outline(face_edges)        # wie Fusion: der Flächenumriss zählt zum Profil
        # Wie in Fusion fängt die Skizze auch die Mitten von Körperkreisen (Zylinder, Bohrungen, Rundungen), deren
        # Achse senkrecht auf der Skizzenebene steht – in Skizzenkoordinaten auf die Ebene gelegt
        normal = np.array(session.normal, dtype=float)
        session.canvas.body_centers = [
            (session.local(centre)[0], session.local(centre)[1], radius)
            for shape in self.timeline.state.bodies.values() for centre, radius, axis in ft.circle_centers(shape)
            if abs(abs(float(np.dot(axis, normal))) - 1.0) < 1e-6]
        self._begin_live(index, is_new, SketchPanel(feature, session))
        self._live["session"] = session
        self._ribbon.show_context(SKETCH_TAB)       # wie Fusion: beim Skizzieren der Reiter SKIZZE
        self.viewport.sketch_input = session
        self.viewport.refresh_ground()          # beim Skizzieren gilt das Raster der Skizzenebene
        # Senkrecht auf die Ebene schauen und den Bereich um Zeichenhilfe bzw. Ursprung zeigen
        self.viewport.look_along(session.normal)
        outline = [point for polyline in (reference or []) for point in polyline]
        outline += [point for entity in session.canvas.entities for point in sketch3d.outline_points(entity)]
        if not outline:
            outline = [(-60.0, -40.0), (60.0, 40.0)]
        self.viewport.fit(np.array([session.world(*point) for point in outline], dtype=np.float32))
        session.show()

    def _selected_planar_face(self):
        """Die gewählte Fläche, wenn genau eine ebene Fläche gewählt ist (sonst None)."""
        faces = [(body, index) for kind, body, index in self._selection if kind == "face"]
        if len(faces) != 1 or faces[0][0] not in self.timeline.state.bodies:
            return None
        body, index = faces[0]
        all_faces = ft.faces_of(self.timeline.state.bodies[body])
        if index >= len(all_faces) or sketch.face_frame(all_faces[index]) is None:
            return None
        return all_faces[index]

    def _create_hole(self) -> None:
        feature = solids.Hole(require_pick=True)       # ohne Fläche/Punkte: wartet auf den Klick auf eine Fläche
        face = self._selected_planar_face()
        if face is not None:
            feature.frame = sketch.face_frame(face)     # Position 0/0 ist die Mitte der Fläche
        # Skizzenpunkte werden nicht mehr automatisch übernommen (wie die Extrusion nichts von selbst wählt):
        # die Skizze lässt sich im Feld „An Punkten der Skizze“ bewusst auswählen.
        self._create(feature)

    def _create_revolve(self) -> None:
        sketches = self._active_sketch_names()
        if not sketches:
            QMessageBox.information(self, "Drehen", "Bitte zuerst eine Skizze anlegen.")
            return
        feature = solids.Revolve(sketch=sketches[-1], operation=self._default_operation())
        if self._pick_regions(feature):
            self._create(feature)

    def _pick_regions(self, feature) -> bool:
        """Lässt bei Skizzen mit mehreren Bereichen das Profil anklicken; False bei Abbruch.

        Vorgewählt sind die Bereiche, die „Mit Löchern“ ergeben würde. Bleibt es dabei,
        wird nichts gemerkt und das Feature folgt weiter der Skizze.
        """
        state = self.timeline.state
        if feature.sketch not in state.profiles or feature.sketch not in state.frames:
            return True
        regions = sketch.regions_2d(state.profiles[feature.sketch], state.frames[feature.sketch])
        if len(regions) < 2:
            return True
        default = set()
        if feature.sketch in state.solid_profiles:
            kept = {region["point"] for region in
                    sketch.regions_2d(state.solid_profiles[feature.sketch], state.frames[feature.sketch])}
            default = {index for index, region in enumerate(regions) if region["point"] in kept}
        picker = RegionPicker(regions, default, self)
        if picker.exec() != QDialog.DialogCode.Accepted:
            return False
        chosen = picker.chosen()
        if set(chosen) != default:
            feature.regions = ft.PROFILE_MODES[2]
            feature.region_points = [list(regions[index]["point"]) for index in chosen]
        return True

    # --- Leinwand auf einer Körperfläche (Runde 81) --------------------------------------------------------------

    @staticmethod
    def _canvas_on_face(feature, face, point) -> dict | None:
        """Werte, die eine Leinwand auf die ebene Fläche `face` legen: Mitte an `point`, Breite so, dass das Bild
        auf die Fläche passt. None bei gekrümmten Flächen."""
        frame = sketch.face_frame(face)
        if frame is None:
            return None
        origin, normal, x_dir = np.array(frame[0:3]), np.array(frame[3:6]), np.array(frame[6:9])
        y_dir = np.cross(normal, x_dir)
        # Ausdehnung der Fläche in ihren eigenen Richtungen (über die Eckpunkte ihrer Kanten)
        corners = np.array([ft.edge_point(edge) for edge in ft.unique_edges(face)] +
                           [list(vertex) for vertex in _vertex_positions(face)]) - origin
        span_x = float(np.ptp(corners @ x_dir)) if len(corners) else feature.width
        span_y = float(np.ptp(corners @ y_dir)) if len(corners) else feature.width
        width = max(1.0, 0.9 * min(span_x, span_y / max(feature.aspect, 1e-6)))
        offset = np.array(point) - origin
        return {"frame": frame, "offset": 0.0, "center_x": float(offset @ x_dir),
                "center_y": float(offset @ y_dir), "width": width}

    def _place_canvas(self, index: int, feature, face, point) -> None:
        values = self._canvas_on_face(feature, face, point)
        if values is None:
            self.viewport.show_info("Leinwand: bitte eine ebene Fläche anklicken.")
            return
        form = self._live["form"]
        for name in ("offset", "center_x", "center_y", "width"):
            form.set_value(name, values[name])
        form.set_list("frame", values["frame"])
        self.timeline.update(index, **values)
        self.viewport.show_info("Leinwand liegt auf der Fläche – an den Pfeilen verschieben, am Bogen drehen, "
                                "am hellen Pfeil skalieren.")
        self._refresh()

    # --- Kombinieren wie in Fusion: Auswahlfelder, Klicks, farbige Markierung ---------------------------------

    def _combine_field(self) -> str:
        """Aktives Auswahlfeld: was der Nutzer angeklickt hat, sonst ohne Ziel das Ziel, danach die Werkzeuge."""
        feature = self.timeline.features[self._live["index"]]
        return self._live.get("combine_field") or ("target" if not feature.target else "tools")

    def _combine_field_clicked(self, name: str) -> None:
        if self._live is not None and isinstance(self.timeline.features[self._live["index"]], solids.CombineBodies):
            self._live["combine_field"] = name
            self._sync_combine_form()

    def _combine_pick(self, index: int, feature, before, ray) -> bool:
        """Klick in die Ansicht beim Kombinieren. Gesucht wird im Modell vor dem Schritt – so lassen sich auch
        Werkzeugkörper treffen, die im Ergebnis schon weggeschnitten sind. Das aktive Feld bekommt den Körper:
        Zielkörper ersetzt das Ziel (danach ist das Werkzeugfeld dran), Werkzeugkörper nimmt hinzu bzw. heraus."""
        if ray is None:
            return False
        field = self._combine_field()
        # Im Werkzeugfeld zählt der Zielkörper nicht: der Klick geht durch ihn hindurch und trifft den Körper
        # dahinter bzw. darin (z. B. einen Bolzen, der im Ziel steckt – Runde 78, sonst kaum anklickbar)
        skip = {feature.target} if field == "tools" else set()
        found = [(math.dist(ray[0], at), name) for name, shape in before.bodies.items()
                 if name not in before.frozen and name not in self._hidden_bodies and name not in skip
                 for _face, at in [ft.face_on_ray(shape, *ray) or (None, None)] if at is not None]
        if not found:
            return True                 # Klick ins Leere: das Werkzeug bleibt, wie es ist
        self._combine_choose(index, feature, min(found)[1])
        return True

    def _combine_choose(self, index: int, feature, body: str) -> None:
        """Einen Körper in das aktive Feld übernehmen (Klick in der Ansicht oder auf seinen Namen im Browser)."""
        tools = feature.tool_names()
        field = self._combine_field()
        if field == "target":
            if body != feature.target:
                self.timeline.update(index, target=body, tools=[name for name in tools if name != body], tool="")
            self._live["combine_field"] = "tools"           # wie Fusion: danach die Werkzeugkörper
        elif body == feature.target:
            self.viewport.show_info(f"{body} ist der Zielkörper – für ein anderes Ziel erst das Feld "
                                    "„Zielkörper“ anklicken.")
            return
        else:
            self.timeline.update(index, tools=[name for name in tools if name != body] if body in tools
                                 else [*tools, body], tool="")
        self._sync_combine_form()
        self._refresh()

    def _sync_combine_form(self) -> None:
        """Auswahlfelder, aktives Feld, Farben und Hinweis in der Ansicht auf den Stand des Schritts bringen."""
        feature = self.timeline.features[self._live["index"]]
        form = self._live["form"]
        if not isinstance(form, FeatureForm):
            return
        field = self._combine_field()
        form.set_selection("target", [feature.target])
        form.set_selection("tools", feature.tool_names())
        form.set_active_field(field)
        form.set_field_color("target", _COMBINE_TARGET_COLOR)
        form.set_field_color("tools", _COMBINE_TOOL_COLORS.get(feature.operation, _COMBINE_TARGET_COLOR))
        tools = ", ".join(feature.tool_names()) or "–"
        task = ("Zielkörper anklicken" if field == "target"
                else "Werkzeugkörper anklicken (nochmals anklicken nimmt ihn heraus)")
        self.viewport.show_info(f"Kombinieren ({feature.operation}): Ziel {feature.target or '–'} · Werkzeuge "
                                f"{tools}  –  {task}")

    def _show_combine_marks(self, feature) -> None:
        """Werkzeugkörper durchscheinend in der Farbe der Operation – so, wie sie vor dem Schritt aussehen, also
        auch dann sichtbar, wenn das Ergebnis sie schon verbraucht hat (Ausschneiden, Vereinigen)."""
        before = self.timeline.state_before(self._live["index"])
        picks = []
        for name in feature.tool_names():
            shape = before.bodies.get(name)
            if shape is None:
                continue
            entry = self._pick_cache.pop(id(shape), None) or (shape, mesh.pick_mesh(shape))
            self._pick_cache[id(shape)] = entry
            picks.append(entry[1])
        if not picks:
            self.viewport.set_operation_preview(None)
            self._sync_combine_form()
            return
        self.viewport.set_operation_preview(_stack([pick.triangles for pick in picks]),
                                            _stack([pick.segments for pick in picks]),
                                            _COMBINE_TOOL_COLORS.get(feature.operation, _COMBINE_TARGET_COLOR))
        self._sync_combine_form()           # Farbe und Hinweis folgen einer geänderten Operation

    def _create_combine(self) -> None:
        bodies = self._body_names()
        if len(bodies) < 2:
            QMessageBox.information(self, "Kombinieren", "Dafür werden mindestens zwei Körper gebraucht.")
            return
        # Ohne Vorauswahl ist noch nichts festgelegt: erst den Zielkörper, dann die Werkzeugkörper anklicken (wie
        # Fusion). Vorher gewählte Körper übernimmt die Auswahl-Übernahme (Ziel = erster, Werkzeuge = übrige).
        self._create(solids.CombineBodies())

    # --- Rückgängig und Wiederholen ---------------------------------------

    def _remember(self) -> None:
        """Merkt den Entwurf vor einer Änderung, damit sie sich rückgängig machen lässt."""
        self._finish_live(True)     # ein noch offenes Werkzeug gilt als bestätigt
        self._undo_stack.append(self.timeline.to_document())
        del self._undo_stack[:-_UNDO_LIMIT]
        self._redo_stack.clear()

    def _edit_parameters(self) -> None:
        """Öffnet die Parameterliste; das Modell läuft bei jeder Änderung mit, Abbrechen stellt alles zurück."""
        self._remember()
        before = dict(self.timeline.parameters)

        def apply(parameters: dict, renames: dict) -> tuple[dict, dict]:
            # Umbenannte Parameter in den Ausdrücken aller Features nachziehen
            for old, new in renames.items():
                for feature in self.timeline.features:
                    feature.expressions = {name: pm.rename(expression, old, new)
                                           for name, expression in feature.expressions.items()}
                    # auch die Ausdrücke der Skizzenmaße (fünfter Eintrag eines Maßes)
                    for rule in getattr(feature, "constraints", []):
                        if len(rule) > 4:
                            rule[4] = pm.rename(rule[4], old, new)
            self.timeline.set_parameters(parameters)
            self._refresh()
            return self.timeline.parameter_values, self.timeline.parameter_errors

        dialog = ParameterDialog(before, apply, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            # Abbrechen: der vor dem Öffnen gemerkte Entwurf kommt zurück (samt aller Ausdrücke)
            self.timeline = Timeline.from_document(self._undo_stack.pop())
            self._refresh()

    def _restore(self, source: list, target: list) -> None:
        self._finish_live(True)
        if not source:
            return
        target.append(self.timeline.to_document())
        self.timeline = Timeline.from_document(source.pop())
        self._refresh()

    def _undo(self) -> None:
        self._restore(self._undo_stack, self._redo_stack)

    def _redo(self) -> None:
        self._restore(self._redo_stack, self._undo_stack)

    def _create_extrude(self) -> None:
        sketches = self._active_sketch_names()
        faces = self._selected_planar_faces()
        if faces and not self._selected_profiles:
            # wie in Fusion: eine gewählte ebene Körperfläche wird selbst aufgezogen (ohne Skizze)
            self._finish_live(True)
            self._create(ft.Extrude(sketch="", face_points=faces, distance=0.0, operation=ft.AUTO_OPERATION))
            return
        if not sketches and not self.timeline.state.bodies:
            QMessageBox.information(self, "Extrusion", "Bitte zuerst eine Skizze anlegen oder eine Körperfläche wählen.")
            return
        self._finish_live(True)
        if self._selected_profiles:
            # das Profil wurde schon im Modell angeklickt: gleich aufziehen
            picked, self._selected_profiles = self._selected_profiles, []
            self._create(self._extrude_of(picked))
            return
        # Wie gewünscht: nichts automatisch wählen – die Extrusion öffnet leer, Profile und ebene Körperflächen
        # werden angeklickt (jeder Klick nimmt dazu bzw. heraus); erst dann erscheint der Pfeil
        self._clear_selection()
        self._create(ft.Extrude(sketch="", distance=0.0, operation=self._default_operation()))
        self.viewport.show_info("Extrusion: Profile oder ebene Körperflächen anklicken – dann am Pfeil ziehen oder "
                                "eine Länge eintippen")

    # --- Runde 58: restliche Fusion-Werkzeuge ---------------------------------------

    def _create_plane_along_path(self) -> None:
        """Ebene entlang Pfad: auf dem Pfad der zuletzt aktiven Skizze (Stelle danach im Eigenschaftenfeld)."""
        sketches = self._active_sketch_names()
        if not sketches:
            QMessageBox.information(self, "Ebene entlang Pfad", "Bitte zuerst eine Skizze mit dem Pfad anlegen.")
            return
        self._finish_live(True)
        self._create(mt.PlaneAlongPath(path=sketches[-1]))

    def _insert_canvas(self) -> None:
        """Leinwand: Bilddatei wählen; das Bild wird im Entwurf gespeichert."""
        start = str(self.data_panel.folder) if self.data_panel.folder else ""
        path, _ = QFileDialog.getOpenFileName(self, "Leinwand", start, "Bilder (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self.insert_canvas_file(path)

    def insert_canvas_file(self, path: str) -> bool:
        """Legt eine Leinwand mit dem Bild an (auch für Tests ohne Dateidialog); große Bilder werden verkleinert."""
        import base64
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from PySide6.QtGui import QImage
        image = QImage(path)
        if image.isNull():
            QMessageBox.warning(self, "Leinwand", "Das Bild ließ sich nicht lesen.")
            return False
        if max(image.width(), image.height()) > _CANVAS_PIXELS:
            image = image.scaled(_CANVAS_PIXELS, _CANVAS_PIXELS, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        encoded = base64.b64encode(bytes(data)).decode("ascii")
        self._create(mt.Canvas(image=encoded, aspect=image.height() / max(image.width(), 1)))
        return True

    def _toggle_isolines(self) -> None:
        """Isokurven ein (Anzahl je Richtung abfragen) bzw. wieder aus."""
        if getattr(self, "_isolines", 0):
            self._isolines = 0
        else:
            count, accepted = QInputDialog.getInt(self, "Isokurven", "Linien je Richtung und Fläche:", 5, 1, 30)
            if not accepted:
                return
            self._isolines = count
        self._show_extras(self.timeline.state, None)

    def _show_extras(self, state, faces) -> None:
        """Gelenkursprünge (kleines Achsenkreuz mit Pfeil), Leinwände und Isokurven anzeigen."""
        size = max(4.0, float(self.viewport._distance) * 0.03)
        lines = []
        for origin in state.joint_origins.values():
            point, normal = np.array(origin["point"]), np.array(origin["normal"])
            side = np.cross(normal, [1.0, 0.0, 0.0] if abs(normal[0]) < 0.9 else [0.0, 1.0, 0.0])
            side /= np.linalg.norm(side)
            other = np.cross(normal, side)
            lines += [point - side * size * 0.5, point + side * size * 0.5,
                      point - other * size * 0.5, point + other * size * 0.5,
                      point, point + normal * size * 1.2]
        self.viewport.set_geometry("joint_origins", np.array(lines, dtype=np.float32).reshape(-1, 3))
        # Leinwände: nur neu hochladen, wenn sich etwas geändert hat (die Bilder können groß sein)
        key = tuple((name, tuple(map(tuple, item["corners"])), item["opacity"], len(item["image"]))
                    for name, item in state.canvases.items() if name not in self._hidden_bodies)
        if key != getattr(self, "_canvas_key", None):
            self._canvas_key = key
            from PySide6.QtGui import QImage
            import base64
            canvases = []
            for name, item in state.canvases.items():
                if name in self._hidden_bodies:
                    continue                    # im Browser ausgeblendet
                image = QImage.fromData(base64.b64decode(item["image"]))
                if not image.isNull():
                    canvases.append((item["corners"], image, item["opacity"]))
            self.viewport.set_canvases(canvases)
        count = getattr(self, "_isolines", 0)
        if count:
            from ..core import analysis
            segments = [analysis.iso_lines(ft.faces_of(shape), count) for name, shape in state.bodies.items()
                        if name in self._picks]
            self.viewport.set_geometry("isolines", _stack(segments))
        else:
            self.viewport.set_geometry("isolines", np.zeros((0, 3), dtype=np.float32))

    def _box_select(self, x0: float, y0: float, x1: float, y1: float, additive: bool) -> None:
        """Rahmenauswahl wie in Fusion: von links nach rechts nur, was ganz im Rahmen liegt, von rechts nach links
        alles, was ihn berührt. Gewählt werden – je nach Auswahlfilter – Flächen, sonst Kanten bzw. Eckpunkte."""
        if self._plane_pick is not None or self._profile_pick is not None:
            return
        crossing = x1 < x0
        left, right, top, bottom = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        inside = lambda spots: ((spots[:, 0] >= left) & (spots[:, 0] <= right)
                                & (spots[:, 1] >= top) & (spots[:, 1] <= bottom))
        allowed = self._select_filter
        found = []
        for name, pick in self._picks.items():
            if "face" in allowed or "body" in allowed:
                spots = self.viewport.project_many(pick.triangles)
                hits = inside(spots).reshape(-1, 3)
                per_triangle = (_touches_rectangle(spots.reshape(-1, 3, 2), (left, top, right, bottom)) if crossing
                                else hits.all(axis=1))
                count = len(pick.face_points)
                faces = [number for number in range(count)
                         if (per_triangle[pick.triangle_face == number].any() if crossing
                             else len(per_triangle[pick.triangle_face == number])
                             and per_triangle[pick.triangle_face == number].all())]
                if "face" not in allowed and faces:
                    faces = list(range(count))           # Filter nur „Körper“: der ganze Körper
                found += [("face", name, number) for number in faces]
            elif "edge" in allowed and len(pick.segments):
                spots = self.viewport.project_many(pick.segments)
                hits = inside(spots).reshape(-1, 2)
                per_segment = (_touches_rectangle(spots.reshape(-1, 2, 2), (left, top, right, bottom)) if crossing
                               else hits.all(axis=1))
                for number in sorted(set(pick.segment_edge.tolist())):
                    own = per_segment[pick.segment_edge == number]
                    if (own.any() if crossing else own.all()):
                        found.append(("edge", name, int(number)))
            elif "vertex" in allowed:
                points = self._vertex_points(name)
                if len(points):
                    for number in np.nonzero(inside(self.viewport.project_many(points)))[0]:
                        found.append(("vertex", name, int(number)))
        if additive:
            self._selection += [item for item in found if item not in self._selection]
        else:
            self._selection = found
        self._show_selection()
        self.statusBar().showMessage(f"Rahmenauswahl: {len(found)} gewählt"
                                     + (" (berührt)" if crossing else " (ganz im Rahmen)"), 4000)

    # --- Runde 57: weitere Fusion-Werkzeuge ---------------------------------------

    def _create_pipe(self) -> None:
        """Rohr entlang der zuletzt aktiven Skizze."""
        sketches = self._active_sketch_names()
        if not sketches:
            QMessageBox.information(self, "Rohr", "Bitte zuerst eine Skizze mit dem Pfad anlegen.")
            return
        self._finish_live(True)
        self._create(ex.Pipe(path=sketches[-1], operation=self._default_operation()))

    def _selected_location(self) -> tuple[float, float, float] | None:
        """Raumpunkt der ersten Auswahl: Eckpunkt, Kantenmitte oder Merkpunkt der Fläche; sonst None."""
        for kind, body, index in self._selection:
            if body not in self._picks:
                continue
            if kind == "vertex":
                points = self._vertex_points(body)
                if index < len(points):
                    return tuple(float(c) for c in points[index])
            source = self._picks[body].edge_points if kind == "edge" else self._picks[body].face_points
            if index < len(source):
                return tuple(float(c) for c in source[index])
        return None

    def _create_point(self) -> None:
        """Konstruktionspunkt an der gewählten Stelle (sonst im Ursprung; die Lage lässt sich danach eintragen)."""
        where = self._selected_location() or (0.0, 0.0, 0.0)
        self._create(ex.ConstructionPoint(x=where[0], y=where[1], z=where[2]))

    def _chosen_bodies(self) -> list[str]:
        """Die gewählten Volumenkörper, sonst alle sichtbaren (ohne Hilfskörper)."""
        state = self.timeline.state
        chosen = [body for _kind, body, _index in self._selection if body in state.bodies]
        if chosen:
            return list(dict.fromkeys(chosen))
        return [name for name in state.bodies if name not in state.helpers and name not in self._hidden_bodies]

    def _choose_material(self) -> None:
        """Physisches Material als Werkzeug: einen Werkstoff für die gewählten bzw. alle sichtbaren Körper."""
        bodies = self._chosen_bodies()
        if not bodies:
            QMessageBox.information(self, "Physisches Material", "Es gibt keinen Körper.")
            return
        current = self.timeline.view_state.get("material", {}).get(bodies[0], next(iter(_DENSITIES)))
        names = list(_DENSITIES)
        chosen, ok = QInputDialog.getItem(self, "Physisches Material", f"Werkstoff für {', '.join(bodies[:4])}"
                                          + (" …" if len(bodies) > 4 else ""), names,
                                          names.index(current) if current in names else 0, False)
        if ok:
            for body in bodies:
                self._set_material(body, chosen)

    def _compute_all(self) -> None:
        """Alle berechnen (Fusion: Alle berechnen): das Modell von vorn neu aufbauen."""
        self._finish_live(True)
        self.timeline.recompute(0)
        self._refresh()
        problems = len(self.timeline.errors)
        self.statusBar().showMessage("Alles neu berechnet" + (f" – {problems} Schritt(e) mit Fehler" if problems
                                                              else " – ohne Fehler"), 6000)

    def centre_of_mass(self, bodies: list[str]) -> tuple[float, float, float, float] | None:
        """Massenschwerpunkt (x, y, z) und Masse in g der Körper; die Dichte kommt aus dem Physischen Material
        (ohne Angabe die erste Werkstoffwahl)."""
        state = self.timeline.state
        assigned = self.timeline.view_state.get("material", {})
        default = next(iter(_DENSITIES.values()))
        total, weighted = 0.0, np.zeros(3)
        for body in bodies:
            shape = state.bodies.get(body)
            if shape is None:
                continue
            mass = solids._volume(shape) / 1000.0 * _DENSITIES.get(assigned.get(body, ""), default)
            total += mass
            weighted += np.array(handles._volume_centre(shape)) * mass
        if total <= 0:
            return None
        centre = weighted / total
        return float(centre[0]), float(centre[1]), float(centre[2]), total

    def _show_centre_of_mass(self) -> None:
        """Zeigt den Schwerpunkt als Kreuz in der Ansicht und seine Lage im Hinweisfeld."""
        found = self.centre_of_mass(self._chosen_bodies())
        if found is None:
            QMessageBox.information(self, "Schwerpunkt", "Es gibt keinen Körper mit Volumen.")
            return
        x, y, z, mass = found
        size = max(2.0, float(self.viewport._distance) * 0.03)
        cross = [(x - size, y, z), (x + size, y, z), (x, y - size, z), (x, y + size, z), (x, y, z - size), (x, y, z + size)]
        self.viewport.set_geometry("measure", np.array(cross, dtype=np.float32))
        number = lambda value: f"{value:.2f}".replace(".", ",")
        self.viewport.show_info(f"Schwerpunkt: {number(x)} / {number(y)} / {number(z)} mm\nMasse: {number(mass)} g")

    def _insert_drawing(self) -> None:
        """DXF/SVG einfügen: Datei wählen, dann die Ebene anklicken; daraus wird eine neue Skizze."""
        start = str(self.data_panel.folder) if self.data_panel.folder else ""
        path, _ = QFileDialog.getOpenFileName(self, "DXF/SVG einfügen", start, "Zeichnungen (*.dxf *.svg)")
        if path:
            self.insert_drawing_file(path)

    def insert_drawing_file(self, path: str) -> bool:
        """Liest die Zeichnung und lässt die Ebene wählen (auch für Tests ohne Dateidialog)."""
        try:
            entities = inserts.read_drawing(path)
        except ft.FeatureError as error:
            QMessageBox.warning(self, "DXF/SVG einfügen", str(error))
            return False
        self._finish_live(True)
        self._start_plane_pick(lambda choice: self._sketch_on(choice, entities),
                               f"{len(entities)} Elemente aus {Path(path).name}: Ebene anklicken (Esc bricht ab)")
        return True

    def _look_at_selection(self) -> None:
        """Anschauen (Navigationsleiste): senkrecht auf die gewählte Fläche blicken, sonst von oben."""
        from ..core import analysis
        for kind, body, index in self._selection:
            shape = self.timeline.state.bodies.get(body)
            if kind != "face" or shape is None:
                continue
            faces = ft.faces_of(shape)
            if index < len(faces):
                try:
                    self.viewport.look_along(analysis.face_normal(faces[index]))
                except ValueError:
                    break
                self.viewport.view_changed.emit()
                return
        self.viewport.look_along((0.0, 0.0, 1.0))
        self.viewport.view_changed.emit()

    def _set_display_style(self, style: str) -> None:
        """Darstellung (Navigationsleiste): schattiert mit Kanten, schattiert oder Drahtgitter (wird gemerkt)."""
        self.viewport.display_style = style
        QSettings("WolfCAD", "WolfCAD").setValue("darstellung", style)
        self.viewport.update()

    def _set_selection_filter(self, kinds: set) -> None:
        """Auswahlfilter: nur noch die erlaubten Arten wählbar; schon Gewähltes anderer Art fällt heraus."""
        self._select_filter = set(kinds)
        kept = [hit for hit in self._selection if hit[0] in kinds or (hit[0] == "face" and "body" in kinds)]
        if kept != self._selection:
            self._selection = kept
            self._show_selection()

    def _create_from_sketch(self, kind) -> None:
        """Werkzeug, das die zuletzt aktive Skizze verarbeitet (Flächen extrudieren, drehen, flicken …)."""
        sketches = self._active_sketch_names()
        if not sketches:
            QMessageBox.information(self, kind.KIND, "Bitte zuerst eine Skizze anlegen.")
            return
        self._finish_live(True)
        self._create(kind(sketch=sketches[-1]))

    def _insert_mesh(self) -> None:
        """Netz einfügen: STL wählen, Dreiecke lesen und gepackt im Schritt speichern."""
        start = str(self.data_panel.folder) if self.data_panel.folder else ""
        path, _ = QFileDialog.getOpenFileName(self, "Netz einfügen", start, "STL-Netze (*.stl)")
        if not path:
            return
        try:
            triangles = ms.read_stl(path)
        except (ft.FeatureError, OSError) as error:
            QMessageBox.critical(self, "Netz einfügen", str(error))
            return
        self._finish_live(True)
        self._create(ms.MeshImport(source=path, data=ms.pack(triangles)))
        self.statusBar().showMessage(f"{len(triangles)} Dreiecke eingefügt", 6000)

    def _save_mesh(self, name: str | None) -> None:
        """Netzkörper als STL speichern (ohne Angabe: der gewählte bzw. einzige)."""
        meshes = self.timeline.state.meshes
        if name is None:
            chosen = [body for _kind, body, _index in self._selection if body in meshes]
            name = chosen[0] if chosen else (next(iter(meshes)) if len(meshes) == 1 else None)
        if name is None:
            QMessageBox.information(self, "Netz speichern", "Bitte einen Netzkörper wählen." if meshes
                                    else "Es gibt keinen Netzkörper.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Netz als STL speichern", f"{name}.stl", "STL (*.stl)")
        if not path:
            return
        if not path.lower().endswith(".stl"):
            path += ".stl"
        try:
            ms.write_stl(meshes[name], path)
        except OSError as error:
            QMessageBox.critical(self, "Netz speichern", str(error))
            return
        self.statusBar().showMessage(f"{name} gespeichert: {path}", 6000)

    def _create_two_sketches(self, kind, second: str) -> None:
        """Werkzeug aus zwei Skizzen: die vorletzte als Profil/erste, die letzte als Pfad bzw. zweite."""
        sketches = self._active_sketch_names()
        if len(sketches) < 2:
            QMessageBox.information(self, kind.KIND, "Bitte zuerst zwei Skizzen anlegen (Profil und Pfad bzw. "
                                                     "zwei Querschnitte).")
            return
        self._finish_live(True)
        self._create(kind(sketch=sketches[-2], **{second: sketches[-1]}))

    def _create_sketch_bend(self) -> None:
        """Blech: an der Linie der zuletzt aktiven Skizze biegen (Fusion: Biegen)."""
        sketches = self._active_sketch_names()
        if not sketches:
            QMessageBox.information(self, "Biegen", "Bitte zuerst eine Skizze mit einer Linie auf dem Blech anlegen.")
            return
        self._finish_live(True)
        self._create(sm.SketchBend(sketch=sketches[-1]))

    def _create_base_flange(self) -> None:
        """Blech: Flansch aus dem Profil der zuletzt aktiven Skizze (wie Fusions Flansch auf ein Profil)."""
        sketches = self._active_sketch_names()
        if not sketches:
            QMessageBox.information(self, "Flansch", "Bitte zuerst eine Skizze mit geschlossenem Profil anlegen.")
            return
        self._finish_live(True)
        self._create(sm.BaseFlange(sketch=sketches[-1]))

    def _profile_regions(self, sketches: list[str]) -> list[dict]:
        """Alle geschlossenen Bereiche der Skizzen: je Bereich Skizze, Innenpunkt und Dreiecke (flach und im Raum)."""
        state = self.timeline.state
        found = []
        for name in sketches:
            if name not in state.profiles or name not in state.frames:
                continue
            axes = state.frames[name]
            origin = np.array([axes.Location().X(), axes.Location().Y(), axes.Location().Z()])
            x_dir = np.array([axes.XDirection().X(), axes.XDirection().Y(), axes.XDirection().Z()])
            y_dir = np.array([axes.YDirection().X(), axes.YDirection().Y(), axes.YDirection().Z()])
            regions = sketch.regions_2d(state.profiles[name], axes)
            for region in regions:
                world = [origin + x_dir * u + y_dir * v for triangle in region["triangles"] for u, v in triangle]
                found.append({"sketch": name, "point": region["point"], "triangles": region["triangles"],
                              "world": np.array(world, dtype=np.float32).reshape(-1, 3), "count": len(regions),
                              "origin": origin, "x": x_dir, "y": y_dir, "normal": np.cross(x_dir, y_dir)})
        return found

    def _extrude_target(self, feature: ft.Extrude, before: ft.BuildState, face) -> dict | None:
        """Extrusion bis zu einer angeklickten Fläche (Fusion „Bis Objekt“): liegt die Fläche parallel vor oder hinter
        dem gewählten Profil, wird die Länge genau bis dorthin gesetzt; eine schräge Fläche wird Zielfläche („Bis
        Fläche“). None, wenn noch nichts gewählt ist oder die Fläche in der Ebene des Profils liegt (dann wird sie
        wie bisher dazugenommen) bzw. schon gewählt ist."""
        if not feature.sketch and not feature.face_points:
            return None
        marker = list(ft.face_point(face))
        if any(math.dist(marker, point[:3]) <= ft.SELECTION_TOLERANCE for point in feature.face_points):
            return None
        try:
            profile, normal = feature.profile_list(before)[0]
        except ft.FeatureError:
            return None
        frame = sketch.face_frame(face)
        start = np.array(ft.face_point(ft.faces_of(profile)[0] if ft.faces_of(profile) else profile))
        normal = np.array(normal, dtype=float)
        if frame is not None and abs(abs(float(np.dot(normal, frame[3:6]))) - 1.0) < 1e-6:
            distance = float(np.dot(np.array(frame[:3]) - start, normal)) - feature.start
            if abs(distance) < 1e-6:
                return None                     # gleiche Ebene: dazunehmen statt Ziel
            return {"extent": ft.EXTENTS[0], "distance": distance}
        return {"extent": ft.EXTENTS[3], "target_points": [marker]}

    def _profile_hit(self, x: float, y: float, regions: list) -> tuple[int, float] | None:
        """Wie _profile_at, dazu die Entfernung vom Auge (zum Vergleich mit einem Körper davor)."""
        eye, direction, _pixel = self.viewport.pick_ray(x, y)
        hits = []
        for number, region in enumerate(regions):
            slope = float(direction @ region["normal"])
            if abs(slope) < 1e-9:
                continue
            along = float((region["origin"] - eye) @ region["normal"]) / slope
            if along <= 0:
                continue
            offset = eye + along * direction - region["origin"]
            point = (float(offset @ region["x"]), float(offset @ region["y"]))
            if any(_inside_triangle(point, triangle) for triangle in region["triangles"]):
                hits.append((round(along, 4), len(region["triangles"]), number))
        if not hits:
            return None
        along, _size, number = min(hits)
        return number, along

    def _profile_at(self, x: float, y: float, regions: list | None = None) -> int | None:
        """Nummer des Bereichs unter dem Mauszeiger: der vorderste, bei gleicher Tiefe der kleinste (Insel vor Rahmen).

        Ohne Angabe gelten die Bereiche der laufenden Profilwahl.
        """
        eye, direction, _pixel = self.viewport.pick_ray(x, y)
        hits = []
        for number, region in enumerate(self._profile_pick["regions"] if regions is None else regions):
            slope = float(direction @ region["normal"])
            if abs(slope) < 1e-9:
                continue
            along = float((region["origin"] - eye) @ region["normal"]) / slope
            if along <= 0:
                continue
            offset = eye + along * direction - region["origin"]
            point = (float(offset @ region["x"]), float(offset @ region["y"]))
            if any(_inside_triangle(point, triangle) for triangle in region["triangles"]):
                hits.append((round(along, 4), len(region["triangles"]), number))
        return min(hits)[2] if hits else None

    def _show_profile_pick(self) -> None:
        """Füllt die gewählten Bereiche und den unter der Maus."""
        pick = self._profile_pick
        shown = set(pick["chosen"]) | ({pick["hover"]} if pick["hover"] is not None else set())
        parts = [pick["regions"][number]["world"] for number in sorted(shown)]
        self.viewport.set_geometry("selected_faces", _stack(parts))

    def _profile_hover(self, x: float, y: float) -> None:
        if self._profile_pick is None:
            return
        hover = self._profile_at(x, y)
        if hover != self._profile_pick["hover"]:
            self._profile_pick["hover"] = hover
            self._show_profile_pick()

    def _profile_clicked(self, x: float, y: float, additive: bool) -> None:
        """Klick wählt das Profil und startet die Extrusion; mit Strg wird nur gesammelt."""
        pick = self._profile_pick
        number = self._profile_at(x, y)
        if number is None:
            # kein Skizzenprofil getroffen: eine ebene Körperfläche wird selbst aufgezogen
            hit = self._hit_at(x, y)
            if hit is not None and hit[0] == "face" and hit[1] in self.timeline.state.bodies:
                face = ft.faces_of(self.timeline.state.bodies[hit[1]])[hit[2]]
                if sketch.face_frame(face) is not None:
                    self._cancel_profile_pick()
                    self._create(ft.Extrude(sketch="", face_points=[list(ft.face_point(face))], distance=0.0,
                                            operation=ft.AUTO_OPERATION))
            return
        regions = pick["regions"]
        chosen = set(pick["chosen"])            # gesammelt wird auch über mehrere Skizzen hinweg
        if additive:
            chosen.symmetric_difference_update({number})
            pick["chosen"] = chosen
            self._show_profile_pick()
            return
        chosen.discard(number)
        # das zuletzt angeklickte Profil führt: an seiner Skizze sitzt der Pfeil
        feature = self._extrude_of([regions[number], *[regions[n] for n in sorted(chosen)]])
        self._cancel_profile_pick()
        self._create(feature)

    def _extrude_of(self, picked: list[dict]) -> ft.Extrude:
        """Extrusion für die gewählten Profilbereiche; Bereiche weiterer Skizzen kommen als Zusatzprofile dazu."""
        by_sketch: dict[str, list[dict]] = {}
        for region in picked:
            by_sketch.setdefault(region["sketch"], []).append(region)
        (name, main), *others = by_sketch.items()
        feature = ft.Extrude(sketch=name, distance=0.0, operation=self._default_operation())
        if main[0]["count"] > 1:
            feature.regions = ft.PROFILE_MODES[2]
            feature.region_points = [list(region["point"]) for region in main]
        feature.extra_profiles = [[other, [list(region["point"]) for region in regions] if regions[0]["count"] > 1 else []]
                                  for other, regions in others]
        return feature

    def _cancel_profile_pick(self) -> None:
        if self._profile_pick is None:
            return
        self._profile_pick = None
        self.viewport.set_geometry("selected_faces", np.zeros((0, 3), dtype=np.float32))
        self.viewport.show_info("")

    # --- Operation in der Ansicht wählen (Verbinden, Ausschneiden, Schnittmenge, neuer Körper) -----

    def _tool_volume(self, feature, before: ft.BuildState) -> list | None:
        """Das Werkzeugvolumen: der Schritt zur Probe als eigener Körper gebaut (None, wenn das nicht geht).

        Gemerkt je Zustand davor und Einstellungen ohne Operation: Berührungsprüfung und rote/gelbe Vorschau
        brauchen dasselbe Volumen, und ein Wechsel der Operation (Knöpfe) ändert es nicht – früher wurde es dafür
        jedes Mal zweimal neu gebaut.
        """
        settings = {key: value for key, value in feature.to_dict().items() if key not in ("operation", "name")}
        key = (id(before), json.dumps(settings, sort_keys=True, default=str))
        cached = getattr(self, "_tool_volume_cache", None)
        if cached is not None and cached[0] == key and cached[2] is before:
            return cached[1]
        trial = before.copy()
        try:
            dataclasses.replace(feature, operation=ft.OPERATIONS[0]).apply(trial)
            new = [shape for name, shape in trial.bodies.items() if name not in before.bodies]
        except Exception:
            new = None
        # der Zustand wird mitgemerkt: solange er lebt, kann seine id() keinem anderen Zustand gehören
        self._tool_volume_cache = (key, new, before)
        return new

    def _operation_state(self, feature, before: ft.BuildState) -> tuple[bool, str]:
        """Berührt das neue Volumen einen vorhandenen Körper, und welche Operation gilt gerade?"""
        new = self._tool_volume(feature, before)
        if new is None:
            return False, feature.operation
        # dieselbe (bei vielen Körpern verteilte) Prüfung wie beim Rechnen; Schnittmengen werden weiterverwendet
        touched, commons, volumes = [], {}, {}
        for shape in new:
            names, found, measured = ft.touched_bodies(before, shape)
            touched += [name for name in names if name not in touched]
            if shape is new[0]:
                commons, volumes = found, measured
        if not touched:
            return False, feature.operation
        if feature.operation == ft.AUTO_OPERATION:
            return True, ft._automatic_operation(before, new[0], touched, commons, volumes)
        return True, feature.operation

    def _show_operation_volume(self, feature, effective: str) -> None:
        """Wie in Fusion: beim Ausschneiden erscheint das Werkzeugvolumen rot, bei der Schnittmenge gelb –
        durchscheinend im Körper, damit zu sehen ist, was wegfällt bzw. worauf sich das Ergebnis beschränkt."""
        color = _OPERATION_COLORS.get(effective)
        if feature is None or color is None:
            self.viewport.set_operation_preview(None)
            return
        new = self._tool_volume(feature, self.timeline.state_before(self._live["index"]))
        if new is None:
            self.viewport.set_operation_preview(None)
            return
        picks = [mesh.pick_mesh(shape) for shape in new]
        self.viewport.set_operation_preview(_stack([pick.triangles for pick in picks]),
                                            _stack([pick.segments for pick in picks]), color)

    def _update_operation_bar(self) -> None:
        """Zeigt die Knöpfe zur Wahl der Operation, sobald der bearbeitete Körper einen anderen berührt."""
        feature = None
        if self._live is not None and self._live.get("session") is None and self._live["index"] < len(self.timeline.features):
            feature = self.timeline.features[self._live["index"]]
        if isinstance(feature, solids.CombineBodies):
            # Kombinieren wählt seine Operation im Eigenschaftenfeld; die Ansicht zeigt Ziel und Werkzeuge farbig
            if self._operation_bar is not None:
                self._operation_bar.hide()
            self._show_combine_marks(feature)
            return
        choices = (*ft.OPERATIONS, ft.AUTO_OPERATION)
        touching = False
        if feature is not None and getattr(feature, "operation", None) in choices:
            before = self.timeline.state_before(self._live["index"])
            if before.bodies and len(before.bodies) <= _OPERATION_BAR_BODIES:
                if Timeline.crash_guard and guard.available() and feature.KIND not in ROBUST_KINDS:
                    # die Berührungsprüfung kann bei verwickelten Körpern lange dauern: nur kurz versuchen
                    status, answer = guard.run(lambda: self._operation_state(feature, before), seconds=2.0,
                                               want_result=True)
                    touching, effective = answer if status == guard.OK else (False, feature.operation)
                else:
                    touching, effective = self._operation_state(feature, before)
        self._show_operation_volume(feature if touching else None, effective if touching else "")
        if not touching:
            if self._operation_bar is not None:
                self._operation_bar.hide()
            return
        if self._operation_bar is None:
            self._operation_bar = QWidget(self.viewport)
            self._operation_bar.setObjectName("operationBar")
            self._operation_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)     # Hintergrund aus dem Design
            row = QHBoxLayout(self._operation_bar)
            row.setContentsMargins(6, 6, 6, 6)
            row.setSpacing(4)
            for operation, label in _OPERATION_LABELS.items():
                button = QPushButton(label)
                button.setCheckable(True)
                button.setToolTip(_OPERATION_TIPS[operation])
                button.clicked.connect(lambda _checked=False, chosen=operation: self._choose_operation(chosen))
                row.addWidget(button)
                self._operation_buttons[operation] = button
        for operation, button in self._operation_buttons.items():
            button.setChecked(operation == effective)
        self._operation_bar.adjustSize()
        # oben mittig in der Ansicht, neben dem Hinweisfeld
        self._operation_bar.move(max(0, (self.viewport.width() - self._operation_bar.width()) // 2), 12)
        self._operation_bar.show()
        self._operation_bar.raise_()

    def _choose_operation(self, operation: str) -> None:
        """Ein Knopf in der Ansicht stellt die Operation des bearbeiteten Features um."""
        if self._live is not None and hasattr(self._live["form"], "set_choice"):
            self._live["form"].set_choice("operation", operation)
            self._update_operation_bar()

    # --- Rechte Maustaste: Menü an der Mausposition ------------------------------------

    def _context_menu(self, x: float, y: float) -> None:
        """Wie in Fusion: rundes Markierungsmenü an der Maus – beim Skizzieren die Skizzenwerkzeuge, sonst die
        wichtigsten Modellwerkzeuge; die Mitte („Mehr“) öffnet die vollständige Liste."""
        if self._draw is not None or self._plane_pick is not None or self._profile_pick is not None:
            return
        where = self.viewport.mapToGlobal(QPoint(int(x), int(y)))
        spot = QPoint(int(x), int(y))           # Stelle in der Ansicht: dort sitzt die Mitte des Rings
        session = self._sketch_session()
        if session is not None:
            form = self._live["form"]
            entries = [(tool, icon(sketch3d._TOOL_ICONS.get(tool, "")), lambda chosen=tool: form.select_tool(chosen))
                       for tool in _SKETCH_RING if tool in sketch3d.TOOLS]
            entries.insert(4, ("Skizze fertigstellen", icon("sketch"), lambda: self._finish_live(True)))
        else:
            entries = []
            for label in _MODEL_RING:
                found = self._tool_actions.get(label)
                if found is not None:
                    entries.append((label, found.icon(), found.trigger))
            if self._live is not None:
                entries[4:5] = [("OK", icon("select"), lambda: self._finish_live(True))]
            canvas = self._canvas_at(x, y) if self._live is None else None
            if canvas is not None:
                # Rechtsklick auf ein Bild: seine Befehle vorn im Ring (Runde 84), dahinter die übrigen
                own = [(f"Leinwand {label.rstrip(' …').lower()}" if label != "Bearbeiten …" else "Leinwand bearbeiten",
                        icon({"Bearbeiten …": "canvas", "Entfernen": "delete"}.get(label, "visibility")), handler)
                       for label, handler in self._canvas_actions(canvas)]
                entries = own + [entry for entry in entries if entry[0] not in ("OK",)][:8 - len(own)]
        previous = getattr(self, "_marking", None)
        if previous is not None:
            previous.deleteLater()      # das vorige Menü aufräumen (es löscht sich nicht selbst beim Schließen)
        self._marking = MarkingMenu(entries, lambda: self._list_menu(where), self.viewport,
                                    scale=max(1.0, self.viewport.height() / 1000.0))
        self._marking.popup_at(spot)

    def _list_menu(self, where: QPoint) -> None:
        """Die vollständige Liste hinter „Mehr“: alle Skizzenwerkzeuge bzw. die Modellwerkzeuge."""
        menu = QMenu(self)
        session = self._sketch_session()
        if session is not None:
            form = self._live["form"]
            for tool in sketch3d.TOOLS:
                entry = menu.addAction(icon(sketch3d._TOOL_ICONS.get(tool, "")), tool)
                entry.setCheckable(True)
                entry.setChecked(session.canvas.tool == tool)
                entry.triggered.connect(lambda _checked=False, chosen=tool: form.select_tool(chosen))
                if tool in _SKETCH_MENU_BREAKS:
                    menu.addSeparator()
            menu.addSeparator()
            menu.addAction("Skizze fertigstellen").triggered.connect(lambda: self._finish_live(True))
            menu.addAction("Skizze verwerfen").triggered.connect(lambda: self._finish_live(False))
        else:
            if self._live is not None:
                menu.addAction("OK").triggered.connect(lambda: self._finish_live(True))
                menu.addAction("Abbrechen").triggered.connect(lambda: self._finish_live(False))
                menu.addSeparator()
            for label in _MODEL_MENU:
                found = self._tool_actions.get(label)
                if found is not None:
                    menu.addAction(found)
        self._menu = menu           # festhalten, solange es offen ist
        menu.popup(where)

    def _create_sweep(self) -> None:
        sketches = self._active_sketch_names()
        if len(sketches) < 2:
            QMessageBox.information(self, "Sweep", "Sweep braucht zwei Skizzen: ein geschlossenes Profil "
                                    "und einen Linienzug als Pfad.")
            return
        # Vorschlag: die letzte Skizze ist der Pfad, die davor das Profil – ohne Profil umgekehrt
        profile, path = sketches[-2], sketches[-1]
        if profile not in self.timeline.state.profiles and path in self.timeline.state.profiles:
            profile, path = path, profile
        self._create(solids.Sweep(sketch=profile, path=path, operation=self._default_operation()))

    def _create_rib(self) -> None:
        sketches = [name for name in self._active_sketch_names() if name in self.timeline.state.curves]
        if not sketches or not self.timeline.state.bodies:
            QMessageBox.information(self, "Rippe", "Die Rippe braucht einen Körper und eine Skizze mit einer "
                                    "offenen Linie, die zum Körper hin zeigt.")
            return
        self._create(solids.Rib(sketch=sketches[-1]))

    def _create_loft(self) -> None:
        sketches = [name for name in self._active_sketch_names() if name in self.timeline.state.profiles]
        if len(sketches) < 2:
            QMessageBox.information(self, "Erhebung", "Die Erhebung braucht mindestens zwei Skizzen mit "
                                    "geschlossenem Profil (z. B. auf versetzten Ebenen).")
            return
        self._create(solids.Loft(sketch=sketches[-2], sketch2=sketches[-1],
                                 operation=self._default_operation()))

    def _edit(self, index: int) -> None:
        feature = self.timeline.features[index]
        if index >= self.timeline.marker:
            QMessageBox.information(self, "Bearbeiten", "Zurückgerollte Schritte lassen sich erst "
                                    "bearbeiten, wenn die Rücksetzmarke dahinter steht.")
            return
        self._finish_live(True)
        self._remember()
        if isinstance(feature, sketch.Sketch):
            # Freie Skizzen werden in der 3D-Ansicht weitergezeichnet
            self._start_sketch(index, False, None)
            return
        self._begin_live(index, False)

    def _rename_feature(self, index: int) -> None:
        """Schritt umbenennen (Timeline oder Browser): Verweise späterer Schritte folgen dem neuen Namen."""
        old = self.timeline.features[index].name
        name, ok = QInputDialog.getText(self, "Umbenennen", "Neuer Name:", text=old)
        if not ok or not name.strip() or name.strip() == old:
            return
        self._finish_live(True)
        self._remember()
        try:
            self.timeline.rename(index, name)
        except ValueError as error:
            QMessageBox.information(self, "Umbenennen", str(error))
            return
        self._refresh()

    def _set_marker(self, position: int) -> None:
        self._finish_live(True)
        self.timeline.set_marker(position)
        self._refresh()

    def _show_feature_menu(self, index: int, position) -> None:
        feature = self.timeline.features[index]
        menu = QMenu(self)
        menu.setToolTipsVisible(True)           # erklärt z. B. „Nach aktuellem Verfahren rechnen“
        menu.addAction("Bearbeiten …", lambda: self._edit(index))
        menu.addAction("Umbenennen …", lambda: self._rename_feature(index))
        menu.addAction("Marke hierhin setzen", lambda: self._set_marker(index + 1))
        menu.addAction("Unterdrückung aufheben" if feature.suppressed else "Unterdrücken",
                       lambda: self._apply(self.timeline.set_suppressed, index, not feature.suppressed))
        menu.addSeparator()
        menu.addAction("Nach links verschieben", lambda: self._apply(self.timeline.move, index, index - 1))
        menu.addAction("Nach rechts verschieben", lambda: self._apply(self.timeline.move, index, index + 1))
        if self.timeline.geometry_level < GEOMETRY_LEVEL:
            # älterer Entwurf (Runde 78): einzelne Schritte gezielt nach dem aktuellen Verfahren rechnen – z. B. wenn
            # sie einen ungültigen Körper ergeben (Rundung oder Kombinieren scheitern daran)
            current = self.timeline.level_of(feature) >= GEOMETRY_LEVEL
            upgrade = menu.addAction("Nach aktuellem Verfahren rechnen",
                                     lambda: self._apply(self.timeline.set_step_level, index, not current))
            upgrade.setCheckable(True)
            upgrade.setChecked(current)
            upgrade.setToolTip("Dieser Entwurf stammt aus einer älteren Version. Angehakt rechnet dieser Schritt wie "
                               "neue Schritte: Stege ignorieren, Körper reparieren, getrennte Flächen zusammenfassen.")
        menu.addSeparator()
        if isinstance(feature, _SKETCH_TYPES) and index < self.timeline.marker \
                and feature.name in {*self.timeline.state.frames, *self.timeline.state.profiles, *self.timeline.state.curves}:
            # wie Fusion: die Skizze verschwindet ab jetzt, Körper aus ihr bleiben (eigener Schritt am Ende)
            menu.addAction(icon("delete"), "Entfernen", lambda: self._remove_sketch(feature.name))
        menu.addAction("Löschen (Schritt aus der Timeline nehmen)", lambda: self._apply(self.timeline.remove, index))
        menu.exec(position)

    def _apply(self, operation, *arguments) -> None:
        self._remember()
        operation(*arguments)
        self._refresh()

    # --- Dateien ----------------------------------------------------------

    # --- Schutz vor Datenverlust: Nachfrage beim Schließen, Wiederherstellung nach einem Absturz --------

    def _document_text(self) -> str:
        return json.dumps(self.timeline.to_document(), sort_keys=True, ensure_ascii=False)

    def _mark_saved(self) -> None:
        """Der jetzige Stand gilt als gespeichert (nach Neu, Öffnen und Speichern)."""
        self._saved_text = self._document_text()

    def is_modified(self) -> bool:
        """Gibt es Änderungen, die noch in keiner Datei stehen?"""
        if not self._saved_text:
            return bool(self.timeline.features)
        return self._document_text() != self._saved_text

    def enable_safeguards(self) -> None:
        """Schaltet Nachfrage und Wiederherstellung ein (macht das echte Programm beim Start)."""
        self.safeguards = True
        self._mark_saved()
        self._recovery_timer.start()

    def write_recovery(self) -> None:
        """Sichert den laufenden Stand, damit er nach einem Absturz wieder angeboten werden kann."""
        if not self.safeguards or not self.is_modified():
            return
        text = self._document_text()
        if text == self._recovery_text:
            return
        try:
            STATE_FOLDER.mkdir(parents=True, exist_ok=True)
            # erst vollständig in eine zweite Datei schreiben, dann umbenennen: nie eine halbe Sicherung
            draft = RECOVERY_FILE.with_suffix(".neu")
            draft.write_text(json.dumps({"pfad": str(self._path) if self._path else "",
                                         "entwurf": self.timeline.to_document()}, ensure_ascii=False), encoding="utf-8")
            draft.replace(RECOVERY_FILE)
            self._recovery_text = text
        except OSError:
            pass                    # die Sicherung ist eine Zugabe – ihr Scheitern darf die Arbeit nicht stören

    def clear_recovery(self) -> None:
        try:
            RECOVERY_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        self._recovery_text = ""

    def offer_recovery(self) -> bool:
        """Bietet nach einem unsauberen Ende den zuletzt gesicherten Stand an; True, wenn er geladen wurde."""
        if not RECOVERY_FILE.exists():
            return False
        try:
            stored = json.loads(RECOVERY_FILE.read_text(encoding="utf-8"))
            timeline = Timeline.from_document(stored["entwurf"])
        except (OSError, ValueError, KeyError, TypeError):
            self.clear_recovery()       # unlesbare Sicherung: nichts anzubieten
            return False
        answer = QMessageBox.question(
            self, "Wiederherstellung",
            "WolfCAD wurde beim letzten Mal nicht sauber beendet.\n"
            f"Es gibt einen gesicherten Stand mit {len(timeline.features)} Schritten. Wiederherstellen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
        if answer != QMessageBox.StandardButton.Yes:
            self.clear_recovery()
            return False
        self._mesh_cache.clear()
        self.timeline = timeline
        self._path = Path(stored["pfad"]) if stored.get("pfad") else None
        self._hidden_bodies = set(self.timeline.view_state.get("hidden", []))
        self._saved_text = "wiederhergestellt"      # gilt als ungespeichert, bis der Nutzer speichert
        self._refresh(fit=True)
        return True

    def _confirm_discard(self, title: str) -> bool:
        """Fragt bei ungespeicherten Änderungen nach; False heißt: nicht fortfahren."""
        if not self.safeguards:
            return True
        self._finish_live(True)
        if not self.is_modified():
            return True
        answer = QMessageBox.question(
            self, title, "Der Entwurf hat ungespeicherte Änderungen. Vorher speichern?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            self._save()
            return not self.is_modified()       # Speichern abgebrochen oder gescheitert: nicht fortfahren
        return True

    def closeEvent(self, event) -> None:
        if not self._confirm_discard("WolfCAD beenden"):
            event.ignore()
            return
        if self.safeguards:
            self._recovery_timer.stop()
            self.clear_recovery()       # sauber beendet: beim nächsten Start gibt es nichts wiederherzustellen
        self.viewport._release_canvases()   # Leinwand-Texturen freigeben, solange der OpenGL-Kontext noch lebt
        if self._print_workspace is not None:
            self._print_workspace.shutdown()
        self._physics.shutdown()
        super().closeEvent(event)

    # --- 3D-Druck -------------------------------------------------------------------------------------------

    def _print_meshes(self, only_selected: bool = False, bodies: list[str] | None = None) -> list:
        """Die Körper als Dreiecksnetze für das Druckbett: alle sichtbaren, nur die gewählten oder die genannten.
        Zerlegt wird fein (0,01 mm wie STL „fein“) auf einer Kopie – die Anzeige behält ihre eigene Zerlegung."""
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
        state = self.timeline.state
        if bodies is None:
            if only_selected:
                bodies = list(dict.fromkeys(body for _kind, body, _index in self._selection if body in state.bodies))
            else:
                bodies = [name for name in state.bodies if name not in state.helpers
                          and name not in self._hidden_bodies]
        aliases = self.timeline.view_state.get("namen", {})
        result = []
        for name in bodies:
            if name not in state.bodies:
                continue
            copy = BRepBuilderAPI_Copy(state.bodies[name]).Shape()
            result.append((aliases.get(name, name), ms.tessellate(copy, exchange.STL_QUALITY["fein"])))
        return result

    def open_print_workspace(self, only_selected: bool = False, bodies: list[str] | None = None) -> None:
        """Öffnet (oder zeigt) den Druck-Reiter und legt die Körper aufs Bett – beim ersten Öffnen alle sichtbaren
        bzw. die gewählten, später nur auf Wunsch (Knöpfe im Reiter) oder die per Rechtsklick genannten."""
        from .print_workspace import PrintWorkspace
        self._finish_live(True)
        first = self._print_workspace is None
        if first:
            self._print_workspace = PrintWorkspace(lambda selected: self._print_meshes(selected))
            self._documents.addTab(self._print_workspace, icon("print3d"), "Druck")
        self._documents.setCurrentWidget(self._print_workspace)
        if bodies is not None:
            self._print_workspace.add_meshes(self._print_meshes(bodies=bodies))
        elif first:
            selected = any(body in self.timeline.state.bodies for _kind, body, _index in self._selection)
            meshes = self._print_meshes(only_selected=selected)
            if meshes:
                self._print_workspace.add_meshes(meshes)

    def _close_document_tab(self, index: int) -> None:
        widget = self._documents.widget(index)
        if widget is self._print_workspace:
            self._print_workspace.shutdown()
            self._documents.removeTab(index)
            widget.deleteLater()
            self._print_workspace = None

    def _open_in_creality(self, body: str) -> None:
        """Übergabe eines Körpers an das Original-Creality-Print (als STL)."""
        from ..printing import slicer
        meshes = self._print_meshes(bodies=[body])
        if not meshes:
            return
        import tempfile
        folder = Path(tempfile.gettempdir()) / "wolfcad-druck" / ("uebergabe-" + time.strftime("%Y%m%d-%H%M%S"))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (re.sub(r"[^\w.-]+", "_", meshes[0][0]) + ".stl")
        ms.write_stl(meshes[0][1], str(path))
        try:
            slicer.open_in_creality_print([path])
        except slicer.SlicerError as error:
            QMessageBox.warning(self, "Creality Print", str(error))

    def _new(self) -> None:
        if not self._confirm_discard("Neuer Entwurf"):
            return
        self._finish_live(False)
        self._mesh_cache.clear()
        self.timeline, self._path = Timeline(), None
        self._hidden_bodies = set()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._mark_saved()
        self._refresh(fit=True)

    def _open(self) -> None:
        if not self._confirm_discard("Entwurf öffnen"):
            return
        start = str(self.data_panel.folder) if self.data_panel.folder else ""
        path, _ = QFileDialog.getOpenFileName(self, "Entwurf öffnen", start, "WolfCAD-Entwurf (*.wcad)")
        if not path:
            return
        self._load_path(path)

    def _open_path(self, path: str) -> None:
        """Öffnet einen Entwurf aus der Projektmappe (mit Nachfrage, falls Ungespeichertes verloren ginge)."""
        if not self._confirm_discard("Entwurf öffnen"):
            return
        self._load_path(path)

    def _load_path(self, path: str) -> None:
        try:
            self.timeline = Timeline.load(path)
        except (OSError, ValueError, KeyError, TypeError) as error:
            QMessageBox.critical(self, "Öffnen", f"Die Datei konnte nicht geladen werden:\n{error}")
            return
        self._path = Path(path)
        self._hidden_bodies = set(self.timeline.view_state.get("hidden", []))      # gespeicherte Sichtbarkeit
        self._mark_saved()
        self._refresh(fit=True)

    def _save(self) -> None:
        if self._path is None:
            folder = self.data_panel.folder       # wie Fusion: im gerade gewählten Projekt speichern
            suggestion = str(folder / "entwurf.wcad") if folder else "entwurf.wcad"
            path, _ = QFileDialog.getSaveFileName(self, "Entwurf speichern", suggestion,
                                                  "WolfCAD-Entwurf (*.wcad)")
            if not path:
                return
            self._path = Path(path)
        try:
            self.timeline.save(self._path)
        except OSError as error:
            QMessageBox.critical(self, "Speichern", f"Speichern fehlgeschlagen:\n{error}")
            return
        self._mark_saved()
        self.clear_recovery()
        self._refresh()
        self._write_preview()

    def _write_preview(self) -> None:
        """Vorschaubild des gespeicherten Entwurfs für die Kacheln der Projektmappe."""
        if self._path is None:
            return
        try:
            target = preview_path(self._path)
            target.parent.mkdir(exist_ok=True)
            picture = self.viewport.grabFramebuffer()
            picture.scaled(320, 240, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation).save(str(target))
        except OSError:
            return          # ohne Vorschaubild geht es auch
        self.data_panel.refresh()

    def _import_f3d(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Datei importieren", "",
                                              "Fusion- und STEP-Dateien (*.f3d *.f3z *.step *.stp)")
        if path and Path(path).suffix.lower() in (".step", ".stp"):
            self.import_step(path)
        elif path:
            self.import_file(path)

    def import_step(self, path: str) -> None:
        """Übernimmt die Körper einer STEP-Datei als Feature der Timeline."""
        try:
            exchange.read_step(path)        # vorab prüfen, damit kein unbrauchbares Feature entsteht
        except exchange.ExchangeError as error:
            QMessageBox.critical(self, "Import", str(error))
            return
        first = not self.timeline.features
        self._remember()
        self.timeline.add(solids.ImportedStep(source=str(path)))
        self._refresh(fit=first)

    def export_bodies(self, path: str, deflection: float = exchange.STL_QUALITY["normal"]) -> int:
        """Schreibt die sichtbaren Körper (ohne Hilfskörper) in die Datei; gibt ihre Anzahl zurück."""
        state = self.timeline.state
        names = [name for name in state.bodies if name not in state.helpers and name not in self._hidden_bodies]
        shapes = [state.bodies[name] for name in names]
        # Farben der Körper und einzeln gefärbter Flächen gehen in die STEP-Datei mit
        colours = []
        for name in names:
            faces = ft.faces_of(state.bodies[name])
            painted = []
            for point, colour in state.face_colors.get(name, []):
                near = min(faces, key=lambda face: math.dist(ft.face_point(face), point), default=None)
                if near is not None and math.dist(ft.face_point(near), point) <= ft.SELECTION_TOLERANCE:
                    painted.append((near, solids.colour_rgb(colour)))
            colours.append((solids.colour_rgb(state.colors.get(name, "")), painted))
        exchange.export(shapes, path, deflection, colours)
        return len(shapes)

    def _show_about(self) -> None:
        from .about import AboutDialog
        AboutDialog(self).exec()

    def _make_drawing(self) -> None:
        """Öffnet das Zeichnungsblatt für die sichtbaren Körper."""
        self._finish_live(True)
        state = self.timeline.state
        shapes = [shape for name, shape in state.bodies.items()
                  if name not in state.helpers and name not in self._hidden_bodies]
        if not shapes:
            QMessageBox.information(self, "Zeichnung", "Es gibt keinen sichtbaren Körper, der gezeichnet werden könnte.")
            return
        # Selbst gesetzte Maße der Zeichnung bleiben im Entwurf gespeichert
        DrawingDialog(shapes, self._path.stem if self._path else "Unbenannt", self,
                      self.timeline.view_state.setdefault("zeichnung", {})).exec()

    def _export(self) -> None:
        self._finish_live(True)
        suggestion = (self._path.stem if self._path else "entwurf") + ".step"
        path, chosen = QFileDialog.getSaveFileName(self, "Körper exportieren", suggestion,
                                                   "STEP (*.step *.stp);;" + ";;".join(_STL_FILTERS))
        if not path:
            return
        if Path(path).suffix.lower() not in exchange.EXPORT_FORMATS:
            path += ".stl" if "STL" in chosen else ".step"      # Endung aus dem gewählten Dateityp
        # Die Feinheit der STL-Datei steckt im gewählten Dateityp
        deflection = _STL_FILTERS.get(chosen, exchange.STL_QUALITY["normal"])
        try:
            count = self.export_bodies(path, deflection)
        except exchange.ExchangeError as error:
            QMessageBox.critical(self, "Export", str(error))
            return
        self.statusBar().showMessage(f"{count} Körper nach {path} exportiert", 8000)

    def import_file(self, path: str) -> None:
        """Übernimmt einen Geometrie-Blob einer .f3d-Datei als Import-Feature."""
        try:
            with F3dArchive(path) as archive:
                if archive.nested_designs():
                    # .f3z bündelt mehrere Entwürfe; deren Einzelimport folgt später
                    QMessageBox.information(
                        self, "Import",
                        "Diese .f3z-Datei enthält mehrere Entwürfe. Bitte die enthaltenen "
                        ".f3d-Dateien vorerst entpacken und einzeln importieren.")
                    return
                blobs = archive.brep_blobs()
                types = archive.feature_types()
            design = ft.load_design(path)
            history = design.history
        except Exception as error:
            QMessageBox.critical(self, "Import", f"Die Datei konnte nicht gelesen werden:\n{error}")
            return
        if not blobs:
            QMessageBox.information(self, "Import", "Die Datei enthält keine Geometriedaten.")
            return

        blob = blobs[0]
        if len(blobs) > 1:
            labels = [b.label for b in blobs]
            choice, accepted = QInputDialog.getItem(
                self, "Import", "Geometrie auswählen (große Blobs brauchen länger):", labels, 0, False)
            if not accepted:
                return
            blob = blobs[labels.index(choice)]

        # Vorab mit Fortschrittsanzeige einlesen; das Feature greift danach auf
        # das gemerkte Ergebnis zu und muss die Datei nicht erneut verarbeiten.
        dialog = QProgressDialog("Körper werden aufgebaut …", "Abbrechen", 0, 100, self)
        dialog.setWindowTitle("Import")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setValue(0)

        def progress(done: int, total: int) -> bool:
            dialog.setMaximum(total)
            dialog.setValue(done)
            QApplication.processEvents()
            return not dialog.wasCanceled()

        try:
            result = ft.load_import(path, blob.path, progress)
        except Exception as error:
            dialog.close()
            QMessageBox.critical(self, "Import", f"Der Import ist fehlgeschlagen:\n{error}")
            return
        cancelled = dialog.wasCanceled()
        dialog.close()
        if cancelled:
            return

        first = not self.timeline.features
        self._remember()
        if history:
            self._adopt_fusion_timeline(path, blob, design, result, types)
            return
        # ohne lesbaren Verlauf: das gespeicherte Modell als ein Import-Feature
        self.timeline.add(ft.ImportedReference(source=path, blob=blob.path))
        self._refresh(fit=first)
        self._show_history(history, design, path)
        stamped = sum(1 for _shape, stamp in result.parts if stamp is not None)
        current = (f"Angezeigt wird das aktuelle Modell: {stamped} Körper." if stamped
                   else "Dieser Blob enthält keine Zeitstempel; angezeigt werden alle Körper.")
        QMessageBox.information(
            self, "Import",
            f"{result.bodies} gespeicherte Körper gelesen, davon {result.solids} als Volumenkörper "
            f"(übrige als offene Flächenkörper).\n"
            f"{result.faces_built} von {result.faces_total} Flächen aufgebaut.\n" + current + "\n\n"
            "Der Konstruktionsverlauf konnte nicht gelesen werden (die Zuordnung der Datenklassen fehlt); "
            "übernommen werden die gespeicherten Körper." + _SCHEMA_HINT + "\n\nVerwendete Feature-Typen:\n"
            + (", ".join(types) or "keine erkannt"))

    def _adopt_fusion_timeline(self, path: str, blob, design, result, types: list[str]) -> None:
        """Übernimmt Fusions ganze Timeline: jeder Schritt wird in Fusions Reihenfolge und mit Fusions Namen ein
        Feature (nachbaubar → bearbeitbar, Skizze → Kurven, sonst gespeicherter Endstand oder Platzhalter).
        Der Geometrie-Blob steht als erstes Feature davor und liefert nur die gespeicherten Endstände."""
        first = not self.timeline.features
        features = [ft.ImportedReference(name=self.timeline.unique_name("FusionDatei"), source=path,
                                         blob=blob.path, content=ft.IMPORT_CONTENTS[2])]
        features += replay.timeline_features(design, path)
        existing = {feature.name for feature in self.timeline.features}
        for feature in features[1:]:
            if feature.name in existing:                     # Import in einen Entwurf mit gleichen Namen
                feature.name = self.timeline.unique_name(feature.name + "_")
        busy = QProgressDialog("Fusion-Timeline wird aufgebaut …", None, 0, 0, self)
        busy.setWindowTitle("Import")
        busy.setWindowModality(Qt.WindowModality.WindowModal)
        busy.setMinimumDuration(0)
        busy.show()
        QApplication.processEvents()
        try:
            self.timeline.add_many(features)
        finally:
            busy.close()
        self._refresh(fit=first)
        self._show_history(design.history, design, path)

        # Zusammenfassung: Art der Schritte und wie viel vom fertigen Fusion-Modell am Ende dasteht
        counts = {"bearbeitbar": 0, "Skizze": 0, "Endstand": 0, "Platzhalter": 0}
        for feature in features[1:]:
            if isinstance(feature, ft.FusionSketchStep):
                counts["Skizze"] += 1
            elif isinstance(feature, ft.FusionStored):
                counts["Endstand"] += 1
            elif isinstance(feature, ft.FusionPlaceholder):
                counts["Platzhalter"] += 1
            else:
                counts["bearbeitbar"] += 1
        failed = sum(1 for index in self.timeline.errors if index >= len(self.timeline.features) - len(features))
        current = [shape for shape, stamp in result.parts if stamp is not None]
        state = self.timeline.state
        present = ft.count_matching_bodies(current, list(state.bodies.values())) if current else 0
        QMessageBox.information(
            self, "Import",
            f"Fusions Timeline übernommen: {len(features) - 1} Schritte in Fusions Reihenfolge.\n"
            f"• {counts['bearbeitbar']} nachgebaut und bearbeitbar\n"
            f"• {counts['Skizze']} Skizzen\n"
            f"• {counts['Endstand']} mit gespeichertem Endstand (nicht bearbeitbar)\n"
            f"• {counts['Platzhalter']} noch nicht nachbaubar (gestrichelt, ohne Wirkung)\n"
            f"{failed} Schritte melden einen Fehler (rot).\n\n"
            f"Am Ende der Timeline stehen {present} der {len(current)} Körper des fertigen Fusion-Modells "
            f"genau so da wie in Fusion; insgesamt {len(state.bodies)} Körper.\n\n"
            "Verwendete Feature-Typen:\n" + (", ".join(types) or "keine erkannt"))

    def _adopt_step(self, row: int) -> None:
        """Übernimmt einen Schritt des Fusion-Verlaufs als bearbeitbares Feature."""
        entry = self._history_entries[row]
        design = ft.load_design(self._history_source)
        stored = ft.stored_bodies(self._history_source)
        if not design.can_rebuild(entry.object_id) and entry.object_id not in stored:
            QMessageBox.information(
                self, "Fusion-Verlauf",
                "Dieser Schritt lässt sich nicht übernehmen: Er ist weder nachbaubar (▶) noch "
                "liegt zu ihm ein gespeicherter Endstand vor (■).\n\nNachbaubar sind bisher Quader, "
                "Zylinder, Kugeln, Extrusionen mit Skizzenprofil sowie Verschieben, Kombinieren "
                "und Entfernen solcher Körper.")
            return
        feature = replay.feature_for_step(design, self._history_source, row)
        feature.name = self.timeline.unique_name(feature.name + "_")
        first = self.timeline.state.body is None
        self._remember()
        self.timeline.add(feature)
        self._refresh(fit=first)

    def _replay_history(self) -> None:
        """Holt abspielbare Schritte des Verlaufs in ihrer Reihenfolge in die Timeline.

        Nachbaubare Schritte werden bearbeitbare Features; Schritte mit gespeichertem
        Endstand setzen diesen ein. Jeder nachgebaute Schritt wird mit seinem
        gespeicherten Endstand verglichen und bei Abweichung durch ihn ersetzt.
        """
        if not self._history_entries:
            QMessageBox.information(self, "Verlauf abspielen", "Bitte zuerst eine Fusion-Datei importieren.")
            return
        design = ft.load_design(self._history_source)
        rows = replay.playable_rows(design, self._history_source)
        rebuildable = len(replay.rebuildable_rows(design))
        count, accepted = QInputDialog.getInt(
            self, "Verlauf abspielen",
            f"{len(rows)} von {len(self._history_entries)} Schritten lassen sich abspielen: "
            f"{rebuildable} nachbaubar (▶), die übrigen mit gespeichertem Endstand (■).\n"
            "Die anderen Schritte werden ausgelassen; Zwischenstände weichen daher vom Original ab.\n\n"
            "Wie viele Schritte abspielen?", len(rows), 1, max(len(rows), 1))
        if not accepted or not rows:
            return
        self.replay_steps(rows[:count])

    def replay_steps(self, rows: list[int]) -> None:
        """Übernimmt die genannten Zeilen des Verlaufs gesammelt (eine Neuberechnung)."""
        design = ft.load_design(self._history_source)
        existing = {feature.name for feature in self.timeline.features}
        features = [replay.feature_for_step(design, self._history_source, row) for row in rows]
        features = [feature for feature in features if feature.name not in existing]
        first = self.timeline.state.body is None
        self._remember()
        self.timeline.add_many(features)
        self._refresh(fit=first)
        failed = len(self.timeline.errors)
        state = self.timeline.state
        frozen = len(state.frozen & set(state.bodies))
        self.statusBar().showMessage(
            f"{len(features)} Schritte abgespielt, {failed} davon mit Fehler (rot in der Timeline). "
            f"{len(state.bodies)} Körper, davon {frozen} im gespeicherten Endstand.")

    def _import_mesh(self, shapes: list) -> tuple:
        """Dreiecke und Kanten der importierten Körper; die Zerlegung wird je Körper gemerkt."""
        import numpy as np

        parts = []
        for shape in shapes:
            key = id(shape)
            if key not in self._mesh_cache:
                # Die Form mit ablegen, damit ihre Kennung nicht neu vergeben wird
                self._mesh_cache[key] = (shape, mesh.triangles(shape, deflection=0.2),
                                         mesh.lines(shape, deflection=0.2))
            parts.append(self._mesh_cache[key])
        if not parts:
            empty = np.zeros((0, 3), dtype=np.float32)
            return empty, empty
        return (np.concatenate([part[1] for part in parts]), np.concatenate([part[2] for part in parts]))

    def _show_fusion_state(self, row: int) -> None:
        """Zeigt das importierte Modell bis zu dem im Verlauf angeklickten Schritt."""
        for index, feature in enumerate(self.timeline.features):
            if isinstance(feature, ft.ImportedReference) and feature.source == self._history_source:
                self.timeline.update(index, up_to_step=float(row + 1))
                self._refresh()
                self.statusBar().showMessage(
                    f"Importiertes Modell bis Schritt {row + 1}: Körper erscheinen mit dem Schritt, "
                    "der sie zuletzt verändert hat (keine echten Zwischenstände).")
                return

    def _show_history(self, history: list, design, source: str) -> None:
        """Füllt die Verlaufsliste: Nummer, Bezeichnung und Maße; ▶ = übernehmbar."""
        self.history.clear()
        self._history_entries, self._history_source = list(history), source
        stored = ft.stored_bodies(source)
        for number, entry in enumerate(history, start=1):
            # ▶ nachbaubar (bearbeitbar), ■ nur mit gespeichertem Endstand abspielbar
            marker = ("▶" if design.can_rebuild(entry.object_id)
                      else "■" if entry.object_id in stored else " ")
            line = f"{number:>5} {marker} {_history_label(entry.kind)}"
            # Maße des Schritts anhängen; Parameter mit dem Wert 0 sagen meist nichts aus
            values = [f"{parameter.role} {parameter.expression}"
                      for parameter in entry.parameters if parameter.value != 0]
            if values:
                line += "  –  " + ", ".join(values[:6])
            self.history.addItem(line)
        self.history_dock.setVisible(bool(history))
        self.history_dock.setWindowTitle(f"Fusion-Verlauf ({len(history)} Schritte)")


# Deutsche Bezeichnungen der häufigsten Fusion-Features
_HISTORY_LABELS = {
    "DcSketch": "Skizze", "DcExtrudeFeature": "Extrusion", "DcRevolveFeature": "Drehen",
    "DcSweepFeature": "Sweep", "DcLoftFeature": "Erhebung", "DcFilletEdgeFeature": "Abrunden",
    "DcChamferFeature": "Fase", "DcShellFeature": "Schale", "DcCombineFeature": "Kombinieren",
    "DcSplitBodyFeature": "Körper teilen", "DcMoveFeature": "Verschieben/Kopieren",
    "DcScaleFeature": "Skalieren", "DcOffsetFacesFeature": "Flächen versetzen",
    "DcDeleteFaceFeature": "Fläche löschen", "DcDeleteBody": "Körper entfernen",
    "DcPasteBodies": "Körper einfügen", "DcMirrorPattern": "Spiegeln",
    "DcBoxPrimitive": "Quader", "DcCylinderPrimitive": "Zylinder", "DcSpherePrimitive": "Kugel",
    "DcBaseFeature": "Basiselement", "DcWorkPlane": "Konstruktionsebene", "DcCanvas": "Ansichtsbild",
    "DcDecal": "Aufkleber", "DcThreadFeature": "Gewinde", "DcAlignFeature": "Ausrichten",
    "DcCreateComponent": "Neue Komponente", "DcPlaceComponentInstance": "Komponente platzieren",
    "DcDeleteInstance": "Komponente löschen",
}


def _history_label(kind: str) -> str:
    """Anzeigename eines Fusion-Features; unbekannte Typen behalten ihren Klassennamen."""
    if kind in _HISTORY_LABELS:
        return _HISTORY_LABELS[kind]
    return kind.removeprefix("Dc").removesuffix("Feature") or "Unbekannt"


def _touches_rectangle(shapes: np.ndarray, rectangle: tuple) -> np.ndarray:
    """Berührt jedes Dreieck bzw. jede Strecke (n × k × 2 Fensterpunkte, k = 3 oder 2) das Rechteck
    (links, oben, rechts, unten)? Wahr bei einem Eckpunkt im Rechteck, einer Rechteckecke im Dreieck oder sich
    kreuzenden Kanten. Punkte hinter der Kamera (nan) zählen nicht."""
    left, top, right, bottom = rectangle
    shapes = np.nan_to_num(np.asarray(shapes, dtype=float), nan=-1e9)
    count, corners = shapes.shape[0], shapes.shape[1]
    xs, ys = shapes[..., 0], shapes[..., 1]
    touched = ((xs >= left) & (xs <= right) & (ys >= top) & (ys <= bottom)).any(axis=1)
    box = np.array([[left, top], [right, top], [right, bottom], [left, bottom]], dtype=float)
    if corners == 3:
        # Rechteckecke im Dreieck (Vorzeichen der drei Kreuzprodukte gleich)
        for corner in box:
            signs = []
            for i in range(3):
                a, b = shapes[:, i], shapes[:, (i + 1) % 3]
                signs.append((b[:, 0] - a[:, 0]) * (corner[1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (corner[0] - a[:, 0]))
            signs = np.array(signs)
            touched |= (signs >= 0).all(axis=0) | (signs <= 0).all(axis=0)
    # Kanten der Form gegen Kanten des Rechtecks
    def cross(o, a, b):
        return (a[..., 0] - o[..., 0]) * (b[..., 1] - o[..., 1]) - (a[..., 1] - o[..., 1]) * (b[..., 0] - o[..., 0])
    for i in range(corners if corners == 3 else 1):
        a, b = shapes[:, i], shapes[:, (i + 1) % corners]
        for j in range(4):
            c, d = np.broadcast_to(box[j], (count, 2)), np.broadcast_to(box[(j + 1) % 4], (count, 2))
            touched |= ((cross(a, b, c) * cross(a, b, d) < 0) & (cross(c, d, a) * cross(c, d, b) < 0))
    return touched


def _vertex_positions(shape) -> list[tuple[float, float, float]]:
    """Lage aller Eckpunkte einer Form."""
    found = []
    for vertex in ft.unique_vertices(shape):
        point = BRep_Tool.Pnt_s(vertex)
        found.append((point.X(), point.Y(), point.Z()))
    return found


def _stack(arrays: list):
    """Hängt Punktlisten aneinander (leere Liste ergibt ein leeres Feld)."""
    import numpy as np

    arrays = [array for array in arrays if len(array)]
    return np.concatenate(arrays) if arrays else np.zeros((0, 3), dtype=np.float32)


def _compound(shapes):
    """Fasst mehrere Formen zu einer zusammen (None, wenn es keine gibt)."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    shapes = list(shapes)
    if not shapes:
        return None
    builder, compound = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound
