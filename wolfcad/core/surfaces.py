# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Flächen wie im Fusion-Reiter FLÄCHE: Flächenkörper sind offene Flächen ohne Volumen (`BuildState.surfaces`).

- Extrudieren / Drehen: aus den Kurven einer Skizze (auch offenen Linienzügen) entsteht eine Fläche.
- Flicken: eine geschlossene Skizze (eben) bzw. ein geschlossener Kurvenzug (auch räumlich, z. B. aus einer
  3D-Skizze) wird mit einer Fläche gefüllt.
- Versatz: eine Fläche im Abstand parallel daneben.
- Aufdicken: aus einer Fläche wird ein Volumenkörper bestimmter Dicke.
- Vernähen: mehrere Flächen, die zusammen eine geschlossene Hülle bilden, werden zu einem Volumenkörper.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
from OCP.BRepOffset import BRepOffset_MakeOffset, BRepOffset_Skin
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeFilling, BRepOffsetAPI_MakeOffsetShape
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.GeomAbs import GeomAbs_C0, GeomAbs_Intersection
from OCP.TopAbs import TopAbs_SHELL
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax1, gp_Dir, gp_Vec

from . import features as ft
from .features import BuildState, Feature, FeatureError

DIRECTIONS = ("Eine Seite", "Symmetrisch")
REVOLVE_AXES = ("X-Achse der Skizze", "Y-Achse der Skizze")
NO_SURFACE = ""


def _surface_of(state: BuildState, name: str):
    if name not in state.surfaces:
        raise FeatureError(f"Flächenkörper '{name}' ist nicht (mehr) vorhanden" if name else "Bitte einen Flächenkörper wählen")
    return state.surfaces[name]


def _curves(state: BuildState, sketch: str):
    curves = state.curves.get(sketch)
    if curves is None or not ft.unique_edges(curves):
        raise FeatureError(f"Die Skizze '{sketch}' hat keine Kurven" if sketch else "Bitte eine Skizze wählen")
    return curves


@dataclass
class SurfaceExtrude(Feature):
    """Fläche extrudieren: die Kurven der Skizze (auch offene) werden senkrecht zur Skizzenebene zu Flächen gezogen."""

    sketch: str = ""
    distance: float = 10.0
    direction: str = DIRECTIONS[0]      # eine Seite oder symmetrisch zur Skizzenebene

    KIND = "Fläche extrudieren"

    def apply(self, state: BuildState) -> None:
        if abs(self.distance) < 1e-9:
            raise FeatureError("Der Abstand darf nicht 0 sein")
        curves = _curves(state, self.sketch)
        normal = gp_Vec(*state.normals[self.sketch])
        if self.direction == DIRECTIONS[1]:
            # symmetrisch: halb nach jeder Seite
            shifted = _moved(curves, normal * (-self.distance / 2))
            shape = BRepPrimAPI_MakePrism(shifted, normal * self.distance).Shape()
        else:
            shape = BRepPrimAPI_MakePrism(curves, normal * self.distance).Shape()
        state.add_surface(shape)


def _translation(vector: gp_Vec):
    from OCP.gp import gp_Trsf
    move = gp_Trsf()
    move.SetTranslation(vector)
    return move


def _moved(shape, vector: gp_Vec):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    return BRepBuilderAPI_Transform(shape, _translation(vector), True).Shape()


@dataclass
class SurfaceRevolve(Feature):
    """Fläche drehen: die Kurven der Skizze werden um eine Achse der Skizze gedreht."""

    sketch: str = ""
    axis: str = REVOLVE_AXES[1]
    angle: float = 360.0

    KIND = "Fläche drehen"

    def apply(self, state: BuildState) -> None:
        if not 0 < self.angle <= 360:
            raise FeatureError("Der Winkel liegt zwischen 0° und 360°")
        curves = _curves(state, self.sketch)
        frame = state.frames[self.sketch]
        if self.axis in state.axes:
            point, direction = state.axes[self.axis]
            from OCP.gp import gp_Pnt
            around = gp_Ax1(gp_Pnt(*point), gp_Dir(*direction))
        elif self.axis in REVOLVE_AXES:
            around = gp_Ax1(frame.Location(), frame.XDirection() if self.axis == REVOLVE_AXES[0] else frame.YDirection())
        else:
            raise FeatureError(f"Achse '{self.axis}' ist nicht (mehr) vorhanden")
        try:
            shape = BRepPrimAPI_MakeRevol(curves, around, math.radians(self.angle)).Shape()
        except Exception as error:      # der Kern bricht ab, wenn das Profil die Achse schneidet
            raise FeatureError("Das Profil darf die Drehachse nicht schneiden") from error
        state.add_surface(shape)


