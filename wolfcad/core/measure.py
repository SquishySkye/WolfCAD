# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Messen: Länge, Fläche und Radius einzelner Kanten und Flächen sowie Abstand und Winkel
zwischen zwei gewählten Elementen."""

from __future__ import annotations

import math

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane, GeomAbs_Sphere
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_REVERSED

Vector = tuple[float, float, float]


def number(value: float) -> str:
    """Zahl mit Komma und drei Nachkommastellen, wie sie angezeigt wird."""
    return f"{value:.3f}".replace(".", ",")


def describe(kind: str, shape) -> list[str]:
    """Messwerte eines einzelnen Elements; `kind` ist "edge", "face" oder "vertex" (Eckpunkt)."""
    if kind == "vertex":
        point = BRep_Tool.Pnt_s(shape)
        return [f"x {number(point.X())} / y {number(point.Y())} / z {number(point.Z())} mm"]
    properties = GProp_GProps()
    if kind == "edge":
        BRepGProp.LinearProperties_s(shape, properties)
        lines = [f"Länge {number(properties.Mass())} mm"]
        curve = BRepAdaptor_Curve(shape)
        if curve.GetType() == GeomAbs_Circle:
            radius = curve.Circle().Radius()
            lines.append(f"Radius {number(radius)} mm (⌀ {number(2 * radius)} mm)")
        return lines
    BRepGProp.SurfaceProperties_s(shape, properties)
    # „Flächeninhalt“ statt „Fläche“: neben dem Elementnamen stand sonst „Fläche: Fläche … mm²“ (Runde 82)
    lines = [f"Flächeninhalt {number(properties.Mass())} mm²"]
    surface = BRepAdaptor_Surface(shape)
    if surface.GetType() == GeomAbs_Cylinder:
        radius = surface.Cylinder().Radius()
        lines.append(f"Radius {number(radius)} mm (⌀ {number(2 * radius)} mm)")
    elif surface.GetType() == GeomAbs_Sphere:
        lines.append(f"Radius {number(surface.Sphere().Radius())} mm")
    return lines


def _direction(kind: str, shape) -> Vector | None:
    """Richtung einer geraden Kante bzw. Normale einer ebenen Fläche (sonst None)."""
    if kind == "vertex":
        return None
    if kind == "edge":
        curve = BRepAdaptor_Curve(shape)
        if curve.GetType() != GeomAbs_Line:
            return None
        direction = curve.Line().Direction()
    else:
        surface = BRepAdaptor_Surface(shape)
        if surface.GetType() != GeomAbs_Plane:
            return None
        direction = surface.Plane().Axis().Direction()
        if shape.Orientation() == TopAbs_REVERSED:
            direction = direction.Reversed()
    return direction.X(), direction.Y(), direction.Z()


def between(first: tuple, second: tuple) -> tuple[list[str], tuple[Vector, Vector] | None]:
    """Abstand und Winkel zwischen zwei Elementen, jeweils als (Art, Form).

    Zurück kommen die Textzeilen und die beiden nächstgelegenen Punkte (None, wenn der
    Abstand nicht bestimmt werden konnte).
    """
    lines, points = [], None
    distance = BRepExtrema_DistShapeShape(first[1], second[1])
    if distance.IsDone() and distance.NbSolution() > 0:
        start, end = distance.PointOnShape1(1), distance.PointOnShape2(1)
        points = ((start.X(), start.Y(), start.Z()), (end.X(), end.Y(), end.Z()))
        lines.append(f"Abstand {number(distance.Value())} mm")
    one, two = _direction(*first), _direction(*second)
    if one is not None and two is not None:
        cosine = max(-1.0, min(1.0, sum(a * b for a, b in zip(one, two))))
        angle = math.degrees(math.acos(cosine))
        if first[0] == second[0] == "edge":
            angle = min(angle, 180.0 - angle)       # Kanten haben keine Richtung: der kleinere Winkel
        elif first[0] != second[0]:
            angle = abs(90.0 - angle)               # Kante gegen Fläche: Winkel zur Ebene, nicht zur Normale
        lines.append(f"Winkel {number(angle)}°")
    return lines, points
