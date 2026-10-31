# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Die einzelnen Konstruktionsschritte (Features) der Timeline.

Jedes Feature ist ein kleiner, parametrischer Bauschritt. Beim Neuberechnen
ruft die Timeline der Reihe nach `apply` auf; das Feature liest den bisherigen
Modellstand aus dem `BuildState` und schreibt sein Ergebnis dorthin zurück.
Alle Längen sind Millimeter.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields

from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse, BRepAlgoAPI_Section
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Transform,
)
from OCP.BRepLib import BRepLib
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.collections import (
    Array1_gp_Pnt2d,
    IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher,
    IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher,
)
from OCP.GeomAbs import GeomAbs_C0
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Pnt2d, gp_Trsf, gp_Vec
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_IN, TopAbs_SOLID, TopAbs_VERTEX, TopAbs_WIRE
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shape

# Skizzenebenen: Name -> (Normale, x-Richtung in der Ebene)
# Hauptebenen: Name -> (Normale, x-Richtung der Skizze). Die y-Richtung ergibt sich als Normale × x.
# Auf der XZ-Ebene (Vorderansicht) zeigt y nach oben (+Z), wie man es beim Zeichnen von vorn erwartet.
MAIN_PLANES = ("XY", "XZ", "YZ")
LEGACY_XZ = "XZ (alt)"      # Lage der XZ-Ebene in Entwürfen vor Dateiversion 2: y zeigte dort nach -Z
PLANES = {
    "XY": ((0, 0, 1), (1, 0, 0)),
    "XZ": ((0, -1, 0), (1, 0, 0)),
    "YZ": ((1, 0, 0), (0, 1, 0)),
    LEGACY_XZ: ((0, 1, 0), (1, 0, 0)),
}
OPERATIONS = ("Neuer Körper", "Vereinigen", "Ausschneiden", "Schnittmenge")
# Richtung einer Extrusion wie in Fusion: nur zur Normale hin, gleich weit nach beiden Seiten (Abstand = ganze
# Länge) oder auf jeder Seite mit eigenem Abstand
CHAMFER_KINDS = ("Gleicher Abstand", "Zwei Abstände", "Abstand und Winkel")
CHAMFER_FIELDS = {"distance2": (CHAMFER_KINDS[1],), "angle": (CHAMFER_KINDS[2],)}   # Felder je Art der Fase
# Rundung wie in Fusion (Runde 91): drei Arten, beim gewöhnlichen Abrunden drei Radiusarten
FILLET_KINDS = ("Verrundung", "Regelverrundung", "Volle Rundung")
RADIUS_KINDS = ("Konstant", "Sehnenlänge", "Variabel")
RULE_SCOPES = ("Alle Kanten", "Kanten der gewählten Flächen")
# Felder je Art der Rundung bzw. je Radiusart (die übrigen blendet das Eigenschaftenfeld aus)
FILLET_FIELDS = {"radius": FILLET_KINDS[:2], "radius_type": FILLET_KINDS[:1], "edge_points": FILLET_KINDS[:1],
                 "sets": FILLET_KINDS[:1], "wall_aware": FILLET_KINDS[:1], "rule_scope": FILLET_KINDS[1:2],
                 "rule_rounds": FILLET_KINDS[1:2], "rule_fillets": FILLET_KINDS[1:2], "face_points": FILLET_KINDS[1:]}
RADIUS_FIELDS = {"radius2": RADIUS_KINDS[2:], "mid_radius": RADIUS_KINDS[2:], "mid_position": RADIUS_KINDS[2:]}
EXTENTS = ("Eine Seite", "Symmetrisch", "Zwei Seiten", "Bis Fläche", "Durch alles")
# Welche Felder einer Extrusion bei welcher Richtung gelten (die übrigen blendet das Eigenschaftenfeld aus)
EXTENT_FIELDS = {"distance": EXTENTS[:3], "distance2": (EXTENTS[2],), "target_points": (EXTENTS[3],),
                 "taper2": (EXTENTS[2],)}
# Art der Extrusion wie Fusion (Runde 93): voller Körper oder dünne Wand mit Dicke, innen/außen/mittig am Umriss
EXTRUDE_TYPES = ("Körper", "Dünn")
WALL_SIDES = ("Innen", "Außen", "Mitte")
EXTRUDE_TYPE_FIELDS = {"wall": EXTRUDE_TYPES[1:], "wall_side": EXTRUDE_TYPES[1:]}
# Was ein Import anzeigt
# „Nur Quelle der Endstände“: der Blob wird eingelesen, zeigt aber selbst nichts – bei übernommener Fusion-Timeline
# setzen die einzelnen Schritte ihre gespeicherten Endstände ein (sonst gäbe es jeden Körper doppelt)
IMPORT_CONTENTS = ("Aktuelles Modell", "Alle gespeicherten Körper", "Nur Quelle der Endstände")
# Auswahl "alle veränderbaren Körper" in Werkzeugen, die auf Körper wirken
ALL_BODIES = "Alle"
# Welche Bereiche einer Skizze ein Profil bilden: Innenbereiche (z. B. ein Kreis im
# Rechteck) bleiben als Loch frei, oder alle geschlossenen Bereiche werden gefüllt
PROFILE_MODES = ("Mit Löchern", "Alle Bereiche", "Gewählte Bereiche")
# Operation, die sich nach der Lage des neuen Volumens richtet (siehe _automatic_operation)
AUTO_OPERATION = "Automatisch"
# Hinweis einer neuen Extrusion, die noch keine Länge hat (wie in Fusion startet sie bei 0): kein echter Fehler –
# die Oberfläche zeigt ihn nicht rot an
ZERO_DISTANCE_MESSAGE = "Noch keine Länge – am Pfeil ziehen oder eine Länge eintippen"
# Abrunden/Fase wie in Fusion: erst wirksam, wenn Kanten oder Flächen gewählt sind
PICK_EDGES_MESSAGE = "Kanten oder Flächen anklicken, die bearbeitet werden sollen"
PICK_PROFILE_MESSAGE = "Profil einer Skizze oder ebene Körperfläche anklicken"
PICK_FACES_MESSAGE = "Flächen anklicken, die bearbeitet werden sollen"
# die Timeline zeigt sie nicht rot
HINT_MESSAGES = (ZERO_DISTANCE_MESSAGE, PICK_EDGES_MESSAGE, PICK_PROFILE_MESSAGE, PICK_FACES_MESSAGE)

# Stand vor dem gerade berechneten Schritt aus der vorigen Neuberechnung (setzt die Timeline): darüber finden
# Schritte ihre Flächen und Kanten wieder, wenn ein früherer Schritt sie verschoben hat (siehe find_by_points)
_PREVIOUS: "BuildState | None" = None


def set_previous(state: "BuildState | None") -> None:
    global _PREVIOUS
    _PREVIOUS = state


# Geometriestufe des gerade berechneten Entwurfs (siehe timeline.GEOMETRY_LEVEL); die Timeline setzt sie vor dem
# Rechnen. Außerhalb einer Timeline (Tests, Einzelaufrufe) gilt das aktuelle Verfahren.
_GEOMETRY_LEVEL = 3


# Kanten, die beim Zusammenfassen von Flächen (unified) stehen bleiben müssen: die Timeline setzt hier vor jedem
# Schritt die Kanten der Körper davor. So verschmilzt ein Schritt nur Nähte, die er selbst erzeugt hat (z. B. die
# Trennkante zwischen zwei bündig vereinigten Quadern); gewollte Teilungen aus früheren Schritten („Fläche teilen“)
# bleiben erhalten wie in Fusion. None = keine Einschränkung (außerhalb der Timeline, ältere Entwürfe).
_KEEP_EDGES = None


def set_keep_edges(bodies) -> None:
    """Merkt die Kanten der übergebenen Körper als unantastbar für `unified` (None hebt das auf)."""
    global _KEEP_EDGES
    if bodies is None:
        _KEEP_EDGES = None
        return
    from OCP.collections import Map_TopoDS_Shape_TopTools_ShapeMapHasher
    keep = Map_TopoDS_Shape_TopTools_ShapeMapHasher()
    for shape in bodies:
        explorer = TopExp_Explorer(shape, TopAbs_EDGE)
        while explorer.More():
            keep.Add(explorer.Current())
            explorer.Next()
    _KEEP_EDGES = keep


def set_geometry_level(level: int) -> None:
    global _GEOMETRY_LEVEL
    _GEOMETRY_LEVEL = level


def geometry_level() -> int:
    return _GEOMETRY_LEVEL
# Größter Abstand (mm), bei dem ein gemerkter Punkt noch als dieselbe Kante/Fläche gilt
SELECTION_TOLERANCE = 0.01


# Bereits eingelesene Importe: (Quelldatei, Blob) -> Importergebnis
_IMPORT_CACHE: dict[tuple[str, str], object] = {}
# Bereits eingelesene Skizzen: Quelldatei -> Kurven je Skizze
_SKETCH_CACHE: dict[str, list[TopoDS_Shape]] = {}
# Bereits eingelesene Entwurfsdaten: Quelldatei -> DesignData
_DESIGN_CACHE: dict[str, object] = {}
# Gespeicherte Endstände: (Quelldatei, Zahl eingelesener Blobs) -> Feature-ID -> Körper
_STORED_CACHE: dict[tuple, dict] = {}
# Bereits gebaute Extrusionsvolumen: (Quelldatei, Feature-ID, Abstand) -> Volumen oder None
_SOLID_CACHE: dict[tuple[str, int, float], TopoDS_Shape | None] = {}


class FeatureError(Exception):
    """Ein Feature konnte mit seinen aktuellen Parametern nicht berechnet werden."""


# Auswahl „keine Konstruktionsebene“: es gilt die Hauptebene bzw. die gewählte Fläche
NO_BASE = "(keine)"


@dataclass
class BuildState:
    """Modellstand während einer Neuberechnung."""

    bodies: dict[str, TopoDS_Shape] = field(default_factory=dict)      # Name -> Volumenkörper
    body_count: int = 0                                                # für fortlaufende Körpernamen
    body_origin: dict[str, int] = field(default_factory=dict)          # Name -> ID des Fusion-Schritts
    # Körper im gespeicherten Endstand: spätere Schritte dürfen sie nicht mehr verändern
    frozen: set[str] = field(default_factory=set)
    # Hilfskörper: im Original am Ende nicht mehr vorhanden (z. B. verbrauchte Werkzeugkörper)
    helpers: set[str] = field(default_factory=set)
    profiles: dict[str, TopoDS_Shape] = field(default_factory=dict)    # Skizzenname -> Fläche
    normals: dict[str, tuple] = field(default_factory=dict)            # Skizzenname -> Ebenennormale
    frames: dict[str, gp_Ax2] = field(default_factory=dict)            # Skizzenname -> Lage der Ebene
    # Skizzenname -> nur die Bereiche, die nicht in einem Loch eines anderen liegen
    solid_profiles: dict[str, TopoDS_Shape] = field(default_factory=dict)
    references: list[TopoDS_Shape] = field(default_factory=list)       # importierte Geometrie
    sketch_curves: list[TopoDS_Shape] = field(default_factory=list)    # importierte Skizzenkurven
    # Skizzenname -> alle Kurven der Skizze (auch offene Linienzüge, z. B. als Pfad für Sweep)
    curves: dict[str, TopoDS_Shape] = field(default_factory=dict)
    construction_curves: list[TopoDS_Shape] = field(default_factory=list)  # Konstruktionslinien der Skizzen
    # je Skizze die Anzeigeteile, die sie zu sketch_curves/construction_curves beigetragen hat (zum Entfernen)
    sketch_parts: dict = field(default_factory=dict)
    planes: dict[str, gp_Ax2] = field(default_factory=dict)            # Konstruktionsebenen: Name -> Lage
    axes: dict[str, tuple] = field(default_factory=dict)               # Konstruktionsachsen: Name -> (Punkt, Richtung)
    sketch_points: dict[str, list] = field(default_factory=dict)       # Skizzenpunkte: Skizze -> [(x, y, z), …]
    # Körperstand vor und nach jedem Feature, das Körper verändert hat (für „Muster von Features“)
    history: dict[str, tuple] = field(default_factory=dict)
    # Komponenten: Körpername -> Name der Komponente, zu der er gehört (Bauteil einer Baugruppe)
    components: dict[str, str] = field(default_factory=dict)
    colors: dict[str, str] = field(default_factory=dict)       # Körpername -> Farbname (Erscheinungsbild)
    # Körpername -> [(Merkpunkt der Fläche, Farbname)] für einzeln gefärbte Flächen
    face_colors: dict[str, list] = field(default_factory=dict)
    # Unterbaugruppen: Komponente -> übergeordnete Komponente
    component_parents: dict[str, str] = field(default_factory=dict)
    # Blechteile: Körpername -> Blechregel und Aufbau für die Abwicklung (siehe sheetmetal.py); die Einträge
    # werden nie verändert, sondern ersetzt (die flache Kopie in `copy` genügt deshalb)
    sheets: dict[str, dict] = field(default_factory=dict)
    # Flächenkörper (Fusion: Surface-Körper): offene Flächen ohne Volumen, Name -> Form (siehe surfaces.py)
    surfaces: dict[str, TopoDS_Shape] = field(default_factory=dict)
    surface_count: int = 0
    # Netzkörper (Fusion: Mesh-Körper): Dreiecksfelder n × 3 × 3, Name -> Feld (siehe meshes.py); Felder werden
    # nie verändert, nur ersetzt
    meshes: dict = field(default_factory=dict)
    mesh_count: int = 0
    # nur zur Anzeige: Zellen, die eine Begrenzungsfüllung nicht füllt (Feature-Name -> Liste von Körpern)
    ghosts: dict = field(default_factory=dict)
    # Konstruktionspunkte: Name -> (x, y, z)
    points: dict = field(default_factory=dict)
    # Gelenkursprünge: Name -> {"body": Körper, "point": (x, y, z), "normal": (nx, ny, nz)}; wandern mit dem Körper
    joint_origins: dict = field(default_factory=dict)
    # Starre Gruppen: Name -> [Körper]; bewegt sich einer (Gelenk, Verschieben), bewegen sich alle
    rigid_groups: dict = field(default_factory=dict)
    # Leinwände (Bilder als Vorlage, nur zur Anzeige): Name -> {"corners": 4 Punkte, "image": PNG-Daten (Base64),
    # "opacity": 0 … 1}
    canvases: dict = field(default_factory=dict)

    def component_bodies(self, component: str) -> list[str]:
        """Die noch vorhandenen Körper einer Komponente, einschließlich ihrer Unterkomponenten."""
        family = {component}
        grown = True
        while grown:        # alle Nachkommen einsammeln (Unterbaugruppen beliebiger Tiefe)
            children = {child for child, parent in self.component_parents.items() if parent in family}
            grown = not children <= family
            family |= children
        return [body for body, owner in self.components.items() if owner in family and body in self.bodies]

    def move_body(self, name: str, transform) -> None:
        """Verschiebt oder dreht einen Körper; seine einzeln gefärbten Flächen wandern mit."""
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        # mit Zerlegung: ein verschobener Körper muss für die Anzeige nicht neu zerlegt werden
        self.bodies[name] = BRepBuilderAPI_Transform(self.bodies[name], transform, True, True).Shape()
        if name in self.face_colors:
            moved = []
            for point, colour in self.face_colors[name]:
                place = gp_Pnt(*point).Transformed(transform)
                moved.append(((place.X(), place.Y(), place.Z()), colour))
            self.face_colors[name] = moved
        # Gelenkursprünge am Körper wandern mit (ihre Lage ist fest mit dem Körper verbunden)
        for origin_name, origin in list(self.joint_origins.items()):
            if origin["body"] == name:
                place = gp_Pnt(*origin["point"]).Transformed(transform)
                direction = gp_Dir(*origin["normal"]).Transformed(transform)
                self.joint_origins[origin_name] = {**origin, "point": (place.X(), place.Y(), place.Z()),
                                                   "normal": (direction.X(), direction.Y(), direction.Z())}

    def moving_with(self, body: str) -> list[str]:
        """Alle Körper, die sich mit diesem gemeinsam bewegen: seine Komponente (sonst nur er selbst) und alles,
        was über starre Gruppen daran hängt – auch über mehrere Gruppen hinweg."""
        found: list[str] = []
        waiting = [body]
        while waiting:
            current = waiting.pop()
            if current in found:
                continue
            found.append(current)
            owner = self.components.get(current)
            waiting += self.component_bodies(owner) if owner else []
            for members in self.rigid_groups.values():
                if current in members:
                    waiting += [member for member in members if member in self.bodies]
        return found

    def copy(self) -> "BuildState":
        """Unabhängige Kopie des Modellstands.

        Kopiert werden nur die Verzeichnisse, nicht die Formen selbst: Features ersetzen
        Einträge, statt vorhandene Formen zu verändern.
        """
        values = {}
        for item in fields(self):
            value = getattr(self, item.name)
            values[item.name] = type(value)(value) if isinstance(value, (dict, list, set)) else value
        return BuildState(**values)

    @property
    def body(self) -> TopoDS_Shape | None:
        """Alle Körper als eine Form: None ohne Körper, sonst der Körper bzw. ein Verbund."""
        shapes = list(self.bodies.values())
        if len(shapes) <= 1:
            return shapes[0] if shapes else None
        builder, compound = BRep_Builder(), TopoDS_Compound()
        builder.MakeCompound(compound)
        for shape in shapes:
            builder.Add(compound, shape)
        return compound

    def add_mesh(self, triangles) -> str:
        """Nimmt einen neuen Netzkörper auf und gibt seinen Namen zurück („Netz1“, …)."""
        self.mesh_count += 1
        name = f"Netz{self.mesh_count}"
        self.meshes[name] = triangles
        return name

    def add_surface(self, shape: TopoDS_Shape) -> str:
        """Nimmt einen neuen Flächenkörper auf und gibt seinen Namen zurück („Flächenkörper1“, …)."""
        self.surface_count += 1
        name = f"Flächenkörper{self.surface_count}"
        self.surfaces[name] = shape
        return name

    def add_body(self, shape: TopoDS_Shape, origin: int | None = None) -> str:
        """Nimmt einen neuen Körper auf und gibt seinen Namen zurück.

        `origin` ist die ID des Fusion-Schritts, der den Körper erzeugt hat; darüber
        finden spätere übernommene Schritte (z. B. Verschieben) ihren Zielkörper.
        """
        self.body_count += 1
        name = f"Körper{self.body_count}"
        self.bodies[name] = shape
        if origin is not None:
            self.body_origin[name] = origin
        return name


@dataclass
class Feature:
    """Basisklasse; `name` ist der in Timeline und Browser angezeigte Name."""

    name: str = ""
    suppressed: bool = False
    # Feldname -> Ausdruck (z. B. "breite / 2"): der Wert des Feldes wird bei jeder
    # Neuberechnung aus den Benutzerparametern der Timeline bestimmt
    expressions: dict = field(default_factory=dict)

    KIND = "Feature"

    def apply(self, state: BuildState) -> None:
        raise NotImplementedError

    def resolve_parameters(self, evaluate) -> None:
        """Gelegenheit, eigene Ausdrücke mit den Benutzerparametern auszurechnen (vor `apply`).

        `evaluate(text)` liefert den Wert eines Ausdrucks. Die Ausdrücke der einfachen
        Zahlenfelder (`expressions`) erledigt die Timeline selbst.
        """

    def to_dict(self) -> dict:
        return {"kind": self.KIND, **asdict(self)}

    def parameters(self) -> list:
        """Die vom Benutzer änderbaren Felder (alles außer Name und Unterdrückung)."""
        return [f for f in fields(self) if f.name not in ("name", "suppressed", "expressions")]


def _plane_axes(plane: str, offset: float) -> gp_Ax2:
    if plane not in PLANES:
        raise FeatureError(f"Unbekannte Ebene '{plane}'")
    normal, x_dir = PLANES[plane]
    origin = gp_Pnt(*(n * offset for n in normal))
    return gp_Ax2(origin, gp_Dir(*normal), gp_Dir(*x_dir))


