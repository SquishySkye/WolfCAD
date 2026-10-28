# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Runde 82: Teilen mit (auch gekrümmten) Körperflächen und Flächenkörpern, die über ihre Ränder verlängert werden."""
import pytest
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

from wolfcad.core import features as ft
from wolfcad.core import solids
from wolfcad.core.timeline import Timeline


def _plate_and_bolt() -> Timeline:
    """Platte 40 × 30 × 5 und darüber ein kurzer Bolzen (Ø 8), der die Platte nicht berührt."""
    timeline = Timeline()
    timeline.add(solids.Box(length=40, width=30, height=5, operation="Neuer Körper"))
    timeline.add(solids.Cylinder(diameter=8, height=6, offset=10, operation="Neuer Körper"))
    return timeline


def _side_of(shape) -> list:
    """Merkpunkt der Mantelfläche eines Zylinders."""
    side = next(face for face in ft.faces_of(shape) if BRepAdaptor_Surface(face).GetType() == GeomAbs_Cylinder)
    return list(ft.face_point(side))


def test_curved_face_of_another_body_splits_when_extended():
    timeline = _plate_and_bolt()
    plate, bolt = list(timeline.state.bodies)
    timeline.add(solids.SplitBody(body=plate, tool_body=bolt, tool_points=[_side_of(timeline.state.bodies[bolt])]))
    assert not timeline.errors, timeline.errors
    volumes = sorted(ft._shape_volume(shape) for shape in timeline.state.bodies.values())
    # ausgestanzter Kreis (π · 4² · 5), Bolzen (π · 4² · 6), Rest der Platte
    assert volumes == pytest.approx([251.327, 301.593, 6000.0 - 251.327], rel=1e-4)


def test_without_extension_a_face_that_misses_reports_it():
    timeline = _plate_and_bolt()
    plate, bolt = list(timeline.state.bodies)
    timeline.add(solids.SplitBody(body=plate, tool_body=bolt, tool_points=[_side_of(timeline.state.bodies[bolt])],
                                  extend=False))
    assert timeline.errors and "verlängern" in str(next(iter(timeline.errors.values())))


def test_surface_body_tool_is_extended_only_when_needed():
    state = ft.BuildState()
    state.bodies["Platte"] = BRepPrimAPI_MakeBox(40.0, 30.0, 5.0).Shape()
    # kleines Flächenstück bei x = 10, das nur in die Ecke der Platte ragt (nicht ganz durch)
    state.surfaces["Fläche1"] = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(10, 0, 0), gp_Dir(1, 0, 0)),
                                                        -1.0, 4.0, -2.0, 6.0).Face()
    solids.SplitBody(body="Platte", tool="Fläche1").apply(state)
    assert len(state.bodies) == 2
    assert sorted(ft._shape_volume(shape) for shape in state.bodies.values()) == pytest.approx([1500.0, 4500.0])
    # mit Flächenpunkten des Flächenkörpers geht es genauso
    again = ft.BuildState()
    again.bodies["Platte"] = BRepPrimAPI_MakeBox(40.0, 30.0, 5.0).Shape()      # wieder die ganze Platte
    again.surfaces["Fläche1"] = state.surfaces["Fläche1"]
    point = list(ft.face_point(ft.faces_of(state.surfaces["Fläche1"])[0]))
    solids.SplitBody(body="Platte", tool_body="Fläche1", tool_points=[point]).apply(again)
    assert len(again.bodies) == 2


def test_planar_face_of_other_body_splits_like_a_plane_and_file_roundtrip():
    timeline = Timeline()
    timeline.add(solids.Box(length=40, width=30, height=5, operation="Neuer Körper"))
    timeline.add(solids.Box(center_x=10, length=10, width=10, height=20, offset=10, operation="Neuer Körper"))
    plate, post = list(timeline.state.bodies)
    # linke Seitenfläche des Pfostens über der Platte (x = 5) teilt sie verlängert
    left = next(face for face in ft.faces_of(timeline.state.bodies[post])
                if abs(ft.face_point(face)[0] - 5.0) < 1e-6)
    timeline.add(solids.SplitBody(body=plate, tool_body=post, tool_points=[list(ft.face_point(left))]))
    assert not timeline.errors, timeline.errors
    assert len(timeline.state.bodies) == 3
    copy = Timeline.from_document(timeline.to_document())       # Speichern und Laden behalten die Trennfläche
    assert len(copy.state.bodies) == 3 and copy.features[-1].tool_points


# --- Werkzeuge mit „Alle“ als Vorgabe, Begrenzungsfüllung mit Ebenen ---------------------------------------------

def test_emboss_with_all_bodies_finds_the_body_of_the_face():
    from wolfcad.core import more_tools as mt
    from wolfcad.core import sketch as sk
    timeline = Timeline()
    timeline.add(solids.Box(length=50, width=30, height=10, operation="Neuer Körper"))
    timeline.add(sk.Sketch(name="Logo", plane="XY", offset=10.0, entities=[sk.circle(-10, 0, 6)]))
    body = next(iter(timeline.state.bodies))
    top = max((ft.face_point(face) for face in ft.faces_of(timeline.state.bodies[body])), key=lambda p: p[2])
    before = ft._shape_volume(timeline.state.bodies[body])
    timeline.add(mt.Emboss(sketch="Logo", face_points=[list(top)], depth=2.0))     # body bleibt „Alle“
    assert not timeline.errors, timeline.errors
    assert timeline.features[-1].body == ft.ALL_BODIES                              # das Feature bleibt unverändert
    assert ft._shape_volume(timeline.state.bodies[body]) == pytest.approx(before + 3.14159265 * 36 * 2, rel=1e-4)


