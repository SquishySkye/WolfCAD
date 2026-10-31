# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Austausch mit anderen Programmen: Körper als STEP oder STL schreiben, STEP-Dateien lesen."""

from __future__ import annotations

from pathlib import Path

from OCP.BRep import BRep_Builder
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.IFSelect import IFSelect_RetDone
from OCP.Message import Message, Message_PrinterOStream
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Reader, STEPControl_Writer
from OCP.StlAPI import StlAPI_Writer
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Compound

EXPORT_FORMATS = {".step": "STEP", ".stp": "STEP", ".stl": "STL"}
# Feinheit einer STL-Datei: größte Abweichung der Dreiecke von der Form in mm
STL_QUALITY = {"fein": 0.01, "normal": 0.05, "grob": 0.2}

# Der STEP-Leser und -Schreiber des Geometriekerns melden jeden Schritt auf der Konsole;
# Fehler werden hier selbst ausgewertet, deshalb bleiben diese Meldungen stumm.
Message.DefaultMessenger_s().RemovePrinters(Message_PrinterOStream.get_type_descriptor_s())


class ExchangeError(ValueError):
    """Eine Datei ließ sich nicht schreiben oder lesen."""


def _compound(shapes: list):
    builder, compound = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound


def export(shapes: list, path: str | Path, deflection: float = 0.05, colours: list | None = None) -> None:
    """Schreibt die Körper in eine Datei; das Format ergibt sich aus der Endung (.step/.stp/.stl).

    `deflection` ist bei STL die größte Abweichung der Dreiecke von der Form in mm.
    `colours` nennt je Körper (Körperfarbe oder None, [(Fläche, Farbe), …]) mit Farben als
    Rot/Grün/Blau von 0 bis 1; STEP nimmt sie mit, STL kennt keine Farben.
    """
    path = Path(path)
    kind = EXPORT_FORMATS.get(path.suffix.lower())
    if kind is None:
        raise ExchangeError(f"Unbekanntes Format '{path.suffix}' – möglich sind .step, .stp und .stl")
    if not shapes:
        raise ExchangeError("Es gibt keinen Körper zum Exportieren")
    if kind == "STEP" and colours and any(body or faces for body, faces in colours):
        done = _write_coloured_step(shapes, colours, path)
    elif kind == "STEP":
        writer = STEPControl_Writer()
        for shape in shapes:                    # jeder Körper bleibt in der Datei ein eigener Körper
            if writer.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone:
                raise ExchangeError("Ein Körper ließ sich nicht nach STEP übertragen")
        done = writer.Write(str(path)) == IFSelect_RetDone
    else:
        compound = _compound(shapes)
        # Eine schon vorhandene Zerlegung (von der Anzeige) würde sonst unverändert übernommen
        BRepTools.Clean_s(compound)
        # Auch der zulässige Knickwinkel folgt der Feinheit, sonst blieben runde Flächen gleich fein
        from . import guard     # im Probelauf-Kind seriell zerlegen (dort fehlen die Arbeits-Threads)
        BRepMesh_IncrementalMesh(compound, deflection, False, min(0.8, 0.15 + deflection * 2.5), not guard.in_child())
        writer = StlAPI_Writer()
        writer.ASCIIMode = False                # binär: deutlich kleinere Dateien
        done = bool(writer.Write(compound, str(path)))
    if not done or not path.exists():
        raise ExchangeError(f"Die Datei '{path}' konnte nicht geschrieben werden")


def _write_coloured_step(shapes: list, colours: list, path: Path) -> bool:
    """Schreibt eine STEP-Datei, in der Körper und einzelne Flächen ihre Farbe tragen."""
    from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

    document = TDocStd_Document(TCollection_ExtendedString("wolfcad"))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    colour_tool = XCAFDoc_DocumentTool.ColorTool_s(document.Main())
    surface = XCAFDoc_ColorType.XCAFDoc_ColorSurf
    for shape, (body_colour, face_colours) in zip(shapes, colours):
        label = shape_tool.AddShape(shape, False)
        if body_colour:
            colour_tool.SetColor(label, Quantity_Color(*body_colour, Quantity_TOC_sRGB), surface)
        for face, colour in face_colours:
            colour_tool.SetColor(shape_tool.AddSubShape(label, face), Quantity_Color(*colour, Quantity_TOC_sRGB), surface)
    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    return bool(writer.Transfer(document, STEPControl_AsIs)) and writer.Write(str(path)) == IFSelect_RetDone