@dataclass
class RectangleSketch(Feature):
    """Skizze mit einem mittig liegenden Rechteck."""

    plane: str = "XY"
    offset: float = 0.0
    width: float = 40.0
    height: float = 20.0
    center_x: float = 0.0
    center_y: float = 0.0

    KIND = "Rechteck-Skizze"

    def apply(self, state: BuildState) -> None:
        if self.width <= 0 or self.height <= 0:
            raise FeatureError("Breite und Höhe müssen größer als 0 sein")
        axes = _plane_axes(self.plane, self.offset)
        origin = gp_Vec(axes.Location().XYZ())
        x, y = gp_Vec(axes.XDirection().XYZ()), gp_Vec(axes.YDirection().XYZ())

        polygon = BRepBuilderAPI_MakePolygon()
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            corner = (origin
                      + x * (self.center_x + sx * self.width / 2)
                      + y * (self.center_y + sy * self.height / 2))
            polygon.Add(gp_Pnt(corner.XYZ()))
        polygon.Close()
        state.profiles[self.name] = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
        state.normals[self.name] = PLANES[self.plane][0]
        state.frames[self.name] = axes


@dataclass
class CircleSketch(Feature):
    """Skizze mit einem Kreis."""

    plane: str = "XY"
    offset: float = 0.0
    radius: float = 10.0
    center_x: float = 0.0
    center_y: float = 0.0

    KIND = "Kreis-Skizze"

    def apply(self, state: BuildState) -> None:
        if self.radius <= 0:
            raise FeatureError("Der Radius muss größer als 0 sein")
        axes = _plane_axes(self.plane, self.offset)
        center = (gp_Vec(axes.Location().XYZ())
                  + gp_Vec(axes.XDirection().XYZ()) * self.center_x
                  + gp_Vec(axes.YDirection().XYZ()) * self.center_y)
        circle = gp_Circ(gp_Ax2(gp_Pnt(center.XYZ()), axes.Direction(), axes.XDirection()),
                         self.radius)
        wire = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(circle).Edge()).Wire()
        state.profiles[self.name] = BRepBuilderAPI_MakeFace(wire).Face()
        state.normals[self.name] = PLANES[self.plane][0]
        state.frames[self.name] = axes


@dataclass
class Extrude(Feature):
    """Zieht das Profil einer Skizze senkrecht zur Skizzenebene in die Höhe."""

    sketch: str = ""
    distance: float = 10.0
    operation: str = OPERATIONS[0]
    regions: str = PROFILE_MODES[0]
    region_points: list = field(default_factory=list)   # je gewähltem Bereich ein Punkt in seinem Inneren
    # Profile weiterer Skizzen, die mit demselben Abstand aufgezogen werden: je Eintrag [Skizze, [Punkte …]]
    # (ohne Punkte gilt das ganze Profil jener Skizze)
    extra_profiles: list = field(default_factory=list)
    extent: str = EXTENTS[0]
    distance2: float = 10.0     # Abstand der zweiten Seite (nur bei „Zwei Seiten“, in Gegenrichtung)
    taper: float = 0.0          # Verjüngung in Grad: positiv wird der Körper vom Profil weg schmaler
    start: float = 0.0          # Startversatz: die Extrusion beginnt so weit längs der Normale vom Profil entfernt
    target_points: list = field(default_factory=list)   # „Bis Fläche“: Merkpunkt der ebenen Zielfläche
    # Wie in Fusion: statt eines Skizzenprofils ebene Körperflächen aufziehen (Merkpunkte der Flächen)
    face_points: list = field(default_factory=list)
    # --- seit Runde 93 (Fusion-Funktionen) ---
    extrude_type: str = EXTRUDE_TYPES[0]    # „Dünn“: nur eine Wand am Umriss des Profils
    wall: float = 1.0                       # Wandstärke der dünnen Extrusion
    wall_side: str = WALL_SIDES[0]          # Lage der Wand zum Umriss: innen, außen oder mittig
    taper2: float = 0.0                     # Verjüngung der zweiten Seite (nur „Zwei Seiten“)
    # Beteiligte Körper wie Fusion (Runde 96): Namen, durch Komma getrennt; leer = alle berührten
    participants: str = ""

    KIND = "Extrusion"

    # Reihenfolge im Eigenschaftenfeld wie in Fusion: Art, Profil, Start, Richtung, Maße, Verjüngung, Operation
    _ORDER = ("extrude_type", "sketch", "regions", "region_points", "extra_profiles", "face_points", "start",
              "extent", "distance", "distance2", "target_points", "taper", "taper2", "wall", "wall_side",
              "operation", "participants")

    def parameters(self) -> list:
        rank = {name: number for number, name in enumerate(self._ORDER)}
        return sorted(super().parameters(), key=lambda item: rank.get(item.name, len(rank)))

    def profile_list(self, state: BuildState) -> list[tuple]:
        """Die aufzuziehenden Profile mit ihrer Richtung: [(Fläche, Normale), …] – aus Körperflächen und Skizzen
        (wie in Fusion auch gemischt: Körperflächen mehrerer Körper und Profile mehrerer Skizzen zugleich)."""
        found = []
        every = [f for body in state.bodies.values() for f in faces_of(body)]
        for marker in self.face_points:
            face = TopoDS.Face(find_by_points(every, face_point, [marker], "Körperfläche")[0])
            found.append((face, _plane_normal(face)))
        if self.face_points and not self.sketch:
            return found                    # nur Körperflächen
        if not self.sketch and not self.extra_profiles:
            raise FeatureError(PICK_PROFILE_MESSAGE)      # wie Fusion: erst wählen, dann aufziehen
        if self.sketch not in state.profiles:
            if self.sketch in state.frames:
                raise FeatureError(f"Skizze '{self.sketch}' hat kein geschlossenes Profil")
            raise FeatureError(f"Skizze '{self.sketch}' ist nicht (mehr) vorhanden")
        profiles = [(profile_of(state, self.sketch, self.regions, self.region_points), state.normals[self.sketch])]
        for name, points in self.extra_profiles:
            # jedes weitere Profil wächst längs der Normale seiner eigenen Skizze
            if name not in state.profiles:
                raise FeatureError(f"Skizze '{name}' ist nicht (mehr) vorhanden oder hat kein geschlossenes Profil")
            profiles.append((profile_of(state, name, PROFILE_MODES[2] if points else PROFILE_MODES[0], points),
                             state.normals[name]))
        return found + profiles

    def _open_thin(self, state: BuildState) -> bool:
        """Dünne Extrusion einer offenen Linie (Fusion: dünn extrudieren, auch ohne geschlossenes Profil, Runde 96):
        die Linien werden zur Wandfläche gezogen und um die Wandstärke aufgedickt. True, wenn das zutraf."""
        if self.extrude_type != EXTRUDE_TYPES[1] or not self.sketch or self.sketch in state.profiles \
                or self.sketch not in state.curves or self.face_points or self.extra_profiles:
            return False
        if self.extent not in EXTENTS[:3]:
            raise FeatureError("Offene Linien lassen sich nur um einen Abstand dünn extrudieren")
        if abs(self.distance) < 1e-9:
            raise FeatureError(ZERO_DISTANCE_MESSAGE)
        from .surfaces import thicken_sheet
        normal = gp_Vec(*state.normals[self.sketch])
        pieces = [self.distance / 2, -self.distance / 2] if self.extent == EXTENTS[1] else \
            [self.distance, -abs(self.distance2) if self.distance > 0 else abs(self.distance2)] \
            if self.extent == EXTENTS[2] else [self.distance]
        low, high = min(0.0, *pieces) + self.start, max(0.0, *pieces) + self.start
        shift = gp_Trsf()
        shift.SetTranslation(normal * low)
        curves = BRepBuilderAPI_Transform(state.curves[self.sketch], shift, True).Shape()
        sheet = BRepPrimAPI_MakePrism(curves, normal * (high - low)).Shape()
        if self.wall_side == WALL_SIDES[2]:
            # mittig: nach beiden Seiten je die halbe Wand aufdicken und vereinigen (eine offene Fläche lässt sich
            # nicht vorher versetzen)
            solid = unified(_boolean(BRepAlgoAPI_Fuse, thicken_sheet(sheet, self.wall / 2),
                                     thicken_sheet(sheet, -self.wall / 2)))
        else:
            solid = thicken_sheet(sheet, self.wall if self.wall_side == WALL_SIDES[1] else -self.wall)
        chosen = {name.strip() for name in self.participants.split(",") if name.strip()}
        _combine(state, solid, self.operation, names=chosen or None)
        return True

    def apply(self, state: BuildState) -> None:
        if self._open_thin(state):
            return
        profiles = self.profile_list(state)
        if self.extent not in EXTENTS:
            raise FeatureError(f"Unbekannte Richtung '{self.extent}'")
        if abs(self.distance) < 1e-9 and self.extent not in (EXTENTS[3], EXTENTS[4]):
            raise FeatureError(ZERO_DISTANCE_MESSAGE)
        if self.extent == EXTENTS[2] and abs(self.distance2) < 1e-9:
            raise FeatureError("Der Abstand der zweiten Seite darf nicht 0 sein")
        if not -89.0 < self.taper < 89.0 or not -89.0 < self.taper2 < 89.0:
            raise FeatureError("Die Verjüngung muss zwischen -89 und 89 Grad liegen")
        if self.extrude_type not in EXTRUDE_TYPES or self.wall_side not in WALL_SIDES:
            raise FeatureError("Unbekannte Art der Extrusion bzw. Lage der Wand")
        if self.extrude_type == EXTRUDE_TYPES[1] and self.wall <= 0:
            raise FeatureError("Die Wandstärke der dünnen Extrusion muss größer als 0 sein")
        target = None
        if self.extent == EXTENTS[3]:
            target = self._target_plane(state, profiles[0])
        # Abschnitte längs der Normale: (Länge mit Vorzeichen) je Seite, jeweils vom Profil aus
        if target is not None:
            pieces = [target[0]]
        elif self.extent == EXTENTS[4]:
            # Durch alles (wie Fusion): so weit, dass es sicher durch alle Körper reicht; die Richtung gibt das
            # Vorzeichen des Abstands an (0 oder positiv: längs der Normale)
            pieces = [math.copysign(_through_all(state, profiles), self.distance if self.distance else 1.0)]
        elif self.extent == EXTENTS[1]:
            pieces = [self.distance / 2, -self.distance / 2]
        elif self.extent == EXTENTS[2]:
            pieces = [self.distance, -abs(self.distance2) if self.distance > 0 else abs(self.distance2)]
        else:
            pieces = [self.distance]
        for profile, normal in profiles:
            if abs(self.start) > 1e-12:
                # Startversatz: das Profil vorher längs seiner Normale verschieben
                shift = gp_Trsf()
                shift.SetTranslation(gp_Vec(*normal) * self.start)
                profile = BRepBuilderAPI_Transform(profile, shift, True).Shape()
            if self.extrude_type == EXTRUDE_TYPES[1]:
                profile = _thin_profile(profile, self.wall, self.wall_side)
            # ein einziger Körper über alle Abschnitte: zwei Teile zu vereinigen, verträgt der Kern bei
            # verjüngten Seiten nicht zuverlässig
            second = self.taper2 if self.extent == EXTENTS[2] else None
            solid = _extruded(profile, normal, pieces, self.taper, second)
            if target is not None and target[1] is not None:
                # schräge Zielfläche: weit genug ziehen und an ihrer Ebene abschneiden
                solid = _boolean(BRepAlgoAPI_Common, solid, target[1])
            chosen = {name.strip() for name in self.participants.split(",") if name.strip()}
            missing = [name for name in chosen if name not in state.bodies]
            if missing:
                raise FeatureError(f"Beteiligter Körper nicht vorhanden: {', '.join(missing)}")
            _combine(state, solid, self.operation, names=chosen or None)

    def _target_plane(self, state: BuildState, first) -> tuple[float, TopoDS_Shape | None]:
        """„Bis Fläche“: (Länge bis zur Zielebene, Halbraum zum Abschneiden bei schräger Fläche oder None)."""
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace
        from OCP.GeomAbs import GeomAbs_Plane
        if not self.target_points:
            raise FeatureError("„Bis Fläche“: im Modell die Fläche anklicken, bis zu der extrudiert wird")
        face = None
        for body in state.bodies.values():
            found = [f for f in faces_of(body) if math.dist(face_point(f), self.target_points[0]) <= SELECTION_TOLERANCE]
            if found:
                face = found[0]
                break
        if face is None:
            raise FeatureError("„Bis Fläche“: die gewählte Fläche ist nicht mehr vorhanden")
        surface = BRepAdaptor_Surface(TopoDS.Face(face))
        if surface.GetType() != GeomAbs_Plane:
            raise FeatureError("„Bis Fläche“ geht nur bis zu einer ebenen Fläche")
        plane = surface.Plane()
        normal, middle = gp_Vec(*first[1]), face_point(first[0])
        facing = gp_Vec(plane.Axis().Direction())
        slope = normal.Dot(facing)
        if abs(slope) < 1e-9:
            raise FeatureError("„Bis Fläche“: die Fläche liegt parallel zur Extrusionsrichtung")
        # Abstand von der Profilmitte längs der Normale bis zur Ebene
        location = plane.Location()
        length = gp_Vec(gp_Pnt(*middle), location).Dot(facing) / slope - self.start
        if abs(length) < 1e-9:
            raise FeatureError("„Bis Fläche“: die Fläche liegt in der Skizzenebene")
        if abs(abs(slope) - 1.0) < 1e-9:
            return length, None             # parallele Fläche: genau so weit ziehen
        # schräge Fläche: weiter ziehen (Diagonale des Profils dazu) und am Halbraum vor der Ebene abschneiden
        box = Bnd_Box()
        BRepBndLib.Add_s(first[0], box)
        reach = math.sqrt(box.SquareExtent()) * 2 + abs(length)
        side = gp_Pnt(*middle).Translated(normal * (self.start + length * 0.5))
        plane_face = BRepBuilderAPI_MakeFace(plane).Face()
        return math.copysign(reach, length), BRepPrimAPI_MakeHalfSpace(plane_face, side).Solid()


def nearest_edge(shape: TopoDS_Shape, point) -> TopoDS_Shape | None:
    """Die Kante des Körpers, die dem Raumpunkt am nächsten liegt (z. B. dem angeklickten Punkt)."""
    return _nearest(unique_edges(shape), point)


def nearest_face(shape: TopoDS_Shape, point) -> TopoDS_Shape | None:
    """Die Fläche des Körpers, die dem Raumpunkt am nächsten liegt."""
    return _nearest(faces_of(shape), point)


def _nearest(candidates: list, point) -> TopoDS_Shape | None:
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    probe = BRepBuilderAPI_MakeVertex(gp_Pnt(*point)).Vertex()
    best, best_distance = None, math.inf
    for candidate in candidates:
        distance = BRepExtrema_DistShapeShape(probe, candidate)
        if distance.IsDone() and distance.Value() < best_distance:
            best, best_distance = candidate, distance.Value()
    return best


def _plane_normal(face) -> tuple[float, float, float]:
    """Nach außen zeigende Normale einer ebenen Körperfläche (sonst FeatureError)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_REVERSED
    surface = BRepAdaptor_Surface(TopoDS.Face(face))
    if surface.GetType() != GeomAbs_Plane:
        raise FeatureError("Extrudieren lässt sich nur eine ebene Körperfläche")
    direction = surface.Plane().Axis().Direction()
    if face.Orientation() == TopAbs_REVERSED:
        direction = direction.Reversed()
    return direction.X(), direction.Y(), direction.Z()


def _extruded(profile, normal: tuple, lengths: list, taper: float, taper2: float | None = None) -> TopoDS_Shape:
    """Das Profil längs der Normale gezogen, über alle Abschnitte `lengths` (vom Profil aus, mit Vorzeichen).

    Ohne Verjüngung ein Prisma vom tiefsten zum höchsten Punkt; mit Verjüngung eine geradlinige Erhebung durch
    versetzte Kopien des Profils – je weiter vom Profil entfernt, desto stärker versetzt. `taper2` gilt (wenn
    angegeben) für die Abschnitte gegen die Normale, also die zweite Seite.
    """
    import math as _math
    positions = sorted({0.0, *[float(length) for length in lengths]})
    low, high = positions[0], positions[-1]
    angle_at = lambda position: taper2 if taper2 is not None and position < 0 else taper
    if all(abs(angle_at(position)) < 1e-9 for position in positions):
        shift = gp_Trsf()
        shift.SetTranslation(gp_Vec(*normal) * low)
        start = BRepBuilderAPI_Transform(profile, shift, True).Shape()
        return BRepPrimAPI_MakePrism(start, gp_Vec(*normal) * (high - low)).Shape()
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset, BRepOffsetAPI_ThruSections
    from OCP.BRepTools import BRepTools
    from OCP.GeomAbs import GeomAbs_Intersection

    def wires_of(shape) -> list:
        found, walker = [], TopExp_Explorer(shape, TopAbs_WIRE)
        while walker.More():
            found.append(TopoDS.Wire(walker.Current()))
            walker.Next()
        return found

    def length_of(wire) -> float:
        properties = GProp_GProps()
        BRepGProp.LinearProperties_s(wire, properties)
        return properties.Mass()

    result = None
    for face in faces_of(profile):
        face = TopoDS.Face(face)
        outer = BRepTools.OuterWire_s(face)
        holes = [wire for wire in wires_of(face) if not wire.IsSame(outer)]
        # je Lage ein Satz Drähte: [äußerer, Loch 1, Loch 2, …] in derselben Reihenfolge wie am Profil
        sections = []
        for position in positions:
            if abs(position) < 1e-12:
                sections.append([outer, *holes])
                continue
            offset = BRepOffsetAPI_MakeOffset(face, GeomAbs_Intersection)
            offset.Perform(-abs(position) * _math.tan(_math.radians(angle_at(position))))  # positiv: kleiner
            if not offset.IsDone():
                raise FeatureError("Mit dieser Verjüngung verschwindet das Profil (Winkel oder Abstand zu groß)")
            move = gp_Trsf()
            move.SetTranslation(gp_Vec(*normal) * position)
            tops = wires_of(BRepBuilderAPI_Transform(offset.Shape(), move, True).Shape())
            if len(tops) != 1 + len(holes):
                raise FeatureError("Mit dieser Verjüngung verschwindet ein Teil des Profils (Winkel oder Abstand zu groß)")
            top_outer = max(tops, key=length_of)
            rest = [wire for wire in tops if not wire.IsSame(top_outer)]
            ordered = [top_outer]
            for hole in holes:
                # Gegenstück eines Lochs: der Draht, dessen Mitte (zurückgeschoben) am nächsten liegt
                here = _wire_centre(hole)
                partner = min(rest, key=lambda wire: _math.dist(
                    tuple(c - n * position for c, n in zip(_wire_centre(wire), normal)), here))
                rest.remove(partner)
                ordered.append(partner)
            sections.append(ordered)

        def loft(number: int):
            maker = BRepOffsetAPI_ThruSections(True, True)      # Volumen mit geraden Flanken
            for section in sections:
                maker.AddWire(section[number])
            maker.CheckCompatibility(False)
            maker.Build()
            if not maker.IsDone():
                raise FeatureError("Die verjüngte Extrusion ließ sich nicht bilden")
            return maker.Shape()

        piece = loft(0)
        for number in range(1, 1 + len(holes)):
            piece = _boolean(BRepAlgoAPI_Cut, piece, loft(number))
        result = piece if result is None else _boolean(BRepAlgoAPI_Fuse, result, piece)
    return result


def _offset_face(face, distance: float) -> TopoDS_Shape:
    """Die ebene Fläche um `distance` nach außen (positiv) bzw. innen gewachsen, samt Löchern."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
    from OCP.GeomAbs import GeomAbs_Arc
    from OCP.ShapeFix import ShapeFix_Face
    if abs(distance) < 1e-12:
        return face
    offset = BRepOffsetAPI_MakeOffset(TopoDS.Face(face), GeomAbs_Arc)
    offset.Perform(distance)
    if not offset.IsDone():
        raise FeatureError("Die Wand lässt sich so nicht bilden (Wandstärke zu groß für das Profil)")
    wires, walker = [], TopExp_Explorer(offset.Shape(), TopAbs_WIRE)
    while walker.More():
        wires.append(TopoDS.Wire(walker.Current()))
        walker.Next()
    if not wires:
        raise FeatureError("Die Wand lässt sich so nicht bilden (das Profil verschwindet)")

    def size(wire) -> float:
        box = Bnd_Box()
        BRepBndLib.Add_s(wire, box)
        return box.SquareExtent()
    wires.sort(key=size, reverse=True)
    maker = BRepBuilderAPI_MakeFace(wires[0], True)
    for hole in wires[1:]:
        maker.Add(TopoDS.Wire(hole.Reversed()))
    fixer = ShapeFix_Face(maker.Face())
    fixer.FixOrientation()
    fixer.Perform()
    return fixer.Face()


