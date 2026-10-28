# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Runde 78 (Nutzerentwurf 2026-10-27): fast deckungsgleiche Skizzenkurven, Geometriestufe je Schritt, Hinweis auf
ungültige Körper in älteren Entwürfen."""
from wolfcad.core import features as ft, sketch, solids
from wolfcad.core.timeline import GEOMETRY_LEVEL, Timeline

# wie Skizze16 des Nutzers: projizierter Kreis r = 4, darauf (9 nm daneben) ein gezeichneter, ein Steg nach außen,
# der Ring r = 4,75 und ein äußerer Kreis
_RING = [["Kreis", 0.0, 0.0, 4.0], ["Linie", 0.0, 4.0, 0.0, 7.5], ["Kreis", 9e-6, 9e-6, 3.999991],
         ["Kreis", 9e-6, 9e-6, 4.75], ["Kreis", 0.0, 0.0, 7.5]]


def _ring_timeline(level: int) -> Timeline:
    timeline = Timeline()
    timeline.geometry_level = level
    timeline.features = [sketch.Sketch(name="S", entities=[list(entity) for entity in _RING]),
                         ft.Extrude(name="Extrusion1", sketch="S", distance=2.0, regions=ft.PROFILE_MODES[2],
                                    region_points=[[4.4, 0.0, 0.0]], operation="Neuer Körper")]
    timeline.marker = 2
    timeline.recompute()
    return timeline


def test_snapping_merges_near_duplicates_only():
    snapped, moved = sketch.snapped_entities(_RING)
    assert moved and snapped[2] is None                     # der fast gleiche Kreis entfällt
    assert snapped[3][1:4] == [0.0, 0.0, 4.75]              # der Ring sitzt genau auf dem Mittelpunkt
    exact, moved = sketch.snapped_entities([["Kreis", 0.0, 0.0, 4.0], ["Kreis", 0.0, 0.0, 4.0]])
    assert not moved and exact[1] is None                   # exakte Doppelung: nichts verschoben
    apart, moved = sketch.snapped_entities([["Kreis", 0.0, 0.0, 4.0], ["Kreis", 0.0, 0.0, 4.01]])
    assert not moved and apart[1] is not None               # 10 µm Abstand: zwei Kreise


def test_current_level_gives_valid_ring():
    timeline = _ring_timeline(GEOMETRY_LEVEL)
    body = timeline.state.bodies["Körper1"]
    assert not timeline.errors and ft.is_valid(body) and len(ft.faces_of(body)) == 4


def test_old_level_unchanged_until_step_is_upgraded():
    timeline = _ring_timeline(1)
    assert not ft.is_valid(timeline.state.bodies["Körper1"])        # altes Verfahren: Steg schlitzt den Ring
    timeline.set_step_level(1, True)                                 # Extrusion – nimmt ihre Skizze mit
    assert [timeline.level_of(f) for f in timeline.features] == [GEOMETRY_LEVEL, GEOMETRY_LEVEL]
    body = timeline.state.bodies["Körper1"]
    assert ft.is_valid(body) and len(ft.faces_of(body)) == 4
    timeline.set_step_level(1, False)
    assert [timeline.level_of(f) for f in timeline.features] == [1, 1]


def test_new_steps_in_old_design_use_current_level_and_are_saved():
    timeline = _ring_timeline(1)
    timeline.add(solids.Box(length=10, width=10, height=10, operation="Neuer Körper"))
    assert timeline.level_of(timeline.features[-1]) == GEOMETRY_LEVEL
    assert timeline.level_of(timeline.features[0]) == 1
    document = timeline.to_document()
    assert document["geometrie"] == 1 and document["features"][-1]["geometrie"] == GEOMETRY_LEVEL
    assert "geometrie" not in document["features"][0]
    again = Timeline.from_document(document)
    assert [again.level_of(f) for f in again.features] == [1, 1, GEOMETRY_LEVEL]


def test_failure_on_invalid_body_names_its_step():
    timeline = _ring_timeline(1)
    timeline.add(ft.Fillet(body="Körper1", radius=0.3))
    message = timeline.errors.get(2, "")
    # die Rundung scheitert am kaputten Ring; die Meldung nennt den Schritt, der ihn erzeugt hat
    assert "Körper1 ist ungültig" in message and "„Extrusion1“" in message
