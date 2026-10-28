# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Physik-Tests am Modell (Runde 83): Festigkeit unter Last, Umströmung durch Luft oder Wasser, Schwimmen.

Alle Einheiten wie im übrigen Programm: Längen in mm, Kräfte in N, Spannungen und E-Modul in MPa (= N/mm²),
Dichten in g/cm³, Geschwindigkeiten in m/s.

Festigkeit (`static_stress`): lineare Elastizität mit quadratischen Tetraedern (TET10). Das Volumennetz kommt von
gmsh (über eine STEP-Datei des Körpers), das Gleichungssystem löst scipy. Gewählte Flächen werden fest gelagert,
auf andere wirkt eine Kraft (N, verteilt) oder ein Druck (MPa, senkrecht zur Fläche). Ergebnis: Verschiebung und
Vergleichsspannung nach von Mises je Knoten, Sicherheitsfaktor gegen die Streckgrenze des Werkstoffs.

Strömung (`flow`): Lattice-Boltzmann-Verfahren D3Q19 mit Smagorinsky-Turbulenzmodell auf einem Würfelgitter um den
Körper (nur numpy). Ergebnis: Widerstandsbeiwert cw, Widerstandskraft bei der gewählten Geschwindigkeit, Druckbeiwert
auf der Oberfläche und Stromlinien. Das Gitter ist grob (einige zehn Zellen über den Körper) – die Werte sind eine
Näherung für Vergleiche und Formoptimierung, keine Windkanalmessung.

Schwimmen (`buoyancy`): exakt am Volumenkörper – Masse aus Volumen und Dichte, Eintauchtiefe so, dass die verdrängte
Wassermenge die Masse trägt (Bisektion über die Höhe des Wasserspiegels).

Eigene Umsetzung; keine Teile aus Autodesk-Software.
"""
from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .features import FeatureError, face_point, faces_of


# --- Werkstoffe ------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Material:
    name: str
    density: float          # g/cm³
    young: float            # E-Modul in MPa
    poisson: float          # Querkontraktionszahl
    yield_strength: float   # Streckgrenze (bei Kunststoffen: Zugfestigkeit) in MPa


# Richtwerte (Datenblätter, gerundet). Gedruckte Teile sind quer zu den Schichten deutlich schwächer – die Werte
# gelten für das volle Material in Schichtrichtung.
MATERIALS = {
    "PLA": Material("PLA", 1.24, 3500.0, 0.36, 50.0),
    "ABS": Material("ABS", 1.05, 2200.0, 0.35, 40.0),
    "PETG": Material("PETG", 1.27, 2100.0, 0.38, 50.0),
    "Aluminium": Material("Aluminium", 2.70, 70000.0, 0.33, 240.0),
    "Stahl": Material("Stahl", 7.85, 210000.0, 0.30, 235.0),
    "Messing": Material("Messing", 8.50, 100000.0, 0.34, 200.0),
}
DEFAULT_MATERIAL = "PLA"


def material_named(label: str | None) -> Material:
    """Werkstoff zu einem Eintrag von „Physisches Material“ (z. B. „PLA (1,24 g/cm³)“); ohne Eintrag PLA."""
    for key, material in MATERIALS.items():
        if label and label.split(" (")[0].strip() == key:
            return material
    return MATERIALS[DEFAULT_MATERIAL]


# Fluide: Dichte in kg/m³, kinematische Viskosität in m²/s (20 °C)
FLUIDS = {"Luft": (1.204, 1.516e-5), "Wasser": (998.2, 1.004e-6)}


# --- Gemeinsames -------------------------------------------------------------------------------------------------

def _faces_at(shape, points: list) -> list:
    """Die Flächen des Körpers zu gemerkten Flächenpunkten (siehe features.find_by_points)."""
    from .features import find_by_points
    return find_by_points(faces_of(shape), face_point, points, "Fläche") if points else []


def _volume_and_centre(shape) -> tuple[float, tuple]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    centre = props.CentreOfMass()
    return props.Mass(), (centre.X(), centre.Y(), centre.Z())


def _bounds(shape) -> tuple[float, ...]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    low, high = box.CornerMin(), box.CornerMax()          # Get() liefert in OCP 8 keinen lesbaren Wert
    return low.X(), low.Y(), low.Z(), high.X(), high.Y(), high.Z()


# --- Schwimmen ---------------------------------------------------------------------------------------------------

@dataclass
class Buoyancy:
    mass: float                 # g
    volume: float               # mm³
    floats: bool
    draft: float                # Eintauchtiefe in mm (vom tiefsten Punkt)
    waterline: float            # Höhe des Wasserspiegels (z in mm)
    submerged: float            # Anteil des eingetauchten Volumens (0 … 1)
    buoyancy_max: float         # größte mögliche Auftriebskraft in N (ganz untergetaucht)
    weight: float               # Gewichtskraft in N
    carry: float                # Zuladung bis zum Untergehen in g (0, wenn es sinkt)


def buoyancy(shape, density: float, water: float = 0.998) -> Buoyancy:
    """Schwimmt der Körper (aufrecht, wie er im Modell steht) in Wasser der Dichte `water` (g/cm³)?

    Die Eintauchtiefe wird so gesucht, dass das Volumen unter dem Wasserspiegel × Wasserdichte die Masse trägt.
    Die Masse kommt aus dem Werkstoffvolumen, der Auftrieb aus der äußeren Hülle: eingeschlossene Hohlräume (eine
    geschlossene Schale, ein Hohlkörper) sind mit Luft gefüllt und verdrängen Wasser mit. Ein offenes Boot zählt
    wie ein Vollkörper aus Wänden – wie in Wirklichkeit, solange kein Wasser hineinläuft, muss es dafür geschlossen
    (mit Deck) modelliert sein."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    material_volume, _centre = _volume_and_centre(shape)
    if material_volume <= 0:
        raise FeatureError("Der Körper hat kein Volumen")
    mass = material_volume / 1000.0 * density           # mm³ → cm³ → g
    hull = _filled(shape)                               # Körper samt eingeschlossener Hohlräume
    volume = _volume_and_centre(hull)[0]
    g = 9.81
    x0, y0, z0, x1, y1, z1 = _bounds(shape)
    pad = 10.0

    def below(level: float) -> float:
        if level <= z0:
            return 0.0
        if level >= z1:
            return volume
        box = BRepPrimAPI_MakeBox(gp_Pnt(x0 - pad, y0 - pad, z0 - pad), gp_Pnt(x1 + pad, y1 + pad, level)).Shape()
        return _volume_and_centre(BRepAlgoAPI_Common(hull, box).Shape())[0]

    needed = mass / water * 1000.0                      # verdrängtes Volumen in mm³, das die Masse trägt
    floats = needed < volume
    if floats:
        low, high = z0, z1
        for _ in range(40):                             # Bisektion auf 1e-12 der Höhe genau
            middle = (low + high) / 2
            if below(middle) < needed:
                low = middle
            else:
                high = middle
        level = (low + high) / 2
    else:
        level = z1
    return Buoyancy(mass=mass, volume=material_volume, floats=floats, draft=level - z0, waterline=level,
                    # mm³ → m³ (1e-9), g/cm³ → kg/m³ (×1000), Kraft = Volumen × Dichte × g
                    submerged=min(1.0, needed / volume), buoyancy_max=volume * 1e-9 * water * 1000.0 * g,
                    weight=mass / 1000.0 * g, carry=max(0.0, volume / 1000.0 * water - mass))