def _thin_profile(profile, wall: float, side: str) -> TopoDS_Shape:
    """Dünne Extrusion (Fusion „Dünn extrudieren“): aus dem Profil nur ein Band der Breite `wall` entlang aller
    Ränder – innen am Umriss, außen oder mittig. Das Band ist Profil-nach-außen minus Profil-nach-innen."""
    outward, inward = {WALL_SIDES[0]: (0.0, wall), WALL_SIDES[1]: (wall, 0.0),
                       WALL_SIDES[2]: (wall / 2, wall / 2)}[side]
    bands = None
    for face in faces_of(profile):
        band = _boolean(BRepAlgoAPI_Cut, _offset_face(face, outward), _offset_face(face, -inward)) \
            if inward > 0 else _boolean(BRepAlgoAPI_Cut, _offset_face(face, outward), face)
        bands = band if bands is None else _boolean(BRepAlgoAPI_Fuse, bands, band)
    if bands is None or not faces_of(bands):
        raise FeatureError("Die Wand lässt sich so nicht bilden")
    return bands


def _wire_centre(wire) -> tuple:
    properties = GProp_GProps()
    BRepGProp.LinearProperties_s(wire, properties)
    point = properties.CentreOfMass()
    return point.X(), point.Y(), point.Z()


def profile_of(state: BuildState, sketch: str, regions: str, points: list = ()) -> TopoDS_Shape:
    """Das Profil einer Skizze: mit freien Löchern, mit allen Bereichen oder nur gewählte Bereiche.

    Gewählte Bereiche werden über einen Punkt in ihrem Inneren gemerkt; so bleibt die Wahl
    gültig, solange an dieser Stelle noch ein Bereich der Skizze liegt.
    """
    if regions == PROFILE_MODES[2]:
        builder, chosen = BRep_Builder(), TopoDS_Compound()
        builder.MakeCompound(chosen)
        faces = faces_of(state.profiles[sketch])
        for point in points:
            face = next((face for face in faces
                         if BRepClass_FaceClassifier(face, gp_Pnt(*point), 1e-6).State() == TopAbs_IN), None)
            if face is None:
                raise FeatureError("Ein gewählter Profilbereich ist in der Skizze nicht mehr vorhanden")
            builder.Add(chosen, face)
        if not points:
            raise FeatureError("Es ist kein Profilbereich gewählt")
        return chosen
    if regions == PROFILE_MODES[0] and sketch in state.solid_profiles:
        return state.solid_profiles[sketch]
    return state.profiles[sketch]


def _combine(state: BuildState, solid: TopoDS_Shape, operation: str, origin: int | None = None,
             participants: set | None = None, names: set | None = None) -> None:
    """Verrechnet ein neues Volumen gemäß der gewählten Operation mit dem Modell.

    Wie in Fusion wirken Vereinigen, Ausschneiden und Schnittmenge nur auf die
    Körper, die das Volumen berührt oder durchdringt. `participants` (Herkünfte, siehe `fusion_participants`)
    beschränkt das wie Fusions „Beteiligte Körper“ auf die Körper, die der übernommene Schritt nennt.
    """
    # Berührte Körper; die dabei schon gerechneten Schnittmengen werden für „Automatisch“ und „Schnittmenge“ weiter
    # verwendet statt neu gerechnet
    # „Neuer Körper“ braucht nicht zu wissen, was das Volumen berührt – die teure Prüfung entfällt dann ganz
    touched, commons, inside = ([], {}, {}) if operation == "Neuer Körper" else touched_bodies(state, solid)
    if participants is not None:
        touched = [name for name in touched if state.body_origin.get(name) in participants]
    if names:
        # „Beteiligte Körper“ von Hand gewählt (Runde 96): nur diese werden geschnitten bzw. vereinigt
        touched = [name for name in touched if name in names]
    if operation == AUTO_OPERATION:
        operation = _automatic_operation(state, solid, touched, commons, inside)
    if operation not in OPERATIONS:
        raise FeatureError(f"Unbekannte Operation '{operation}'")
    if operation in ("Neuer Körper", "Vereinigen"):
        # Wie in Fusion: getrennte Teile (z. B. zwei getrennte Profile in einem Schritt) werden getrennte Körper;
        # beim Vereinigen verschmilzt jedes Teil nur mit dem, was es berührt. Die Operation ist schon für das
        # ganze Volumen entschieden – kein Teil schneidet, während ein anderes vereinigt
        pieces = _connected_pieces(solid)
        if len(pieces) > 1:
            for piece in pieces:
                _combine(state, piece, operation, origin, participants, names)
            return
        # ein zusammenhängendes Teil: mit der vereinigten Form weiterrechnen – bisher blieb hier das unvereinigte
        # Volumen stehen (z. B. zwei Dreiecke eines durch eine Linie geteilten Profils als zwei Volumenkörper in
        # einem Körper, mit Trennkante; Screenshot des Nutzers 2026-10-22)
        solid = pieces[0]
    if operation == "Neuer Körper":
        # aus mehreren aneinandergrenzenden Profilen vereinigt: Trennkanten zwischen ihnen fallen weg (wie Fusion)
        state.add_body(unified(solid), origin)
        return
    if operation == "Vereinigen":
        if not touched:
            # Ohne Berührung gibt es nichts zu vereinigen: das Volumen bleibt ein eigener Körper
            state.add_body(unified(solid), origin)
            return
        # Bewusst als Kette (jeder Schritt nimmt einen Körper dazu): eine Sammelrechnung mit allen Körpern und eine
        # paarweise verteilte Vereinigung waren an stark verschachtelten Körpern langsamer bzw. hingen minutenlang
        merged = solid
        for name in touched:
            merged = _boolean(BRepAlgoAPI_Fuse, state.bodies[name], merged)
        # Das Ergebnis behält den Namen des ältesten beteiligten Körpers
        for name in touched[1:]:
            del state.bodies[name]
        state.bodies[touched[0]] = unified(merged)
        return

    if not state.bodies:
        raise FeatureError(f"Für '{operation}' fehlt ein vorhandener Körper")
    if not touched:
        raise FeatureError(f"'{operation}': Das Volumen trifft keinen vorhandenen Körper")
    tool = BRepAlgoAPI_Cut if operation == "Ausschneiden" else BRepAlgoAPI_Common
    # Viele betroffene Körper (Zufallstest: 18 Körper je 0,8 s): die voneinander unabhängigen Rechnungen auf mehrere
    # Prozesse verteilen; die Ergebnisse kommen im Binärformat des Kerns zurück. Scheitert das, wie bisher hier.
    open_names = [name for name in touched if not (tool is BRepAlgoAPI_Common and name in commons)]
    done = {}
    if len(open_names) >= PARALLEL_BOOLEAN_MIN:
        from . import guard
        blobs = guard.run_parallel(lambda name: _shape_bytes(_boolean(tool, state.bodies[name], solid)), open_names)
        if blobs is not None:
            done = {name: _shape_from_bytes(blob) for name, blob in zip(open_names, blobs)}
    for name in touched:
        if tool is BRepAlgoAPI_Common and name in commons:
            result = commons[name]
        elif name in done:
            result = done[name]
        else:
            result = _boolean(tool, state.bodies[name], solid)
        if TopExp_Explorer(result, TopAbs_SOLID).More():
            store_pieces(state, name, result)   # in Stücke zerschnitten: je Stück ein Körper
        else:
            del state.bodies[name]      # vollständig weggeschnitten


def touched_bodies(state: BuildState, solid: TopoDS_Shape) -> tuple[list[str], dict, dict]:
    """Die Körper, die das Volumen berührt oder durchdringt, in Modellreihenfolge – dazu die dabei schon gerechneten
    Schnittmengen (Name → Form) und gemessenen Schnittmengen-Volumen (Name → Zahl) für „Automatisch“/„Schnittmenge“."""
    touched, commons, inside = [], {}, {}
    candidates = [name for name in state.bodies if name not in state.frozen]
    flags = None
    if len(candidates) >= PARALLEL_TOUCH_MIN:
        # Viele Körper (Zufallstest: 144 Körper, eine Spule – 20 s nur für diese Prüfung): Berührung auf mehrere
        # Prozesse verteilt prüfen. Zurück kommt je Körper „berührt ja/nein“ und das Volumen der Schnittmenge (für
        # „Automatisch“); die Schnittmengen selbst werden nur bei Bedarf unten neu gerechnet. Scheitert das
        # Verteilen, wird wie bisher hier der Reihe nach geprüft.
        from . import guard
        overlapping = [name for name in candidates if not _boxes_apart(state.bodies[name], solid)]
        if len(overlapping) >= PARALLEL_TOUCH_MIN:
            def check(name):
                touching, common = _touch_common(state.bodies[name], solid)
                return touching, (_measure(common)[0] if touching and common is not None else None)
            found = guard.run_parallel(check, overlapping)
            if found is not None:
                answers = dict(zip(overlapping, found))
                flags = [answers.get(name, (False, None))[0] for name in candidates]
                inside = {name: volume for name, (flag, volume) in answers.items() if flag and volume is not None}
    for number, name in enumerate(candidates):
        if flags is not None:
            if flags[number]:
                touched.append(name)
            continue
        touching, common = _touch_common(state.bodies[name], solid)
        if touching:
            touched.append(name)
            if common is not None:
                commons[name] = common
    return touched, commons, inside


def _shape_bytes(shape: TopoDS_Shape) -> bytes:
    """Form im Binärformat des Kerns (zum Übertragen aus einem Kindprozess)."""
    import io
    from OCP.BinTools import BinTools
    buffer = io.BytesIO()
    BinTools.Write_s(shape, buffer)
    return buffer.getvalue()


def _shape_from_bytes(blob: bytes) -> TopoDS_Shape:
    """Gegenstück zu `_shape_bytes`."""
    import io
    from OCP.BinTools import BinTools
    shape = TopoDS_Shape()
    BinTools.Read_s(shape, io.BytesIO(blob))
    return shape


def unified(shape: TopoDS_Shape) -> TopoDS_Shape:
    """Fasst nach einer Booleschen Operation Flächen, die in derselben Fläche liegen, und Kanten auf derselben Kurve
    zusammen – wie Fusion. Ohne das blieben z. B. zwei bündig vereinigte Quader in 10 statt 6 Flächen zerstückelt:
    die Teilflächen ließen sich nicht einzeln löschen und eine Rundung an der zerstückelten Kante scheiterte
    (Nutzervideo 2026-10-22). Ergibt das Zusammenfassen keinen gültigen Volumenkörper, bleibt die Form, wie sie ist."""
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    try:
        unify = ShapeUpgrade_UnifySameDomain(shape, True, True, False)
        if _KEEP_EDGES is not None and geometry_level() >= 2:
            unify.KeepShapes(_KEEP_EDGES)       # nur die Nähte dieses Schritts verschmelzen
        unify.Build()
        result = unify.Shape()
        if not result.IsNull() and TopExp_Explorer(result, TopAbs_SOLID).More():
            if is_valid(result):
                return result
            if geometry_level() < 2:
                return shape                # ältere Entwürfe: wie damals unverändert lassen
            # das Zusammenfassen hinterlässt manchmal nur Toleranzfehler: reparieren statt verwerfen
            fixed = repaired(result)
            if fixed is not None:
                return fixed
    except Exception:
        pass
    return shape


# Ergebnisse der Gültigkeitsprüfung je Formkern (Runde 85). Die Gültigkeit hängt nicht von der Lage ab: Kopien aus
# Muster, Kreismuster und Einfügen teilen den Formkern und brauchen nur eine Prüfung. Schlüssel: Hash der Form ohne
# Lage -> [(Form, gültig)]
_VALIDITY: dict[int, list] = {}
_VALIDITY_LIMIT = 1000     # hält die Formen fest – nicht zu viele


