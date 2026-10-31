# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""3D-Ansicht: zeichnet Dreiecke und Linien mit OpenGL und einer Orbit-Kamera.

Bedienung: linke oder rechte Maustaste ziehen = drehen, mittlere Maustaste
ziehen = verschieben, Mausrad = zoomen.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QImage, QMatrix4x4, QPainter, QPen, QPolygonF, QVector2D, QVector3D, QVector4D,
)
from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture,
                              QOpenGLVertexArrayObject)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QDoubleSpinBox, QLabel, QRubberBand

from ..core import parameters as pm
from . import theme
from .handles import SET_COLORS     # Farben der Auswahlsätze (Kanten wie ihr Pfeil)
from .parameters_ui import ExpressionBox

# Benötigte OpenGL-Konstanten (PySide6 bringt dafür keine Namen mit)
GL_LINES, GL_TRIANGLES = 0x0001, 0x0004
_GL_UNSIGNED_INT = 0x1405
# Versatz 0 in den gebundenen Indexpuffer: PySide verlangt hier einen Zeiger statt einer Zahl
import shiboken6 as _shiboken
_NO_OFFSET = _shiboken.VoidPtr(0)
GL_DEPTH_TEST, GL_FLOAT = 0x0B71, 0x1406
GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT = 0x4000, 0x0100
GL_POLYGON_OFFSET_FILL = 0x8037
GL_CLIP_DISTANCE0 = 0x3000
# Ebenen, die von der Schnittanalyse aufgeschnitten werden (das Modell, nicht Skizzen oder Griffe)
# Seiten des Ansichtswürfels: Beschriftung -> Außennormale (zugleich die Richtung zum Betrachter)
_CUBE_FACES = {"OBEN": (0, 0, 1), "UNTEN": (0, 0, -1), "VORNE": (0, -1, 0), "HINTEN": (0, 1, 0),
               "RECHTS": (1, 0, 0), "LINKS": (-1, 0, 0)}
_CUBE_RIM = 0.56            # ab hier (von der Mitte einer Seite aus, 0 … 1) beginnen Kanten- und Eckfelder
_HOME_VIEW = (35.0, 25.0)   # Ausgangsansicht: Drehung und Neigung in Grad
_CLIPPED_LAYERS = ("faces", "edges", "import_faces", "import_edges", "selected_faces", "selected_edges")
GL_LEQUAL = 0x0203
GL_BLEND, GL_CONSTANT_ALPHA, GL_ONE_MINUS_CONSTANT_ALPHA = 0x0BE2, 0x8003, 0x8004    # durchscheinendes Zeichnen

_FIELD_OF_VIEW = 35.0    # senkrechter Öffnungswinkel der Kamera in Grad
_CLICK_SLOP = 4          # Bildpunkte, die sich die Maus bei einem Klick bewegen darf
_HANDLE_GRIP_PIXELS = 22 # kleinster Abstand, in dem ein Klick den Ziehpfeil greift
_HANDLE_GRIP_SHARE = 0.018   # Greifweite als Anteil der Fensterhöhe (für hochauflösende Bildschirme)
_ARROW_SIZE = 0.075      # Länge des Ziehpfeils (Lücke, Schaft und Spitze) als Anteil der Kameraentfernung
# Aufbau des Ziehpfeils nach dem Vorbild der Griffe in Fusion: ein kurzer, kräftiger Pfeil, der mit etwas
# Abstand vor der Fläche schwebt; zum Fußpunkt führt nur eine dünne Maßlinie. Anteile der Pfeillänge:
_ARROW_GAP = 0.12        # Abstand zwischen Fläche und Pfeil
_ARROW_NECK = 0.50       # bis hierhin reicht der Schaft, danach folgt die Spitze
_SHAFT_RADIUS, _HEAD_RADIUS, _GUIDE_RADIUS = 0.11, 0.27, 0.014
_KNOB_RADIUS = 0.24      # Kugelgriff am Ende eines Winkelbogens
_ARC_RADIUS = 0.03       # Dicke des Winkelbogens
_HANDLE_STEP = 0.5       # Raster in mm, auf das gezogene Werte einrasten
ANGLE = "angle"          # Griffart "Winkel" (siehe handles.Handle.kind); sonst gerader Pfeil
_ANGLE_STEP = 1.0        # Raster in Grad für gezogene Winkel
_ANGLE_RADIUS = 0.08     # der Winkelbogen liegt um diesen Anteil der Kameraentfernung weiter außen
_HANDLE_SEGMENTS = 12    # Ecken, aus denen Schaft und Kegel des Pfeils gebildet werden
# so viele Pfeile können gleichzeitig angezeigt werden – Verschieben braucht 7 (drei Pfeile, drei Drehgriffe,
# Skalieren); bis Runde 81 waren es 4, der Skalierpfeil fehlte deshalb in der Ansicht
_MAX_HANDLES = 8
_GIZMO_RING = 50.0       # Bogenlänge (Grad) des Ringstücks eines Drehgriffs im Verschiebe-Kreuz
# Bodenraster (XY-Ebene), auf dem Körper aufgezogen werden: Rasterweiten in mm, die mit dem Zoom wechseln
_GROUND_STEPS = (0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0, 5000.0)
_GROUND_MIN_PIXELS = 34  # so weit liegen die Rasterlinien auf dem Bildschirm mindestens auseinander
_GROUND_MAX_LINES = 160  # mehr Linien je Richtung werden nicht gezeichnet (dann gröberes Raster)
# Wie in Fusion ist das Raster ein Feld um das Gezeichnete, das mit ihm wächst (nicht bis zum Horizont):
_PATCH_MIN_HALF = 60.0   # so weit reicht es mindestens um seine Mitte (mm) – auch im leeren Entwurf
_PATCH_PAD_SHARE = 0.35  # Rand um das Gezeichnete als Anteil seiner Größe …
_PATCH_PAD_MIN = 20.0    # … mindestens aber so viele mm


def grid_patch(points) -> tuple[float, float, float, float]:
    """Ausdehnung des Rasterfelds (x von, y von, x bis, y bis) für die gezeichneten Punkte.

    Das Feld umfasst immer den Ursprung, lässt ringsum einen Rand und hat eine Mindestgröße.
    """
    xs = [0.0, *(float(point[0]) for point in points)]
    ys = [0.0, *(float(point[1]) for point in points)]
    low_x, high_x, low_y, high_y = min(xs), max(xs), min(ys), max(ys)
    pad = max(_PATCH_PAD_MIN, _PATCH_PAD_SHARE * max(high_x - low_x, high_y - low_y))
    low_x, high_x, low_y, high_y = low_x - pad, high_x + pad, low_y - pad, high_y + pad
    for _axis in range(2):
        # zu schmale Richtung um ihre Mitte auf die Mindestgröße bringen
        if high_x - low_x < 2 * _PATCH_MIN_HALF:
            middle = (low_x + high_x) / 2
            low_x, high_x = middle - _PATCH_MIN_HALF, middle + _PATCH_MIN_HALF
        low_x, high_x, low_y, high_y = low_y, high_y, low_x, high_x     # Rollen tauschen, zweimal = wie vorher
    return low_x, low_y, high_x, high_y
_ZOOM_OUT_STEPS = 12     # so oft weicht die Kamera höchstens zurück, um Pfeile ins Bild zu holen

_VERTEX_SHADER = """#version 330 core
layout(location = 0) in vec3 position;
uniform mat4 model_view;
uniform mat4 projection;
uniform vec4 clip_plane;    // Schnittebene der Schnittanalyse: weggeschnitten wird, wo der Wert negativ ist
out vec3 view_position;
void main() {
    vec4 in_view = model_view * vec4(position, 1.0);
    view_position = in_view.xyz;
    gl_Position = projection * in_view;
    gl_ClipDistance[0] = dot(vec4(position, 1.0), clip_plane);
}
"""

# Die Flächennormale wird aus den Bildschirmableitungen berechnet; dadurch
# müssen keine Normalen pro Eckpunkt gespeichert werden.
_FRAGMENT_SHADER = """#version 330 core
in vec3 view_position;
uniform vec3 color;
uniform bool lit;
out vec4 result;
void main() {
    if (!lit) { result = vec4(color, 1.0); return; }
    vec3 normal = normalize(cross(dFdx(view_position), dFdy(view_position)));
    float diffuse = abs(dot(normal, normalize(vec3(0.45, 0.8, 0.4))));
    result = vec4(color * (0.45 + 0.55 * diffuse), 1.0);
}
"""

# Glatt schattierte Ebenen (Runde 94, Formen wie in Blender): Normale je Eckpunkt statt aus den Bildschirmableitungen
# – die Oberfläche wirkt rund statt facettiert
_SMOOTH_VERTEX_SHADER = """#version 330 core
layout(location = 0) in vec3 position;
layout(location = 1) in vec3 normal;
uniform mat4 model_view;
uniform mat4 projection;
uniform vec4 clip_plane;
out vec3 view_normal;
void main() {
    vec4 in_view = model_view * vec4(position, 1.0);
    view_normal = mat3(model_view) * normal;
    gl_Position = projection * in_view;
    gl_ClipDistance[0] = dot(vec4(position, 1.0), clip_plane);
}
"""
_SMOOTH_FRAGMENT_SHADER = """#version 330 core
in vec3 view_normal;
uniform vec3 color;
uniform bool lit;
out vec4 result;
void main() {
    vec3 normal = normalize(view_normal);
    float diffuse = abs(dot(normal, normalize(vec3(0.45, 0.8, 0.4))));
    // etwas Glanzlicht wie Blenders Formen-Ansicht (Matcap-ähnlich): Form und Dellen lesen sich besser
    float rim = pow(1.0 - abs(normal.z), 2.0);
    result = vec4(color * (0.38 + 0.55 * diffuse) + vec3(0.10) * rim, 1.0);
}
"""

# Leinwand (Bild als Vorlage auf einer Ebene): Eckpunkt mit Bildkoordinate, Bild halbdurchsichtig
_CANVAS_VERTEX_SHADER = """#version 330 core
layout(location = 0) in vec3 position;
layout(location = 1) in vec2 corner;
uniform mat4 model_view;
uniform mat4 projection;
out vec2 picture;
void main() {
    picture = corner;
    gl_Position = projection * model_view * vec4(position, 1.0);
}
"""
_CANVAS_FRAGMENT_SHADER = """#version 330 core
in vec2 picture;
uniform sampler2D image;
uniform float opacity;
out vec4 result;
void main() {
    vec4 colour = texture(image, picture);
    result = vec4(colour.rgb, colour.a * opacity);
}
"""
GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA = 0x0302, 0x0303

# Linien werden als schmale Flächen in Bildschirmbreite gezeichnet: Die Linienbreite von
# OpenGL wirkt im Kernprofil nicht, 1 Bildpunkt ist auf hochauflösenden Bildschirmen kaum zu sehen.
_LINE_GEOMETRY_SHADER = """#version 330 core
layout(lines) in;
layout(triangle_strip, max_vertices = 4) out;
uniform vec2 window_size;   // Größe der Ansicht in Bildpunkten
uniform float line_width;   // Breite der Linie in Bildpunkten
out vec3 view_position;
void main() {
    vec4 start = gl_in[0].gl_Position;
    vec4 end = gl_in[1].gl_Position;
    // Stücke hinter der Kamera abschneiden, sonst kippt die Umrechnung auf den Bildschirm
    float limit = 1e-5;
    if (start.w < limit && end.w < limit) return;
    if (start.w < limit) start = mix(end, start, (end.w - limit) / (end.w - start.w));
    if (end.w < limit) end = mix(start, end, (start.w - limit) / (start.w - end.w));
    vec2 along = (end.xy / end.w - start.xy / start.w) * window_size;
    float length_px = length(along);
    vec2 across = length_px > 1e-6 ? vec2(-along.y, along.x) / length_px : vec2(1.0, 0.0);
    // Halbe Breite quer zur Linie, zurück in Gerätekoordinaten
    vec2 shift = across * line_width / window_size;
    view_position = vec3(0.0);
    // Die Schnittebene gilt auch für die Linienflächen: Abstand der Enden weiterreichen
    float cut_start = gl_in[0].gl_ClipDistance[0];
    float cut_end = gl_in[1].gl_ClipDistance[0];
    gl_ClipDistance[0] = cut_start; gl_Position = vec4(start.xy + shift * start.w, start.zw); EmitVertex();
    gl_ClipDistance[0] = cut_start; gl_Position = vec4(start.xy - shift * start.w, start.zw); EmitVertex();
    gl_ClipDistance[0] = cut_end; gl_Position = vec4(end.xy + shift * end.w, end.zw); EmitVertex();
    gl_ClipDistance[0] = cut_end; gl_Position = vec4(end.xy - shift * end.w, end.zw); EmitVertex();
    EndPrimitive();
}
"""
_LINE_SCALE_HEIGHT = 1100.0     # ab dieser Fensterhöhe werden Linien entsprechend breiter gezeichnet


# Ebenen, die eine Darstellungsart weglässt (die farbigen Körperflächen „faces#…“ prüft paintGL eigens)
_STYLE_HIDDEN = {"shaded": ("edges", "import_edges"),
                 "wireframe": ("faces", "import_faces", "section_cap")}


def _axis_aligned(yaw: float, pitch: float) -> bool:
    """Blickt die Kamera genau entlang einer Achse (Draufsicht, Vorderansicht …)?"""
    if abs(abs(pitch) - 90.0) < 1e-9:
        return True
    return abs(pitch) < 1e-9 and abs((yaw % 90.0 + 45.0) % 90.0 - 45.0) < 1e-9


class _Layer:
    """Ein Satz Eckpunkte auf der Grafikkarte samt Darstellungsart."""

    def __init__(self, mode: int, color: tuple, lit: bool, line_width: float = 1.0, on_top: bool = False):
        self.mode, self.color, self.lit, self.line_width = mode, color, lit, line_width
        self.on_top = on_top    # ohne Tiefentest zeichnen: bleibt sichtbar, auch wenn es im Körper liegt
        self.alpha = 1.0        # Deckkraft; unter 1 scheint durch, was dahinter liegt
        self.normals = None     # Normalen je Eckpunkt (glatt schattiert, siehe _SMOOTH_*); None: flach
        # Indiziert gezeichnet (Runde 94, Formen): `data` sind dann die gemeinsamen Ecken, `indices` die Dreiecke als
        # Eckennummern. Beim Formen ändern sich nur die Ecken – die Nummern werden nur bei neuer Topologie übertragen
        self.indices = None
        self.index_buffer = None
        self.indices_dirty = False
        self.vao = QOpenGLVertexArrayObject()
        self.buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.count = 0
        self.data = np.zeros((0, 3), dtype=np.float32)
        self.dirty = True   # Daten müssen (erneut) auf die Grafikkarte geladen werden