def _filled(shape):
    """Der Körper mit gefüllten Hohlräumen: von jedem Volumenkörper nur die äußere Schale."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.BRepClass3d import BRepClass3d
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS, TopoDS_Compound
    builder, filled = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(filled)
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        outer = BRepClass3d.OuterShell_s(TopoDS.Solid(explorer.Current()))
        builder.Add(filled, BRepBuilderAPI_MakeSolid(outer).Solid())
        explorer.Next()
    return filled


# --- Festigkeit (FEM) --------------------------------------------------------------------------------------------

SUPPORT, FORCE, PRESSURE = "Fest", "Kraft", "Druck"


@dataclass
class Load:
    kind: str                       # FORCE (N, gleichmäßig verteilt) oder PRESSURE (MPa, senkrecht auf die Fläche)
    face_points: list               # Merkpunkte der Flächen
    value: float
    direction: tuple = ()           # bei FORCE: Richtung (leer = senkrecht in die Fläche hinein)


@dataclass
class StressResult:
    nodes: np.ndarray               # (n, 3) Knoten in mm
    displacement: np.ndarray        # (n, 3) Verschiebung in mm
    von_mises: np.ndarray           # (n,) Vergleichsspannung in MPa
    surface: np.ndarray             # (m, 3) Knotennummern der Oberflächendreiecke (für die Anzeige)
    material: Material
    elements: int = 0
    max_stress: float = 0.0
    max_displacement: float = 0.0
    safety: float = math.inf        # Streckgrenze / größte Spannung
    reaction: float = 0.0           # Summe der Lagerkräfte in N (Gegenprobe: entspricht der Last)
    load_total: float = 0.0
    coarsened: float = 0.0          # > 0: das gewünschte Netz war zu fein, gerechnet wurde mit dieser Größe (mm)


# Natürliche Koordinaten der 10 Knoten eines TET10 in gmsh-Reihenfolge: Ecken 0–3, dann Kantenmitten
# (0,1) (1,2) (2,0) (3,0) (3,2) (3,1)
_TET10_EDGES = ((0, 1), (1, 2), (2, 0), (3, 0), (3, 2), (3, 1))
_CORNERS = np.array([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)])
_TET10_NODES = np.vstack([_CORNERS, [(_CORNERS[a] + _CORNERS[b]) / 2 for a, b in _TET10_EDGES]])
# Integration: 4 Punkte, exakt für quadratische Ansätze (Gewicht je 1/24, Volumen des Bezugstetraeders 1/6)
_GAUSS_A, _GAUSS_B = 0.5854101966249685, 0.1381966011250105
_GAUSS = np.array([(_GAUSS_B, _GAUSS_B, _GAUSS_B), (_GAUSS_A, _GAUSS_B, _GAUSS_B),
                   (_GAUSS_B, _GAUSS_A, _GAUSS_B), (_GAUSS_B, _GAUSS_B, _GAUSS_A)])
_GAUSS_WEIGHT = 1.0 / 24.0


def _tet10_gradients(point) -> np.ndarray:
    """Ableitungen der 10 Formfunktionen nach (r, s, t) am Punkt: (10, 3)."""
    r, s, t = point
    lam = (1.0 - r - s - t, r, s, t)
    dlam = np.array([(-1.0, -1.0, -1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)])
    grads = [(4.0 * lam[i] - 1.0) * dlam[i] for i in range(4)]
    grads += [4.0 * (lam[a] * dlam[b] + lam[b] * dlam[a]) for a, b in _TET10_EDGES]
    return np.array(grads)


def _elasticity(material: Material) -> np.ndarray:
    """Elastizitätsmatrix (6 × 6) für isotropes Material, Voigt-Reihenfolge xx yy zz xy yz zx."""
    e, nu = material.young, material.poisson
    lam = e * nu / ((1 + nu) * (1 - 2 * nu))
    mu = e / (2 * (1 + nu))
    d = np.zeros((6, 6))
    d[:3, :3] = lam
    d[[0, 1, 2], [0, 1, 2]] += 2 * mu
    d[[3, 4, 5], [3, 4, 5]] = mu
    return d


def _strain_matrix(coords: np.ndarray, gradients: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Verzerrungsmatrix B (e, 6, 30) und Jacobi-Determinante (e,) für Elemente mit Knoten `coords` (e, 10, 3) an
    einer Stelle mit Formfunktions-Ableitungen `gradients` (10, 3)."""
    jacobian = np.einsum("ni,enj->eij", gradients, coords)
    det = np.linalg.det(jacobian)
    inverse = np.linalg.inv(jacobian)
    dn = np.einsum("eij,nj->eni", inverse, gradients)       # Ableitungen nach x, y, z: (e, 10, 3)
    count = len(coords)
    b = np.zeros((count, 6, 30))
    x, y, z = dn[:, :, 0], dn[:, :, 1], dn[:, :, 2]
    b[:, 0, 0::3], b[:, 1, 1::3], b[:, 2, 2::3] = x, y, z
    b[:, 3, 0::3], b[:, 3, 1::3] = y, x
    b[:, 4, 1::3], b[:, 4, 2::3] = z, y
    b[:, 5, 0::3], b[:, 5, 2::3] = z, x
    return b, det