def is_valid(shape: TopoDS_Shape) -> bool:
    """Formal gültige Form laut Prüfung des Geometriekerns (geschlossene Hülle, passende Kanten …)."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.TopLoc import TopLoc_Location
    try:
        key = hash(shape.Located(TopLoc_Location()))
        for other, valid in _VALIDITY.get(key, ()):
            if shape.IsPartner(other) and shape.Orientation() == other.Orientation():
                return valid
        valid = BRepCheck_Analyzer(shape).IsValid()
    except Exception:
        return False
    if sum(len(entries) for entries in _VALIDITY.values()) > _VALIDITY_LIMIT:
        _VALIDITY.clear()
    _VALIDITY.setdefault(key, []).append((shape, valid))
    return valid


def repaired(shape: TopoDS_Shape) -> TopoDS_Shape | None:
    """Repariert kleine Formfehler (Toleranzen, Kantenreihenfolge); None, wenn die Form danach nicht gültig ist."""
    from OCP.ShapeFix import ShapeFix_Shape
    try:
        fixer = ShapeFix_Shape(shape)
        fixer.Perform()
        result = fixer.Shape()
    except Exception:
        return None
    if result.IsNull() or not TopExp_Explorer(result, TopAbs_SOLID).More() or not is_valid(result):
        return None
    return result


def healed(shape: TopoDS_Shape, source_valid: bool = True) -> TopoDS_Shape | None:
    """Ein Körper nach einem Schritt, mit zusammengefassten Flächen, damit z. B. eine Vereinigung keine Nahtkanten
    zurücklässt (Nutzerbilder 2026-10-26). None, wenn der Schritt eine ungültige Form erzeugt hat.

    `source_valid`: War der Körper vor dem Schritt gültig (bzw. ist er neu)? Dann hat erst dieser Schritt die Form
    kaputt gemacht – etwa zwei Rundungen in einer Rille, die sich überlappen (Nutzerbild 5: Dreiecksmurks und eine
    hauchdünne Fläche in den Körper). Reparieren hieße dann, Murks schönzurechnen; wie Fusion wird der Schritt
    abgelehnt. War der Körper schon vorher ungültig (z. B. aus einem Import), wird repariert, soweit es geht, und
    sonst unverändert weitergereicht.
    """
    if not TopExp_Explorer(shape, TopAbs_SOLID).More():
        return shape                        # Flächen-/Hilfsformen ohne Volumen: unverändert
    if not is_valid(shape):
        if source_valid:
            return None
        fixed = repaired(shape)
        if fixed is None:
            return shape
        shape = fixed
    return unified(shape)


def _connected_pieces(solid: TopoDS_Shape) -> list[TopoDS_Shape]:
    """Die zusammenhängenden Teile eines Volumens: einzelne Volumenkörper, die sich berühren, werden zu einem
    Teil vereinigt. Ein Volumen aus einem Stück kommt unverändert zurück."""
    solids = []
    explorer = TopExp_Explorer(solid, TopAbs_SOLID)
    while explorer.More():
        solids.append(explorer.Current())
        explorer.Next()
    if len(solids) <= 1:
        return [solid]
    groups: list[list[int]] = []
    for index, shape in enumerate(solids):
        joined = [group for group in groups if any(_touches(solids[other], shape) for other in group)]
        merged = [index] + [member for group in joined for member in group]
        groups = [group for group in groups if group not in joined] + [merged]
    pieces = []
    for group in groups:
        piece = solids[group[0]]
        for member in group[1:]:
            piece = _boolean(BRepAlgoAPI_Fuse, piece, solids[member])
        pieces.append(piece)
    return pieces


def store_pieces(state: BuildState, name: str, shape: TopoDS_Shape) -> list[str]:
    """Legt das Ergebnis eines Schnitts ab: zerfällt ein Körper dabei in getrennte Stücke (z. B. Boden abgetrennt,
    Nutzerbild 2026-10-26), wird wie in Fusion jedes Stück ein eigener Körper. Das erste Stück behält Name, Herkunft
    und Farbe. Ältere Entwürfe (Geometriestufe 1) behalten ein Stück aus mehreren Teilen wie damals."""
    pieces = _connected_pieces(shape) if geometry_level() >= 2 else [shape]
    state.bodies[name] = unified(pieces[0])
    names = [name]
    for piece in pieces[1:]:
        added = state.add_body(unified(piece), state.body_origin.get(name))
        if name in state.colors:
            state.colors[added] = state.colors[name]
        names.append(added)
    return names


def _automatic_operation(state: BuildState, solid: TopoDS_Shape, touched: list[str], commons: dict | None = None,
                         volumes: dict | None = None) -> str:
    """Wählt die Operation wie Fusion: ohne Berührung ein neuer Körper; steckt das Volumen
    überwiegend in vorhandenen Körpern, wird ausgeschnitten, sonst vereinigt. `commons`: schon gerechnete
    Schnittmengen je Körper (aus der Berührungsprüfung), `volumes`: schon gemessene Schnittmengen-Volumen."""
    if not touched:
        return "Neuer Körper"
    inside = 0.0
    for name in touched:
        if volumes and name in volumes:
            inside += max(0.0, volumes[name])
            continue
        try:
            common = (commons or {}).get(name)
            if common is None:
                common = _boolean(BRepAlgoAPI_Common, state.bodies[name], solid)
            inside += max(0.0, _measure(common)[0])
        except FeatureError:
            continue
    return "Ausschneiden" if inside > 0.5 * _measure(solid)[0] else "Vereinigen"


def _touches(first: TopoDS_Shape, second: TopoDS_Shape) -> bool:
    """Berühren oder durchdringen sich die beiden Formen?"""
    return _touch_common(first, second)[0]


def _boxes_apart(first: TopoDS_Shape, second: TopoDS_Shape) -> bool:
    """Liegen die Hüllquader der beiden Formen getrennt (dann können sie sich nicht berühren)?"""
    boxes = []
    for shape in (first, second):
        box = Bnd_Box()
        BRepBndLib.Add_s(shape, box)
        boxes.append(box)
    return boxes[0].IsOut(boxes[1])


def _touch_common(first: TopoDS_Shape, second: TopoDS_Shape) -> tuple[bool, TopoDS_Shape | None]:
    """Berühren oder durchdringen sich die beiden Formen? Dazu die Schnittmenge, wenn sie dabei gerechnet wurde
    (sonst None)."""
    if _boxes_apart(first, second):
        return False, None      # schneller Ausschluss über die Hüllquader
    # Erst die Schnittmenge (Durchdringung), dann der Schnitt der Oberflächen (bloßes Berühren an Fläche, Kante oder
    # Punkt). Beides ist an verwickelten Körpern 4- bis 7-mal schneller als das genaue Abstandsmaß (Zufallstest
    # Startwert 11: 312 Körper, 66 s nur für diese Prüfung); das Abstandsmaß bleibt der Rückfall, wenn eine der
    # Rechnungen scheitert.
    try:
        common = _built(BRepAlgoAPI_Common, first, second)
        if common.IsDone() and TopExp_Explorer(common.Shape(), TopAbs_SOLID).More():
            return True, common.Shape()
        section = _built(BRepAlgoAPI_Section, first, second)
        if section.IsDone() and common.IsDone():
            shape = section.Shape()
            touching = TopExp_Explorer(shape, TopAbs_EDGE).More() or TopExp_Explorer(shape, TopAbs_VERTEX).More()
            return touching, common.Shape()
    except Exception:       # Kernfehler: zur genauen Messung
        pass
    distance = BRepExtrema_DistShapeShape(first, second)
    return distance.IsDone() and distance.Value() < 1e-6, None


def _built(operation, base: TopoDS_Shape, tool: TopoDS_Shape):
    """Die gerechnete Boolesche Operation (Fuse, Cut, Common, Section) von `base` mit `tool`.

    Im Hauptprozess rechnet der Kern parallel (18 sich überlappende Zylinder vereinigen: 81 → 19 s); im
    Kindprozess eines Probelaufs seriell, weil parallele Rechnungen dort verklemmen können (siehe guard.in_child).
    """
    from OCP.collections import List_TopoDS_Shape
    from . import guard
    result = operation()
    arguments, tools = List_TopoDS_Shape(), List_TopoDS_Shape()
    arguments.Append(base)
    tools.Append(tool)
    result.SetArguments(arguments)
    result.SetTools(tools)
    result.SetRunParallel(not guard.in_child())
    result.Build()
    return result


def _boolean(operation, base: TopoDS_Shape, tool: TopoDS_Shape) -> TopoDS_Shape:
    """Boolesche Operation von `base` mit `tool` als Form; scheitert sie, ein FeatureError."""
    result = _built(operation, base, tool)
    if not result.IsDone():
        raise FeatureError("Die Boolesche Operation ist fehlgeschlagen")
    return result.Shape()


def _sharp_edges(shape: TopoDS_Shape) -> list:
    """Alle scharfen Kanten des Körpers, jede genau einmal.

    Tangentiale Übergänge (z. B. die Ränder einer vorhandenen Verrundung) und
    Nahtkanten von Zylindern werden ausgelassen, weil sie sich nicht sinnvoll
    verrunden oder fasen lassen.
    """
    BRepLib.EncodeRegularity_s(shape, 1e-4)
    edge_faces = IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, edge_faces)
    sharp = []
    for index in range(1, edge_faces.Size() + 1):
        faces = list(edge_faces.FindFromIndex(index))
        if len(faces) != 2 or faces[0].IsSame(faces[1]):
            continue
        edge = TopoDS.Edge(edge_faces.FindKey(index))
        if BRep_Tool.Continuity_s(edge, TopoDS.Face(faces[0]), TopoDS.Face(faces[1])) == GeomAbs_C0:
            sharp.append(edge)
    return sharp


def missing_body(name: str) -> FeatureError:
    """Meldung für einen fehlenden Körper: ohne Auswahl „Bitte einen Körper wählen“, sonst „… nicht mehr da“."""
    if not name:
        return FeatureError("Bitte einen Körper wählen (im Modell anklicken oder im Eigenschaftenfeld)")
    return FeatureError(f"Körper '{name}' ist nicht (mehr) vorhanden")


def select_bodies(state: BuildState, body: str) -> list[str]:
    """Namen der gewählten Körper: ein benannter Körper, alle Körper einer Komponente
    oder mit "Alle" alle veränderbaren."""
    if body not in state.bodies and body in state.components.values():
        names = [name for name in state.component_bodies(body) if name not in state.frozen]
        if not names:
            raise FeatureError(f"Komponente '{body}' enthält keinen veränderbaren Körper mehr")
        return names
    if body == ALL_BODIES:
        names = [name for name in state.bodies if name not in state.frozen]
        if not names:
            raise FeatureError("Es ist noch kein veränderbarer Körper vorhanden")
        return names
    if body not in state.bodies:
        raise missing_body(body)
    if body in state.frozen:
        raise FeatureError(f"Körper '{body}' ist ein gespeicherter Endstand und nicht veränderbar")
    return [body]


def unique_edges(shape: TopoDS_Shape) -> list:
    """Alle Kanten einer Form, jede genau einmal, in fester Reihenfolge."""
    found = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, found)
    return [TopoDS.Edge(found.FindKey(index)) for index in range(1, found.Size() + 1)]


def unique_vertices(shape: TopoDS_Shape) -> list:
    """Alle Eckpunkte einer Form, jeder genau einmal, in fester Reihenfolge."""
    found = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, TopAbs_VERTEX, found)
    return [TopoDS.Vertex(found.FindKey(index)) for index in range(1, found.Size() + 1)]


def faces_of(shape: TopoDS_Shape) -> list:
    """Alle Flächen einer Form in fester Reihenfolge."""
    found = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, TopAbs_FACE, found)
    return [TopoDS.Face(found.FindKey(index)) for index in range(1, found.Size() + 1)]


_EDGE_POINTS: dict[int, list] = {}         # wie _FACE_POINTS: Merkpunkt je Kante (Runde 79)


def edge_point(edge) -> tuple[float, float, float]:
    """Merkpunkt einer Kante: der Punkt in der Mitte ihres Parameterbereichs."""
    key = hash(edge)
    for known, point in _EDGE_POINTS.get(key, ()):
        if known.IsEqual(edge):
            return point
    curve = BRepAdaptor_Curve(edge)
    found = curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2)
    point = (found.X(), found.Y(), found.Z())
    if len(_EDGE_POINTS) > _FACE_POINTS_LIMIT:
        _EDGE_POINTS.clear()
    _EDGE_POINTS.setdefault(key, []).append((edge, point))
    return point


def face_on_ray(shape: TopoDS_Shape, origin, direction) -> tuple | None:
    """Die erste Fläche der Form, die ein Sehstrahl trifft, samt Trefferpunkt: (Fläche, (x, y, z)) oder None.

    Gerechnet wird an der exakten Form (nicht an der angezeigten Vernetzung). So findet ein Werkzeug die
    angeklickte Fläche im Körper vor seiner eigenen Wirkung – z. B. die Schale, deren Vorschau die Oberseite
    schon geöffnet zeigt: der Klick geht dort durch das Loch, gemeint ist aber die Oberseite.
    """
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    from OCP.gp import gp_Dir, gp_Lin
    finder = IntCurvesFace_ShapeIntersector()
    finder.Load(shape, 1e-7)
    finder.Perform(gp_Lin(gp_Pnt(*origin), gp_Dir(*direction)), 0.0, 1e12)
    if not finder.IsDone() or finder.NbPnt() == 0:
        return None
    nearest = min(range(1, finder.NbPnt() + 1), key=finder.WParameter)
    point = finder.Pnt(nearest)
    return finder.Face(nearest), (point.X(), point.Y(), point.Z())


def circle_centers(shape: TopoDS_Shape) -> list[tuple[tuple, float, tuple]]:
    """Mittelpunkte aller Kreis- und Bogenkanten der Form (Zylinder, Bohrungen, Rundungen …), wie Fusion sie fängt:
    [(Mittelpunkt, Radius, Achsrichtung), …], jeder Kreis nur einmal (gleiche Mitte und gleicher Radius)."""
    from OCP.GeomAbs import GeomAbs_Circle
    found: list[tuple[tuple, float, tuple]] = []
    for edge in unique_edges(shape):
        curve = BRepAdaptor_Curve(edge)
        if curve.GetType() != GeomAbs_Circle:
            continue
        circle = curve.Circle()
        centre, axis = circle.Location(), circle.Axis().Direction()
        point = (centre.X(), centre.Y(), centre.Z())
        if any(math.dist(point, other) < 1e-6 and abs(circle.Radius() - radius) < 1e-6
               for other, radius, _axis in found):
            continue
        found.append((point, circle.Radius(), (axis.X(), axis.Y(), axis.Z())))
    return found


# Flächenschwerpunkte je Fläche gemerkt (Runde 79): Gewählte Flächen werden bei jeder Neuberechnung über ihre
# Merkpunkte wiedergefunden – dafür wurden alle Flächen eines Körpers neu integriert (im Nutzerentwurf ~1500 Flächen,
# 20 ms je Live-Änderung). Formen ändern sich nach dem Entstehen nicht; dieselbe Fläche hat denselben Schwerpunkt.
_FACE_POINTS: dict[int, list] = {}
_FACE_POINTS_LIMIT = 60000


def face_point(face) -> tuple[float, float, float]:
    """Merkpunkt einer Fläche: ihr Flächenschwerpunkt."""
    key = hash(face)
    for known, point in _FACE_POINTS.get(key, ()):
        if known.IsEqual(face):         # gleicher Hash allein genügt nicht: wirklich dieselbe Fläche?
            return point
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, properties)
    centre = properties.CentreOfMass()
    point = (centre.X(), centre.Y(), centre.Z())
    if len(_FACE_POINTS) > _FACE_POINTS_LIMIT:
        _FACE_POINTS.clear()            # einfache Obergrenze: der Speicher füllt sich danach von selbst neu
    _FACE_POINTS.setdefault(key, []).append((face, point))
    return point


def find_by_points(items: list, point_of, points: list, label: str) -> list:
    """Sucht zu jedem gemerkten Punkt das Element (Kante oder Fläche) an dieser Stelle.

    Gewählte Kanten und Flächen werden über ihre Lage gemerkt, weil sich ihre
    Nummerierung bei jeder Neuberechnung ändern kann. Liegt an einer Stelle kein
    Element mehr, ist die Auswahl ungültig geworden.
    """
    located = [(point_of(item), item) for item in items]
    chosen = []
    for number, point in enumerate(points):
        distance, item = min(((math.dist(found, point[:3]), item) for found, item in located),
                             key=lambda pair: pair[0], default=(math.inf, None))
        if distance > SELECTION_TOLERANCE:
            # Liegt der Merkpunkt auf einem Element (z. B. Mitte eines früheren Kantenstücks, das inzwischen mit
            # seinem Nachbarn zu einer durchgehenden Kante zusammengefasst ist – siehe `unified`), gilt dieses
            item = _containing(items, point)
        if distance > SELECTION_TOLERANCE and item is None:
            # Wie Fusion: hat ein früherer Schritt die Fläche/Kante verschoben (z. B. eine längere Extrusion),
            # wird sie über den vorigen Stand wiedergefunden und der Merkpunkt nachgeführt
            item = _follow(items, point_of, point)
            if item is None:
                raise FeatureError(f"Eine gewählte {label} ist im Modell nicht mehr vorhanden")
            if not isinstance(point, list) and isinstance(points, list):
                points[number] = [*point_of(item), *point[3:]]     # unveränderlicher Merkpunkt: Eintrag ersetzen
        if not any(item.IsSame(other) for other in chosen):
            chosen.append(item)
    return chosen


def _containing(items: list, point: list):
    """Das Element (Kante oder Fläche), auf dem der Punkt liegt (Abstand bis SELECTION_TOLERANCE); sonst None."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*[float(c) for c in point[:3]])).Vertex()
    best = None
    for item in items:
        try:
            measure = BRepExtrema_DistShapeShape(vertex, item)
            distance = measure.Value() if measure.IsDone() else math.inf
        except Exception:
            continue
        if distance <= SELECTION_TOLERANCE and (best is None or distance < best[0]):
            best = (distance, item)
    return best[1] if best is not None else None


def _follow(items: list, point_of, point: list):
    """Das Element in `items`, das dem im vorigen Stand an `point` gemerkten entspricht (gleiche Art und Richtung,
    kleinste Verschiebung); None ohne vorigen Stand oder ohne passenden Kandidaten. Der Merkpunkt wird nachgeführt."""
    if _PREVIOUS is None:
        return None
    kind_of = faces_of if point_of is face_point else unique_edges
    old = None
    for body in [*_PREVIOUS.bodies.values(), *_PREVIOUS.surfaces.values()]:
        for candidate in kind_of(body):
            if math.dist(point_of(candidate), point[:3]) <= SELECTION_TOLERANCE:
                old = candidate
                break
        if old is not None:
            break
    if old is None:
        return None
    scored = [(score, item) for item in items for score in [_correspondence(old, item, point_of)] if score is not None]
    if not scored:
        return None
    _score, best = min(scored, key=lambda pair: pair[0])
    if isinstance(point, list):
        point[:3] = list(point_of(best))       # nachgeführt: beim nächsten Mal wird er direkt gefunden
    return best


