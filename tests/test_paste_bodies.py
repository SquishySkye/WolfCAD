# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für Körper kopieren/einfügen: der kopierte Inhalt ist eigenständig und lässt sich in einen anderen
Entwurf einfügen, verschieben, speichern und wieder laden."""

import json

from test_solids import build, measure

from wolfcad.core.features import feature_from_dict
from wolfcad.core.solids import Box, PasteBodies, Sphere, copy_bodies
from wolfcad.core.timeline import Timeline


def _content():
    """Zwei Körper aus „Entwurf A“, einer davon gefärbt, über JSON wie in der Zwischenablage."""
    source = build(Box(length=10, width=10, height=10), Sphere(diameter=6, center_x=30))
    state = source.state.copy()
    state.colors["Körper2"] = "Rot"
    return json.loads(json.dumps(copy_bodies(state, ["Körper1", "Körper2"], "Entwurf A"))), source


def test_paste_into_other_design_keeps_shapes_and_colours():
    content, source = _content()
    target = Timeline()
    target.add(Box(length=4, width=4, height=4))        # Entwurf B hat schon einen eigenen Körper
    target.add(PasteBodies(data=content["data"], bodies=content["bodies"], centre=content["centre"]))
    state = target.state
    assert list(state.bodies) == ["Körper1", "Körper2", "Körper3"]
    for pasted, original in (("Körper2", "Körper1"), ("Körper3", "Körper2")):
        volume, centre = measure(state.bodies[pasted])
        expected_volume, expected_centre = measure(source.state.bodies[original])
        assert abs(volume - expected_volume) < 1e-6
        assert all(abs(a - b) < 1e-6 for a, b in zip(centre, expected_centre))
    assert state.colors.get("Körper3") == "Rot"
    assert content["source"] == "Entwurf A"


def test_pasted_bodies_move_and_survive_saving():
    content, _source = _content()
    paste = PasteBodies(data=content["data"], bodies=content["bodies"], centre=content["centre"], move_z=15)
    # wie beim Speichern und Laden eines Entwurfs: über JSON und das Verzeichnis der Feature-Typen
    loaded = feature_from_dict(json.loads(json.dumps(paste.to_dict())))
    state = build(loaded).state
    _volume, centre = measure(state.bodies["Körper1"])
    assert abs(centre[2] - 20.0) < 1e-6         # Quadermitte 5 + Verschiebung 15
    assert "data" not in [field.name for field in loaded.parameters()]


def test_copy_without_bodies_is_refused():
    import pytest
    from wolfcad.core.features import FeatureError
    with pytest.raises(FeatureError):
        copy_bodies(Timeline().state, ["gibt es nicht"])