def _mesh(shape, size: float, fixed_points: list, load_groups: list[list]):
    """Volumennetz (TET10) über gmsh. Gibt Knoten (n, 3), Elemente (e, 10), je Flächengruppe die Dreiecke (TRI6,
    Knotennummern) und die Oberflächendreiecke für die Anzeige zurück.

    Die gewählten Flächen werden über ihren Schwerpunkt den Flächen in gmsh zugeordnet."""
    try:
        import gmsh
    except ImportError as error:
        raise FeatureError("Für die Festigkeitsrechnung fehlt das Paket gmsh (pip install gmsh)") from error
    from . import exchange
    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / "teil.step")
        exchange.export([shape], path, 0.1)
        gmsh.initialize(interruptible=False)
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add("teil")
            gmsh.model.occ.importShapes(path)
            gmsh.model.occ.synchronize()
            gmsh.option.setNumber("Mesh.MeshSizeMax", size)
            gmsh.option.setNumber("Mesh.MeshSizeMin", size * 0.2)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)
            gmsh.option.setNumber("Mesh.ElementOrder", 2)
            gmsh.option.setNumber("Mesh.HighOrderOptimize", 0)
            gmsh.model.mesh.generate(3)
            tags, coords, _ = gmsh.model.mesh.getNodes()
            index = np.zeros(int(tags.max()) + 1, dtype=np.int64)
            index[tags.astype(np.int64)] = np.arange(len(tags))
            nodes = coords.reshape(-1, 3)
            types, _elements, connectivity = gmsh.model.mesh.getElements(3)
            tets = [np.asarray(c, dtype=np.int64).reshape(-1, 10) for t, c in zip(types, connectivity) if t == 11]
            if not tets:
                raise FeatureError("gmsh hat kein Volumennetz erzeugt (Körper geschlossen?)")
            elements = index[np.vstack(tets)]
            # Flächen von gmsh mit Schwerpunkt, um die gewählten Flächen wiederzufinden
            surfaces = [(tag, np.array(gmsh.model.occ.getCenterOfMass(2, tag))) for _dim, tag in
                        gmsh.model.getEntities(2)]

            def triangles_of(points: list) -> np.ndarray:
                found = []
                for point in points:
                    tag = min(surfaces, key=lambda item: float(np.linalg.norm(item[1] - np.array(point[:3]))))[0]
                    types2, _e, conn2 = gmsh.model.mesh.getElements(2, tag)
                    found += [np.asarray(c, dtype=np.int64).reshape(-1, 6) for t, c in zip(types2, conn2) if t == 9]
                return index[np.vstack(found)] if found else np.zeros((0, 6), dtype=np.int64)

            fixed = triangles_of(fixed_points)
            loads = [triangles_of(points) for points in load_groups]
            types2, _e, conn2 = gmsh.model.mesh.getElements(2)
            boundary = [np.asarray(c, dtype=np.int64).reshape(-1, 6) for t, c in zip(types2, conn2) if t == 9]
            surface = index[np.vstack(boundary)] if boundary else np.zeros((0, 6), dtype=np.int64)
        finally:
            gmsh.finalize()
    return nodes, elements, fixed, loads, surface