@dataclass
class Patch(Feature):
    """Flicken: eine geschlossene Skizze wird zur Fläche gefüllt – eben aus dem Profil, sonst (räumlicher
    Kurvenzug) als möglichst glatte Füllfläche durch die Randkurven."""

    sketch: str = ""

    KIND = "Flicken"

    def apply(self, state: BuildState) -> None:
        profile = state.profiles.get(self.sketch)
        if profile is not None:
            state.add_surface(profile)
            return
        curves = _curves(state, self.sketch)
        filling = BRepOffsetAPI_MakeFilling()
        for edge in ft.unique_edges(curves):
            filling.Add(edge, GeomAbs_C0)
        try:
            filling.Build()
        except Exception as error:      # der Kern meldet ungeschlossene Ränder mit einer Ausnahme
            raise FeatureError("Der Kurvenzug ist nicht geschlossen – Flicken braucht einen geschlossenen Rand") from error
        if not filling.IsDone():
            raise FeatureError("Der Kurvenzug ist nicht geschlossen – Flicken braucht einen geschlossenen Rand")
        state.add_surface(filling.Shape())


@dataclass
class OffsetSurface(Feature):
    """Versatz: der Flächenkörper parallel im Abstand (negativ: zur anderen Seite) – als neuer Flächenkörper."""

    surface: str = NO_SURFACE
    distance: float = 5.0
    flip: bool = False              # zur anderen Seite (Fusion: Richtung umkehren)

    KIND = "Fläche versetzen"

    def apply(self, state: BuildState) -> None:
        shape = _surface_of(state, self.surface)
        if abs(self.distance) < 1e-9:
            raise FeatureError("Der Abstand darf nicht 0 sein")
        maker = BRepOffsetAPI_MakeOffsetShape()
        distance = -self.distance if self.flip else self.distance
        maker.PerformByJoin(shape, distance, 1e-6, BRepOffset_Skin, False, False, GeomAbs_Intersection)
        if not maker.IsDone():
            raise FeatureError("Die Fläche ließ sich so nicht versetzen")
        state.add_surface(maker.Shape())


@dataclass
class Thicken(Feature):
    """Aufdicken: aus dem Flächenkörper wird ein Volumenkörper der angegebenen Dicke (negativ: zur anderen Seite);
    die Fläche verschwindet dabei wie in Fusion."""

    surface: str = NO_SURFACE
    thickness: float = 2.0
    flip: bool = False              # zur anderen Seite aufdicken

    KIND = "Aufdicken"

    def apply(self, state: BuildState) -> None:
        shape = _surface_of(state, self.surface)
        if abs(self.thickness) < 1e-9:
            raise FeatureError("Die Dicke darf nicht 0 sein")
        # erst zu einer zusammenhängenden Hülle vernähen (Extrudieren liefert einzelne Flächen), dann mit dem
        # Versatz „aufdicken“: der Kern baut den Körper zwischen Fläche und versetzter Fläche
        sewing = BRepBuilderAPI_Sewing(1e-4)
        sewing.Add(shape)
        sewing.Perform()
        maker = BRepOffset_MakeOffset()
        thickness = -self.thickness if self.flip else self.thickness
        maker.Initialize(sewing.SewedShape(), thickness, 1e-6, BRepOffset_Skin, False, False,
                         GeomAbs_Intersection, True)
        try:
            maker.MakeOffsetShape()
        except Exception as error:
            raise FeatureError("Die Fläche ließ sich nicht aufdicken") from error
        if not maker.IsDone():
            raise FeatureError("Die Fläche ließ sich nicht aufdicken")
        solid = maker.Shape()
        if abs(_volume(solid)) <= 1e-9:
            raise FeatureError("Die Fläche ließ sich nicht aufdicken")
        if _volume(solid) < 0:
            solid.Reverse()         # nach innen gekehrt: umdrehen, sonst rechnen spätere Verknüpfungen falsch
        state.add_body(solid)
        del state.surfaces[self.surface]


