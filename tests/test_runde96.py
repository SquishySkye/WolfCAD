"""Runde 96: weitere Fusion-Optionen – Schräge zweiseitig, Sweep, Rippe, Bohrspitze, Gewinde, Spule/Rohr."""
import math

import pytest

from wolfcad.core import extras as ex, features as ft, sketch as sk, solids
from wolfcad.core.timeline import Timeline


def _box(**draft) -> Timeline:
    timeline = Timeline()
    timeline.add(solids.Box(length=20, width=20, height=20))
    timeline.add(solids.Draft(**draft))
    return timeline


def test_symmetric_draft_is_widest_at_the_middle():
    timeline = _box(angle=5, neutral="Mitte", sides="Symmetrisch")
    assert timeline.errors == {}
    body = timeline.state.body
    # oben und unten schmaler als in der Mitte: Volumen kleiner, Hülle in x/y bleibt 20 (Mitte)
    bounds = solids._bounds(body)
    assert bounds[3] - bounds[0] == pytest.approx(20, abs=1e-4)
    shrink = 10 * math.tan(math.radians(5))
    # zwei gleiche Pyramidenstümpfe von 20 auf 20 − 2·shrink, je 10 hoch
    small = 20 - 2 * shrink
    frustum = 10 / 3 * (20 * 20 + 20 * small + small * small)
    assert solids._volume(body) == pytest.approx(2 * frustum, rel=1e-4)


def test_two_sided_draft_uses_own_angle_below():
    timeline = _box(angle=5, angle2=10, neutral="Mitte", sides="Zwei Seiten")
    assert timeline.errors == {}
    top = 20 - 2 * 10 * math.tan(math.radians(5))
    bottom = 20 - 2 * 10 * math.tan(math.radians(10))
    frustum = lambda small: 10 / 3 * (400 + 20 * small + small * small)
    assert solids._volume(timeline.state.body) == pytest.approx(frustum(top) + frustum(bottom), rel=1e-4)


def _rib(**options) -> Timeline:
    timeline = Timeline()
    timeline.add(solids.Box(length=40, width=40, height=4))             # Platte, Oberseite z = 4
    # senkrechte Skizze (XZ) mit einer Linie über der Platte: die Rippe wächst nach unten bis zur Platte
    timeline.add(sk.Sketch(name="R", plane="XZ", entities=[sk.line(-15, 14, 15, 14)]))
    timeline.add(solids.Rib(sketch="R", thickness=2, **options))
    return timeline


def test_rib_side_and_depth():
    plate = 40 * 40 * 4
    full = _rib()
    assert full.errors == {}
    assert solids._volume(full.state.body) == pytest.approx(plate + 30 * 10 * 2, rel=1e-6)   # 14 − 4 = 10 hoch
    one_side = _rib(thickness_side="Seite 1")
    assert solids._volume(one_side.state.body) == pytest.approx(plate + 600, rel=1e-6)
    assert one_side.errors == {}


def test_rib_one_side_lies_beside_the_sketch_plane_and_depth_limits():
    one_side = _rib(thickness_side="Seite 1")
    assert one_side.errors == {}
    # XZ-Skizze: Normale −Y – „Seite 1“ liegt ganz bei y ≤ 0
    assert solids._volume(one_side.state.body) == pytest.approx(40 * 40 * 4 + 600, rel=1e-6)
    shallow = _rib(depth=6)          # nur 6 mm tief von der Linie aus: erreicht die Platte (10 mm) nicht
    assert shallow.errors                                   # die Rippe schließt nicht an


def test_blind_hole_with_drill_point():
    def hole(**options):
        timeline = Timeline()
        timeline.add(solids.Box(length=30, width=30, height=20))
        timeline.add(solids.Hole(frame=[0, 0, 20, 0, 0, 1, 1, 0, 0], diameter=6, depth=10, **options))
        assert timeline.errors == {}
        return 30 * 30 * 20 - solids._volume(timeline.state.body)
    flat = hole()
    pointed = hole(drill_point="Spitz", drill_angle=118)
    tip = 3 / math.tan(math.radians(59))
    assert flat == pytest.approx(math.pi * 9 * 10, rel=1e-6)
    assert pointed - flat == pytest.approx(math.pi * 9 * tip / 3, rel=1e-4)