def _triangle_loads(nodes: np.ndarray, triangles: np.ndarray, kind: str, value: float,
                    direction: tuple) -> tuple[np.ndarray, float]:
    """Knotenkräfte (n, 3) einer Last auf TRI6-Dreiecken: bei gleichmäßiger Flächenlast tragen beim quadratischen
    Dreieck die Ecken nichts und jede Kantenmitte ein Drittel (konsistente Lastverteilung). Gibt auch die
    Gesamtkraft in N zurück."""
    forces = np.zeros_like(nodes)
    if not len(triangles):
        return forces, 0.0
    a, b, c = (nodes[triangles[:, k]] for k in range(3))
    cross = np.cross(b - a, c - a)
    areas = 0.5 * np.linalg.norm(cross, axis=1)
    normals = cross / np.maximum(2 * areas, 1e-30)[:, None]       # Außennormale (gmsh: nach außen orientiert)
    total_area = float(areas.sum())
    if kind == PRESSURE:
        per_triangle = -normals * (value * areas)[:, None]           # Druck drückt in die Fläche hinein
    else:
        if direction:
            unit = np.array(direction, dtype=float)
            unit = unit / (np.linalg.norm(unit) or 1.0)
            per_triangle = np.outer(areas / total_area * value, unit)
        else:
            per_triangle = -normals * (areas / total_area * value)[:, None]
    for k in range(3, 6):                                            # Kantenmitten je ein Drittel
        np.add.at(forces, triangles[:, k], per_triangle / 3.0)
    return forces, float(np.linalg.norm(per_triangle.sum(axis=0)))


# Bis zu so vielen Unbekannten löst der direkte Löser (schnell, genau); darüber das CG-Verfahren: der direkte Löser
# brauchte bei 114 000 Unbekannten 51 s und 3,9 GB, bei feineren Netzen mehr Speicher als der Rechner hatte
_DIRECT_LIMIT = 40_000
# Höchstzahl der Elemente: 53 000 Elemente brauchten 3 GB; darüber wird automatisch gröber vernetzt
_MAX_ELEMENTS = 120_000
_CG_TOLERANCE = 1e-7     # relativer Rest; Verschiebungen auf etwa 6 Stellen genau