@dataclass
class Stitch(Feature):
    """Vernähen: Flächenkörper, die zusammen eine geschlossene Hülle bilden, werden zu einem Volumenkörper
    (Fusion: Zusammenfügen/Vernähen). Ist die Hülle nicht dicht, bleibt ein vernähter Flächenkörper."""

    surfaces: list = field(default_factory=list)     # Namen der Flächenkörper
    tolerance: float = 0.01

    KIND = "Vernähen"

    def apply(self, state: BuildState) -> None:
        if len(self.surfaces) < 1:
            raise FeatureError("Bitte die Flächenkörper zum Vernähen anklicken")
        sewing = BRepBuilderAPI_Sewing(self.tolerance)
        for name in self.surfaces:
            sewing.Add(_surface_of(state, name))
        sewing.Perform()
        sewn = sewing.SewedShape()
        for name in self.surfaces:
            del state.surfaces[name]
        shells = TopExp_Explorer(sewn, TopAbs_SHELL)
        if shells.More() and sewing.NbFreeEdges() == 0:
            maker = BRepBuilderAPI_MakeSolid(TopoDS.Shell(shells.Current()))
            solid = maker.Solid()
            if _volume(solid) < 0:          # nach innen gekehrte Hülle umdrehen
                solid.Reverse()
            state.add_body(solid)
        else:
            state.add_surface(sewn)     # nicht dicht: als zusammenhängende Fläche behalten


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


@dataclass
class SurfaceSweep(Feature):
    """Fläche als Sweep: die Kurve(n) der Profilskizze (offen oder geschlossen) entlang der Pfadskizze ziehen."""

    sketch: str = ""
    path: str = ""

    KIND = "Fläche sweepen"

    def apply(self, state: BuildState) -> None:
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
        from .solids import _path_wire
        if not self.sketch or not self.path or self.sketch == self.path:
            raise FeatureError("Bitte eine Profilskizze und eine andere Skizze als Pfad wählen")
        profile, spine = _path_wire(state, self.sketch), _path_wire(state, self.path)
        pipe = BRepOffsetAPI_MakePipe(spine, profile)
        pipe.Build()
        if not pipe.IsDone():
            raise FeatureError("Die Fläche ließ sich nicht entlang des Pfads ziehen")
        state.add_surface(pipe.Shape())


@dataclass
class SurfaceLoft(Feature):
    """Fläche als Erhebung: verbindet die Kurvenzüge mehrerer Skizzen der Reihe nach (offen oder geschlossen)."""

    sketch: str = ""
    sketch2: str = ""
    sketch3: str = ""       # wahlfrei

    KIND = "Fläche erheben"

    def apply(self, state: BuildState) -> None:
        from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
        from .solids import _path_wire
        names = [name for name in (self.sketch, self.sketch2, self.sketch3) if name]
        if len(names) < 2 or len(set(names)) < len(names):
            raise FeatureError("Bitte mindestens zwei verschiedene Skizzen wählen")
        loft = BRepOffsetAPI_ThruSections(False, False)     # Fläche, keine Volumenhülle
        for name in names:
            loft.AddWire(_path_wire(state, name))
        loft.Build()
        if not loft.IsDone():
            raise FeatureError("Die Kurvenzüge lassen sich nicht verbinden (gleiche Art: alle offen oder alle geschlossen)")
        state.add_surface(loft.Shape())


@dataclass
class Unstitch(Feature):
    """Trennen (Fusion: Trennen/Unstitch): ein Flächen- oder Volumenkörper zerfällt in seine einzelnen Flächen,
    jede ein eigener Flächenkörper."""

    surface: str = NO_SURFACE       # Flächenkörper oder Volumenkörper (Name)

    KIND = "Trennen"

    def apply(self, state: BuildState) -> None:
        if self.surface in state.surfaces:
            shape = state.surfaces.pop(self.surface)
        elif self.surface in state.bodies:
            shape = state.bodies.pop(self.surface)
        else:
            raise FeatureError("Bitte einen Flächen- oder Volumenkörper wählen")
        for face in ft.faces_of(shape):
            state.add_surface(face)