def test_left_hand_thread_is_mirror_image():
    import numpy as np

    def thread(hand):
        timeline = Timeline()
        timeline.add(solids.Cylinder(diameter=10, height=12))
        timeline.add(solids.Thread(face_points=[[5, 0, 6]], hand=hand))
        assert timeline.errors == {}, timeline.errors
        return timeline.state.body

    right, left = thread("Rechts"), thread("Links")
    assert solids._volume(right) == pytest.approx(solids._volume(left), rel=1e-4)
    # Probe auf halber Höhe an zwei Gegenwinkeln: rechts und links ist Material an gespiegelten Stellen
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt
    inside = lambda body, angle: BRepClass3d_SolidClassifier(
        body, gp_Pnt(4.6 * np.cos(angle), 4.6 * np.sin(angle), 6.0), 1e-7).State() == TopAbs_IN
    samples = np.linspace(0, 2 * np.pi, 24, endpoint=False)
    pattern_right = [inside(right, a) for a in samples]
    pattern_left = [inside(left, np.pi - a) for a in samples]      # Spiegelung x → −x: Winkel a → π − a
    assert pattern_right == pattern_left and len(set(pattern_right)) == 2
    assert [inside(left, a) for a in samples] != pattern_right        # wirklich andersherum gewunden


@pytest.mark.parametrize("section", ex.SECTIONS)
def test_coil_sections(section):
    timeline = Timeline()
    timeline.add(ex.Coil(diameter=30, pitch=8, turns=2, section=3, section_type=section))
    assert timeline.errors == {}, timeline.errors
    area = {"Kreis": math.pi * 2.25, "Quadrat": 9.0}.get(section, math.sqrt(3) / 4 * 9)
    length = 2 * math.hypot(math.pi * 30, 8)                    # zwei Windungen auf dem Windungsdurchmesser
    assert solids._volume(timeline.state.body) == pytest.approx(area * length, rel=0.03)