_step_cache: dict[tuple[str, float], list] = {}
_step_colours: dict[tuple[str, float], list] = {}      # zu jedem gelesenen Körper seine Farben


def _read_coloured_step(path: Path) -> tuple[list, list] | None:
    """Liest Körper samt Farben; None, wenn die Datei so nicht lesbar ist (dann hilft der einfache Leser).

    Die Farben kommen je Körper als (Körperfarbe oder None, [(Fläche, Farbe), …]) mit Rot/Grün/Blau von 0 bis 1.
    """
    from OCP.collections import Sequence_TDF_Label
    from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopAbs import TopAbs_FACE
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

    document = TDocStd_Document(TCollection_ExtendedString("wolfcad"))
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    if reader.ReadFile(str(path)) != IFSelect_RetDone or not reader.Transfer(document):
        return None
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    colour_tool = XCAFDoc_DocumentTool.ColorTool_s(document.Main())
    labels = Sequence_TDF_Label()
    shape_tool.GetFreeShapes(labels)

    def colour_of(shape):
        # Flächenfarbe hat Vorrang vor der allgemeinen Farbe eines Teils
        found = Quantity_Color()
        for kind in (XCAFDoc_ColorType.XCAFDoc_ColorSurf, XCAFDoc_ColorType.XCAFDoc_ColorGen):
            if colour_tool.GetColor(shape, kind, found):
                return tuple(found.Values(Quantity_TOC_sRGB))
        return None

    solids, colours = [], []
    for index in range(1, labels.Length() + 1):
        explorer = TopExp_Explorer(shape_tool.GetShape_s(labels.Value(index)), TopAbs_SOLID)
        while explorer.More():
            solid = explorer.Current()
            body = colour_of(solid)
            painted = []
            faces = TopExp_Explorer(solid, TopAbs_FACE)
            while faces.More():
                colour = colour_of(faces.Current())
                if colour is not None and colour != body:
                    painted.append((faces.Current(), colour))
                faces.Next()
            solids.append(solid)
            colours.append((body, painted))
            explorer.Next()
    return solids, colours


def read_step(path: str | Path) -> list:
    """Liest die Volumenkörper einer STEP-Datei (je Datei und Änderungsstand nur einmal)."""
    path = Path(path)
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError as error:
        raise ExchangeError(f"Die Datei '{path}' ist nicht (mehr) vorhanden") from error
    if key not in _step_cache:
        reader = STEPControl_Reader()
        if reader.ReadFile(str(path)) != IFSelect_RetDone:
            raise ExchangeError(f"'{path.name}' ist keine lesbare STEP-Datei")
        # Zuerst mit Farben lesen; liefert das nichts, die Körper ohne Farben holen
        coloured = _read_coloured_step(path)
        if coloured and coloured[0]:
            solids, colours = coloured
        else:
            reader.TransferRoots()
            solids = []
            explorer = TopExp_Explorer(reader.OneShape(), TopAbs_SOLID)
            while explorer.More():
                solids.append(explorer.Current())
                explorer.Next()
            colours = [(None, []) for _solid in solids]
        if not solids:
            raise ExchangeError(f"'{path.name}' enthält keine Volumenkörper")
        _step_cache.clear()                     # nur den jeweils letzten Stand merken
        _step_colours.clear()
        _step_cache[key] = solids
        _step_colours[key] = colours
    return _step_cache[key]


def step_colours(path: str | Path) -> list:
    """Die Farben zu den Körpern von `read_step`, in derselben Reihenfolge.

    Je Körper (Körperfarbe oder None, [(Fläche, Farbe), …]) mit Rot/Grün/Blau von 0 bis 1.
    """
    solids = read_step(path)
    path = Path(path)
    return _step_colours.get((str(path), path.stat().st_mtime), [(None, []) for _solid in solids])