def _correspondence(old, new, point_of) -> float | None:
    """Wie gut entspricht `new` dem früheren Element `old` (kleiner = besser; None = gar nicht)?
    Flächen: gleiche Flächenart, eben mit gleich gerichteter Normale; Kanten: gleiche Kurvenart und Richtung."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane
    old_point, new_point = gp_Pnt(*point_of(old)), gp_Pnt(*point_of(new))
    if point_of is face_point:
        a, b = BRepAdaptor_Surface(TopoDS.Face(old)), BRepAdaptor_Surface(TopoDS.Face(new))
        if a.GetType() != b.GetType():
            return None
        if a.GetType() == GeomAbs_Plane:
            na, nb = _plane_normal(TopoDS.Face(old)), _plane_normal(TopoDS.Face(new))
            if sum(x * y for x, y in zip(na, nb)) < 0.9999:
                return None
            # Abstand der Ebenen längs der Normale zählt wenig (die Fläche ist verschoben worden), der Versatz
            # in der Ebene viel (eine andere Fläche gleicher Richtung)
            shift = gp_Vec(old_point, new_point)
            along = abs(shift.X() * na[0] + shift.Y() * na[1] + shift.Z() * na[2])
            across = math.sqrt(max(shift.SquareMagnitude() - along * along, 0.0))
            return along * 0.1 + across
        if a.GetType() == GeomAbs_Cylinder:
            ca, cb = a.Cylinder(), b.Cylinder()
            if not ca.Axis().Direction().IsParallel(cb.Axis().Direction(), 1e-6):
                return None
            return ca.Axis().Location().Distance(cb.Axis().Location()) * 0.1 + abs(ca.Radius() - cb.Radius())
        return old_point.Distance(new_point)
    a, b = BRepAdaptor_Curve(TopoDS.Edge(old)), BRepAdaptor_Curve(TopoDS.Edge(new))
    if a.GetType() != b.GetType():
        return None
    if a.GetType() == GeomAbs_Line and not a.Line().Direction().IsParallel(b.Line().Direction(), 1e-6):
        return None
    if a.GetType() == GeomAbs_Circle:
        if not a.Circle().Axis().Direction().IsParallel(b.Circle().Axis().Direction(), 1e-6):
            return None
        return a.Circle().Location().Distance(b.Circle().Location()) + abs(a.Circle().Radius() - b.Circle().Radius())
    return old_point.Distance(new_point)


def _modify_edges(state: BuildState, modify, body: str, points: list) -> None:
    """Wendet `modify(Körper, Kanten)` an: auf die gewählten Kanten oder, ohne Auswahl,
    auf alle scharfen Kanten der gewählten Körper."""
    changed = False
    for name in select_bodies(state, body):
        body_shape = state.bodies[name]
        if points:
            edges = find_by_points(unique_edges(body_shape), edge_point, points, "Kante")
        else:
            edges = _sharp_edges(body_shape)
        if edges:
            state.bodies[name] = modify(body_shape, edges)
            changed = True
    if not changed:
        raise FeatureError("Der Körper hat keine scharfen Kanten mehr")


@dataclass
class Fillet(Feature):
    """Verrundet Kanten wie Fusion „Abrunden“.

    Drei Arten (`fillet_type`):
    - Verrundung: die gewählten Kanten; Radius konstant, über die Sehnenlänge oder variabel (Anfang, ein
      Zwischenpunkt, Ende). Weitere Auswahlsätze (`sets`) runden andere Kanten im selben Schritt mit eigenem Radius.
    - Regelverrundung: alle scharfen Kanten des Körpers bzw. der gewählten Flächen, wahlweise nur Außenkanten
      (Rundungen) oder nur Innenkanten (Kehlen).
    - Volle Rundung: über eine Mittelfläche (z. B. Oberseite einer Rippe) zwischen zwei parallelen Seitenflächen;
      der Radius ergibt sich aus deren Abstand.
    Ohne gewählte Kanten (nur ältere Entwürfe) werden alle scharfen Kanten gerundet.
    """

    radius: float = 2.0
    radius2: float = 0.0        # variabler Radius: am Ende der Kante (0 = überall gleich)
    body: str = ALL_BODIES
    edge_points: list = field(default_factory=list)     # Merkpunkte der gewählten Kanten
    # Tangentenkette wie in Fusion: der Geometriekern nimmt tangential anschließende Kanten selbst mit (eine
    # angeklickte Oberkante eines abgerundeten Quaders rundet den ganzen Umlauf) – abschalten lässt sich das nicht
    # Wie Fusion: ohne gewählte Kanten geschieht nichts (neu angelegte Schritte). Ältere Entwürfe ohne dieses Feld
    # runden ohne Auswahl wie früher alle scharfen Kanten
    require_pick: bool = False
    # Gleich dicke Wand: sind Innen- und Außenkante einer gleich starken Wand gewählt, bekommt die äußere den Radius
    # plus Wandstärke – beide Bögen konzentrisch (sonst wird die Wand in der Biegung dicker). Neu angelegte Rundungen
    # haben das eingeschaltet, ältere Entwürfe nicht
    wall_aware: bool = False
    # --- seit Runde 91 (Fusion-Funktionen) ---
    fillet_type: str = FILLET_KINDS[0]
    radius_type: str = RADIUS_KINDS[0]
    mid_radius: float = 0.0     # variabel: Radius am Zwischenpunkt (0 = kein Zwischenpunkt)
    mid_position: float = 0.5   # variabel: Lage des Zwischenpunkts längs der Kante (0 = Anfang, 1 = Ende)
    # weitere Auswahlsätze wie das „+“ in Fusion: je Eintrag {"radius": mm, "edge_points": [...]}
    sets: list = field(default_factory=list)
    rule_scope: str = RULE_SCOPES[0]
    rule_rounds: bool = True    # Regelverrundung: Außenkanten (Rundungen)
    rule_fillets: bool = True   # Regelverrundung: Innenkanten (Kehlen)
    face_points: list = field(default_factory=list)     # Flächen der Regel- bzw. Mittelflächen der vollen Rundung

    KIND = "Verrundung"

    def __post_init__(self) -> None:
        # vor Runde 91 gab es keine Radiusart: ein abweichender Radius am Ende hieß „variabel“ (ältere Entwürfe,
        # Fusion-Übernahme und Code, der Fillet(radius=…, radius2=…) anlegt)
        if self.radius_type == RADIUS_KINDS[0] and self.radius2 > 0 and abs(self.radius2 - self.radius) > 1e-9:
            self.radius_type = RADIUS_KINDS[2]

    def to_dict(self) -> dict:
        data = super().to_dict()
        if self.radius_type != RADIUS_KINDS[2]:
            # ungenutzter Endradius: nicht speichern, sonst hielte __post_init__ die Rundung beim Laden für variabel
            data["radius2"] = 0.0
        return data

    # Reihenfolge im Eigenschaftenfeld wie in Fusion: Art, Auswahl, Sätze, Radiusart, Maße, Optionen
    _ORDER = ("fillet_type", "body", "edge_points", "face_points", "sets", "radius_type", "radius", "radius2",
              "mid_radius", "mid_position", "rule_scope", "rule_rounds", "rule_fillets", "wall_aware")

    def parameters(self) -> list:
        items = [item for item in super().parameters() if item.name != "require_pick"]
        rank = {name: number for number, name in enumerate(self._ORDER)}
        return sorted(items, key=lambda item: rank.get(item.name, len(rank)))

    def _groups(self) -> list[tuple[float, float, list]]:
        """(Radius am Anfang, am Ende, Kantenpunkte) je Auswahlsatz; der erste ist der gerade bearbeitete."""
        variable = self.radius_type == RADIUS_KINDS[2]
        # der gerade bearbeitete Satz zählt erst, wenn er Kanten hat (sonst steht er nach „+“ leer bei 0)
        groups = [(self.radius, self.radius2 if variable else 0.0, self.edge_points)] if self.edge_points or \
            not any(entry.get("edge_points") for entry in self.sets) else []
        for entry in self.sets:
            # die Liste selbst (keine Kopie): hat ein früherer Schritt eine Kante verschoben, führt find_by_points
            # ihren Merkpunkt darin nach
            if entry.get("edge_points"):
                groups.append((float(entry.get("radius", self.radius)), 0.0, entry["edge_points"]))
        return groups

    def apply(self, state: BuildState) -> None:
        if self.fillet_type not in FILLET_KINDS:
            raise FeatureError(f"Unbekannte Art der Rundung '{self.fillet_type}'")
        if self.fillet_type == FILLET_KINDS[2]:
            self._full_round(state)
            return
        finished_sets = any(entry.get("edge_points") for entry in self.sets) and self.fillet_type == FILLET_KINDS[0]
        if self.radius <= 0 and (self.edge_points or not finished_sets):
            raise FeatureError("Der Radius muss größer als 0 sein")
        if self.fillet_type == FILLET_KINDS[1]:
            self._rule_fillet(state)
            return
        if self.radius_type not in RADIUS_KINDS:
            raise FeatureError(f"Unbekannte Radiusart '{self.radius_type}'")
        groups = self._groups()
        for radius, _end, _points in groups:
            if radius <= 0:
                raise FeatureError("Der Radius jedes Auswahlsatzes muss größer als 0 sein")
        points = [point for _start, _end, group in groups for point in group]
        if self.require_pick and not points:
            raise FeatureError(PICK_EDGES_MESSAGE)
        if self.radius_type == RADIUS_KINDS[2] and self.mid_radius > 0 and not 0.0 < self.mid_position < 1.0:
            raise FeatureError("Der Zwischenpunkt liegt zwischen 0 (Anfang) und 1 (Ende) der Kante")
        largest = max([self.radius, self.radius2, self.mid_radius, *[start for start, _e, _g in groups]])

        def fillet(body: TopoDS_Shape, edges: list, scale: float = 1.0) -> TopoDS_Shape:
            maker = BRepFilletAPI_MakeFillet(body)
            # Kante -> (Radius Anfang, Ende, gehört zum bearbeiteten Satz); jeder Satz sucht seine Kanten selbst
            plan = _edge_plan(body, groups, edges, (self.radius, 0.0), main=self.edge_points)
            edges = [edge for edge, _start, _end, _main in plan]
            # Außenkanten gleich starker Wände: Radius plus Wandstärke (je Kante der Zuschlag)
            extra = wall_outer_extra(body, edges) if self.wall_aware else {}
            for number, (edge, start, end, main) in enumerate(plan):
                if self.radius_type == RADIUS_KINDS[1] and main:
                    start = _radius_from_chord(body, edge, start)       # Sehnenlänge -> Radius je Kante
                if main and self.radius_type == RADIUS_KINDS[2] and self.mid_radius > 0:
                    # variabel mit Zwischenpunkt: Radius über die (relative) Lage auf der Kante interpoliert
                    maker.Add(TopoDS.Edge(edge))
                    table = Array1_gp_Pnt2d(1, 3)
                    table.SetValue(1, gp_Pnt2d(0.0, start * scale))
                    table.SetValue(2, gp_Pnt2d(self.mid_position, self.mid_radius * scale))
                    table.SetValue(3, gp_Pnt2d(1.0, (end if end > 0 else start) * scale))
                    maker.SetRadius(table, maker.Contour(TopoDS.Edge(edge)), 1)
                elif end > 0 and abs(end - start) > 1e-9:
                    # variable Verrundung: der Radius wächst gleichmäßig vom Anfang zum Ende der Kante
                    maker.Add(start * scale, end * scale, TopoDS.Edge(edge))
                else:
                    maker.Add((start + extra.get(number, 0.0)) * scale, edge)
            return _finish(maker, "Verrundung", "Radius")

        _modify_edges(state, _with_full_round(fillet, largest), self.body, points)

    def _rule_fillet(self, state: BuildState) -> None:
        """Regelverrundung: alle scharfen Kanten (bzw. die Ränder der gewählten Flächen), gefiltert nach Außen-
        und Innenkanten – wie Fusion „Regelverrundung“ mit „Rundungen“/„Kehlen“."""
        if not (self.rule_rounds or self.rule_fillets):
            raise FeatureError("Bitte Rundungen (Außenkanten), Kehlen (Innenkanten) oder beides einschalten")
        if self.rule_scope not in RULE_SCOPES:
            raise FeatureError(f"Unbekannter Umfang '{self.rule_scope}'")
        if self.rule_scope == RULE_SCOPES[1] and not self.face_points:
            raise FeatureError("Für die Regelverrundung an Flächen bitte Flächen in der Ansicht anklicken")
        changed = False
        for name in select_bodies(state, self.body):
            body = state.bodies[name]
            edges = _sharp_edges(body)
            if self.rule_scope == RULE_SCOPES[1]:
                try:
                    faces = find_by_points(faces_of(body), face_point, self.face_points, "Fläche")
                except FeatureError:
                    continue            # die Flächen gehören zu einem anderen Körper
                rims = [edge for face in faces for edge in unique_edges(face)]
                edges = [edge for edge in edges if any(edge.IsSame(rim) for rim in rims)]
            edges = [edge for edge in edges
                     if (self.rule_fillets if _edge_is_concave(body, edge) else self.rule_rounds)]
            if edges:
                build = lambda shape, chosen, scale=1.0: _fillet_all(shape, chosen, self.radius * scale)
                state.bodies[name] = _with_full_round(build, self.radius)(body, edges)
                changed = True
        if not changed:
            raise FeatureError("Keine passenden Kanten für die Regelverrundung gefunden")

    def _full_round(self, state: BuildState) -> None:
        """Volle Rundung: je Mittelfläche die beiden Kanten zu den zwei parallelen Seitenflächen mit dem halben
        Abstand der Seiten runden – die Mittelfläche verschwindet, es bleibt ein Halbzylinder (wie Fusion)."""
        if not self.face_points:
            raise FeatureError("Für die volle Rundung bitte die Mittelfläche (z. B. Oberseite einer Rippe) anklicken")
        changed = False
        for name in select_bodies(state, self.body):
            body = state.bodies[name]
            try:
                centres = find_by_points(faces_of(body), face_point, self.face_points, "Fläche")
            except FeatureError:
                continue
            edges, radius = [], 0.0
            for centre in centres:
                pair = _full_round_sides(body, centre)
                if pair is None:
                    raise FeatureError("Volle Rundung: neben der Mittelfläche liegen keine zwei parallelen ebenen "
                                       "Seitenflächen (bitte die Fläche zwischen den Seiten wählen)")
                rims, gap = pair
                edges += rims
                radius = max(radius, gap / 2)
            build = lambda shape, chosen, scale=1.0: _fillet_all(shape, chosen, radius * scale)
            state.bodies[name] = _with_full_round(build, radius)(body, edges)
            changed = True
        if not changed:
            raise FeatureError("Die Mittelfläche liegt nicht auf dem gewählten Körper")


def _edge_plan(body: TopoDS_Shape, groups: list, fallback: list, sizes: tuple, main=None) -> list:
    """[(Kante, Maß Anfang, Maß Ende, zum bearbeiteten Satz?)] für die Auswahlsätze einer Rundung bzw. Fase.

    Jeder Satz sucht seine Kanten in seiner eigenen Merkpunktliste (das Nachführen verschobener Kanten schreibt
    dorthin zurück). Eine Kante in mehreren Sätzen zählt nur im ersten. Ohne jede Auswahl (ältere Entwürfe)
    gelten die `fallback`-Kanten (alle scharfen) mit den Maßen `sizes`. `main` ist die Merkpunktliste des gerade
    bearbeiteten Satzes (nur für ihn gelten Sehnenlänge und Zwischenpunkt)."""
    if not any(group[-1] for group in groups):
        return [(edge, *sizes, True) for edge in fallback]
    edges = unique_edges(body)
    plan = []
    for *measures, points in groups:
        if not points:
            continue
        start, end = (measures + [0.0])[:2]
        for edge in find_by_points(edges, edge_point, points, "Kante"):
            if not any(edge.IsSame(other) for other, *_rest in plan):
                plan.append((edge, start, end, points is main))
    return plan


def _fillet_all(body: TopoDS_Shape, edges: list, radius: float) -> TopoDS_Shape:
    """Rundet alle `edges` mit demselben Radius in einem Schritt."""
    maker = BRepFilletAPI_MakeFillet(body)
    for edge in edges:
        maker.Add(radius, TopoDS.Edge(edge))
    return _finish(maker, "Verrundung", "Radius")


def _face_normal_at(face, point: gp_Pnt) -> gp_Vec | None:
    """Nach außen zeigende Normale der Fläche am (nächsten) Punkt; None, wenn sie dort nicht bestimmbar ist."""
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.TopAbs import TopAbs_REVERSED
    face = TopoDS.Face(face)
    projector = GeomAPI_ProjectPointOnSurf(point, BRep_Tool.Surface_s(face))
    if projector.NbPoints() == 0:
        return None
    u, v = projector.LowerDistanceParameters()
    props = BRepLProp_SLProps(BRepAdaptor_Surface(face), u, v, 1, 1e-7)
    if not props.IsNormalDefined():
        return None
    normal = gp_Vec(props.Normal().XYZ())
    if face.Orientation() == TopAbs_REVERSED:
        normal.Reverse()
    return normal


def _edge_frame(body: TopoDS_Shape, edge):
    """(Mittelpunkt, Tangente, Fläche A, Fläche B, Normale A, Normale B) einer Kante mit zwei Nachbarflächen."""
    neighbours = IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapesAndAncestors_s(body, TopAbs_EDGE, TopAbs_FACE, neighbours)
    if not neighbours.Contains(edge):
        return None
    faces = list(neighbours.FindFromKey(edge))
    if len(faces) != 2:
        return None
    curve = BRepAdaptor_Curve(TopoDS.Edge(edge))
    middle, tangent = gp_Pnt(), gp_Vec()
    curve.D1((curve.FirstParameter() + curve.LastParameter()) / 2, middle, tangent)
    normals = [_face_normal_at(face, middle) for face in faces]
    if tangent.Magnitude() < 1e-12 or None in normals:
        return None
    return middle, tangent.Normalized(), faces[0], faces[1], normals[0], normals[1]


def _edge_is_concave(body: TopoDS_Shape, edge) -> bool:
    """Innenkante (Kehle, der Körper umschließt sie) statt Außenkante (Rundung)?

    In Fläche A, senkrecht zur Kante und in die Fläche hinein, zeigt die Richtung `across`. Zeigt die Normale der
    Fläche B dorthin, knickt der Körper nach innen (Kehle), sonst nach außen."""
    frame = _edge_frame(body, edge)
    if frame is None:
        return False
    middle, tangent, face_a, _face_b, normal_a, normal_b = frame
    across = normal_a.Crossed(tangent)
    # die Richtung zeigen lassen, in der Fläche A tatsächlich weitergeht
    step = max(1e-4, 1e-3 * _shape_size(body))
    probe = middle.Translated(across.Normalized() * step)
    classifier = BRepClass_FaceClassifier()
    projector = _project_on_face(TopoDS.Face(face_a), probe)
    if projector is not None:
        classifier.Perform(TopoDS.Face(face_a), projector, 1e-6)
        if classifier.State() != TopAbs_IN:
            across.Reverse()
    return across.Dot(normal_b) > 1e-9


def _project_on_face(face, point: gp_Pnt):
    """Flächenparameter (gp_Pnt2d) des Punkts auf der Fläche, None, wenn die Projektion scheitert."""
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    projector = GeomAPI_ProjectPointOnSurf(point, BRep_Tool.Surface_s(face))
    if projector.NbPoints() == 0:
        return None
    u, v = projector.LowerDistanceParameters()
    return gp_Pnt2d(u, v)


def _shape_size(shape: TopoDS_Shape) -> float:
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    low, high = box.CornerMin(), box.CornerMax()
    return low.Distance(high)


def _radius_from_chord(body: TopoDS_Shape, edge, chord: float) -> float:
    """Radius zu einer Sehnenlänge (Fusion „Sehnenlänge“): Der Bogen spannt den Winkel θ zwischen den Normalen der
    beiden Nachbarflächen, die Sehne ist 2·r·sin(θ/2). Ohne bestimmbaren Winkel wie bei 90° (r = Sehne / √2)."""
    frame = _edge_frame(body, edge)
    angle = math.pi / 2
    if frame is not None:
        angle = frame[4].Angle(frame[5])
    if angle < 1e-3:
        raise FeatureError("Sehnenlänge: die Flächen an dieser Kante gehen tangential ineinander über")
    return chord / (2 * math.sin(angle / 2))


def _full_round_sides(body: TopoDS_Shape, centre) -> tuple[list, float] | None:
    """Die Kanten zwischen der Mittelfläche und zwei parallelen, ebenen Nachbarflächen (Seiten einer Rippe) und
    der Abstand der Seiten; bei mehreren Paaren das mit dem kleinsten Abstand. None, wenn es keines gibt."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    neighbours = IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapesAndAncestors_s(body, TopAbs_EDGE, TopAbs_FACE, neighbours)
    sides = []          # (Kante, Nachbarfläche, Ebene)
    for edge in unique_edges(centre):
        if not neighbours.Contains(edge):
            continue
        for face in neighbours.FindFromKey(edge):
            if face.IsSame(centre):
                continue
            surface = BRepAdaptor_Surface(TopoDS.Face(face))
            if surface.GetType() == GeomAbs_Plane:
                sides.append((edge, face, surface.Plane()))
    best = None
    for first in range(len(sides)):
        for second in range(first + 1, len(sides)):
            (edge_a, _fa, plane_a), (edge_b, _fb, plane_b) = sides[first], sides[second]
            if not plane_a.Axis().Direction().IsParallel(plane_b.Axis().Direction(), 1e-6):
                continue
            gap = plane_a.Distance(plane_b.Location())
            if gap > 1e-6 and (best is None or gap < best[1]):
                best = ([edge_a, edge_b], gap)
    return best


@dataclass
class Chamfer(Feature):
    """Versieht die gewählten Kanten mit einer Fase fester Breite; ohne Auswahl alle
    scharfen Kanten der gewählten Körper."""

    distance: float = 1.0
    body: str = ALL_BODIES
    edge_points: list = field(default_factory=list)     # Merkpunkte der gewählten Kanten
    # Feldname nicht „kind“: so heißt beim Speichern schon die Art des Features
    chamfer_type: str = CHAMFER_KINDS[0]  # wie in Fusion: gleicher Abstand, zwei Abstände oder Abstand und Winkel
    distance2: float = 1.0              # zweiter Abstand (nur „Zwei Abstände“)
    angle: float = 45.0                 # Winkel in Grad (nur „Abstand und Winkel“)
    # wie Fusion „Richtung umkehren“: der erste Abstand bzw. der Winkel gilt auf der anderen
    # Nachbarfläche der Kante. Nicht gleichwertig zum Tauschen der Abstände – der Geometriekern
    # schafft manche ungleiche Fase nur mit einer bestimmten Bezugsfläche.
    flip: bool = False
    require_pick: bool = False          # wie bei der Verrundung: ohne gewählte Kanten geschieht nichts
    # weitere Auswahlsätze wie das „+“ in Fusion (Runde 91): je Eintrag {"distance": mm, "edge_points": [...]};
    # zweiter Abstand und Winkel gelten für alle Sätze
    sets: list = field(default_factory=list)

    KIND = "Fase"

    # Reihenfolge im Eigenschaftenfeld wie in Fusion: Art, Auswahl, Sätze, Maße
    _ORDER = ("chamfer_type", "body", "edge_points", "sets", "distance", "distance2", "angle", "flip")

    def parameters(self) -> list:
        items = [item for item in super().parameters() if item.name != "require_pick"]
        rank = {name: number for number, name in enumerate(self._ORDER)}
        return sorted(items, key=lambda item: rank.get(item.name, len(rank)))

    def apply(self, state: BuildState) -> None:
        finished_sets = any(entry.get("edge_points") for entry in self.sets)
        if self.distance <= 0 and (self.edge_points or not finished_sets):
            raise FeatureError("Der Abstand muss größer als 0 sein")
        # der gerade bearbeitete Satz zählt erst, wenn er Kanten hat (sonst steht er nach „+“ leer bei 0)
        groups = [(self.distance, self.edge_points)] if self.edge_points or not finished_sets else []
        # die Listen selbst (keine Kopien): das Nachführen verschobener Kanten schreibt dorthin zurück
        groups += [(float(entry.get("distance", self.distance)), entry["edge_points"])
                   for entry in self.sets if entry.get("edge_points")]
        points = [point for _distance, group in groups for point in group]
        sizes = [distance for distance, _group in groups]
        if any(distance <= 0 for distance, _group in groups):
            raise FeatureError("Der Abstand jedes Auswahlsatzes muss größer als 0 sein")
        if self.require_pick and not points:
            raise FeatureError(PICK_EDGES_MESSAGE)
        if self.chamfer_type not in CHAMFER_KINDS:
            raise FeatureError(f"Unbekannte Art der Fase '{self.chamfer_type}'")
        if self.chamfer_type == CHAMFER_KINDS[1] and self.distance2 <= 0:
            raise FeatureError("Der zweite Abstand muss größer als 0 sein")
        if self.chamfer_type == CHAMFER_KINDS[2] and not 0 < self.angle < 90:
            raise FeatureError("Der Winkel muss zwischen 0 und 90 Grad liegen")

        def chamfer(body: TopoDS_Shape, edges: list, scale: float = 1.0) -> TopoDS_Shape:
            maker = BRepFilletAPI_MakeChamfer(body)
            neighbours = IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
            TopExp.MapShapesAndAncestors_s(body, TopAbs_EDGE, TopAbs_FACE, neighbours)
            for edge, size, _end, _main in _edge_plan(body, groups, edges, (self.distance, 0.0)):
                if self.chamfer_type == CHAMFER_KINDS[0]:
                    maker.Add(size * scale, edge)
                    continue
                # ungleiche Fase: der erste Abstand gilt auf der ersten Nachbarfläche der Kante,
                # mit „Richtung umkehren“ auf der anderen
                faces = neighbours.FindFromKey(edge)
                face = TopoDS.Face(faces.Last() if self.flip else faces.First())
                if self.chamfer_type == CHAMFER_KINDS[1]:
                    maker.Add(size * scale, self.distance2 * scale, TopoDS.Edge(edge), face)
                else:
                    maker.AddDA(size * scale, math.radians(self.angle), TopoDS.Edge(edge), face)
            return _finish(maker, "Fase", "Abstand")

        _modify_edges(state, _with_full_round(chamfer, max([self.distance2, *sizes, self.distance])), self.body,
                      points)


PARALLEL_BOOLEAN_MIN = 4           # ab so vielen auszuschneidenden Körpern wird verteilt gerechnet
PARALLEL_TOUCH_MIN = 8             # ab so vielen möglicherweise berührten Körpern wird die Berührung verteilt geprüft
_FULL_ROUND_SCALE = 1.0 - 1e-6     # so viel kleiner versucht es der Rückfall für volle Rundungen/Fasen


def _edge_corner(body: TopoDS_Shape, edge) -> tuple | None:
    """Für eine gerade Kante zwischen zwei ebenen Flächen: (Punkt, Richtung, [Normale A, Normale B],
    [Punkt auf A, Punkt auf B], hohl). „Hohl“ heißt Innenecke (der Körper umschließt sie); None bei anderen Kanten."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Line, GeomAbs_Plane
    curve = BRepAdaptor_Curve(TopoDS.Edge(edge))
    if curve.GetType() != GeomAbs_Line:
        return None
    neighbours = IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapesAndAncestors_s(body, TopAbs_EDGE, TopAbs_FACE, neighbours)
    if not neighbours.Contains(edge):
        return None
    faces = list(neighbours.FindFromKey(edge))
    if len(faces) != 2 or any(BRepAdaptor_Surface(TopoDS.Face(f)).GetType() != GeomAbs_Plane for f in faces):
        return None
    point = gp_Pnt(*edge_point(edge))
    direction = gp_Vec(curve.Line().Direction().XYZ())
    normals = [gp_Vec(*_plane_normal(face)) for face in faces]
    places = [gp_Pnt(*face_point(face)) for face in faces]
    # Richtung von der Kante in Fläche A hinein (senkrecht zur Kante); zeigt sie gegen die Normale von B, ist die
    # Ecke erhaben (außen), sonst hohl (innen)
    into = gp_Vec(point, places[0])
    into = into - direction * into.Dot(direction)
    if into.Magnitude() < 1e-12:
        return None
    hollow = into.Dot(normals[1]) > 1e-9
    return point, direction, normals, places, hollow


def wall_outer_extra(body: TopoDS_Shape, edges: list) -> dict:
    """Zuschläge für Außenkanten gleich starker Wände: Kantennummer -> Wandstärke. Ein Paar bilden eine hohle
    (innere) und eine erhabene (äußere) gerade Kante, parallel, deren Flächen jeweils parallel mit entgegengesetzter
    Normale liegen und beidseits gleich weit entfernt sind (die Wandstärke)."""
    corners = {number: _edge_corner(body, edge) for number, edge in enumerate(edges)}
    corners = {number: corner for number, corner in corners.items() if corner is not None}
    extra = {}
    for inner_number, inner in corners.items():
        if not inner[4]:
            continue
        for outer_number, outer in corners.items():
            if outer[4] or outer_number in extra or not inner[1].IsParallel(outer[1], 1e-6):
                continue
            thickness = []
            for normal, place in zip(inner[2], inner[3]):
                # die Außenfläche derselben Wand: Normale entgegengesetzt
                match = [(o_place, o_normal) for o_normal, o_place in zip(outer[2], outer[3])
                         if o_normal.IsOpposite(normal, 1e-6)]
                if not match:
                    break
                o_place, o_normal = match[0]
                thickness.append(abs(gp_Vec(place, o_place).Dot(normal)))
            if len(thickness) == 2 and thickness[0] > 1e-9 and abs(thickness[0] - thickness[1]) < 1e-6:
                extra[outer_number] = thickness[0]
                break
    return extra
def _through_all(state: BuildState, profiles: list) -> float:
    """Länge für „Durch alles“: weiter als alle Körper und die Profile voneinander entfernt sind."""
    box = Bnd_Box()
    for body in state.bodies.values():
        BRepBndLib.Add_s(body, box)
    for profile, _normal in profiles:
        BRepBndLib.Add_s(profile, box)
    if box.IsVoid():
        return 100.0
    low, high = box.CornerMin(), box.CornerMax()
    return 2.0 * low.Distance(high) + 10.0


def _with_full_round(build, size: float):
    """Verrundung/Fase bis an die Grenze, wie in Fusion: eine Rundung mit dem halben Abstand zweier paralleler
    Flächen (z. B. Quader 20 × 20 mit Radius 10 → Zylinder) lehnt der Geometriekern ab, weil die Fläche dazwischen
    ganz verschwinden würde. Dann wird mit einem um ein Millionstel kleineren Maß gerechnet und die übrig gebliebenen
    hauchdünnen Streifen werden entfernt. Was wirklich zu groß ist, scheitert weiterhin mit der gewohnten Meldung."""
    def attempt(body: TopoDS_Shape, edges: list) -> TopoDS_Shape:
        try:
            return build(body, edges)
        except FeatureError as error:
            try:
                nearly = build(body, edges, _FULL_ROUND_SCALE)
            except FeatureError:
                raise error from None
            return _without_slivers(nearly, max(size * (1.0 - _FULL_ROUND_SCALE) * 4.0, 1e-6), size)
    return attempt


def _without_slivers(shape: TopoDS_Shape, width: float, full_radius: float = 0.0) -> TopoDS_Shape:
    """Entfernt Flächen, die schmaler als `width` sind, und fasst gleichartige Nachbarflächen zusammen; bleibt der
    Körper dabei nicht gültig, kommt er unverändert zurück. `full_radius`: das eigentlich gewünschte Maß der vollen
    Rundung (siehe `_merge_split_cylinders`)."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.ShapeFix import ShapeFix_FixSmallFace
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    try:
        fixer = ShapeFix_FixSmallFace()
        fixer.Init(shape)
        fixer.SetPrecision(width)
        fixer.Perform()
        unify = ShapeUpgrade_UnifySameDomain(fixer.FixShape(), True, True, False)
        unify.Build()
        cleaned = unify.Shape()
        if BRepCheck_Analyzer(cleaned).IsValid() and TopExp_Explorer(cleaned, TopAbs_SOLID).More():
            # Zylinderstücke zusammenfassen; ein Durchgang schafft nicht immer alle Gruppen, deshalb wiederholen,
            # solange es weniger Flächen werden (höchstens viermal)
            for _attempt in range(4):
                merged = _merge_split_cylinders(cleaned, width, full_radius)
                if len(faces_of(merged)) >= len(faces_of(cleaned)):
                    break
                cleaned = merged
            return cleaned
    except Exception:
        pass
    return shape