def _tight_bounds(shape):
    """Genauer Hüllquader (der schnelle ist bei Freiformflächen wie der Spule großzügig)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    low, high = box.CornerMin(), box.CornerMax()
    return low.X(), low.Y(), low.Z(), high.X(), high.Y(), high.Z()


def test_coil_types_and_place():
    def bounds(**options):
        timeline = Timeline()
        timeline.add(ex.Coil(**{"diameter": 30, "pitch": 8, "section": 3, **options}))
        assert timeline.errors == {}, timeline.errors
        return _tight_bounds(timeline.state.body)
    by_height = bounds(coil_type="Windungen und Höhe", turns=4, height=24)
    assert by_height[5] - by_height[2] == pytest.approx(24 + 3, abs=0.1)        # Höhe + Draht
    by_pitch = bounds(coil_type="Höhe und Steigung", height=30, pitch=10)
    assert by_pitch[5] - by_pitch[2] == pytest.approx(30 + 3, abs=0.1)
    outside = bounds(turns=2, section_place="Außen")
    assert outside[3] == pytest.approx(15 + 3, abs=0.05)                         # ganz außerhalb von Ø 30
    spiral = bounds(coil_type="Spirale", turns=2)
    assert spiral[5] - spiral[2] == pytest.approx(3, abs=0.05)                   # flach
    assert spiral[3] == pytest.approx(15 + 16 + 1.5, abs=0.5)                    # zwei Windungen à 8 mm nach außen


@pytest.mark.parametrize("section", ex.PIPE_SECTIONS)
def test_pipe_sections(section):
    timeline = Timeline()
    timeline.add(sk.Sketch(name="P", entities=[sk.line(0, 0, 50, 0)]))
    timeline.add(ex.Pipe(path="P", diameter=6, section_type=section))
    assert timeline.errors == {}, timeline.errors
    area = {"Kreis": math.pi * 9, "Quadrat": 36.0, "Dreieck": math.sqrt(3) / 4 * 36}[section]
    assert solids._volume(timeline.state.body) == pytest.approx(area * 50, rel=1e-4)


def _sweep(**options) -> Timeline:
    timeline = Timeline()
    # Quadrat 10 × 10 in der XY-Ebene am Ursprung, Pfad 40 mm nach oben (XZ-Skizze: Linie längs z)
    timeline.add(sk.Sketch(name="Q", entities=sk.rectangle(-5, -5, 5, 5)))
    timeline.add(sk.Sketch(name="P", plane="XZ", entities=[sk.line(0, 0, 0, 40)]))
    timeline.add(solids.Sweep(sketch="Q", path="P", **options))
    assert timeline.errors == {}, timeline.errors
    return timeline


def test_sweep_distance_twist_and_taper():
    plain = _sweep()
    assert solids._volume(plain.state.body) == pytest.approx(4000, rel=1e-6)
    half = _sweep(distance=0.5)
    assert solids._volume(half.state.body) == pytest.approx(2000, rel=1e-6)
    twisted = _sweep(twist=90)
    # verdreht bleibt der Querschnitt gleich groß: Volumen wie vorher, oben um 90° gedreht
    assert solids._volume(twisted.state.body) == pytest.approx(4000, rel=0.02)
    tapered = _sweep(taper=5)
    assert solids._volume(tapered.state.body) < 4000 * 0.9
    top = _tight_bounds(tapered.state.body)
    assert top[5] == pytest.approx(40, abs=0.05)


def test_extrude_cuts_only_participating_bodies():
    timeline = Timeline()
    timeline.add(solids.Box(length=10, width=10, height=10))
    timeline.add(solids.Box(center_x=8, length=10, width=10, height=10, operation="Neuer Körper"))
    timeline.add(sk.Sketch(name="S", offset=10, entities=[sk.circle(4, 0, 3)]))      # überdeckt beide Körper
    timeline.add(ft.Extrude(sketch="S", distance=-10, operation="Ausschneiden", participants="Körper2"))
    assert timeline.errors == {}
    assert solids._volume(timeline.state.bodies["Körper1"]) == pytest.approx(1000)      # unberührt
    assert solids._volume(timeline.state.bodies["Körper2"]) < 1000


def test_loft_along_centerline():
    timeline = Timeline()
    timeline.add(sk.Sketch(name="A", entities=sk.rectangle(-5, -5, 5, 5)))
    timeline.add(sk.Sketch(name="B", offset=20, entities=sk.rectangle(-3, -3, 3, 3)))
    timeline.add(sk.Sketch(name="M", plane="XZ", entities=[sk.line(0, 0, 0, 20)]))
    timeline.add(solids.Loft(sketch="A", sketch2="B", centerline="M"))
    assert timeline.errors == {}, timeline.errors
    bounds = _tight_bounds(timeline.state.body)
    assert bounds[2] == pytest.approx(0, abs=1e-3) and bounds[5] == pytest.approx(20, abs=1e-3)
    assert 6 * 6 * 20 < solids._volume(timeline.state.body) < 10 * 10 * 20


@pytest.mark.parametrize("side", ["Innen", "Außen", "Mitte"])
def test_thin_extrude_open_line(side):
    timeline = Timeline()
    timeline.add(sk.Sketch(name="L", entities=[sk.line(0, 0, 30, 0), sk.line(30, 0, 30, 20)]))
    timeline.add(ft.Extrude(sketch="L", distance=10, extrude_type="Dünn", wall=2, wall_side=side))
    assert timeline.errors == {}, timeline.errors
    # Wand aus zwei Schenkeln (30 + 20 mm) × 2 mm × 10 mm, an der Ecke etwas mehr oder weniger
    assert solids._volume(timeline.state.body) == pytest.approx(50 * 2 * 10, rel=0.06)
    assert _tight_bounds(timeline.state.body)[5] == pytest.approx(10, abs=1e-3)


def test_text_on_path_follows_the_arc():
    timeline = Timeline()
    timeline.add(sk.Sketch(name="T", entities=[sk.arc(0, 0, 30, 30, 150)], texts=[["WOLF", 5.0, 0.0, 5.0, 0.0, "", 0]]))
    assert timeline.errors == {}, timeline.errors
    letters = timeline.state.profiles["T"]
    faces = ft.faces_of(letters)
    assert len(faces) >= 4
    # außen auf dem Bogen (Radius 30, Schrift 5 mm): Flächenmitten zwischen 30 und 36 mm von der Mitte
    radii = [math.hypot(*ft.face_point(face)[:2]) for face in faces]
    assert 30 < min(radii) and max(radii) < 36
    # von links nach rechts lesbar: das „W“ (erste Fläche) liegt links (kleinstes x … größeres x nach rechts)
    xs = [ft.face_point(face)[0] for face in faces]
    assert xs[0] < xs[-1]