def test_boundary_fill_accepts_construction_planes():
    from wolfcad.core import sketch as sk
    from wolfcad.core import surfaces as sf
    timeline = Timeline()
    timeline.add(sk.Sketch(name="Wand", plane="XY", entities=[sk.circle(0, 0, 10)]))
    timeline.add(sf.SurfaceExtrude(sketch="Wand", distance=30.0))
    timeline.add(sk.ConstructionPlane(plane="XY", offset=5.0))
    timeline.add(sk.ConstructionPlane(plane="XY", offset=15.0))
    tools = [*timeline.state.surfaces, *timeline.state.planes]
    timeline.add(sf.BoundaryFill(tools=tools, cell_points=[[0.0, 0.0, 10.0]]))
    assert not timeline.errors, timeline.errors
    volumes = [ft._shape_volume(shape) for shape in timeline.state.bodies.values()]
    assert volumes == pytest.approx([3.14159265 * 100 * 10], rel=1e-3)      # Zylinderstück zwischen den Ebenen


# --- Fusion-Import: Parameterkopf neuerer Dateien, beteiligte Körper ----------------------------------------------

def _wide(text: str) -> bytes:
    import struct
    return struct.pack("<I", len(text)) + text.encode("utf-16-le")


def test_parameter_with_longer_header_of_newer_files():
    import struct
    from wolfcad.f3d import segment
    for header in segment._PARAMETER_HEADERS:
        body = (_wide("7 mm") + bytes(9) + _wide("AlongDistance") + bytes(4) + _wide("mm") + _wide("d1")
                + struct.pack("<d", 0.7) + bytes(36) + b"\x01" + struct.pack("<Q", 42) + bytes(2))
        bulk = bytes(header) + body
        owner, parameter = segment._parameter_at(bulk, 0, {42})
        assert owner == 42 and parameter.role == "AlongDistance" and parameter.value == pytest.approx(0.7)
        assert parameter.expression == "7 mm" and parameter.name == "d1"


def test_fusion_boolean_only_touches_participants():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    state = ft.BuildState()
    state.add_body(BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 10.0, 10.0, 10.0).Shape(), 1)       # Herkunft 1
    state.add_body(BRepPrimAPI_MakeBox(gp_Pnt(20, 0, 0), 10.0, 10.0, 10.0).Shape(), 2)      # Herkunft 2
    cutter = BRepPrimAPI_MakeBox(gp_Pnt(5, 2, 2), 20.0, 4.0, 4.0).Shape()                  # trifft beide
    ft._combine(state, cutter, "Ausschneiden", participants={2})
    volumes = {state.body_origin[name]: ft._shape_volume(shape) for name, shape in state.bodies.items()}
    assert volumes[1] == pytest.approx(1000.0)                  # nicht beteiligt: unverändert
    assert volumes[2] == pytest.approx(1000.0 - 5 * 4 * 4)


# --- Clips der Erklärkarten ----------------------------------------------------------------------------------------

# Befehle ohne Vorführung im Modell (Datei, Programm, Ansicht) – alle übrigen Werkzeuge haben ein Video
WITHOUT_CLIP = {"Neu", "Öffnen", "Speichern", "Import", "Zeichnung", "Export", "3D-Druck", "Rückgängig", "Wiederholen",
                "Physisches Material", "Alle berechnen", "Parameter", "Netz einfügen", "Netz als STL speichern",
                "Zuschnitt (DXF)", "DXF/SVG einfügen", "Eigenschaften", "Einpassen", "Raster", "Einstellungen",
                "Abspielen", "Hilfskörper", "Ansichten", "Über WolfCAD"}


def test_every_tool_in_the_menus_has_a_clip():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from wolfcad.gui import tool_hints
    from wolfcad.gui.clip_recorder import clip_path
    missing = [tool for tool in tool_hints.MODEL_HINTS if tool not in WITHOUT_CLIP and clip_path("modell", tool) is None]
    missing += [tool for tool in tool_hints.SKETCH_HINTS if clip_path("skizze", tool) is None]
    assert not missing, missing


# --- Verschieben: um jede Achse drehen ------------------------------------------------------------------------------

def test_move_turns_about_every_axis_about_the_body_centre():
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    timeline = Timeline()
    timeline.add(solids.Box(center_x=30, length=40, width=10, height=4, operation="Neuer Körper"))
    timeline.add(solids.MoveBody(pivot="Körpermitte", angle_x=90.0, angle_y=90.0))
    assert not timeline.errors
    shape = next(iter(timeline.state.bodies.values()))
    bounds = solids._bounds(shape)
    sizes = [round(bounds[i + 3] - bounds[i], 6) for i in range(3)]
    # 40 × 10 × 4: um X gedreht → 40 × 4 × 10, danach um Y → 10 × 4 × 40
    assert sizes == pytest.approx([10.0, 4.0, 40.0])
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    centre = props.CentreOfMass()
    assert (centre.X(), centre.Y(), centre.Z()) == pytest.approx((30.0, 0.0, 2.0), abs=1e-6)   # an Ort und Stelle


def test_old_move_steps_keep_their_single_rotation():
    timeline = Timeline()
    timeline.add(solids.Box(center_x=30, length=10, width=10, height=10, operation="Neuer Körper"))
    timeline.add(solids.MoveBody(axis="Z", angle=90.0))          # Vorgabe Drehpunkt „Ursprung“ wie alte Dateien
    bounds = solids._bounds(next(iter(timeline.state.bodies.values())))
    assert (bounds[0] + bounds[3]) / 2 == pytest.approx(0.0, abs=1e-6)
    assert (bounds[1] + bounds[4]) / 2 == pytest.approx(30.0)