def _solve(matrix, forces: np.ndarray, report) -> np.ndarray:
    """Löst K·u = f. Kleine Systeme direkt (SuperLU); große mit dem konjugierten Gradientenverfahren und einem
    3×3-Block-Jacobi-Vorkonditionierer (je Knoten die Steifigkeit seiner drei Richtungen) – der Speicher bleibt bei
    dem der Matrix selbst."""
    from scipy.sparse import block_diag, csr_matrix
    from scipy.sparse.linalg import LinearOperator, cg, spsolve
    count = matrix.shape[0]
    if count <= _DIRECT_LIMIT:
        return spsolve(matrix, forces)
    # Block-Jacobi: die 3×3-Diagonalblöcke je Knoten umkehren (die freien Unbekannten liegen meist in Dreiergruppen;
    # wo nicht, gilt die einfache Diagonale)
    diagonal = matrix.diagonal()
    groups = count // 3
    blocks = None
    try:
        dense = np.zeros((groups, 3, 3))
        coo = matrix.tocoo()
        same = (coo.row // 3 == coo.col // 3) & (coo.row < groups * 3) & (coo.col < groups * 3)
        dense[coo.row[same] // 3, coo.row[same] % 3, coo.col[same] % 3] = coo.data[same]
        inverse = np.linalg.inv(dense)
        blocks = block_diag(list(inverse), format="csr") if groups * 3 == count else None
    except np.linalg.LinAlgError:
        blocks = None
    if blocks is not None:
        precondition = LinearOperator((count, count), matvec=lambda vector: blocks @ vector)
    else:
        precondition = LinearOperator((count, count), matvec=lambda vector: vector / diagonal)

    steps = [0]

    def counted(_vector) -> None:
        steps[0] += 1
        if steps[0] % 200 == 0:
            report(0.6 + 0.2 * min(1.0, steps[0] / 4000), f"Gleichungssystem: {steps[0]} Schritte …")

    solution, info = cg(matrix, forces, rtol=_CG_TOLERANCE, maxiter=20_000, M=precondition, callback=counted)
    if info != 0:
        raise FeatureError("Das Gleichungssystem ließ sich nicht genau genug lösen – das Netz etwas gröber wählen")
    return solution


def static_stress(shape, material: Material, supports: list, loads: list[Load], size: float | None = None,
                  progress=None) -> StressResult:
    """Lineare statische Festigkeitsrechnung (siehe Moduldoku). `supports`: Merkpunkte fest gelagerter Flächen."""
    try:
        from scipy.sparse import coo_matrix
        from scipy.sparse.linalg import spsolve
    except ImportError as error:
        raise FeatureError("Für die Festigkeitsrechnung fehlt das Paket scipy (pip install scipy)") from error
    if not supports:
        raise FeatureError("Mindestens eine Fläche fest lagern (sonst fliegt das Teil davon)")
    if not loads:
        raise FeatureError("Mindestens eine Last (Kraft oder Druck) auf eine Fläche setzen")
    report = progress or (lambda _share, _text: None)
    x0, y0, z0, x1, y1, z1 = _bounds(shape)
    size = size or max(math.dist((x0, y0, z0), (x1, y1, z1)) / 22.0, 0.3)
    support_points = [p for p in supports]
    for points in [support_points, *[load.face_points for load in loads]]:
        _faces_at(shape, points)                                     # prüft, dass die Flächen noch da sind
    report(0.05, "Volumennetz …")
    nodes, elements, fixed, load_triangles, surface = _mesh(shape, size, support_points,
                                                            [load.face_points for load in loads])
    coarsened = 0.0
    if len(elements) > _MAX_ELEMENTS:
        # zu fein für den Speicher eines normalen Rechners: einmal gröber vernetzen (Elementzahl ~ 1/Größe³)
        coarsened = size * (len(elements) / _MAX_ELEMENTS) ** (1 / 3) * 1.05
        report(0.2, f"Netz gröber ({coarsened:.1f} mm) …")
        nodes, elements, fixed, load_triangles, surface = _mesh(shape, coarsened, support_points,
                                                                [load.face_points for load in loads])
    count = len(nodes)
    report(0.35, f"Steifigkeit ({len(elements)} Elemente) …")
    d = _elasticity(material)
    gauss = [_tet10_gradients(point) for point in _GAUSS]
    dofs = (3 * elements[:, :, None] + np.arange(3)[None, None, :]).reshape(len(elements), 30)
    rows, cols, values = [], [], []
    chunk = 4000
    for start in range(0, len(elements), chunk):
        coords = nodes[elements[start:start + chunk]]
        stiffness = np.zeros((len(coords), 30, 30))
        for gradients in gauss:
            b, det = _strain_matrix(coords, gradients)
            if np.any(det <= 0):
                raise FeatureError("Das Netz enthält verdrehte Elemente – feiner oder gröber vernetzen")
            stiffness += np.einsum("eki,kl,elj->eij", b, d, b) * (det * _GAUSS_WEIGHT)[:, None, None]
        block = dofs[start:start + chunk]
        rows.append(np.repeat(block, 30, axis=1).ravel())
        cols.append(np.tile(block, (1, 30)).ravel())
        values.append(stiffness.ravel())
    matrix = coo_matrix((np.concatenate(values), (np.concatenate(rows), np.concatenate(cols))),
                        shape=(3 * count, 3 * count)).tocsr()
    forces = np.zeros((count, 3))
    total = 0.0
    for load, triangles in zip(loads, load_triangles):
        part, amount = _triangle_loads(nodes, triangles, load.kind, load.value, tuple(load.direction))
        forces += part
        total += amount
    fixed_nodes = np.unique(fixed.ravel())
    if not len(fixed_nodes):
        raise FeatureError("Die gelagerte Fläche ließ sich im Netz nicht finden")
    blocked = np.zeros(3 * count, dtype=bool)
    blocked[(3 * fixed_nodes[:, None] + np.arange(3)).ravel()] = True
    free = ~blocked
    report(0.6, f"Gleichungssystem ({int(free.sum())} Unbekannte) …")
    solution = np.zeros(3 * count)
    solution[free] = _solve(matrix[free][:, free], forces.ravel()[free], report)
    displacement = solution.reshape(-1, 3)
    reaction = matrix[blocked] @ solution
    report(0.85, "Spannungen …")
    stress_sum = np.zeros((count, 6))
    hits = np.zeros(count)
    element_u = solution[dofs]                                       # (e, 30)
    for local, point in enumerate(_TET10_NODES):
        for start in range(0, len(elements), chunk):
            coords = nodes[elements[start:start + chunk]]
            b, _det = _strain_matrix(coords, _tet10_gradients(point))
            sigma = np.einsum("kl,elj,ej->ek", d, b, element_u[start:start + chunk])
            np.add.at(stress_sum, elements[start:start + chunk, local], sigma)
            np.add.at(hits, elements[start:start + chunk, local], 1.0)
    stress = stress_sum / np.maximum(hits, 1.0)[:, None]
    sxx, syy, szz, sxy, syz, szx = stress.T
    von_mises = np.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
                        + 3.0 * (sxy ** 2 + syz ** 2 + szx ** 2))
    report(1.0, "fertig")
    # Anzeige: jedes TRI6 in vier ebene Dreiecke (Ecken und Kantenmitten: 0-3-5, 3-1-4, 5-4-2, 3-4-5)
    display = np.vstack([surface[:, [0, 3, 5]], surface[:, [3, 1, 4]], surface[:, [5, 4, 2]], surface[:, [3, 4, 5]]])
    peak = float(von_mises.max()) if len(von_mises) else 0.0
    return StressResult(nodes=nodes, displacement=displacement, von_mises=von_mises, surface=display,
                        material=material, elements=len(elements), max_stress=peak,
                        max_displacement=float(np.linalg.norm(displacement, axis=1).max()),
                        safety=material.yield_strength / peak if peak > 0 else math.inf,
                        reaction=float(np.linalg.norm(reaction.reshape(-1, 3).sum(axis=0))), load_total=total,
                        coarsened=coarsened)


# --- Strömung (Lattice-Boltzmann D3Q19) --------------------------------------------------------------------------

_E = np.array([(0, 0, 0), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1),
               (1, 1, 0), (-1, -1, 0), (1, -1, 0), (-1, 1, 0), (1, 0, 1), (-1, 0, -1), (1, 0, -1), (-1, 0, 1),
               (0, 1, 1), (0, -1, -1), (0, 1, -1), (0, -1, 1)])
_W = np.array([1 / 3] + [1 / 18] * 6 + [1 / 36] * 12)
_OPPOSITE = np.array([0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15, 18, 17])
_AXIS_ORDER = {"X": (0, 1, 2), "Y": (1, 0, 2), "Z": (2, 1, 0)}     # Strömungsachse wird Gitter-x
_SIDE, _WAKE = 2.0, 3.0         # Abstand des Rechenraums vorn/seitlich bzw. hinten, in Querabmessungen
_MAX_CELLS = 1_500_000          # größte Zahl von Gitterzellen (Speicher und Rechenzeit)


