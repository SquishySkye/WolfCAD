# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für Benutzerparameter und Ausdrücke."""

import math

import pytest

from wolfcad.core import parameters as pm
from wolfcad.core.solids import Box
from wolfcad.core.timeline import Timeline


def volume(timeline) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(timeline.state.body, properties)
    return properties.Mass()


def test_expressions():
    assert pm.evaluate("2 + 3 * 4") == 14
    assert pm.evaluate("(2 + 3) * 4 mm") == 20
    assert pm.evaluate("2,5 * 2") == 5                  # Dezimalkomma
    assert pm.evaluate("2^3") == 8
    assert pm.evaluate("breite / 2 - 1", {"breite": 40}) == 19
    assert pm.evaluate("sqrt(16) + abs(-2)") == 6
    assert pm.evaluate("sin(30)") == pytest.approx(0.5)  # Winkel in Grad
    assert pm.evaluate("2 * pi") == pytest.approx(2 * math.pi)
    assert pm.is_number("12,5") and not pm.is_number("breite")


@pytest.mark.parametrize("text", ["", "1 +", "foo", "1 / 0", "__import__('os')", "breite.real", "[1, 2]",
                                  "sqrt(-1)", "open('x')", "1 if 1 else 2"])
def test_bad_expressions_are_rejected(text):
    with pytest.raises(pm.ParameterError):
        pm.evaluate(text, {"breite": 40})


def test_parameters_may_refer_to_each_other():
    values, errors = pm.resolve({"hoehe": "breite / 4", "breite": "2 * basis", "basis": "20"})
    assert values == {"basis": 20, "breite": 40, "hoehe": 10} and not errors
    values, errors = pm.resolve({"a": "b + 1", "b": "a + 1", "c": "5", "d": "fehlt * 2", "sin": "3", "1x": "2"})
    assert values == {"c": 5}
    assert "im Kreis" in errors["a"] and "im Kreis" in errors["b"]
    assert "Unbekannter Parameter 'fehlt'" in errors["d"]
    assert "vergeben" in errors["sin"] and "gültiger Name" in errors["1x"]


def test_feature_follows_parameter():
    timeline = Timeline()
    timeline.set_parameters({"breite": "40", "hoehe": "breite / 4"})
    timeline.add(Box(length=1, width=20, height=1))
    timeline.set_expression(0, "length", "breite")
    timeline.set_expression(0, "height", "hoehe")
    assert not timeline.errors
    assert (timeline.features[0].length, timeline.features[0].height) == (40, 10)
    assert volume(timeline) == pytest.approx(40 * 20 * 10)
    # Parameter ändern: das Modell folgt
    timeline.set_parameters({"breite": "60", "hoehe": "breite / 4"})
    assert volume(timeline) == pytest.approx(60 * 20 * 15)
    # Bindung lösen: der letzte Wert bleibt als feste Zahl stehen
    timeline.set_expression(0, "height", None)
    timeline.set_parameters({"breite": "20", "hoehe": "1"})
    assert volume(timeline) == pytest.approx(20 * 20 * 15)


def test_missing_parameter_is_reported_and_saved_files_keep_parameters():
    timeline = Timeline()
    timeline.set_parameters({"breite": "40"})
    timeline.add(Box(length=10, width=20, height=5))
    timeline.set_expression(0, "length", "breite * 2")
    restored = Timeline.from_document(timeline.to_document())
    assert restored.parameters == {"breite": "40"}
    assert restored.features[0].expressions == {"length": "breite * 2"} and restored.features[0].length == 80
    restored.set_parameters({})
    assert "Unbekannter Parameter 'breite'" in restored.errors[0]
    # Dateien ohne Parameter (älterer Stand) lassen sich weiter laden
    document = timeline.to_document()
    del document["parameters"]
    for feature in document["features"]:
        del feature["expressions"]
    assert Timeline.from_document(document).features[0].expressions == {}


def test_rename_only_touches_whole_names():
    assert pm.rename("breite / 2 + breite_innen - sqrt(breite)", "breite", "b") == "b / 2 + breite_innen - sqrt(b)"
    assert pm.rename("abs(x) + abs", "abs", "betrag") == "abs(x) + betrag"      # Funktionen bleiben


def test_sketch_dimension_follows_parameter():
    from wolfcad.core import constraints as cs
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import Extrude
    timeline = Timeline()
    timeline.set_parameters({"breite": "40"})
    rules = [[cs.HORIZONTAL, 0, -1, 0.0], [cs.VERTICAL, 1, -1, 0.0], [cs.HORIZONTAL, 2, -1, 0.0],
             [cs.VERTICAL, 3, -1, 0.0], [cs.LENGTH, 0, -1, 40.0, "breite"], [cs.LENGTH, 1, -1, 20.0, "breite / 2"]]
    timeline.add(sk.Sketch(name="Skizze1", entities=sk.rectangle(0, 0, 40, 20), constraints=rules))
    timeline.add(Extrude(sketch="Skizze1", distance=10))
    assert not timeline.errors and volume(timeline) == pytest.approx(40 * 20 * 10)
    timeline.set_parameters({"breite": "60"})
    assert not timeline.errors and volume(timeline) == pytest.approx(60 * 30 * 10)
    assert timeline.features[0].constraints[4][3:] == [60.0, "breite"]
    # Speichern und Laden behält den Ausdruck; fehlt der Parameter, meldet die Skizze das
    restored = Timeline.from_document(timeline.to_document())
    assert restored.features[0].constraints[5] == [cs.LENGTH, 1, -1, 30.0, "breite / 2"]
    restored.set_parameters({})
    assert "Unbekannter Parameter 'breite'" in restored.errors[0]
    # Wird ein Element gelöscht, behalten die übrigen Maße ihren Ausdruck
    assert cs.without_entity(rules, 0)[-1] == [cs.LENGTH, 0, -1, 30.0, "breite / 2"]