def _merge_split_cylinders(shape: TopoDS_Shape, width: float, full_radius: float = 0.0) -> TopoDS_Shape:
    """Fasst Zylinderstücke zusammen, die nur um Rechenungenauigkeit gegeneinander versetzt sind.

    Beim Rückfall der vollen Rundung (Maß um ein Millionstel kleiner) liegen die Achsen der einzelnen Rundungen
    um wenige Hunderttausendstel auseinander. Der Zusammenfasser des Kerns vereint aber nur exakt gleiche Flächen –
    aus dem Quader mit vier gerundeten Kanten würden so vier Mantelstücke statt eines Zylinders wie in Fusion.
    Deshalb bekommen die Stücke einer Gruppe eine gemeinsame, gemittelte Zylinderfläche (umgerechneter Winkel- und
    Höhenbereich), werden neu vernäht und dann zusammengefasst. Klappt das nicht sauber oder ändert sich das
    Volumen merklich, bleibt der Körper unverändert. Passt der Radius einer Gruppe zum verkleinerten Maß der vollen
    Rundung (`full_radius` × Rückfall-Faktor), bekommt der gemeinsame Zylinder wieder genau `full_radius`."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepTools import BRepTools
    from OCP.Geom import Geom_CylindricalSurface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.gp import gp_Ax1, gp_Ax3
    from OCP.ShapeFix import ShapeFix_Solid
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    from OCP.TopAbs import TopAbs_SHELL
    import numpy as np

    vector = lambda d: np.array([d.X(), d.Y(), d.Z()])
    tolerance = max(width * 4.0, 1e-6)
    faces = faces_of(shape)
    # Gruppen gleich großer Zylinder mit (fast) gleicher Achse bilden
    groups: list[list[tuple[int, object]]] = []
    for index, face in enumerate(faces):
        adaptor = BRepAdaptor_Surface(face)
        if adaptor.GetType() != GeomAbs_Cylinder:
            continue
        cylinder = adaptor.Cylinder()
        axis, place = vector(cylinder.Axis().Direction()), vector(cylinder.Location())
        for group in groups:
            first = group[0][1]
            first_axis, first_place = vector(first.Axis().Direction()), vector(first.Location())
            apart = place - first_place
            apart -= (apart @ first_axis) * first_axis           # Abstand quer zur Achse
            if abs(abs(axis @ first_axis) - 1.0) < 1e-9 and abs(cylinder.Radius() - first.Radius()) < tolerance \
                    and np.linalg.norm(apart) < tolerance:
                group.append((index, cylinder))
                break
        else:
            groups.append([(index, cylinder)])
    groups = [group for group in groups if len(group) > 1]
    if not groups:
        return shape
    try:
        replaced = {}
        cylinders = []                                   # (Achspunkt, Achsrichtung, Radius, Lage) je Gruppe
        for group in groups:
            first = group[0][1].Position()
            axis = vector(first.Direction())
            # gemeinsame Achse: mittlere Lage quer zur Achse, gemeinsamer Radius
            base = vector(first.Location())
            centre = np.mean([base + ((vector(c.Location()) - base) - ((vector(c.Location()) - base) @ axis) * axis)
                              for _index, c in group], axis=0)
            common = gp_Ax3(gp_Pnt(*centre), first.Direction(), first.XDirection())
            radius = float(np.mean([c.Radius() for _index, c in group]))
            if full_radius > 0 and abs(radius - full_radius * _FULL_ROUND_SCALE) < tolerance:
                radius = full_radius                             # das Maß, das der Nutzer eingegeben hat
            surface = Geom_CylindricalSurface(common, radius)
            x_axis, y_axis = vector(common.XDirection()), vector(common.YDirection())
            pieces = []                                          # (Index, Winkelbereich, Höhenbereich) je Stück
            for index, cylinder in group:
                own = cylinder.Position()
                u0, u1, v0, v1 = BRepTools.UVBounds_s(faces[index])
                # Winkel läuft gleich herum, wenn Achsrichtung und Händigkeit beide gleich oder beide verschieden sind
                same_axis = vector(own.Direction()) @ axis > 0
                sign = 1.0 if same_axis == (own.Direct() == common.Direct()) else -1.0
                shift = math.atan2(vector(own.XDirection()) @ y_axis, vector(own.XDirection()) @ x_axis)
                start, end = sorted((shift + sign * u0, shift + sign * u1))
                height = float((vector(own.Location()) - centre) @ axis)
                low, high = (height + v0, height + v1) if same_axis else (height - v1, height - v0)
                pieces.append((index, start % (2.0 * math.pi), end - start, low, high))
            # Die Naht des gemeinsamen Zylinders (Winkel 0) in die größte Lücke zwischen den Stücken legen –
            # über die Naht hinweg fasst der Kern Flächen nicht zusammen.
            ordered = sorted(pieces, key=lambda piece: piece[1])
            gaps = [((ordered[(k + 1) % len(ordered)][1] - (piece[1] + piece[2])) % (2.0 * math.pi), piece[1] + piece[2])
                    for k, piece in enumerate(ordered)]
            # (eine winzige Überlappung zweier Stücke ergibt modulo 2π fast 2π – das ist keine Lücke)
            gaps = [(0.0 if gap > 2.0 * math.pi - 1e-4 else gap, start) for gap, start in gaps]
            gap, gap_start = max(gaps)
            seam = gap_start + gap / 2.0 if 1e-4 < gap < 2.0 * math.pi - 1e-4 else ordered[0][1]
            common.Rotate(gp_Ax1(gp_Pnt(*centre), first.Direction()), seam if common.Direct() else -seam)
            surface = Geom_CylindricalSurface(common, surface.Radius())
            shifted = []
            for index, start, span, low, high in pieces:
                start = (start - seam) % (2.0 * math.pi)
                if start > 2.0 * math.pi - 1e-6:                 # Rundungsrest knapp unter 2π: an den Anfang
                    start = 0.0
                shifted.append((start, span, low, high, index))
            shifted.sort()
            # Schließen die Stücke lückenlos aneinander und sind gleich hoch, wird daraus gleich EINE Fläche (bei
            # vollem Umlauf der ganze Mantel) – mit durchgehenden Randkreisen wie ein echter Zylinder
            joined = all(abs(shifted[k + 1][0] - (shifted[k][0] + shifted[k][1])) < 1e-5
                         and abs(shifted[k + 1][2] - shifted[k][2]) < tolerance
                         and abs(shifted[k + 1][3] - shifted[k][3]) < tolerance for k in range(len(shifted) - 1))
            if joined:
                start, end = shifted[0][0], shifted[-1][0] + shifted[-1][1]
                if end - start > 2.0 * math.pi - 1e-5:
                    start, end = 0.0, 2.0 * math.pi
                low, high = shifted[0][2], shifted[0][3]
                replaced[shifted[0][4]] = BRepBuilderAPI_MakeFace(surface, start, end, low, high, 1e-7).Face()
                for _start, _span, _low, _high, index in shifted[1:]:
                    replaced[index] = None                       # in der gemeinsamen Fläche aufgegangen
            else:
                for start, span, low, high, index in shifted:
                    replaced[index] = BRepBuilderAPI_MakeFace(surface, start, start + span, low, high, 1e-7).Face()
            cylinders.append((centre, axis, surface.Radius(), common))
        # Ebene Deckel quer zur Achse (z. B. Ober- und Unterseite des gerundeten Quaders) neu begrenzen: ihre Ränder
        # sind noch die alten, leicht versetzten Bögen – passend zum exakten Zylinder werden es exakte Kreisbögen
        # (sonst stürzte z. B. die Schale nach innen am Ergebnis ab)
        for index, face in enumerate(faces):
            if index not in replaced:
                rebuilt = _with_exact_rims(face, cylinders, tolerance)
                if rebuilt is not None:
                    replaced[index] = rebuilt
        sewing = BRepBuilderAPI_Sewing(tolerance)
        # die neuen, exakten Zylinderstücke zuerst: ihre Kanten sollen beim Vernähen bevorzugt werden
        for index in sorted(replaced):
            if replaced[index] is not None:
                sewing.Add(replaced[index])
        for index, face in enumerate(faces):
            if index not in replaced:
                sewing.Add(face)
        sewing.Perform()
        explorer = TopExp_Explorer(sewing.SewedShape(), TopAbs_SHELL)
        if not explorer.More():
            return shape
        shell = TopoDS.Shell(explorer.Current())
        explorer.Next()
        if explorer.More():                                      # mehr als eine Hülle: lieber nichts ändern
            return shape
        fixer = ShapeFix_Solid(BRepBuilderAPI_MakeSolid(shell).Solid())
        fixer.Perform()
        unify = ShapeUpgrade_UnifySameDomain(fixer.Solid(), True, True, False)
        unify.Build()
        merged = unify.Shape()
        before, after = _shape_volume(shape), _shape_volume(merged)
        if BRepCheck_Analyzer(merged).IsValid() and TopExp_Explorer(merged, TopAbs_SOLID).More() \
                and after > 0 and abs(after - before) <= 1e-5 * abs(before) + 1e-6 \
                and len(faces_of(merged)) < len(faces):
            return merged
    except Exception:
        pass
    return shape


def _with_exact_rims(face, cylinders: list, tolerance: float):
    """Eine ebene Fläche quer zu einer der Zylinderachsen, deren Randbögen auf diesem Zylinder liegen, neu
    gebaut: diese Bögen werden exakte Kreisbögen (Ebene ∩ Zylinder) zwischen ihren alten Endpunkten, die übrigen
    Randkanten bleiben; Lücken von Rechenungenauigkeit schließt die Drahtreparatur. None, wenn nichts zu tun ist
    oder der Neubau nicht gelingt."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
    from OCP.ElCLib import ElCLib
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Plane
    from OCP.gp import gp_Ax2, gp_Circ
    from OCP.ShapeExtend import ShapeExtend_WireData
    from OCP.ShapeFix import ShapeFix_Wire
    from OCP.TopAbs import TopAbs_REVERSED
    import numpy as np

    adaptor = BRepAdaptor_Surface(face)
    if adaptor.GetType() != GeomAbs_Plane:
        return None
    plane = adaptor.Plane()
    normal = np.array([plane.Axis().Direction().X(), plane.Axis().Direction().Y(), plane.Axis().Direction().Z()])
    origin = np.array([plane.Location().X(), plane.Location().Y(), plane.Location().Z()])
    point = lambda p: np.array([p.X(), p.Y(), p.Z()])
    # nur Zylinder, deren Achse senkrecht auf der Ebene steht
    across = [(centre, axis, radius, common) for centre, axis, radius, common in cylinders
              if abs(abs(axis @ normal) - 1.0) < 1e-9]
    if not across:
        return None

    circles: dict[int, object] = {}

    def exact_arc(edge):
        """Der exakte Kreisbogen für einen alten Randbogen auf einem der Zylinder als (Kreis, Anfangswinkel,
        Spanne, umgekehrt durchlaufen) – sonst None."""
        curve = BRepAdaptor_Curve(edge)
        if curve.GetType() != GeomAbs_Circle:
            return None
        middle = point(curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2.0))
        for centre, axis, radius, common in across:
            foot = centre + ((middle - centre) @ axis) * axis
            if abs(np.linalg.norm(middle - foot) - radius) > tolerance:
                continue
            centre_here = centre + ((origin - centre) @ axis) * axis          # Kreismitte in dieser Ebene
            # derselbe Kreis für alle Bögen auf diesem Zylinder (zum Zusammenfassen über `is` erkennbar)
            key = circles.setdefault(id(common), gp_Circ(gp_Ax2(gp_Pnt(*centre_here), common.Direction(),
                                                                 common.XDirection()), radius))
            circle = key
            first, last = TopExp.FirstVertex_s(edge, True), TopExp.LastVertex_s(edge, True)
            u_first = ElCLib.Parameter_s(circle, BRep_Tool.Pnt_s(first))
            u_last = ElCLib.Parameter_s(circle, BRep_Tool.Pnt_s(last))
            u_middle = ElCLib.Parameter_s(circle, gp_Pnt(*middle))
            if first.IsSame(last) or abs(u_first - u_last) < 1e-9:
                return (key, 0.0, 2.0 * math.pi, False)                      # ganzer Kreis
            # gegen den Uhrzeiger von Anfang zu Ende – liegt die alte Mitte nicht dazwischen, andersherum
            span = (u_last - u_first) % (2.0 * math.pi)
            if (u_middle - u_first) % (2.0 * math.pi) <= span:
                return (key, u_first, span, False)
            return (key, u_last, 2.0 * math.pi - span, True)
        return None

    changed = False
    wires = []
    explorer = TopExp_Explorer(face, TopAbs_WIRE)
    while explorer.More():
        wire = TopoDS.Wire(explorer.Current())
        explorer.Next()
        # Randkanten der Reihe nach; aufeinanderfolgende Bögen auf demselben Kreis werden ein Bogen (bzw. ein
        # ganzer Kreis), damit sie zu den durchgehenden Rändern der zusammengefassten Zylinderfläche passen
        runs = []                       # je Eintrag: alte Kante oder [Kreis, Anfangswinkel, Spanne, umgekehrt]
        walk = BRepTools_WireExplorer(wire, face)
        while walk.More():
            edge = walk.Current()
            walk.Next()
            arc = exact_arc(edge)
            if arc is None:
                runs.append(edge)
                continue
            changed = True
            previous = runs[-1] if runs and isinstance(runs[-1], list) else None
            if previous is not None and previous[0] is arc[0] and previous[3] == arc[3] and (
                    abs((previous[1] + previous[2]) % (2.0 * math.pi) - arc[1] % (2.0 * math.pi)) < 1e-5
                    or abs((arc[1] + arc[2]) % (2.0 * math.pi) - previous[1] % (2.0 * math.pi)) < 1e-5):
                if abs((previous[1] + previous[2]) % (2.0 * math.pi) - arc[1] % (2.0 * math.pi)) >= 1e-5:
                    previous[1] = arc[1]                         # (umgekehrt durchlaufen: der neue Bogen liegt davor)
                previous[2] += arc[2]
            else:
                runs.append(list(arc))
        # ein Lauf über den Anfang des Drahts hinweg: letzten und ersten Bogen zusammenfassen
        if len(runs) > 1 and all(isinstance(run, list) for run in (runs[0], runs[-1])) \
                and runs[0][0] is runs[-1][0] and runs[0][3] == runs[-1][3]:
            first, last = runs[0], runs[-1]
            if abs((last[1] + last[2]) % (2.0 * math.pi) - first[1] % (2.0 * math.pi)) < 1e-5:
                first[1], first[2] = last[1], first[2] + last[2]
                runs.pop()
            elif abs((first[1] + first[2]) % (2.0 * math.pi) - last[1] % (2.0 * math.pi)) < 1e-5:
                first[2] += last[2]
                runs.pop()
        data = ShapeExtend_WireData()
        for run in runs:
            if not isinstance(run, list):
                data.Add(run)
                continue
            circle, start, span, reverse = run
            if span > 2.0 * math.pi - 1e-5:
                edge = BRepBuilderAPI_MakeEdge(circle).Edge()                  # ganzer Kreis
            else:
                edge = BRepBuilderAPI_MakeEdge(circle, start, start + span).Edge()
            data.Add(TopoDS.Edge(edge.Reversed()) if reverse else edge)
        fixer = ShapeFix_Wire()
        fixer.Load(data)
        fixer.SetPrecision(tolerance)
        fixer.SetMaxTolerance(tolerance * 10.0)
        fixer.FixReorder()
        fixer.FixConnected(tolerance)
        wires.append((wire.IsSame(BRepTools.OuterWire_s(face)), fixer.Wire()))
    if not changed:
        return None
    outer = [wire for is_outer, wire in wires if is_outer]
    if len(outer) != 1:
        return None
    maker = BRepBuilderAPI_MakeFace(plane, outer[0], True)
    for is_outer, wire in wires:
        if not is_outer:
            maker.Add(wire)
    if not maker.IsDone():
        return None
    rebuilt = maker.Face()
    from OCP.ShapeFix import ShapeFix_Face
    repair = ShapeFix_Face(rebuilt)
    repair.SetPrecision(tolerance)
    repair.Perform()
    rebuilt = repair.Face()
    if face.Orientation() == TopAbs_REVERSED:
        rebuilt.Reverse()
    return rebuilt


def _shape_volume(shape: TopoDS_Shape) -> float:
    """Volumen eines Körpers (für Plausibilitätsprüfungen)."""
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


def _finish(maker, label: str, parameter: str) -> TopoDS_Shape:
    """Führt Verrundung/Fase aus und übersetzt Kernfehler in eine klare Meldung."""
    try:
        maker.Build()
        if maker.IsDone():
            return maker.Shape()
    except Exception:
        pass
    raise FeatureError(f"{label} nicht möglich – {parameter} ist für die Geometrie zu groß")


@dataclass
class ImportedReference(Feature):
    """Aus einer .f3d-Datei übernommene Körper (ohne deren Konstruktionsverlauf)."""

    source: str = ""     # Pfad der Ursprungsdatei
    blob: str = ""       # Pfad des Geometrie-Blobs innerhalb des Archivs
    content: str = IMPORT_CONTENTS[0]
    up_to_step: float = 0.0     # nur Körper bis zu diesem Schritt des Fusion-Verlaufs (0 = alle)

    KIND = "Import"

    def apply(self, state: BuildState) -> None:
        if self.content == IMPORT_CONTENTS[2]:
            load_import(self.source, self.blob)       # nur einlesen: die Endstände stehen danach bereit
            return
        state.references.extend(import_parts(self.source, self.blob, self.content, int(self.up_to_step)))


def import_parts(source: str, blob: str, content: str, up_to_step: int) -> list[TopoDS_Shape]:
    """Wählt aus einem eingelesenen Blob die anzuzeigenden Körper aus.

    Zurückgegeben werden die einzelnen Körper (immer dieselben Objekte), damit die
    Anzeige ihre Zerlegung in Dreiecke je Körper merken kann.

    "Aktuelles Modell" zeigt nur Körper mit Zeitstempel, also den Stand nach dem letzten
    Schritt, der den jeweiligen Körper verändert hat. Mit `up_to_step` erscheinen nur
    Körper, deren letzter Schritt höchstens diese Nummer hat. Fehlen Zeitstempel oder
    ihre Zuordnung, werden alle Körper gezeigt.
    """
    result = load_import(source, blob)
    stamped = [(shape, stamp) for shape, stamp in result.parts if stamp is not None]
    if content != IMPORT_CONTENTS[0] or not stamped:
        return [shape for shape, _stamp in result.parts]      # alles zeigen
    step_of = load_design(source).step_of_stamp() if up_to_step > 0 else {}
    # Körper, deren Schritt unbekannt ist, bleiben sichtbar
    return [shape for shape, stamp in stamped
            if up_to_step <= 0 or step_of.get(stamp, 0) <= up_to_step]