@dataclass
class FlowResult:
    fluid: str
    speed: float                    # m/s
    direction: str                  # z. B. "+X"
    drag_coefficient: float         # cw bezogen auf die Stirnfläche
    frontal_area: float             # mm²
    drag_force: float               # N bei `speed`
    lift: tuple = (0.0, 0.0)        # Querkräfte in N (die beiden anderen Achsen, in Modellachsen)
    reynolds: float = 0.0           # Reynoldszahl der echten Strömung (Körperlänge in Strömungsrichtung)
    lattice_reynolds: float = 0.0   # Reynoldszahl, mit der das Gitter rechnet
    cells: int = 0
    steps: int = 0
    pressure_points: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))   # Punkte (mm) …
    pressure_values: np.ndarray = field(default_factory=lambda: np.zeros(0))        # … und Druckbeiwert cp
    streamlines: list = field(default_factory=list)          # je Linie ein Array (k, 3) in mm


def _voxels(shape, origin: np.ndarray, spacing: float, size: tuple, order: tuple, sign: int) -> np.ndarray:
    """Belegung des Gitters: True, wo der Körper liegt (Zellmitte im Körper). Gitterachsen sind die Modellachsen in
    `order`; bei `sign` < 0 läuft die Gitter-x-Achse gegen die Modellachse."""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt
    solid = np.zeros(size, dtype=bool)
    x0, y0, z0, x1, y1, z1 = _bounds(shape)
    low, high = np.array((x0, y0, z0)), np.array((x1, y1, z1))
    classifier = BRepClass3d_SolidClassifier(shape)
    for i in range(size[0]):
        for j in range(size[1]):
            for k in range(size[2]):
                grid = np.array((i + 0.5, j + 0.5, k + 0.5)) * spacing
                model = np.empty(3)
                model[list(order)] = grid
                model = origin + model if sign > 0 else origin + _flip(model, order[0], size[0] * spacing)
                if np.any(model < low) or np.any(model > high):
                    continue
                classifier.Perform(gp_Pnt(*model), 1e-6)
                solid[i, j, k] = classifier.State() == TopAbs_IN
    return solid


def _flip(model: np.ndarray, axis: int, length: float) -> np.ndarray:
    flipped = model.copy()
    flipped[axis] = length - model[axis]
    return flipped


