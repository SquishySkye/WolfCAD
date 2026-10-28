# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Fehlertest über alle Werkzeuge: jedes Feature mit Vorgabewerten auf einem Grundmodell anwenden.

Erlaubt sind Fehlermeldungen für den Nutzer (FeatureError, z. B. „Bitte eine Fläche wählen“), aber keine
Python-Ausnahmen; Speichern und Laden muss denselben Stand ergeben.
"""
import pytest

from wolfcad.core import features as ft
from wolfcad.core import sketch as sk
from wolfcad.core import solids
from wolfcad.core.timeline import Timeline

# Features, die eine Datei oder einen Fusion-Import brauchen, lassen sich ohne Eingaben nicht sinnvoll prüfen
SKIP = {"Import", "Fusion-Import", "Importierte Skizzen", "Fusion-Extrusion", "Fusion-Quader", "Fusion-Zylinder",
        "Fusion-Kugel", "Fusion-Gespeichert", "Fusion-Kombinieren", "Fusion-Entfernen", "Fusion-Verschieben",
        "STEP-Import", "Fusion-Endstand"}


def base() -> Timeline:
    """Quader, eine Skizze mit Kreis und eine offene Linie, ein Flächenkörper."""
    timeline = Timeline()
    timeline.crash_guard = False
    timeline.add(solids.Box(name="Quader", length=40, width=30, height=20))
    timeline.add(sk.Sketch(name="Skizze", plane="XY", offset=20.0, entities=[sk.circle(0, 0, 5)]))
    timeline.add(sk.Sketch(name="Linie", plane="XZ", entities=[sk.line(-30, 0, -30, 25)]))
    return timeline


@pytest.mark.parametrize("kind", sorted(set(ft.FEATURE_CLASSES) - SKIP))
def test_every_feature_with_defaults(kind):
    timeline = base()
    feature = ft.FEATURE_CLASSES[kind]()
    # Felder, die auf Skizzen zeigen, mit der vorhandenen Skizze füllen (sonst prüft man nur „Skizze fehlt“)
    for name in ("sketch", "path"):
        if hasattr(feature, name) and getattr(feature, name) in ("", None):
            setattr(feature, name, "Skizze" if name == "sketch" else "Linie")
    feature.name = "Prüfling"
    try:
        timeline.add(feature)
    except ft.FeatureError:
        return
    # nur Nutzer-Meldungen, keine Programmfehler (die Timeline fängt FeatureError und meldet anderes als
    # „Geometriefehler“ bzw. mit dem Namen der Ausnahme)
    for message in timeline.errors.values():
        assert "Traceback" not in message and "object has no attribute" not in message, message
        assert "unexpected keyword" not in message and "NoneType" not in message, message
        # rohe Meldungen des Geometriekerns sind für Nutzer unverständlich: jedes Werkzeug soll erklären, was fehlt
        assert not message.startswith("Geometriefehler"), message
    again = Timeline.from_document(timeline.to_document())
    assert again.errors.keys() == timeline.errors.keys()
    assert sorted(again.state.bodies) == sorted(timeline.state.bodies)


def test_mirror_all_cannot_explode_body_count():
    """Spiegeln mit „Neuer Körper“ auf „Alle“ verdoppelt die Körper – ab 1000 kommt eine Meldung statt eines Hängers."""
    timeline = Timeline()
    timeline.crash_guard = False
    timeline.add(solids.Box(name="Q", length=1, width=1, height=1, center_x=5))
    timeline.add(solids.RectangularPattern(name="M", count=20, spacing=2, second_count=20, second_spacing=2))
    assert not timeline.errors and len(timeline.state.bodies) == 400
    timeline.add(solids.MirrorBody(name="S1", plane="YZ"))
    assert not timeline.errors and len(timeline.state.bodies) == 800
    timeline.add(solids.MirrorBody(name="S2", plane="XZ"))          # 1600 Körper: abgelehnt
    assert "höchstens 1000" in timeline.errors.get(3, "")
    assert len(timeline.state.bodies) == 800
