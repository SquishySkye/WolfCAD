# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Flächen (Fusion-Reiter FLÄCHE): Flächenkörper extrudieren, drehen, flicken, versetzen, aufdicken, vernähen."""
import math

import pytest
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from wolfcad.core import sketch as sk
from wolfcad.core import surfaces as sf
from wolfcad.core.timeline import Timeline


def area(shape) -> float:
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, properties)
    return properties.Mass()


def volume(shape) -> float:
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


def test_extrude_open_line_patch_thicken_and_stitch():
    timeline = Timeline()
    # offener Linienzug (L-Form) → zwei ebene Flächen, kein Volumen
    timeline.add(sk.Sketch(name="L", plane="XY", entities=[sk.line(0, 0, 20, 0), sk.line(20, 0, 20, 10)]))
    timeline.add(sf.SurfaceExtrude(name="E", sketch="L", distance=5))
    assert not timeline.errors, timeline.errors
    assert list(timeline.state.surfaces) == ["Flächenkörper1"] and not timeline.state.bodies
    assert area(timeline.state.surfaces["Flächenkörper1"]) == pytest.approx((20 + 10) * 5)
    # Aufdicken: aus der Fläche wird ein Volumenkörper, die Fläche verschwindet
    timeline.add(sf.Thicken(name="A", surface="Flächenkörper1", thickness=1))
    assert not timeline.errors, timeline.errors
    assert not timeline.state.surfaces and len(timeline.state.bodies) == 1
    # 30 mm Wand × 5 mm hoch × 1 mm dick, an der Innenecke 1 × 1 × 5 weniger
    assert volume(next(iter(timeline.state.bodies.values()))) == pytest.approx(145, rel=1e-6)
    # Flicken + Fläche drehen: Zylindermantel mit Deckel und Boden, vernäht zu einem Volumenkörper
    timeline.add(sk.Sketch(name="Mantel", plane="XZ", entities=[sk.line(10, 0, 10, 20)]))
    timeline.add(sf.SurfaceRevolve(name="D", sketch="Mantel", axis=sf.REVOLVE_AXES[1], angle=360))
    timeline.add(sk.Sketch(name="Boden", plane="XY", entities=[sk.circle(0, 0, 10)]))
    timeline.add(sf.Patch(name="P1", sketch="Boden"))
    timeline.add(sk.Sketch(name="Deckel", plane="XY", offset=20.0, entities=[sk.circle(0, 0, 10)]))
    timeline.add(sf.Patch(name="P2", sketch="Deckel"))
    assert not timeline.errors, timeline.errors
    names = list(timeline.state.surfaces)
    assert len(names) == 3
    timeline.add(sf.Stitch(name="V", surfaces=names))
    assert not timeline.errors, timeline.errors
    assert not timeline.state.surfaces
    solids = list(timeline.state.bodies.values())
    assert any(volume(s) == pytest.approx(math.pi * 100 * 20, rel=1e-3) for s in solids)
    # Speichern und Laden
    again = Timeline.from_document(timeline.to_document())
    assert not again.errors and len(again.state.bodies) == 2


def test_offset_and_errors():
    timeline = Timeline()
    timeline.add(sk.Sketch(name="Q", plane="XY", entities=sk.rectangle(0, 0, 10, 10)))
    timeline.add(sf.Patch(name="P", sketch="Q"))
    timeline.add(sf.OffsetSurface(name="V", surface="Flächenkörper1", distance=3))
    assert not timeline.errors, timeline.errors
    from wolfcad.core.sheetmetal import _bounds
    # Richtung hängt an der Ausrichtung der Fläche (in Fusion dreht man sie am Pfeil um)
    assert abs(_bounds(timeline.state.surfaces["Flächenkörper2"])[2]) == pytest.approx(3, abs=1e-6)
    timeline.add(sf.Thicken(name="A", surface="gibt es nicht"))
    assert "nicht (mehr) vorhanden" in str(timeline.errors)