def flow(shape, direction: str = "+X", speed: float = 10.0, fluid: str = "Luft", resolution: int = 16,
         steps: int | None = None, progress=None) -> FlowResult:
    """Umströmung des Körpers (siehe Moduldoku). `direction`: Richtung, in die das Fluid strömt (z. B. "+X");
    `resolution`: Zellen über die größte Abmessung des Körpers quer zur Strömung."""
    if fluid not in FLUIDS:
        raise FeatureError(f"Unbekanntes Fluid '{fluid}'")
    if len(direction) != 2 or direction[0] not in "+-" or direction[1] not in "XYZ":
        raise FeatureError("Richtung wie „+X“ angeben")
    if speed <= 0:
        raise FeatureError("Die Geschwindigkeit muss größer als 0 sein")
    report = progress or (lambda _share, _text: None)
    order, sign = _AXIS_ORDER[direction[1]], (1 if direction[0] == "+" else -1)
    x0, y0, z0, x1, y1, z1 = _bounds(shape)
    low, high = np.array((x0, y0, z0)), np.array((x1, y1, z1))
    extent = high - low
    along, across = extent[order[0]], max(extent[order[1]], extent[order[2]], 1e-6)
    # Die Auflösung gilt quer zur Strömung (dort entscheidet sich der Widerstand); ein langer Körper bekommt
    # entsprechend mehr Zellen in Strömungsrichtung – bis zu einer Obergrenze, danach wird das Gitter gröber
    spacing = across / resolution
    # Raum: vorn und seitlich je _SIDE Körpergrößen, hinten _WAKE – genug für Nachlauf und freie Anströmung (seitlich
    # zu eng drängt die Strömung am Körper vorbei und erhöht den Widerstand; mit 1,2 lag die Kugel bei Re 100 um gut
    # ein Drittel zu hoch)
    reach = across                      # Abstände des Raums richten sich nach dem Querschnitt
    lengths = np.array((along + (_SIDE + _WAKE) * reach, extent[order[1]] + 2 * _SIDE * reach,
                        extent[order[2]] + 2 * _SIDE * reach))
    while np.prod(np.ceil(lengths / spacing)) > _MAX_CELLS:
        spacing *= 1.1
    size = tuple(int(math.ceil(length / spacing)) for length in lengths)
    resolution = across / spacing
    origin_grid = np.array((_SIDE * reach, _SIDE * reach, _SIDE * reach))
    origin = np.empty(3)
    origin[list(order)] = low[list(order)] - origin_grid
    if sign < 0:
        # Gitter-x läuft gegen die Modellachse: der Anfang liegt hinter dem Körper
        origin[order[0]] = high[order[0]] + _SIDE * reach - size[0] * spacing
    report(0.02, f"Gitter {size[0]}×{size[1]}×{size[2]} …")
    solid = _voxels(shape, origin, spacing, size, order, sign)
    if not solid.any():
        raise FeatureError("Der Körper ist für das Gitter zu klein – feiner rechnen")
    # Gittergrößen: Anströmung u0 (Gitter), Viskosität aus der gewünschten Reynoldszahl, nach unten begrenzt durch
    # das Turbulenzmodell (Smagorinsky) für Stabilität
    u0 = 0.1           # Gittergeschwindigkeit (Machzahl 0,17 – für das Verfahren noch inkompressibel genug)
    density, viscosity = FLUIDS[fluid]
    real_re = speed * across / 1000.0 / viscosity     # bezogen auf die Querabmessung (wie beim Zylinder/Kugel)
    nu = max(u0 * resolution / max(real_re, 1.0), 0.0005)
    tau = 3.0 * nu + 0.5
    lattice_re = u0 * resolution / nu
    steps = steps or int(2.0 * size[0] / u0)             # zweimal durch den ganzen Raum
    f = np.empty((19, *size), dtype=np.float32)
    feq = lambda rho, ux, uy, uz: _equilibrium(rho, ux, uy, uz)
    rho = np.ones(size, dtype=np.float32)
    ux = np.full(size, u0, dtype=np.float32)
    ux[solid] = 0.0
    zero = np.zeros(size, dtype=np.float32)
    f[:] = feq(rho, ux, zero, zero)
    fluid_mask = ~solid
    inlet = _equilibrium(np.ones(size[1:], dtype=np.float32), np.full(size[1:], u0, dtype=np.float32),
                         np.zeros(size[1:], dtype=np.float32), np.zeros(size[1:], dtype=np.float32))
    # Gerechnet wird auf flachen Feldern (19 × Zellen) mit Matrixprodukten (BLAS, mehrkernig) statt Schleifen über
    # die Richtungen (Runde 88: 45 → ~20 ms je Schritt bei 150 000 Zellen); Rückprall- und Kraftzellen sind einmal
    # vorberechnete Indexlisten statt in jedem Schritt neu ausgewerteter Masken
    cells = int(np.prod(size))
    directions = _E.astype(np.float32)                       # (19, 3)
    weights = _W.astype(np.float32)[:, None]                 # (19, 1)
    # Impulsfluss Π_ab für das Turbulenzmodell: Zeilen xx yy zz xy yz zx, Nebendiagonale doppelt gewichtet
    flux = np.array([_E[:, a] * _E[:, b] for a, b in ((0, 0), (1, 1), (2, 2), (0, 1), (1, 2), (0, 2))],
                    dtype=np.float32)                        # (6, 19)
    flux_weight = np.array([1, 1, 1, 2, 2, 2], dtype=np.float32)[:, None]
    # Verbindungen Fluid → Körper je Richtung (für Rückprall und Kraft nach dem Impulsaustausch)
    links = [np.flatnonzero(np.roll(solid, -_E[i], axis=(0, 1, 2)) & fluid_mask) for i in range(19)]
    solid_cells = np.flatnonzero(solid)
    inlet_flat = inlet.reshape(19, -1)
    inlet_velocity = np.array([[u0], [0.0], [0.0]], dtype=np.float32)      # Anströmung am Einlass
    force_history = []
    smagorinsky = 0.16
    turbulence = np.float32(18.0 * 1.41421 * smagorinsky ** 2)
    flat = f.reshape(19, cells)                              # Sicht auf f (gleicher Speicher)
    for step in range(steps):
        rho = flat.sum(axis=0)
        velocity = (directions.T @ flat) / rho               # (3, Zellen)
        # Ein- und Auslass: links feste Anströmung (erste Gitterscheibe)
        slab = size[1] * size[2]
        rho[:slab] = 1.0
        velocity[:, :slab] = inlet_velocity
        equilibrium = _equilibrium_flat(rho, velocity, directions, weights)
        # Smagorinsky: lokale Relaxationszeit aus dem Nichtgleichgewichts-Impulsfluss
        neq = flat - equilibrium
        pi = flux @ neq                                      # (6, Zellen)
        q = np.sqrt((flux_weight * pi * pi).sum(axis=0))
        tau_eff = 0.5 * (tau + np.sqrt(tau * tau + turbulence * q / rho))
        post = flat - neq / tau_eff
        post[0] = equilibrium[0]
        # Kraft auf den Körper (Impulsaustausch): jede Population, die auf den Körper trifft, prallt zurück
        force = np.zeros(3)
        for i in range(1, 19):
            force += 2.0 * float(post[i, links[i]].sum()) * _E[i]
        force_history.append(force)
        # Strömen (je Richtung um ihren Gittervektor verschoben)
        post_cube = post.reshape(19, *size)
        for i in range(19):
            f[i] = np.roll(post_cube[i], _E[i], axis=(0, 1, 2))
        # Rückprall am Körper: was in eine Körperzelle laufen würde, kehrt um
        for i in range(1, 19):
            flat[_OPPOSITE[i], links[i]] = post[i, links[i]]
        flat[:, solid_cells] = weights                       # im Körper: Ruhe (wird nicht gelesen)
        flat[:, :slab] = inlet_flat                          # Einlass: feste Anströmung
        f[:, -1] = f[:, -2]                                  # Auslass: Nullgradient
        if step % 25 == 0:
            report(0.05 + 0.85 * step / steps, f"Strömung {step}/{steps} …")
    rho = rho.reshape(size)
    ux, uy, uz = (component.reshape(size) for component in velocity)
    # Mittelwert über das letzte Viertel (die Strömung pendelt hinter stumpfen Körpern)
    mean = np.mean(force_history[-max(steps // 4, 1):], axis=0)
    # Stirnfläche in Zellen: die Rückprall-Wand liegt eine halbe Zelle außerhalb der Körperzellen, der wirksame
    # Umriss ist deshalb um einen halben Zellenrand größer als die Zahl der Körperzellen
    shadow = solid.any(axis=0)
    rim = shadow & ~(np.roll(shadow, 1, 0) & np.roll(shadow, -1, 0) & np.roll(shadow, 1, 1) & np.roll(shadow, -1, 1))
    frontal = float(shadow.sum() + 0.5 * rim.sum())
    cw = mean[0] / (0.5 * 1.0 * u0 * u0 * frontal) if frontal else 0.0
    frontal_mm2 = frontal * spacing * spacing
    drag = 0.5 * density * speed * speed * cw * frontal_mm2 * 1e-6
    side = [0.5 * density * speed * speed * (mean[k] / (0.5 * u0 * u0 * frontal)) * frontal_mm2 * 1e-6
            for k in (1, 2)] if frontal else [0.0, 0.0]
    report(0.93, "Druck und Stromlinien …")
    pressure_points, pressure_values = _surface_pressure(rho, solid, origin, spacing, order, sign, size, u0)
    lines = _streamlines(ux, uy, uz, solid, origin, spacing, order, sign, size)
    report(1.0, "fertig")
    return FlowResult(fluid=fluid, speed=speed, direction=direction, drag_coefficient=float(cw),
                      frontal_area=frontal_mm2, drag_force=float(drag), lift=(float(side[0]), float(side[1])),
                      reynolds=real_re, lattice_reynolds=lattice_re, cells=int(np.prod(size)), steps=steps,
                      pressure_points=pressure_points, pressure_values=pressure_values, streamlines=lines)


def _equilibrium_flat(rho: np.ndarray, velocity: np.ndarray, directions: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Gleichgewichtsverteilung für alle 19 Richtungen auf einmal (flach: 19 × Zellen)."""
    eu = directions @ velocity                               # e_i · u, (19, Zellen)
    usq = (velocity * velocity).sum(axis=0)
    result = eu * eu
    result *= 4.5
    result += 3.0 * eu
    result += 1.0 - 1.5 * usq
    result *= weights * rho
    return result


def _equilibrium(rho, ux, uy, uz) -> np.ndarray:
    usq = ux * ux + uy * uy + uz * uz
    result = np.empty((19, *rho.shape), dtype=np.float32)
    for i, (ex, ey, ez) in enumerate(_E):
        eu = ex * ux + ey * uy + ez * uz
        result[i] = _W[i] * rho * (1.0 + 3.0 * eu + 4.5 * eu * eu - 1.5 * usq)
    return result


def _to_model(cells: np.ndarray, origin, spacing, order, sign, size) -> np.ndarray:
    """Gitterzellen (k, 3, Mitte bei +0,5) → Modellpunkte (k, 3) in mm."""
    grid = (cells + 0.5) * spacing
    model = np.empty_like(grid)
    model[:, list(order)] = grid
    if sign < 0:
        model[:, order[0]] = size[0] * spacing - model[:, order[0]]
    return model + origin


def _surface_pressure(rho, solid, origin, spacing, order, sign, size, u0):
    """Druckbeiwert cp in den Fluidzellen direkt am Körper: cp = (p − p∞) / (½ ρ u²), p = ρ/3 im Gitter."""
    near = np.zeros_like(solid)
    for axis in range(3):
        for shift in (-1, 1):
            near |= np.roll(solid, shift, axis=axis)
    near &= ~solid
    cells = np.argwhere(near)
    # Bezug: Druck der freien Strömung vor dem Körper (nicht der feste Einlasswert – vom Einlass bis zum Körper
    # baut sich ein leichter Druckanstieg auf; damit lag der Staudruck bei cp 1,5 statt 1,0)
    upstream = int(max(np.argwhere(solid)[:, 0].min() // 2, 1))
    reference = float(np.median(rho[upstream]))
    values = (rho[near] - reference) / 3.0 / (0.5 * u0 * u0)
    return _to_model(cells.astype(float), origin, spacing, order, sign, size), values.astype(float)


def _streamlines(ux, uy, uz, solid, origin, spacing, order, sign, size, count: int = 9) -> list:
    """Stromlinien: von einem Raster vor dem Körper der Strömung folgen (Euler-Schritte von einer halben Zelle)."""
    occupied = np.argwhere(solid)
    if not len(occupied):
        return []
    j0, k0 = occupied[:, 1].min(), occupied[:, 2].min()
    j1, k1 = occupied[:, 1].max(), occupied[:, 2].max()
    lines = []
    for a in np.linspace(j0 - 1, j1 + 1, count):
        for b in np.linspace(k0 - 1, k1 + 1, count):
            point = np.array((2.0, a, b))
            path = [point.copy()]
            for _ in range(int(size[0] * 3)):
                cell = np.clip(point.astype(int), 0, np.array(size) - 1)
                if solid[tuple(cell)]:
                    break
                velocity = np.array((ux[tuple(cell)], uy[tuple(cell)], uz[tuple(cell)]))
                speed = np.linalg.norm(velocity)
                if speed < 1e-6:
                    break
                point = point + velocity / speed * 0.5
                if point[0] >= size[0] - 1 or np.any(point < 0) or np.any(point >= np.array(size) - 1):
                    break
                path.append(point.copy())
            if len(path) > 4:
                lines.append(_to_model(np.array(path) - 0.5, origin, spacing, order, sign, size))
    return lines
