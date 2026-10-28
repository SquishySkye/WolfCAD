# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für DXF- und SVG-Einfügen in Skizzen (core/inserts.py)."""
import math

import pytest

from wolfcad.core import inserts
from wolfcad.core import sketch as sk
from wolfcad.core.features import FeatureError


def _dxf(tmp_path, body: str):
    """Schreibt eine minimale DXF-Datei mit dem gegebenen ENTITIES-Inhalt."""
    path = tmp_path / "probe.dxf"
    path.write_text("0\nSECTION\n2\nENTITIES\n" + body + "0\nENDSEC\n0\nEOF\n")
    return str(path)


def test_dxf_line_circle_arc(tmp_path):
    path = _dxf(tmp_path, "0\nLINE\n10\n0\n20\n0\n11\n10\n21\n5\n"
                          "0\nCIRCLE\n10\n3\n20\n4\n40\n2\n"
                          "0\nARC\n10\n0\n20\n0\n40\n5\n50\n0\n51\n90\n")
    found = inserts.read_dxf(path)
    assert [entity[0] for entity in found] == [sk.line(0, 0, 1, 1)[0], sk.circle(0, 0, 1)[0], sk.arc(0, 0, 1, 0, 90)[0]]
    assert found[1][1:4] == [3.0, 4.0, 2.0]


def test_dxf_lwpolyline_closed_with_bulge(tmp_path):
    # Quadrat, dessen erste Kante ein Halbkreis ist (bulge 1 = 180°)
    path = _dxf(tmp_path, "0\nLWPOLYLINE\n90\n4\n70\n1\n10\n0\n20\n0\n42\n1\n10\n10\n20\n0\n"
                          "10\n10\n20\n10\n10\n0\n20\n10\n")
    found = inserts.read_dxf(path)
    assert len(found) == 4
    arc = found[0]
    assert arc[0] == sk.arc(0, 0, 1, 0, 90)[0]
    assert math.isclose(arc[3], 5.0, rel_tol=1e-9)


def test_dxf_empty_raises(tmp_path):
    with pytest.raises(FeatureError):
        inserts.read_dxf(_dxf(tmp_path, ""))


def test_svg_shapes_and_path(tmp_path):
    path = tmp_path / "probe.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg">'
                    '<rect x="0" y="0" width="96" height="96"/>'
                    '<circle cx="48" cy="48" r="10"/>'
                    '<g transform="translate(96,0)"><path d="M0 0 L 96 0 C 96 50 50 96 0 96 Z"/></g>'
                    '<path d="M0 0 A 10 10 0 0 1 20 0"/>'
                    '</svg>')
    found = inserts.read_svg(str(path))
    kinds = [entity[0] for entity in found]
    assert kinds.count(sk.line(0, 0, 1, 1)[0]) >= 4 + 2
    assert sk.circle(0, 0, 1)[0] in kinds
    assert sk.spline((0, 0), (1, 1))[0] in kinds
    circle = found[kinds.index(sk.circle(0, 0, 1)[0])]
    # 96 px = 25,4 mm, y-Achse gespiegelt
    assert math.isclose(circle[1], 12.7) and math.isclose(circle[2], -12.7)
    assert math.isclose(circle[3], 10 * inserts.PX_TO_MM)


def test_read_drawing_rejects_other(tmp_path):
    with pytest.raises(FeatureError):
        inserts.read_drawing(str(tmp_path / "x.png"))
