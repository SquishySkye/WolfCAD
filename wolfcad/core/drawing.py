# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Zeichnungsableitung: Ansichten der Körper mit verdeckten Kanten, auf einem Blatt angeordnet.

Jede Ansicht ist eine senkrechte Projektion in einer Blickrichtung. Sichtbare und verdeckte
Kanten kommen aus der Verdeckungsrechnung des Geometriekerns und werden als Linienzüge in
Blattkoordinaten (mm) ausgegeben. Das Blatt lässt sich als SVG schreiben.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace
from OCP.BRepTools import BRepTools_WireExplorer
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Line
from OCP.BRepLib import BRepLib
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.gp import gp_Ax2, gp_Dir, gp_Pln, gp_Pnt
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

Polyline = list[tuple[float, float]]

# Ansicht -> (Blickrichtung zum Betrachter hin, Richtung nach rechts im Bild)
VIEWS = {
    "Vorne": ((0, -1, 0), (1, 0, 0)),
    "Oben": ((0, 0, 1), (1, 0, 0)),
    "Rechts": ((1, 0, 0), (0, 1, 0)),
    "Links": ((-1, 0, 0), (0, -1, 0)),
    "Isometrisch": ((1, -1, 1), (1, 1, 0)),
}
# Anordnung der Ansichten: Ansicht -> (Spalte, Zeile). Methode 1 (in Europa üblich) klappt die Ansichten
# vom Betrachter weg: Draufsicht unter der Vorderansicht, Ansicht von links rechts daneben. Methode 3
# (amerikanisch) setzt jede Ansicht auf die Seite, von der man schaut.
METHODS = {
    "Methode 1 (Europa)": {"Vorne": (0, 0), "Oben": (0, 1), "Links": (1, 0), "Isometrisch": (1, 1),
                           "Schnitt A–A": (2, 0), "Schnitt B–B": (2, 1)},
    "Methode 3 (Amerika)": {"Oben": (0, 0), "Vorne": (0, 1), "Rechts": (1, 1), "Isometrisch": (1, 0),
                            "Schnitt A–A": (2, 1), "Schnitt B–B": (2, 0)},
}
SHEETS = {"A4 quer": (297.0, 210.0), "A3 quer": (420.0, 297.0)}
# Übliche Maßstäbe (Zeichnung : Wirklichkeit), vom größten zum kleinsten
SCALES = [(10, 1), (5, 1), (2, 1), (1, 1), (1, 2), (1, 5), (1, 10), (1, 20), (1, 50), (1, 100)]
_MARGIN, _TITLE_HEIGHT, _GAP = 12.0, 22.0, 26.0
_OWN_COLOR = "#0f7a3d"      # Farbe der selbst gesetzten Maße
_LOST_COLOR = "#c62828"     # Maße, deren Punkte es im Modell nicht mehr gibt
ALIGNED = "schräg"          # Maßart: wahrer Abstand längs der Verbindung der beiden Punkte
DETAIL = "Detail C"         # Name der ersten Detailansicht: ein Kreisausschnitt einer Ansicht, vergrößert gezeichnet
DETAIL_LETTERS = "CDEFGH"   # Buchstaben der Detailansichten (A und B gehören den Schnitten)


def detail_name(number: int) -> str:
    """Name der Detailansicht Nummer `number` (ab 0): „Detail C“, „Detail D“, …"""
    return f"Detail {DETAIL_LETTERS[number]}"


def is_detail(name: str) -> bool:
    return name.startswith("Detail ")
RADIUS = "radius"           # Maßart: Radius einer Rundung – erster Punkt ihre Mitte, zweiter ein Punkt auf dem Bogen
ANGLE = "winkel"            # Maßart: Winkel am ersten Punkt (Scheitel) zwischen dem zweiten und einem dritten Punkt
RESNAP_SHARE = 0.25         # so weit (Anteil der Ansichtsgröße) darf ein Maßpunkt beim Neuberechnen wandern


@dataclass
class View:
    """Eine projizierte Ansicht in Modellmaßen (mm), Ursprung links unten in ihrem Rahmen."""

    name: str
    visible: list[Polyline] = field(default_factory=list)
    hidden: list[Polyline] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0
    # Runde Flächen, auf die man genau längs ihrer Achse schaut: (x, y, Durchmesser)
    rounds: list[tuple[float, float, float]] = field(default_factory=list)
    # Schnittflächen einer Schnittansicht: je Fläche ihre Ränder (außen und Löcher) als Linienzüge
    hatches: list[list[Polyline]] = field(default_factory=list)
    # Rundungen (Teilzylinder wie Verrundungen), auf die man längs ihrer Achse schaut:
    # (Mitte x, Mitte y, Radius, Punkt x, Punkt y) – der Punkt liegt mitten auf dem Bogen
    arcs: list[tuple[float, float, float, float, float]] = field(default_factory=list)


def _polylines(compound) -> list[Polyline]:
    """Die Kanten eines Projektionsergebnisses als Linienzüge in der Bildebene."""
    if compound is None or compound.IsNull():
        return []
    BRepLib.BuildCurves3d_s(compound)       # die Projektion liefert zunächst nur Kurven in der Ebene
    lines = []
    explorer = TopExp_Explorer(compound, TopAbs_EDGE)
    while explorer.More():
        curve = BRepAdaptor_Curve(TopoDS.Edge(explorer.Current()))
        explorer.Next()
        sampler = GCPnts_QuasiUniformDeflection(curve, 0.02)
        if not sampler.IsDone() or sampler.NbPoints() < 2:
            continue
        lines.append([(sampler.Value(i).X(), sampler.Value(i).Y()) for i in range(1, sampler.NbPoints() + 1)])
    return lines


SECTION = "Schnitt A–A"     # Schnittansicht: mitten durch das Modell, Blick von vorn
CROSS_SECTION = "Schnitt B–B"       # Querschnitt dazu: quer durch das Modell, Blick von links
# Schnittansicht -> (Achse der Schnittebene, ihre Normale, Blickrichtung wie diese Ansicht)
_SECTIONS = {SECTION: (1, (0, 1, 0), "Vorne"), CROSS_SECTION: (0, (1, 0, 0), "Links")}


def _flat(direction: tuple, right: tuple, point) -> tuple[float, float]:
    """Lage eines Raumpunkts in der Bildebene der Ansicht (wie die Verdeckungsrechnung sie liefert)."""
    up = (direction[1] * right[2] - direction[2] * right[1], direction[2] * right[0] - direction[0] * right[2],
          direction[0] * right[1] - direction[1] * right[0])
    norm_right = math.sqrt(sum(c * c for c in right))
    norm_up = math.sqrt(sum(c * c for c in up)) or 1.0
    coords = (point.X(), point.Y(), point.Z())
    return (sum(c * r for c, r in zip(coords, right)) / norm_right, sum(c * u for c, u in zip(coords, up)) / norm_up)


