"""Runde 97: Zahnrad (Evolventenverzahnung, schräg, Pfeil, Hohlrad, Zahnstange) und Modelldateien mit mehreren
Objekten (OBJ, 3MF, STEP) für Slicer und „Netz einfügen“."""
import math
import zipfile

import numpy as np
import pytest
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from wolfcad.core import features as ft
from wolfcad.core import gears, meshes, start_values
from wolfcad.core.timeline import Timeline


def _volume(shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def _only_body(timeline: Timeline):
    assert not timeline.errors, timeline.errors
    bodies = list(timeline.state.bodies.values())
    assert len(bodies) == 1
    assert BRepCheck_Analyzer(bodies[0]).IsValid()
    return bodies[0]


# --- Zahnrad ---------------------------------------------------------------------------------------------------

def test_involute_tooth_geometry_follows_din_867():
    gear = gears.geometry(2.0, 20, 20.0)
    assert gear.pitch == pytest.approx(20.0)                        # d = m·z
    assert gear.tip == pytest.approx(22.0) and gear.root == pytest.approx(17.5)
    assert gear.base == pytest.approx(20.0 * math.cos(math.radians(20.0)))
    # Zahndicke am Teilkreis = halbe Teilung π·m/2
    assert 2 * gear.half_pitch * gear.pitch == pytest.approx(math.pi)
    # Zahn wird nach außen schmaler, am Kopf bleibt er breiter als 0
    assert gears.half_angle(gear, gear.tip, 20.0) < gears.half_angle(gear, gear.pitch, 20.0)
    assert gears.half_angle(gear, gear.tip, 20.0) > 0


@pytest.mark.parametrize("kind", gears.GEAR_TYPES)
def test_every_gear_type_is_a_valid_solid(kind):
    timeline = Timeline()
    timeline.add(gears.Gear(gear_type=kind, module=2.0, teeth=20, thickness=10.0, bore=6.0))
    body = _only_body(timeline)
    assert _volume(body) > 1000


def test_spur_gear_volume_is_about_the_pitch_circle_disc():
    """Zähne und Lücken gleichen sich am Teilkreis etwa aus: Volumen ≈ π r² b minus Bohrung."""
    timeline = Timeline()
    timeline.add(gears.Gear(module=2.0, teeth=20, thickness=10.0, bore=6.0))
    expected = math.pi * 20.0 ** 2 * 10.0 - math.pi * 3.0 ** 2 * 10.0
    assert _volume(_only_body(timeline)) == pytest.approx(expected, rel=0.05)


def test_helical_and_herringbone_keep_the_volume_of_the_spur_gear():
    volumes = {}
    for kind in gears.GEAR_TYPES[:3]:
        timeline = Timeline()
        timeline.add(gears.Gear(gear_type=kind, module=2.0, teeth=16, thickness=8.0, bore=0.0))
        volumes[kind] = _volume(_only_body(timeline))
    assert volumes["Schrägverzahnt"] == pytest.approx(volumes["Stirnrad"], rel=0.01)
    assert volumes["Pfeilverzahnt"] == pytest.approx(volumes["Stirnrad"], rel=0.01)


def test_ring_gear_has_teeth_inside_and_wall_outside():
    timeline = Timeline()
    timeline.add(gears.Gear(gear_type="Hohlrad", module=2.0, teeth=30, thickness=5.0, rim=4.0))
    body = _only_body(timeline)
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box = Bnd_Box()
    BRepBndLib.Add_s(body, box)
    low_x, high_x = box.CornerMin().X(), box.CornerMax().X()
    # Außen: Fußkreis der Innenzähne (r + 1,25 m) plus Wand
    assert high_x - low_x == pytest.approx(2 * (30.0 + 2.5 + 4.0), abs=0.05)
    # die Mitte ist frei (das passende Ritzel läuft innen)
    assert _volume(body) < math.pi * 36.5 ** 2 * 5.0 - math.pi * 28.0 ** 2 * 5.0


def test_rack_length_is_teeth_times_pitch():
    timeline = Timeline()
    timeline.add(gears.Gear(gear_type="Zahnstange", module=1.5, teeth=10, thickness=6.0, rim=3.0))
    body = _only_body(timeline)
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box = Bnd_Box()
    BRepBndLib.Add_s(body, box)
    low_x, high_x = box.CornerMin().X(), box.CornerMax().X()
    assert high_x - low_x == pytest.approx(10 * math.pi * 1.5, abs=0.01)


def test_bad_gear_values_are_reported():
    for values in ({"teeth": 2}, {"module": 0.0}, {"backlash": 5.0}, {"bore": 40.0}, {"pressure_angle": 50.0}):
        timeline = Timeline()
        timeline.add(gears.Gear(**{"module": 2.0, "teeth": 20, "thickness": 5.0, **values}))
        assert timeline.errors, values


def test_gear_starts_neutral_saves_and_loads():
    assert start_values.table()["Zahnrad"] == {"thickness": 0.0}
    started = gears.Gear()
    start_values.apply_start(started)
    assert started.thickness == 0.0 and start_values.is_neutral(started)
    timeline = Timeline()
    timeline.add(gears.Gear(gear_type="Schrägverzahnt", teeth=18, helix_angle=15.0, thickness=6.0))
    again = ft.feature_from_dict(timeline.features[0].to_dict())
    assert isinstance(again, gears.Gear) and again.teeth == 18 and again.helix_angle == 15.0


def test_dragged_teeth_value_is_rounded():
    gear = gears.Gear(teeth=19.6)
    assert gear.tooth_count() == 20 and gear.pitch_diameter() == pytest.approx(40.0)


# --- Modelldateien mit mehreren Objekten -------------------------------------------------------------------------

def _cube(size: float = 10.0, at=(0.0, 0.0, 0.0)) -> np.ndarray:
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    return meshes.tessellate(BRepPrimAPI_MakeBox(gp_Pnt(*at), size, size, size).Shape())


def test_obj_with_quads_and_negative_indices(tmp_path):
    path = tmp_path / "quad.obj"
    path.write_text("# Quadrat\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nvt 0 0\nf 1/1 2/1 3/1 4/1\nf -4 -2 -1\n")
    triangles = meshes.read_obj(str(path))
    assert triangles.shape == (3, 3, 3)
    assert np.allclose(triangles[0], [[0, 0, 0], [1, 0, 0], [1, 1, 0]])


def test_3mf_build_items_become_separate_objects_with_their_placement(tmp_path):
    cube = _cube(1.0)
    points, corners = np.unique(cube.reshape(-1, 3), axis=0, return_inverse=True)
    vertices = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in points)
    faces = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in corners.reshape(-1, 3))
    model = ('<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>'
             f'<object id="1" name="Würfel" type="model"><mesh><vertices>{vertices}</vertices>'
             f'<triangles>{faces}</triangles></mesh></object>'
             '<object id="2" type="model"><components><component objectid="1" transform="2 0 0 0 2 0 0 0 2 0 0 0"/>'
             '</components></object></resources><build><item objectid="1"/>'
             '<item objectid="2" transform="1 0 0 0 1 0 0 0 1 50 0 0"/></build></model>')
    path = tmp_path / "zwei.3mf"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", model)
    found = meshes.read_3mf(str(path))
    assert [name for name, _t in found] == ["Würfel", "Objekt 2"]
    second = found[1][1].reshape(-1, 3)
    assert np.allclose(second.min(0), [50, 0, 0]) and np.allclose(second.max(0), [52, 2, 2])


def test_read_mesh_file_dispatches_and_rejects_unknown(tmp_path):
    stl = tmp_path / "w.stl"
    meshes.write_stl(_cube(), str(stl))
    assert [name for name, _t in meshes.read_mesh_file(str(stl))] == ["w"]
    with pytest.raises(ft.FeatureError):
        meshes.read_mesh_file(str(tmp_path / "x.ply"))
