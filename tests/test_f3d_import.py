# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Import-Test an einer echten Fusion-Datei.

Die Beispieldatei gehört nicht zum Projekt. Der Test läuft nur, wenn die
Umgebungsvariable WOLFCAD_SAMPLE_F3D auf eine .f3d-Datei zeigt.
"""

import os

import pytest
from wolfcad.f3d import brep_import, sab
from wolfcad.f3d.container import F3dArchive

SAMPLE = os.environ.get("WOLFCAD_SAMPLE_F3D")
pytestmark = pytest.mark.skipif(not SAMPLE, reason="WOLFCAD_SAMPLE_F3D ist nicht gesetzt")


def test_smallest_blob_is_imported_almost_completely():
    with F3dArchive(SAMPLE) as archive:
        blob = min(archive.brep_blobs(), key=lambda b: b.size)
        result = brep_import.import_bodies(sab.parse(archive.read(blob.path)))
    # Je nach Datei enthält der Blob auch offene Flächenkörper; verlangt wird,
    # dass fast alle Flächen entstehen und Volumenkörper darunter sind.
    assert result.bodies >= 1
    assert result.solids >= 1
    assert result.faces_built >= 0.98 * result.faces_total


def test_design_is_read_with_history_sketches_and_rebuildable_steps():
    from wolfcad.f3d import schema as schema_file

    schema = schema_file.load()
    if not schema:
        pytest.skip("Klassenzuordnung (fusion_schema.json) fehlt")
    with F3dArchive(SAMPLE) as archive:
        design = archive.design(schema)
    assert len(design.history) > 100
    assert design.sketches and design.extrudes
    assert sum(1 for entry in design.history if design.can_rebuild(entry.object_id)) > 50