class _NumberBox(ExpressionBox):
    """Eingabefeld in der Ansicht, das Eingabe und Esc selbst behält.

    Sonst nähme das Hauptfenster diese Tasten als Kürzel für „Bearbeiten beenden“.
    Wie die Zahlenfelder der Werkzeuge nimmt es auch Ausdrücke mit Parametern an.
    """

    confirmed = Signal(float)
    cancelled = Signal()
    base_style = ""         # Aussehen des Felds in der Ansicht (von edit_number gesetzt)

    via_tab = False         # die letzte Bestätigung kam mit der Tabulatortaste (weiter zum nächsten Maß)
    backward = False        # … und zwar mit Umschalt+Tab (zurück zum vorigen Maß)

    def event(self, event) -> bool:
        keys = (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape)
        if event.type() == QEvent.Type.ShortcutOverride and event.key() in keys:
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            # Tab soll nicht zum nächsten Bedienelement springen, sondern das Maß bestätigen
            self.keyPressEvent(event)
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.via_tab = event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
            self.backward = event.key() == Qt.Key.Key_Backtab      # Umschalt+Tab: zum vorigen Feld
            self.interpretText()
            if self.problem:
                # Unbrauchbare Eingabe (z. B. unbekannter Parameter): das Feld bleibt offen und
                # zeigt den Fehler, statt stillschweigend den alten Wert zu übernehmen
                self.setStyleSheet(self.base_style + "border:2px solid #e05555;")
                return
            self.confirmed.emit(self.value())
        elif event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(event)