def test_sweep_loft_unstitch_trim_extend_and_boundary_fill():
    from wolfcad.core import solids
    timeline = Timeline()
    # Sweep: offene Linie (Profil) entlang einer Linie → Rechteckfläche 10 × 30
    timeline.add(sk.Sketch(name="Profil", plane="XZ", entities=[sk.line(0, 0, 10, 0)]))
    timeline.add(sk.Sketch(name="Pfad", plane="XY", entities=[sk.line(0, 0, 0, 30)]))
    timeline.add(sf.SurfaceSweep(name="S", sketch="Profil", path="Pfad"))
    assert not timeline.errors, timeline.errors
    assert area(timeline.state.surfaces["Flächenkörper1"]) == pytest.approx(10 * 30, rel=1e-6)
    # Erhebung zwischen zwei Kreisen (Mantel eines Kegelstumpfs)
    timeline.add(sk.Sketch(name="K1", plane="XY", entities=[sk.circle(50, 0, 10)]))
    timeline.add(sk.Sketch(name="K2", plane="XY", offset=20.0, entities=[sk.circle(50, 0, 5)]))
    timeline.add(sf.SurfaceLoft(name="E", sketch="K1", sketch2="K2"))
    assert not timeline.errors, timeline.errors
    side = math.sqrt(20 ** 2 + 5 ** 2)
    assert area(timeline.state.surfaces["Flächenkörper2"]) == pytest.approx(math.pi * (10 + 5) * side, rel=1e-3)
    # Verlängern: Rechteckfläche 10 × 30 um 2 mm an allen Rändern → 14 × 34
    timeline.add(sf.ExtendSurface(name="V", surface="Flächenkörper1", distance=2))
    assert not timeline.errors, timeline.errors
    assert area(timeline.state.surfaces["Flächenkörper1"]) == pytest.approx(14 * 34, rel=1e-3)
    # Trennen: ein Quader zerfällt in sechs Flächen
    timeline.add(solids.Box(name="Q", length=10, width=10, height=10, center_x=-50))
    quader = [name for name in timeline.state.bodies][-1]
    timeline.add(sf.Unstitch(name="T", surface=quader))
    assert not timeline.errors, timeline.errors
    assert quader not in timeline.state.bodies and len(timeline.state.surfaces) == 2 + 6
    # Stutzen: Rechteckfläche an einer Ebenen-Fläche (Flicken bei z = 0, x … ) teilen, größtes Stück bleibt
    trim = Timeline()
    trim.add(sk.Sketch(name="W", plane="XY", entities=[sk.line(0, 0, 40, 0)]))
    trim.add(sf.SurfaceExtrude(name="Wand", sketch="W", distance=20))                       # Wand 40 × 20
    trim.add(sk.Sketch(name="Q", plane="YZ", offset=10.0, entities=sk.rectangle(-30, -30, 30, 30)))
    trim.add(sf.Patch(name="Messer", sketch="Q"))                                            # Ebene bei x = 10
    trim.add(sf.TrimSurface(name="St", surface="Flächenkörper1", tool="Flächenkörper2"))
    assert not trim.errors, trim.errors
    assert area(trim.state.surfaces["Flächenkörper1"]) == pytest.approx(30 * 20, rel=1e-6)
    # angeklickt: das große Stück (Punkt bei x = 25) fällt weg, das kleine bleibt
    trim.update(len(trim.features) - 1, remove_points=[[25.0, 0.0, 10.0]])
    assert not trim.errors, trim.errors
    assert area(trim.state.surfaces["Flächenkörper1"]) == pytest.approx(10 * 20, rel=1e-6)
    # Begrenzungsfüllung: zwei Flicken (Boden, Deckel) und ein gedrehter Mantel schließen eine Zelle ein
    fill = Timeline()
    fill.add(sk.Sketch(name="M", plane="XZ", entities=[sk.line(10, -5, 10, 25)]))
    fill.add(sf.SurfaceRevolve(name="D", sketch="M", angle=360))
    fill.add(sk.Sketch(name="B", plane="XY", entities=[sk.circle(0, 0, 15)]))
    fill.add(sf.Patch(name="P1", sketch="B"))
    fill.add(sk.Sketch(name="T", plane="XY", offset=20.0, entities=[sk.circle(0, 0, 15)]))
    fill.add(sf.Patch(name="P2", sketch="T"))
    fill.add(sf.BoundaryFill(name="F", tools=list(fill.state.surfaces)))
    assert not fill.errors, fill.errors
    assert volume(next(iter(fill.state.bodies.values()))) == pytest.approx(math.pi * 100 * 20, rel=1e-3)
    # Richtung umkehren statt negativer Werte
    flip = Timeline()
    flip.add(sk.Sketch(name="Q", plane="XY", entities=sk.rectangle(0, 0, 10, 10)))
    flip.add(sf.Patch(name="P", sketch="Q"))
    flip.add(sf.OffsetSurface(name="A", surface="Flächenkörper1", distance=3))
    flip.add(sf.OffsetSurface(name="B", surface="Flächenkörper1", distance=3, flip=True))
    from wolfcad.core.sheetmetal import _bounds
    zs = sorted(_bounds(flip.state.surfaces[name])[2] for name in ("Flächenkörper2", "Flächenkörper3"))
    assert zs == pytest.approx([-3, 3], abs=1e-6)


def test_boundary_fill_leaves_out_clicked_cells():
    """Quader + Trennfläche in der Mitte = zwei Zellen; ein Punkt in der linken nimmt sie heraus."""
    from wolfcad.core import solids
    timeline = Timeline()
    timeline.add(solids.Box(name="Q", length=20, width=10, height=10))           # x −10 … 10
    timeline.add(sk.Sketch(name="M", plane="YZ", entities=sk.rectangle(-20, -20, 20, 20)))
    timeline.add(sf.Patch(name="Mitte", sketch="M"))                           # Ebene x = 0
    tools = [next(iter(timeline.state.bodies)), "Flächenkörper1"]
    timeline.add(sf.BoundaryFill(name="F", tools=tools))
    assert not timeline.errors, timeline.errors
    filled = [name for name in timeline.state.bodies if name not in tools]
    assert volume(timeline.state.bodies[filled[0]]) == pytest.approx(2000, rel=1e-6)
    timeline.update(len(timeline.features) - 1, remove_points=[[-5.0, 0.0, 5.0]])
    assert not timeline.errors, timeline.errors
    filled = [name for name in timeline.state.bodies if name not in tools]
    assert volume(timeline.state.bodies[filled[0]]) == pytest.approx(1000, rel=1e-6)