def _rounds(shapes: list, direction: tuple, right: tuple, arcs: list | None = None) -> list[tuple[float, float, float]]:
    """Bohrungen und Wellen, auf die die Ansicht längs ihrer Achse schaut: Mitte in der Bildebene, Durchmesser.

    Teilzylinder (Verrundungen) sind keine Bohrungen: sie kommen, wenn `arcs` übergeben ist, dort hinein als
    (Mitte x, Mitte y, Radius, Punkt x, Punkt y) mit einem Punkt mitten auf dem Bogen.
    """
    length = math.sqrt(sum(c * c for c in direction))
    found = []
    for shape in shapes:
        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        while explorer.More():
            surface = BRepAdaptor_Surface(TopoDS.Face(explorer.Current()))
            explorer.Next()
            if surface.GetType() != GeomAbs_Cylinder:
                continue
            axis = surface.Cylinder().Axis()
            along = abs(sum(a * d for a, d in zip((axis.Direction().X(), axis.Direction().Y(), axis.Direction().Z()),
                                                  direction))) / length
            if along < 1 - 1e-9:
                continue
            x, y = _flat(direction, right, axis.Location())
            span = surface.LastUParameter() - surface.FirstUParameter()
            if span < 2 * math.pi - 1e-6:
                # nur ein Teil des Umfangs: eine Rundung, bemaßt mit ihrem Radius
                middle = surface.Value((surface.FirstUParameter() + surface.LastUParameter()) / 2,
                                       surface.FirstVParameter())
                on_arc = _flat(direction, right, middle)
                arc = (round(x, 6), round(y, 6), round(surface.Cylinder().Radius(), 6),
                       round(on_arc[0], 6), round(on_arc[1], 6))
                if arcs is not None and arc not in arcs:
                    arcs.append(arc)
                continue
            entry = (round(x, 6), round(y, 6), round(2 * surface.Cylinder().Radius(), 6))
            if entry not in found:
                found.append(entry)
    return found


def section_position(shapes: list, shift: float = 0.0, name: str = SECTION) -> float:
    """Lage der Schnittebene in mm: die Mitte des Modells, um `shift` verschoben (A–A längs y, B–B längs x)."""
    from . import solids        # hier eingebunden: die Körperwerkzeuge brauchen dieses Modul nicht
    axis = _SECTIONS[name][0]
    boxes = [solids._bounds(shape) for shape in shapes]
    return (min(b[axis] for b in boxes) + max(b[axis + 3] for b in boxes)) / 2 + shift


def section_frame(shapes: list, shift: float = 0.0, name: str = SECTION, angle: float = 0.0) -> tuple:
    """Lage eines Schnitts: (Normale der Schnittebene, Abstand vom Ursprung, Blickrichtung, Richtung nach rechts).

    `angle` (Grad) dreht den Schnitt A–A um die Hochachse; 0 heißt parallel zur Vorderansicht.
    """
    from . import solids
    axis, normal, like = _SECTIONS[name]
    direction, right = VIEWS[like]
    if name != SECTION or abs(angle) < 1e-9:
        return normal, section_position(shapes, shift, name), direction, right
    cosine, sine = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    normal, direction, right = (-sine, cosine, 0.0), (sine, -cosine, 0.0), (cosine, sine, 0.0)
    boxes = [solids._bounds(shape) for shape in shapes]
    centre = ((min(b[0] for b in boxes) + max(b[3] for b in boxes)) / 2,
              (min(b[1] for b in boxes) + max(b[4] for b in boxes)) / 2)
    return normal, centre[0] * normal[0] + centre[1] * normal[1] + shift, direction, right


def section(shapes: list, shift: float = 0.0, name: str = SECTION, angle: float = 0.0) -> View:
    """Schnittansicht: A–A parallel zur Vorderansicht geschnitten und von vorn gesehen, B–B quer dazu von links.

    Die Schnittebene liegt mittig, um `shift` mm vom Betrachter weg verschoben; `angle` dreht den Schnitt
    A–A um die Hochachse. Die Schnittflächen kommen als Ränder zurück und werden auf dem Blatt schraffiert.
    """
    from . import solids
    normal, middle, direction, right = section_frame(shapes, shift, name, angle)
    like = _SECTIONS[name][2]
    origin = [component * middle for component in normal]
    kept = [origin[i] + normal[i] for i in range(3)]       # behalten wird die Hälfte hinter der Schnittebene
    plane = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(*origin), gp_Dir(*normal))).Face()
    behind = BRepPrimAPI_MakeHalfSpace(plane, gp_Pnt(*kept)).Solid()
    halves = []
    for shape in shapes:
        cut = BRepAlgoAPI_Common(shape, behind)
        if cut.IsDone() and TopExp_Explorer(cut.Shape(), TopAbs_FACE).More():
            halves.append(cut.Shape())
    if not halves:
        return View(name)
    view = project(halves, like, name, (direction, right))
    low = _view_origin(halves, direction, right)
    for shape in shapes:
        face = solids.section_face(shape, normal, middle)
        if face is None:
            continue
        explorer = TopExp_Explorer(face, TopAbs_FACE)
        while explorer.More():
            rims = []
            wires = TopExp_Explorer(explorer.Current(), TopAbs_WIRE)
            while wires.More():
                rim = []
                walker = BRepTools_WireExplorer(TopoDS.Wire(wires.Current()))
                while walker.More():
                    curve = BRepAdaptor_Curve(walker.Current())
                    first, last = curve.FirstParameter(), curve.LastParameter()
                    if walker.Current().Orientation() == TopAbs_REVERSED:
                        first, last = last, first
                    steps = 1 if curve.GetType() == GeomAbs_Line else 24
                    for k in range(steps):
                        x, y = _flat(direction, right, curve.Value(first + (last - first) * k / steps))
                        rim.append((x - low[0], y - low[1]))
                    walker.Next()
                if len(rim) >= 3:
                    rims.append(rim)
                wires.Next()
            if rims:
                view.hatches.append(rims)
            explorer.Next()
    return view


def _view_origin(shapes: list, direction: tuple, right: tuple) -> tuple[float, float]:
    """Linke untere Ecke der Ansicht in der Bildebene (darauf beziehen sich ihre Linienzüge)."""
    lines = _raw_lines(shapes, direction, right)
    points = [point for group in lines for line in group for point in line]
    return (min(p[0] for p in points), min(p[1] for p in points)) if points else (0.0, 0.0)