@dataclass
class ImportedSketches(Feature):
    """Alle Skizzen einer Fusion-Datei als Kurven in ihrer Lage im Raum (nur Anzeige)."""

    source: str = ""     # Pfad der Ursprungsdatei

    KIND = "Fusion-Skizzen"

    def apply(self, state: BuildState) -> None:
        state.sketch_curves.extend(load_sketch_curves(self.source))


@dataclass
class FusionExtrude(Feature):
    """Aus einer Fusion-Datei übernommene Extrusion: Profil und Richtung stammen aus der
    Datei, Abstand und Operation lassen sich hier ändern."""

    source: str = ""        # Pfad der Ursprungsdatei
    fusion_id: int = 0      # ID des Extrusions-Features in der Datei
    distance: float = 10.0
    operation: str = OPERATIONS[0]

    KIND = "Fusion-Extrusion"

    def apply(self, state: BuildState) -> None:
        from ..f3d import extrudes

        design = load_design(self.source)
        if not design.can_rebuild(self.fusion_id):
            raise FeatureError("Profil der Extrusion ist in der Datei nicht lesbar")
        if abs(self.distance) < 1e-9:
            raise FeatureError("Der Abstand darf nicht 0 sein")
        if not design.extrudes[self.fusion_id].profiles:
            self._extrude_faces(state, design)
            return
        # Der Profilaufbau ist aufwendig; das Volumen wird je Abstand nur einmal berechnet
        key = (self.source, self.fusion_id, self.distance)
        if key not in _SOLID_CACHE:
            extrude = design.extrudes[self.fusion_id]
            _SOLID_CACHE[key] = extrudes.solid(extrude, design.sketches[extrude.sketch_id], self.distance)
        solid = _SOLID_CACHE[key]
        try:
            if solid is None:
                raise FeatureError("Aus den Skizzenkurven ließ sich kein geschlossenes Profil bilden")
            _combine(state, solid, self.operation, origin=self.fusion_id,
                     participants=fusion_participants(self.source, self.fusion_id))
        except FeatureError:
            # Scheitert der Nachbau (z. B. weil ein früherer Schritt abweicht), trägt der gespeicherte Endstand die
            # Timeline weiter – sonst fehlten alle Körper dieses Schritts (Runde 82)
            if reconcile_step(state, self.source, self.fusion_id) == "ohne":
                raise
            return
        reconcile_step(state, self.source, self.fusion_id)

    def _extrude_faces(self, state: BuildState, design) -> None:
        """Runde 82: Profil sind Körperflächen (in Fusion eine Fläche statt eines Skizzenprofils gewählt). Die Flächen
        werden über Fusions Flächennamen im eigenen Körper wiedergefunden (wie bei „Fläche löschen“) und entlang der
        gespeicherten Profilnormale gezogen; danach Abgleich mit dem gespeicherten Endstand."""
        extrude = design.extrudes[self.fusion_id]
        named = stored_named_faces(self.source)
        try:
            if not named:
                raise FeatureError("Extrusion: die Geometrie der Datei ist nicht eingelesen (Flächen unbekannt)")
            builder, profile = BRep_Builder(), TopoDS_Compound()
            builder.MakeCompound(profile)
            count = 0
            for face_set in extrude.face_sets:
                creators = [design.bodies.creator_of.get(body) for body in face_set.bodies]
                for name in _bodies_from(state, creators, frozen_too=True):     # die Fläche wird nur gelesen
                    for face in faces_at_named(state.bodies[name], face_set.faces, named):
                        builder.Add(profile, face)
                        count += 1
            if not count:
                raise FeatureError("Extrusion: die gewählten Körperflächen sind im Modell nicht zu finden")
            solid = BRepPrimAPI_MakePrism(profile, gp_Vec(*extrude.normal) * self.distance).Shape()
            _combine(state, solid, self.operation, origin=self.fusion_id,
                     participants=fusion_participants(self.source, self.fusion_id))
        except FeatureError:
            # wie bei den Flächenschritten: mit gespeichertem Endstand geht es trotzdem weiter
            if reconcile_step(state, self.source, self.fusion_id) == "ohne":
                raise
            return
        reconcile_step(state, self.source, self.fusion_id)


@dataclass
class _FusionPrimitive(Feature):
    """Gemeinsame Basis der aus Fusion übernommenen Grundkörper: Die Lage stammt aus der
    Datei, Maße und Operation lassen sich hier ändern."""

    source: str = ""        # Pfad der Ursprungsdatei
    fusion_id: int = 0      # ID des Features in der Datei
    operation: str = OPERATIONS[0]

    PRIMITIVE = ""          # Art für primitives.solid

    def sizes(self) -> dict[str, float]:
        raise NotImplementedError

    def apply(self, state: BuildState) -> None:
        from ..f3d import primitives

        primitive = load_design(self.source).primitives.get(self.fusion_id)
        if primitive is None:
            raise FeatureError("Lage des Grundkörpers ist in der Datei nicht lesbar")
        solid = primitives.solid(self.PRIMITIVE, primitive.matrix, self.sizes())
        if solid is None:
            raise FeatureError("Die Maße ergeben kein Volumen")
        _combine(state, solid, self.operation, origin=self.fusion_id,
                 participants=fusion_participants(self.source, self.fusion_id))
        reconcile_step(state, self.source, self.fusion_id)


@dataclass
class FusionBox(_FusionPrimitive):
    length_x: float = 10.0
    length_y: float = 10.0
    height: float = 10.0
    offset_x: float = 0.0
    offset_y: float = 0.0

    KIND = "Fusion-Quader"
    PRIMITIVE = "box"

    def sizes(self) -> dict[str, float]:
        return {"x": self.length_x, "y": self.length_y, "z": self.height,
                "offset_x": self.offset_x, "offset_y": self.offset_y}


@dataclass
class FusionCylinder(_FusionPrimitive):
    diameter: float = 10.0
    height: float = 10.0

    KIND = "Fusion-Zylinder"
    PRIMITIVE = "cylinder"

    def sizes(self) -> dict[str, float]:
        return {"diameter": self.diameter, "height": self.height}


@dataclass
class FusionSphere(_FusionPrimitive):
    diameter: float = 10.0

    KIND = "Fusion-Kugel"
    PRIMITIVE = "sphere"

    def sizes(self) -> dict[str, float]:
        return {"diameter": self.diameter}


def move_bodies(state: BuildState, creators: list[int], rotation: tuple[float, ...],
                offset: tuple[float, float, float]) -> int:
    """Bewegt alle Körper, die einer der genannten Fusion-Schritte erzeugt hat.

    `rotation` sind die neun Werte der Drehmatrix (zeilenweise), `offset` die
    Verschiebung in mm. Gibt die Zahl der bewegten Körper zurück.
    """
    transform = gp_Trsf()
    transform.SetValues(rotation[0], rotation[1], rotation[2], offset[0],
                        rotation[3], rotation[4], rotation[5], offset[1],
                        rotation[6], rotation[7], rotation[8], offset[2])
    targets = _bodies_from(state, creators)
    for name in targets:
        state.bodies[name] = BRepBuilderAPI_Transform(state.bodies[name], transform, True).Shape()
    return len(targets)


def _bodies_from(state: BuildState, creators, frozen_too: bool = False) -> list[str]:
    """Namen der veränderbaren Körper, die einer der genannten Fusion-Schritte erzeugt hat. Mit `frozen_too` auch
    eingefrorene (gespeicherter Endstand) – wenn nur gelesen wird, z. B. die Profilfläche einer Extrusion."""
    return [name for name, origin in state.body_origin.items()
            if origin in creators and name in state.bodies and (frozen_too or name not in state.frozen)]


def stored_bodies(source: str) -> dict[int, list[TopoDS_Shape]]:
    """Gespeicherte Endstände je Fusion-Schritt: Feature-ID -> Körper, die es zuletzt verändert hat.

    Berücksichtigt nur Geometrie-Blobs dieser Datei, die bereits eingelesen wurden
    (das Einlesen großer Blobs dauert Minuten und wird hier nicht angestoßen).
    """
    loaded = [result for (path, _blob), result in _IMPORT_CACHE.items() if path == source]
    key = (source, len(loaded))
    if key not in _STORED_CACHE:
        feature_of = load_design(source).stamp_feature if loaded else {}
        grouped: dict[int, list[TopoDS_Shape]] = {}
        for result in loaded:
            for shape, stamp in result.parts:
                if stamp in feature_of:
                    grouped.setdefault(feature_of[stamp], []).append(shape)
        _STORED_CACHE.clear()
        _STORED_CACHE[key] = grouped
    return _STORED_CACHE[key]


def _measure(shape: TopoDS_Shape) -> tuple[float, tuple[float, float, float]]:
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    centre = properties.CentreOfMass()
    return abs(properties.Mass()), (centre.X(), centre.Y(), centre.Z())


def _same_bodies(first: list[TopoDS_Shape], second: list[TopoDS_Shape]) -> bool:
    """Stimmen die beiden Körpermengen überein? (Volumen auf 1 %, Schwerpunkt auf 0,1 mm)"""
    if len(first) != len(second):
        return False
    remaining = [_measure(shape) for shape in second]
    for volume, centre in map(_measure, first):
        match = next((item for item in remaining
                      if abs(item[0] - volume) <= 0.01 * max(volume, 1e-9)
                      and max(abs(a - b) for a, b in zip(item[1], centre)) < 0.1), None)
        if match is None:
            return False
        remaining.remove(match)
    return True


def count_matching_bodies(reference: list[TopoDS_Shape], ours: list[TopoDS_Shape]) -> int:
    """Wie viele der Bezugskörper haben unter `ours` eine Entsprechung (Volumen auf 1 %, Schwerpunkt auf 0,1 mm,
    wie beim Abgleich)? Jeder eigene Körper zählt höchstens einmal; nach Volumen sortiert, damit auch Tausende
    Körper schnell verglichen sind."""
    import bisect
    measured = sorted((volume, centre) for volume, centre in map(_measure, ours))
    volumes = [volume for volume, _centre in measured]
    used = [False] * len(measured)
    found = 0
    for volume, centre in map(_measure, reference):
        low = bisect.bisect_left(volumes, volume - 0.01 * max(volume, 1e-9))
        for index in range(low, len(measured)):
            if volumes[index] > volume + 0.01 * max(volume, 1e-9):
                break
            if not used[index] and max(abs(a - b) for a, b in zip(measured[index][1], centre)) < 0.1:
                used[index] = True
                found += 1
                break
    return found


_SHAPE_FACTS: dict[int, tuple] = {}     # id(Form) -> (Form, Hüllquader, (Volumen, Schwerpunkt)) – siehe _facts
_SHAPE_FACTS_LIMIT = 50000


def _facts(shape: TopoDS_Shape) -> tuple:
    """Hüllquader und (Volumen, Schwerpunkt) einer Form, gemerkt. Formen werden nie verändert, nur ersetzt; der
    Eintrag hält seine Form fest, damit die id eindeutig bleibt. Beim Abspielen großer Fusion-Dateien wurden
    sonst dieselben Körper tausendfach neu vermessen."""
    entry = _SHAPE_FACTS.get(id(shape))
    if entry is None or entry[0] is not shape:
        box = Bnd_Box()
        BRepBndLib.Add_s(shape, box)
        entry = (shape, box, _measure(shape))
        if len(_SHAPE_FACTS) >= _SHAPE_FACTS_LIMIT:
            _SHAPE_FACTS.clear()
        _SHAPE_FACTS[id(shape)] = entry
    return entry


def _has_twin(state: BuildState, shape: TopoDS_Shape) -> bool:
    """Steht schon ein deckungsgleicher Körper im Modell (Volumen auf 1 %, Schwerpunkt auf 0,1 mm)? Nur Körper,
    deren Hüllquader den der Form trifft, werden verglichen."""
    _shape, box, (volume, centre) = _facts(shape)
    for other in state.bodies.values():
        _other, other_box, (other_volume, other_centre) = _facts(other)
        if box.IsOut(other_box):
            continue
        if abs(other_volume - volume) <= 0.01 * max(volume, 1e-9) \
                and max(abs(a - b) for a, b in zip(other_centre, centre)) < 0.1:
            return True
    return False


def fusion_participants(source: str, fusion_id: int) -> set | None:
    """Die Körper (als Herkunft: erzeugender Fusion-Schritt), die ein übernommener Schritt bearbeitet – None, wenn die
    Datei keine nennt. Ohne diese Beschränkung schnitt eine nachgebaute Extrusion auch Körper, die sie in Fusion nicht
    betrifft (Runde 82: 13 Endkörper von „camera.f3d“ wichen dadurch ab)."""
    targets = load_design(source).targets.get(fusion_id)
    return set(targets) if targets else None


def reconcile_step(state: BuildState, source: str, fusion_id: int) -> str:
    """Gleicht das Ergebnis eines übernommenen Schritts mit seinem gespeicherten Endstand ab.

    Gibt zurück: "ohne" (kein gespeicherter Endstand bekannt), "bestätigt" (der Nachbau
    stimmt überein und bleibt bearbeitbar) oder "ersetzt" (der gespeicherte Endstand wurde
    eingesetzt und gegen spätere Schritte eingefroren).
    """
    design = load_design(source)
    if design.leaves_no_body(fusion_id):
        # Was dieser Schritt erzeugt, existiert im fertigen Modell nicht mehr
        state.helpers.update(name for name, origin in state.body_origin.items() if origin == fusion_id)
    stored = stored_bodies(source).get(fusion_id)
    if not stored:
        return "ohne"
    targets = design.targets.get(fusion_id, [])
    # Der Endstand enthält nur Körper, die nach diesem Schritt nicht mehr verändert wurden. Körper, die ein späterer
    # Schritt noch nennt, gehören nicht dazu: sie bleiben stehen und werden nicht verglichen (früher wurden sie beim
    # Ersetzen mit gelöscht – spätere Schritte fanden dann ihren Zielkörper nicht mehr)
    involved = _bodies_from(state, {fusion_id, *targets})
    if _same_bodies([state.bodies[name] for name in involved], stored):
        return "bestätigt"
    # jeder gespeicherte Körper steht schon deckungsgleich da (die übrigen beteiligten werden später noch verändert)
    if all(_has_twin(state, shape) for shape in stored):
        return "bestätigt"
    own = [name for name in involved
           if not (hasattr(design, "used_after") and design.used_after(state.body_origin.get(name), fusion_id))]
    for name in own:
        del state.bodies[name]
    # Der Körper behält die Herkunft des Zielkörpers, damit spätere Verweise ihn finden
    origin = targets[0] if targets else fusion_id
    for shape in stored:
        # steht schon ein deckungsgleicher Körper im Modell (z. B. ein stehen gebliebener, später noch benutzter
        # Körper), nicht doppelt einsetzen: Boolesche Rechnungen mit deckungsgleichen Körpern hängen im Kern
        if not _has_twin(state, shape):
            state.frozen.add(state.add_body(shape, origin))
    return "ersetzt"


@dataclass
class FusionStored(Feature):
    """Schritt einer Fusion-Datei, der sich nicht nachbauen lässt: setzt den gespeicherten
    Endstand der Körper ein, die dieser Schritt zuletzt verändert hat (nicht bearbeitbar)."""

    source: str = ""        # Pfad der Ursprungsdatei
    fusion_id: int = 0      # ID des Features in der Datei
    # Art des Schritts in Fusion (nur zur Anzeige); nicht „kind“ – so heißt beim Speichern die Art des Features
    # (früher hieß das Feld so, und gespeicherte Entwürfe ließen sich nicht mehr laden)
    fusion_kind: str = ""

    KIND = "Fusion-Endstand"

    def apply(self, state: BuildState) -> None:
        if reconcile_step(state, self.source, self.fusion_id) == "ohne":
            raise FeatureError("Zu diesem Schritt ist kein gespeicherter Körper eingelesen")


def combine_bodies(state: BuildState, target: int, tools: list[int], operation: str,
                   keep_tools: bool = False) -> None:
    """Verrechnet die Werkzeugkörper mit dem Zielkörper; die Werkzeugkörper entfallen, außer mit `keep_tools`
    (Fusions „Werkzeuge beibehalten“).

    `target` und `tools` sind IDs der Fusion-Schritte, die die Körper erzeugt haben.
    """
    operations = {"Vereinigen": BRepAlgoAPI_Fuse, "Ausschneiden": BRepAlgoAPI_Cut,
                  "Schnittmenge": BRepAlgoAPI_Common}
    if operation not in operations:
        raise FeatureError(f"Unbekannte Operation '{operation}'")
    target_names = _bodies_from(state, [target])
    tool_names = [name for name in _bodies_from(state, tools) if name not in target_names]
    if not target_names:
        raise FeatureError("Der Zielkörper ist nicht im Modell")
    if not tool_names:
        raise FeatureError("Die Werkzeugkörper sind nicht im Modell")
    name = target_names[0]
    result = state.bodies[name]
    for tool in tool_names:
        result = _boolean(operations[operation], result, state.bodies[tool])
    if not keep_tools:
        for tool in tool_names:
            del state.bodies[tool]
    if TopExp_Explorer(result, TopAbs_SOLID).More():
        state.bodies[name] = unified(result)
    else:
        del state.bodies[name]      # nichts übrig geblieben


@dataclass
class FusionCombine(Feature):
    """Aus einer Fusion-Datei übernommenes Kombinieren: Ziel- und Werkzeugkörper stammen
    aus der Datei, die Operation lässt sich hier ändern."""

    source: str = ""        # Pfad der Ursprungsdatei
    fusion_id: int = 0      # ID des Features in der Datei
    operation: str = "Vereinigen"

    KIND = "Fusion-Kombinieren"

    def apply(self, state: BuildState) -> None:
        combine = load_design(self.source).combines.get(self.fusion_id)
        if combine is None:
            raise FeatureError("Das Kombinieren ist in der Datei nicht lesbar")
        try:
            combine_bodies(state, combine.target, combine.tools, self.operation,
                           keep_tools=load_design(self.source).tools_kept(self.fusion_id))
        except FeatureError:
            # Fehlen Körper für den Nachbau, hilft womöglich der gespeicherte Endstand
            if reconcile_step(state, self.source, self.fusion_id) == "ohne":
                raise
            return
        reconcile_step(state, self.source, self.fusion_id)


@dataclass
class FusionRemove(Feature):
    """Aus einer Fusion-Datei übernommenes Entfernen eines Körpers."""

    source: str = ""        # Pfad der Ursprungsdatei
    fusion_id: int = 0      # ID des Features in der Datei

    KIND = "Fusion-Körper entfernen"

    def apply(self, state: BuildState) -> None:
        creators = load_design(self.source).removals.get(self.fusion_id)
        names = _bodies_from(state, creators or [])
        if not names:
            raise FeatureError("Der zu entfernende Körper ist nicht im Modell")
        for name in names:
            del state.bodies[name]


@dataclass
class FusionMove(Feature):
    """Aus einer Fusion-Datei übernommenes Verschieben: Zielkörper und Drehung stammen aus
    der Datei, die Verschiebung lässt sich hier ändern."""

    source: str = ""        # Pfad der Ursprungsdatei
    fusion_id: int = 0      # ID des Features in der Datei
    offset_x: float = 0.0
    offset_y: float = 0.0
    offset_z: float = 0.0

    KIND = "Fusion-Verschieben"

    def apply(self, state: BuildState) -> None:
        move = load_design(self.source).moves.get(self.fusion_id)
        if move is None:
            raise FeatureError("Die Verschiebung ist in der Datei nicht lesbar")
        matrix = move.matrix
        rotation = (*matrix[0:3], *matrix[4:7], *matrix[8:11])
        moved = move_bodies(state, move.creators, rotation,
                            (self.offset_x, self.offset_y, self.offset_z))
        outcome = reconcile_step(state, self.source, self.fusion_id)
        if not moved and outcome == "ohne":
            raise FeatureError("Der Zielkörper der Verschiebung ist nicht im Modell")