@dataclass
class TrimSurface(Feature):
    """Stutzen (Fusion: Stutzen): der Flächenkörper wird am Schnittwerkzeug (anderer Flächen- oder Volumenkörper)
    geteilt; die angeklickten Stücke fallen weg. Ohne angeklickte Stücke bleibt das größte Stück."""

    surface: str = NO_SURFACE
    tool: str = ""                                          # Flächen- oder Volumenkörper, an dem geschnitten wird
    remove_points: list = field(default_factory=list)       # angeklickte Punkte auf Stücken, die wegfallen

    KIND = "Fläche stutzen"

    def apply(self, state: BuildState) -> None:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
        from OCP.collections import List_TopoDS_Shape      # Liste von Formen für den Kern
        shape = _surface_of(state, self.surface)
        cutter = state.surfaces.get(self.tool) or state.bodies.get(self.tool)
        if cutter is None or self.tool == self.surface:
            raise FeatureError("Bitte ein Schnittwerkzeug wählen (anderer Flächen- oder Volumenkörper)")
        arguments, tools = List_TopoDS_Shape(), List_TopoDS_Shape()
        arguments.Append(shape)
        tools.Append(cutter)
        splitter = BRepAlgoAPI_Splitter()
        splitter.SetArguments(arguments)
        splitter.SetTools(tools)
        splitter.Build()
        if not splitter.IsDone():
            raise FeatureError("Die Fläche ließ sich am Werkzeug nicht teilen")
        pieces = ft.faces_of(splitter.Shape())
        if len(pieces) <= len(ft.faces_of(shape)):
            raise FeatureError("Das Werkzeug schneidet die Fläche nicht")
        if self.remove_points:
            # weg fällt jedes Stück, auf dem ein angeklickter Punkt liegt
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
            from OCP.BRepExtrema import BRepExtrema_DistShapeShape
            from OCP.gp import gp_Pnt

            def touched(face) -> bool:
                for point in self.remove_points:
                    measure = BRepExtrema_DistShapeShape(face, BRepBuilderAPI_MakeVertex(gp_Pnt(*point)).Vertex())
                    if measure.IsDone() and measure.Value() < 1e-3:
                        return True
                return False
            kept = [face for face in pieces if not touched(face)]
        else:
            kept = [max(pieces, key=_area)]
        if not kept:
            raise FeatureError("Es würde nichts von der Fläche übrig bleiben")
        sewing = BRepBuilderAPI_Sewing(1e-4)
        for face in kept:
            sewing.Add(face)
        sewing.Perform()
        state.surfaces[self.surface] = sewing.SewedShape()


@dataclass
class ExtendSurface(Feature):
    """Verlängern (Fusion: Verlängern): jede Fläche des Flächenkörpers wird an ihren Rändern um den Abstand auf
    ihrer eigenen Geometrie weitergeführt (Ebene bleibt Ebene, Zylinder bleibt Zylinder). Ausschnitte in der Fläche
    gehen dabei verloren; Splineflächen enden spätestens an der Grenze ihres Parameterbereichs."""

    surface: str = NO_SURFACE
    distance: float = 5.0

    KIND = "Fläche verlängern"

    def apply(self, state: BuildState) -> None:
        from OCP.BRep import BRep_Tool
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.BRepTools import BRepTools
        from OCP.gp import gp_Pnt, gp_Vec
        shape = _surface_of(state, self.surface)
        if self.distance <= 0:
            raise FeatureError("Der Abstand muss größer als 0 sein")
        sewing = BRepBuilderAPI_Sewing(1e-4)
        for face in ft.faces_of(shape):
            surface = BRep_Tool.Surface_s(face)
            u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
            # Parameterschritt, der in der Flächenmitte der gewünschten Länge entspricht
            point, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
            surface.D1((u0 + u1) / 2, (v0 + v1) / 2, point, du, dv)
            step_u = self.distance / du.Magnitude() if du.Magnitude() > 1e-12 else 0.0
            step_v = self.distance / dv.Magnitude() if dv.Magnitude() > 1e-12 else 0.0
            new = [u0 - step_u, u1 + step_u, v0 - step_v, v1 + step_v]
            # geschlossene (umlaufende) Richtungen nicht über eine volle Runde hinaus
            if surface.IsUPeriodic() and new[1] - new[0] > surface.UPeriod():
                new[0:2] = [u0, u0 + surface.UPeriod()]
            if surface.IsVPeriodic() and new[3] - new[2] > surface.VPeriod():
                new[2:4] = [v0, v0 + surface.VPeriod()]
            # Splineflächen enden an ihrem Parameterbereich: dort nur bis an dessen Grenze
            low_u, high_u, low_v, high_v = surface.Bounds()
            new = [max(new[0], low_u), min(new[1], high_u), max(new[2], low_v), min(new[3], high_v)]
            maker = BRepBuilderAPI_MakeFace(surface, *new, 1e-6)
            if not maker.IsDone():
                raise FeatureError("Eine der Flächen ließ sich nicht verlängern")
            sewing.Add(maker.Face())
        sewing.Perform()
        state.surfaces[self.surface] = sewing.SewedShape()


