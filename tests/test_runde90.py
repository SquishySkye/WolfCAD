"""Runde 90: Nachträgliches Bearbeiten ohne unnötiges Neurechnen.

Eine Leinwand ändert nur ein Bild in der Anzeige. Ändert man sie, dürfen spätere Schritte nicht neu gerechnet
werden – vorher lief bei jedem Mausschritt die ganze Timeline dahinter (mit Probelauf) erneut und es ruckelte.
"""
import base64

from wolfcad.core import features as ft, more_tools as mt, sketch as sk
from wolfcad.core.timeline import Timeline

# kleinstes gültiges PNG (1×1 Pixel); die Leinwand prüft nur, dass Bilddaten da sind
PIXEL = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")).decode()


def _model() -> Timeline:
    timeline = Timeline()
    timeline.add(mt.Canvas(name="Leinwand1", image=PIXEL, width=80))
    timeline.add(sk.Sketch(name="Skizze1", entities=sk.rectangle(0, 0, 40, 20)))
    timeline.add(ft.Extrude(name="Extrusion1", sketch="Skizze1", distance=15))
    timeline.add(ft.Fillet(name="Rundung1", radius=2))
    return timeline


def test_canvas_change_keeps_later_results():
    timeline = _model()
    body = timeline.state.bodies["Körper1"]
    calls = []
    original = ft.Fillet.apply
    ft.Fillet.apply = lambda self, state: (calls.append(1), original(self, state))[1]
    try:
        timeline.update(0, center_x=12.5)
    finally:
        ft.Fillet.apply = original
    assert calls == []                                  # die Rundung wurde nicht erneut gerechnet
    assert timeline.state.bodies["Körper1"] is body     # derselbe Körper (Auswahl-Zerlegung bleibt gültig)
    corners = timeline.state.canvases["Leinwand1"]["corners"]
    assert abs(sum(point[0] for point in corners) / 4 - 12.5) < 1e-9
    # auch die gemerkten Zwischenstände danach zeigen die verschobene Leinwand
    assert timeline.state_before(3).canvases["Leinwand1"]["corners"] == corners


def test_canvas_fast_path_matches_full_recompute():
    fast, full = _model(), _model()
    fast.update(0, center_y=-7.0, angle=30.0)
    full.features[0].center_y, full.features[0].angle = -7.0, 30.0
    full.recompute()
    assert fast.state.canvases == full.state.canvases
    assert fast.errors == full.errors == {}


def test_invalid_canvas_value_still_reports_error():
    timeline = _model()
    timeline.update(0, width=-5.0)                      # ungültig: die gewöhnliche Neuberechnung meldet es
    assert 0 in timeline.errors
    timeline.update(0, width=50.0)                      # wieder gültig: Fehler weg, Leinwand da
    assert 0 not in timeline.errors and "Leinwand1" in timeline.state.canvases
