"""Runde 92: G-Code-Vorschau wie im Original – Druckreihenfolge, Bahnbreite, Stränge als Körper."""
import numpy as np

from wolfcad.printing import gcode

SAMPLE = """;LAYER_CHANGE
;Z:0.2
G1 Z0.2
;TYPE:Outer wall
;WIDTH:0.5
G1 X10 Y0 E1
G1 X10 Y10 E2
G0 X20 Y20
;TYPE:Sparse infill
;WIDTH:0.4
G1 X25 Y20 E3
;LAYER_CHANGE
;Z:0.4
G1 Z0.4
;TYPE:Outer wall
;WIDTH:0.5
G1 X30 Y20 E4
"""


def test_order_widths_and_layer_bounds(tmp_path):
    source = tmp_path / "probe.gcode"
    source.write_text(SAMPLE)
    path = gcode.read(source)
    assert path.layer_count == 2
    walls, infill = path.orders["Outer wall"], path.orders["Sparse infill"]
    # Druckreihenfolge über die Arten hinweg, aufsteigend
    assert list(walls) == sorted(walls) and walls[1] < infill[0] < walls[2]
    assert np.allclose(path.widths["Outer wall"], 0.5) and np.allclose(path.widths["Sparse infill"], 0.4)
    assert path.layer_first[0] <= walls[0] and path.layer_last[0] == infill[0]
    assert path.layer_first[1] == path.layer_last[1] == walls[2]
    assert abs(path.thickness(0) - 0.2) < 1e-9 and abs(path.thickness(1) - 0.2) < 1e-6


def test_solid_strand_has_width_and_height():
    segments = np.array([[[0, 0, 0.4], [10, 0, 0.4]]], np.float32)
    strand = gcode.solid_triangles(segments, np.array([0.5], np.float32), np.array([0.2], np.float32))
    assert strand.shape == (1, 18, 3)
    points = strand.reshape(-1, 3)
    assert np.isclose(points[:, 1].max() - points[:, 1].min(), 0.5)          # Breite quer zur Bahn
    assert np.isclose(points[:, 2].max(), 0.4) and np.isclose(points[:, 2].min(), 0.2)    # Schichthöhe
    assert points[:, 0].min() < 0 and points[:, 0].max() > 10                  # Enden verlängert (Ecken zu)