def _raw_lines(shapes: list, direction: tuple, right: tuple) -> tuple[list[Polyline], list[Polyline]]:
    """Sichtbare und verdeckte Kanten der Körper in der Bildebene, noch unverschoben."""
    algorithm = HLRBRep_Algo()
    for shape in shapes:
        algorithm.Add(shape)
    algorithm.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*direction), gp_Dir(*right))))
    algorithm.Update()
    algorithm.Hide()
    result = HLRBRep_HLRToShape(algorithm)
    # Scharfe Kanten und Umrisse; glatte Übergänge (z. B. an Verrundungen) bleiben wie üblich weg
    return (_polylines(result.VCompound()) + _polylines(result.OutLineVCompound()),
            _polylines(result.HCompound()) + _polylines(result.OutLineHCompound()))


def project(shapes: list, name: str, label: str | None = None, frame: tuple | None = None) -> View:
    """Berechnet eine Ansicht der Körper mit sichtbaren und verdeckten Kanten.

    `label` benennt die Ansicht abweichend von ihrer Blickrichtung (z. B. die Schnittansicht); `frame` gibt
    Blickrichtung und Richtung nach rechts ausdrücklich vor (gedrehter Schnitt).
    """
    direction, right = frame or VIEWS[name]
    visible, hidden = _raw_lines(shapes, direction, right)
    points = [point for line in visible + hidden for point in line]
    if not points:
        return View(label or name)
    low_x, low_y = min(p[0] for p in points), min(p[1] for p in points)
    shift = lambda lines: [[(x - low_x, y - low_y) for x, y in line] for line in lines]
    raw_arcs: list = []
    rounds = [] if name == "Isometrisch" else [(x - low_x, y - low_y, diameter)
                                               for x, y, diameter in _rounds(shapes, direction, right, raw_arcs)]
    arcs = [] if name == "Isometrisch" else [(cx - low_x, cy - low_y, radius, px - low_x, py - low_y)
                                             for cx, cy, radius, px, py in raw_arcs]
    return View(label or name, shift(visible), shift(hidden),
                max(p[0] for p in points) - low_x, max(p[1] for p in points) - low_y, rounds, arcs=arcs)


def _clip_to_circle(lines: list[Polyline], cx: float, cy: float, radius: float) -> list[Polyline]:
    """Die Teile der Linienzüge, die innerhalb des Kreises liegen."""
    result = []
    for line in lines:
        piece: Polyline = []
        for (ax, ay), (bx, by) in zip(line, line[1:]):
            dx, dy = bx - ax, by - ay
            # Schnitt der Strecke mit dem Kreis: |a + t·d − c|² = r²
            fx, fy = ax - cx, ay - cy
            a = dx * dx + dy * dy
            if a < 1e-18:
                continue
            b, c = 2 * (fx * dx + fy * dy), fx * fx + fy * fy - radius * radius
            root = b * b - 4 * a * c
            if root <= 0:
                low = high = 2.0            # die Gerade verfehlt den Kreis
            else:
                low, high = (-b - math.sqrt(root)) / (2 * a), (-b + math.sqrt(root)) / (2 * a)
            start, end = max(0.0, low), min(1.0, high)
            if start >= end:
                if piece:
                    result.append(piece)
                    piece = []
                continue
            first, last = (ax + dx * start, ay + dy * start), (ax + dx * end, ay + dy * end)
            if piece and math.dist(piece[-1], first) > 1e-9:
                result.append(piece)
                piece = []
            piece = piece or [first]
            piece.append(last)
            if end < 1.0:                   # die Strecke verlässt den Kreis: der Zug endet hier
                result.append(piece)
                piece = []
        if piece:
            result.append(piece)
    return [piece for piece in result if len(piece) >= 2]


def detail_view(source: View, centre: tuple[float, float], radius: float, magnification: float,
                name: str = DETAIL) -> View:
    """Detailansicht: der Kreisausschnitt einer Ansicht, um `magnification` vergrößert, mit Kreis als Rand."""
    cx, cy = centre

    def placed(lines: list[Polyline]) -> list[Polyline]:
        return [[((x - cx + radius) * magnification, (y - cy + radius) * magnification) for x, y in line]
                for line in _clip_to_circle(lines, cx, cy, radius)]

    size = 2 * radius * magnification
    rim = [(size / 2 + size / 2 * math.cos(2 * math.pi * k / 72), size / 2 + size / 2 * math.sin(2 * math.pi * k / 72))
           for k in range(73)]
    return View(name, placed(source.visible) + [rim], placed(source.hidden), size, size)


def choose_scale(views: list[View], sheet: tuple[float, float], method: str | None = None) -> tuple[int, int]:
    """Der größte übliche Maßstab, bei dem alle Ansichten nebeneinander auf das Blatt passen."""
    usable_width = sheet[0] - 2 * _MARGIN
    usable_height = sheet[1] - 2 * _MARGIN - _TITLE_HEIGHT
    columns, rows = _grid(views, METHODS[method or next(iter(METHODS))])
    for drawing, real in SCALES:
        factor = drawing / real
        width = sum(max(v.width for v in column) * factor for column in columns) + _GAP * len(columns)
        height = sum(max(v.height for v in row) * factor for row in rows) + _GAP * len(rows)
        if width <= usable_width and height <= usable_height:
            return drawing, real
    return SCALES[-1]


def _grid(views: list[View], cells: dict) -> tuple[list[list[View]], list[list[View]]]:
    """Die Ansichten nach Spalten und nach Zeilen geordnet (gemäß der Anordnung `cells`)."""
    by_name = {view.name: view for view in views}
    columns = [[by_name[n] for n, (c, _r) in cells.items() if c == column and n in by_name] for column in (0, 1, 2)]
    rows = [[by_name[n] for n, (_c, r) in cells.items() if r == row and n in by_name] for row in (0, 1)]
    return [c for c in columns if c], [r for r in rows if r]