class Viewport(QOpenGLWidget):
    # Klick ohne Ziehen: x, y in Fensterkoordinaten; True, wenn die Auswahl erweitert werden soll
    clicked = Signal(float, float, bool)
    # Rahmenauswahl mit der linken Taste: Ecke beim Drücken, Ecke beim Loslassen, Strg/Umschalt gehalten
    box_selected = Signal(float, float, float, float, bool)
    # Der Wert des Ziehpfeils wurde geändert (durch Ziehen oder im Wertfeld an der Pfeilspitze)
    handle_changed = Signal(float)
    handle_released = Signal()      # der gezogene Pfeil wurde losgelassen
    # Griffpunkt in einer Ebene (z. B. Bohrungsmitte) gezogen: Punkt in der Ebene, dazu die Mausposition im Fenster
    point_dragged = Signal(float, float, float, float, float)
    point_released = Signal()
    view_changed = Signal()         # Kamera gedreht, verschoben oder gezoomt
    hovered = Signal(float, float)  # Maus ohne gedrückte Taste bewegt (Fensterkoordinaten)
    # Rechtsklick ohne Ziehen: das Hauptfenster zeigt dort das Kontextmenü (beim Skizzieren die Skizzenwerkzeuge)
    context_requested = Signal(float, float)
    # Wischgeste: rechte Taste gedrückt gezogen – das Hauptfenster hebt im runden Menü das Feld in dieser
    # Richtung hervor und löst es beim Loslassen aus
    gesture_moved = Signal(float, float)
    gesture_released = Signal(float, float)
    pressed = Signal()              # irgendeine Maustaste in der Ansicht gedrückt (schließt offene Menüs)
    home_requested = Signal()       # das Haus am Ansichtswürfel wurde angeklickt

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._program: QOpenGLShaderProgram | None = None
        self._line_program: QOpenGLShaderProgram | None = None     # zeichnet Linien als schmale Flächen
        # Ebenen: Körperflächen, Körperkanten, Skizzen, importierte Flächen und Kanten
        self._layers = {
            # Bodenraster mit den Achsen durch den Ursprung (X rot, Y grün) – liegt unter dem Modell
            "ground_grid": _Layer(GL_LINES, (0.23, 0.25, 0.31), False),
            "ground_grid_major": _Layer(GL_LINES, (0.33, 0.36, 0.44), False),
            "ground_axis_x": _Layer(GL_LINES, (0.86, 0.30, 0.30), False, line_width=1.8),
            "ground_axis_y": _Layer(GL_LINES, (0.36, 0.76, 0.42), False, line_width=1.8),
            "faces": _Layer(GL_TRIANGLES, (0.62, 0.66, 0.72), True),
            "edges": _Layer(GL_LINES, (0.08, 0.09, 0.11), False, line_width=1.2),
            "sketches": _Layer(GL_LINES, (0.25, 0.65, 1.0), False, line_width=2.0),
            "import_faces": _Layer(GL_TRIANGLES, (0.80, 0.68, 0.48), True),
            "import_edges": _Layer(GL_LINES, (0.10, 0.09, 0.07), False, line_width=1.2),
            # Auswahl: zuletzt gezeichnet, damit sie über dem Modell liegt
            "selected_faces": _Layer(GL_TRIANGLES, (0.25, 0.60, 1.00), True),
            # Fläche unter dem Mauszeiger, auf der ein Werkzeug gerade ansetzen würde (Fusion hellt sie auf)
            "hover_faces": _Layer(GL_TRIANGLES, (0.93, 0.95, 1.00), False),     # flach, ohne Licht: deutlich hell
            # im Modell angeklickte Skizzenprofile (vor der Extrusion)
            "selected_profiles": _Layer(GL_TRIANGLES, (0.25, 0.60, 1.00), True),
            "selected_edges": _Layer(GL_LINES, (1.00, 0.85, 0.20), False, line_width=4.0),
            # Formen-Modus (Runde 94): das Netz beim Formen in Tonfarbe, geschützte (maskierte) Bereiche dunkel
            # darüber, der Pinselkreis über allem
            "sculpt_mesh": _Layer(GL_TRIANGLES, (0.78, 0.70, 0.62), True),
            "sculpt_mask": _Layer(GL_TRIANGLES, (0.10, 0.10, 0.12), False),
            "sculpt_brush": _Layer(GL_LINES, (1.00, 1.00, 1.00), False, line_width=2.0, on_top=True),
            # Formen (Runde 98): Netzansicht als Drahtgitter über dem Netz (wie Blenders Überlagerung „Drahtgitter“)
            # und die gelbe Vorschau des Schleifenschnitts
            "sculpt_wire": _Layer(GL_LINES, (0.08, 0.08, 0.10), False, line_width=0.8),
            "sculpt_loop": _Layer(GL_LINES, (1.00, 0.82, 0.10), False, line_width=2.2),
            # Kanten der abgeschlossenen Auswahlsätze einer Rundung/Fase, je Satz in der Farbe seines Pfeils
            **{f"set_edges{number}": _Layer(GL_LINES, colour, False, line_width=4.0)
               for number, colour in enumerate(SET_COLORS)},
            # Skizzieren in der Ansicht: Raster, Zeichenhilfe, Vorschau des laufenden Elements, Fangmarke
            "construction_model": _Layer(GL_LINES, (0.72, 0.50, 0.22), False, line_width=1.2),
            "planes": _Layer(GL_LINES, (0.95, 0.62, 0.20), False, line_width=1.5),
            "measure": _Layer(GL_LINES, (1.00, 0.85, 0.20), False, line_width=2.0, on_top=True),
            "section_cap": _Layer(GL_TRIANGLES, (0.84, 0.50, 0.30), True),
            "pick_fill": _Layer(GL_TRIANGLES, (0.45, 0.33, 0.14), False),
            "pick_planes": _Layer(GL_LINES, (0.95, 0.62, 0.20), False, line_width=1.8),
            "sketch_grid_major": _Layer(GL_LINES, (0.36, 0.39, 0.46), False),
            "sketch_grid": _Layer(GL_LINES, (0.24, 0.26, 0.31), False),
            # geschlossene Skizzenbereiche: durchscheinend blau gefüllt wie in Fusion (unter den Linien)
            "sketch_profiles": _Layer(GL_TRIANGLES, (0.30, 0.58, 1.00), False, on_top=True),
            # projizierte Geometrie: lila wie in Fusion
            "sketch_reference": _Layer(GL_LINES, (0.70, 0.50, 0.95), False, line_width=1.5, on_top=True),
            # fixierte Elemente: grün wie in Fusion
            "sketch_locked": _Layer(GL_LINES, (0.35, 0.82, 0.42), False, line_width=2.5, on_top=True),
            # Mittellinien: strichpunktiert in der Farbe der Konstruktion
            "sketch_centerline": _Layer(GL_LINES, (0.95, 0.62, 0.20), False, line_width=1.6, on_top=True),
            # offene Endpunkte (nur an einem Element): kleine weiße Quadrate wie Fusions „freie Punkte“
            "sketch_points": _Layer(GL_LINES, (0.95, 0.96, 0.98), False, line_width=1.6, on_top=True),
            # Krümmungskamm gewählter Freiformkurven
            "sketch_comb": _Layer(GL_LINES, (0.85, 0.45, 0.95), False, line_width=1.0, on_top=True),
            "sketch_fixed": _Layer(GL_LINES, (0.93, 0.95, 0.98), False, line_width=2.5, on_top=True),
            "sketch_live": _Layer(GL_LINES, (0.30, 0.68, 1.00), False, line_width=2.5, on_top=True),
            "sketch_preview": _Layer(GL_LINES, (0.62, 0.82, 1.00), False, line_width=2.0, on_top=True),
            "sketch_construction": _Layer(GL_LINES, (0.95, 0.62, 0.20), False, line_width=1.3, on_top=True),
            "sketch_dimensions": _Layer(GL_LINES, (0.78, 0.70, 0.45), False, line_width=1.2, on_top=True),
            "sketch_marker": _Layer(GL_LINES, (1.00, 0.85, 0.20), False, line_width=2.0, on_top=True),
            # Ausrichtungshilfe: gestrichelte Linie zum gemerkten Punkt (wie Fusion)
            "sketch_alignment": _Layer(GL_LINES, (0.95, 0.62, 0.20), False, line_width=1.4, on_top=True),
            # Werkzeugvolumen beim Ausschneiden (rot) und bei der Schnittmenge (gelb): durchscheinend im Körper
            "operation_volume": _Layer(GL_TRIANGLES, (0.95, 0.25, 0.22), True, on_top=True),
            "operation_edges": _Layer(GL_LINES, (0.95, 0.25, 0.22), False, line_width=1.8, on_top=True),
            # nicht gefüllte Zellen der Begrenzungsfüllung: grau durchscheinend („Geister“, anklickbar)
            "ghost_volume": _Layer(GL_TRIANGLES, (0.62, 0.68, 0.78), True, on_top=True),
            "ghost_edges": _Layer(GL_LINES, (0.62, 0.68, 0.78), False, line_width=1.2, on_top=True),
            # Isokurven-Analyse (magenta wie in Fusion) und Gelenkursprünge (türkis, immer sichtbar)
            "isolines": _Layer(GL_LINES, (0.92, 0.36, 0.86), False, line_width=1.1),
            "joint_origins": _Layer(GL_LINES, (0.20, 0.85, 0.95), False, line_width=2.0, on_top=True),
            # verschiebbarer Griffpunkt (Bohrungsmitte) und seine Fangmarke
            "point_grip": _Layer(GL_TRIANGLES, (1.00, 0.55, 0.10), False, on_top=True),
            **{f"handle{index}": _Layer(GL_TRIANGLES, (1.00, 0.55, 0.10), False, on_top=True)
               for index in range(_MAX_HANDLES)},
        }
        # Kamera: Blickziel, Abstand und zwei Drehwinkel (Grad)
        self._target = QVector3D(0, 0, 0)
        self._distance = 200.0
        self._yaw, self._pitch = 35.0, 25.0
        self._last_mouse = QPoint()
        self._press_position = QPointF()
        self._dragged = False
        # Ziehpfeil des gerade bearbeiteten Features (None = keiner) und Wertfeld an seiner Spitze
        # Zeichenwerkzeug der Skizze (siehe sketch3d.SketchSession); solange es gesetzt ist,
        # gehen Klicks und Mausbewegungen an die Skizze statt an Auswahl und Drehen
        self.sketch_input = None
        self.release_position = QPointF()
        self.orbit_binding = "shift_middle"     # womit gedreht wird (siehe gui/settings.py); setzt das Hauptfenster
        self._canvas_gl: list[dict] = []        # Leinwände (siehe set_canvases)
        self._point_grip: tuple | None = None   # (Punkt, Ebenennormale) des ziehbaren Griffpunkts oder None
        self._point_drag = False
        # Modus der linken Maustaste aus der Navigationsleiste: "" bzw. "orbit" dreht, "pan" schwenkt, "zoom" zoomt
        self.left_mode = ""
        # Ziehen mit der linken Taste ohne Navigationsmodus: "select" = Rahmenauswahl wie in Fusion, "orbit" = drehen
        self.left_drag = "select"
        self._band: QRubberBand | None = None       # sichtbarer Rahmen während der Rahmenauswahl
        # Darstellung (Navigationsleiste): "shaded_edges", "shaded" (ohne Kanten) oder "wireframe" (nur Kanten)
        self.display_style = "shaded_edges"
        self._gesture = False                   # läuft gerade eine Wischgeste mit der rechten Taste?
        self._ground_visible = True     # Bodenraster zeigen (Schalter „Raster“ in der Werkzeugleiste)
        self._ground_step = 10.0        # zuletzt gezeichnete Rasterweite in mm
        self._ground_points: list = []  # Eckpunkte des Modells auf dem Boden: daran bemisst sich das Rasterfeld
        self._ground_extra: list = []   # zusätzlich einzuschließende Punkte (z. B. die Maus beim Aufziehen)
        self.view_changed.connect(self.refresh_ground)
        self.setMouseTracking(True)     # Bewegungen auch ohne gedrückte Taste melden
        # Texte im Modell (Maße und Zeichen der Skizze): (Punkt im Raum, Text, hervorgehoben)
        self._labels: list[tuple] = []
        # Maßfelder beim Zeichnen (wie Fusion): je Feld (Punkt im Raum, Text, aktiv, festgehalten)
        self._hud: list[tuple] = []
        self._label_boxes: list[QRectF] = []    # Fensterrechtecke der zuletzt gezeichneten Texte
        # Eingabefeld für eine Zahl an einer Stelle im Modell (z. B. ein Skizzenmaß)
        self._number_box = _NumberBox(pm.evaluate, self)
        self._number_box.setRange(0.001, 1e5)
        self._number_box.setDecimals(2)
        self._number_box.setSuffix(" mm")
        self._number_box.hide()
        self._number_point = None
        self._number_callback = None
        self._number_cancelled = None       # wird bei Esc aufgerufen (z. B. um ein neues Maß zu verwerfen)
        self._number_box.confirmed.connect(self._number_confirmed)
        self._number_box.cancelled.connect(self._number_escaped)
        # jedes getippte Zeichen melden (Maßfelder beim Zeichnen: der Wert gilt sofort, wie in Fusion)
        self._number_edited = None
        self._number_box.lineEdit().textEdited.connect(
            lambda text: self._number_edited(text) if self._number_edited is not None else None)
        self._sketch_drag = False           # die Skizze hat beim Drücken etwas zum Ziehen gegriffen
        self._cube_hover = None                 # Feld des Ansichtswürfels unter der Maus (Richtung oder "home")
        self._rubber_band: tuple | None = None  # Auswahlrahmen: (vier Punkte im Raum, auch Berührtes?)
        self._highlight: list | None = None     # halb durchsichtig hervorgehobene Fläche (Punkte im Raum)
        self.resolver = pm.evaluate             # rechnet Ausdrücke aus (das Hauptfenster setzt die Parameter ein)
        self._handle_expression = ""            # zuletzt am Pfeil eingetippter Ausdruck
        self._cube_press: tuple | None = None   # Würfelseite, auf der die Maustaste gedrückt wurde
        self._clip: tuple | None = None     # Schnittebene (a, b, c, d): sichtbar bleibt a·x + b·y + c·z + d >= 0
        # Feste Anzeige oben links (z. B. Messwerte)
        self._info = QLabel(self)
        self._info.move(14, 14)
        self.info_offset = 14       # linker Rand der Hinweise oben links (rückt neben den schwebenden Browser)
        self._info.hide()
        self._hint = QLabel(self)
        self._hint.hide()
        self._handles: list = []
        self._handle_drag_started = False   # True, sobald der Griffversatz beim Anfassen feststeht
        self._hover: int | None = None      # Pfeil, über dem die Maus gerade steht
        self._active = 0
        self._handle = None     # der aktive Pfeil aus _handles
        self._handle_drag = False
        self._handle_moved = False      # wurde der gegriffene Pfeil schon bewegt (sonst ist es ein Klick)?
        self._handle_offset = 0.0
        # Wertfeld am Pfeil: nimmt wie die Felder der Werkzeuge auch Ausdrücke mit Parametern an
        self._value_box = ExpressionBox(lambda text: self.resolver(text), self, live=True)   # Modell läuft beim Tippen mit
        self._value_box.setRange(-1e5, 1e5)
        self._value_box.setDecimals(2)
        self._value_box.setSuffix(" mm")
        self._value_font_pixels = 0                     # zuletzt gesetzte Schriftgröße des Wertfelds
        self._value_box.edited.connect(self._value_box_edited)
        self.apply_theme()
        self._value_box.hide()

    # --- Daten ------------------------------------------------------------

    def set_geometry(self, name: str, vertices: np.ndarray, normals: np.ndarray | None = None) -> None:
        """Ersetzt die Eckpunkte einer Ebene; hochgeladen wird beim nächsten Zeichnen. Mit `normals` (je Eckpunkt)
        wird die Ebene glatt schattiert."""
        layer = self._layers[name]
        layer.data = np.ascontiguousarray(vertices, dtype=np.float32)
        layer.normals = None if normals is None or len(normals) != len(layer.data) else \
            np.ascontiguousarray(normals, dtype=np.float32)
        layer.indices = None            # lose Dreiecke (nicht indiziert)
        layer.dirty = True
        self.update()

    def set_indexed(self, name: str, vertices: np.ndarray, normals: np.ndarray, indices: np.ndarray | None) -> None:
        """Ebene aus gemeinsamen Ecken (mit Normalen, glatt schattiert) und Dreiecken als Eckennummern. `indices`
        None: die bisherigen Dreiecke bleiben (nur die Ecken haben sich bewegt – das Übliche beim Formen)."""
        layer = self._layers[name]
        layer.data = np.ascontiguousarray(vertices, dtype=np.float32)
        # ohne Normalen (Linien, z. B. das Drahtgitter beim Formen: `indices` sind dann Kanten als Eckenpaare)
        layer.normals = None if normals is None else np.ascontiguousarray(normals, dtype=np.float32)
        if indices is not None:
            layer.indices = np.ascontiguousarray(indices, dtype=np.uint32).ravel()
            layer.indices_dirty = True
        layer.dirty = True
        self.update()

    def look_along(self, normal) -> None:
        """Dreht die Kamera so, dass sie senkrecht auf eine Ebene mit dieser Normale schaut."""
        length = math.sqrt(sum(c * c for c in normal)) or 1.0
        x, y, z = (c / length for c in normal)
        # Blickrichtung zum Auge laut _matrices: (sin(yaw)·cos(pitch), -cos(yaw)·cos(pitch), sin(pitch))
        self.set_view(math.degrees(math.atan2(x, -y)) if abs(z) < 0.999 else 0.0,
                      math.degrees(math.asin(max(-1.0, min(1.0, z)))))

    def show_hint(self, text: str, x: float = 0.0, y: float = 0.0) -> None:
        """Zeigt einen kurzen Text neben dem Mauszeiger (leerer Text blendet ihn aus)."""
        if not text:
            self._hint.hide()
            return
        self._hint.setText(text)
        self._hint.adjustSize()
        self._hint.move(int(x) + 18, int(y) + 14)
        self._hint.show()

    def set_face_groups(self, groups: list[tuple[tuple, np.ndarray]]) -> None:
        """Zeigt die Körperflächen nach Farbe getrennt: je Eintrag (Farbe, Dreiecke).

        Für jede Farbe gibt es eine eigene Ebene; nicht mehr gebrauchte werden geleert.
        """
        for number, (color, triangles) in enumerate(groups):
            name = f"faces#{number}"
            if name not in self._layers:
                self._layers[name] = _Layer(GL_TRIANGLES, color, True)
            self._layers[name].color = color
            self.set_geometry(name, triangles)
        for name in [n for n in self._layers if n.startswith("faces#") and int(n[6:]) >= len(groups)]:
            self.set_geometry(name, np.zeros((0, 3), dtype=np.float32))

    def set_point_grip(self, point, normal) -> None:
        """Zeigt einen in seiner Ebene ziehbaren Griffpunkt (orangefarbene Scheibe); None blendet ihn aus."""
        self._point_grip = None if point is None else (tuple(map(float, point)), tuple(map(float, normal)))
        self._draw_point_grip()

    def _draw_point_grip(self) -> None:
        if self._point_grip is None:
            self.set_geometry("point_grip", np.zeros((0, 3), dtype=np.float32))
            return
        centre, normal = (np.array(v) for v in self._point_grip)
        side = np.cross(normal, [1.0, 0.0, 0.0] if abs(normal[0]) < 0.9 else [0.0, 1.0, 0.0])
        side /= np.linalg.norm(side)
        other = np.cross(normal, side)
        radius = self._distance * 0.012
        ring = [centre + (side * math.cos(a) + other * math.sin(a)) * radius
                for a in np.linspace(0.0, 2 * math.pi, 25)]
        triangles = [point for a, b in zip(ring, ring[1:]) for point in (centre, a, b)]
        self.set_geometry("point_grip", np.array(triangles, dtype=np.float32))

    def _point_grip_hit(self, position: QPointF) -> bool:
        """Liegt die Maus auf dem Griffpunkt (großzügig, damit er auf großen Bildschirmen leicht zu treffen ist)?"""
        if self._point_grip is None:
            return False
        spot = self.project(self._point_grip[0])
        reach = max(14.0, self.height() / 70.0)
        return (spot - position).manhattanLength() <= reach * 1.4

    def _point_on_grip_plane(self, x: float, y: float):
        """Schnittpunkt des Sehstrahls mit der Ebene des Griffpunkts (None, wenn er parallel läuft)."""
        eye, direction, _pixel = self.pick_ray(x, y)
        centre, normal = (np.array(v) for v in self._point_grip)
        slope = float(direction @ normal)
        if abs(slope) < 1e-9:
            return None
        return eye + direction * float((centre - eye) @ normal) / slope

    def set_canvases(self, canvases: list) -> None:
        """Leinwände: je Eintrag (4 Ecken links unten, rechts unten, rechts oben, links oben; QImage; Deckkraft)."""
        made = [entry for entry in self._canvas_gl if entry.get("texture") is not None]
        if made:
            # alte Texturen im eigenen Kontext freigeben (vor dem ersten Zeichnen gibt es noch keine)
            self.makeCurrent()
            for entry in made:
                entry["texture"].destroy()
            self.doneCurrent()
        self._canvas_gl = [{"corners": corners, "image": image, "opacity": opacity, "dirty": True}
                           for corners, image, opacity in canvases]
        self.update()

    def _release_canvases(self) -> None:
        """Gibt die Leinwand-Texturen im noch bestehenden Kontext frei; beim nächsten Zeichnen entstehen sie neu."""
        self.makeCurrent()
        for entry in self._canvas_gl:
            if entry.get("texture") is not None:
                entry["texture"].destroy()
                entry["texture"] = None
            entry["dirty"] = True
        self.doneCurrent()

    def _paint_canvases(self, gl, view, projection) -> None:
        """Zeichnet die Leinwände halbdurchsichtig mit Tiefentest (das Modell davor verdeckt sie)."""
        if not self._canvas_gl:
            return
        program = self._canvas_program
        program.bind()
        program.setUniformValue("model_view", view)
        program.setUniformValue("projection", projection)
        program.setUniformValue1i("image", 0)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        # Liegt die Leinwand auf einer Körperfläche (Runde 81), wäre sie mit ihr gleich tief und flimmerte streifig:
        # ein Polygon-Versatz zieht sie beim Tiefenvergleich ein Stück zur Kamera (die Körperflächen liegen +1 dahinter)
        gl.glEnable(GL_POLYGON_OFFSET_FILL)
        gl.glPolygonOffset(-2.0, -4.0)
        for entry in self._canvas_gl:
            if entry["dirty"]:
                # zwei Dreiecke mit Bildkoordinaten; QImage zählt von oben, die Textur von unten
                a, b, c, d = (np.array(p, dtype=np.float32) for p in entry["corners"])
                data = np.array([[*a, 0, 0], [*b, 1, 0], [*c, 1, 1], [*a, 0, 0], [*c, 1, 1], [*d, 0, 1]],
                                dtype=np.float32)
                entry["vao"], entry["buffer"] = QOpenGLVertexArrayObject(), QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
                entry["vao"].create()
                entry["buffer"].create()
                entry["vao"].bind()
                entry["buffer"].bind()
                entry["buffer"].allocate(data.tobytes(), data.nbytes)
                program.enableAttributeArray(0)
                program.setAttributeBuffer(0, GL_FLOAT, 0, 3, 20)
                program.enableAttributeArray(1)
                program.setAttributeBuffer(1, GL_FLOAT, 12, 2, 20)
                entry["vao"].release()
                entry["buffer"].release()
                entry["texture"] = QOpenGLTexture(entry["image"].mirrored(False, True))
                entry["dirty"] = False
            program.setUniformValue1f("opacity", float(entry["opacity"]))
            entry["texture"].bind(0)
            entry["vao"].bind()
            gl.glDrawArrays(GL_TRIANGLES, 0, 6)
            entry["vao"].release()
            entry["texture"].release()
        gl.glPolygonOffset(1.0, 1.0)
        gl.glDisable(GL_POLYGON_OFFSET_FILL)
        gl.glDisable(GL_BLEND)
        program.release()

    def set_ghosts(self, triangles: np.ndarray | None, edges: np.ndarray | None = None) -> None:
        """Zeigt Körper grau durchscheinend (z. B. nicht gefüllte Zellen); None blendet sie aus."""
        empty = np.zeros((0, 3), dtype=np.float32)
        self._layers["ghost_volume"].alpha = 0.22
        self.set_geometry("ghost_volume", empty if triangles is None else triangles)
        self.set_geometry("ghost_edges", empty if edges is None else edges)

    def set_operation_preview(self, triangles: np.ndarray | None, edges: np.ndarray | None = None,
                              color: tuple = (0.95, 0.25, 0.22)) -> None:
        """Zeigt das Werkzeugvolumen einer Operation durchscheinend in dieser Farbe (None blendet es aus)."""
        empty = np.zeros((0, 3), dtype=np.float32)
        volume, outline = self._layers["operation_volume"], self._layers["operation_edges"]
        volume.color, volume.alpha = color, 0.38
        outline.color = color
        self.set_geometry("operation_volume", empty if triangles is None else triangles)
        self.set_geometry("operation_edges", empty if edges is None else edges)

    def commit_value_box(self) -> bool:
        """Übernimmt einen angefangenen Wert im Wertfeld am Pfeil (z. B. wenn Enter das Werkzeug bestätigt –
        die Eingabetaste ist fensterweit belegt und erreicht das Feld sonst nicht). True, wenn es etwas gab."""
        if not self._value_box.isVisible() or not self._value_box.hasFocus():
            return False
        self._value_box.interpretText()         # Text → Wert (meldet über valueChanged)
        self._value_box._finished()             # auch einen Ausdruck bzw. unveränderten Text sicher melden
        return True

    def _value_box_edited(self) -> None:
        """Im Wertfeld am Pfeil wurde eine Zahl oder ein Ausdruck bestätigt."""
        self._handle_expression = self._value_box.expression
        self.handle_changed.emit(self._value_box.value())

    def take_handle_expression(self) -> str:
        """Der zuletzt am Pfeil eingetippte Ausdruck (leer bei einer Zahl oder beim Ziehen); wird dabei gelöscht."""
        expression, self._handle_expression = self._handle_expression, ""
        return expression

    def set_highlight(self, points: list | None) -> None:
        """Hebt eine ebene Fläche (Eckpunkte im Raum) halb durchsichtig hervor; None blendet sie aus."""
        self._highlight = list(points) if points else None
        self.update()

    def set_rubber_band(self, points: list | None, touching: bool = False) -> None:
        """Zeigt den Auswahlrahmen der Skizze (vier Punkte im Raum) oder blendet ihn mit None aus."""
        self._rubber_band = (points, touching) if points else None
        self.update()

    def set_clip(self, plane: tuple | None) -> None:
        """Schnittanalyse: schneidet das Modell an der Ebene auf (None schaltet den Schnitt ab)."""
        self._clip = plane
        self.update()

    def show_info(self, text: str) -> None:
        """Zeigt einen mehrzeiligen Text oben links in der Ansicht (leerer Text blendet ihn aus)."""
        if not text:
            self._info.hide()
            return
        font = self._info.font()
        font.setPixelSize(max(14, int(self.height() * 0.0105)))
        self._info.setFont(font)
        self._info.setText(text)
        self._info.adjustSize()
        self._info.move(self.info_offset, 14)
        self._info.show()

    def set_hud(self, fields: list) -> None:
        """Zeigt die Maßfelder des gerade gezeichneten Elements: je Eintrag (Punkt, Text, aktiv, festgehalten)."""
        self._hud = list(fields)
        self.update()

    def confirmed_backward(self) -> bool:
        """Wurde das Maßfeld zuletzt mit Umschalt+Tab bestätigt?"""
        return bool(getattr(self._number_box, "backward", False))

    def _paint_hud(self) -> None:
        """Maßfelder wie Fusion: Kästchen mit dem Live-Wert an der Geometrie; das aktive blau umrandet,
        festgehaltene mit Schloss. Unter dem offenen Eingabefeld wird das aktive Feld nicht gezeichnet."""
        if not self._hud:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        # hud_scale: größere Maßfelder für die Aufnahme der Werkzeug-Clips (sonst 1)
        font.setPixelSize(int(max(13, int(self.height() * 0.0095)) * getattr(self, "hud_scale", 1.0)))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        # zuerst die Maß- und Hilfslinien mit Pfeilspitzen (Fusions gelbgrüne Maßfarbe), darüber die Felder
        dimension = QColor("#b8e44a")
        painter.setPen(QPen(dimension, 1.4))
        for entry in self._hud:
            lines, arrows = (entry[4], entry[5]) if len(entry) > 5 else ([], [])
            for a, b in lines:
                painter.drawLine(self.project(a), self.project(b))
            for tip, towards in arrows:
                head, tail = self.project(tip), self.project(towards)
                dx, dy = tail.x() - head.x(), tail.y() - head.y()
                norm = math.hypot(dx, dy)
                if norm < 1e-6:
                    continue
                ux, uy = dx / norm, dy / norm
                size = max(8.0, self.height() * 0.006)
                left = QPointF(head.x() + ux * size - uy * size * 0.4, head.y() + uy * size + ux * size * 0.4)
                right = QPointF(head.x() + ux * size + uy * size * 0.4, head.y() + uy * size - ux * size * 0.4)
                painter.setBrush(dimension)
                painter.drawPolygon(QPolygonF([head, left, right]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for entry in self._hud:
            point, text, active, locked = entry[:4]
            if active and self._number_box.isVisible():
                continue
            centre = self.project(point)
            lock = 14 if locked else 0
            width, height = metrics.horizontalAdvance(text) + 16 + lock, metrics.height() + 8
            box = QRectF(centre.x() - width / 2, centre.y() - height / 2, width, height)
            painter.setBrush(QColor(22, 25, 31, 230))
            painter.setPen(QPen(QColor("#4aa3ff") if active else QColor("#5d6575"), 2.0 if active else 1.0))
            painter.drawRoundedRect(box, 3, 3)
            painter.setPen(QColor("#ffffff") if active else QColor("#d8dce4"))
            painter.drawText(box.adjusted(lock, 0, 0, 0), Qt.AlignmentFlag.AlignCenter, text)
            if locked:
                # kleines Schloss links im Kästchen: Bügel und Körper
                x, y = box.left() + 6, box.center().y()
                painter.setPen(QPen(QColor("#ffd24a"), 1.4))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawArc(QRectF(x + 1, y - 7, 7, 8), 0, 180 * 16)
                painter.setBrush(QColor("#ffd24a"))
                painter.drawRect(QRectF(x, y - 3, 9, 7))
        painter.end()

    def set_labels(self, labels: list) -> None:
        """Zeigt Texte an Punkten im Modell: je Eintrag (Punkt, Text, hervorgehoben)."""
        self._labels = list(labels)
        self.update()

    def label_at(self, x: float, y: float) -> int | None:
        """Nummer des Textes unter dem Fensterpunkt (None, wenn dort keiner steht)."""
        for index, box in enumerate(self._label_boxes):
            if box.contains(x, y):
                return index
        return None

    def edit_number(self, point, value: float, callback, suffix: str = " mm", cancelled=None,
                    expression: str = "", resolver=None, initial_text: str | None = None,
                    minimum: float = 0.001, edited=None) -> None:
        """Öffnet am Punkt im Modell ein Eingabefeld; Eingabe ruft `callback(wert, ausdruck)`.

        `ausdruck` ist leer, wenn eine feste Zahl eingegeben wurde. `resolver(text)` rechnet
        Ausdrücke mit den Benutzerparametern aus. Esc bricht ab und ruft `cancelled()`.
        """
        self._number_point, self._number_callback, self._number_cancelled = point, callback, cancelled
        self._number_edited = edited            # `edited(text)` bei jedem getippten Zeichen
        box = self._number_box
        box.setRange(minimum, 1e5)
        box.setSuffix("" if expression else suffix)    # hinter einem Ausdruck stünde die Einheit falsch
        box._resolver = resolver or pm.evaluate
        pixels = max(15, int(self.height() * 0.011))
        box.set_expression(expression, value)
        # nach set_expression: das Feld färbt sich dort selbst ein, hier gilt das Aussehen der Ansicht
        box.base_style = theme.current().value_box + f"font-size:{pixels}px;"
        box.setStyleSheet(box.base_style)
        box.adjustSize()
        box.setMinimumWidth(max(box.width(), pixels * 9))      # Platz zum Eintippen eines Ausdrucks
        position = self.project(point)
        box.move(int(position.x() - box.width() / 2), int(position.y() - box.height() / 2))
        box.show()
        box.setFocus()
        if initial_text is None:
            box.selectAll()
        else:
            # Der Benutzer hat schon zu tippen begonnen: sein Text steht im Feld, die Marke dahinter
            box.lineEdit().setText(initial_text)
            box.lineEdit().setCursorPosition(len(initial_text))
            if edited is not None and initial_text:
                edited(initial_text)            # die erste Ziffer gilt schon

    def event(self, event) -> bool:
        """Tab beim Zeichnen springt (wie Fusion) ins aktive Maßfeld, statt den Fokus weiterzugeben. Eine Sitzung mit
        eigenen Tasten (Formen: Blender-Tasten) beansprucht diese vor den fensterweiten Kürzeln."""
        if (event.type() == QEvent.Type.ShortcutOverride and self.sketch_input is not None
                and hasattr(self.sketch_input, "wants_key") and self.sketch_input.wants_key(event)):
            event.accept()
            return True
        if (event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
                and self.sketch_input is not None and not self._number_box.isVisible()
                and hasattr(self.sketch_input, "typed") and self.sketch_input.typed(None)):
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        """Beim Skizzieren beginnt eine getippte Ziffer die Eingabe eines Maßes (wie in Fusion)."""
        if self._number_box.isVisible():
            # Das Maßfeld ist offen: Tasten, die noch hier ankommen, gehören dorthin. Gibt das Feld eine Taste
            # unbearbeitet zurück (sie landet dann wieder hier), endet die Weitergabe – sonst liefe sie endlos im Kreis.
            if getattr(self, "_forwarding_key", False):
                return
            self._forwarding_key = True
            try:
                self._number_box.setFocus()
                if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                    self._number_box.keyPressEvent(event)       # Tab/Umschalt+Tab bestätigt das Feld direkt
                else:
                    QCoreApplication.sendEvent(self._number_box, event)
            finally:
                self._forwarding_key = False
            return
        if (self.sketch_input is not None and hasattr(self.sketch_input, "key_pressed")
                and self.sketch_input.key_pressed(event)):
            return                  # die Sitzung hat die Taste verarbeitet (Formen: Pinsel, Radius, Stärke …)
        text = event.text()
        # Ziffern beginnen ein Maß; "=" öffnet das Feld leer, um einen Ausdruck oder Parameternamen einzutippen
        if (self.sketch_input is not None and text and (text.isdigit() or text in ",.=")
                and self.sketch_input.typed("" if text == "=" else text)):
            return
        if (self.sketch_input is None and text and (text.isdigit() or text in ",.-=") and self._handle is not None
                and self._value_box.isVisible()):
            # Wie in Fusion: Ziffern am offenen Werkzeug tippen das Maß des aktiven Pfeils ein ("=" für Ausdrücke)
            line = self._value_box.lineEdit()
            self._value_box.setFocus()
            typed = "" if text == "=" else text
            line.setText(typed)
            line.setCursorPosition(len(typed))      # vor die angehängte Einheit („ mm“), sonst landet die
            return                                  # nächste Ziffer dahinter
        super().keyPressEvent(event)

    def moved_since_press(self) -> bool:
        """Hat sich die Maus seit dem Drücken so weit bewegt, dass es ein Ziehen ist (kein Klick)?"""
        return self._dragged

    def _number_escaped(self) -> None:
        cancelled = self._number_cancelled
        self.close_number()
        if cancelled is not None:
            cancelled()

    def close_number(self) -> None:
        self._number_callback = self._number_cancelled = self._number_edited = None
        self._number_box.hide()
        self.setFocus()

    def confirmed_with_tab(self) -> bool:
        """Wurde das Maßfeld zuletzt mit Tab bestätigt (statt mit der Eingabetaste)?"""
        return self._number_box.via_tab

    def number_is_open(self) -> bool:
        return self._number_box.isVisible()

    def _number_confirmed(self, value: float) -> None:
        callback, expression = self._number_callback, self._number_box.expression
        self.close_number()
        if callback is not None:
            callback(value, expression)

    # --- Ansichtswürfel ---------------------------------------------------------

    def _cube_faces(self) -> list[tuple[str, tuple, list[QPointF]]]:
        """Die sichtbaren Seiten des Ansichtswürfels: (Beschriftung, Außennormale, Eckpunkte im Fenster)."""
        return [(label, normal, self._cube_cell(normal, (-1, 1), (-1, 1)))
                for label, normal in _CUBE_FACES.items() if self._cube_visible(normal)]

    def _cube_layout(self) -> tuple[float, QPointF]:
        """Halbe Kantenlänge des Ansichtswürfels in Bildpunkten und sein Mittelpunkt im Fenster."""
        size = max(46.0, self.height() * 0.045)
        return size, QPointF(self.width() - size * 2.1, size * 2.1)

    def _cube_visible(self, normal: tuple) -> bool:
        view, _projection = self._matrices()
        return view.mapVector(QVector3D(*[float(c) for c in normal])).z() > 0.05

    @staticmethod
    def _cube_basis(normal: tuple) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Außennormale einer Würfelseite und zwei Richtungen in ihr."""
        n = np.array(normal, dtype=float)
        a = np.array([n[1], n[2], n[0]])
        return n, a, np.cross(n, a)

    def _cube_cell(self, normal: tuple, across: tuple, along: tuple) -> list[QPointF]:
        """Eckpunkte eines Felds auf einer Würfelseite; `across`/`along` sind seine Grenzen (-1 … 1)."""
        view, _projection = self._matrices()
        size, centre = self._cube_layout()
        n, a, b = self._cube_basis(normal)
        corners = []
        for u, v in ((across[0], along[0]), (across[1], along[0]), (across[1], along[1]), (across[0], along[1])):
            corner = view.mapVector(QVector3D(*(n + a * u + b * v)))
            corners.append(QPointF(centre.x() + corner.x() * size, centre.y() - corner.y() * size))
        return corners

    def _cube_regions(self) -> list[tuple[tuple, list[QPointF]]]:
        """Alle anklickbaren Felder des Würfels: (Blickrichtung, Eckpunkte).

        Jede sichtbare Seite hat neun Felder: die Mitte dreht auf die Seite, die Ränder auf die
        Kante zur Nachbarseite, die Ecken auf die Ecke (Schrägansicht).
        """
        bands = {-1: (-1.0, -_CUBE_RIM), 0: (-_CUBE_RIM, _CUBE_RIM), 1: (_CUBE_RIM, 1.0)}
        regions = []
        for normal in _CUBE_FACES.values():
            if not self._cube_visible(normal):
                continue
            n, a, b = self._cube_basis(normal)
            for u in (-1, 0, 1):
                for v in (-1, 0, 1):
                    direction = tuple(int(round(c)) for c in n + a * u + b * v)
                    regions.append((direction, self._cube_cell(normal, bands[u], bands[v])))
        return regions

    def _home_box(self) -> QRectF:
        """Feld neben dem Würfel, das zur Ausgangsansicht zurückführt."""
        size, centre = self._cube_layout()
        return QRectF(centre.x() - size * 2.6, centre.y() - size * 1.9, size * 0.62, size * 0.62)

    def _cube_face_at(self, position: QPointF):
        """Was unter dem Fensterpunkt am Ansichtswürfel liegt: eine Blickrichtung, "home" oder None."""
        if self._home_box().contains(position):
            return "home"
        for direction, corners in self._cube_regions():
            if QPolygonF(corners).containsPoint(position, Qt.FillRule.OddEvenFill):
                return direction
        return None

    def _cube_label(self, label: str, pixel_size: int) -> QImage:
        """Beschriftung einer Würfelseite als Bild (gemerkt je Text, Schriftgröße und Bildschirmskalierung)."""
        ratio = self.devicePixelRatioF()
        key = (label, pixel_size, ratio)
        cache = self.__dict__.setdefault("_cube_labels", {})
        if key not in cache:
            font = QFont(self.font())
            font.setPixelSize(pixel_size)
            font.setBold(True)
            metrics = QFontMetricsF(font)
            width, height = metrics.horizontalAdvance(label) + 2, metrics.height()
            image = QImage(max(1, math.ceil(width * ratio)), max(1, math.ceil(height * ratio)),
                           QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(ratio)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            painter.setFont(font)
            painter.setPen(QColor("#1b1e24"))
            painter.drawText(QRectF(0, 0, width, height), Qt.AlignmentFlag.AlignCenter, label)
            painter.end()
            cache[key] = image
        return cache[key]

    def _paint_cube(self) -> None:
        """Zeichnet den Ansichtswürfel oben rechts samt Auswahlrahmen der Skizze (über dem fertigen Bild)."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._highlight:
            # z. B. die Ebene unter der Maus bei der Ebenenwahl: durchscheinend, das Modell bleibt sichtbar
            painter.setPen(QPen(QColor(255, 170, 60), 2))
            painter.setBrush(QColor(255, 170, 60, 70))
            painter.drawPolygon(QPolygonF([self.project(point) for point in self._highlight]))
        if self._rubber_band:
            # Auswahlrahmen: blau = nur ganz Enthaltenes, grün gestrichelt = auch Berührtes (wie in Fusion)
            points, touching = self._rubber_band
            color = QColor(110, 220, 130) if touching else QColor(90, 170, 255)
            pen = QPen(color, 1.5, Qt.PenStyle.DashLine if touching else Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 55))
            painter.drawPolygon(QPolygonF([self.project(point) for point in points]))
        pixel_size = max(11, int(self.height() * 0.0085))
        for label, normal, corners in self._cube_faces():
            polygon = QPolygonF(corners)
            painter.setPen(QColor("#11141a"))
            painter.setBrush(QColor("#c9d0db"))
            painter.drawPolygon(polygon)
            # Beschriftung als fertiges Bild: drawText kostete je Bild 6–9 ms (Runde 79), ein Bild nur Bruchteile
            image = self._cube_label(label, pixel_size)
            centre = polygon.boundingRect().center()
            size = image.deviceIndependentSize()
            painter.drawImage(QPointF(centre.x() - size.width() / 2, centre.y() - size.height() / 2), image)
        if isinstance(self._cube_hover, tuple):
            # Das Feld unter der Maus: Seite, Kante oder Ecke
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 210, 74, 190))
            for direction, corners in self._cube_regions():
                if direction == self._cube_hover:
                    painter.drawPolygon(QPolygonF(corners))
        # Haus: zurück zur Ausgangsansicht
        box = self._home_box()
        painter.setPen(QPen(QColor("#ffd24a") if self._cube_hover == "home" else QColor("#c9d0db"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        left, right, top, bottom, middle = box.left(), box.right(), box.top(), box.bottom(), box.center().x()
        painter.drawPolyline(QPolygonF([QPointF(left, box.center().y()), QPointF(middle, top),
                                        QPointF(right, box.center().y())]))
        painter.drawRect(QRectF(left + box.width() * 0.18, box.center().y(), box.width() * 0.64, box.height() / 2))
        self._paint_origin(painter)
        painter.end()

    def _paint_labels(self) -> None:
        """Zeichnet die Texte über das fertige Bild und merkt sich, wo sie stehen."""
        self._label_boxes = []
        if not self._labels:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(max(13, int(self.height() * 0.0095)))     # lesbar auch auf großen Bildschirmen
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        for point, text, highlighted in self._labels:
            centre = self.project(point)
            width, height = metrics.horizontalAdvance(text) + 10, metrics.height() + 4
            box = QRectF(centre.x() - width / 2, centre.y() - height / 2, width, height)
            self._label_boxes.append(box)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(27, 30, 36, 215))
            painter.drawRoundedRect(box, 4, 4)
            painter.setPen(QColor("#ffd24a") if highlighted else QColor("#e6e9ef"))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()

    # --- Bodenraster ----------------------------------------------------------

    def set_ground_visible(self, on: bool) -> None:
        self._ground_visible = on
        self.refresh_ground()
        self.update()

    def ground_step(self) -> float:
        """Rasterweite des Bodenrasters in mm, passend zum Zoom (darauf rasten aufgezogene Körper ein)."""
        _eye, _direction, pixel_angle = self.pick_ray(self.width() / 2, self.height() / 2)
        per_pixel = max(self._distance * pixel_angle, 1e-9)        # mm je Bildpunkt am Blickziel
        step = next((s for s in _GROUND_STEPS if s / per_pixel >= _GROUND_MIN_PIXELS), _GROUND_STEPS[-1])
        low_x, low_y, high_x, high_y = self.ground_patch()
        while max(high_x - low_x, high_y - low_y) / step > _GROUND_MAX_LINES and step < _GROUND_STEPS[-1]:
            step = _GROUND_STEPS[_GROUND_STEPS.index(step) + 1]
        return step

    def ground_patch(self) -> tuple[float, float, float, float]:
        """Ausdehnung des Rasterfelds: es umschließt das Modell (und was gerade aufgezogen wird) mit Rand."""
        return grid_patch([*self._ground_points, *self._ground_extra])

    def set_ground_extent(self, *arrays: np.ndarray) -> None:
        """Meldet, was im Modell liegt (Punktlisten): das Rasterfeld wächst und schrumpft damit."""
        points = []
        for array in arrays:
            if len(array):
                low, high = array.min(axis=0), array.max(axis=0)
                points += [(float(low[0]), float(low[1])), (float(high[0]), float(high[1]))]
        if points != self._ground_points:
            self._ground_points = points
            self.refresh_ground()

    def include_in_ground(self, points: list) -> None:
        """Schließt vorübergehend weitere Punkte ins Rasterfeld ein (leere Liste hebt das auf)."""
        points = [(float(point[0]), float(point[1])) for point in points]
        if points != self._ground_extra:
            self._ground_extra = points
            self.refresh_ground()

    def ground_point(self, x: float, y: float) -> tuple[float, float] | None:
        """Stelle auf dem Bodenraster (XY-Ebene) unter dem Fensterpunkt; None, wenn der Blick sie nicht trifft."""
        eye, direction, _pixel = self.pick_ray(x, y)
        if abs(direction[2]) < 1e-6:
            return None
        along = -eye[2] / direction[2]
        if along <= 0:
            return None
        return float(eye[0] + along * direction[0]), float(eye[1] + along * direction[1])

    def refresh_ground(self) -> None:
        """Zeichnet das Bodenraster neu: ein Feld um das Modell, die Rasterweite folgt dem Zoom.

        Beim Skizzieren ruht es: die Skizze zeigt das Raster ihrer eigenen Ebene.
        """
        empty = np.zeros((0, 3), dtype=np.float32)
        sketching = self.sketch_input is not None and not getattr(self.sketch_input, "uses_ground", False)
        if not self._ground_visible or sketching:
            for name in ("ground_grid", "ground_grid_major", "ground_axis_x", "ground_axis_y"):
                self.set_geometry(name, empty)
            return
        step = self._ground_step = self.ground_step()
        patch_low_x, patch_low_y, patch_high_x, patch_high_y = self.ground_patch()
        # nach außen auf Hauptlinien runden, damit das Feld mit einer kräftigen Linie abschließt
        block = 5 * step
        first_x, last_x = math.floor(patch_low_x / block) * 5, math.ceil(patch_high_x / block) * 5
        first_y, last_y = math.floor(patch_low_y / block) * 5, math.ceil(patch_high_y / block) * 5
        low_x, high_x, low_y, high_y = first_x * step, last_x * step, first_y * step, last_y * step
        fine, major = [], []
        # jede fünfte Linie ist eine Hauptlinie; die Linien durch den Ursprung gehören den Achsen
        for index in range(first_x, last_x + 1):
            if index:
                (major if index % 5 == 0 else fine).extend([(index * step, low_y, 0.0), (index * step, high_y, 0.0)])
        for index in range(first_y, last_y + 1):
            if index:
                (major if index % 5 == 0 else fine).extend([(low_x, index * step, 0.0), (high_x, index * step, 0.0)])
        self.set_geometry("ground_grid", np.array(fine, dtype=np.float32).reshape(-1, 3))
        self.set_geometry("ground_grid_major", np.array(major, dtype=np.float32).reshape(-1, 3))
        x_axis = [(low_x, 0.0, 0.0), (high_x, 0.0, 0.0)] if first_y <= 0 <= last_y else []
        y_axis = [(0.0, low_y, 0.0), (0.0, high_y, 0.0)] if first_x <= 0 <= last_x else []
        self.set_geometry("ground_axis_x", np.array(x_axis, dtype=np.float32).reshape(-1, 3))
        self.set_geometry("ground_axis_y", np.array(y_axis, dtype=np.float32).reshape(-1, 3))

    def _paint_origin(self, painter: QPainter) -> None:
        """Markiert den Ursprung auf dem Bodenraster: heller Punkt im Ring (auch durch Körper hindurch sichtbar)."""
        if not self._ground_visible:
            return
        view, _projection = self._matrices()
        if view.map(QVector3D(0, 0, 0)).z() >= 0:
            return                      # der Ursprung liegt hinter der Kamera
        centre = self.project((0.0, 0.0, 0.0))
        size = max(5.0, self.height() * 0.0045)
        painter.setPen(QPen(QColor(20, 22, 28), 1.5))
        painter.setBrush(QColor(240, 243, 248))
        painter.drawEllipse(centre, size, size)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(240, 243, 248), 1.5))
        painter.drawEllipse(centre, size * 2.0, size * 2.0)

    def set_view(self, yaw: float, pitch: float) -> None:
        """Stellt die Blickrichtung ein (Drehwinkel um die Hochachse und Neigung in Grad)."""
        # Genau von oben oder unten ist erlaubt (Ansichtswürfel); beim Drehen mit der Maus bleibt 1° Abstand
        self._yaw, self._pitch = yaw, max(-90.0, min(90.0, pitch))
        # Wie Fusions „Perspektive mit orthogonalen Flächen“: genau entlang einer Achse (Würfelseite, Draufsicht)
        # wird ohne Fluchtpunkt gezeichnet – die Fläche erscheint perfekt zweidimensional
        self._ortho = _axis_aligned(self._yaw, self._pitch)
        self._refresh_handle()
        self.update()
        self.view_changed.emit()

    def fit(self, *arrays: np.ndarray) -> None:
        """Richtet die Kamera so aus, dass alle übergebenen Punkte sichtbar sind."""
        filled = [a for a in arrays if len(a)]
        if not filled:
            return
        points = np.concatenate(filled)
        low, high = points.min(axis=0), points.max(axis=0)
        self._target = QVector3D(*((low + high) / 2).tolist())
        self._distance = max(float(np.linalg.norm(high - low)) * 1.4, 1.0)
        self._refresh_handle()
        self.update()
        self.view_changed.emit()

    # --- OpenGL -----------------------------------------------------------

    def initializeGL(self) -> None:
        self._program = QOpenGLShaderProgram(self)
        self._program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _VERTEX_SHADER)
        self._program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _FRAGMENT_SHADER)
        if not self._program.link():
            raise RuntimeError(f"Shader konnten nicht gebunden werden: {self._program.log()}")
        # Zweites Programm für Linien: derselbe Aufbau, dazwischen die Verbreiterung
        self._line_program = QOpenGLShaderProgram(self)
        self._line_program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _VERTEX_SHADER)
        self._line_program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Geometry, _LINE_GEOMETRY_SHADER)
        self._line_program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _FRAGMENT_SHADER)
        if not self._line_program.link():
            raise RuntimeError(f"Linien-Shader konnten nicht gebunden werden: {self._line_program.log()}")
        # Programm für glatt schattierte Ebenen (Normalen je Eckpunkt)
        self._smooth_program = QOpenGLShaderProgram(self)
        self._smooth_program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _SMOOTH_VERTEX_SHADER)
        self._smooth_program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _SMOOTH_FRAGMENT_SHADER)
        if not self._smooth_program.link():
            raise RuntimeError(f"Glatt-Shader konnten nicht gebunden werden: {self._smooth_program.log()}")
        # Drittes Programm für die Leinwände (Bilder mit Textur)
        self._canvas_program = QOpenGLShaderProgram(self)
        self._canvas_program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _CANVAS_VERTEX_SHADER)
        self._canvas_program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _CANVAS_FRAGMENT_SHADER)
        if not self._canvas_program.link():
            raise RuntimeError(f"Leinwand-Shader konnten nicht gebunden werden: {self._canvas_program.log()}")
        for entry in self._canvas_gl:
            entry["dirty"] = True       # neuer Kontext: Texturen und Puffer neu anlegen
        # Texturen freigeben, solange der Kontext noch besteht (sonst warnt Qt beim Schließen)
        self.context().aboutToBeDestroyed.connect(self._release_canvases)
        for layer in self._layers.values():
            layer.vao.create()
            layer.buffer.create()
            # Nach einem Neuaufbau des OpenGL-Kontexts alles erneut hochladen (auch die Eckennummern)
            layer.dirty = True
            layer.index_buffer = None

    def _upload(self, layer: _Layer) -> None:
        layer.vao.bind()
        layer.buffer.bind()
        data = layer.data
        if layer.indices is not None:
            # Eckennummern der Dreiecke in einen eigenen Puffer, der zum VAO gehört (nur bei neuer Topologie)
            if layer.index_buffer is None:
                layer.index_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)
                layer.index_buffer.create()
                layer.indices_dirty = True
            layer.index_buffer.bind()
            if layer.indices_dirty:
                layer.index_buffer.allocate(layer.indices.tobytes(), layer.indices.nbytes)
                layer.indices_dirty = False
        if layer.normals is not None:
            # Lage und Normale abwechselnd je Eckpunkt (6 Gleitkommazahlen)
            both = np.ascontiguousarray(np.hstack([data, layer.normals]), dtype=np.float32)
            layer.buffer.allocate(both.tobytes(), both.nbytes)
            self._smooth_program.enableAttributeArray(0)
            self._smooth_program.setAttributeBuffer(0, GL_FLOAT, 0, 3, 24)
            self._smooth_program.enableAttributeArray(1)
            self._smooth_program.setAttributeBuffer(1, GL_FLOAT, 12, 3, 24)
        else:
            layer.buffer.allocate(data.tobytes(), data.nbytes)
            self._program.enableAttributeArray(0)
            self._program.setAttributeBuffer(0, GL_FLOAT, 0, 3, 12)
            self._program.disableAttributeArray(1)
        layer.vao.release()
        layer.buffer.release()
        layer.count = len(layer.indices) if layer.indices is not None else len(data)
        layer.dirty = False

    def view_direction(self) -> tuple[float, float, float]:
        """Blickrichtung der Kamera im Modell (vom Auge zum Zielpunkt), als Einheitsvektor."""
        view, _projection = self._matrices()
        inverse, _ok = view.inverted()
        direction = inverse.mapVector(QVector3D(0.0, 0.0, -1.0)).normalized()
        return direction.x(), direction.y(), direction.z()

    def _matrices(self) -> tuple[QMatrix4x4, QMatrix4x4]:
        projection = QMatrix4x4()
        aspect = self.width() / max(self.height(), 1)
        if getattr(self, "_ortho", False):
            # Parallelprojektion gleicher Bildgröße wie die Perspektive in Zielentfernung
            half = self._distance * math.tan(math.radians(_FIELD_OF_VIEW) / 2)
            projection.ortho(-half * aspect, half * aspect, -half, half, -self._distance * 100.0, self._distance * 100.0)
        else:
            projection.perspective(_FIELD_OF_VIEW, aspect, self._distance * 0.01, self._distance * 100.0)
        view = QMatrix4x4()
        view.translate(0, 0, -self._distance)
        view.rotate(-90 + self._pitch, 1, 0, 0)   # Z-Achse des Modells zeigt nach oben
        view.rotate(-self._yaw, 0, 0, 1)
        view.translate(-self._target)
        return view, projection

    def paintGL(self) -> None:
        gl = self.context().functions()
        # QPainter (Würfel, Maßfelder, Texte) lässt das Mischen eingeschaltet. Ohne Zurücksetzen würden deckende
        # Flächen im nächsten Bild mit der zuletzt gesetzten Mischfunktion gezeichnet – nach einer halbdurchsichtigen
        # Vorschau (z. B. Kombinieren) erschien das Modell dann durchscheinend (Runde 80)
        gl.glDisable(GL_BLEND)
        gl.glClearColor(*theme.current().background, 1.0)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        gl.glEnable(GL_DEPTH_TEST)
        # Gleich tiefe Bildpunkte dürfen überschreiben, sonst bliebe die Auswahl unsichtbar
        gl.glDepthFunc(GL_LEQUAL)
        # Flächen leicht nach hinten schieben, damit Kanten nicht flimmern
        gl.glEnable(GL_POLYGON_OFFSET_FILL)
        gl.glPolygonOffset(1.0, 1.0)

        view, projection = self._matrices()
        # Linien wachsen mit der Fensterhöhe, damit sie auf großen Bildschirmen sichtbar bleiben
        line_scale = max(1.0, self.height() / _LINE_SCALE_HEIGHT)
        for program in (self._program, self._smooth_program, self._line_program):
            program.bind()
            program.setUniformValue("model_view", view)
            program.setUniformValue("projection", projection)
        # Uniforms gelten für das gerade gebundene Programm: das Linienprogramm muss hier gebunden sein. Seit Runde 94
        # stand das Glatt-Programm zuletzt in der Schleife – die Linien bekamen keine Fenstergröße und verschwanden
        # alle (Bodenraster, Achsen, Kanten), Runde 97
        self._line_program.bind()
        self._line_program.setUniformValue("window_size", QVector2D(self.width(), self.height()))
        active = None
        # Farbige Körperflächen zuerst: was „obenauf“ gezeichnet wird (Griffe, Skizzen), muss danach kommen
        ordered = sorted(self._layers.items(), key=lambda item: not item[0].startswith("faces#"))
        hidden = _STYLE_HIDDEN.get(self.display_style, ())
        # Zwei Durchgänge (Runde 81): erst das Modell, dann die Leinwände, zuletzt alles „obenauf“ (Griffe, Auswahl,
        # Skizzen). So liegen die Ziehgriffe einer Leinwand über dem halbdurchsichtigen Bild statt darunter.
        passes = [(False, layer_item) for layer_item in ordered if not layer_item[1].on_top] + [(None, None)] + \
                 [(True, layer_item) for layer_item in ordered if layer_item[1].on_top]
        for stage, item in passes:
            if stage is None:
                if active is not None:
                    active.release()
                    active = None
                gl.glDisable(GL_CLIP_DISTANCE0)
                self._paint_canvases(gl, view, projection)
                continue
            name, layer = item
            if name in hidden or (self.display_style == "wireframe" and name.startswith("faces#")):
                continue        # von der gewählten Darstellung ausgeblendet
            if not layer.vao.isCreated():
                # Ebenen, die erst nach dem Start entstanden sind (farbige Körper)
                layer.vao.create()
                layer.buffer.create()
            if layer.dirty:
                self._upload(layer)
            if not layer.count:
                continue
            program = self._line_program if layer.mode == GL_LINES else \
                self._smooth_program if layer.normals is not None else self._program
            if program is not active:
                program.bind()
                active = program
                # Die Linienflächen dürfen nicht wie die Körperflächen nach hinten rücken
                (gl.glDisable if layer.mode == GL_LINES else gl.glEnable)(GL_POLYGON_OFFSET_FILL)
            program.setUniformValue("color", QVector3D(*layer.color))
            cut = self._clip is not None and (name in _CLIPPED_LAYERS or name.startswith("faces#"))
            (gl.glEnable if cut else gl.glDisable)(GL_CLIP_DISTANCE0)
            if cut:
                program.setUniformValue("clip_plane", QVector4D(*self._clip))
            program.setUniformValue1i("lit", int(layer.lit))
            if layer.mode == GL_LINES:
                program.setUniformValue1f("line_width", float(layer.line_width * line_scale))
            layer.vao.bind()
            if layer.on_top:
                gl.glDisable(GL_DEPTH_TEST)
            if layer.alpha < 1.0:
                # feste Deckkraft für die ganze Ebene: das Modell dahinter bleibt sichtbar
                gl.glEnable(GL_BLEND)
                gl.glBlendColor(0.0, 0.0, 0.0, layer.alpha)
                gl.glBlendFunc(GL_CONSTANT_ALPHA, GL_ONE_MINUS_CONSTANT_ALPHA)
            if layer.indices is not None:
                gl.glDrawElements(layer.mode, layer.count, _GL_UNSIGNED_INT, _NO_OFFSET)
            else:
                gl.glDrawArrays(layer.mode, 0, layer.count)
            if layer.alpha < 1.0:
                gl.glDisable(GL_BLEND)
            if layer.on_top:
                gl.glEnable(GL_DEPTH_TEST)
            layer.vao.release()
        if active is not None:
            active.release()
        gl.glDisable(GL_CLIP_DISTANCE0)
        # Die Texte liegen über allem: ohne Tiefentest, sonst verdeckt sie das Modell
        gl.glDisable(GL_DEPTH_TEST)
        gl.glDisable(GL_POLYGON_OFFSET_FILL)
        self._paint_labels()
        self._paint_hud()
        if getattr(self, "cube_visible", True):     # die Aufnahmen der Werkzeug-Clips zeigen ihn nicht
            self._paint_cube()

    # --- Maus -------------------------------------------------------------

    # --- Ziehpfeil ----------------------------------------------------------

    def apply_theme(self) -> None:
        """Übernimmt die Farben des geltenden Designs (Hinweisfelder, Wertfeld, Hintergrund, Pfeile)."""
        look = theme.current()
        self._info.setStyleSheet(look.overlay + "border:1px solid #ffd24a;")
        self._hint.setStyleSheet(look.overlay.replace("padding:6px 9px", "padding:2px 5px") + "border:1px solid #5aa9ff;")
        self._value_box.setStyleSheet(look.value_box + (f"font-size:{self._value_font_pixels}px;"
                                                        if self._value_font_pixels else ""))
        # Bodenraster: Linien etwas heller (dunkles Design) bzw. dunkler (helles Design) als der Hintergrund
        towards = 1.0 if sum(look.background) / 3 < 0.5 else 0.0
        for name, share in (("ground_grid", 0.10), ("ground_grid_major", 0.22)):
            self._layers[name].color = tuple(part + (towards - part) * share for part in look.background)
        # Netz beim Formen und Importkörper: Grau bzw. leicht in der Farbe des Betriebssystems statt Braun
        for name in ("sculpt_mesh", "import_faces"):
            self._layers[name].color = theme.mesh_body_colour()
        if self._handles:
            self.set_handles(self._handles, follow=False)      # nur neu einfärben
        self.update()

    def set_handles(self, handles: list, active: int | None = None, follow: bool = True) -> None:
        """Zeigt die Ziehpfeile (siehe handles.Handle); eine leere Liste blendet sie aus.

        Das Wertfeld gehört zum aktiven Pfeil: dem zuletzt gegriffenen, anfangs dem ersten. Mit `follow` weicht die
        Kamera zurück, bis alle Griffe im Bild liegen (beim Setzen durch ein Werkzeug, nicht beim Zoomen).
        """
        self._handles = list(handles)[:_MAX_HANDLES]
        if active is not None:
            self._active = active
        self._active = min(self._active, max(len(self._handles) - 1, 0))
        self._handle = self._handles[self._active] if self._handles else None
        for index in range(_MAX_HANDLES):
            layer = self._layers[f"handle{index}"]
            if index < len(self._handles):
                # alle Griffe im selben Blau wie in Fusion; die Achse zeigt die Richtung des Pfeils. Das
                # Verschiebe-Kreuz zeigt wie Fusions Verschieben die Achsfarben (X rot, Y grün, Z blau)
                handle = self._handles[index]
                color = handle.color if getattr(handle, "gizmo", False) or getattr(handle, "tinted", False) \
                    else theme.current().handle
                if index == self._hover or (self._handle_drag and index == self._active):
                    # Der Pfeil unter der Maus bzw. der gezogene leuchtet heller
                    color = tuple(min(1.0, c + 0.35) for c in color)
                layer.color = color
                self.set_geometry(f"handle{index}", self._arrow(self._handles[index]))
            else:
                self.set_geometry(f"handle{index}", np.zeros((0, 3), dtype=np.float32))
        if self._handle is None:
            self._value_box.hide()
            return
        if follow and not self._handle_drag and self._keep_handles_in_view():
            return      # die Kamera wurde verstellt und alles bereits neu gezeichnet
        handle = self._handle
        expression = getattr(handle, "expression", "")
        unit = getattr(handle, "unit", "") or (" °" if handle.kind == ANGLE else " mm")
        if not self._value_box.hasFocus():
            # tippt man gerade im Feld, bleibt der getippte Text stehen (das Modell läuft trotzdem mit)
            self._value_box.setSuffix("" if expression else unit)
            self._value_box.set_expression(expression, handle.value * handle.factor)
        self._value_box.setStyleSheet(theme.current().value_box + (f"font-size:{self._value_font_pixels}px;"
                                                                   if self._value_font_pixels else ""))
        self._value_box.show()
        self._place_value_box()

    def _keep_handles_in_view(self) -> bool:
        """Weicht mit der Kamera zurück, bis alle Pfeilköpfe im Bild liegen; True, wenn verstellt wurde."""
        if self.width() < 50 or self.height() < 50:
            return False    # noch nicht angezeigt
        margin = 40
        for _ in range(_ZOOM_OUT_STEPS):
            if all(self._inside(point, margin) for point in self._handle_extent()):
                return False
            self._distance *= 1.2
            if all(self._inside(point, margin) for point in self._handle_extent()):
                # Mit der neuen Entfernung alles neu zeichnen (Pfeilgröße hängt von ihr ab)
                self.set_handles(self._handles)
                self.update()
                return True
        return False

    def _handle_extent(self) -> list[QPointF]:
        """Fensterpunkte, die im Bild liegen sollen: die Griffstellen, bei Winkeln der ganze Bogen."""
        points = [self._handle_grip(handle) for handle in self._handles]
        for handle in self._handles:
            if handle.kind == ANGLE:
                points += [self.project(point) for point in self._handle_line(handle)]
        return points

    def _inside(self, point: QPointF, margin: int) -> bool:
        return margin <= point.x() <= self.width() - margin and margin <= point.y() <= self.height() - margin

    def _handle_under(self, position: QPointF) -> int | None:
        """Nummer des Pfeils unter dem Fensterpunkt (Kopf oder Schaft), sonst None.

        Die Greifweite wächst mit der Fenstergröße, damit der Pfeil auch auf
        hochauflösenden Bildschirmen gut zu treffen ist.
        """
        reach = max(_HANDLE_GRIP_PIXELS, self.height() * _HANDLE_GRIP_SHARE)
        best, best_distance = None, reach
        for index, handle in enumerate(self._handles):
            # Abstand des Mauspunkts zur Mittellinie des Griffs auf dem Bildschirm
            line = [self.project(point) for point in self._handle_line(handle)]
            for start, end in zip(line, line[1:]):
                along = end - start
                length = along.x() ** 2 + along.y() ** 2
                offset = position - start
                share = 0.0 if length < 1e-9 else max(0.0, min(1.0, (offset.x() * along.x() + offset.y() * along.y()) / length))
                nearest = start + along * share
                distance = math.hypot(position.x() - nearest.x(), position.y() - nearest.y())
                if distance <= best_distance:
                    best, best_distance = index, distance
        return best

    def _angle_point(self, handle, degrees: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Punkt des Winkelbogens beim Winkel sowie dort die Richtung nach außen und die
        Richtung, in die der Winkel wächst."""
        axis, reference = np.array(handle.direction, dtype=float), np.array(handle.reference, dtype=float)
        side = np.cross(axis, reference)
        angle = math.radians(degrees)
        outward = math.cos(angle) * reference + math.sin(angle) * side
        tangent = -math.sin(angle) * reference + math.cos(angle) * side
        # Etwas weiter außen als das Gedrehte, damit der Bogen frei liegt und gut zu greifen ist
        radius = handle.radius + self._distance * (_ANGLE_RADIUS + handle.lead)
        return np.array(handle.base, dtype=float) + radius * outward, outward, tangent

    def _handle_head(self, handle) -> tuple[np.ndarray, np.ndarray]:
        """Fußpunkt des Pfeilkopfs und die Richtung, in die er zeigt."""
        # Pfeile des Verschiebe-Kreuzes zeigen immer in die positive Achsrichtung (wie in Fusion)
        sign = 1.0 if handle.value >= 0 or getattr(handle, "gizmo", False) else -1.0
        if handle.kind == ANGLE:
            point, _outward, tangent = self._angle_point(handle, handle.value)
            return point, tangent * sign
        direction = np.array(handle.direction, dtype=float) * sign
        return np.array(handle.tip(), dtype=float) + direction * self._distance * handle.lead, direction

    def _handle_line(self, handle) -> list[np.ndarray]:
        """Mittellinie des Griffs im Raum: Schaft bzw. Bogen bis zur Spitze des Pfeilkopfs."""
        tip, direction = self._handle_head(handle)
        if getattr(handle, "gizmo", False):
            # Verschiebe-Kreuz: Pfeil ab der Körpermitte, Drehgriff nur sein Ringstück (nicht der durchlaufene Weg)
            if handle.kind == ANGLE:
                return [self._angle_point(handle, handle.value - _GIZMO_RING / 2 + _GIZMO_RING * step / 6)[0]
                        for step in range(7)]
            return [np.array(handle.tip(), dtype=float), tip, tip + direction * self._distance * _ARROW_SIZE]
        if handle.kind == ANGLE:
            steps = max(1, int(abs(handle.value) / 6.0))
            line = [self._angle_point(handle, handle.value * step / steps)[0] for step in range(steps + 1)]
        else:
            line = [np.array(handle.base, dtype=float), tip]
        return line + [tip + direction * self._distance * _ARROW_SIZE]

    def active_handle(self):
        """Der Pfeil, den das Wertfeld und das Ziehen gerade betreffen (oder None)."""
        return self._handle

    @staticmethod
    def _frame(direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Zwei Einheitsvektoren senkrecht zur Richtung (und zueinander)."""
        side = np.cross(direction, [0.0, 0.0, 1.0])
        if np.linalg.norm(side) < 1e-6:
            side = np.cross(direction, [1.0, 0.0, 0.0])
        side = side / np.linalg.norm(side)
        return side, np.cross(direction, side)

    @staticmethod
    def _tube(start, end, first, second, radius_start: float, radius_end: float, closed: bool = True) -> list:
        """Dreiecke eines Rohrs oder Kegelstumpfs zwischen zwei Punkten, auf Wunsch mit Deckeln."""
        angles = np.linspace(0.0, 2.0 * math.pi, _HANDLE_SEGMENTS, endpoint=False)
        ring = [first * math.cos(angle) + second * math.sin(angle) for angle in angles]
        triangles = []
        for current, following in zip(ring, ring[1:] + ring[:1]):
            low_a, low_b = start + current * radius_start, start + following * radius_start
            high_a, high_b = end + current * radius_end, end + following * radius_end
            triangles += [low_a, low_b, high_b, low_a, high_b, high_a]
            if closed:
                triangles += [low_b, low_a, start, high_a, high_b, end]
        return triangles

    def _arrow(self, handle) -> np.ndarray:
        """Dreiecke eines Ziehpfeils: dünne Maßlinie vom Fußpunkt zur Fläche, davor ein kurzer kräftiger Pfeil."""
        if handle.kind == ANGLE:
            return self._gizmo_ring(handle) if getattr(handle, "gizmo", False) else self._arc_arrow(handle)
        if getattr(handle, "gizmo", False):
            return self._gizmo_arrow(handle)
        base = np.array(handle.base, dtype=float)
        tip, direction = self._handle_head(handle)
        # Größe passend zur Entfernung der Kamera, damit der Pfeil auf dem Bildschirm gleich groß bleibt
        size = self._distance * _ARROW_SIZE
        side, other = self._frame(direction)
        start, neck, head = tip + direction * size * _ARROW_GAP, tip + direction * size * _ARROW_NECK, tip + direction * size
        triangles = self._tube(start, neck, side, other, size * _SHAFT_RADIUS, size * _SHAFT_RADIUS)
        triangles += self._tube(neck, head, side, other, size * _HEAD_RADIUS, 0.0)
        if np.linalg.norm(tip - base) > 1e-9:
            triangles += self._tube(base, tip, side, other, size * _GUIDE_RADIUS, size * _GUIDE_RADIUS, closed=False)
        return np.array(triangles, dtype=np.float32)

    def _gizmo_arrow(self, handle) -> np.ndarray:
        """Pfeil des Verschiebe-Kreuzes (wie Fusion): kräftiger Schaft von der Körpermitte nach außen, Kegelspitze;
        keine Maßlinie – das ganze Kreuz wandert mit dem Körper."""
        centre = np.array(handle.tip(), dtype=float)
        start, direction = self._handle_head(handle)
        size = self._distance * _ARROW_SIZE
        side, other = self._frame(direction)
        neck, head = start + direction * size * _ARROW_NECK, start + direction * size
        triangles = self._tube(centre + direction * size * 0.15, neck, side, other,
                               size * _SHAFT_RADIUS * 0.55, size * _SHAFT_RADIUS * 0.55)
        triangles += self._tube(neck, head, side, other, size * _HEAD_RADIUS, 0.0)
        return np.array(triangles, dtype=np.float32)

    def _gizmo_ring(self, handle) -> np.ndarray:
        """Drehgriff des Verschiebe-Kreuzes (wie Fusion): ein Ringstück um die Achse, das mit dem Winkel mitläuft,
        in der Mitte ein Knauf zum Greifen."""
        size = self._distance * _ARROW_SIZE
        axis = np.array(handle.direction, dtype=float)
        steps = 10
        degrees = [handle.value - _GIZMO_RING / 2 + _GIZMO_RING * step / steps for step in range(steps + 1)]
        points = [self._angle_point(handle, value) for value in degrees]
        triangles = []
        for (start, outward, _t), (end, _o, _tangent) in zip(points, points[1:]):
            triangles += self._tube(start, end, outward, axis, size * _ARC_RADIUS * 1.3, size * _ARC_RADIUS * 1.3,
                                    closed=False)
        # Knauf genau auf dem Ring beim aktuellen Winkel (dort greift man: siehe _handle_grip)
        centre, direction = self._handle_head(handle)
        radius = size * _KNOB_RADIUS * 0.55
        side, other = self._frame(direction)
        rings = 6
        for band in range(rings):
            low, high = math.pi * band / rings, math.pi * (band + 1) / rings
            triangles += self._tube(centre - direction * radius * math.cos(low), centre - direction * radius * math.cos(high),
                                    side, other, radius * math.sin(low), radius * math.sin(high), closed=False)
        return np.array(triangles, dtype=np.float32)

    def _arc_arrow(self, handle) -> np.ndarray:
        """Dreiecke eines Winkelgriffs: dünner Bogen von 0 bis zum Winkel, am Ende ein Kugelgriff zum Anfassen."""
        size = self._distance * _ARROW_SIZE
        axis = np.array(handle.direction, dtype=float)
        triangles = []
        steps = max(1, int(abs(handle.value) / 6.0))
        points = [self._angle_point(handle, handle.value * step / steps) for step in range(steps + 1)]
        for (start, outward, _t), (end, _o, _tangent) in zip(points, points[1:]):
            triangles += self._tube(start, end, outward, axis, size * _ARC_RADIUS, size * _ARC_RADIUS, closed=False)
        # Kugelgriff dort, wo der Bogen gegriffen wird (ein Stück in Drehrichtung hinter seinem Ende)
        tip, direction = self._handle_head(handle)
        centre, radius = tip + direction * size / 2, size * _KNOB_RADIUS
        if steps and np.linalg.norm(centre - tip) > 1e-9:
            _point, outward, _tangent = points[-1]
            triangles += self._tube(tip, centre, outward, axis, size * _ARC_RADIUS, size * _ARC_RADIUS, closed=False)
        side, other = self._frame(direction)
        rings = 6
        for band in range(rings):
            low, high = math.pi * band / rings, math.pi * (band + 1) / rings
            triangles += self._tube(centre - direction * radius * math.cos(low), centre - direction * radius * math.cos(high),
                                    side, other, radius * math.sin(low), radius * math.sin(high), closed=False)
        return np.array(triangles, dtype=np.float32)

    def project_many(self, points: np.ndarray) -> np.ndarray:
        """Fensterpositionen vieler Raumpunkte auf einmal (n × 3 → n × 2); Punkte hinter der Kamera werden nan."""
        view, projection = self._matrices()
        matrix = np.array((projection * view).data(), dtype=float).reshape(4, 4).T     # Qt speichert spaltenweise
        points = np.asarray(points, dtype=float).reshape(-1, 3)
        mapped = np.c_[points, np.ones(len(points))] @ matrix.T
        w = mapped[:, 3]
        with np.errstate(divide="ignore", invalid="ignore"):
            x, y = mapped[:, 0] / w, mapped[:, 1] / w
        result = np.c_[(x + 1) / 2 * self.width(), (1 - y) / 2 * self.height()]
        result[w <= 1e-9] = np.nan
        return result

    def project(self, point) -> QPointF:
        """Fensterposition eines Punkts im Raum."""
        view, projection = self._matrices()
        mapped = (projection * view).map(QVector3D(*[float(c) for c in point]))
        return QPointF((mapped.x() + 1) / 2 * self.width(), (1 - mapped.y()) / 2 * self.height())

    def _handle_grip(self, handle=None) -> QPointF | None:
        """Fensterposition der Stelle, an der ein Pfeil gegriffen wird (Mitte des Pfeilkopfs).

        Ohne Angabe ist der aktive Pfeil gemeint.
        """
        handle = handle or self._handle
        if handle is None:
            return None
        tip, direction = self._handle_head(handle)
        if handle.kind == ANGLE and getattr(handle, "gizmo", False):
            return self.project(tip)        # Drehgriff des Verschiebe-Kreuzes: Knauf auf dem Ring
        return self.project(tip + direction * self._distance * _ARROW_SIZE / 2)

    def _place_value_box(self) -> None:
        grip = self._handle_grip()
        if grip is not None:
            # Schriftgröße wächst mit der Fensterhöhe (lesbar auch auf 4K); über das
            # Stylesheet, weil dieses eine per setFont gesetzte Schrift überstimmt
            pixels = max(15, int(self.height() * 0.011))
            if pixels != self._value_font_pixels:
                self._value_font_pixels = pixels
                self._value_box.setStyleSheet(theme.current().value_box + f"font-size:{pixels}px;")
            self._value_box.adjustSize()
            # rechts neben dem Pfeil, mit so viel Abstand, dass das Feld die Pfeilspitze nicht verdeckt
            self._value_box.move(int(grip.x()) + 14 + int(self.height() * 0.03), int(grip.y()) - self._value_box.height() // 2)

    def _handle_value_at(self, x: float, y: float) -> float | None:
        """Wert entlang des Pfeils, der dem Mauspunkt am nächsten liegt (None, wenn nicht bestimmbar)."""
        origin, ray, _pixel = self.pick_ray(x, y)
        base, direction = np.array(self._handle.base, dtype=float), np.array(self._handle.direction, dtype=float)
        if self._handle.kind == ANGLE:
            # Winkel: Mausstrahl mit der Ebene senkrecht zur Drehachse schneiden
            slope = float(ray @ direction)
            if abs(slope) < 0.05:       # Blick fast in der Ebene: kein brauchbarer Schnittpunkt
                return None
            hit = origin + ray * (float((base - origin) @ direction) / slope) - base
            reference = np.array(self._handle.reference, dtype=float)
            degrees = math.degrees(math.atan2(float(hit @ np.cross(direction, reference)), float(hit @ reference)))
            # Den Umlauf wählen, der am aktuellen Wert liegt (sonst spränge der Winkel bei ±180°)
            current = self._handle.value + (self._handle_offset if self._handle_drag_started else 0.0)
            return degrees + 360.0 * round((current - degrees) / 360.0)
        w = origin - base
        b = float(ray @ direction)
        denominator = 1.0 - b * b
        if denominator < 1e-9:      # Blick genau entlang des Pfeils
            return None
        return float((direction @ w - b * (ray @ w)) / denominator)

    def pick_ray(self, x: float, y: float) -> tuple[np.ndarray, np.ndarray, float]:
        """Strahl durch den Fensterpunkt: (Auge, Richtung, Winkel eines Bildpunkts im Bogenmaß)."""
        view, projection = self._matrices()
        inverse, _ok = (projection * view).inverted()
        # Fensterpunkt in normierte Gerätekoordinaten (-1 … 1), y zeigt dort nach oben
        nx, ny = 2.0 * x / max(self.width(), 1) - 1.0, 1.0 - 2.0 * y / max(self.height(), 1)
        near = inverse.map(QVector3D(nx, ny, -1.0))
        far = inverse.map(QVector3D(nx, ny, 1.0))
        eye_matrix, _ok = view.inverted()
        eye = eye_matrix.map(QVector3D(0, 0, 0))
        direction = np.array([far.x() - near.x(), far.y() - near.y(), far.z() - near.z()])
        direction /= np.linalg.norm(direction)
        if getattr(self, "_ortho", False):
            # parallele Strahlen: Ausgangspunkt auf Höhe des Fensterpunkts, so weit zurück wie die Kamera
            start = np.array([near.x(), near.y(), near.z()])
            target = np.array([self._target.x(), self._target.y(), self._target.z()])
            on_plane = start + direction * float((target - start) @ direction)
            origin = on_plane - direction * self._distance
            pixel_angle = 2.0 * math.tan(math.radians(_FIELD_OF_VIEW) / 2) / max(self.height(), 1)
            return origin, direction, pixel_angle
        pixel_angle = 2.0 * math.tan(math.radians(_FIELD_OF_VIEW) / 2) / max(self.height(), 1)
        return np.array([eye.x(), eye.y(), eye.z()]), direction, pixel_angle

    def _orbiting(self, buttons, modifiers) -> bool:
        """Dreht diese Kombination aus Tasten die Ansicht? Links nur im Orbit-Modus bzw. bei der Einstellung
        „Ansicht drehen“ (sonst Rahmenauswahl), dazu die eingestellte Belegung."""
        if buttons & Qt.MouseButton.LeftButton:
            return self.left_drag == "orbit" or self.left_mode == "orbit"
        if self.orbit_binding == "right":
            return bool(buttons & Qt.MouseButton.RightButton)
        wanted = Qt.KeyboardModifier.ControlModifier if self.orbit_binding == "ctrl_middle" else Qt.KeyboardModifier.ShiftModifier
        return bool(buttons & Qt.MouseButton.MiddleButton) and bool(modifiers & wanted)

    def _gesture_allowed(self) -> bool:
        """Wischen ist möglich, wenn die rechte Taste nicht dreht und gerade nichts gezeichnet wird."""
        if self.orbit_binding == "right":
            return False
        if self.sketch_input is None:
            return True
        return not getattr(self.sketch_input, "is_drawing", lambda: True)()

    def mouseDoubleClickEvent(self, event) -> None:
        """Doppelklick: beim Skizzieren wählt er den ganzen Zug; sonst wie ein gewöhnliches Drücken."""
        # Qt meldet einen Doppelklick schon, wenn zwei Klicks schnell aufeinander folgen – auch an verschiedenen
        # Stellen. Ein echter Doppelklick liegt dort, wo der erste Klick losgelassen wurde.
        previous = getattr(self, "_last_release", None)
        same_spot = previous is not None and (event.position() - previous).manhattanLength() <= 6
        if self.sketch_input is not None and event.button() == Qt.MouseButton.LeftButton and same_spot \
                and getattr(getattr(self.sketch_input, "canvas", None), "tool", "") == "Auswählen":
            # ein echter Doppelklick liegt an derselben Stelle wie das Drücken davor
            # nur beim Auswählen: beim Zeichnen ist der zweite Klick eines schnellen Doppelklicks ein Punkt
            self.sketch_input.double_click(event.position().x(), event.position().y())
            self._dragged = True            # das folgende Loslassen ist kein weiterer Klick
            return
        self.mousePressEvent(event)

    def mousePressEvent(self, event) -> None:
        self.pressed.emit()
        self._gesture = False
        self._last_mouse = event.position().toPoint()
        self._press_position = event.position()
        self._dragged = False
        self._cube_press = self._cube_face_at(event.position()) if event.button() == Qt.MouseButton.LeftButton else None
        if self._cube_press is not None:
            self._sketch_drag = False
            return              # der Klick gehört dem Ansichtswürfel
        if event.button() == Qt.MouseButton.LeftButton and self._point_grip_hit(event.position()):
            self._point_drag = True             # Griffpunkt greifen (z. B. Bohrung verschieben)
            self._sketch_drag = False
            return
        # Beim Skizzieren kann ein Punkt gegriffen und gezogen werden
        self._sketch_drag = (self.sketch_input is not None and event.button() == Qt.MouseButton.LeftButton
                             and self.sketch_input.drag_start(event.position().x(), event.position().y()))
        # Den Pfeil unter der Maus greifen (am Kopf oder irgendwo am Schaft)
        grabbed_index = self._handle_under(event.position())
        if event.button() == Qt.MouseButton.LeftButton and grabbed_index is not None:
            # Anklicken wechselt nur den aktiven Pfeil; die Kamera bleibt, wo sie ist (bis Runde 89 wich sie hier
            # zurück, sobald ein anderer Griff außerhalb des Bilds lag – die Ansicht sprang beim Klick)
            self.set_handles(self._handles, active=grabbed_index, follow=False)
            self._handle_drag = True
            # Erst wenn die Maus sich bewegt, wird gezogen; ein Klick ohne Bewegung wählt wie in Fusion die
            # Fläche darunter (der Pfeil der Schale z. B. sitzt mitten auf der Fläche, die man öffnen will)
            self._handle_moved = False
            # Der Griff sitzt etwas vor der Pfeilspitze; diesen Abstand beim Ziehen abziehen,
            # damit der Wert beim Anfassen nicht springt
            self._handle_drag_started = False
            grabbed = self._handle_value_at(event.position().x(), event.position().y())
            self._handle_offset = 0.0 if grabbed is None else grabbed - self._handle.value
            self._handle_drag_started = True

    def leaveEvent(self, event) -> None:
        """Verlässt die Maus die Ansicht, endet die Hervorhebung des Pfeils."""
        if self._hover is not None:
            self._hover = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.set_handles(self._handles, follow=False)      # nur die Hervorhebung, die Kamera bleibt
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._point_drag:
            self._point_drag = False
            self.point_released.emit()
            if self._dragged:
                return
            # nur angeklickt, nicht gezogen: wie ein gewöhnlicher Klick behandeln
        self.release_position = event.position()        # für Werkzeuge, die wissen müssen, wo losgelassen wurde
        self._last_release = event.position()           # für die Doppelklick-Erkennung
        if self._gesture:
            # Ende der Wischgeste: das Feld in Wischrichtung wird ausgelöst
            self._gesture = False
            self.gesture_released.emit(event.position().x(), event.position().y())
            return
        if self._cube_press is not None:
            pressed, self._cube_press = self._cube_press, None
            if not self._dragged and self._cube_face_at(event.position()) == pressed:
                if pressed == "home":
                    self.set_view(*_HOME_VIEW)
                    self.home_requested.emit()      # das Hauptfenster passt das Modell wieder ein
                else:
                    self.look_along(pressed)        # Blick auf die angeklickte Seite, Kante oder Ecke
            return
        if self._handle_drag:
            self._handle_drag = False
            self.set_handles(self._handles, follow=False)   # Kamera bleibt auch nach dem Loslassen stehen
            if getattr(self, "_handle_moved", False):
                self.handle_released.emit()
            if not getattr(self, "_handle_moved", True) and event.button() == Qt.MouseButton.LeftButton:
                additive = bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                                     | Qt.KeyboardModifier.ShiftModifier))
                self.clicked.emit(event.position().x(), event.position().y(), additive)
            return
        if self._sketch_drag and self.sketch_input is None:
            self._sketch_drag = False       # das Werkzeug wurde inzwischen beendet
        if self._sketch_drag:
            self._sketch_drag = False
            self.sketch_input.set_additive(bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                                                     | Qt.KeyboardModifier.ShiftModifier)))
            self.sketch_input.drag_end()
        if self.sketch_input is not None:
            # Beim Skizzieren setzt ein Linksklick einen Punkt, ein Rechtsklick beendet das Element
            if not self._dragged and event.button() == Qt.MouseButton.LeftButton:
                # Strg oder Umschalt erweitert die Auswahl
                self.sketch_input.set_additive(bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                                                         | Qt.KeyboardModifier.ShiftModifier)))
                self.sketch_input.press(event.position().x(), event.position().y())
            elif not self._dragged and event.button() == Qt.MouseButton.RightButton:
                # Wie in Fusion: ein angefangenes Element beendet der Rechtsklick, sonst öffnet er das Skizzenmenü.
                # Werkzeuge ohne `is_drawing` (Aufziehen eines Grundkörpers) brechen mit dem Rechtsklick ab.
                if getattr(self.sketch_input, "is_drawing", lambda: True)():
                    self.sketch_input.finish()
                else:
                    self.context_requested.emit(event.position().x(), event.position().y())
            return
        if event.button() == Qt.MouseButton.RightButton and not self._dragged:
            self.context_requested.emit(event.position().x(), event.position().y())
            return
        if event.button() == Qt.MouseButton.LeftButton and self._band is not None and self._band.isVisible():
            self._band.hide()
            additive = bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                                 | Qt.KeyboardModifier.ShiftModifier))
            self.box_selected.emit(self._press_position.x(), self._press_position.y(),
                                   event.position().x(), event.position().y(), additive)
            return
        if event.button() == Qt.MouseButton.LeftButton and not self._dragged:
            additive = bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                                 | Qt.KeyboardModifier.ShiftModifier))
            self.clicked.emit(event.position().x(), event.position().y(), additive)

    def mouseMoveEvent(self, event) -> None:
        if self._cube_press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            # Ansichtswürfel gedrückt und gezogen: die Ansicht dreht sich – egal ob skizziert, ein Werkzeug offen
            # ist oder gewählt wird (wie Fusion); ohne Bewegung bleibt es ein Klick auf die Würfelseite
            position = event.position().toPoint()
            delta = position - self._last_mouse
            self._last_mouse = position
            if (event.position() - self._press_position).manhattanLength() > _CLICK_SLOP:
                self._dragged = True
            if self._dragged:
                self._orbit_by(delta.x(), delta.y())
                self._ortho = False
                self._refresh_handle()
                self.update()
                self.view_changed.emit()
            return
        if self._point_drag:
            if (event.position() - self._press_position).manhattanLength() > _CLICK_SLOP:
                self._dragged = True
                point = self._point_on_grip_plane(event.position().x(), event.position().y())
                if point is not None:
                    self.point_dragged.emit(*[float(c) for c in point], event.position().x(), event.position().y())
            return
        if self.sketch_input is not None and not self._number_box.isVisible() and not self.hasFocus():
            self.setFocus()         # damit getippte Maße beim Zeichnen in der Ansicht ankommen
        if self._handle_drag:
            if not self._handle_moved:
                # unter der Klickschwelle bleibt es ein Klick: der Wert ändert sich noch nicht
                if (event.position() - self._press_position).manhattanLength() <= _CLICK_SLOP:
                    return
                self._handle_moved = True
                self._dragged = True
            value = self._handle_value_at(event.position().x(), event.position().y())
            if value is not None:
                # In Schritten rasten wie in Fusion; genaue Werte gehen über das Wertfeld
                step = _ANGLE_STEP if self._handle.kind == ANGLE else _HANDLE_STEP
                value = round((value - self._handle_offset) / step) * step
                if self._handle.minimum is not None:
                    value = max(self._handle.minimum, value)
                if self._handle.maximum is not None:
                    value = min(self._handle.maximum, value)
                self.handle_changed.emit(value * self._handle.factor)
            return
        position = event.position().toPoint()
        delta = position - self._last_mouse
        self._last_mouse = position
        # Erst ab ein paar Bildpunkten gilt die Bewegung als Ziehen und nicht mehr als Klick
        if (event.position() - self._press_position).manhattanLength() > _CLICK_SLOP:
            self._dragged = True
        buttons = event.buttons()
        if not buttons:
            cube = self._cube_face_at(event.position())
            if cube != self._cube_hover:
                self._cube_hover = cube
                self.update()
            # Zeigen, dass sich der Pfeil unter der Maus ziehen lässt: Mauszeiger und hellere Farbe
            hover = self._handle_under(event.position())
            if hover != self._hover:
                self._hover = hover
                self.setCursor(Qt.CursorShape.SizeAllCursor if hover is not None
                               else Qt.CursorShape.ArrowCursor)
                self.set_handles(self._handles, follow=False)  # Hervorhebung ohne Kamerasprung
            self.hovered.emit(event.position().x(), event.position().y())
        if buttons & Qt.MouseButton.RightButton and self.orbit_binding != "right":
            # rechte Taste gezogen: Wischgeste im runden Menü (das Menü öffnet sich an der Stelle des Drückens)
            if self._dragged and (self._gesture or self._gesture_allowed()):
                if not self._gesture:
                    self._gesture = True
                    self.context_requested.emit(self._press_position.x(), self._press_position.y())
                self.gesture_moved.emit(event.position().x(), event.position().y())
            return
        if self.sketch_input is not None and not buttons & (Qt.MouseButton.MiddleButton
                                                             | Qt.MouseButton.RightButton):
            # Beim Skizzieren folgt die Vorschau der Maus; gedreht wird nur mit der rechten Taste
            self.sketch_input.move(event.position().x(), event.position().y())
            return
        if (buttons & Qt.MouseButton.LeftButton and not buttons & Qt.MouseButton.MiddleButton and self.left_mode == ""
                and self.left_drag == "select" and self._dragged):
            # Rahmenauswahl wie in Fusion: von links nach rechts nur ganz Umschlossenes, von rechts nach links
            # alles Berührte (Rahmen dann gestrichelt dargestellt über die Form des Gummibands)
            if self._band is None:
                self._band = QRubberBand(QRubberBand.Shape.Rectangle, self)
            self._band.setGeometry(QRect(self._press_position.toPoint(), event.position().toPoint()).normalized())
            self._band.show()
            return
        orbiting = self._orbiting(buttons, event.modifiers())
        if buttons & Qt.MouseButton.LeftButton and self.left_mode == "zoom":
            # Zoomen aus der Navigationsleiste: nach oben ziehen nähert, nach unten entfernt
            self._distance *= math.pow(1.006, delta.y())
            self._refresh_handle()
            self.update()
            self.view_changed.emit()
            return
        if buttons & Qt.MouseButton.LeftButton and self.left_mode == "pan":
            orbiting = False        # Schwenken aus der Navigationsleiste: wie die mittlere Taste
        if (buttons & Qt.MouseButton.MiddleButton or self.left_mode == "pan" and buttons & Qt.MouseButton.LeftButton) \
                and not orbiting:
            # Verschieben in der Bildebene, Geschwindigkeit passend zum Abstand
            view, _ = self._matrices()
            inverse, _ok = view.inverted()
            step = self._distance * 0.0012
            right = inverse.mapVector(QVector3D(1, 0, 0))
            up = inverse.mapVector(QVector3D(0, 1, 0))
            self._target += right * (-delta.x() * step) + up * (delta.y() * step)
        elif orbiting:
            self._orbit_by(delta.x(), delta.y())
            self._ortho = False             # gedreht: wieder perspektivisch
        else:
            return
        self._refresh_handle()
        self.update()
        self.view_changed.emit()

    def _orbit_by(self, dx: float, dy: float) -> None:
        """Drehen mit der Maus. Wie in Fusion geht es frei über die Draufsicht (und Untersicht) hinaus – bis Runde 83
        hielt die Neigung bei ±89° an, die Ansicht blieb an der Draufsicht hängen (Nutzermeldung). Die Neigung läuft
        im Kreis (−180 … 180°); steht das Modell dabei auf dem Kopf, dreht die waagrechte Bewegung andersherum,
        damit das Modell weiter der Maus folgt."""
        upside_down = abs(self._pitch) > 90.0
        self._yaw -= dx * 0.4 * (-1.0 if upside_down else 1.0)
        self._pitch = (self._pitch + dy * 0.4 + 180.0) % 360.0 - 180.0

    def wheelEvent(self, event) -> None:
        """Zoomen wie in Fusion zum Punkt unter dem Mauszeiger: Kamera und Drehpunkt rücken gemeinsam auf ihn zu
        (Streckung um diesen Punkt). Er bleibt dabei unter dem Zeiger, und die Kamera nähert sich der getroffenen
        Oberfläche nur an – bisher zoomte das Rad auf den Drehpunkt, der oft im Körper liegt; die Kamera fuhr dann
        durch die Oberfläche und das Bild sprang (Nutzermeldung Runde 83)."""
        wheel = getattr(self.sketch_input, "wheel", None)
        if wheel is not None and wheel(event.angleDelta().y()):
            return          # die Sitzung nutzt das Rad selbst (Formen: Zahl der Schleifenschnitte wie in Blender)
        factor = math.pow(0.999, event.angleDelta().y())
        anchor = self._zoom_anchor(event.position())
        if anchor is not None:
            target = np.array([self._target.x(), self._target.y(), self._target.z()])
            moved = anchor + (target - anchor) * factor
            self._target = QVector3D(*(float(c) for c in moved))
        self._distance *= factor
        self._refresh_handle()
        self.update()
        self.view_changed.emit()

    def _zoom_anchor(self, position: QPointF) -> np.ndarray | None:
        """Der Punkt, auf den gezoomt wird: die vorderste Modellfläche unter dem Zeiger, sonst der Punkt der Ebene
        durch den Drehpunkt (quer zur Blickrichtung) unter dem Zeiger."""
        from . import picking
        origin, direction, _angle = self.pick_ray(position.x(), position.y())
        hit = picking.ray_triangles(origin, direction, self._zoom_triangles())
        if hit is not None:
            return origin + direction * hit[1]
        target = np.array([self._target.x(), self._target.y(), self._target.z()])
        facing = float(direction @ np.array(self.view_direction()))
        along = float((target - origin) @ direction)
        if along <= 0 or abs(facing) < 1e-6:
            return None
        return origin + direction * along

    def _zoom_triangles(self) -> np.ndarray:
        """Alle sichtbaren Flächendreiecke des Modells (n, 3, 3), gemerkt bis sich eine Flächenebene ändert."""
        layers = [layer for name, layer in self._layers.items()
                  if (name == "faces" or name.startswith("faces#") or name == "import_faces") and len(layer.data)]
        key = tuple(id(layer.data) for layer in layers)
        if getattr(self, "_zoom_cache", (None,))[0] != key:
            stacked = (np.concatenate([layer.data for layer in layers]) if layers
                       else np.zeros((0, 3), dtype=np.float32))
            self._zoom_cache = (key, stacked.reshape(-1, 3, 3))
        return self._zoom_cache[1]

    def _refresh_handle(self) -> None:
        """Zeichnet den Ziehpfeil nach einer Kamerabewegung neu (Pfeilkopfgröße und Wertfeld).

        Die Kamera folgt dabei nicht den Griffen (`follow=False`): sonst zoomte die Ansicht beim Hineinzoomen von
        selbst wieder heraus, sobald ein Pfeil den Bildrand berührt – das Bild sprang hin und her."""
        if self._handles:
            self.set_handles(self._handles, follow=False)
        if self._point_grip is not None:
            self._draw_point_grip()             # Griffpunkt bleibt auf dem Bildschirm gleich groß

