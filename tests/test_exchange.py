# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für den Export nach STEP/STL und den STEP-Import."""

import math

import pytest

from wolfcad.core import exchange
from wolfcad.core.solids import Box, Cylinder, Hole, ImportedStep, MoveBody, _volume
from wolfcad.core.timeline import Timeline


def model() -> Timeline:
    timeline = Timeline()
    timeline.add(Box(length=40, width=20, height=10))
    timeline.add(Cylinder(center_x=60, diameter=10, height=30, operation="Neuer Körper"))
    return timeline


def test_step_round_trip_keeps_bodies_and_volume(tmp_path):
    timeline = model()
    path = tmp_path / "teil.step"
    exchange.export(list(timeline.state.bodies.values()), path)
    solids = exchange.read_step(path)
    assert len(solids) == 2
    assert sum(_volume(s) for s in solids) == pytest.approx(8000 + math.pi * 25 * 30, rel=1e-6)
    # Als Feature importiert sind es eigene Körper, die sich weiter bearbeiten lassen
    imported = Timeline()
    imported.add(ImportedStep(source=str(path)))
    imported.add(MoveBody(move_z=5))
    imported.add(Hole(diameter=4, offset=20))
    assert not imported.errors and len(imported.state.bodies) == 2
    restored = Timeline.from_document(imported.to_document())
    assert len(restored.state.bodies) == 2


def test_stl_is_written_as_binary_mesh(tmp_path):
    path = tmp_path / "teil.stl"
    exchange.export(list(model().state.bodies.values()), path)
    data = path.read_bytes()
    triangles = int.from_bytes(data[80:84], "little")
    assert triangles > 12 and len(data) == 84 + 50 * triangles     # Aufbau einer binären STL-Datei


def test_errors_are_reported(tmp_path):
    with pytest.raises(exchange.ExchangeError):
        exchange.export([], tmp_path / "leer.step")
    with pytest.raises(exchange.ExchangeError):
        exchange.export(list(model().state.bodies.values()), tmp_path / "teil.obj")
    broken = tmp_path / "kaputt.step"
    broken.write_text("das ist keine STEP-Datei")
    with pytest.raises(exchange.ExchangeError):
        exchange.read_step(broken)
    missing = Timeline()
    missing.add(ImportedStep(source=str(tmp_path / "fehlt.step")))
    assert "nicht (mehr) vorhanden" in missing.errors[0]


def test_step_keeps_colours(tmp_path):
    from wolfcad.core.features import face_point, faces_of
    timeline = model()
    shapes = list(timeline.state.bodies.values())
    top = next(f for f in faces_of(shapes[0]) if abs(face_point(f)[2] - 10) < 1e-6)
    path = tmp_path / "bunt.step"
    exchange.export(shapes, path, colours=[((0.2, 0.5, 0.9), [(top, (0.8, 0.2, 0.2))]), (None, [])])
    import re
    text = path.read_text()
    # die Datei speichert die Farben mit einfacher Genauigkeit, deshalb gerundet vergleichen
    written = sorted(tuple(round(float(v), 4) for v in match.groups())
                     for match in re.finditer(r"COLOUR_RGB\('',([\d.E+-]+),([\d.E+-]+),([\d.E+-]+)\)", text))
    assert written == [(0.2, 0.5, 0.9), (0.8, 0.2, 0.2)]
    assert len(exchange.read_step(path)) == 2                     # die Körper bleiben lesbar
    plain = tmp_path / "grau.step"
    exchange.export(shapes, plain, colours=[(None, []), (None, [])])
    assert "COLOUR_RGB" not in plain.read_text()


def test_step_import_reads_colours(tmp_path):
    from wolfcad.core.features import face_point, faces_of
    from wolfcad.core.solids import BODY_COLORS, nearest_colour
    timeline = model()
    shapes = list(timeline.state.bodies.values())
    top = next(f for f in faces_of(shapes[0]) if abs(face_point(f)[2] - 10) < 1e-6)
    path = tmp_path / "bunt.step"
    exchange.export(shapes, path, colours=[(BODY_COLORS["Blau"], [(top, BODY_COLORS["Rot"])]), (None, [])])
    imported = Timeline()
    imported.add(ImportedStep(source=str(path)))
    state = imported.state
    assert state.colors == {"Körper1": "Blau"}
    (point, colour), = state.face_colors["Körper1"]
    assert colour == "Rot" and point == pytest.approx(face_point(top), abs=1e-6)
    assert nearest_colour((0.9, 0.1, 0.1)) == "#e61a1a"            # fremde Farbtöne bleiben genau erhalten
    # eine Datei ohne Farben bleibt ohne Farben
    plain = tmp_path / "grau.step"
    exchange.export(shapes, plain)
    grey = Timeline()
    grey.add(ImportedStep(source=str(plain)))
    assert not grey.state.colors and not grey.state.face_colors and len(grey.state.bodies) == 2


def test_foreign_step_colours_are_kept_exactly(tmp_path):
    from wolfcad.core.solids import colour_rgb, nearest_colour
    timeline = model()
    shapes = list(timeline.state.bodies.values())
    path = tmp_path / "fremd.step"
    exchange.export(shapes, path, colours=[((0.1, 0.9, 0.7), []), (None, [])])     # kein Farbton des Programms
    imported = Timeline()
    imported.add(ImportedStep(source=str(path)))
    colour = imported.state.colors["Körper1"]
    assert colour.startswith("#") and colour_rgb(colour) == pytest.approx((0.1, 0.9, 0.7), abs=0.005)
    assert nearest_colour(colour_rgb("Rot")) == "Rot" and colour_rgb("unbekannt") is None