def _number(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


@dataclass
class Placed:
    """Eine Ansicht an ihrem Platz auf dem Blatt (mm, y nach unten)."""

    view: View
    left: float
    top: float
    bottom: float


@dataclass
class Sheet:
    """Ein angeordnetes Zeichenblatt: Größe, Maßstab und die platzierten Ansichten."""

    size: tuple[float, float]
    scale: tuple[int, int]
    method: str
    placed: list[Placed]
    cut: float | None = None        # Lage der Schnittlinie A–A in der Draufsicht (in deren Maßen), sonst None
    cross: float | None = None      # ebenso die Schnittlinie B–B (senkrecht in der Draufsicht)
    # Detailansichten: je {"ansicht": Name, "mitte": (x, y), "radius": r, "faktor": Vergrößerung, "name", "buchstabe"};
    # `detail` ist die erste davon (oder None)
    detail: dict | None = None
    details: list = field(default_factory=list)
    # Verlauf des Schnitts A–A in der Draufsicht: zwei Punkte in deren Maßen (auch für gedrehte Schnitte)
    cut_line: tuple | None = None

    @property
    def factor(self) -> float:
        return self.scale[0] / self.scale[1]

    def to_sheet(self, name: str, x: float, y: float) -> tuple[float, float] | None:
        """Blattkoordinaten eines Punkts, der in den Maßen der Ansicht `name` gegeben ist."""
        for placed in self.placed:
            if placed.view.name == name:
                return placed.left + x * self.factor, placed.bottom - y * self.factor
        return None

    def move(self, name: str, dx: float, dy: float) -> tuple[float, float]:
        """Verschiebt eine Ansicht auf dem Blatt um dx/dy mm (ohne sie neu zu berechnen).

        Zurück kommt die tatsächliche Verschiebung – am Blattrand wird sie gekappt.
        """
        for placed in self.placed:
            if placed.view.name == name:
                # die Ansicht bleibt innerhalb des Blattrahmens
                edge = _MARGIN / 2
                width = placed.view.width * self.factor
                dx = min(max(dx, edge - placed.left), max(self.size[0] - edge - width - placed.left, edge - placed.left))
                dy = min(max(dy, edge - placed.top), max(self.size[1] - edge - placed.bottom, edge - placed.top))
                placed.left, placed.top, placed.bottom = placed.left + dx, placed.top + dy, placed.bottom + dy
                return dx, dy
        return 0.0, 0.0

    def view_at(self, x: float, y: float, margin: float = 2.0) -> str | None:
        """Name der Ansicht, die an dieser Blattstelle liegt (zum Greifen und Verschieben)."""
        for placed in self.placed:
            right = placed.left + placed.view.width * self.factor
            if placed.left - margin <= x <= right + margin and placed.top - margin <= y <= placed.bottom + margin:
                return placed.view.name
        return None

    def from_sheet(self, name: str, x: float, y: float) -> tuple[float, float] | None:
        """Umkehrung von `to_sheet`: eine Blattstelle in den Maßen der Ansicht `name`."""
        for placed in self.placed:
            if placed.view.name == name:
                return (x - placed.left) / self.factor, (placed.bottom - y) / self.factor
        return None

    def snap_points(self) -> list[tuple[str, tuple[float, float], tuple[float, float]]]:
        """Punkte, an denen sich Maße ansetzen lassen: (Ansicht, Punkt in ihren Maßen, Punkt auf dem Blatt).

        Das sind die Enden aller sichtbaren Kanten und die Mitten von Bohrungen und Wellen.
        """
        found = []
        for placed in self.placed:
            view = placed.view
            if view.name == "Isometrisch" or is_detail(view.name):
                continue        # im Schrägbild sind Längen verzerrt, das Detail ist vergrößert
            points = {(round(line[i][0], 4), round(line[i][1], 4)) for line in view.visible for i in (0, -1)}
            points |= {(round(x, 4), round(y, 4)) for x, y, _diameter in view.rounds}
            # Rundungen: ihre Mitte und der Punkt mitten auf dem Bogen (für Radiusmaße)
            points |= {(round(arc[i], 4), round(arc[i + 1], 4)) for arc in view.arcs for i in (0, 3)}
            found += [(view.name, point, self.to_sheet(view.name, *point)) for point in sorted(points)]
        return found


def arrange(shapes: list, sheet_name: str = "A4 quer", view_names: list[str] | None = None,
            method: str | None = None, section_shift: float = 0.0, offsets: dict | None = None,
            cross_shift: float = 0.0, detail: dict | list | None = None, section_angle: float = 0.0) -> Sheet:
    """Berechnet die Ansichten, wählt den Maßstab und legt fest, wo jede Ansicht auf dem Blatt steht.

    `offsets` verschiebt einzelne Ansichten gegenüber ihrem üblichen Platz: {Ansicht: (dx, dy)} in mm.
    """
    if not shapes:
        raise ValueError("Es gibt keinen Körper, der gezeichnet werden könnte")
    sheet = SHEETS[sheet_name]
    # Ohne Angabe alle Ansichten; eine leere Auswahl bleibt leer und wird unten gemeldet
    method = method or next(iter(METHODS))
    cells = METHODS[method]
    # Ohne Angabe die vier üblichen Ansichten; die Schnittansicht kommt nur auf Wunsch dazu
    chosen = [name for name in cells if name not in _SECTIONS] if view_names is None else view_names
    shifts = {SECTION: section_shift, CROSS_SECTION: cross_shift}
    views = [section(shapes, shifts[name], name, section_angle) if name in _SECTIONS else project(shapes, name)
             for name in chosen if name in cells]
    has_section = any(view.name == SECTION and view.visible for view in views)
    has_cross = any(view.name == CROSS_SECTION and view.visible for view in views)
    views = [view for view in views if view.visible]
    if not views:
        raise ValueError("Keine Ansicht gewählt")
    drawing, real = choose_scale(views, sheet, method)
    factor = drawing / real
    column_width = {c: max((v.width * factor for v in views if cells[v.name][0] == c), default=0.0) for c in (0, 1, 2)}
    row_height = {r: max((v.height * factor for v in views if cells[v.name][1] == r), default=0.0) for r in (0, 1)}
    placed = []
    for view in views:
        column, row = cells[view.name]
        left = _MARGIN + _GAP / 2 + sum(column_width[c] + _GAP for c in range(column) if column_width[c])
        top = _MARGIN + _GAP / 2 + (row_height[0] + _GAP if row else 0.0)
        dx, dy = (offsets or {}).get(view.name, (0.0, 0.0))
        placed.append(Placed(view, left + dx, top + dy, top + dy + view.height * factor))
    cut, cut_line = None, None
    if has_section and any(view.name == "Oben" for view in views):
        top_points = [point for line in _raw_lines(shapes, *VIEWS["Oben"])[0] for point in line]
        low_x, low_y = min(point[0] for point in top_points), min(point[1] for point in top_points)
        if abs(section_angle) < 1e-9:
            cut = section_position(shapes, section_shift) - low_y
        # Verlauf des Schnitts in der Draufsicht: durch den Fußpunkt der Ebene, längs ihrer Richtung
        normal, middle, _direction, along = section_frame(shapes, section_shift, SECTION, section_angle)
        reach = 0.5 * math.hypot(max(p[0] for p in top_points) - low_x, max(p[1] for p in top_points) - low_y)
        foot = (normal[0] * middle, normal[1] * middle)
        # Fußpunkt längs der Schnittrichtung in die Mitte des Modells schieben
        centre = ((low_x + max(p[0] for p in top_points)) / 2, (low_y + max(p[1] for p in top_points)) / 2)
        slide = (centre[0] - foot[0]) * along[0] + (centre[1] - foot[1]) * along[1]
        foot = (foot[0] + along[0] * slide, foot[1] + along[1] * slide)
        cut_line = tuple((foot[0] + along[0] * side * reach - low_x, foot[1] + along[1] * side * reach - low_y)
                         for side in (-1.0, 1.0))
    cross = None
    if has_cross and any(view.name == "Oben" for view in views):
        low_x = min(point[0] for line in _raw_lines(shapes, *VIEWS["Oben"])[0] for point in line)
        cross = section_position(shapes, cross_shift, CROSS_SECTION) - low_x
    # Detailansichten rechts neben die übrigen Ansichten stellen, untereinander (verschiebbar wie jede andere)
    wanted = [detail] if isinstance(detail, dict) else list(detail or [])
    shown = []
    right = max(item.left + item.view.width * factor for item in placed)
    top = _MARGIN + _GAP / 2
    below = max(item.bottom for item in placed) + _GAP + 8.0       # Zeile unter allen Ansichten (für Platzmangel rechts)
    row_left = _MARGIN + _GAP / 2
    sources = {item.view.name: item.view for item in placed}
    for entry in wanted[:len(DETAIL_LETTERS)]:
        source = sources.get(entry.get("ansicht"))
        if source is None or entry.get("radius", 0) <= 1e-6 or entry.get("faktor", 0) <= 0:
            continue
        name = detail_name(len(shown))
        view = detail_view(source, tuple(entry["mitte"]), entry["radius"], entry["faktor"], name)
        width = view.width * factor
        dx, dy = (offsets or {}).get(name, (0.0, 0.0))
        if right + _GAP + width <= sheet[0] - _MARGIN:
            # rechts ist Platz: dort untereinander
            placed.append(Placed(view, right + _GAP + dx, top + dy, top + dy + view.height * factor))
            top += view.height * factor + _GAP
        else:
            # sonst in einer Zeile unter den Ansichten, damit nichts verdeckt wird
            placed.append(Placed(view, row_left + dx, below + dy, below + dy + view.height * factor))
            row_left += width + _GAP
        shown.append({**entry, "name": name, "buchstabe": name[-1]})
    return Sheet(sheet, (drawing, real), method, placed, cut, cross, shown[0] if shown else None, shown, cut_line)


def _own_dimension(layout: Sheet, entry) -> tuple[str, tuple[float, float], float, float] | None:
    """Zeichenvorschrift eines eigenen Maßes: (Linienzug, Textstelle auf dem Blatt, Textdrehung in Grad, Wert).

    Ein Eintrag ist (Ansicht, Punkt 1, Punkt 2[, Art[, Platz]]); ohne Art wird waagrecht oder senkrecht
    gemessen, je nachdem, was größer ist, mit `ALIGNED` der wahre Abstand längs der Verbindung. `Platz` ist
    ein Punkt, durch den die Maßlinie läuft (in den Maßen der Ansicht, also Modell-mm ab ihrer linken
    unteren Ecke, y nach unten) – so bleibt die Linie auch nach einem Maßstabswechsel an derselben Stelle
    des Teils; ohne ihn sucht sich das Maß selbst einen Platz neben der Ansicht.
    """
    name, first, second = entry[0], entry[1], entry[2]
    kind = entry[3] if len(entry) > 3 else ""
    spot = entry[4] if len(entry) > 4 else None
    place = next((item for item in layout.placed if item.view.name == name), None)
    if place is None:
        return None
    factor = layout.factor
    if spot is not None:
        spot = (spot[0] * factor, spot[1] * factor)         # ab hier in Blatt-mm
    (ax, ay), (bx, by) = layout.to_sheet(name, *first), layout.to_sheet(name, *second)
    centre_x, centre_y = place.left + place.view.width * factor / 2, (place.top + place.bottom) / 2
    length = math.hypot(bx - ax, by - ay)
    if kind == RADIUS:
        # Radiusmaß: Hinweislinie vom Bogen zur gewählten Stelle (sonst 7 mm nach außen), dort die Zahl
        if length < 1e-9:
            return None
        ux, uy = (bx - ax) / length, (by - ay) / length
        end = (place.left + spot[0], place.bottom + spot[1]) if spot is not None else (bx + ux * 7.0, by + uy * 7.0)
        outline = f"M{bx:.2f},{by:.2f} L{end[0]:.2f},{end[1]:.2f}"
        return outline, (end[0] + (4.0 if end[0] >= bx else -4.0), end[1] - 0.8), 0.0, math.dist(first, second)
    if kind == ANGLE:
        # Winkelmaß: Bogen um den Scheitel zwischen den beiden Schenkeln, die Zahl mitten am Bogen
        if len(entry) < 6 or not entry[5]:
            return None
        cx, cy = layout.to_sheet(name, *entry[5])
        second_length = math.hypot(cx - ax, cy - ay)
        if length < 1e-9 or second_length < 1e-9:
            return None
        start, end = math.atan2(by - ay, bx - ax), math.atan2(cy - ay, cx - ax)
        sweep = (end - start + math.pi) % (2 * math.pi) - math.pi      # der kleinere der beiden Winkel
        radius = max(6.0, min(22.0, 0.45 * min(length, second_length)))
        if spot is not None:
            # von Hand platziert: der Bogen läuft durch die gewählte Stelle
            radius = max(3.0, math.hypot(place.left + spot[0] - ax, place.bottom + spot[1] - ay))
        reach = radius + 2.0
        from_x, from_y = ax + radius * math.cos(start), ay + radius * math.sin(start)
        to_x, to_y = ax + radius * math.cos(end), ay + radius * math.sin(end)
        outline = (f"M{ax:.2f},{ay:.2f} L{ax + reach * math.cos(start):.2f},{ay + reach * math.sin(start):.2f} "
                   f"M{ax:.2f},{ay:.2f} L{ax + reach * math.cos(end):.2f},{ay + reach * math.sin(end):.2f} "
                   f"M{from_x:.2f},{from_y:.2f} A{radius:.2f},{radius:.2f} 0 0 {1 if sweep > 0 else 0} {to_x:.2f},{to_y:.2f}")
        middle = start + sweep / 2
        text = ax + (radius + 3.5) * math.cos(middle), ay + (radius + 3.5) * math.sin(middle) + 1.0
        return outline, text, 0.0, abs(math.degrees(sweep))
    if kind == ALIGNED and length > 1e-9:
        # Maßlinie parallel zur Verbindung, auf der von der Ansichtsmitte abgewandten Seite
        ux, uy = (bx - ax) / length, (by - ay) / length
        nx, ny = -uy, ux
        if ((ax + bx) / 2 - centre_x) * nx + ((ay + by) / 2 - centre_y) * ny < 0:
            nx, ny = -nx, -ny
        away = 9.0
        if spot is not None:
            # Abstand der Maßlinie so, dass sie durch den gewählten Platz läuft (auch auf die andere Seite)
            away = (place.left + spot[0] - ax) * nx + (place.bottom + spot[1] - ay) * ny
        over = away + math.copysign(1.5, away)
        outline = (f"M{ax:.2f},{ay:.2f} L{ax + nx * over:.2f},{ay + ny * over:.2f} "
                   f"M{bx:.2f},{by:.2f} L{bx + nx * over:.2f},{by + ny * over:.2f} "
                   f"M{ax + nx * away:.2f},{ay + ny * away:.2f} L{bx + nx * away:.2f},{by + ny * away:.2f}")
        angle = math.degrees(math.atan2(uy, ux))
        angle = angle - 180 if angle > 90 else angle + 180 if angle <= -90 else angle     # Schrift nie kopfüber
        up = math.sin(math.radians(angle)), -math.cos(math.radians(angle))                 # "über" der gedrehten Schrift
        text = (ax + bx) / 2 + nx * away + up[0], (ay + by) / 2 + ny * away + up[1]
        return outline, text, angle, math.dist(first, second)
    if abs(second[0] - first[0]) >= abs(second[1] - first[1]):
        # waagrechtes Maß: die Maßlinie liegt auf der Seite, auf der die Punkte näher am Rand der Ansicht sind
        above = (ay + by) / 2 < centre_y
        y = min(ay, by, place.top) - 6.0 if above else max(ay, by, place.bottom) + 14.0
        if spot is not None:
            y = place.bottom + spot[1]
        outline = f"M{ax:.2f},{ay:.2f} V{y:.2f} M{bx:.2f},{by:.2f} V{y:.2f} M{ax:.2f},{y:.2f} H{bx:.2f}"
        return outline, ((ax + bx) / 2, y - 1.0), 0.0, abs(second[0] - first[0])
    before = (ax + bx) / 2 < centre_x
    x = min(ax, bx, place.left) - 6.0 if before else max(ax, bx, place.left + place.view.width * factor) + 14.0
    if spot is not None:
        x = place.left + spot[0]
    outline = f"M{ax:.2f},{ay:.2f} H{x:.2f} M{bx:.2f},{by:.2f} H{x:.2f} M{x:.2f},{ay:.2f} V{by:.2f}"
    return outline, (x - 1.0, (ay + by) / 2), -90.0, abs(second[1] - first[1])


def dimension_anchors(layout: Sheet, extra: list) -> list[tuple[float, float] | None]:
    """Die Textstellen der eigenen Maße auf dem Blatt (None, wenn die Ansicht fehlt) – zum Anklicken."""
    shapes = [_own_dimension(layout, entry) for entry in extra]
    return [shape[1] if shape else None for shape in shapes]


def resnap(layout: Sheet, extra: list, sizes: dict | None = None) -> tuple[list, set[int]]:
    """Hängt eigene Maße nach einer Änderung des Modells an die aktuellen Fangpunkte.

    `sizes` nennt Breite und Höhe jeder Ansicht zu dem Zeitpunkt, als die Maße zuletzt stimmten; ist die
    Ansicht seither gewachsen oder geschrumpft, wandern die Punkte anteilig mit, bevor der nächste Fangpunkt
    gesucht wird. Zurück kommen die angepassten Einträge und die Nummern der Maße, deren Punkte verloren sind
    (kein Fangpunkt in der Nähe oder beide Punkte fielen zusammen) – diese behalten ihre alten Punkte.
    """
    by_view: dict[str, list] = {}
    for name, point, _sheet in layout.snap_points():
        by_view.setdefault(name, []).append(point)
    views = {placed.view.name: placed.view for placed in layout.placed}
    result, lost = [], set()
    for index, entry in enumerate(extra):
        entry = list(entry)
        view = views.get(entry[0])
        result.append(entry)
        if view is None:
            continue                    # Ansicht gerade ausgeblendet: das Maß wartet unverändert
        old = (sizes or {}).get(entry[0]) or (view.width, view.height)
        stretch = (view.width / old[0] if old[0] > 1e-9 else 1.0, view.height / old[1] if old[1] > 1e-9 else 1.0)
        reach = RESNAP_SHARE * max(view.width, view.height)
        if len(entry) > 3 and entry[3] == RADIUS:
            # Radiusmaß: die Mitte sucht ihren Fangpunkt, der Punkt auf dem Bogen folgt dem (neuen) Radius dort
            guess = entry[1][0] * stretch[0], entry[1][1] * stretch[1]
            curves = [(cx, cy, radius) for cx, cy, radius, _px, _py in view.arcs]
            curves += [(cx, cy, diameter / 2) for cx, cy, diameter in view.rounds]
            near = min(curves, key=lambda curve: math.dist(curve[:2], guess), default=None)
            away = math.dist(entry[1], entry[2])
            if near is None or math.dist(near[:2], guess) > reach or away < 1e-9:
                lost.add(index)
                continue
            direction = ((entry[2][0] - entry[1][0]) / away, (entry[2][1] - entry[1][1]) / away)
            entry[1] = [near[0], near[1]]
            entry[2] = [near[0] + direction[0] * near[2], near[1] + direction[1] * near[2]]
            continue
        moved = []
        third = len(entry) > 5 and bool(entry[5])           # Winkelmaße haben einen dritten Punkt
        for point in [*entry[1:3], *([entry[5]] if third else [])]:
            guess = point[0] * stretch[0], point[1] * stretch[1]
            near = min(by_view.get(entry[0], []), key=lambda candidate: math.dist(candidate, guess), default=None)
            if near is not None and math.dist(near, guess) <= reach:
                moved.append(list(near))
        if len(moved) == (3 if third else 2) and len({tuple(point) for point in moved}) == len(moved):
            entry[1], entry[2] = moved[0], moved[1]
            if third:
                entry[5] = moved[2]
        else:
            lost.add(index)
    return result, lost


def render(layout: Sheet, title: str, show_hidden: bool = True, dimensions: bool = True,
           extra: list | None = None, marks: list | None = None, lost: set | None = None) -> str:
    """Das angeordnete Blatt als SVG-Text (Einheit mm).

    `extra` sind eigene Maße: je Eintrag (Ansicht, Punkt 1, Punkt 2[, Art]) in den Maßen der Ansicht;
    `lost` nennt die Nummern der Maße, deren Punkte es nicht mehr gibt (rot gestrichelt mit Fragezeichen).
    `marks` sind hervorzuhebende Punkte auf dem Blatt (z. B. der erste angeklickte Punkt eines Maßes).
    """
    sheet, factor, method = layout.size, layout.factor, layout.method
    drawing, real = layout.scale
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{sheet[0]}mm" height="{sheet[1]}mm" '
        f'viewBox="0 0 {sheet[0]} {sheet[1]}" font-family="sans-serif">',
        f'<rect width="{sheet[0]}" height="{sheet[1]}" fill="white"/>',
        f'<rect x="{_MARGIN / 2}" y="{_MARGIN / 2}" width="{sheet[0] - _MARGIN}" height="{sheet[1] - _MARGIN}" '
        'fill="none" stroke="black" stroke-width="0.5"/>',
        # Schraffur der Schnittflächen: dünne Linien unter 45°
        '<defs><pattern id="schraffur" width="1.6" height="1.6" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="1.6" stroke="#333" stroke-width="0.14"/>'
        '</pattern></defs>',
    ]

    def path(lines: list[Polyline], left: float, bottom: float) -> str:
        # Im SVG zeigt y nach unten; `bottom` ist die Blatthöhe der Unterkante der Ansicht
        return " ".join("M" + " L".join(f"{left + x * factor:.3f},{bottom - y * factor:.3f}" for x, y in line)
                        for line in lines)

    for placed in layout.placed:
        view, left, top, bottom = placed.view, placed.left, placed.top, placed.bottom
        if show_hidden and view.hidden and view.name != "Isometrisch":
            parts.append(f'<path d="{path(view.hidden, left, bottom)}" fill="none" stroke="#555" '
                         'stroke-width="0.18" stroke-dasharray="1.6 0.9"/>')
        for rims in view.hatches:
            # Schnittfläche schraffiert; Löcher in der Fläche bleiben frei (gerade/ungerade-Regel)
            outline = " ".join(part + " Z" for part in path(rims, left, bottom).split(" M")).replace("Z Z", "Z")
            parts.append(f'<path d="{outline if outline.startswith("M") else "M" + outline}" fill="url(#schraffur)" '
                         'fill-rule="evenodd" stroke="none"/>')
        parts.append(f'<path d="{path(view.visible, left, bottom)}" fill="none" stroke="black" '
                     'stroke-width="0.35" stroke-linecap="round" stroke-linejoin="round"/>')
        if dimensions:
            # Mittenkreuz und Durchmesser an Bohrungen und Wellen, auf die man längs der Achse schaut;
            # mehrere Durchmesser an derselben Mitte (z. B. Bohrung mit Senkung) stehen in einer Zeile
            centres: dict[tuple, list[float]] = {}
            for x, y, diameter in view.rounds:
                centres.setdefault((round(x, 3), round(y, 3)), []).append(diameter)
            for (x, y), diameters in centres.items():
                cx, cy, arm = left + x * factor, bottom - y * factor, max(diameters) * factor / 2 + 1.5
                parts.append(f'<path d="M{cx - arm:.2f},{cy:.2f} H{cx + arm:.2f} M{cx:.2f},{cy - arm:.2f} V{cy + arm:.2f}" '
                             'stroke="#1a55aa" stroke-width="0.13" stroke-dasharray="2.4 0.7 0.5 0.7" fill="none"/>')
                label = " / ".join("⌀ " + _number(diameter) for diameter in sorted(diameters))
                parts.append(f'<text x="{cx + arm * 0.72 + 0.8:.2f}" y="{cy - arm * 0.72 - 0.6:.2f}" font-size="2.8" '
                             f'fill="#1a55aa">{label}</text>')
        if dimensions:
            # Rundungen: je Radius ein Maß mit Hinweislinie vom Bogen nach außen; gleiche Radien werden gezählt
            by_radius: dict[float, list] = {}
            for arc in view.arcs:
                by_radius.setdefault(round(arc[2], 3), []).append(arc)
            for radius, same in sorted(by_radius.items()):
                cx, cy, _r, px, py = same[0]
                away = math.hypot(px - cx, py - cy) or 1.0
                ux, uy = (px - cx) / away, (py - cy) / away
                start = left + px * factor, bottom - py * factor
                end = start[0] + ux * 7.0, start[1] - uy * 7.0
                label = (f"{len(same)}× " if len(same) > 1 else "") + "R " + _number(radius)
                anchor = "start" if ux >= 0 else "end"
                parts.append(f'<path d="M{start[0]:.2f},{start[1]:.2f} L{end[0]:.2f},{end[1]:.2f}" stroke="#1a55aa" '
                             'stroke-width="0.18" fill="none"/>')
                parts.append(f'<text x="{end[0] + (0.8 if ux >= 0 else -0.8):.2f}" y="{end[1] + (2.6 if uy < 0 else -0.6):.2f}" '
                             f'font-size="2.8" text-anchor="{anchor}" fill="#1a55aa">{label}</text>')
        if layout.cut_line is not None and view.name == "Oben":
            # Schnittverlauf in der Draufsicht: Strichpunktlinie mit den Buchstaben A an beiden Enden
            (x1, y1), (x2, y2) = [(left + x * factor, bottom - y * factor) for x, y in layout.cut_line]
            length = math.hypot(x2 - x1, y2 - y1) or 1.0
            ux, uy = (x2 - x1) / length, (y2 - y1) / length
            # über das Modell hinaus verlängern (rechts bis hinter das Höhenmaß)
            x1, y1, x2, y2 = x1 - ux * 6.0, y1 - uy * 6.0, x2 + ux * 12.0, y2 + uy * 12.0
            parts.append(f'<path d="M{x1:.2f},{y1:.2f} L{x2:.2f},{y2:.2f}" stroke="black" stroke-width="0.3" '
                         'stroke-dasharray="6 1.2 0.8 1.2" fill="none"/>')
            for end_x, end_y in ((x1, y1), (x2, y2)):
                parts.append(f'<text x="{end_x:.2f}" y="{end_y - 1.6:.2f}" font-size="3.6" font-weight="bold" '
                             'text-anchor="middle">A</text>')
        if layout.cross is not None and view.name == "Oben":
            # Querschnitt B–B: senkrechte Strichpunktlinie mit den Buchstaben B an beiden Enden
            cut_x = left + layout.cross * factor
            ends = top - 6.0, bottom + 12.0         # unten hinter dem Breitenmaß
            parts.append(f'<path d="M{cut_x:.2f},{ends[0]:.2f} V{ends[1]:.2f}" stroke="black" stroke-width="0.3" '
                         'stroke-dasharray="6 1.2 0.8 1.2" fill="none"/>')
            for end in ends:
                parts.append(f'<text x="{cut_x + 2.6:.2f}" y="{end + 1.2:.2f}" font-size="3.6" font-weight="bold" '
                             'text-anchor="middle">B</text>')
        for entry in layout.details:
            if view.name != entry["ansicht"]:
                continue
            # Kreis um die Stelle, die eine Detailansicht vergrößert zeigt, mit ihrem Buchstaben
            mx, my = entry["mitte"]
            ring = entry["radius"] * factor
            parts.append(f'<circle cx="{left + mx * factor:.2f}" cy="{bottom - my * factor:.2f}" r="{ring:.2f}" '
                         'fill="none" stroke="black" stroke-width="0.25" stroke-dasharray="3 1"/>')
            parts.append(f'<text x="{left + mx * factor + ring * 0.75 + 1.2:.2f}" y="{bottom - my * factor - ring * 0.75:.2f}" '
                         f'font-size="3.6" font-weight="bold">{entry["buchstabe"]}</text>')
        caption = view.name
        shown_detail = next((entry for entry in layout.details if entry["name"] == view.name), None)
        if shown_detail is not None:
            # Maßstab der Detailansicht: Blattmaßstab mal Vergrößerung
            caption += f"  ({_number(drawing * shown_detail['faktor'])}:{real})"
        parts.append(f'<text x="{left:.2f}" y="{top - 3.5:.2f}" font-size="3.2" fill="#333">{escape(caption)}</text>')
        if dimensions and view.name != "Isometrisch" and not is_detail(view.name):
            # Gesamtmaße: Breite unter der Ansicht, Höhe rechts daneben
            right_edge, y = left + view.width * factor, bottom + 7.0
            parts.append(f'<path d="M{left:.2f},{bottom + 1.5:.2f} V{y + 1.5:.2f} M{right_edge:.2f},{bottom + 1.5:.2f} '
                         f'V{y + 1.5:.2f} M{left:.2f},{y:.2f} H{right_edge:.2f}" stroke="#1a55aa" stroke-width="0.18" fill="none"/>')
            parts.append(f'<text x="{(left + right_edge) / 2:.2f}" y="{y - 1.0:.2f}" font-size="3" text-anchor="middle" '
                         f'fill="#1a55aa">{_number(view.width)}</text>')
            x = right_edge + 7.0
            parts.append(f'<path d="M{right_edge + 1.5:.2f},{top:.2f} H{x + 1.5:.2f} M{right_edge + 1.5:.2f},{bottom:.2f} '
                         f'H{x + 1.5:.2f} M{x:.2f},{top:.2f} V{bottom:.2f}" stroke="#1a55aa" stroke-width="0.18" fill="none"/>')
            middle = (top + bottom) / 2
            parts.append(f'<text x="{x - 1.0:.2f}" y="{middle:.2f}" font-size="3" text-anchor="middle" fill="#1a55aa" '
                         f'transform="rotate(-90 {x - 1.0:.2f} {middle:.2f})">{_number(view.height)}</text>')

    # Eigene Maße: zwischen zwei angeklickten Punkten einer Ansicht; verlorene Maße erscheinen rot gestrichelt
    for index, entry in enumerate(extra or []):
        shape = _own_dimension(layout, entry)
        if shape is None:
            continue
        outline, (x, y), angle, value = shape
        gone = index in (lost or ())
        colour, dash = (_LOST_COLOR, ' stroke-dasharray="1.4 0.8"') if gone else (_OWN_COLOR, "")
        parts.append(f'<path d="{outline}" stroke="{colour}" stroke-width="0.2" fill="none"{dash}/>')
        turn = f' transform="rotate({angle:.2f} {x:.2f} {y:.2f})"' if abs(angle) > 1e-6 else ""
        # weißes Feld hinter der Maßzahl, damit sie auch quer über Kanten lesbar bleibt
        unit = "°" if len(entry) > 3 and entry[3] == ANGLE else ""
        prefix = "R " if len(entry) > 3 and entry[3] == RADIUS else ""
        label_width = 1.9 * (len(_number(value)) + len(unit) + len(prefix) + (2 if gone else 0)) + 1.0
        parts.append(f'<rect x="{x - label_width / 2:.2f}" y="{y - 2.7:.2f}" width="{label_width:.2f}" height="3.4" '
                     f'fill="white" stroke="none"{turn}/>')
        parts.append(f'<text x="{x:.2f}" y="{y:.2f}" font-size="3" text-anchor="middle" fill="{colour}"{turn}>'
                     f'{prefix}{_number(value)}{unit}{" ?" if gone else ""}</text>')
    for x, y in marks or []:
        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="1.1" fill="none" stroke="#d0502a" stroke-width="0.35"/>')

    # Schriftfeld unten rechts
    field_width, x0 = 110.0, sheet[0] - _MARGIN / 2 - 110.0
    y0 = sheet[1] - _MARGIN / 2 - _TITLE_HEIGHT
    today = datetime.date.today().strftime("%d.%m.%Y")
    parts += [
        f'<rect x="{x0}" y="{y0}" width="{field_width}" height="{_TITLE_HEIGHT}" fill="white" stroke="black" stroke-width="0.5"/>',
        f'<path d="M{x0},{y0 + 11} H{x0 + field_width} M{x0 + 55},{y0 + 11} V{y0 + _TITLE_HEIGHT}" stroke="black" stroke-width="0.25"/>',
        f'<text x="{x0 + 3}" y="{y0 + 7.5}" font-size="5" font-weight="bold">{escape(title)}</text>',
        f'<text x="{x0 + 3}" y="{y0 + 18}" font-size="3.4">Maßstab {drawing}:{real} · {escape(method.split(" (")[0])}</text>',
        f'<text x="{x0 + 58}" y="{y0 + 18}" font-size="3.4">{today} · Maße in mm · WolfCAD</text>',
        "</svg>",
    ]
    return "\n".join(parts)


def sheet_svg(shapes: list, title: str, sheet_name: str = "A4 quer", view_names: list[str] | None = None,
              show_hidden: bool = True, dimensions: bool = True, method: str | None = None,
              section_shift: float = 0.0, extra: list | None = None, offsets: dict | None = None,
              cross_shift: float = 0.0) -> str:
    """Das fertige Zeichenblatt als SVG-Text (Einheit mm): Ansichten anordnen und zeichnen."""
    return render(arrange(shapes, sheet_name, view_names, method, section_shift, offsets, cross_shift), title,
                  show_hidden, dimensions, extra)