@dataclass
class FusionPlaceholder(Feature):
    """Ein Schritt der übernommenen Fusion-Timeline, der (noch) nicht nachgebaut wird und zu dem kein eigener
    gespeicherter Endstand vorliegt. Er hält die Reihenfolge der Timeline vollständig, verändert aber nichts;
    seine Wirkung steckt in den gespeicherten Endständen späterer Schritte."""

    source: str = ""        # Pfad der Ursprungsdatei
    fusion_id: int = 0      # ID des Features in der Datei
    # Art des Schritts in Fusion (Klassenname); nicht „kind“ – so heißt beim Speichern die Art des Features
    fusion_kind: str = ""

    KIND = "Fusion-Schritt"
    placeholder = True      # die Timeline zeigt ihn gestrichelt

    def describe(self) -> str:
        return f"Fusion-Schritt „{self.fusion_kind.removeprefix('Dc')}“ – noch nicht nachgebaut (ohne Wirkung)"

    def apply(self, state: BuildState) -> None:
        return


@dataclass
class FusionSketchStep(Feature):
    """Eine Skizze der übernommenen Fusion-Timeline: zeigt ihre Kurven an der Stelle im Raum."""

    source: str = ""        # Pfad der Ursprungsdatei
    fusion_id: int = 0      # ID des Skizzen-Features in der Datei

    KIND = "Fusion-Skizze"

    def apply(self, state: BuildState) -> None:
        from ..f3d import sketches
        key = (self.source, self.fusion_id)
        if key not in _SKETCH_STEP_CACHE:
            found = load_design(self.source).sketches.get(self.fusion_id)
            _SKETCH_STEP_CACHE[key] = sketches.to_shape(found) if found is not None else None
        shape = _SKETCH_STEP_CACHE[key]
        if shape is None:
            raise FeatureError("Die Skizze ist in der Datei nicht lesbar")
        state.sketch_curves.append(shape)


_SKETCH_STEP_CACHE: dict = {}       # (Datei, Skizzen-ID) -> Kurven der Skizze im Raum


@dataclass
class FusionPaste(Feature):
    """Aus einer Fusion-Datei übernommenes Einfügen einer Körperkopie: der kopierte Körper stammt aus der Datei,
    die Kopie liegt zunächst an derselben Stelle (ein folgendes Verschieben bewegt sie)."""

    source: str = ""        # Pfad der Ursprungsdatei
    fusion_id: int = 0      # ID des Features in der Datei

    KIND = "Fusion-Einfügen"

    def apply(self, state: BuildState) -> None:
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
        paste = load_design(self.source).pastes.get(self.fusion_id)
        if paste is None:
            raise FeatureError("Das Einfügen ist in der Datei nicht lesbar")
        # auch eingefrorene (aus dem gespeicherten Endstand eingesetzte) Körper dürfen kopiert werden
        originals = [name for name, origin in state.body_origin.items()
                     if origin in paste.sources and name in state.bodies]
        for name in originals:
            # die Kopie gehört dem eingebetteten Feature: über diesen „Erzeuger“ finden spätere Schritte sie
            state.add_body(BRepBuilderAPI_Copy(state.bodies[name], True, False).Shape(), paste.base)
        outcome = reconcile_step(state, self.source, self.fusion_id)
        if not originals and outcome == "ohne":
            raise FeatureError("Der kopierte Körper ist nicht im Modell")


_FACE_MATCH_TOLERANCE = 1e-3       # mm: so genau muss eine Fläche zur benannten Fläche der Geometriedatei passen


def stored_named_faces(source: str) -> dict:
    """Benannte Flächen aus den eingelesenen Geometrie-Blobs der Datei: Name -> [(Fläche, Nachbarnamen)]."""
    merged: dict = {}
    for (path, _blob), result in _IMPORT_CACHE.items():
        if path == source:
            for name, entries in getattr(result, "named_faces", {}).items():
                merged.setdefault(name, []).extend(entries)
    return merged


def faces_at_named(shape: TopoDS_Shape, references: list, named: dict) -> list:
    """Die Flächen des eigenen Körpers, die Fusions benannte Flächen meinen.

    Je Verweis werden die gleichnamigen Flächen der Geometriedatei geprüft – zuerst die mit den meisten passenden
    Nachbarn. Eine eigene Fläche passt, wenn Schwerpunkt und Inhalt übereinstimmen (gleiche Fläche) oder wenn sie auf
    derselben Trägerfläche liegt und die gespeicherte Fläche berührt (die Fläche wurde später noch beschnitten).

    Schnell auch bei vielen gleichnamigen Kopien: Kandidaten außerhalb des Körpers und eigene Flächen, deren
    Hüllquader die gespeicherte Fläche nicht treffen, fallen vorab weg (der erste Lauf an einer echten Datei rechnete
    sonst über 25 Minuten); Schwerpunkt und Inhalt eigener Flächen werden nur bei Bedarf und einmal gerechnet."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf

    def box_of(item) -> Bnd_Box:
        box = Bnd_Box()
        BRepBndLib.Add_s(item, box)
        box.Enlarge(_FACE_MATCH_TOLERANCE * 10.0)
        return box

    body_box = box_of(shape)
    own = [(face, box_of(face)) for face in faces_of(shape)]
    measured: dict[int, tuple] = {}                 # Platz in `own` -> (Schwerpunkt, Inhalt, Flächenart)

    def measure(index: int) -> tuple:
        if index not in measured:
            face = own[index][0]
            measured[index] = (face_point(face), _face_area(face), BRepAdaptor_Surface(face).GetType())
        return measured[index]

    found = []
    for reference in references:
        candidates = sorted(named.get(reference.name, []),
                            key=lambda entry: -len(entry[1] & set(reference.neighbours)))
        hit = None
        for stored, _around in candidates:
            stored = TopoDS.Face(stored)            # nach dem Verschieben eine allgemeine Form: als Fläche lesen
            stored_box = box_of(stored)
            if body_box.IsOut(stored_box):
                continue                            # Kopie an anderer Stelle: gehört zu einem anderen Körper
            nearby = [index for index, (_face, box) in enumerate(own) if not box.IsOut(stored_box)]
            if not nearby:
                continue
            stored_point, stored_area = face_point(stored), _face_area(stored)
            stored_type = BRepAdaptor_Surface(stored).GetType()
            # 1) dieselbe Fläche
            for index in nearby:
                point, area, kind = measure(index)
                if kind == stored_type and math.dist(point, stored_point) < 0.01 \
                        and abs(area - stored_area) <= 0.01 * max(area, 1e-9):
                    hit = own[index][0]
                    break
            if hit is None:
                # 2) gleiche Trägerfläche, und die Flächen berühren sich
                surface = BRep_Tool.Surface_s(stored)
                for index in nearby:
                    _point, _area, kind = measure(index)
                    if kind != stored_type:
                        continue
                    # ein Punkt AUF der eigenen Fläche (der Schwerpunkt liegt bei gekrümmten Flächen daneben,
                    # etwa im Inneren eines Zylindermantels)
                    projector = GeomAPI_ProjectPointOnSurf(_point_on_face(own[index][0]), surface)
                    if projector.NbPoints() and projector.LowerDistance() < _FACE_MATCH_TOLERANCE \
                            and BRepExtrema_DistShapeShape(own[index][0], stored).Value() < _FACE_MATCH_TOLERANCE:
                        hit = own[index][0]
                        break
            if hit is not None:
                break
        if hit is not None and not any(hit.IsSame(other) for other in found):
            found.append(hit)
    return found


def _point_on_face(face) -> gp_Pnt:
    """Ein Punkt auf der Trägerfläche der Fläche: Mitte ihres Parameterbereichs."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    u0, u1, v0, v1 = BRepTools.UVBounds_s(TopoDS.Face(face))
    return BRepAdaptor_Surface(TopoDS.Face(face)).Value((u0 + u1) / 2.0, (v0 + v1) / 2.0)


def _face_area(face) -> float:
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, properties)
    return properties.Mass()


def _fusion_face_step(state: BuildState, source: str, fusion_id: int, label: str, build) -> None:
    """Gemeinsamer Ablauf für „Fläche löschen“ und „Flächen versetzen“: je Gruppe die Körper (über ihr
    erzeugendes Feature) und darin die benannten Flächen suchen, `build(Körper, Flächen)` anwenden, danach mit dem
    gespeicherten Endstand abgleichen."""
    design = load_design(source)
    face_feature = design.face_features.get(fusion_id)
    if face_feature is None:
        raise FeatureError(f"{label} ist in der Datei nicht lesbar")
    named = stored_named_faces(source)
    try:
        if not named:
            raise FeatureError(f"{label}: die Geometrie der Datei ist nicht eingelesen (Flächen unbekannt)")
        changed = False
        for face_set in face_feature.sets:
            creators = [design.bodies.creator_of.get(body) for body in face_set.bodies]
            for name in _bodies_from(state, creators):
                faces = faces_at_named(state.bodies[name], face_set.faces, named)
                if faces:
                    state.bodies[name] = build(state.bodies[name], faces)
                    changed = True
        if not changed:
            raise FeatureError(f"{label}: die Flächen sind im Modell nicht zu finden")
    except FeatureError:
        if reconcile_step(state, source, fusion_id) == "ohne":
            raise
        return
    reconcile_step(state, source, fusion_id)


@dataclass
class FusionDeleteFace(Feature):
    """Aus einer Fusion-Datei übernommenes „Fläche löschen“: die Flächen stammen aus der Datei (über Fusions
    Flächennamen in der Geometriedatei wiedergefunden)."""

    source: str = ""        # Pfad der Ursprungsdatei
    fusion_id: int = 0      # ID des Features in der Datei

    KIND = "Fusion-Fläche löschen"

    def apply(self, state: BuildState) -> None:
        from .solids import remove_faces
        _fusion_face_step(state, self.source, self.fusion_id, "Fläche löschen", remove_faces)


@dataclass
class FusionOffsetFaces(Feature):
    """Aus einer Fusion-Datei übernommenes „Flächen versetzen“; der Abstand lässt sich hier ändern."""

    source: str = ""        # Pfad der Ursprungsdatei
    fusion_id: int = 0      # ID des Features in der Datei
    distance: float = 1.0   # mm, positiv nach außen

    KIND = "Fusion-Flächen versetzen"

    def apply(self, state: BuildState) -> None:
        from .solids import offset_faces
        if abs(self.distance) < 1e-9:
            raise FeatureError("Der Abstand darf nicht 0 sein")
        _fusion_face_step(state, self.source, self.fusion_id, "Flächen versetzen",
                          lambda body, faces: offset_faces(body, faces, self.distance))


_EDGE_MATCH_TOLERANCE = 1e-3       # mm: so genau muss eine Kante zur zwischengespeicherten Geometrie passen


def edges_at_cached(shape: TopoDS_Shape, cached: list) -> list:
    """Die Kanten des Körpers, die zu Fusions zwischengespeicherter Kantengeometrie passen.

    `cached`: CachedEdge-Objekte (cm, siehe f3d/edges.py). Eine Körperkante passt, wenn Anfang, Mitte und Ende der
    gespeicherten Kante auf ihr liegen – oder umgekehrt ihre eigenen Punkte auf der gespeicherten Kante (so werden
    auch Teilstücke gefunden, wenn die Kante im eigenen Körper zerlegt ist). Vorab sortiert der Hüllquader aus."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex

    def near(point: gp_Pnt, shape) -> bool:
        return BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(point).Vertex(), shape).Value() \
            < _EDGE_MATCH_TOLERANCE

    candidates = []
    for edge in unique_edges(shape):
        box = Bnd_Box()
        BRepBndLib.Add_s(edge, box)
        box.Enlarge(_EDGE_MATCH_TOLERANCE * 10.0)
        curve = BRepAdaptor_Curve(edge)
        first, last = curve.FirstParameter(), curve.LastParameter()
        own = [curve.Value(t) for t in (first, (first + last) / 2.0, last)]
        candidates.append((edge, box, own))
    found = []
    for cached_edge in cached:
        points = [gp_Pnt(*(value * 10.0 for value in point)) for point in cached_edge.points()]      # cm → mm
        reference = cached_edge.to_edge()
        reference_box = Bnd_Box()
        if reference is not None:
            BRepBndLib.Add_s(reference, reference_box)
            reference_box.Enlarge(_EDGE_MATCH_TOLERANCE * 10.0)
        for edge, box, own in candidates:
            if any(edge.IsSame(other) for other in found):
                continue
            whole = not any(box.IsOut(point) for point in points) and all(near(point, edge) for point in points)
            piece = (reference is not None and not any(reference_box.IsOut(point) for point in own)
                     and all(near(point, reference) for point in own))
            if whole or piece:
                found.append(edge)
    return found


def _fusion_edge_step(state: BuildState, source: str, fusion_id: int, label: str, build) -> None:
    """Gemeinsamer Ablauf für übernommene Abrundungen und Fasen: je Kantengruppe die Körper (über ihr erzeugendes
    Feature) und darin die Kanten suchen, `build(Körper, Kanten, Gruppe)` anwenden, danach mit dem gespeicherten
    Endstand abgleichen. Fehlt etwas für den Nachbau, hilft womöglich der gespeicherte Endstand."""
    design = load_design(source)
    edge_feature = design.edge_features.get(fusion_id)
    if edge_feature is None:
        raise FeatureError(f"{label} ist in der Datei nicht lesbar")
    try:
        changed = False
        for edge_set in edge_feature.sets:
            creators = [design.bodies.creator_of.get(body) for body in edge_set.bodies]
            for name in _bodies_from(state, creators):
                edges = edges_at_cached(state.bodies[name], edge_set.edges)
                if edges:
                    state.bodies[name] = build(state.bodies[name], edges, edge_set)
                    changed = True
        if not changed:
            raise FeatureError(f"{label}: die Kanten sind im Modell nicht zu finden")
    except FeatureError:
        if reconcile_step(state, source, fusion_id) == "ohne":
            raise
        return
    reconcile_step(state, source, fusion_id)


@dataclass
class FusionFillet(Feature):
    """Aus einer Fusion-Datei übernommene Abrundung: die Kanten stammen aus der Datei (über Fusions
    zwischengespeicherte Kantengeometrie wiedergefunden), der Radius lässt sich hier ändern."""

    source: str = ""        # Pfad der Ursprungsdatei
    fusion_id: int = 0      # ID des Features in der Datei
    radius: float = 1.0     # mm

    KIND = "Fusion-Abrunden"

    def apply(self, state: BuildState) -> None:
        if self.radius <= 0:
            raise FeatureError("Der Radius muss größer als 0 sein")

        def fillet(body: TopoDS_Shape, edges: list, scale: float = 1.0) -> TopoDS_Shape:
            maker = BRepFilletAPI_MakeFillet(body)
            try:
                for edge in edges:
                    maker.Add(self.radius * scale, edge)
            except Exception as error:      # z. B. Kante ohne genau zwei Nachbarflächen
                raise FeatureError(f"Abrunden: eine Kante lässt sich nicht runden ({error})") from None
            return _finish(maker, "Verrundung", "Radius")

        # wie beim eigenen Abrunden auch bis zur vollen Rundung
        _fusion_edge_step(state, self.source, self.fusion_id, "Abrunden",
                          lambda body, edges, _set: _with_full_round(fillet, self.radius)(body, edges))


@dataclass
class FusionChamfer(Feature):
    """Aus einer Fusion-Datei übernommene Fase (gleicher oder zwei Abstände); die Abstände lassen sich ändern.
    Bei zwei Abständen gilt der erste auf der ersten Nachbarfläche der Kante – passt das nicht zu Fusion, setzt
    der Abgleich den gespeicherten Endstand ein."""

    source: str = ""        # Pfad der Ursprungsdatei
    fusion_id: int = 0      # ID des Features in der Datei
    distance: float = 1.0   # mm
    distance2: float = 0.0  # mm, 0 = gleicher Abstand

    KIND = "Fusion-Fase"

    def apply(self, state: BuildState) -> None:
        if self.distance <= 0 or self.distance2 < 0:
            raise FeatureError("Der Abstand muss größer als 0 sein")

        def chamfer(body: TopoDS_Shape, edges: list, scale: float = 1.0) -> TopoDS_Shape:
            maker = BRepFilletAPI_MakeChamfer(body)
            neighbours = IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
            TopExp.MapShapesAndAncestors_s(body, TopAbs_EDGE, TopAbs_FACE, neighbours)
            try:
                for edge in edges:
                    if self.distance2 > 0 and abs(self.distance2 - self.distance) > 1e-9:
                        face = TopoDS.Face(neighbours.FindFromKey(edge).First())
                        maker.Add(self.distance * scale, self.distance2 * scale, TopoDS.Edge(edge), face)
                    else:
                        maker.Add(self.distance * scale, edge)
            except Exception as error:      # z. B. Kante ohne genau zwei Nachbarflächen
                raise FeatureError(f"Fase: eine Kante lässt sich nicht anfasen ({error})") from None
            return _finish(maker, "Fase", "Abstand")

        size = max(self.distance, self.distance2)
        _fusion_edge_step(state, self.source, self.fusion_id, "Fase",
                          lambda body, edges, _set: _with_full_round(chamfer, size)(body, edges))


def load_design(source: str):
    """Liest Verlauf, Skizzen und Extrusionen einer .f3d-Datei und merkt sie sich."""
    from ..f3d import schema
    from ..f3d.container import F3dArchive

    if source not in _DESIGN_CACHE:
        try:
            with F3dArchive(source) as archive:
                _DESIGN_CACHE[source] = archive.design(schema.load())
        except OSError as error:
            raise FeatureError(f"Quelldatei nicht lesbar: {error}") from error
    return _DESIGN_CACHE[source]


def load_sketch_curves(source: str) -> list[TopoDS_Shape]:
    """Kurven aller Skizzen einer .f3d-Datei in Weltkoordinaten (werden gemerkt)."""
    from ..f3d import sketches

    if source not in _SKETCH_CACHE:
        found = load_design(source).sketches
        shapes = (sketches.to_shape(sketch) for _, sketch in sorted(found.items()))
        _SKETCH_CACHE[source] = [shape for shape in shapes if shape is not None]
    return _SKETCH_CACHE[source]


def load_import(source: str, blob: str, progress=None):
    """Liest einen Geometrie-Blob ein und merkt sich das Ergebnis.

    Jede Neuberechnung der Timeline würde die Datei sonst erneut einlesen.
    `progress(erledigt, gesamt)` wird an den Import durchgereicht; bricht der
    Benutzer darüber ab, wird das Teilergebnis nicht gemerkt.
    """
    # Import erst hier laden, damit der Kern ohne das Importmodul nutzbar bleibt
    from ..f3d import brep_import, sab
    from ..f3d.container import F3dArchive

    key = (source, blob)
    if key in _IMPORT_CACHE:
        return _IMPORT_CACHE[key]
    try:
        with F3dArchive(source) as archive:
            data = archive.read(blob)
    except (OSError, KeyError) as error:
        raise FeatureError(f"Quelldatei nicht lesbar: {error}") from error

    cancelled = False

    def report(done: int, total: int) -> bool:
        nonlocal cancelled
        if progress is not None and progress(done, total) is False:
            cancelled = True
            return False
        return True

    # Flächen, auf die der Verlauf verweist (Fläche löschen, Flächen versetzen), einzeln mit Namen merken
    try:
        wanted = load_design(source).wanted_face_names()
    except FeatureError:
        wanted = set()
    result = brep_import.import_bodies(sab.parse(data), report, wanted)
    if not cancelled:
        _IMPORT_CACHE[key] = result
    return result


# Zuordnung für das Laden gespeicherter Entwürfe
FEATURE_CLASSES = {
    cls.KIND: cls
    for cls in (RectangleSketch, CircleSketch, Extrude, Fillet, Chamfer, ImportedReference,
                ImportedSketches, FusionExtrude, FusionBox, FusionCylinder, FusionSphere,
                FusionMove, FusionCombine, FusionRemove, FusionStored, FusionFillet, FusionChamfer, FusionPaste,
                FusionPlaceholder, FusionSketchStep, FusionDeleteFace, FusionOffsetFaces)
}


def feature_from_dict(data: dict) -> Feature:
    data = dict(data)
    kind = data.pop("kind")
    if kind not in FEATURE_CLASSES:
        raise ValueError(f"Unbekannter Feature-Typ '{kind}'")
    return FEATURE_CLASSES[kind](**data)


# Die eigenen Volumen-Werkzeuge tragen sich beim Laden in FEATURE_CLASSES ein
from . import extras, meshes, more_tools, sculpt, sheetmetal, sketch, solids, surfaces  # noqa: E402,F401