def _plane_sheet(state: BuildState, axes, tools: list):
    """Eine Konstruktionsebene als begrenzende Fläche: ein Quadrat um ihren Ursprung, groß genug, um alle übrigen
    Begrenzungen zu durchschneiden (eine unbegrenzte Ebene kann der Zellbildner nicht verarbeiten)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Pln
    from .solids import _bounds
    reach = 50.0
    for name in tools:
        shape = state.surfaces.get(name) or state.bodies.get(name)
        if shape is not None:
            bounds = _bounds(shape)
            corner = max(abs(value - coordinate) for value, coordinate in
                         zip(bounds, (axes.Location().X(), axes.Location().Y(), axes.Location().Z()) * 2))
            reach = max(reach, 2 * corner + 10.0)
    return BRepBuilderAPI_MakeFace(gp_Pln(axes.Location(), axes.Direction()), -reach, reach, -reach, reach).Face()


@dataclass
class BoundaryFill(Feature):
    """Begrenzungsfüllung (Fusion: Begrenzungsfüllung): Flächen- und Volumenkörper als Wände bilden geschlossene
    Zellen; die angeklickten Zellen (ohne Auswahl: alle) werden zu einem neuen Volumenkörper."""

    tools: list = field(default_factory=list)               # Namen der begrenzenden Flächen-/Volumenkörper
    cell_points: list = field(default_factory=list)         # Punkte in den gewünschten Zellen (leer: alle)
    remove_points: list = field(default_factory=list)       # Punkte in Zellen, die nicht gefüllt werden (Klick)

    KIND = "Begrenzungsfüllung"

    def apply(self, state: BuildState) -> None:
        from OCP.BOPAlgo import BOPAlgo_MakerVolume
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.TopAbs import TopAbs_IN, TopAbs_SOLID
        from OCP.collections import List_TopoDS_Shape      # Liste von Formen für den Kern
        from OCP.gp import gp_Pnt
        if len(self.tools) < 1:
            raise FeatureError("Bitte die begrenzenden Flächen- bzw. Volumenkörper anklicken")
        shapes = List_TopoDS_Shape()
        for name in self.tools:
            shape = state.surfaces.get(name) or state.bodies.get(name)
            if shape is None and name in state.planes:
                shape = _plane_sheet(state, state.planes[name], self.tools)     # wie Fusion: auch Ebenen
            if shape is None:
                raise FeatureError(f"'{name}' ist nicht (mehr) vorhanden")
            shapes.Append(shape)
        maker = BOPAlgo_MakerVolume()
        maker.SetArguments(shapes)
        maker.SetIntersect(True)
        maker.Perform()
        if maker.HasErrors():
            raise FeatureError("Die Begrenzungen schließen keine Zelle ein")
        cells = []
        explorer = TopExp_Explorer(maker.Shape(), TopAbs_SOLID)
        while explorer.More():
            cells.append(TopoDS.Solid(explorer.Current()))
            explorer.Next()
        inside = lambda cell, points: any(
            BRepClass3d_SolidClassifier(cell, gp_Pnt(*point), 1e-6).State() == TopAbs_IN for point in points)
        if self.cell_points:
            cells = [cell for cell in cells if inside(cell, self.cell_points)]
        # angeklickte Zellen fallen heraus (wie Fusions Häkchen in der Zellenliste); sie bleiben zur Anzeige
        # als „Geister“ erhalten, damit man sie in der Vorschau wieder anklicken kann
        state.ghosts[self.name] = [cell for cell in cells if inside(cell, self.remove_points)]
        cells = [cell for cell in cells if not inside(cell, self.remove_points)]
        if not cells:
            raise FeatureError("Keine geschlossene Zelle gefunden bzw. angeklickt")
        result = cells[0]
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        for cell in cells[1:]:
            result = ft._boolean(BRepAlgoAPI_Fuse, result, cell)
        state.add_body(result)


def _area(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, properties)
    return properties.Mass()


# In das Verzeichnis für das Laden gespeicherter Entwürfe eintragen
for _feature_class in (SurfaceExtrude, SurfaceRevolve, Patch, OffsetSurface, Thicken, Stitch, SurfaceSweep, SurfaceLoft, Unstitch,
                       TrimSurface, ExtendSurface, BoundaryFill):
    ft.FEATURE_CLASSES[_feature_class.KIND] = _feature_class
